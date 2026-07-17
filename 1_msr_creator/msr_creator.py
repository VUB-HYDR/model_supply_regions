"""Create Model Supply Regions (MSRs) from renewable-resource input layers.

The workflow reads an Excel control file, processes regional geospatial
inputs, scores suitability layers, identifies competitive renewable-resource
areas, optionally relaxes resource thresholds for resource-lagging regions,
polygonizes suitable areas, and attributes final MSRs with capacity, 
infrastructure distance, load-centre, land-cover, climate class and elevation metrics.
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

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
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
from rasterio.mask import mask
from rasterio.warp import Resampling, reproject
from rasterstats import zonal_stats
from scipy.ndimage import label
from shapely.geometry import LineString, MultiPolygon
from shapely.geometry import box, mapping
from shapely.ops import split
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry
def _disable_shapely_array_interface():
    def _raise_attribute_error(self):
        raise AttributeError("__array_interface__ is intentionally disabled")

    BaseGeometry.__array_interface__ = property(_raise_attribute_error)
    BaseMultipartGeometry.__array_interface__ = property(
        _raise_attribute_error
    )
    Polygon.__array_interface__ = property(_raise_attribute_error)


CONTROL_FILE_NAME = "control_file_msr_creator.xlsx"
PATHS_SHEET = "paths"
CONFIGURATIONS_SHEET = "configurations"
PARAMETERS_SHEET = "parameters"
DATASETS_SHEET = "datasets"

LOGGER = logging.getLogger(__name__)

@dataclass
class MsrCreatorConfig:
    """Run-wide settings derived from the MSR Creator control workbook."""

    control_file: Path
    control_datasets: pd.DataFrame
    input_folder_datasets: Path
    output_folder: Path
    regions: pd.DataFrame
    technologies_to_run: list[str]
    re_technology: str
    roads_buffered_search: bool
    grid_buffered_search: bool
    band_count_for_multi_resolve_algorithm: int
    msr_min_capacity_threshold: int
    msr_max_capacity_threshold: int
    stages_to_run: list[int]
    control_dataset_names: pd.DataFrame
    control_configurations: pd.DataFrame
    control_paths: pd.DataFrame
    control_parameters: pd.DataFrame
    file_name_population_density: str
    file_name_land_cover: str
    file_name_climate_classes: str
    file_name_elevation: str
    file_name_protected_areas: str
    file_name_substations: str
    file_name_urban_area_load_centers: str
    file_name_roads: str
    file_name_power_grid: str
    file_name_transmission_grid: str
    file_name_continent_distance_surface_tgrid: str
    file_name_distribution_grid: str
    file_name_region_boundaries: str
    file_name_water_bodies: str
    resource_raster_name: str
    road_type: int
    land_discount: float
    slope_threshold: float
    population_threshold: int
    re_spatial_footprint: float
    resource_lower_limit: float
    resource_threshold: float
    run_info_column_headers: list[str]
    max_area_to_cap_msrs_km2: float
    land_cover_classes: list[int]
    default_min_contiguous_area_suitable_for_msr_km2: float
    elevation_threshold: int
    hours_in_year: int
    days_in_year: int
    wind_spacing_downwind_rotor_diameters: int
    wind_spacing_crosswind_rotor_diameters: int
    wind_rotor_diameter: int
    wind_turbine_capacity: float


@dataclass
class RegionPaths:
    """Output paths for one region and technology."""

    output_folder_msr_creator: Path
    region_maps_for_clipping_folder: Path
    stage_1_clipping_folder: Path
    stage_2_scoring_folder: Path
    stage_3_competitive_resource_folder: Path
    stage_5_polygonization_folder: Path
    stage_6_attribution_folder: Path
    output_path: Path


@dataclass
class RegionContext:
    """Region-specific state prepared before running workflow stages."""

    region_name_with_spaces: str
    region_name_without_spaces: str
    single_region_boundary: gpd.GeoDataFrame
    region_area_km2: float
    paths: RegionPaths
    road_buffer_distance_m: float | None = None
    grid_buffer_distance_m: float | None = None
    stop_processing: bool = False
    final_resource_threshold: float | None = None
    indicative_yield_gwh: float | None = None
    competitive_resource_raster_path: Path | None = None

def configure_logging(level: int = logging.INFO) -> None:
    """Configure terminal logging for MSR workflow progress."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

def load_control_workbook(control_file: Path) -> dict[str, pd.DataFrame]:
    """Read the MSR Creator control workbook.

    Returns:
        dict[str, pandas.DataFrame]: A dictionary containing the control 
        workbook sheets used by the script
    """

    return {
        "control_datasets": pd.read_excel(
            control_file,
            sheet_name=DATASETS_SHEET,
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
        "control_parameters": (
            pd.read_excel(
                control_file,
                sheet_name=PARAMETERS_SHEET,
                index_col=0,
            )
        ),
    }


def build_msr_creator_config(
    control_file: Path,
    control: dict[str, pd.DataFrame],
) -> MsrCreatorConfig:
    """Build run-wide MSR Creator configuration from control DataFrames.

    Returns:
        MsrCreatorConfig: Run-wide settings used by all regions and stages.
    """

    control_paths = control["control_paths"]
    control_datasets = control["control_datasets"]
    control_configurations = control["control_configurations"]
    control_parameters = control["control_parameters"]

    # paths
    input_folder_datasets = Path(
        str(control_paths.loc["input_folder_datasets"][0])
    )
    output_folder = Path(str(control_paths.loc["output_folder"][0]))

    # datasets
    regions = pd.read_csv(
        Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
        f"{str(control_datasets.loc['file_name_regions'][0])}.csv"),
        names=["region"],
        sep=";"
    )
    file_name_region_boundaries = str(control_datasets.loc["file_name_region_boundaries"][0])
    file_name_population_density = str(control_datasets.loc["file_name_population_density"][0])
    file_name_urban_area_load_centers = str(control_datasets.loc["file_name_urban_area_load_centers"][0])
    file_name_land_cover = str(control_datasets.loc["file_name_land_cover"][0])
    file_name_elevation = str(control_datasets.loc["file_name_elevation"][0])
    file_name_climate_classes = str(control_datasets.loc["file_name_climate_classes"][0])
    file_name_protected_areas = str(control_datasets.loc["file_name_protected_areas"][0])
    file_name_water_bodies = str(control_datasets.loc["file_name_water_bodies"][0])
    file_name_roads = str(control_datasets.loc["file_name_roads"][0])
    file_name_substations = str(control_datasets.loc["file_name_substations"][0])
    file_name_power_grid = str(control_datasets.loc["file_name_power_grid"][0])
    file_name_transmission_grid = str(control_datasets.loc["file_name_transmission_grid"][0])
    file_name_continent_distance_surface_tgrid = str(control_datasets.loc["file_name_continent_distance_surface_tgrid"][0])
    file_name_distribution_grid = str(control_datasets.loc["file_name_distribution_grid"][0])

    # configurations
    re_technology = ""
    technologies_to_run = []
    if bool(control_configurations.loc["run_code_for_solar_pv"][0]):
        technologies_to_run.append("solarpv")
    if bool(control_configurations.loc["run_code_for_solar_csp"][0]):
        technologies_to_run.append("solarcsp")
    if bool(control_configurations.loc["run_code_for_wind"][0]):
        technologies_to_run.append("wind")
    if bool(control_configurations.loc["run_code_for_offshore_wind"][0]):
        technologies_to_run.append("offshorewind")

    roads_buffered_search=bool(
            control_configurations.loc["roads_buffered_search"][0]
        )
    grid_buffered_search=bool(
            control_configurations.loc["grid_buffered_search"][0]
        )
    
    
    # parameters
    band_count_for_multi_resolve_algorithm=int(
            control_parameters.loc["band_count_for_multi_resolve_algorithm"][0]
        )

    hours_in_year = int(control_parameters.loc["hours_in_year"][0])
    days_in_year = int(control_parameters.loc["days_in_year"][0])

    elevation_threshold = int(control_parameters.loc["elevation_threshold"][0])
    population_threshold = int(control_parameters.loc["population_threshold"][0])
    msr_max_capacity_threshold = int(control_parameters.loc["msr_max_capacity_threshold"][0])
    msr_min_capacity_threshold = int(control_parameters.loc["msr_min_capacity_threshold"][0])
    land_cover_classes = [
        int(value.strip())
        for value in str(control_parameters.loc["land_cover_classes"][0]).split(",")
        if value.strip()
    ]
    road_type = int(control_parameters.loc["road_type"][0])



    wind_turbine_capacity = float(control_parameters.loc["wind_turbine_capacity"][0]) 
    wind_rotor_diameter = int(
        control_parameters.loc["wind_rotor_diameter"][0]
    )
    wind_spacing_downwind_rotor_diameters = int(
        control_parameters.loc["wind_spacing_downwind_rotor_diameters"][0]
    )
    wind_spacing_crosswind_rotor_diameters = int(
        control_parameters.loc["wind_spacing_crosswind_rotor_diameters"][0]
    )

    # Technology-specific assumptions define which resource raster is used and
    # how suitable area converts to indicative capacity/yield.
    if "solarpv" in technologies_to_run:
        resource_raster_name = str(control_datasets.loc["file_name_ghi_map"][0])
        land_discount = float(
            float(control_parameters.loc["pv_land_discount_factor"][0]) / 100
        )
        slope_threshold = float(control_parameters.loc["pv_slope_threshold"][0])
        re_spatial_footprint = float(control_parameters.loc["pv_footprint"][0])
        resource_lower_limit = float(control_parameters.loc["pv_ghi_lower_limit"][0])
        resource_threshold = float(control_parameters.loc["pv_ghi_threshold"][0])
        run_info_column_headers = ['resource_threshold_kwh_per_m2_day', 'yield_gwh']

    elif "solarcsp" in technologies_to_run:
        resource_raster_name = str(control_datasets.loc["file_name_dni_map"][0])
        land_discount = float(
            float(control_parameters.loc["csp_land_discount_factor"][0]) / 100
        )
        slope_threshold = float(control_parameters.loc["csp_slope_threshold"][0])
        re_spatial_footprint = float(control_parameters.loc["csp_footprint"][0])
        resource_lower_limit = float(control_parameters.loc["csp_dni_lower_limit"][0])
        resource_threshold = float(control_parameters.loc["csp_dni_threshold"][0])
        run_info_column_headers = ['resource_threshold_kwh_per_m2_day', 'yield_gwh']

    elif "wind" in technologies_to_run:
        resource_raster_name = str(control_datasets.loc["file_name_wind_speed_map"][0])
        land_discount = float(
            float(control_parameters.loc["wind_land_discount_factor"][0]) / 100
        )
        slope_threshold = float(control_parameters.loc["wind_slope_threshold"][0])
        resource_lower_limit = float(control_parameters.loc["wind_speed_lower_limit"][0])
        resource_threshold = float(control_parameters.loc["wind_speed_threshold"][0])
        number_of_turbines_per_km2 = math.floor(
            1 / (
                wind_spacing_downwind_rotor_diameters
                * (wind_rotor_diameter / 1000)
                * wind_spacing_crosswind_rotor_diameters
                * (wind_rotor_diameter / 1000)
            )
        )
        re_spatial_footprint = (
            number_of_turbines_per_km2
            * wind_turbine_capacity
        )
        run_info_column_headers = ['resource_threshold_m_per_s', 'yield_gwh']
    # TODO: Add offshore wind
    
    else:
        raise ValueError(f"Unsupported RE technology: {re_technology}")

    max_area_to_cap_msrs_km2 = (
        msr_max_capacity_threshold
        / land_discount
        / re_spatial_footprint
    )
    default_min_contiguous_area_suitable_for_msr_km2 = (
        msr_min_capacity_threshold
        / land_discount
        / re_spatial_footprint
    )
    return MsrCreatorConfig(
        control_file=control_file,
        control_datasets=control_datasets,
        input_folder_datasets=input_folder_datasets,
        output_folder=output_folder,
        regions=regions,
        technologies_to_run=technologies_to_run,
        re_technology=re_technology,
        road_type=road_type,
        roads_buffered_search=roads_buffered_search,
        grid_buffered_search=grid_buffered_search,
        band_count_for_multi_resolve_algorithm=(
            band_count_for_multi_resolve_algorithm
        ),
        msr_max_capacity_threshold=msr_max_capacity_threshold,
        msr_min_capacity_threshold=msr_min_capacity_threshold,
        stages_to_run=get_stages_to_run(control_configurations),
        control_dataset_names=control_datasets,
        control_configurations=control_configurations,
        control_paths=control_paths,
        control_parameters=control_parameters,
        file_name_population_density=file_name_population_density,
        file_name_land_cover=file_name_land_cover,
        file_name_elevation=file_name_elevation,
        file_name_climate_classes=file_name_climate_classes,
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
        file_name_region_boundaries=file_name_region_boundaries,
        file_name_water_bodies=file_name_water_bodies,
        resource_raster_name=resource_raster_name,
        land_discount=land_discount,
        slope_threshold=slope_threshold,
        population_threshold=population_threshold,
        re_spatial_footprint=re_spatial_footprint,
        resource_lower_limit=resource_lower_limit,
        resource_threshold=resource_threshold,
        run_info_column_headers=run_info_column_headers,
        max_area_to_cap_msrs_km2=max_area_to_cap_msrs_km2,
        land_cover_classes=land_cover_classes,
        default_min_contiguous_area_suitable_for_msr_km2=(
            default_min_contiguous_area_suitable_for_msr_km2
        ),
        elevation_threshold=elevation_threshold,
        hours_in_year=hours_in_year,
        days_in_year=days_in_year,
        wind_turbine_capacity=wind_turbine_capacity,
        wind_rotor_diameter=wind_rotor_diameter,
        wind_spacing_downwind_rotor_diameters=wind_spacing_downwind_rotor_diameters,
        wind_spacing_crosswind_rotor_diameters=wind_spacing_crosswind_rotor_diameters,
    )

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


def compute_categorical_raster_distribution(
    msr_geometry,
    categorical_raster_path,
) -> dict[int, float]:
    """Return percent coverage by categorical raster class for one MSR."""

    try:
        with rasterio.open(categorical_raster_path) as src:
            out_image, _ = mask(
                src,
                [msr_geometry.__geo_interface__],
                crop=True,
                all_touched=False,
                filled=False,
            )
            data = out_image[0].compressed()
            nodata = src.nodata
    except ValueError:
        return {}

    if nodata is not None:
        data = data[data != nodata]
    if np.issubdtype(data.dtype, np.floating):
        data = data[~np.isnan(data)]

    if data.size == 0:
        return {}

    unique, counts = np.unique(data, return_counts=True)
    total = counts.sum()

    return {
        int(value): (count / total) * 100
        for value, count in zip(unique, counts)
    }


def compute_elevation_attributes(msr_geometry, elevation_raster_path):
    """Return mean, minimum, and maximum elevation for one MSR."""

    try:
        with rasterio.open(elevation_raster_path) as src:
            out_image, _ = mask(
                src,
                [msr_geometry.__geo_interface__],
                crop=True,
                all_touched=True,
                filled=False,
            )
            data = out_image[0].compressed()
            nodata = src.nodata
    except ValueError:
        return np.nan, np.nan, np.nan

    if nodata is not None:
        data = data[data != nodata]
    if np.issubdtype(data.dtype, np.floating):
        data = data[~np.isnan(data)]

    if data.size == 0:
        return np.nan, np.nan, np.nan

    return (
        float(np.mean(data)),
        float(np.min(data)),
        float(np.max(data)),
    )





def get_stages_to_run(control_configurations: pd.DataFrame) -> list[int]:
    """Return stage numbers enabled in the control workbook.

    Returns:
        list[int]: Enabled workflow stage numbers.
    """

    run_status_of_process_scripts = [
        control_configurations.loc[
            "perform_stage_1_prepare_input_datasets"
        ][0],
        control_configurations.loc[
            "perform_stage_2_score_input_datasets"
        ][0],
        control_configurations.loc[
            "perform_stage_3_get_resource_potential"
        ][0],
        control_configurations.loc[
            "perform_stage_4_resource_sufficiency_check_and_relaxation"
        ][0],
        control_configurations.loc[
            "perform_stage_5_polygonization"
        ][0],
        control_configurations.loc[
            "perform_stage_6_attribution"
        ][0],
    ]
    return [
        stage_number
        for stage_number in range(1, 7)
        if run_status_of_process_scripts[stage_number - 1] != 0
    ]




def process_all_regions(config: MsrCreatorConfig) -> None:
    """Prepare shared region-boundary data and process configured regions.

    """

    LOGGER.info(
        f"Loading region boundaries | "
        f"path={config.input_folder_datasets / f'{config.file_name_region_boundaries}.shp'}"
    )
    _disable_shapely_array_interface()
    region_boundaries = gpd.read_file(
        config.input_folder_datasets
        / f"{config.file_name_region_boundaries}.shp"
    )
    LOGGER.info(
        f"Processing regions | count={len(config.regions)} "
        f"| stages={config.stages_to_run} | technology={config.re_technology}"
    )

    for region_counter in range(0, len(config.regions)):
        region_name = config.regions.region[region_counter]
        context = prepare_region_context(region_name, config, region_boundaries)
        process_region(context, config)


def prepare_region_context(
    region_name: str,
    config: MsrCreatorConfig,
    region_boundaries: gpd.GeoDataFrame,
) -> RegionContext:
    """Prepare region-specific boundaries, output folders, and buffers.

    Region boundaries are written to the clipping folder in EPSG:4326 because
    many vector/raster clipping operations expect geographic coordinates. Area
    is calculated in ESRI:54009 so km2 values use a projected equal-area basis.

    Returns:
        RegionContext: Region-specific inputs, output paths, and optional
        road/grid buffer distances in metres.
    """

    region_name_without_spaces = region_name.replace(" ", "")

    road_buffer_distance_m = None
    if config.roads_buffered_search:

        buffer_distance = pd.read_csv(
            Path(Path(str(config.control_paths.loc["input_folder_datasets"][0]))) /
            str(config.control_datasets.loc["buffer_distance"][0]),
            names=["region", "grid_buffer_distance", "road_buffer_distance", "comments"],
            sep=";"
        )
        road_buffer_distance_m = int(
            buffer_distance.loc[buffer_distance.region == region_name, "road_buffer_distance"].iloc[0]
            * 1000
        )

    grid_buffer_distance_m = None
    if config.grid_buffered_search:

        buffer_distance = pd.read_csv(
            Path(Path(str(config.control_paths.loc["input_folder_datasets"][0]))) /
            str(config.control_datasets.loc["buffer_distance"][0]),
            names=["region", "grid_buffer_distance", "road_buffer_distance", "comments"],
            sep=";"
        )
        grid_buffer_distance_m = int(
            buffer_distance.loc[buffer_distance.region == region_name, "grid_buffer_distance"].iloc[0]
            * 1000
        )

    output_folder_msr_creator = Path(
        Path(str(config.output_folder))
        / "1_msr_creator"
        / region_name_without_spaces
    )

    if config.re_technology == "solarpv":
        output_path = (
            output_folder_msr_creator / "stage6_attribution" / f"{config.re_technology}_final_msrs.shp"
        )
    elif config.re_technology == "wind":
        output_path = (
            output_folder_msr_creator / "stage6_attribution" / f"{config.re_technology}_{config.elevation_threshold}_final_msrs.shp"
        )

    paths = RegionPaths(
        output_folder_msr_creator=output_folder_msr_creator,
        region_maps_for_clipping_folder=output_folder_msr_creator / "region_boundary_maps",
        stage_1_clipping_folder=output_folder_msr_creator / "stage1_input_datasets",
        stage_2_scoring_folder=output_folder_msr_creator / "stage2_scored_datasets",
        stage_3_competitive_resource_folder=(
            output_folder_msr_creator / "stage3_competitive_resource_area"
        ),
        stage_5_polygonization_folder=output_folder_msr_creator / "stage5_polygonization",
        stage_6_attribution_folder=output_folder_msr_creator / "stage6_attribution",
        output_path=output_path,
    )
    paths.stage_1_clipping_folder.mkdir(parents=True, exist_ok=True)
    paths.stage_2_scoring_folder.mkdir(parents=True, exist_ok=True)
    paths.stage_3_competitive_resource_folder.mkdir(parents=True, exist_ok=True)
    paths.stage_5_polygonization_folder.mkdir(parents=True, exist_ok=True)
    paths.stage_6_attribution_folder.mkdir(parents=True, exist_ok=True)

    single_region_boundary = region_boundaries[
        region_boundaries.name == region_name
    ]
    paths.region_maps_for_clipping_folder.mkdir(parents=True, exist_ok=True)
    single_region_boundary.to_crs('EPSG:4326').to_file(
        paths.region_maps_for_clipping_folder / f"{region_name_without_spaces}.shp"
    )
    region_area_km2 = (
        single_region_boundary.to_crs("ESRI:54009").area.iloc[0] / 1000000
    )
    LOGGER.info(
        f"Prepared region context | region={region_name_without_spaces} "
        f"| output={output_folder_msr_creator}"
    )

    return RegionContext(
        region_name_with_spaces=region_name,
        region_name_without_spaces=region_name_without_spaces,
        single_region_boundary=single_region_boundary,
        region_area_km2=region_area_km2,
        paths=paths,
        road_buffer_distance_m=road_buffer_distance_m,
        grid_buffer_distance_m=grid_buffer_distance_m,
    )
  
def process_region(context: RegionContext, config: MsrCreatorConfig) -> None:
    """Run enabled workflow stages for a single region."""

    LOGGER.info(
        f"Starting region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    for stage in config.stages_to_run:
        if context.stop_processing:
            break
        stage_name = {
            1: "Stage 1 prepare input datasets",
            2: "Stage 2 score input datasets",
            3: "Stage 3 competitive resource",
            4: "Stage 4 resource sufficiency check and relaxation",
            5: "Stage 5 polygonization",
            6: "Stage 6 attribution",
        }.get(stage, f"Unknown stage {stage}")

        LOGGER.info(
            f"Starting {stage_name} | region={context.region_name_without_spaces} "
            f"| technology={config.re_technology}"
        )
        try:
            if stage == 1:
                run_stage_1_prepare_input_datasets(context, config)
            elif stage == 2:
                run_stage_2_score_input_datasets(context, config)
            elif stage == 3:
                run_stage_3_competitive_resource(context, config)
            elif stage == 4:
                run_stage_4_resource_sufficiency_check_and_relaxation(context, config)
            elif stage == 5:
                run_stage_5_polygonization(context, config)
            elif stage == 6:
                run_stage_6_attribution(context, config)
            else:
                LOGGER.warning(
                    f"Skipping unknown stage {stage} | "
                    f"region={context.region_name_without_spaces}"
                )
                continue
        except Exception:
            LOGGER.exception(
                f"{stage_name} failed | region={context.region_name_without_spaces} "
                f"| technology={config.re_technology}"
            )
            raise
        LOGGER.info(
            f"Finished {stage_name} | region={context.region_name_without_spaces}"
        )

    LOGGER.info(
        f"Finished region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )


def run_stage_1_prepare_input_datasets(
    context: RegionContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 1: clip inputs and build distance surfaces.

    Raster and vector inputs are first clipped to the active region. Raster
    outputs are reprojected to ESRI:54009 so distance and area-based operations
    use metre units.
    """

    paths = context.paths
    single_region = context.single_region_boundary
    upper_left_x, lower_right_y, lower_right_x, upper_left_y = (
        single_region.total_bounds
    )
    min_x, min_y, max_x, max_y = single_region.total_bounds
    clip_geometry = json.dumps(
        mapping(box(upper_left_x, upper_left_y, lower_right_x, lower_right_y))
    )

    LOGGER.info(
        f"Stage 1 clipping and distance surfaces started | "
        f"region={context.region_name_without_spaces}"
    )

    raster_names = [
        config.file_name_population_density,
        config.file_name_land_cover,
        config.file_name_elevation,
        config.resource_raster_name,
        config.file_name_climate_classes
    ]
    for raster_name in raster_names:
        input_raster_dataset = xarray.open_dataarray(
            config.input_folder_datasets / f"{raster_name}.tif"
        )
        clipped_raster = input_raster_dataset.rio.clip_box(
            min_x, min_y, max_x, max_y
        )
        del input_raster_dataset
        clipped_raster = clipped_raster.rio.clip(single_region.geometry)
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
            f"region={context.region_name_without_spaces} | layer={raster_name}"
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
        # Read with bbox first to reduce IO before exact region clipping.
        clipped_vector = gpd.read_file(
            config.input_folder_datasets / f"{vector_name}.shp",
            bbox=tuple(single_region.total_bounds),
        )

        if not clipped_vector.empty and vector_name == config.file_name_roads:
            clipped_vector = clipped_vector[
                clipped_vector.GP_RTP <= config.road_type
            ]

        if not clipped_vector.empty:
            clipped_vector = gpd.clip(clipped_vector, single_region.envelope)
            clipped_vector["raster_value"] = 1
        else:
            LOGGER.warning(
                f"Vector layer has no features in region extent; using fallback "
                f"mask | region={context.region_name_without_spaces} "
                f"| layer={vector_name}"
            )
            # Empty layers are represented explicitly so rasterization produces
            # a complete mask. Exclusion layers use 0 to preserve exclusion
            # semantics when the source feature is absent.
            clipped_vector = gpd.GeoDataFrame(
                {'geometry': single_region.envelope},
                geometry='geometry',
            )
            clipped_vector["raster_value"] = 1
            if vector_name in [
                config.file_name_protected_areas,
                config.file_name_water_bodies,
            ]:
                clipped_vector["raster_value"] = 0

        clipped_vector = gpd.clip(clipped_vector, single_region.geometry)
        if clipped_vector.empty:
            LOGGER.warning(
                f"Vector layer is empty after region clipping; using fallback "
                f"mask | region={context.region_name_without_spaces} "
                f"| layer={vector_name}"
            )
            clipped_vector = gpd.GeoDataFrame(
                {'geometry': single_region.geometry},
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
            f"Vector clipped | region={context.region_name_without_spaces} "
            f"| layer={vector_name}"
        )
        rasterized_clipped_vector = make_geocube(
            clipped_vector,
            measurements=["raster_value"],
            resolution=(0.0025, -0.0025),
            geom=clip_geometry,
        ).fillna(0)
        rasterized_clipped_vector = rasterized_clipped_vector.rio.clip(
            single_region.geometry)
        rasterized_clipped_vector.rio.reproject(
            "ESRI:54009").raster_value.rio.to_raster(
                paths.stage_1_clipping_folder / (
                    f"{config.re_technology}_{vector_name}_rasterized_projected.tif"
                )
            )
        LOGGER.info(
            f"Vector rasterized and projected to ESRI:54009 | "
            f"region={context.region_name_without_spaces} | layer={vector_name}"
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
        f"Slope raster created | region={context.region_name_without_spaces} "
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
        # geographic CRS to match region-boundary geometry handling.
        distance_surface = distance_surface.rio.reproject("EPSG:4326")
        distance_surface = distance_surface.rio.clip(single_region.envelope)
        distance_surface = distance_surface.rio.clip(single_region.geometry)
        distance_surface = distance_surface.rio.reproject("ESRI:54009")
        distance_surface.rio.to_raster(
            paths.stage_1_clipping_folder / (
                f"{config.re_technology}_distance_surface_{dataset_name}.tif"
            )
        )
        LOGGER.info(
            f"Distance surface created | region={context.region_name_with_spaces} "
            f"| layer={dataset_name}"
        )

    LOGGER.info(
        f"Stage 1 clipping and distance surfaces finished | "
        f"region={context.region_name_with_spaces}"
    )

def run_stage_2_score_input_datasets(
    context: RegionContext,
    config: MsrCreatorConfig,
) -> None:
    """Scored layers use the existing convention where values
    encode unsuitable, partially suitable, or suitable pixels for later
    multiplication in Stage 2."""

    LOGGER.info(
        f"Stage 2 scoring started | "
        f"region={context.region_name_without_spaces}"
    )

    paths = context.paths

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
                    ~(layer_to_score <= context.road_buffer_distance_m), 1)
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
                ~(layer_to_score > config.resource_threshold), 1)
            scored_layer = scored_layer.where(
                ~(scored_layer == 0),
                (layer_to_score - config.resource_lower_limit)
                / (config.resource_threshold - config.resource_lower_limit),
            )

        scored_layer.rio.to_raster(
            paths.stage_2_scoring_folder / (
                f"{config.re_technology}_{layer_to_score_name}_scored.tif"
            )
        )
        LOGGER.info(
            f"Layer scored | region={context.region_name_without_spaces} "
            f"| layer={layer_to_score_name}"
        )
    LOGGER.info(
        f"Stage 2 scoring finished | "
        f"region={context.region_name_without_spaces}"
    )


def run_stage_3_competitive_resource(
    context: RegionContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 3: identify competitive resource and optional sufficiency output.
    Scored layers are multiplied so exclusion masks remove unsuitable pixels
    while the resource layer preserves relative resource quality. If configured,
    the sufficiency stage relaxes the resource threshold for regions where
    the retained suitable area is below the configured region-area share.
    """

    paths = context.paths
    single_region = context.single_region_boundary
    LOGGER.info(
        f"Stage 3 competitive resource started | "
        f"region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    resource_raster = xarray.open_dataarray(
        paths.stage_1_clipping_folder / (
            f"{config.re_technology}_{config.resource_raster_name}_projected.tif"
        )
    )
    suitable_area_raster_with_no_exclusions = xarray.open_dataarray(
        paths.stage_2_scoring_folder / (
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
            paths.stage_2_scoring_folder / (
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
        single_region.geometry
    )
    suitable_area_resource_raster = suitable_area_resource_raster.rio.reproject(
        "ESRI:54009"
    )
    suitable_area_resource_raster.rio.to_raster(
        paths.stage_3_competitive_resource_folder / (
            f"{config.re_technology}_suitable_resource.tif"
        )
    )
    LOGGER.info(
        f"Suitable resource raster written | "
        f"region={context.region_name_without_spaces} | "
        f"technology={config.re_technology} | "
        f"path={paths.stage_3_competitive_resource_folder / f'{config.re_technology}_suitable_resource.tif'}"
    )

    competitive_area_resource_raster = (
        competitive_area_resource_raster.rio.reproject("EPSG:4326"))
    competitive_area_resource_raster = competitive_area_resource_raster.rio.clip(
        single_region.geometry)
    competitive_area_resource_raster = (
        competitive_area_resource_raster.rio.reproject("ESRI:54009"))
    competitive_area_resource_raster.rio.to_raster(
        paths.stage_3_competitive_resource_folder / (
            f"{config.re_technology}_competitive_resource.tif"))
    LOGGER.info(
        f"Competitive resource raster written | "
        f"region={context.region_name_without_spaces} | "
        f"technology={config.re_technology} | "
        f"path={paths.stage_3_competitive_resource_folder / f'{config.re_technology}_competitive_resource.tif'}"
    )

    context.final_resource_threshold = config.resource_threshold
    context.indicative_yield_gwh = np.nan
    context.competitive_resource_raster_path = paths.stage_3_competitive_resource_folder / f"{config.re_technology}_competitive_resource.tif"

    pd.DataFrame(
        {
            "resource_threshold": [context.final_resource_threshold],
            "indicative_yield_gwh": [context.indicative_yield_gwh],
            "competitive_resource_raster_path": [context.competitive_resource_raster_path],
        }
    ).to_csv(
        paths.stage_3_competitive_resource_folder / (
            f"{config.re_technology}_log_resource_identification_polygonization.csv"
        ),
        index=False,
        sep=";",
    )

    LOGGER.info(
        f"Stage 3 competitive resource finished | "
        f"region={context.region_name_without_spaces} | "
        f"technology={config.re_technology} | "
        f"resource threshold={config.resource_threshold:.3f}"
        )

def run_stage_4_resource_sufficiency_check_and_relaxation(
    context: RegionContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 4: optional resource sufficiency check and threshold relaxation.
    
    Relax the resource threshold until enough suitable area is retained.

    This optional check is intended for resource-lagging regions. It lowers
    the resource threshold by ``resource_relaxation_step`` until retained
    contiguous suitable area exceeds ``region_area_threshold`` of region
    area or reaches ``resource_lower_limit``.

    """

    paths = context.paths
    single_region = context.single_region_boundary
    LOGGER.info(
        f"Stage 3 resource sufficiency check started | "
        f"region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    
    resource_relaxation_step = float(config.control_parameters.loc["resource_relaxation_step"][0])
    region_area_threshold = float(float(config.control_parameters.loc["region_area_threshold"][0]) / 100)

    suitable_area_resource_raster_path = (
        paths.stage_3_competitive_resource_folder / (
            f"{config.re_technology}_suitable_resource.tif")
    )

    suitable_area_resource_raster = xarray.open_dataarray(
        suitable_area_resource_raster_path)
    suitable_area_resource_raster = suitable_area_resource_raster.squeeze("band")

    raster_pixel_size_m = abs(suitable_area_resource_raster.affine[0])
    min_contiguous_pixels_to_retain = ceil(
        config.default_min_contiguous_area_suitable_for_msr_km2
        * 1000000
        / (raster_pixel_size_m * raster_pixel_size_m)
    )

    suitable_area_resource_values = suitable_area_resource_raster.to_numpy()

    resource_threshold = config.resource_threshold
    indicative_yield_gwh = np.nan
    cutoff_normalized = 1

    break_while_loop = 0

    while (
        break_while_loop == 0 
        and resource_threshold >= config.resource_lower_limit
    ):
        suitable_area_resource_values_filtered = np.where(
            suitable_area_resource_values < resource_threshold, 
            0,
            suitable_area_resource_values
        )

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

        if config.re_technology == 'solarpv':
            # PV yield uses GHI, pixel area, days/year, efficiency, spacing,
            # and land-discount assumptions.

            pv_conversion_efficiency = float(config.control_parameters.loc["pv_conversion_efficiency"][0]) / 100
            pv_spacing_factor = float(config.control_parameters.loc["pv_spacing_factor"][0]) / 100
        
            indicative_yield_gwh = (
                suitable_area_resource_without_small_contiguous_regions.sum()
                * raster_pixel_size_m
                * raster_pixel_size_m
                * (config.days_in_year / 1000000)
                * pv_conversion_efficiency
                * pv_spacing_factor
                * config.land_discount
            )

        if config.re_technology == 'solarcsp':
            # CSP yield uses configurable resource-bin production percentages.

            csp_land_classes = np.array(
                [
                    float(value.strip())
                    for value in str(
                        config.control_parameters.loc["csp_land_classes"][0]).split(",")
                ],
                dtype=float,
            )
            csp_production_percentage_per_land_class = np.array(
                [
                    float(value.strip())
                    for value in str(
                        config.control_parameters.loc["csp_production_percentage_per_land_class"][0]).split(",")
                ],
                dtype=float,
            )
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
                config.re_spatial_footprint
                * area_per_spatial_cluster
                * config.land_discount
            )
            indicative_yield_gwh = (
                csp_max_capacity_per_spatial_cluster
                * config.hours_in_year
                * csp_production_percentage_per_land_class
                / 100
            ).sum() / 1000

        if config.re_technology == 'wind':
            # Wind yield uses configurable resource-bin production percentages.

            wind_land_classes = np.array(
                [
                    float(value.strip())
                    for value in str(
                        config.control_parameters.loc["wind_land_classes"][0]).split(",")
                ],
                dtype=float,
            )
            wind_production_percentage_per_land_class = np.array(
                [
                    float(value.strip())
                    for value in str(
                        config.control_parameters.loc["wind_production_percentage_per_land_class"][0]).split(",")
                ],
                dtype=float,
            )

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
                        config.wind_spacing_downwind_rotor_diameters
                        * config.wind_rotor_diameter
                        * config.wind_spacing_crosswind_rotor_diameters
                        * config.wind_rotor_diameter
                        / 1000000
                    ),
                    0,
                )
                * (config.wind_turbine_capacity)
                * config.land_discount
            )
            indicative_yield_gwh = (
                wind_max_capacity_per_spatial_cluster
                * config.hours_in_year
                * wind_production_percentage_per_land_class
                / 100
            ).sum() / 1000
        LOGGER.debug(
            f"Indicative {config.re_technology} yield: {indicative_yield_gwh:.3f} GWh "
            f"at threshold {resource_threshold:.3f} "
            f"and normalized cutoff {cutoff_normalized:.3f}"
        )

        sufficiency_parameter = (
            np.count_nonzero(suitable_area_resource_without_small_contiguous_regions)
            * raster_pixel_size_m
            * raster_pixel_size_m
            / 1000000
        )
        sufficiency_condition = context.region_area_km2 * region_area_threshold

        LOGGER.info(
            f"Resource sufficiency iteration | "
            f"region={context.region_name_without_spaces} "
            f"| technology={config.re_technology} "
            f"| threshold={resource_threshold:.3f} "
            f"| retained_area={sufficiency_parameter:.2f} km2 "
            f"| required_area={sufficiency_condition:.2f} km2 "
            f"| indicative_yield={indicative_yield_gwh:.3f} GWh"
        )

        if sufficiency_parameter > sufficiency_condition:
            break_while_loop = 1
        
        else:
            resource_threshold = resource_threshold - resource_relaxation_step
            
            if resource_threshold >= config.resource_lower_limit:
                
                cutoff_normalized = (
                    (resource_threshold - config.resource_lower_limit)
                    / (config.resource_threshold - config.resource_lower_limit)
                )

    raster_path = paths.stage_3_competitive_resource_folder / (
        f"{config.re_technology}_competitive_resource.tif"
    )
    
    if resource_threshold < config.resource_threshold:

        relaxed_raster_path = paths.stage_3_competitive_resource_folder / (
            f"{config.re_technology}_competitive_resource_relaxed.tif"
        )

        competitive_area_resource_raster_relaxed = suitable_area_resource_raster.where(
            ~(suitable_area_resource_raster < resource_threshold),
            0
        )

        competitive_area_resource_raster_relaxed = (
            competitive_area_resource_raster_relaxed.rio.reproject("EPSG:4326")
        )
        competitive_area_resource_raster_relaxed = (
            competitive_area_resource_raster_relaxed.rio.clip(single_region.geometry)
        )
        competitive_area_resource_raster_relaxed = (
            competitive_area_resource_raster_relaxed.rio.reproject("ESRI:54009")
        )
        competitive_area_resource_raster_relaxed.rio.to_raster(
            relaxed_raster_path
        )

        raster_path = relaxed_raster_path
        
        LOGGER.warning(
            f"Relaxed competitive resource raster written | "
            f"region={context.region_name_without_spaces} | "
            f"| original={config.resource_threshold:.3f} "
            f"| relaxed={resource_threshold:.3f} "
            f"| path={relaxed_raster_path}"
        )
    
    else:
        LOGGER.info(
            f"Resource threshold did not require relaxation | "
            f"region={context.region_name_without_spaces} "
            f"| threshold={config.resource_threshold:.3f}"
        )

    context.final_resource_threshold = resource_threshold
    context.indicative_yield_gwh = indicative_yield_gwh
    context.competitive_resource_raster_path = raster_path
    pd.DataFrame(
        {
            "resource_threshold": context.final_resource_threshold,
            "indicative_yield_gwh": context.indicative_yield_gwh,
            "competitive_resource_raster_path": context.competitive_resource_raster_path,
        }
    ).to_csv(
        paths.stage_3_competitive_resource_folder / (
            f"{config.re_technology}_log_resource_identification_polygonization.csv"
        ),
        index=False,
        sep=";",
    )

    LOGGER.info(
        f"Stage 3 resource sufficiency check finished | "
        f"region={context.region_name_without_spaces} "
        f"| threshold={resource_threshold:.3f} "
        f"| indicative_yield={indicative_yield_gwh:.3f} GWh"
    )


def run_stage_5_polygonization(
    context: RegionContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 5: polygonize competitive resource rasters into MSRs.

    Resource potential is split into quality bands before polygonization so
    that contiguous areas with similar resource quality are resolved separately.
    Very small polygons are removed and very large polygons are split to respect
    the configured minimum and maximum MSR sizes.

    Args:
        resource_potential_raster_path: Competitive resource raster path. The
            raster is expected in ESRI:54009 for metre-based area operations.
        stage_5_polygonization_folder: Folder for intermediate band rasters and
            shapefiles.
        re_technology: Technology identifier used in output names.
        resource_threshold: Minimum resource value retained for MSR creation.
            Units are kWh/m2/day for solar and m/s for wind.
        band_count_for_multi_resolve_algorithm: Number of resource-quality
            bands to polygonize.
        max_area_to_cap_msrs_km2: Maximum MSR area before quadrat splitting.
        default_min_contiguous_area_suitable_for_msr_km2: Minimum contiguous area in
            km2 required to retain a polygon.

    Returns:
        geopandas.GeoDataFrame | int: Final MSR polygons, or ``0`` when no
        qualifying polygons are produced.
    """

    paths = context.paths
    LOGGER.info(
        f"Stage 5 polygonization started | "
        f"region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    if (
    context.final_resource_threshold is not None
    and context.competitive_resource_raster_path is not None
    ):
        resource_threshold = context.final_resource_threshold
        resource_potential_raster_path = (
            context.competitive_resource_raster_path
        )
    else:
        handover = pd.read_csv(
            paths.stage_3_competitive_resource_folder
            / (
                f"{config.re_technology}_"
                "log_resource_identification_polygonization.csv"
            ),
            sep=";",
        )

        resource_threshold = float(
            handover.loc[0, "resource_threshold"]
        )
        resource_potential_raster_path = Path(
            handover.loc[
                0,
                "competitive_resource_raster_path",
            ]
        )

    stage_5_polygonization_folder = Path(paths.stage_5_polygonization_folder)

    resource_potential_raster = xarray.open_dataarray(resource_potential_raster_path)
    resource_potential_raster = resource_potential_raster.squeeze("band")

    resource_potential_values = resource_potential_raster.data * 1
    resource_potential_values[np.isnan(resource_potential_values)] = 0
    max_resource_pixel_value = resource_potential_values.max()

    is_first_msr = 1
    for resource_band in range(1, config.band_count_for_multi_resolve_algorithm + 1):
        resolved_raster_path = (
            stage_5_polygonization_folder
            / f"{config.re_technology}ResourceBand{resource_band}_resolve.tif"
        )
        single_band_initial_msrs_path = (
            stage_5_polygonization_folder
            / f"{config.re_technology}ResourceBand{resource_band}_InitialMSRs.shp"
        )
        single_band_final_msrs_path = (
            stage_5_polygonization_folder
            / f"{config.re_technology}ResourceBand{resource_band}_FinalMSRs.shp"
        )

        resource_band_upper_limit = resource_threshold + resource_band * (
            (max_resource_pixel_value - resource_threshold)
            / config.band_count_for_multi_resolve_algorithm
        )
        resource_band_lower_limit = resource_threshold + (resource_band - 1) * (
            (max_resource_pixel_value - resource_threshold)
            / config.band_count_for_multi_resolve_algorithm
        )

        LOGGER.info(
            f"Polygonizing resource band {resource_band}/"
            f"{config.band_count_for_multi_resolve_algorithm}: "
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
                >= config.default_min_contiguous_area_suitable_for_msr_km2 * 1000000
            ]
            if not initial_polygons_above_min_area_threshold.empty:

                single_band_final = initial_polygons_above_min_area_threshold[
                    initial_polygons_above_min_area_threshold.area
                    <= config.max_area_to_cap_msrs_km2 * 1000000
                ].reset_index(drop=True)
                single_band_to_be_capped = initial_polygons_above_min_area_threshold[
                    initial_polygons_above_min_area_threshold.area
                    > config.max_area_to_cap_msrs_km2 * 1000000
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
                                    np.sqrt(config.max_area_to_cap_msrs_km2) * 1000,
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
                                >= config.default_min_contiguous_area_suitable_for_msr_km2
                                * 1000000
                            ]
                single_band_final[
                    single_band_final.area
                    >= config.default_min_contiguous_area_suitable_for_msr_km2 * 1000000
                ].to_file(single_band_final_msrs_path)

                single_band = single_band_final[
                    single_band_final.area
                    >= config.default_min_contiguous_area_suitable_for_msr_km2 * 1000000
                ].reset_index(
                    drop=True)
                if is_first_msr == 1:
                    multi_band = single_band
                    is_first_msr = 0
                else:
                    multi_band = gpd.overlay(single_band, multi_band, how='union')
                    multi_band = multi_band[
                        multi_band.area
                        >= config.default_min_contiguous_area_suitable_for_msr_km2 * 1000000
                    ]
    try:
        multi_band['FID'] = multi_band.index
        msrs = multi_band
    except Exception as exc:
        LOGGER.warning(f"MSRs were not developed during polygonization | reason={exc}")
        msrs = 0

    if type(msrs) == int:
        if paths.output_path.is_file():
            for suffix in [".shp", ".shx", ".prj", ".cpg", ".dbf"]:
                (paths.stage_6_attribution_folder / f"{config.re_technology}_{config.elevation_threshold}_final_msrs{suffix}").unlink()
        LOGGER.warning(
            f"No MSRs created because sufficient resource was not found | "
            f"region={context.region_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

        context.stop_processing = True
    else:
        msrs.to_file(paths.output_path)
        LOGGER.info(
            f"MSR polygons written | region={context.region_name_without_spaces} "
            f"| path={paths.output_path}"
        )


def run_stage_6_attribution(
    context: RegionContext,
    config: MsrCreatorConfig,
) -> None:
    """Stage 6: attribute final MSRs with capacity and proximity data.
    Capacity is estimated from MSR area, ``land_discount``, and
    ``re_spatial_footprint``. Road, grid, substation, and load-centre
    distances are reported in kilometres after metre-based projected CRS
    distance calculations.
    """

    paths = context.paths
    single_region = context.single_region_boundary
    LOGGER.info(
        f"Stage 6 attribution started | "
        f"region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

    msrs = gpd.read_file(paths.output_path)
    msrs['AreakM2'] = msrs.geometry.area / 1000000
    msrs['CapacityMW'] = (
        msrs['AreakM2'] * config.land_discount * config.re_spatial_footprint)

    distance_to_roads_stats_per_msr = zonal_stats(
        str(paths.output_path),
        str(paths.stage_1_clipping_folder / (
            f"{config.re_technology}_distance_surface_{config.file_name_roads}.tif")),
        stats="count min mean max median sum",
    )
    msrs['RoadDist'] = (
        pd.DataFrame(distance_to_roads_stats_per_msr)['mean'] / 1000)
    LOGGER.info(
        f"Road distances attributed | region={context.region_name_without_spaces}"
    )

    clipped_vector = gpd.read_file(
        config.input_folder_datasets / f"{config.file_name_transmission_grid}.shp",
        bbox=tuple(single_region.total_bounds),
    )
    if not clipped_vector.empty:
        clipped_vector = gpd.clip(clipped_vector, single_region.geometry)
    if clipped_vector.empty:
        distance_to_tgrid_stats_per_msr = zonal_stats(
            str(paths.output_path),
            str(config.input_folder_datasets / (
                f"{config.file_name_continent_distance_surface_tgrid}.tif")),
            stats="count min mean max median sum",
        )
        msrs['T_Dist_gf'] = (
            pd.DataFrame(distance_to_tgrid_stats_per_msr)['mean'] / 1000)
        LOGGER.warning(
            f"Transmission grid absent in region; using continent distance "
            f"surface fallback | region={context.region_name_without_spaces}"
        )
    else:
        distance_to_tgrid_stats_per_msr = zonal_stats(
            str(paths.output_path),
            str(paths.stage_1_clipping_folder / (
                f"{config.re_technology}_distance_surface_{config.file_name_transmission_grid}.tif")),
            stats="count min mean max median sum",
        )

        msrs['T_Dist_gf'] = (
            pd.DataFrame(distance_to_tgrid_stats_per_msr)['mean'] / 1000)
        LOGGER.info(
            f"Transmission grid distances attributed | "
            f"region={context.region_name_without_spaces}"
        )

    distance_to_dgrid_stats_per_msr = zonal_stats(
        str(paths.output_path),
        str(paths.stage_1_clipping_folder / (
            f"{config.re_technology}_distance_surface_{config.file_name_distribution_grid}.tif")),
        stats="count min mean max median sum",
    )
    msrs['D_Dist_gf'] = (
        pd.DataFrame(distance_to_dgrid_stats_per_msr)['mean'] / 1000)
    LOGGER.info(
        f"Distribution grid distances attributed | "
        f"region={context.region_name_without_spaces}"
    )

    msrs['TD_Dist_gf'] = msrs[['T_Dist_gf', 'D_Dist_gf']].min(axis=1)
    LOGGER.info(
        f"Closest grid distances attributed | "
        f"region={context.region_name_without_spaces}"
    )

    substations = gpd.read_file(
        config.input_folder_datasets / f"{config.file_name_substations}.shp",
        bbox=tuple(single_region.total_bounds),
    ).to_crs("ESRI:54009")
    msrs['SubstnDist'] = (
        msrs.centroid.apply(
            minimum_distance_of_msr_centroid_from_geometry_set,
            geometry_set=substations.centroid,
        )
        / 1000
    )
    LOGGER.info(
        f"Substation distances attributed | region={context.region_name_without_spaces}"
    )

    load_centers = gpd.read_file(
        config.input_folder_datasets / (
            f"{config.file_name_urban_area_load_centers}.shp"),
        bbox=tuple(single_region.total_bounds),
    ).to_crs("ESRI:54009")
    msrs['Load_dst'] = (
        msrs.centroid.apply(
            minimum_distance_of_msr_centroid_from_geometry_set,
            geometry_set=load_centers.centroid,
        )
        / 1000
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
        f"Load-centre distances and variables attributed | "
        f"region={context.region_name_without_spaces}"
    )

    land_cover_raster_path = (
        paths.stage_1_clipping_folder / (
            f"{config.re_technology}_{config.file_name_land_cover}_projected.tif"))
    land_distributions = msrs.geometry.apply(
        lambda geometry: compute_categorical_raster_distribution(
            geometry,
            land_cover_raster_path
        )
    )
    land_cover = land_distributions.apply(pd.Series).fillna(0)
    if land_cover.empty:
        msrs['LUDomCl'] = np.nan
        msrs['LUDomSh'] = 0.0
    else:
        land_cover = land_cover.rename(columns=lambda value: f"LU_{int(value)}")

        unexpected_land_columns = [
            column
            for column in land_cover.columns
            if int(column.replace("LU_", ""))
            not in config.land_cover_classes
        ]
        if unexpected_land_columns:
            total_msr_area = msrs['AreakM2'].sum()
            unexpected_area_shares = {
                column: round((
                    (msrs["AreakM2"] * land_cover[column].fillna(0) / 100)
                    .sum()
                    / total_msr_area
                    * 100
                ), 3)
                for column in unexpected_land_columns
            }
            unexpected_area_shares = dict(
                sorted(
                    unexpected_area_shares.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            )
            LOGGER.warning(
                f"Land-cover classes outside the suitable-class list occur "
                f"share of total MSR area (%)={unexpected_area_shares} | "
                f"region={context.region_name_with_spaces}"
            )

        for col in land_cover.columns:
            msrs[col] = land_cover[col]
        valid_land = land_cover.sum(axis=1) > 0
        msrs['LUDomCl'] = np.nan
        msrs.loc[valid_land, 'LUDomCl'] = (
            land_cover.loc[valid_land]
            .idxmax(axis=1)
            .str.replace('LU_', '', regex=False)
            .astype(float)
        )
        msrs['LUDomSh'] = land_cover.max(axis=1)
    LOGGER.info(
        f"Land-cover attributed | region={context.region_name_without_spaces}"
    )

    climate_class_raster_path = (
        paths.stage_1_clipping_folder / (
            f"{config.re_technology}_{config.file_name_climate_classes}_projected.tif"))
    climate_distributions = msrs.geometry.apply(
        lambda geometry: compute_categorical_raster_distribution(
            geometry,
            climate_class_raster_path
        )
    )
    kc = climate_distributions.apply(pd.Series).fillna(0)
    if kc.empty:
        msrs['KCDomCl'] = np.nan
        msrs['KCDomSh'] = 0.0
    else:
        kc = kc.rename(columns=lambda value: f"KC_{int(value)}")
        for col in kc.columns:
            msrs[col] = kc[col]
        valid_kc = kc.sum(axis=1) > 0
        msrs['KCDomCl'] = np.nan
        msrs.loc[valid_kc, 'KCDomCl'] = (
            kc.loc[valid_kc]
            .idxmax(axis=1)
            .str.replace('KC_', '', regex=False)
            .astype(float)
        )
        msrs['KCDomSh'] = kc.max(axis=1)
    LOGGER.info(
        f"Climate class attributed | region={context.region_name_without_spaces}"
    )

    elevation_raster_path = (
        paths.stage_1_clipping_folder / (
            f"{config.re_technology}_{config.file_name_elevation}_projected.tif"))
    msrs[['ElMean', "ElMin", "ElMax"]] = msrs.geometry.apply(
        lambda geometry: compute_elevation_attributes(
            geometry,
            elevation_raster_path
        )
    ).apply(pd.Series)
    LOGGER.info(
        f"Elevation attributed | region={context.region_name_without_spaces}"
    )

    msrs.to_file(paths.output_path)

    LOGGER.info(
        f"Attribution complete | region={context.region_name_without_spaces} "
        f"| path={paths.output_path}"
    )

    plot_land_cover_composition(msrs, config, context)
    plot_climate_class_composition(msrs, config, context)
    plot_elevation_composition(msrs, config, context)

def plot_land_cover_composition(
    msrs: pd.DataFrame,
    config: MsrCreatorConfig,
    context: RegionContext,
) -> None:
    """Plot country-level land-cover composition of MSR area.

    The LU_<class> columns contain the percentage of each land-cover class
    within an MSR. 

    """

    legend = pd.read_csv(
        Path(
            Path(str(config.control_paths.loc["input_folder_datasets"][0])) /
            f"{str(config.control_datasets.loc['file_name_land_cover_legend'][0])}.csv"
        ),
        sep=";",
    )

    if config.re_technology == "wind":
        re_name = "Wind"
    elif config.re_technology == "solarpv":
        re_name = "Solar PV"
    elif config.re_technology == "solarcsp":
        re_name = "Solar CSP"

    output_path = (
        context.paths.output_folder_msr_creator / 
        f"{config.re_technology}_land_cover_composition.png"
    )

    color_map = dict(
        zip(
            legend["value"].astype(int),
            legend["color"],
        )
    )
    label_map = dict(
        zip(
            legend["value"].astype(int),
            legend["label"],
        )
    )

    land_cover_columns = [
        column
        for column in msrs.columns
        if column.startswith("LU_")
    ]

    if not land_cover_columns:
        LOGGER.warning(
            f"No land-cover composition columns found | "
            f"region={context.region_name_with_spaces}"
            f"technology={config.re_technology} | "
        )
        return

    land_cover_area = msrs[
        ["AreakM2"] + land_cover_columns
    ].copy()

    for column in land_cover_columns:
        land_cover_area[column] = (
            land_cover_area["AreakM2"]
            * land_cover_area[column].fillna(0)
            / 100
        )

    region_area = land_cover_area[land_cover_columns].sum()

    region_percentage = (
        region_area / region_area.sum() * 100
    )

    region_percentage = region_percentage[
        region_percentage > 0
    ]

    region_percentage = pd.DataFrame(
        [region_percentage],
        index=[context.region_name_with_spaces],
    )

    class_values = [
        int(column.replace("LU_", ""))
        for column in region_percentage.columns
    ]

    colors = [
        color_map[value]
        for value in class_values
    ]

    fig, ax = plt.subplots(
        figsize=(6, 3)
    )

    region_percentage.plot(
        kind="barh",
        stacked=True,
        ax=ax,
        color=colors,
        width=0.6,
        legend=False,
    )

    ax.set_xlim(0, 100)
    ax.set_xlabel(
        f"Share of {re_name} MSR area (%)"
    )
    ax.set_ylabel("")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    handles = [
        Patch(
            facecolor=color_map[value],
            label=label_map[value],
        )
        for value in class_values
    ]

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),
        ncol=2,
        frameon=False,
        fontsize=6,
        title="Land-cover classes",
    )


    fig.suptitle(
        f"{context.region_name_with_spaces} composition of "
        f"{re_name} MSRs by land-cover class"
    )

    plt.tight_layout()
    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

    LOGGER.info(
        f"Land-cover composition plot written | "
        f"region={context.region_name_with_spaces} "
        f"technology={config.re_technology} | path={output_path}"
    )

def plot_climate_class_composition(
    msrs: pd.DataFrame,
    config: MsrCreatorConfig,
    context: RegionContext,
) -> None:
    """Plot region-level Köppen-Geiger climate class composition of MSR area.

    The KC_<class> columns contain the percentage of each climate class class
    within an MSR.
    """

    legend = pd.read_csv(
        Path(
            Path(str(config.control_paths.loc["input_folder_datasets"][0])) /
            f"{str(config.control_datasets.loc['file_name_climate_class_legend'][0])}.csv"
        ),
        sep=";",
    )

    if config.re_technology == "wind":
        re_name = "Wind"
    elif config.re_technology == "solarpv":
        re_name = "Solar PV"
    elif config.re_technology == "solarcsp":
        re_name = "Solar CSP"
    else:
        re_name = config.re_technology

    output_path = (
        context.paths.output_folder_msr_creator
        / f"{config.re_technology}_climate_class_composition.png"
    )

    color_map = dict(
        zip(
            legend["value"].astype(int),
            legend["color"],
        )
    )
    label_map = dict(
        zip(
            legend["value"].astype(int),
            legend["label_long"],
        )
    )

    climate_class_columns = [
        column
        for column in msrs.columns
        if column.startswith("KC_")
    ]

    if not climate_class_columns:
        LOGGER.warning(
            f"No climate class composition columns found | "
            f"region={context.region_name_without_spaces} | "
            f"technology={config.re_technology}"
        )
        return

    climate_class_area = msrs[
        ["AreakM2"] + climate_class_columns
    ].copy()

    for column in climate_class_columns:
        climate_class_area[column] = (
            climate_class_area["AreakM2"]
            * climate_class_area[column].fillna(0)
            / 100
        )

    region_area = climate_class_area[climate_class_columns].sum()
    region_percentage = (
        region_area / region_area.sum() * 100
    )

    region_percentage = region_percentage[
        region_percentage > 0
    ]

    region_percentage = pd.DataFrame(
        [region_percentage],
        index=[context.region_name_with_spaces],
    )

    class_values = [
        int(column.replace("KC_", ""))
        for column in region_percentage.columns
    ]

    colors = [
        color_map[value]
        for value in class_values
    ]

    fig, ax = plt.subplots(
        figsize=(6, 3)
    )

    region_percentage.plot(
        kind="barh",
        stacked=True,
        ax=ax,
        color=colors,
        width=0.6,
        legend=False,
    )

    ax.set_xlim(0, 100)
    ax.set_xlabel(
        f"Share of {re_name} MSR area (%)"
    )
    ax.set_ylabel("")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    handles = [
        Patch(
            facecolor=color_map[value],
            label=label_map[value],
        )
        for value in class_values
    ]

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),
        ncol=2,
        frameon=False,
        fontsize=8,
        title="Köppen-Geiger climate classes"
    )

    fig.suptitle(
        f"{context.region_name_with_spaces} composition of "
        f"{re_name} MSRs by climate class"
    )

    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Climate class composition plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )

def plot_elevation_composition(
    msrs: pd.DataFrame,
    config: MsrCreatorConfig,
    context: RegionContext,
) -> None:
    """Plot region-level elevation composition of MSR area.

    """
    if config.re_technology == "wind":
        re_name = "Wind"
        colormap = "Greens"
    elif config.re_technology == "solarpv":
        re_name = "Solar PV"
        colormap = "Oranges"
    elif config.re_technology == "solarcsp":
        re_name = "Solar CSP"
        colormap = "Oranges"
    
    msrs = msrs.copy()
    
    elevation_raster_path = (
        context.paths.stage_1_clipping_folder
        / (f"{config.re_technology}_{config.file_name_elevation}_projected.tif")
    )

    output_path = (
        context.paths.output_folder_msr_creator
        / f"{config.re_technology}_elevation_composition.png"
    )

    elevation_bins = [
        -np.inf, 250, 500, 1000, 1500, 2000, 2500, 3000, np.inf
    ]

    elevation_labels = [
        "< 250",
        "250–500",
        "500–1000",
        "1000–1500",
        "1500–2000",
        "2000–2500",
        "2500–3000",
        "> 3000",
    ]

    with rasterio.open(elevation_raster_path) as src:
        out_image, _ = mask(
            src,
            [geometry.__geo_interface__ for geometry in msrs.geometry],
            crop=True,
            all_touched=True,
            filled=False,
        )
    
        elevation_values = out_image[0].compressed()

        if src.nodata is not None:
            elevation_values = elevation_values[
                elevation_values != src.nodata
            ]

    counts, _ = np.histogram(
        elevation_values,
        bins=elevation_bins,
    )

    elevation_percentage = pd.DataFrame(
        [
            counts / counts.sum() * 100
        ],
        index=[context.region_name_without_spaces],
        columns=elevation_labels,
    )

    elevation_percentage = elevation_percentage.loc[
        :,
        elevation_percentage.sum(axis=0) > 0
    ]

    cmap = plt.get_cmap(colormap)
    elevation_color_map = {
        label: cmap(i / (len(elevation_labels) - 1))
        for i, label in enumerate(elevation_labels)
    }
    colors = [
        elevation_color_map[col]
        for col in elevation_percentage.columns
    ]

    fig, ax = plt.subplots(
        figsize=(6, 3)
    )

    elevation_percentage.plot(
        kind="barh",
        stacked=True,
        ax=ax,
        width=0.6,
        color=colors,
        legend=False
    )


    ax.set_xlim(0, 100)
    ax.set_xlabel(f"Share of {re_name} MSR area (%)")
    ax.set_ylabel("")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    handles = [
        Patch(
            facecolor=elevation_color_map[column],
            label=column
        )
        for column in elevation_percentage.columns
    ]

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),
        ncol=2,
        frameon=False,
        fontsize=8,
        title="Elevation (m.a.s.l.)"
    )
    fig.suptitle(
        f"{context.region_name_with_spaces} elevation composition of "
        f"{config.re_technology} MSRs"
    )

    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Elevation composition plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )


def main() -> None:
    """Load control inputs and run the MSR Creator workflow."""

    configure_logging()
    LOGGER.info("MSR Creator workflow started")
    try:
        control_file = Path(CONTROL_FILE_NAME)
        control = load_control_workbook(control_file)
        LOGGER.info(f"Control workbook loaded | path={control_file}")
        config = build_msr_creator_config(control_file, control)
        LOGGER.info(
            f"Configuration prepared | technologies={config.technologies_to_run} "
            f"| regions={len(config.regions)} | stages={config.stages_to_run}"
        )

        for tech in config.technologies_to_run:
            config.re_technology = tech
            process_all_regions(config)
    except Exception:
        LOGGER.exception("MSR Creator workflow failed")
        raise
    LOGGER.info("MSR Creator workflow completed")


if __name__ == "__main__":
    main()
