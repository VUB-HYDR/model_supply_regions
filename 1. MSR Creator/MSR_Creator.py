"""Create Model Supply Regions (MSRs) from renewable-resource input layers.

The workflow reads an Excel control file, processes country-level geospatial
inputs, scores suitability layers, identifies competitive renewable-resource
areas, optionally relaxes resource thresholds for resource-lagging countries,
polygonizes suitable areas, and attributes final MSRs with capacity, distance,
substation, and load-centre metrics.

The script assumes source layers are available in the configured input folder
and that country boundaries use names matching the configured country list.
Geographic CRS data are used for clipping against country geometry; equal-area
projected CRS data are used for area and distance calculations.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import string
import struct
import time
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import rasterio
import richdem as rd
import rioxarray
import scipy.stats
import xarray
import xrspatial
from geocube.api.core import make_geocube
from osgeo import osr
from rasterio.features import shapes
from rasterio.warp import Resampling, reproject
from rasterstats import zonal_stats
from scipy.ndimage.measurements import label
from shapely.geometry import LineString, MultiPolygon
from shapely.geometry import box, mapping
from shapely.ops import split


CONTROL_FILE_NAME = "control_file_msr_creator.xlsx"
INPUT_DATASET_SHEET = "input_datasets"
COUNTRY_WISE_INPUTS_SHEET = "country_wise_input_datasets"
CONFIGURATIONS_SHEET = "configurations"
PATHS_SHEET = "paths"
ANALYSIS_INPUTS_SHEET = "analysis_inputs"
LOGGER = logging.getLogger(__name__)


@dataclass
class MsrCreatorConfig:
    """Run-wide settings derived from the MSR Creator control workbook."""

    control_file: Path
    home_directory: Path
    input_spatial_datasets_folder: Path
    countries: pd.DataFrame
    re_technology: str
    relax_thresholds_for_resource_lagging_countries: bool
    roads_buffered_search: bool
    grid_buffered_search: bool
    band_count_for_multi_resolve_algorithm: int
    default_min_capacity_suitable_to_create_msr_mw: float
    stages_to_run: list[int]
    control_dataset_names: pd.DataFrame
    control_country_wise_inputs: pd.DataFrame
    control_configurations: pd.DataFrame
    control_paths: pd.DataFrame
    control_analysis_inputs: pd.DataFrame
    file_name_population_density: str
    file_name_land_cover: str
    file_name_elevation: str
    file_name_protected_areas: str
    file_name_substations: str
    file_name_urban_area_load_centers: str
    file_name_roads: str
    file_name_power_grid: str
    file_name_transmission_grid: str
    file_name_continent_distance_surface_tgrid: str
    file_name_distribution_grid: str
    file_name_country_boundaries: str
    file_name_water_bodies: str
    resource_raster_name: str
    road_type: int
    land_discount: float
    slope_threshold: float
    population_threshold: int
    re_spatial_footprint_mw_per_km2: float
    resource_lower_limit: float
    user_resource_threshold: float
    run_info_column_headers: list[str]
    max_area_to_cap_msrs_km2: float
    land_cover_classes: list[int]
    default_min_contiguous_area_suitable_for_msr_km2: float
    country_maps_for_clipping_folder: Path
    elevation_threshold: int
    min_suitable_country_area: float
    resource_relaxation_step: float
    hours_in_year: int
    days_in_year: int
    wind_land_classes: np.ndarray
    wind_production_percentage_per_land_class: np.ndarray
    csp_land_classes: np.ndarray
    csp_production_percentage_per_land_class: np.ndarray
    pv_conversion_efficiency: float
    pv_spacing_factor: float
    wind_spacing_downwind_rotor_diameters: int
    wind_spacing_crosswind_rotor_diameters: int
    wind_rotor_diameter_meters: int
    wind_turbine_capacity_watts: float
    log_file: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class CountryPaths:
    """Output paths for one country and technology run."""

    output_folder: Path
    stage_1_clipping_folder: Path
    stage_1_scoring_folder: Path
    stage_2_competitive_resource_folder: Path
    polygonization_folder: Path
    final_msrs_folder: Path
    final_msrs_path: Path


@dataclass
class CountryContext:
    """Country-specific state prepared before running workflow stages."""

    country_name_with_spaces: str
    country_name_without_spaces: str
    single_country_boundary: gpd.GeoDataFrame
    country_area_km2: float
    paths: CountryPaths
    roads_buffer_distance_m: float | None = None
    grid_buffer_distance_m: float | None = None
    stop_processing: bool = False


def quadrat_cut_geometry(geometry, quadrat_width):
    """Split a large polygon into quadrat-sized pieces.

    The input geometry is expected to be in a projected CRS where coordinate
    units are metres. The function is used to cap very large MSR polygons
    before final attribution.

    Args:
        geometry: Shapely polygon geometry to split.
        quadrat_width: Approximate grid spacing in geometry units, expected to
            be metres for the MSR workflow.

    Returns:
        MultiPolygon: Split geometry parts.
    """

    west, south, east, north = geometry.bounds
    x_num = math.floor((east - west) / quadrat_width) + 1
    y_num = math.floor((north - south) / quadrat_width) + 1
    x_points = np.linspace(west, east, num=2 + x_num)
    y_points = np.linspace(south, north, num=2 + y_num)

    vertical_lines = [
        LineString([(x, y_points[0]), (x, y_points[-1])])
        for x in x_points
    ]
    horizont_lines = [
        LineString([(x_points[0], y), (x_points[-1], y)])
        for y in y_points
    ]
    lines = vertical_lines + horizont_lines

    for line in lines:
        geometry = MultiPolygon(split(geometry, line))

    return geometry


def polygonize_resource_potential(
    resource_potential_raster_path,
    polygonization_folder,
    re_technology,
    resource_threshold,
    band_count_for_multi_resolve_algorithm,
    max_area_to_cap_msrs_km2,
    min_contiguous_area_suitable_for_msr_km2,
):
    """Polygonize competitive resource pixels into candidate MSRs.

    Resource potential is split into quality bands before polygonization so
    that contiguous areas with similar resource quality are resolved separately.
    Very small polygons are removed and very large polygons are split to respect
    the configured minimum and maximum MSR sizes.

    Args:
        resource_potential_raster_path: Competitive resource raster path. The
            raster is expected in ESRI:54009 for metre-based area operations.
        polygonization_folder: Folder for intermediate band rasters and
            shapefiles.
        re_technology: Technology identifier used in output names.
        resource_threshold: Minimum resource value retained for MSR creation.
            Units are kWh/m2/day for solar and m/s for wind.
        band_count_for_multi_resolve_algorithm: Number of resource-quality
            bands to polygonize.
        max_area_to_cap_msrs_km2: Maximum MSR area before quadrat splitting.
        min_contiguous_area_suitable_for_msr_km2: Minimum contiguous area in
            km2 required to retain a polygon.

    Returns:
        geopandas.GeoDataFrame | int: Final MSR polygons, or ``0`` when no
        qualifying polygons are produced.
    """

    polygonization_folder = Path(polygonization_folder)

    resource_potential_raster = xarray.open_dataarray(resource_potential_raster_path)
    resource_potential_raster = resource_potential_raster.squeeze("band")

    resource_potential_values = resource_potential_raster.data * 1
    resource_potential_values[np.isnan(resource_potential_values)] = 0
    max_resource_pixel_value = resource_potential_values.max()

    is_first_msr = 1
    for resource_band in range(1, band_count_for_multi_resolve_algorithm + 1):
        resolved_raster_path = (
            polygonization_folder
            / f"{re_technology}ResourceBand{resource_band}_resolve.tif"
        )
        single_band_initial_msrs_path = (
            polygonization_folder
            / f"{re_technology}ResourceBand{resource_band}_InitialMSRs.shp"
        )
        single_band_final_msrs_path = (
            polygonization_folder
            / f"{re_technology}ResourceBand{resource_band}_FinalMSRs.shp"
        )

        resource_band_upper_limit = resource_threshold + resource_band * (
            (max_resource_pixel_value - resource_threshold)
            / band_count_for_multi_resolve_algorithm
        )
        resource_band_lower_limit = resource_threshold + (resource_band - 1) * (
            (max_resource_pixel_value - resource_threshold)
            / band_count_for_multi_resolve_algorithm
        )

        LOGGER.info(
            f"Polygonizing resource band {resource_band}/"
            f"{band_count_for_multi_resolve_algorithm}: "
            f"{resource_band_lower_limit:.3f} to "
            f"{resource_band_upper_limit:.3f}"
        )

        subset_resource_potential_raster = resource_potential_raster * 1
        subset_resource_potential_raster = subset_resource_potential_raster.where(
            ~(resource_potential_raster < resource_band_lower_limit), 0)
        subset_resource_potential_raster = subset_resource_potential_raster.where(
            ~(resource_potential_raster > resource_band_upper_limit), 0)
        resolved_raster = subset_resource_potential_raster.where(
            ~(subset_resource_potential_raster > 0), 1)
        resolved_raster.rio.to_raster(resolved_raster_path)

        # Transform is required so polygonized geometries inherit raster scale.
        initial_polygons = shapes(
            resolved_raster.data.astype('float32'),
            mask=resolved_raster.data == 1,
            transform=resolved_raster.rio.transform(),
        )
        initial_polygons = ({
            'properties': {'raster_val': pixel_value},
            'geometry': polygon_geometry,
        } for counter, (polygon_geometry, pixel_value) in enumerate(
            initial_polygons))
        initial_polygons = list(initial_polygons)
        time.sleep(1)
        initial_polygons = gpd.GeoDataFrame.from_features(
            initial_polygons, crs="ESRI:54009")

        if not initial_polygons.empty:
            if initial_polygons.explode(ignore_index=True).index.nlevels > 1:
                initial_polygons = initial_polygons.explode(
                    ignore_index=True).droplevel(1).reset_index(drop=True)
            initial_polygons = initial_polygons.drop(columns=['raster_val'])
            initial_polygons.to_file(single_band_initial_msrs_path)

            initial_polygons_above_min_area_threshold = initial_polygons[
                initial_polygons.area
                >= min_contiguous_area_suitable_for_msr_km2 * 1000000
            ]
            if not initial_polygons_above_min_area_threshold.empty:

                single_band_final = initial_polygons_above_min_area_threshold[
                    initial_polygons_above_min_area_threshold.area
                    <= max_area_to_cap_msrs_km2 * 1000000
                ].reset_index(drop=True)
                single_band_to_be_capped = initial_polygons_above_min_area_threshold[
                    initial_polygons_above_min_area_threshold.area
                    > max_area_to_cap_msrs_km2 * 1000000
                ].reset_index(drop=True)

                if len(single_band_to_be_capped) > 0:
                    for i in range(0, len(single_band_to_be_capped)):
                        LOGGER.debug(
                            f"Splitting oversized MSR polygon {i + 1}/"
                            f"{len(single_band_to_be_capped)}"
                        )
                        single_polygon_parts = gpd.GeoDataFrame(
                            crs=single_band_to_be_capped.crs,
                            geometry=list(
                                quadrat_cut_geometry(
                                    single_band_to_be_capped.geometry.loc[i],
                                    np.sqrt(max_area_to_cap_msrs_km2) * 1000,
                                )
                            ),
                        )
                        if i == 0 and single_band_final.empty:
                            single_band_final = single_polygon_parts
                        else:
                            single_band_final = gpd.overlay(
                                single_polygon_parts, single_band_final,
                                how='union')
                            single_band_final = single_band_final[
                                single_band_final.area
                                >= min_contiguous_area_suitable_for_msr_km2
                                * 1000000
                            ]
                single_band_final[
                    single_band_final.area
                    >= min_contiguous_area_suitable_for_msr_km2 * 1000000
                ].to_file(single_band_final_msrs_path)

                single_band = single_band_final[
                    single_band_final.area
                    >= min_contiguous_area_suitable_for_msr_km2 * 1000000
                ].reset_index(
                    drop=True)
                if is_first_msr == 1:
                    multi_band = single_band
                    is_first_msr = 0
                else:
                    multi_band = gpd.overlay(single_band, multi_band, how='union')
                    multi_band = multi_band[
                        multi_band.area
                        >= min_contiguous_area_suitable_for_msr_km2 * 1000000
                    ]
    try:
        multi_band['FID'] = multi_band.index
        return multi_band
    except Exception as exc:
        LOGGER.warning(f"MSRs were not developed during polygonization | reason={exc}")
        return 0


def minimum_distance_of_msr_centroid_from_geometry_set(
    msr_centroid,
    geometry_set,
):
    """Return the nearest distance from an MSR centroid to a geometry set.

    Args:
        msr_centroid: Shapely point geometry for the MSR centroid.
        geometry_set: GeoSeries of point, line, polygon, or mixed geometries.
            Coordinates are expected in a projected CRS with metre units.

    Returns:
        float: Minimum distance in geometry units, expected to be metres.
    """

    return geometry_set.distance(msr_centroid).min()


def compute_load_center_attributes_for_msr_centroid(
    msr_centroid,
    load_centers,
):
    """Summarize load-centre proximity for an MSR centroid.

    Args:
        msr_centroid: Shapely point geometry in a projected CRS with metre
            units.
        load_centers: GeoDataFrame of load centres with ``name_conve`` and
            ``max_pop_al`` fields.

    Returns:
        tuple: Closest city name, closest city population, population within
        100 km, city count within 100 km, and a compact city-name listing.
    """

    load_center_distances = load_centers.geometry.distance(msr_centroid) / 1000
    best_distance = load_center_distances.min()

    closest_city_name = load_centers.name_conve[
        load_center_distances == best_distance
    ].iloc[0]
    closest_city_population_count = load_centers.max_pop_al[
        load_center_distances == best_distance
    ].iloc[0]
    pop_within_100km = load_centers.max_pop_al[load_center_distances < 100].sum()
    city_count_within_100km = len(load_center_distances[load_center_distances < 100])

    if city_count_within_100km <= 10:
        cities_100km = (
            np.array2string(
                load_centers.name_conve[load_center_distances < 100].values,
                separator=",",
            )
            .strip("[]")
            .replace("'", "")
            .replace(",", ", ")
        )
    else:
        cities_100km = "Above 10 cities"

    return (
        closest_city_name,
        closest_city_population_count,
        pop_within_100km,
        city_count_within_100km,
        cities_100km,
    )


def run_resource_sufficiency_stage(
    suitable_area_resource_raster_path,
    user_resource_threshold,
    resource_lower_limit,
    re_technology,
    land_discount,
    re_spatial_footprint_mw_per_km2,
    wind_rotor_diameter_meters,
    wind_spacing_downwind_rotor_diameters,
    wind_spacing_crosswind_rotor_diameters,
    wind_turbine_capacity_watts,
    default_min_contiguous_area_suitable_for_msr_km2,
    country_area_km2,
    min_suitable_country_area,
    resource_relaxation_step,
    hours_in_year,
    days_in_year,
    wind_land_classes,
    wind_production_percentage_per_land_class,
    csp_land_classes,
    csp_production_percentage_per_land_class,
    pv_conversion_efficiency,
    pv_spacing_factor
):
    """Relax the resource threshold until enough suitable area is retained.

    This optional check is intended for resource-lagging countries. It lowers
    the resource threshold by ``resource_relaxation_step`` until retained
    contiguous suitable area exceeds ``min_suitable_country_area`` of country
    area or reaches ``resource_lower_limit``.

    Returns:
        tuple[float, float]: Relaxed resource threshold and indicative annual
        yield in GWh.
    """

    suitable_area_resource_raster = xarray.open_dataarray(
        suitable_area_resource_raster_path)
    suitable_area_resource_raster = suitable_area_resource_raster.squeeze("band")

    raster_pixel_size_m = abs(suitable_area_resource_raster.affine[0])
    min_contiguous_pixels_to_retain = ceil(
        default_min_contiguous_area_suitable_for_msr_km2
        * 1000000
        / (raster_pixel_size_m * raster_pixel_size_m)
    )

    suitable_area_resource_values = suitable_area_resource_raster.to_numpy()
    resource_threshold = user_resource_threshold
    cutoff_normalized = 1

    break_while_loop = 0
    while break_while_loop == 0 and resource_threshold >= resource_lower_limit:
        suitable_area_resource_values_filtered = np.where(
            suitable_area_resource_values < resource_threshold, 0,
            suitable_area_resource_values)
        # Connected-component labelling removes isolated areas below MSR scale.
        feat, count = label(suitable_area_resource_values_filtered)
        feature_pixel_count = np.bincount(feat[feat >= 0])
        desired_features_to_retain = np.where(
            feature_pixel_count > min_contiguous_pixels_to_retain)
        suitable_area_resource_without_small_contiguous_regions = np.zeros_like(
            suitable_area_resource_values_filtered)
        if len(desired_features_to_retain[0]) > 1:
            for f in desired_features_to_retain[0][1:]:
                suitable_area_resource_without_small_contiguous_regions = np.where(
                    feat == f,
                    suitable_area_resource_values_filtered,
                    suitable_area_resource_without_small_contiguous_regions)
        LOGGER.debug(
            f"Resource sufficiency retained "
            f"{len(desired_features_to_retain[0])} contiguous features"
        )
        suitable_area_resource_without_small_contiguous_regions[
            np.isnan(suitable_area_resource_without_small_contiguous_regions)
        ] = 0

        if re_technology == 'solarpv':
            # PV yield uses GHI, pixel area, days/year, efficiency, spacing,
            # and land-discount assumptions.
            indicative_yield_gwh = (
                suitable_area_resource_without_small_contiguous_regions.sum()
                * raster_pixel_size_m
                * raster_pixel_size_m
                * (days_in_year / 1000000)
                * pv_conversion_efficiency
                * pv_spacing_factor
                * land_discount
            )

        if re_technology == 'solarcsp':
            # CSP yield uses configurable resource-bin production percentages.
            area_per_spatial_cluster = (
                np.histogram(
                    suitable_area_resource_without_small_contiguous_regions,
                    bins=csp_land_classes,
                )[0]
                * raster_pixel_size_m
                * raster_pixel_size_m
                / 1000000
            )
            csp_max_capacity_per_spatial_cluster = (
                re_spatial_footprint_mw_per_km2
                * area_per_spatial_cluster
                * land_discount
            )
            indicative_yield_gwh = (
                csp_max_capacity_per_spatial_cluster
                * hours_in_year
                * csp_production_percentage_per_land_class
                / 100
            ).sum() / 1000

        if re_technology == 'wind':
            # Wind yield uses configurable resource-bin production percentages.
            area_per_spatial_cluster = (
                np.histogram(
                    suitable_area_resource_without_small_contiguous_regions,
                    bins=wind_land_classes,
                )[0]
                * raster_pixel_size_m
                * raster_pixel_size_m
                / 1000000
            )
            wind_max_capacity_per_spatial_cluster = (
                np.round(
                    area_per_spatial_cluster
                    / (
                        wind_spacing_downwind_rotor_diameters
                        * wind_rotor_diameter_meters
                        * wind_spacing_crosswind_rotor_diameters
                        * wind_rotor_diameter_meters
                        / 1000000
                    ),
                    0,
                )
                * (wind_turbine_capacity_watts / 1e6)
                * land_discount
            )
            indicative_yield_gwh = (
                wind_max_capacity_per_spatial_cluster
                * hours_in_year
                * wind_production_percentage_per_land_class
                / 100
            ).sum() / 1000
        LOGGER.debug(
            f"Indicative {re_technology} yield: {indicative_yield_gwh:.3f} GWh "
            f"at threshold {resource_threshold:.3f} "
            f"and normalized cutoff {cutoff_normalized:.3f}"
        )

        sufficiency_parameter = (
            np.count_nonzero(suitable_area_resource_without_small_contiguous_regions)
            * raster_pixel_size_m
            * raster_pixel_size_m
            / 1000000
        )
        sufficiency_condition = country_area_km2 * min_suitable_country_area

        if sufficiency_parameter > sufficiency_condition:
            break_while_loop = 1
        else:
            resource_threshold = resource_threshold - resource_relaxation_step
            if resource_threshold >= resource_lower_limit:
                cutoff_normalized = (
                    (resource_threshold - resource_lower_limit)
                    / (user_resource_threshold - resource_lower_limit)
                )

    return resource_threshold, indicative_yield_gwh


def configure_logging(level: int = logging.INFO) -> None:
    """Configure terminal logging for MSR workflow progress."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def control_subpath(value):
    """Convert a workbook folder value into a relative path component.

    The control workbook stores some subfolders with leading/trailing slashes.
    Stripping those separators keeps path joining platform-independent while
    preserving the configured folder name.
    """

    return Path(str(value).strip("/\\"))


def load_control_workbook(control_file: Path) -> dict[str, pd.DataFrame]:
    """Read the MSR Creator control workbook into named DataFrames.

    Returns:
        dict[str, pandas.DataFrame]: DataFrames keyed by their role in the
        workflow. The expected sheets are defined by the module-level sheet
        constants.
    """

    return {
        "control_dataset_names": pd.read_excel(
            control_file,
            sheet_name=INPUT_DATASET_SHEET,
            index_col=0,
        ),
        "control_country_wise_inputs": pd.read_excel(
            control_file,
            sheet_name=COUNTRY_WISE_INPUTS_SHEET,
            index_col=0,
        ),
        "control_configurations": pd.read_excel(
            control_file,
            sheet_name=CONFIGURATIONS_SHEET,
            index_col=0,
        ),
        "control_paths": pd.read_excel(
            control_file,
            sheet_name=PATHS_SHEET,
            index_col=0,
        ),
        "control_analysis_inputs": (
            pd.read_excel(
                control_file,
                sheet_name=ANALYSIS_INPUTS_SHEET,
                index_col=0,
            )
        ),
    }


def get_stages_to_run(control_configurations: pd.DataFrame) -> list[int]:
    """Return stage numbers enabled in the control workbook.

    Returns:
        list[int]: Enabled workflow stage numbers. Stage ``3`` maps to Stage 4
        part i polygonization for compatibility with the existing control-file
        convention.
    """

    run_status_of_process_scripts = [
        control_configurations.loc[
            "perform_stage_1_clipping_multi_country_datasets_prepare_distance_surfaces_scoring_all_data_layers"
        ][0],
        control_configurations.loc[
            "perform_stage_2_get_resource_potential_with_or_without_resource_sufficiency_check_stage_3"
        ][0],
        control_configurations.loc["perform_stage_4_part_i_polygonization"][0],
        control_configurations.loc[
            "perform_stage_4_part_ii_attribution_msr_capacity_area_distance_to_grid_road_and_others"
        ][0],
    ]
    return [
        stage_number
        for stage_number in range(1, 5)
        if run_status_of_process_scripts[stage_number - 1] != 0
    ]


def build_msr_creator_config(
    control_file: Path,
    control: dict[str, pd.DataFrame],
) -> MsrCreatorConfig:
    """Build run-wide MSR Creator configuration from control DataFrames.

    The control workbook is the source of user-editable assumptions: file
    names, output paths, technology choice, resource thresholds, land-discount
    factors, capacity-density assumptions, and optional sufficiency settings.

    Returns:
        MsrCreatorConfig: Run-wide settings used by all countries and stages.
    """
    
    control_dataset_names = control["control_dataset_names"]
    control_country_wise_inputs = control["control_country_wise_inputs"]
    control_configurations = control["control_configurations"]
    control_paths = control["control_paths"]
    control_analysis_inputs = control["control_analysis_inputs"]

    home_directory = Path(str(control_paths.loc["home_directory"][0]))
    input_spatial_datasets_folder = Path(
        str(control_paths.loc["folder_address_input_spatial_datasets"][0])
    )
    country_maps_for_clipping_folder = Path(home_directory / "region_boundary_maps")
    countries = pd.read_csv(
        control_paths.loc["file_address_country_names_list"][0],
        names=["country"]
    )

    file_name_population_density = str(control_dataset_names.loc["file_name_population_density"][0])
    file_name_land_cover = str(control_dataset_names.loc["file_name_land_cover"][0])
    file_name_elevation = str(control_dataset_names.loc["file_name_elevation"][0])
    file_name_protected_areas = str(control_dataset_names.loc["file_name_protected_areas"][0])
    file_name_substations = str(control_dataset_names.loc["file_name_substations"][0])
    file_name_urban_area_load_centers = str(control_dataset_names.loc["file_name_urban_area_load_centers"][0])
    file_name_roads = str(control_dataset_names.loc["file_name_roads"][0])
    file_name_power_grid = str(control_dataset_names.loc["file_name_power_grid"][0])
    file_name_transmission_grid = str(control_dataset_names.loc["file_name_transmission_grid"][0])
    file_name_continent_distance_surface_tgrid = str(control_dataset_names.loc["file_name_continent_distance_surface_tgrid"][0])
    file_name_distribution_grid = str(control_dataset_names.loc["file_name_distribution_grid"][0])
    file_name_country_boundaries = str(control_dataset_names.loc["file_name_country_boundaries"][0])
    file_name_water_bodies = str(control_dataset_names.loc["file_name_water_bodies"][0])

    re_technology = str(control_configurations.loc["re_technology"][0])
    relax_thresholds_for_resource_lagging_countries = bool(
        control_configurations.loc["relax_thresholds_for_resource_lagging_countries"][0]
        )
    roads_buffered_search=bool(
            control_configurations.loc["roads_buffered_search"][0]
        )
    grid_buffered_search=bool(
            control_configurations.loc["grid_buffered_search"][0]
        )
    band_count_for_multi_resolve_algorithm=int(
            control_configurations.loc["band_count_for_multi_resolve_algorithm"][0]
        )
    default_min_capacity_suitable_to_create_msr_mw = float(
        control_configurations.loc["default_min_capacity_suitable_to_create_msr_mw"][0]
    )

    resource_relaxation_step = float(control_analysis_inputs.loc["resource_relaxation_step"][0])
    min_suitable_country_area = float(float(control_analysis_inputs.loc["min_suitable_country_area"][0]) / 100)
    elevation_threshold = int(control_analysis_inputs.loc["elevation_threshold"][0])
    hours_in_year = int(control_analysis_inputs.loc["hours_in_year"][0])
    days_in_year = int(control_analysis_inputs.loc["days_in_year"][0])
    population_threshold = int(control_analysis_inputs.loc["population_threshold"][0])
    max_msr_capacity = int(control_analysis_inputs.loc["msr_max_capacity_allowed"][0])
    land_cover_classes = [
        int(value.strip())
        for value in str(control_analysis_inputs.loc["land_cover_classes"][0]).split(",")
        if value.strip()
    ]
    road_type = int(control_analysis_inputs.loc["road_type"][0])

    pv_conversion_efficiency = float(control_analysis_inputs.loc["pv_conversion_efficiency"][0]) / 100
    pv_spacing_factor = float(control_analysis_inputs.loc["pv_spacing_factor"][0]) / 100

    csp_land_classes = np.array(
        [
            float(value.strip())
            for value in str(control_analysis_inputs.loc["csp_land_classes"][0]).split(",")
        ],
        dtype=float,
    )
    csp_production_percentage_per_land_class = np.array(
        [
            float(value.strip())
            for value in str(
                control_analysis_inputs.loc["csp_production_percentage_per_land_class"][0]).split(",")
        ],
        dtype=float,
    )

    wind_turbine_capacity_watts = float(control_analysis_inputs.loc["wind_turbine_capacity_watts"][0]) 
    wind_rotor_diameter_meters = int(
        control_analysis_inputs.loc["wind_rotor_diameter_meters"][0]
    )
    wind_spacing_downwind_rotor_diameters = int(
        control_analysis_inputs.loc["wind_spacing_downwind_rotor_diameters"][0]
    )
    wind_spacing_crosswind_rotor_diameters = int(
        control_analysis_inputs.loc["wind_spacing_crosswind_rotor_diameters"][0]
    )
    wind_land_classes = np.array(
        [
            float(value.strip())
            for value in str(control_analysis_inputs.loc["wind_land_classes"][0]).split(",")
        ],
        dtype=float,
    )
    wind_production_percentage_per_land_class = np.array(
        [
            float(value.strip())
            for value in str(
                control_analysis_inputs.loc["wind_production_percentage_per_land_class"][0]).split(",")
        ],
        dtype=float,
    )

    # Technology-specific assumptions define which resource raster is used and
    # how suitable area converts to indicative capacity/yield.
    if re_technology == 'solarpv':
        resource_raster_name = str(control_dataset_names.loc["file_name_ghi_map"][0])
        land_discount = float(
            float(control_analysis_inputs.loc["pv_land_discount_factor"][0]) / 100
        )
        slope_threshold = float(control_analysis_inputs.loc["pv_slope_threshold"][0])
        re_spatial_footprint_mw_per_km2 = float(control_analysis_inputs.loc["pv_footprint_mw_per_km2"][0])
        resource_lower_limit = float(control_analysis_inputs.loc["pv_ghi_lower_limit"][0])
        user_resource_threshold = float(control_analysis_inputs.loc["pv_ghi_threshold"][0])
        run_info_column_headers = ['resource_threshold_kwh_per_m2_day', 'yield_gwh']

    elif re_technology == 'solarcsp':
        resource_raster_name = str(control_dataset_names.loc["file_name_dni_map"][0])
        land_discount = float(
            float(control_analysis_inputs.loc["csp_land_discount_factor"][0]) / 100
        )
        slope_threshold = float(control_analysis_inputs.loc["csp_slope_threshold"][0])
        re_spatial_footprint_mw_per_km2 = float(control_analysis_inputs.loc["csp_footprint_mw_per_km2"][0])
        resource_lower_limit = float(control_analysis_inputs.loc["csp_dni_lower_limit"][0])
        user_resource_threshold = float(control_analysis_inputs.loc["csp_dni_threshold"][0])
        run_info_column_headers = ['resource_threshold_kwh_per_m2_day', 'yield_gwh']

    elif re_technology == 'wind':
        resource_raster_name = str(control_dataset_names.loc["file_name_wind_speed_map"][0])
        land_discount = float(
            float(control_analysis_inputs.loc["wind_land_discount_factor"][0]) / 100
        )
        slope_threshold = float(control_analysis_inputs.loc["wind_slope_threshold"][0])
        resource_lower_limit = float(control_analysis_inputs.loc["wind_speed_lower_limit"][0])
        user_resource_threshold = float(control_analysis_inputs.loc["wind_speed_threshold"][0])

        number_of_turbines_per_km2 = math.floor(
            1 / (
                wind_spacing_downwind_rotor_diameters
                * (wind_rotor_diameter_meters / 1000)
                * wind_spacing_crosswind_rotor_diameters
                * (wind_rotor_diameter_meters / 1000)
            )
        )
        re_spatial_footprint_mw_per_km2 = (
            number_of_turbines_per_km2
            * wind_turbine_capacity_watts
            / 1e6
        )
        run_info_column_headers = ['resource_threshold_m_per_s', 'yield_gwh']

    else:
        raise ValueError(f"Unsupported RE technology: {re_technology}")

    max_area_to_cap_msrs_km2 = (
        max_msr_capacity
        / land_discount
        / re_spatial_footprint_mw_per_km2
    )
    default_min_contiguous_area_suitable_for_msr_km2 = (
        default_min_capacity_suitable_to_create_msr_mw
        / land_discount
        / re_spatial_footprint_mw_per_km2
    )
    return MsrCreatorConfig(
        control_file=control_file,
        home_directory=home_directory,
        input_spatial_datasets_folder=input_spatial_datasets_folder,
        country_maps_for_clipping_folder=country_maps_for_clipping_folder,
        countries=countries,
        re_technology=re_technology,
        road_type=road_type,
        relax_thresholds_for_resource_lagging_countries=
            relax_thresholds_for_resource_lagging_countries,
        roads_buffered_search=roads_buffered_search,
        grid_buffered_search=grid_buffered_search,
        band_count_for_multi_resolve_algorithm=(
            band_count_for_multi_resolve_algorithm
        ),
        default_min_capacity_suitable_to_create_msr_mw=(
            default_min_capacity_suitable_to_create_msr_mw
        ),
        stages_to_run=get_stages_to_run(control_configurations),
        control_dataset_names=control_dataset_names,
        control_country_wise_inputs=control_country_wise_inputs,
        control_configurations=control_configurations,
        control_paths=control_paths,
        control_analysis_inputs=control_analysis_inputs,
        file_name_population_density=file_name_population_density,
        file_name_land_cover=file_name_land_cover,
        file_name_elevation=file_name_elevation,
        file_name_protected_areas=file_name_protected_areas,
        file_name_substations=file_name_substations,
        file_name_urban_area_load_centers=file_name_urban_area_load_centers,
        file_name_roads=file_name_roads,
        file_name_power_grid=file_name_power_grid,
        file_name_transmission_grid=file_name_transmission_grid,
        file_name_continent_distance_surface_tgrid=(
            file_name_continent_distance_surface_tgrid
        ),
        file_name_distribution_grid=file_name_distribution_grid,
        file_name_country_boundaries=file_name_country_boundaries,
        file_name_water_bodies=file_name_water_bodies,
        resource_raster_name=resource_raster_name,
        land_discount=land_discount,
        slope_threshold=slope_threshold,
        population_threshold=population_threshold,
        re_spatial_footprint_mw_per_km2=re_spatial_footprint_mw_per_km2,
        resource_lower_limit=resource_lower_limit,
        user_resource_threshold=user_resource_threshold,
        run_info_column_headers=run_info_column_headers,
        max_area_to_cap_msrs_km2=max_area_to_cap_msrs_km2,
        land_cover_classes=land_cover_classes,
        default_min_contiguous_area_suitable_for_msr_km2=(
            default_min_contiguous_area_suitable_for_msr_km2
        ),
        elevation_threshold=elevation_threshold,
        min_suitable_country_area=min_suitable_country_area,
        resource_relaxation_step=resource_relaxation_step,
        hours_in_year=hours_in_year,
        days_in_year=days_in_year,
        wind_land_classes=wind_land_classes,
        wind_production_percentage_per_land_class=(
            wind_production_percentage_per_land_class
        ),
        wind_turbine_capacity_watts=wind_turbine_capacity_watts,
        wind_rotor_diameter_meters=wind_rotor_diameter_meters,
        wind_spacing_downwind_rotor_diameters=wind_spacing_downwind_rotor_diameters,
        wind_spacing_crosswind_rotor_diameters=wind_spacing_crosswind_rotor_diameters,
        csp_land_classes=csp_land_classes,
        csp_production_percentage_per_land_class=(
            csp_production_percentage_per_land_class
        ),
        pv_conversion_efficiency=pv_conversion_efficiency,
        pv_spacing_factor=pv_spacing_factor,
    )


def process_all_countries(config: MsrCreatorConfig) -> None:
    """Prepare shared country-boundary data and process configured countries.

    """

    LOGGER.info(
        f"Loading country boundaries | "
        f"path={config.input_spatial_datasets_folder / f'{config.file_name_country_boundaries}.shp'}"
    )
    country_boundaries = gpd.read_file(
        config.input_spatial_datasets_folder
        / f"{config.file_name_country_boundaries}.shp"
    )
    LOGGER.info(
        f"Processing countries | count={len(config.countries)} "
        f"| stages={config.stages_to_run} | technology={config.re_technology}"
    )

    for country_counter in range(0, len(config.countries)):
        country_name = config.countries.country[country_counter]
        context = prepare_country_context(country_name, config, country_boundaries)
        process_country(context, config)


def prepare_country_context(
    country_name: str,
    config: MsrCreatorConfig,
    country_boundaries: gpd.GeoDataFrame,
) -> CountryContext:
    """Prepare country-specific boundaries, output folders, and buffers.

    Country boundaries are written to the clipping folder in EPSG:4326 because
    many vector/raster clipping operations expect geographic coordinates. Area
    is calculated in ESRI:54009 so km2 values use a projected equal-area basis.

    Returns:
        CountryContext: Country-specific inputs, output paths, and optional
        road/grid buffer distances in metres.
    """

    country_name_without_spaces = country_name.replace(" ", "")

    roads_buffer_distance_m = None
    if config.roads_buffered_search:
        roads_buffer_distance_m = (
            int(config.control_country_wise_inputs.loc[country_name_without_spaces][1])
            * 1000
        )

    grid_buffer_distance_m = None
    if config.grid_buffered_search:
        grid_buffer_distance_m = (
            config.control_country_wise_inputs.loc[country_name_without_spaces][0]
            * 1000
        )

    output_folder = (
        control_subpath(config.control_paths.loc["folder_address_output_folder"][0])
        / country_name_without_spaces
    )
    paths = CountryPaths(
        output_folder=output_folder,
        stage_1_clipping_folder=output_folder / "stage1_input_datasets",
        stage_1_scoring_folder=output_folder / "stage1_scored_datasets",
        stage_2_competitive_resource_folder=(
            output_folder / "stage2_competitive_resource_area"
        ),
        polygonization_folder=output_folder / "stage4_polygonization",
        final_msrs_folder=output_folder / "stage4_msr",
        final_msrs_path=(
            output_folder / "stage4_msr" / f"{config.re_technology}_final_msrs.shp"
        ),
    )
    paths.stage_1_clipping_folder.mkdir(parents=True, exist_ok=True)
    paths.stage_1_scoring_folder.mkdir(parents=True, exist_ok=True)
    paths.stage_2_competitive_resource_folder.mkdir(parents=True, exist_ok=True)
    paths.polygonization_folder.mkdir(parents=True, exist_ok=True)
    paths.final_msrs_folder.mkdir(parents=True, exist_ok=True)

    single_country_boundary = country_boundaries[
        country_boundaries.name == country_name
    ]
    config.country_maps_for_clipping_folder.mkdir(parents=True, exist_ok=True)
    single_country_boundary.to_crs('EPSG:4326').to_file(
        config.country_maps_for_clipping_folder / f"{country_name_without_spaces}.shp"
    )
    country_area_km2 = (
        single_country_boundary.to_crs("ESRI:54009").area.iloc[0] / 1000000
    )
    LOGGER.info(
        f"Prepared country context | country={country_name_without_spaces} "
        f"| area={country_area_km2:.2f} km2 | output={output_folder}"
    )

    return CountryContext(
        country_name_with_spaces=country_name,
        country_name_without_spaces=country_name_without_spaces,
        single_country_boundary=single_country_boundary,
        country_area_km2=country_area_km2,
        paths=paths,
        roads_buffer_distance_m=roads_buffer_distance_m,
        grid_buffer_distance_m=grid_buffer_distance_m,
    )


def process_country(context: CountryContext, config: MsrCreatorConfig) -> None:
    """Run enabled workflow stages for a single country."""

    LOGGER.info(
        f"Starting country workflow | country={context.country_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    for stage in config.stages_to_run:
        if context.stop_processing:
            break
        stage_name = {
            1: "Stage 1 clipping, distance surfaces, and scoring",
            2: "Stage 2 competitive resource",
            3: "Stage 4 part i polygonization",
            4: "Stage 4 part ii attribution",
        }.get(stage, f"Unknown stage {stage}")

        LOGGER.info(
            f"Starting {stage_name} | country={context.country_name_without_spaces} "
            f"| technology={config.re_technology}"
        )
        try:
            if stage == 1:
                run_stage_1_clipping_and_scoring(context, config)
            elif stage == 2:
                run_stage_2_competitive_resource(context, config)
            elif stage == 3:
                run_stage_4_polygonization(context, config)
            elif stage == 4:
                run_stage_4_attribution(context, config)
            else:
                LOGGER.warning(
                    f"Skipping unknown stage {stage} | "
                    f"country={context.country_name_without_spaces}"
                )
                continue
        except Exception:
            LOGGER.exception(
                f"{stage_name} failed | country={context.country_name_without_spaces} "
                f"| technology={config.re_technology}"
            )
            raise
        LOGGER.info(
            f"Finished {stage_name} | country={context.country_name_without_spaces}"
        )

    LOGGER.info(
        f"Finished country workflow | country={context.country_name_without_spaces}"
    )


def run_stage_1_clipping_and_scoring(
    context: CountryContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 1: clip inputs, build distance surfaces, and score suitability.

    Raster and vector inputs are first clipped to the active country. Raster
    outputs are reprojected to ESRI:54009 so distance and area-based operations
    use metre units. Scored layers use the existing convention where values
    encode unsuitable, partially suitable, or suitable pixels for later
    multiplication in Stage 2.
    """

    paths = context.paths
    single_country = context.single_country_boundary
    upper_left_x, lower_right_y, lower_right_x, upper_left_y = (
        single_country.total_bounds
    )
    min_x, min_y, max_x, max_y = single_country.total_bounds
    clip_geometry = json.dumps(
        mapping(box(upper_left_x, upper_left_y, lower_right_x, lower_right_y))
    )

    LOGGER.info(
        f"Stage 1 part i clipping and distance surfaces started | "
        f"country={context.country_name_without_spaces}"
    )

    raster_names = [
        config.file_name_population_density,
        config.file_name_land_cover,
        config.file_name_elevation,
        config.resource_raster_name,
    ]
    for raster_name in raster_names:
        input_raster_dataset = xarray.open_dataarray(
            config.input_spatial_datasets_folder / f"{raster_name}.tif"
        )
        clipped_raster = input_raster_dataset.rio.clip_box(
            min_x, min_y, max_x, max_y
        )
        del input_raster_dataset
        clipped_raster = clipped_raster.rio.clip(single_country.geometry)
        clipped_raster.rio.to_raster(
            paths.stage_1_clipping_folder
            / f"{config.re_technology}_{raster_name}_clipped.tif"
        )
        projected_raster = clipped_raster.rio.reproject("ESRI:54009")
        projected_raster.rio.to_raster(
            paths.stage_1_clipping_folder
            / f"{config.re_technology}_{raster_name}_projected.tif"
        )
        LOGGER.info(
            f"Raster clipped and projected to ESRI:54009 | "
            f"country={context.country_name_without_spaces} | layer={raster_name}"
        )
    del clipped_raster, projected_raster

    vector_names = [
        config.file_name_roads,
        config.file_name_water_bodies,
        config.file_name_power_grid,
        config.file_name_transmission_grid,
        config.file_name_distribution_grid,
        config.file_name_protected_areas,
    ]
    for vector_name in vector_names:
        # Read with bbox first to reduce IO before exact country clipping.
        clipped_vector = gpd.read_file(
            config.input_spatial_datasets_folder / f"{vector_name}.shp",
            bbox=tuple(single_country.total_bounds),
        )

        if not clipped_vector.empty and vector_name == config.file_name_roads:
            clipped_vector = clipped_vector[
                clipped_vector.GP_RTP <= config.road_type
            ]

        if not clipped_vector.empty:
            clipped_vector = gpd.clip(clipped_vector, single_country.envelope)
            clipped_vector["raster_value"] = 1
        else:
            LOGGER.warning(
                f"Vector layer has no features in country extent; using fallback "
                f"mask | country={context.country_name_without_spaces} "
                f"| layer={vector_name}"
            )
            # Empty layers are represented explicitly so rasterization produces
            # a complete mask. Exclusion layers use 0 to preserve exclusion
            # semantics when the source feature is absent.
            clipped_vector = gpd.GeoDataFrame(
                {'geometry': single_country.envelope},
                geometry='geometry',
            )
            clipped_vector["raster_value"] = 1
            if vector_name in [
                config.file_name_protected_areas,
                config.file_name_water_bodies,
            ]:
                clipped_vector["raster_value"] = 0

        clipped_vector = gpd.clip(clipped_vector, single_country.geometry)
        if clipped_vector.empty:
            LOGGER.warning(
                f"Vector layer is empty after country clipping; using fallback "
                f"mask | country={context.country_name_without_spaces} "
                f"| layer={vector_name}"
            )
            clipped_vector = gpd.GeoDataFrame(
                {'geometry': single_country.geometry},
                geometry='geometry',
            )
            clipped_vector["raster_value"] = 1
            if vector_name in [
                config.file_name_protected_areas,
                config.file_name_water_bodies,
            ]:
                clipped_vector["raster_value"] = 0
        clipped_vector = clipped_vector.to_crs('EPSG:4326')
        clipped_vector.to_file(
            paths.stage_1_clipping_folder
            / f"{config.re_technology}_{vector_name}_clipped.shp"
        )
        LOGGER.info(
            f"Vector clipped | country={context.country_name_without_spaces} "
            f"| layer={vector_name}"
        )
        rasterized_clipped_vector = make_geocube(
            clipped_vector,
            measurements=["raster_value"],
            resolution=(0.0025, -0.0025),
            geom=clip_geometry,
        ).fillna(0)
        rasterized_clipped_vector = rasterized_clipped_vector.rio.clip(
            single_country.geometry)
        rasterized_clipped_vector.rio.reproject(
            "ESRI:54009").raster_value.rio.to_raster(
                paths.stage_1_clipping_folder / (
                    f"{config.re_technology}_{vector_name}_rasterized_projected.tif"
                )
            )
        LOGGER.info(
            f"Vector rasterized and projected to ESRI:54009 | "
            f"country={context.country_name_without_spaces} | layer={vector_name}"
        )
    del clipped_vector, rasterized_clipped_vector

    elevation_raster = rd.LoadGDAL(
        str(paths.stage_1_clipping_folder / (
            f"{config.re_technology}_{config.file_name_elevation}_projected.tif"
        ))
    )
    slope_raster = rd.TerrainAttribute(
        elevation_raster,
        attrib='slope_percentage',
    )
    rd.SaveGDAL(
        str(paths.stage_1_clipping_folder / f"{config.re_technology}_slope_projected.tif"),
        slope_raster,
    )
    LOGGER.info(
        f"Slope raster created | country={context.country_name_without_spaces} "
        f"| path={paths.stage_1_clipping_folder / f'{config.re_technology}_slope_projected.tif'}"
    )
    del elevation_raster, slope_raster

    distance_dataset_names = [
        config.file_name_power_grid,
        config.file_name_transmission_grid,
        config.file_name_distribution_grid,
        config.file_name_roads,
    ]
    for dataset_name in distance_dataset_names:
        raster = xarray.open_dataarray(
            paths.stage_1_clipping_folder / (
                f"{config.re_technology}_{dataset_name}_rasterized_projected.tif"
            )
        )
        distance_surface = xrspatial.proximity(
            raster.squeeze('band'),
            distance_metric="EUCLEADIAN",
        )
        # Distance surfaces are generated in projected units and clipped in the
        # geographic CRS to match country-boundary geometry handling.
        distance_surface = distance_surface.rio.reproject("EPSG:4326")
        distance_surface = distance_surface.rio.clip(single_country.envelope)
        distance_surface = distance_surface.rio.clip(single_country.geometry)
        distance_surface = distance_surface.rio.reproject("ESRI:54009")
        distance_surface.rio.to_raster(
            paths.stage_1_clipping_folder / (
                f"{config.re_technology}_distance_surface_{dataset_name}.tif"
            )
        )
        LOGGER.info(
            f"Distance surface created | country={context.country_name_without_spaces} "
            f"| layer={dataset_name}"
        )

    LOGGER.info(
        f"Stage 1 part i clipping and distance surfaces finished | "
        f"country={context.country_name_without_spaces}"
    )
    LOGGER.info(
        f"Stage 1 part ii scoring started | "
        f"country={context.country_name_without_spaces}"
    )

    layer_to_score_names = [
        f"{config.file_name_population_density}_projected",
        f"{config.file_name_land_cover}_projected",
        f"{config.file_name_elevation}_projected",
        f"{config.file_name_protected_areas}_rasterized_projected",
        f"{config.file_name_water_bodies}_rasterized_projected",
        f"distance_surface_{config.file_name_roads}",
        f"distance_surface_{config.file_name_transmission_grid}",
        "slope_projected",
        f"{config.resource_raster_name}_projected",
    ]
    for layer_to_score_name in layer_to_score_names:
        layer_to_score = xarray.open_dataarray(
            paths.stage_1_clipping_folder / (
                f"{config.re_technology}_{layer_to_score_name}.tif"
            )
        )
        scored_layer = layer_to_score * 0

        if layer_to_score_name == f"{config.file_name_land_cover}_projected":
            scored_layer = scored_layer.where(
                ~layer_to_score.isin(config.land_cover_classes), 1)

        if layer_to_score_name == f"{config.file_name_elevation}_projected":
            scored_layer = scored_layer.where(~(layer_to_score < config.elevation_threshold), 1)

        if layer_to_score_name == f"{config.file_name_population_density}_projected":
            scored_layer = scored_layer.where(
                ~(layer_to_score <= config.population_threshold),
                1,
            )

        if layer_to_score_name == f"{config.file_name_protected_areas}_rasterized_projected":
            scored_layer = scored_layer.where(~(layer_to_score == 0), 1)

        if layer_to_score_name == f"{config.file_name_water_bodies}_rasterized_projected":
            scored_layer = scored_layer.where(~(layer_to_score == 0), 1)

        if layer_to_score_name == f"distance_surface_{config.file_name_roads}":
            if config.roads_buffered_search:
                scored_layer = scored_layer.where(
                    ~(layer_to_score <= context.roads_buffer_distance_m), 1)
            else:
                scored_layer = scored_layer.where(~(layer_to_score >= 0), 1)

        if layer_to_score_name == f"distance_surface_{config.file_name_transmission_grid}":
            if config.grid_buffered_search:
                scored_layer = scored_layer.where(
                    ~(layer_to_score <= context.grid_buffer_distance_m),
                    1,
                )
            else:
                scored_layer = scored_layer.where(~(layer_to_score >= 0), 1)

        if layer_to_score_name == "slope_projected":
            scored_layer = scored_layer.where(
                ~(layer_to_score <= config.slope_threshold),
                1,
            )

        if layer_to_score_name == f"{config.resource_raster_name}_projected":
            scored_layer = scored_layer.where(
                ~(layer_to_score < config.resource_lower_limit), -1)
            scored_layer = scored_layer.where(
                ~(layer_to_score > config.user_resource_threshold), 1)
            scored_layer = scored_layer.where(
                ~(scored_layer == 0),
                (layer_to_score - config.resource_lower_limit)
                / (config.user_resource_threshold - config.resource_lower_limit),
            )

        scored_layer.rio.to_raster(
            paths.stage_1_scoring_folder / (
                f"{config.re_technology}_{layer_to_score_name}_scored.tif"
            )
        )
        LOGGER.info(
            f"Layer scored | country={context.country_name_without_spaces} "
            f"| layer={layer_to_score_name}"
        )
    LOGGER.info(
        f"Stage 1 part ii scoring finished | "
        f"country={context.country_name_without_spaces}"
    )


def run_stage_2_competitive_resource(
    context: CountryContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 2: identify competitive resource and optional sufficiency output.

    Scored layers are multiplied so exclusion masks remove unsuitable pixels
    while the resource layer preserves relative resource quality. If configured,
    the sufficiency stage relaxes the resource threshold for countries where
    the retained suitable area is below the configured country-area share.
    """

    paths = context.paths
    single_country = context.single_country_boundary
    LOGGER.info(
        f"Stage 2 competitive resource started | "
        f"country={context.country_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    resource_raster = xarray.open_dataarray(
        paths.stage_1_clipping_folder / (
            f"{config.re_technology}_{config.resource_raster_name}_projected.tif"
        )
    )
    suitable_area_raster_with_no_exclusions = xarray.open_dataarray(
        paths.stage_1_scoring_folder / (
            f"{config.re_technology}_{config.resource_raster_name}_projected_scored.tif"
        )
    )

    suitable_area_raster = suitable_area_raster_with_no_exclusions
    exclusion_count = 0
    scored_layer_names = [
        f"{config.file_name_population_density}_projected",
        f"{config.file_name_land_cover}_projected",
        f"{config.file_name_elevation}_projected",
        f"{config.file_name_elevation}_projected",
        f"{config.file_name_protected_areas}_rasterized_projected",
        f"{config.file_name_water_bodies}_rasterized_projected",
        f"distance_surface_{config.file_name_roads}",
        f"distance_surface_{config.file_name_transmission_grid}",
        "slope_projected",
    ]
    for scored_layer_name in scored_layer_names:
        scored_layer = xarray.open_dataarray(
            paths.stage_1_scoring_folder / (
                f"{config.re_technology}_{scored_layer_name}_scored.tif"
            )
        )
        if not (
            config.re_technology != 'wind'
            and scored_layer_name == f"{config.file_name_elevation}_projected"
        ):
            suitable_area_raster = suitable_area_raster * scored_layer.reindex(
                {'x': suitable_area_raster.x, 'y': suitable_area_raster.y},
                method="nearest",
            )
            competitive_area_raster = suitable_area_raster * 0
            competitive_area_raster = competitive_area_raster.where(
                ~(suitable_area_raster >= 1),
                1,
            )

            suitable_area_resource_raster = resource_raster.where(
                ~(suitable_area_raster <= 0),
                0,
            )
            competitive_area_resource_raster = resource_raster.where(
                ~(suitable_area_raster < 1),
                0,
            )
        exclusion_count = exclusion_count + 1

    suitable_area_resource_raster = suitable_area_resource_raster.rio.reproject(
        "EPSG:4326"
    )
    suitable_area_resource_raster = suitable_area_resource_raster.rio.clip(
        single_country.geometry
    )
    suitable_area_resource_raster = suitable_area_resource_raster.rio.reproject(
        "ESRI:54009"
    )
    suitable_area_resource_raster.rio.to_raster(
        paths.stage_2_competitive_resource_folder / (
            f"{config.re_technology}_suitable_resource.tif"
        )
    )
    LOGGER.info(
        f"Suitable resource raster written | "
        f"country={context.country_name_without_spaces} "
        f"| path={paths.stage_2_competitive_resource_folder / f'{config.re_technology}_suitable_resource.tif'}"
    )

    competitive_area_resource_raster = (
        competitive_area_resource_raster.rio.reproject("EPSG:4326"))
    competitive_area_resource_raster = competitive_area_resource_raster.rio.clip(
        single_country.geometry)
    competitive_area_resource_raster = (
        competitive_area_resource_raster.rio.reproject("ESRI:54009"))
    competitive_area_resource_raster.rio.to_raster(
        paths.stage_2_competitive_resource_folder / (
            f"{config.re_technology}_competitive_resource.tif"))
    LOGGER.info(
        f"Competitive resource raster written | "
        f"country={context.country_name_without_spaces} "
        f"| path={paths.stage_2_competitive_resource_folder / f'{config.re_technology}_competitive_resource.tif'}"
    )

    if config.relax_thresholds_for_resource_lagging_countries:
        LOGGER.info(
            f"Resource sufficiency check started | "
            f"country={context.country_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

        suitable_area_resource_raster_path = (
            paths.stage_2_competitive_resource_folder / (
                f"{config.re_technology}_suitable_resource.tif"))

        resource_threshold, indicative_yield_gwh = run_resource_sufficiency_stage(
            suitable_area_resource_raster_path,
            config.user_resource_threshold,
            config.resource_lower_limit,
            config.re_technology,
            config.land_discount,
            config.re_spatial_footprint_mw_per_km2,
            config.wind_rotor_diameter_meters,
            config.wind_spacing_downwind_rotor_diameters,
            config.wind_spacing_crosswind_rotor_diameters,
            config.wind_turbine_capacity_watts,
            config.default_min_contiguous_area_suitable_for_msr_km2,
            context.country_area_km2,
            config.min_suitable_country_area,
            config.resource_relaxation_step,
            config.hours_in_year,
            config.days_in_year,
            config.wind_land_classes,
            config.wind_production_percentage_per_land_class,
            config.csp_land_classes,
            config.csp_production_percentage_per_land_class,
            config.pv_conversion_efficiency,
            config.pv_spacing_factor
        )

        if resource_threshold < config.user_resource_threshold:
            LOGGER.warning(
                f"Resource threshold relaxed | "
                f"country={context.country_name_without_spaces} "
                f"| original={config.user_resource_threshold:.3f} "
                f"| relaxed={resource_threshold:.3f}"
            )
            competitive_area_resource_raster = suitable_area_resource_raster.where(
                ~(suitable_area_resource_raster < resource_threshold), 0)
            competitive_area_resource_raster = (
                competitive_area_resource_raster.rio.reproject("EPSG:4326"))
            competitive_area_resource_raster = (
                competitive_area_resource_raster.rio.clip(single_country.geometry))
            competitive_area_resource_raster = (
                competitive_area_resource_raster.rio.reproject("ESRI:54009"))
            competitive_area_resource_raster.rio.to_raster(
                paths.stage_2_competitive_resource_folder / (
                    f"{config.re_technology}_competitive_resource_relaxed.tif"))
            LOGGER.info(
                f"Relaxed competitive resource raster written | "
                f"country={context.country_name_without_spaces} "
                f"| path={paths.stage_2_competitive_resource_folder / f'{config.re_technology}_competitive_resource_relaxed.tif'}"
            )
            config.log_file = config.log_file.append(pd.DataFrame(
                [
                    f"{context.country_name_without_spaces}: Resource threshold "
                    f"reduced to: {resource_threshold}"
                ],
                columns=['log'],
            ))

        pd.DataFrame(
            {
                'resource_threshold': resource_threshold,
                'indicative_yield_gwh': indicative_yield_gwh,
                'minimum_msr_capacity_criteria': (
                    config.default_min_capacity_suitable_to_create_msr_mw),
            },
            index=[0],
        ).to_csv(
            paths.stage_2_competitive_resource_folder / (
                f"{config.re_technology}_log_resource_identification_polygonization.csv"))
        LOGGER.info(
            f"Stage 2 competitive resource finished with sufficiency check | "
            f"country={context.country_name_without_spaces} "
            f"| threshold={resource_threshold:.3f} "
            f"| indicative_yield={indicative_yield_gwh:.3f} GWh"
        )

    else:
        resource_threshold = config.user_resource_threshold
        indicative_yield_gwh = np.nan
        pd.DataFrame(
            {
                'resource_threshold': resource_threshold,
                'indicative_yield_gwh': indicative_yield_gwh,
                'minimum_msr_capacity_criteria': (
                    config.default_min_capacity_suitable_to_create_msr_mw),
            },
            index=[0],
        ).to_csv(
            paths.stage_2_competitive_resource_folder / (
                f"{config.re_technology}_log_resource_identification_polygonization.csv"))
        LOGGER.info(
            f"Stage 2 competitive resource finished without sufficiency check | "
            f"country={context.country_name_without_spaces} "
            f"| threshold={resource_threshold:.3f}"
        )


def run_stage_4_polygonization(
    context: CountryContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 4 part i: polygonize competitive resource rasters into MSRs.

    The selected competitive resource raster is converted from suitable pixels
    to vector MSR polygons. Polygon size limits are applied in km2, and the
    configured band count controls how many resource-quality intervals are
    resolved before merging.
    """

    paths = context.paths
    LOGGER.info(
        f"Stage 4 part i polygonization started | "
        f"country={context.country_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    inputs_from_stage2 = pd.read_csv(
        paths.stage_2_competitive_resource_folder / (
            f"{config.re_technology}_log_resource_identification_polygonization.csv"))
    resource_threshold = inputs_from_stage2.resource_threshold.values[0]
    indicative_yield_gwh = inputs_from_stage2.indicative_yield_gwh.values[0]

    run_info_values = [(resource_threshold, indicative_yield_gwh)]
    run_info = pd.DataFrame(run_info_values, columns=config.run_info_column_headers)
    run_info.to_csv(
        paths.final_msrs_folder / f"{config.re_technology}run_info.csv",
        index=False,
    )

    if resource_threshold < config.user_resource_threshold:
        resource_potential_raster_path = (
            paths.stage_2_competitive_resource_folder / (
                f"{config.re_technology}_competitive_resource_relaxed.tif"))
    else:
        resource_potential_raster_path = (
            paths.stage_2_competitive_resource_folder / (
                f"{config.re_technology}_competitive_resource.tif"))

    min_contiguous_area_suitable_for_msr_km2 = (
        config.default_min_contiguous_area_suitable_for_msr_km2)

    msrs = polygonize_resource_potential(
        resource_potential_raster_path,
        paths.polygonization_folder,
        config.re_technology,
        resource_threshold,
        config.band_count_for_multi_resolve_algorithm,
        config.max_area_to_cap_msrs_km2,
        min_contiguous_area_suitable_for_msr_km2,
    )

    if type(msrs) == int:
        if paths.final_msrs_path.is_file():
            for suffix in [".shp", ".shx", ".prj", ".cpg", ".dbf"]:
                (paths.final_msrs_folder / f"{config.re_technology}_final_msrs{suffix}").unlink()
        LOGGER.warning(
            f"No MSRs created because sufficient resource was not found | "
            f"country={context.country_name_without_spaces} "
            f"| technology={config.re_technology}"
        )
        config.log_file = config.log_file.append(pd.DataFrame(
            [
                f"{context.country_name_without_spaces}: Sufficient resource "
                "not found to create any MSRs"
            ],
            columns=['log'],
        ))
        context.stop_processing = True
    else:
        msrs.to_file(paths.final_msrs_path)
        LOGGER.info(
            f"MSR polygons written | country={context.country_name_without_spaces} "
            f"| path={paths.final_msrs_path}"
        )


def run_stage_4_attribution(
    context: CountryContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 4 part ii: attribute final MSRs with capacity and proximity data.

    Capacity is estimated from MSR area, ``land_discount``, and
    ``re_spatial_footprint_mw_per_km2``. Road, grid, substation, and load-centre
    distances are reported in kilometres after metre-based projected CRS
    distance calculations.
    """

    paths = context.paths
    single_country = context.single_country_boundary
    LOGGER.info(
        f"Stage 4 part ii attribution started | "
        f"country={context.country_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    msrs = gpd.read_file(paths.final_msrs_path)
    msrs['AreakM2'] = msrs.geometry.area / 1000000
    msrs['CapacityMW'] = (
        msrs['AreakM2'] * config.land_discount * config.re_spatial_footprint_mw_per_km2)

    distance_to_roads_stats_per_msr = zonal_stats(
        paths.final_msrs_path,
        paths.stage_1_clipping_folder / (
            f"{config.re_technology}_distance_surface_{config.file_name_roads}.tif"),
        stats="count min mean max median sum",
    )
    msrs['RoadDist'] = (
        pd.DataFrame(distance_to_roads_stats_per_msr)['mean'] / 1000)
    LOGGER.info(
        f"Road distances attributed | country={context.country_name_without_spaces}"
    )

    clipped_vector = gpd.read_file(
        config.input_spatial_datasets_folder / f"{config.file_name_transmission_grid}.shp",
        bbox=tuple(single_country.total_bounds),
    )
    if not clipped_vector.empty:
        clipped_vector = gpd.clip(clipped_vector, single_country.geometry)
    if clipped_vector.empty:
        distance_to_tgrid_stats_per_msr = zonal_stats(
            paths.final_msrs_path,
            config.input_spatial_datasets_folder / (
                f"{config.file_name_continent_distance_surface_tgrid}.tif"),
            stats="count min mean max median sum",
        )
        msrs['T_Dist_gf'] = (
            pd.DataFrame(distance_to_tgrid_stats_per_msr)['mean'] / 1000)
        LOGGER.warning(
            f"Transmission grid absent in country; using continent distance "
            f"surface fallback | country={context.country_name_without_spaces}"
        )
    else:
        distance_to_tgrid_stats_per_msr = zonal_stats(
            paths.final_msrs_path,
            paths.stage_1_clipping_folder / (
                f"{config.re_technology}_distance_surface_{config.file_name_transmission_grid}.tif"),
            stats="count min mean max median sum",
        )

        msrs['T_Dist_gf'] = (
            pd.DataFrame(distance_to_tgrid_stats_per_msr)['mean'] / 1000)
        LOGGER.info(
            f"Transmission grid distances attributed | "
            f"country={context.country_name_without_spaces}"
        )

    distance_to_dgrid_stats_per_msr = zonal_stats(
        paths.final_msrs_path,
        paths.stage_1_clipping_folder / (
            f"{config.re_technology}_distance_surface_{config.file_name_distribution_grid}.tif"),
        stats="count min mean max median sum",
    )
    msrs['D_Dist_gf'] = (
        pd.DataFrame(distance_to_dgrid_stats_per_msr)['mean'] / 1000)
    LOGGER.info(
        f"Distribution grid distances attributed | "
        f"country={context.country_name_without_spaces}"
    )

    msrs['TD_Dist_gf'] = msrs[['T_Dist_gf', 'D_Dist_gf']].min(axis=1)
    LOGGER.info(
        f"Closest grid distances attributed | "
        f"country={context.country_name_without_spaces}"
    )

    substations = gpd.read_file(
        config.input_spatial_datasets_folder / f"{config.file_name_substations}.shp",
        bbox=tuple(single_country.total_bounds),
    ).to_crs("ESRI:54009")
    msrs['SubstnDist'] = (
        msrs.centroid.apply(
            minimum_distance_of_msr_centroid_from_geometry_set,
            geometry_set=substations.centroid,
        )
        / 1000
    )
    LOGGER.info(
        f"Substation distances attributed | country={context.country_name_without_spaces}"
    )

    load_centers = gpd.read_file(
        config.input_spatial_datasets_folder / (
            f"{config.file_name_urban_area_load_centers}.shp"),
        bbox=tuple(single_country.total_bounds),
    ).to_crs("ESRI:54009")
    msrs['Load_dst'] = (
        msrs.centroid.apply(
            minimum_distance_of_msr_centroid_from_geometry_set,
            geometry_set=load_centers.centroid,
        )
        / 1000
    )
    LOGGER.info(
        f"Load-centre distances attributed | "
        f"country={context.country_name_without_spaces}"
    )

    load_center_attributes = msrs.centroid.apply(
        compute_load_center_attributes_for_msr_centroid,
        load_centers=load_centers,
    )
    load_center_related_attributes = pd.DataFrame(
        load_center_attributes.tolist(),
        columns=[
            'closest_city_name',
            'closest_city_population_count',
            'pop_within_100km',
            'city_count_within_100km',
            'cities_100km',
        ],
        index=load_center_attributes.index,
    )
    msrs['City_name'] = load_center_related_attributes['closest_city_name']
    msrs['City_Pop'] = load_center_related_attributes[
        'closest_city_population_count']
    msrs['CtLst100kM'] = load_center_related_attributes['cities_100km']
    msrs['CtCnt100kM'] = load_center_related_attributes[
        'city_count_within_100km']
    msrs['PopIn100kM'] = load_center_related_attributes['pop_within_100km']
    LOGGER.info(
        f"Load-centre attributes inserted | country={context.country_name_without_spaces}"
    )
    msrs.to_file(paths.final_msrs_path)

    LOGGER.info(
        f"Attribution complete | country={context.country_name_without_spaces} "
        f"| path={paths.final_msrs_path}"
    )

    config.log_file = config.log_file.append(pd.DataFrame(
        [f"{context.country_name_without_spaces}:Attribution completed"],
        columns=['log'],
    ))
    date_time_stamp = time.localtime()
    date_time_stamp = (
        f"{date_time_stamp.tm_year}"
        f"{date_time_stamp.tm_mon}"
        f"{date_time_stamp.tm_mday}"
        f"{date_time_stamp.tm_hour}"
        f"{date_time_stamp.tm_min}"
        f"{date_time_stamp.tm_sec}"
    )
    config.log_file.to_csv(
        config.home_directory / f"{date_time_stamp}{config.re_technology}_log_file.csv"
    )


def main() -> None:
    """Load control inputs and run the configured MSR Creator workflow."""

    configure_logging()
    LOGGER.info("MSR Creator workflow started")
    try:
        control_file = Path(CONTROL_FILE_NAME)
        control = load_control_workbook(control_file)
        LOGGER.info(f"Control workbook loaded | path={control_file}")
        config = build_msr_creator_config(control_file, control)
        LOGGER.info(
            f"Configuration prepared | technology={config.re_technology} "
            f"| countries={len(config.countries)} | stages={config.stages_to_run}"
        )
        process_all_countries(config)
    except Exception:
        LOGGER.exception("MSR Creator workflow failed")
        raise
    LOGGER.info("MSR Creator workflow completed")


if __name__ == "__main__":
    main()
