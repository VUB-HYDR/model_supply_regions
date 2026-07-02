# BE SURE TO INSTALL THESE LIBRARIES ON THE SERVER / COMPUTER BEFORE ATTEMPTING TO RUN THE CODE

# import relevant libraries

import json
import math
import os
import shutil
import string
import struct
import time
from math import ceil
from pathlib import Path

# del os.environ['PROJ_LIB']
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
from colorama import Fore
from geocube.api.core import make_geocube
from osgeo import osr
from rasterio.features import shapes
from rasterio.warp import reproject, Resampling
from rasterstats import zonal_stats
from scipy.ndimage.measurements import label
from shapely.geometry import box, mapping
from shapely.geometry import LineString, MultiPolygon
from shapely.ops import split


# import warnings
# warnings.filterwarnings("ignore")


def quadrat_cut_geometry(geometry, quadrat_width):
    # Code adopted from OSMNX with alterations:
    # https://osmnx.readthedocs.io/en/stable/index.html

    # create n evenly spaced points between the min and max x and y bounds
    west, south, east, north = geometry.bounds
    x_num = math.floor((east - west) / quadrat_width) + 1
    y_num = math.floor((north - south) / quadrat_width) + 1
    x_points = np.linspace(west, east, num=2 + x_num)
    y_points = np.linspace(south, north, num=2 + y_num)

    # create a quadrat grid of lines at each of the evenly spaced points
    vertical_lines = [
        LineString([(x, y_points[0]), (x, y_points[-1])])
        for x in x_points
    ]
    horizont_lines = [
        LineString([(x_points[0], y), (x_points[-1], y)])
        for y in y_points
    ]
    lines = vertical_lines + horizont_lines

    # recursively split the geometry by each quadrat line
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
    polygonization_folder = Path(polygonization_folder)

    # Open the Competitive resource raster for polygonization
    resource_potential_raster = xarray.open_dataarray(resource_potential_raster_path)
    resource_potential_raster = resource_potential_raster.squeeze("band")

    # Get MaxResourcePixelValue
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

        print(
            f"Creating MSRs for band {resource_band} : "
            f"{resource_band_lower_limit} to {resource_band_upper_limit} "
        )

        subset_resource_potential_raster = resource_potential_raster * 1
        subset_resource_potential_raster = subset_resource_potential_raster.where(
            ~(resource_potential_raster < resource_band_lower_limit), 0)
        subset_resource_potential_raster = subset_resource_potential_raster.where(
            ~(resource_potential_raster > resource_band_upper_limit), 0)
        resolved_raster = subset_resource_potential_raster.where(
            ~(subset_resource_potential_raster > 0), 1)
        resolved_raster.rio.to_raster(resolved_raster_path)

        # Transform is needed so polygonized geometries use the raster pixel size.
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
                        print(
                            f"dividing polygon {i + 1}/"
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
    except:
        print(f"{Fore.RED}******MSRs not developed*******")
        return 0


def minimum_distance_of_msr_centroid_from_geometry_set(
    msr_centroid,
    geometry_set,
):
    # Geometry can be point, line, polygon, or mixed.
    return geometry_set.distance(msr_centroid).min()


def compute_load_center_attributes_for_msr_centroid(
    msr_centroid,
    load_centers,
):
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
    csp_footprint_mw_per_km2,
    wind_rotor_diameter_meters,
    wind_single_turbine_capacity_watts,
    default_min_contiguous_area_suitable_for_msr_km2,
    country_area_km2,
):

    # Reduce cutoff threshold for resource-lagging countries until sufficiency.
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

    wind_land_classes = np.arange(0, 12.5, 0.5)
    wind_production_percentage_per_land_class = np.array(
        [0, 0, 0, 0, 0, 1, 3, 6, 9, 13, 18, 24, 30, 37, 43, 48, 54,
         58, 61, 64, 65, 66, 66, 65],
        dtype=int,
    )

    csp_land_classes = np.array(
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
         17, 18],
        dtype=int,
    )
    csp_production_percentage_per_land_class = np.array(
        [0.00, 7.84, 21.40, 31.01, 38.47, 44.57, 49.72, 54.19, 58.12,
         61.65, 64.83, 67.74, 70.42, 72.90, 75.20, 77.36, 79.39, 81.30],
        dtype=float,
    )

    break_while_loop = 0
    while break_while_loop == 0 and resource_threshold >= resource_lower_limit:
        # subset resource raster as per resource cutofff
        suitable_area_resource_values_filtered = np.where(
            suitable_area_resource_values < resource_threshold, 0,
            suitable_area_resource_values)
        # get same shape array with each pixel value replaced by feature no it belongs. Label by default detects features of cross pattern i.e. diagnoal pixels are ignored
        feat, count = label(suitable_area_resource_values_filtered)
        # count no of pixels included per feature. This array is different dimension
        feature_pixel_count = np.bincount(feat[feat >= 0])
        # exclude features below the minimum area threshold
        desired_features_to_retain = np.where(
            feature_pixel_count > min_contiguous_pixels_to_retain)
        # initialize new array of same shape as the filtered resource raster, fill it with original pixel values feature by feature, considering only the retained features.
        suitable_area_resource_without_small_contiguous_regions = np.zeros_like(
            suitable_area_resource_values_filtered)
        if len(desired_features_to_retain[0]) > 1:
            for f in desired_features_to_retain[0][1:]:
                suitable_area_resource_without_small_contiguous_regions = np.where(
                    feat == f,
                    suitable_area_resource_values_filtered,
                    suitable_area_resource_without_small_contiguous_regions)
        print(f"Retained features in total= {len(desired_features_to_retain[0])}")
        suitable_area_resource_without_small_contiguous_regions[
            np.isnan(suitable_area_resource_without_small_contiguous_regions)
        ] = 0

        if re_technology == 'solarpv':
            indicative_yield_gwh = (
                suitable_area_resource_without_small_contiguous_regions.sum()
                * raster_pixel_size_m
                * raster_pixel_size_m
                * (365 / 1000000)
                * (0.16 / 5)
                * land_discount
            )

        if re_technology == 'solarcsp':
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
                csp_footprint_mw_per_km2
                * area_per_spatial_cluster
                * land_discount
            )
            indicative_yield_gwh = (
                csp_max_capacity_per_spatial_cluster
                * 8760
                * csp_production_percentage_per_land_class
                / 100
            ).sum() / 1000

        if re_technology == 'wind':
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
                        5
                        * wind_rotor_diameter_meters
                        * 3
                        * wind_rotor_diameter_meters
                        / 1000000
                    ),
                    0,
                )
                * (wind_single_turbine_capacity_watts / 1e6)
                * land_discount
            )
            indicative_yield_gwh = (
                wind_max_capacity_per_spatial_cluster
                * 8760
                * wind_production_percentage_per_land_class
                / 100
            ).sum() / 1000
        print(
            f"Indicative {re_technology} Yield is {indicative_yield_gwh} GWH "
            f"at resource threshold of {resource_threshold} KWh/m2-d and "
            f"cutoff of {cutoff_normalized}"
        )

        sufficiency_parameter = (
            np.count_nonzero(suitable_area_resource_without_small_contiguous_regions)
            * raster_pixel_size_m
            * raster_pixel_size_m
            / 1000000
        )
        sufficiency_condition = country_area_km2 * 0.05

        if sufficiency_parameter > sufficiency_condition:
            break_while_loop = 1
        else:
            resource_threshold = resource_threshold - 0.01
            if resource_threshold >= resource_lower_limit:
                cutoff_normalized = (
                    (resource_threshold - resource_lower_limit)
                    / (user_resource_threshold - resource_lower_limit)
                )

    return resource_threshold, indicative_yield_gwh


def control_subpath(value):
    return Path(str(value).strip("/\\"))


'''
*********Main Code**********
For detail on methodology, please consult the paper: Link soon coming 

- Script 1: Methodology Stage 1 (part-i) clipping and distance surfaces, Stage 1 (part-ii) Scoring
- Script 2: Methodology Stage 2 get Competitive resource potential with optional Stage-3 resource sufficiency check
- Script 3: Methodology Stage 4 part(i) Polygonization
- Script 4: Methodology Stage 4 part(ii) Attribution


'''
CONTROL_FILE_NAME = "control_file_msr_creator.xlsx"
INPUT_DATASET_SHEET = "input_datasets"
COUNTRY_WISE_INPUTS_SHEET = "country_wise_input_datasets"
CONFIGURATIONS_SHEET = "configurations"
PATHS_SHEET = "paths"
ANALYSIS_INPUTS_SHEET = "analysis_inputs"

# Read control input file
control_dataset_names = pd.read_excel(
    CONTROL_FILE_NAME,
    sheet_name=INPUT_DATASET_SHEET,
    index_col=0,
)
control_country_wise_inputs = pd.read_excel(
    CONTROL_FILE_NAME,
    sheet_name=COUNTRY_WISE_INPUTS_SHEET,
    index_col=0,
)
control_configurations = pd.read_excel(
    CONTROL_FILE_NAME,
    sheet_name=CONFIGURATIONS_SHEET,
    index_col=0,
)
control_paths = pd.read_excel(
    CONTROL_FILE_NAME,
    sheet_name=PATHS_SHEET,
    index_col=0,
)
control_analysis_inputs = (
    pd.read_excel(
        CONTROL_FILE_NAME,
        sheet_name=ANALYSIS_INPUTS_SHEET,
        index_col=0,
    )
    .transpose()
    .drop(index='comments')
)

home_directory = Path(str(control_paths.loc["home_directory"][0]))
input_spatial_datasets_folder = Path(
    str(control_paths.loc["folder_address_input_spatial_datasets"][0])
)

# Fetch run configuration
all_countries = pd.read_csv(
    control_paths.loc["file_address_country_names_list"][0],
    names=["ct"],
)
re_technology = control_configurations.loc["re_technology"][0]
road_type = control_configurations.loc["road_type"][0]
relax_thresholds_for_resource_lagging_countries = control_configurations.loc[
    "relax_thresholds_for_resource_lagging_countries"
][0]
roads_buffered_search = control_configurations.loc["roads_buffered_search"][0]
grid_buffered_search = control_configurations.loc["grid_buffered_search"][0]
band_count_for_multi_resolve_algorithm = control_configurations.loc[
    "band_count_for_multi_resolve_algorithm"
][0]
default_min_capacity_suitable_to_create_msr_mw = control_configurations.loc[
    "default_min_capacity_suitable_to_create_msr_mw"
][0]
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
scripts = []
for i in range(1, 5):
    if not run_status_of_process_scripts[i - 1] == 0:
        scripts.append(i)


# Assign file names
file_name_population_density = control_dataset_names.loc[
    "file_name_population_density"
][0]
file_name_land_cover = control_dataset_names.loc["file_name_land_cover"][0]
file_name_elevation = control_dataset_names.loc["file_name_elevation"][0]
file_name_protected_areas = control_dataset_names.loc["file_name_protected_areas"][0]
file_name_substations = control_dataset_names.loc["file_name_substations"][0]
file_name_urban_area_load_centers = control_dataset_names.loc[
    "file_name_urban_area_load_centers"
][0]
file_name_roads = control_dataset_names.loc["file_name_roads"][0]
file_name_power_grid = control_dataset_names.loc["file_name_power_grid"][0]
file_name_transmission_grid = control_dataset_names.loc[
    "file_name_transmission_grid"
][0]
file_name_continent_distance_surface_tgrid = control_dataset_names.loc[
    "file_name_continent_distance_surface_tgrid"
][0]
file_name_distribution_grid = control_dataset_names.loc[
    "file_name_distribution_grid"
][0]
file_name_country_boundaries = control_dataset_names.loc[
    "file_name_country_boundaries"
][0]
file_name_ghi_map = control_dataset_names.loc["file_name_ghi_map"][0]
file_name_dni_map = control_dataset_names.loc["file_name_dni_map"][0]
file_name_wind_speed_map = control_dataset_names.loc["file_name_wind_speed_map"][0]
file_name_water_bodies = control_dataset_names.loc["file_name_water_bodies"][0]


# Assign RE tech related inputs

pv_slope_threshold = [int(control_analysis_inputs.pv_slope_threshold)]
csp_slope_threshold = [int(control_analysis_inputs.csp_slope_threshold)]
wind_slope_threshold = [int(control_analysis_inputs.wind_slope_threshold)]
population_threshold = [int(control_analysis_inputs.population_threshold)]
ghi_thresholds = [
    float(control_analysis_inputs.pv_ghi_lower_limit),
    float(control_analysis_inputs.pv_ghi_threshold),
]
dni_thresholds = [
    float(control_analysis_inputs.csp_dni_lower_limit),
    float(control_analysis_inputs.csp_dni_threshold),
]
wind_threshold = [
    float(control_analysis_inputs.wind_speed_lower_limit),
    float(control_analysis_inputs.wind_speed_threshold),
]
land_discount_pv = float(
    int(control_analysis_inputs.solar_pv_land_discount_factor) / 100
)
land_discount_csp = float(
    int(control_analysis_inputs.solar_csp_land_discount_factor) / 100
)
land_discount_wind = float(
    int(control_analysis_inputs.wind_land_discount_factor) / 100
)
max_msr_capacity = int(control_analysis_inputs.msr_max_capacity_allowed)

if re_technology == 'solar_pv':
    resource_raster_name = file_name_ghi_map
    land_discount = land_discount_pv
    slope_threshold = pv_slope_threshold
    re_spatial_footprint_mw_per_km2 = int(
        control_analysis_inputs.pv_footprint_mw_per_km2
    )
    resource_lower_limit = ghi_thresholds[0]
    user_resource_threshold = ghi_thresholds[1]
    run_info_column_headers = ['resource_threshold_kwh_per_m2_day', 'yield_gwh']
if re_technology == 'solar_csp':
    resource_raster_name = file_name_dni_map
    land_discount = land_discount_csp
    slope_threshold = csp_slope_threshold
    re_spatial_footprint_mw_per_km2 = int(
        control_analysis_inputs.csp_footprint_mw_per_km2
    )
    resource_lower_limit = dni_thresholds[0]
    user_resource_threshold = dni_thresholds[1]
    run_info_column_headers = ['resource_threshold_kwh_per_m2_day', 'yield_gwh']
if re_technology == 'wind':
    resource_raster_name = file_name_wind_speed_map
    land_discount = land_discount_wind
    slope_threshold = wind_slope_threshold
    wind_rotor_diameter_meters = int(
        control_analysis_inputs.wind_turbine_rotor_diameter_meters
    )
    number_of_turbines_per_km2 = math.floor(
        1 / (
            5
            * (wind_rotor_diameter_meters / 1000)
            * 3
            * (wind_rotor_diameter_meters / 1000)
        )
    )
    wind_footprint_mw_per_km2 = (
        number_of_turbines_per_km2
        * int(control_analysis_inputs.wind_turbine_capacity_watts)
        / 1e6
    )
    re_spatial_footprint_mw_per_km2 = wind_footprint_mw_per_km2
    resource_lower_limit = wind_threshold[0]
    user_resource_threshold = wind_threshold[1]
    run_info_column_headers = ['resource_threshold_m_per_s', 'yield_gwh']

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

rotor_diameter, turbine_nameplate_capacity = (
    int(control_analysis_inputs.wind_turbine_rotor_diameter_meters),
    int(control_analysis_inputs.wind_turbine_capacity_watts),
)

country_boundaries = gpd.read_file(
    input_spatial_datasets_folder / f"{file_name_country_boundaries}.shp"
)
country_maps_for_clipping_folder = home_directory / "region_boundary_maps"

log_file = pd.DataFrame()
for country_counter in range(0, len(all_countries)):
    region_name_with_spaces = all_countries.ct[country_counter]
    region_name_without_spaces = all_countries.ct[country_counter].replace(" ", "")
    print(f"{Fore.GREEN}Running MSR script for {region_name_without_spaces}")

    if roads_buffered_search:
        roads_buffer_distance_meters = (
            int(control_country_wise_inputs.loc[region_name_without_spaces][1])
            * 1000
        )

    if grid_buffered_search:
        grid_buffer_distance_meters = (
            control_country_wise_inputs.loc[region_name_without_spaces][0]
            * 1000
        )

    output_folder = (
        home_directory
        / control_subpath(control_paths.loc["folder_address_output_folder"][0])
        / region_name_without_spaces
    )
    stage1_clipping_folder = output_folder / "stage1_input_datasets"
    stage1_clipping_folder.mkdir(parents=True, exist_ok=True)
    stage1_scoring_folder = output_folder / "stage1_scored_datasets"
    stage1_scoring_folder.mkdir(parents=True, exist_ok=True)
    stage2_competitive_resource_folder = (
        output_folder / "stage2_competitive_resource_area")
    stage2_competitive_resource_folder.mkdir(parents=True, exist_ok=True)
    polygonization_folder = output_folder / "stage4_polygonization"
    polygonization_folder.mkdir(parents=True, exist_ok=True)
    final_msrs_folder = output_folder / "stage4_msr"
    final_msrs_folder.mkdir(parents=True, exist_ok=True)

    final_msrs_path = final_msrs_folder / f"{re_technology}_final_msrs.shp"

    single_country = country_boundaries[
        country_boundaries.name == region_name_with_spaces
    ]
    country_maps_for_clipping_folder.mkdir(parents=True, exist_ok=True)
    single_country.to_crs('EPSG:4326').to_file(
        country_maps_for_clipping_folder / f"{region_name_without_spaces}.shp"
    )
    country_area_km2 = single_country.to_crs("ESRI:54009").area.iloc[0] / 1000000

    for script in scripts:
        if script == 1:
            upper_left_x, lower_right_y, lower_right_x, upper_left_y = (
                single_country.total_bounds
            )
            min_x, min_y, max_x, max_y = single_country.total_bounds
            clip_geometry = json.dumps(
                mapping(box(upper_left_x, upper_left_y, lower_right_x, lower_right_y))
            )

            print(
                Fore.BLUE
                + "Starting Stage 1 (part-i) clipping and distance surfaces"
            )

            raster_names = [
                file_name_population_density,
                file_name_land_cover,
                file_name_elevation,
                resource_raster_name,
            ]
            for raster_name in raster_names:
                input_raster_dataset = xarray.open_dataarray(
                    input_spatial_datasets_folder / f"{raster_name}.tif"
                )
                clipped_raster = input_raster_dataset.rio.clip_box(
                    min_x, min_y, max_x, max_y
                )
                del input_raster_dataset
                clipped_raster = clipped_raster.rio.clip(single_country.geometry)
                clipped_raster.rio.to_raster(
                    stage1_clipping_folder
                    / f"{re_technology}_{raster_name}_clipped.tif"
                )
                projected_raster = clipped_raster.rio.reproject("ESRI:54009")
                projected_raster.rio.to_raster(
                    stage1_clipping_folder
                    / f"{re_technology}_{raster_name}_projected.tif"
                )
                print(
                    f"clipped and projected to ESRI:54009 {raster_name} "
                    "raster dataset"
                )
            del clipped_raster, projected_raster

            vector_names = [
                file_name_roads,
                file_name_water_bodies,
                file_name_power_grid,
                file_name_transmission_grid,
                file_name_distribution_grid,
                file_name_protected_areas,
            ]
            for vector_name in vector_names:
                clipped_vector = gpd.read_file(
                    input_spatial_datasets_folder / f"{vector_name}.shp",
                    bbox=tuple(single_country.total_bounds),
                )

                if not clipped_vector.empty and vector_name == file_name_roads:
                    clipped_vector = clipped_vector[clipped_vector.gp_rtp <= road_type]

                if not clipped_vector.empty:
                    clipped_vector = gpd.clip(clipped_vector, single_country.envelope)
                    clipped_vector["raster_value"] = 1
                else:
                    clipped_vector = gpd.GeoDataFrame(
                        {'geometry': single_country.envelope},
                        geometry='geometry',
                    )
                    clipped_vector["raster_value"] = 1
                    if vector_name in [file_name_protected_areas, file_name_water_bodies]:
                        clipped_vector["raster_value"] = 0

                clipped_vector = gpd.clip(clipped_vector, single_country.geometry)
                if clipped_vector.empty:
                    clipped_vector = gpd.GeoDataFrame(
                        {'geometry': single_country.geometry},
                        geometry='geometry',
                    )
                    clipped_vector["raster_value"] = 1
                    if vector_name in [file_name_protected_areas, file_name_water_bodies]:
                        clipped_vector["raster_value"] = 0
                clipped_vector = clipped_vector.to_crs('EPSG:4326')
                clipped_vector.to_file(
                    stage1_clipping_folder
                    / f"{re_technology}_{vector_name}_clipped.shp"
                )
                print(f"clipped {vector_name} vector dataset")
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
                        stage1_clipping_folder / (
                            f"{re_technology}_{vector_name}_rasterized_projected.tif"
                        )
                    )
                print(
                    f"{vector_name} vector dataset is rasterized and "
                    "projected ESRI:54009"
                )
            del clipped_vector, rasterized_clipped_vector

            elevation_raster = rd.LoadGDAL(
                str(stage1_clipping_folder / (
                    f"{re_technology}_{file_name_elevation}_projected.tif"
                ))
            )
            slope_raster = rd.TerrainAttribute(
                elevation_raster,
                attrib='slope_percentage',
            )
            rd.SaveGDAL(
                str(stage1_clipping_folder / f"{re_technology}_slope_projected.tif"),
                slope_raster,
            )
            del elevation_raster, slope_raster

            distance_dataset_names = [
                file_name_power_grid,
                file_name_transmission_grid,
                file_name_distribution_grid,
                file_name_roads,
            ]
            for dataset_name in distance_dataset_names:
                raster = xarray.open_dataarray(
                    stage1_clipping_folder / (
                        f"{re_technology}_{dataset_name}_rasterized_projected.tif"
                    )
                )
                distance_surface = xrspatial.proximity(
                    raster.squeeze('band'),
                    distance_metric="EUCLEADIAN",
                )
                distance_surface = distance_surface.rio.reproject("EPSG:4326")
                distance_surface = distance_surface.rio.clip(single_country.envelope)
                distance_surface = distance_surface.rio.clip(single_country.geometry)
                distance_surface = distance_surface.rio.reproject("ESRI:54009")
                distance_surface.rio.to_raster(
                    stage1_clipping_folder / (
                        f"{re_technology}_distance_surface_{dataset_name}.tif"
                    )
                )
                print(f"{dataset_name} distance surface created")

            print("Stage 1 (part-i) clipping and distance surfaces finished")

            print(f"{Fore.BLUE}Starting Stage 1 (part-ii) Scoring")

            layer_to_score_names = [
                f"{file_name_population_density}_projected",
                f"{file_name_land_cover}_projected",
                f"{file_name_elevation}_projected",
                f"{file_name_protected_areas}_rasterized_projected",
                f"{file_name_water_bodies}_rasterized_projected",
                f"distance_surface_{file_name_roads}",
                f"distance_surface_{file_name_transmission_grid}",
                "slope_projected",
                f"{resource_raster_name}_projected",
            ]
            for layer_to_score_name in layer_to_score_names:
                layer_to_score = xarray.open_dataarray(
                    stage1_clipping_folder / (
                        f"{re_technology}_{layer_to_score_name}.tif"
                    )
                )
                scored_layer = layer_to_score * 0

                if layer_to_score_name == f"{file_name_land_cover}_projected":
                    scored_layer = scored_layer.where(
                        ~layer_to_score.isin(
                            [
                                11, 14, 20, 30, 110, 120, 130, 140, 150, 180,
                                190, 200,
                            ]
                        ),
                        1,
                    )

                if layer_to_score_name == f"{file_name_elevation}_projected":
                    scored_layer = scored_layer.where(~(layer_to_score < 2000), 1)

                if (
                    layer_to_score_name
                    == f"{file_name_population_density}_projected"
                ):
                    scored_layer = scored_layer.where(
                        ~(layer_to_score <= population_threshold[0]),
                        1,
                    )

                if (
                    layer_to_score_name
                    == f"{file_name_protected_areas}_rasterized_projected"
                ):
                    scored_layer = scored_layer.where(~(layer_to_score == 0), 1)

                if (
                    layer_to_score_name
                    == f"{file_name_water_bodies}_rasterized_projected"
                ):
                    scored_layer = scored_layer.where(~(layer_to_score == 0), 1)

                if layer_to_score_name == f"distance_surface_{file_name_roads}":
                    if roads_buffered_search:
                        scored_layer = scored_layer.where(
                            ~(layer_to_score <= roads_buffer_distance_meters), 1)
                    else:
                        scored_layer = scored_layer.where(~(layer_to_score >= 0), 1)

                if (
                    layer_to_score_name
                    == f"distance_surface_{file_name_transmission_grid}"
                ):
                    if grid_buffered_search:
                        scored_layer = scored_layer.where(
                            ~(layer_to_score <= grid_buffer_distance_meters),
                            1,
                        )
                    else:
                        scored_layer = scored_layer.where(~(layer_to_score >= 0), 1)

                if layer_to_score_name == "slope_projected":
                    scored_layer = scored_layer.where(
                        ~(layer_to_score <= slope_threshold[0]),
                        1,
                    )

                if layer_to_score_name == f"{resource_raster_name}_projected":
                    scored_layer = scored_layer.where(
                        ~(layer_to_score < resource_lower_limit), -1)
                    scored_layer = scored_layer.where(
                        ~(layer_to_score > user_resource_threshold), 1)
                    scored_layer = scored_layer.where(
                        ~(scored_layer == 0),
                        (layer_to_score - resource_lower_limit)
                        / (user_resource_threshold - resource_lower_limit),
                    )

                scored_layer.rio.to_raster(
                    stage1_scoring_folder / (
                        f"{re_technology}_{layer_to_score_name}_scored.tif"
                    )
                )
                print(f"{layer_to_score_name} scored")
            print("Stage 1 (part-ii) Scoring finished")

        if script == 2:
            print(f"{Fore.BLUE}Starting Stage 2 Get Competitive resource")

            resource_raster = xarray.open_dataarray(
                stage1_clipping_folder / (
                    f"{re_technology}_{resource_raster_name}_projected.tif"
                )
            )
            suitable_area_raster_with_no_exclusions = xarray.open_dataarray(
                stage1_scoring_folder / (
                    f"{re_technology}_{resource_raster_name}_projected_scored.tif"
                )
            )

            suitable_area_raster = suitable_area_raster_with_no_exclusions
            exclusion_count = 0
            scored_layer_names = [
                f"{file_name_population_density}_projected",
                f"{file_name_land_cover}_projected",
                f"{file_name_elevation}_projected",
                f"{file_name_elevation}_projected",
                f"{file_name_protected_areas}_rasterized_projected",
                f"{file_name_water_bodies}_rasterized_projected",
                f"distance_surface_{file_name_roads}",
                f"distance_surface_{file_name_transmission_grid}",
                "slope_projected",
            ]
            for scored_layer_name in scored_layer_names:
                scored_layer = xarray.open_dataarray(
                    stage1_scoring_folder / (
                        f"{re_technology}_{scored_layer_name}_scored.tif"
                    )
                )
                if not (
                    re_technology != 'wind'
                    and scored_layer_name == f"{file_name_elevation}_projected"
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
                stage2_competitive_resource_folder / (
                    f"{re_technology}_suitable_resource.tif"
                )
            )

            competitive_area_resource_raster = (
                competitive_area_resource_raster.rio.reproject("EPSG:4326"))
            competitive_area_resource_raster = competitive_area_resource_raster.rio.clip(
                single_country.geometry)
            competitive_area_resource_raster = (
                competitive_area_resource_raster.rio.reproject("ESRI:54009"))
            competitive_area_resource_raster.rio.to_raster(
                stage2_competitive_resource_folder / (
                    f"{re_technology}_competitive_resource.tif"))

            if relax_thresholds_for_resource_lagging_countries:
                print(
                    f"{Fore.BLUE}Performing resource sufficiency check (stage-3) "
                    "before finishing the stage-2"
                )

                suitable_area_resource_raster_path = (
                    stage2_competitive_resource_folder / (
                        f"{re_technology}_suitable_resource.tif"))
                csp_footprint_mw_per_km2 = float(
                    control_analysis_inputs.csp_footprint_mw_per_km2)
                wind_rotor_diameter_meters = float(
                    control_analysis_inputs.wind_turbine_rotor_diameter_meters)
                wind_single_turbine_capacity_watts = float(
                    control_analysis_inputs.wind_turbine_capacity_watts)
                resource_threshold, indicative_yield_gwh = run_resource_sufficiency_stage(
                    suitable_area_resource_raster_path,
                    user_resource_threshold,
                    resource_lower_limit,
                    re_technology,
                    land_discount,
                    csp_footprint_mw_per_km2,
                    wind_rotor_diameter_meters,
                    wind_single_turbine_capacity_watts,
                    default_min_contiguous_area_suitable_for_msr_km2,
                    country_area_km2,
                )

                if resource_threshold < user_resource_threshold:
                    competitive_area_resource_raster = suitable_area_resource_raster.where(
                        ~(suitable_area_resource_raster < resource_threshold), 0)
                    competitive_area_resource_raster = (
                        competitive_area_resource_raster.rio.reproject("EPSG:4326"))
                    competitive_area_resource_raster = (
                        competitive_area_resource_raster.rio.clip(single_country.geometry))
                    competitive_area_resource_raster = (
                        competitive_area_resource_raster.rio.reproject("ESRI:54009"))
                    competitive_area_resource_raster.rio.to_raster(
                        stage2_competitive_resource_folder / (
                            f"{re_technology}_competitive_resource_relaxed.tif"))
                    log_file = log_file.append(pd.DataFrame(
                        [
                            f"{region_name_without_spaces}: Resource threshold "
                            f"reduced to: {resource_threshold}"
                        ],
                        columns=['log'],
                    ))

                pd.DataFrame(
                    {
                        'resource_threshold': resource_threshold,
                        'indicative_yield_gwh': indicative_yield_gwh,
                        'minimum_msr_capacity_criteria': (
                            default_min_capacity_suitable_to_create_msr_mw),
                    },
                    index=[0],
                ).to_csv(
                    stage2_competitive_resource_folder / (
                        f"{re_technology}_log_resource_identification_polygonization.csv"))
                print(
                    "Stage 2 (get Competitive resource) finished with resource "
                    f"sufficiency check and ResourceThreshold of {resource_threshold}"
                )

            else:
                resource_threshold = user_resource_threshold
                indicative_yield_gwh = np.nan
                pd.DataFrame(
                    {
                        'resource_threshold': resource_threshold,
                        'indicative_yield_gwh': indicative_yield_gwh,
                        'minimum_msr_capacity_criteria': (
                            default_min_capacity_suitable_to_create_msr_mw),
                    },
                    index=[0],
                ).to_csv(
                    stage2_competitive_resource_folder / (
                        f"{re_technology}_log_resource_identification_polygonization.csv"))
                print("Stage 2 (get Competitive resource) finished without resource sufficiency check")
        if script == 3:

            print(f"{Fore.BLUE}Starting stage 4 part(i) Polygonization")

            inputs_from_stage2 = pd.read_csv(
                stage2_competitive_resource_folder / (
                    f"{re_technology}_log_resource_identification_polygonization.csv"))
            resource_threshold = inputs_from_stage2.resource_threshold.values[0]
            indicative_yield_gwh = inputs_from_stage2.indicative_yield_gwh.values[0]

            run_info_values = [(resource_threshold, indicative_yield_gwh)]
            run_info = pd.DataFrame(run_info_values, columns=run_info_column_headers)
            run_info.to_csv(final_msrs_folder / f"{re_technology}run_info.csv", index=False)

            if resource_threshold < user_resource_threshold:
                resource_potential_raster_path = (
                    stage2_competitive_resource_folder / (
                        f"{re_technology}_competitive_resource_relaxed.tif"))
            else:
                resource_potential_raster_path = (
                    stage2_competitive_resource_folder / (
                        f"{re_technology}_competitive_resource.tif"))

            min_contiguous_area_suitable_for_msr_km2 = (
                default_min_contiguous_area_suitable_for_msr_km2)

            msrs = polygonize_resource_potential(
                resource_potential_raster_path,
                polygonization_folder,
                re_technology,
                resource_threshold,
                band_count_for_multi_resolve_algorithm,
                max_area_to_cap_msrs_km2,
                min_contiguous_area_suitable_for_msr_km2,
            )

            if type(msrs) == int:
                if final_msrs_path.is_file():
                    for suffix in [".shp", ".shx", ".prj", ".cpg", ".dbf"]:
                        (final_msrs_folder / f"{re_technology}_final_msrs{suffix}").unlink()
                print(
                    f"{Fore.RED}*******Sufficient resource not found to create "
                    "any MSRs**************"
                )
                log_file = log_file.append(pd.DataFrame(
                    [
                        f"{region_name_without_spaces}: Sufficient resource "
                        "not found to create any MSRs"
                    ],
                    columns=['log'],
                ))
                break
            else:
                msrs.to_file(final_msrs_path)
                print(f"{Fore.GREEN}MSR creation done")

        if script == 4:
            print(
                f"{Fore.BLUE}Stage 4 part(ii) Attribution: Calculating "
                "attributes from zone centroids from Lines, Roads, Power "
                "stations, Loadcenters"
            )

            msrs = gpd.read_file(final_msrs_path)
            msrs['area_km2'] = msrs.geometry.area / 1000000
            msrs['capacity_mw'] = (
                msrs['area_km2'] * land_discount * re_spatial_footprint_mw_per_km2)

            distance_to_roads_stats_per_msr = zonal_stats(
                final_msrs_path,
                stage1_clipping_folder / (
                    f"{re_technology}_distance_surface_{file_name_roads}.tif"),
                stats="count min mean max median sum",
            )
            msrs['road_dist'] = (
                pd.DataFrame(distance_to_roads_stats_per_msr)['mean'] / 1000)
            print("Distances to roads inserted")

            clipped_vector = gpd.read_file(
                input_spatial_datasets_folder / f"{file_name_transmission_grid}.shp",
                bbox=tuple(single_country.total_bounds),
            )
            if not clipped_vector.empty:
                clipped_vector = gpd.clip(clipped_vector, single_country.geometry)
            if clipped_vector.empty:
                distance_to_tgrid_stats_per_msr = zonal_stats(
                    final_msrs_path,
                    input_spatial_datasets_folder / (
                        f"{file_name_continent_distance_surface_tgrid}.tif"),
                    stats="count min mean max median sum",
                )
                msrs['t_dist_gf'] = (
                    pd.DataFrame(distance_to_tgrid_stats_per_msr)['mean'] / 1000)
                print("Transmission Grid is absent. Distance from nearest crossborder transmission grid is computed")
            else:
                distance_to_tgrid_stats_per_msr = zonal_stats(
                    final_msrs_path,
                    stage1_clipping_folder / (
                        f"{re_technology}_distance_surface_{file_name_transmission_grid}.tif"),
                    stats="count min mean max median sum",
                )

                msrs['t_dist_gf'] = (
                    pd.DataFrame(distance_to_tgrid_stats_per_msr)['mean'] / 1000)
                print("Distances to Transmission lines inserted")

            distance_to_dgrid_stats_per_msr = zonal_stats(
                final_msrs_path,
                stage1_clipping_folder / (
                    f"{re_technology}_distance_surface_{file_name_distribution_grid}.tif"),
                stats="count min mean max median sum",
            )
            msrs['d_dist_gf'] = (
                pd.DataFrame(distance_to_dgrid_stats_per_msr)['mean'] / 1000)
            print("Distances to Distribution lines inserted")

            msrs['td_dist_gf'] = msrs[['t_dist_gf', 'd_dist_gf']].min(axis=1)
            print("Distances to closest grid line (Transmission or Distribution) inserted")

            substations = gpd.read_file(
                input_spatial_datasets_folder / f"{file_name_substations}.shp",
                bbox=tuple(single_country.total_bounds),
            ).to_crs("ESRI:54009")
            msrs['substation_dist'] = (
                msrs.centroid.apply(
                    minimum_distance_of_msr_centroid_from_geometry_set,
                    geometry_set=substations.centroid,
                )
                / 1000
            )
            print("distance to nearest substation inserted")

            load_centers = gpd.read_file(
                input_spatial_datasets_folder / (
                    f"{file_name_urban_area_load_centers}.shp"),
                bbox=tuple(single_country.total_bounds),
            ).to_crs("ESRI:54009")
            msrs['load_dist'] = (
                msrs.centroid.apply(
                    minimum_distance_of_msr_centroid_from_geometry_set,
                    geometry_set=load_centers.centroid,
                )
                / 1000
            )
            print("distance to nearest load center inserted")

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
            msrs['city_name'] = load_center_related_attributes['closest_city_name']
            msrs['city_pop'] = load_center_related_attributes[
                'closest_city_population_count']
            msrs['cities_100km'] = load_center_related_attributes['cities_100km']
            msrs['city_count_100km'] = load_center_related_attributes[
                'city_count_within_100km']
            msrs['pop_in_100km'] = load_center_related_attributes['pop_within_100km']
            print("load center related attributes inserted")
            msrs.to_file(final_msrs_path)

            print(f"{Fore.GREEN}Attribution complete")

            log_file = log_file.append(pd.DataFrame(
                [f"{region_name_without_spaces}:Attribution completed"],
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
            log_file.to_csv(home_directory / f"{date_time_stamp}{re_technology}_log_file.csv")
    #try:shutil.rmtree(stage1_clipping_folder)
    # #except: pass
    # try:shutil.rmtree(SubfolderStage1_Scoring)
    # except: pass
    # try:shutil.rmtree(SubfolderStage2_CompetitiveResource)
    # except: pass
    # try:shutil.rmtree(SubfolderStage2_CompetitiveResource)
    # except: pass
    # try:shutil.rmtree(SubFolder_Polygonization)
    # except: pass
