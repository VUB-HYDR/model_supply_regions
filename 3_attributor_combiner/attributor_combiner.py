"""Combine and attribute region-level MSR shapefiles.

This script is part of the Model Supply Regions (MSR) workflow. 
It reads MSR shapefiles created by the MSR Creator, adds resource,
capacity factor profiles and yield from the Profile Generator,
cost attributes, and combines the results into technology-level 
prescreen shapefiles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from matplotlib.style import context
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
import pandas as pd
from rasterstats import zonal_stats
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry


def _disable_shapely_array_interface():
    """Compatibility workaround for old Shapely with newer NumPy."""

    def _raise_attribute_error(self):
        raise AttributeError("__array_interface__ is intentionally disabled")

    geometry_classes = (
        BaseGeometry,
        BaseMultipartGeometry,
        Point,
        LineString,
        Polygon,
        MultiPoint,
        MultiLineString,
        MultiPolygon,
    )

    for geometry_class in geometry_classes:
        geometry_class.__array_interface__ = property(
            _raise_attribute_error
        )

CONTROL_FILE_NAME = "control_file_attributor_combiner.xlsx"
PATHS_SHEET = "paths"
CONFIGURATIONS_SHEET = "configurations"
PARAMETERS_SHEET = "parameters"
DATASETS_SHEET = "datasets"

LOGGER = logging.getLogger(__name__)

@dataclass
class AttributorCombinerConfig:
    """Run-wide settings loaded from the Attributor Combiner control files."""

    control_file: Path
    control_parameters: pd.DataFrame
    control_configurations: pd.DataFrame
    control_datasets: pd.DataFrame
    input_folder_datasets: Path
    output_folder: Path
    regions: pd.DataFrame
    cost_assumptions: pd.DataFrame
    file_name_resource_raster: str
    technologies_to_run: list[str]
    re_technology: str
    time_profile: str
    hours_in_year: int
    days_in_year: int
    wind_hub_height: int
    elevation_threshold: int
    reference_plant_capacity: int
    solar_pv_loss_adjustment: float
    wind_loss_adjustment: float


@dataclass
class RegionPaths:
    """Output paths for one region and technology."""

    output_folder_attributor_combiner: Path
    output_path: Path
    output_profile_generator: Path
    output_resource_raster: Path
    output_msr_creator: Path

@dataclass
class RegionContext:
    """Technology-specific names and paths used during attribution."""

    region_name_with_spaces: str
    region_name_without_spaces: str
    paths: RegionPaths

def configure_logging(level: int = logging.INFO) -> None:
    """Configure terminal logging for MSR workflow progress."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_control_workbook(control_file: Path) -> dict[str, pd.DataFrame]:
    """Read the Attributor Combiner control workbook.

    Returns:
        dict[str, pandas.DataFrame]: A dictionary containing the control 
        workbook sheets used by the script.
    """

    return {
        "control_paths": pd.read_excel(
            control_file,
            sheet_name=PATHS_SHEET,
            index_col=0,
        ),
        "control_parameters": pd.read_excel(
            control_file,
            sheet_name=PARAMETERS_SHEET,
            index_col=0,
        ),
        "control_configurations": pd.read_excel(
            control_file,
            sheet_name=CONFIGURATIONS_SHEET,
            index_col=0,
        ),
        "control_datasets": pd.read_excel(
            control_file,
            sheet_name=DATASETS_SHEET,
            index_col=0,
        ),
    }


def build_attributor_combiner_config(
    control_file: Path,
    control: dict[str, pd.DataFrame],
) -> AttributorCombinerConfig:
    """Build run-wide Attributor Combiner configuration from control DataFrames.

    Returns:
        AttributorCombinerConfig: Run-wide settings used by all countries and stages.
    """

    control_paths = control["control_paths"]
    control_parameters = control["control_parameters"]
    control_configurations = control["control_configurations"]
    control_datasets = control["control_datasets"]

    # paths
    input_folder_datasets = Path(
        str(control_paths.loc["input_folder_datasets"][0])
    )
    output_folder = Path(str(control_paths.loc["output_folder"][0]))

    regions = pd.read_csv(
        Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
        str(control_datasets.loc["file_name_regions"][0])),
        names=["region"],
    )

    cost_assumptions = pd.read_csv(
        Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
        str(control_datasets.loc["file_name_cost_assumptions"][0])),
        sep=";", index_col=0)
    
    file_name_resource_raster = str(control_datasets.loc["file_name_resource_raster"][0])

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

    local_time_profile = bool(control_configurations.loc["local_time_profile"][0])
    if local_time_profile:
        time_profile = "local_time_profiles"
    else:
        time_profile = "utc_profiles"

    wind_hub_height = int(control_parameters.loc["wind_hub_height"][0])
    elevation_threshold = int(control_parameters.loc["elevation_threshold"][0])
    hours_in_year = int(control_parameters.loc["hours_in_year"][0])
    days_in_year = int(control_parameters.loc["days_in_year"][0])
    reference_plant_capacity = int(control_parameters.loc["reference_plant_capacity"][0])
    solar_pv_loss_adjustment = float(control_parameters.loc["solarpv_loss_adjustment"][0]) / 100
    wind_loss_adjustment = float(control_parameters.loc["wind_loss_adjustment"][0]) / 100

    return AttributorCombinerConfig(
        control_file=control_file,
        control_configurations=control_configurations,
        control_parameters=control_parameters,
        control_datasets=control_datasets,
        input_folder_datasets=input_folder_datasets,
        output_folder=output_folder,
        regions=regions,
        cost_assumptions=cost_assumptions,
        file_name_resource_raster=file_name_resource_raster,
        re_technology=re_technology,
        technologies_to_run=technologies_to_run,
        time_profile=time_profile,
        wind_hub_height=wind_hub_height,
        elevation_threshold=elevation_threshold,
        hours_in_year=hours_in_year,
        days_in_year=days_in_year,
        reference_plant_capacity=reference_plant_capacity,
        solar_pv_loss_adjustment=solar_pv_loss_adjustment,
        wind_loss_adjustment=wind_loss_adjustment,
    )

def prepare_region_context(
    region_name: str,
    config: AttributorCombinerConfig,
) -> RegionContext:
    """Prepare region-specific output folders and file paths."""

    region_name_without_spaces = region_name.replace(" ", "")

    output_subfolder_resource_raster = Path(
            Path(str(config.output_folder))
            / "1_msr_creator"
            / region_name_without_spaces
            / "stage1_input_datasets"
    )
    matches = list(output_subfolder_resource_raster.glob(f"{config.re_technology}_{config.file_name_resource_raster}_projected.tif"))
    if not matches:
        raise FileNotFoundError(
            f"No projected resource raster found for {region_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_resource_raster}: {matches}"
        )
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple projected resource rasters found for {region_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_resource_raster}: {matches}"
        )
    output_resource_raster = matches[0]

    output_subfolder_attribution = Path(
        Path(str(config.output_folder))
        / "1_msr_creator"
        / region_name_without_spaces
        / "stage6_attribution"
    )
    if config.re_technology == "solarpv":
        output_msr_creator = Path(
            output_subfolder_attribution
            / f"{config.re_technology}_final_msrs.shp"
        )
    elif config.re_technology == "wind":
        output_msr_creator = Path(
            output_subfolder_attribution
            / f"{config.re_technology}_{config.elevation_threshold}_final_msrs.shp"
        )
    
    output_subfolder_profile_generator = Path(
        Path(str(config.output_folder))
        / "2_profile_generator"
        / region_name_without_spaces
        / config.time_profile
    )
    matches = list(output_subfolder_profile_generator.glob(f"{config.re_technology}*CF_profiles.csv"))
    if not matches:
        raise FileNotFoundError(
            f"No CF profile found for {region_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_profile_generator}"
        )
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple CF profiles found for {region_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_profile_generator}"
        )
    output_profile_generator = matches[0]

    output_folder_attributor_combiner = Path(
        Path(str(config.output_folder))
        / "3_attributor_combiner"
        / region_name_without_spaces
    )
    if config.re_technology == "solarpv":
        output_path = (
            output_folder_attributor_combiner
            / f"{config.re_technology}_prescreen.shp"
        )
    elif config.re_technology == "wind":
        output_path = (
            output_folder_attributor_combiner
            / f"{config.re_technology}_{config.elevation_threshold}_prescreen.shp"
        )


    paths = RegionPaths(
        output_folder_attributor_combiner=output_folder_attributor_combiner,
        output_path=output_path,
        output_profile_generator=output_profile_generator,
        output_resource_raster=output_resource_raster,
        output_msr_creator=output_msr_creator,
    )
    output_folder_attributor_combiner.mkdir(parents=True, exist_ok=True)

    LOGGER.info(
        f"Prepared region context | region={region_name_without_spaces} " 
        f"| output={output_folder_attributor_combiner}"
    )
    return RegionContext(
        region_name_with_spaces=region_name,
        region_name_without_spaces=region_name_without_spaces,
        paths=paths,
    )

def add_attributes(
    context: RegionContext,
    config: AttributorCombinerConfig,
) -> None:
    
    """Add Solar PV and wind resource and cost attributes.

    GHI is reported as kWh/m2/day, capacity factor is reported as percent and
    annual yield as GWh. The loss adjustment preserves the original assumption
    for outage, inverter, and wiring losses.

    Wind speed is in m/s, capacity factor is percent, and annual yield is GWh.
    The IEC class thresholds preserve the original simplified assumptions.
    """
    
    paths = context.paths

    profiles = pd.read_csv(paths.output_profile_generator)
    stat = zonal_stats(str(paths.output_msr_creator), str(paths.output_resource_raster), stats="mean")
    _disable_shapely_array_interface()
    msrs = gpd.read_file(paths.output_msr_creator)
    msrs = msrs.sort_values(by=["FID"])
    msrs["CtryName"] = context.region_name_without_spaces


    if config.re_technology == "solarpv":

        # Add resource attributes
        msrs["GHIkWhm2d"] = pd.Series(stat[0]["mean"], index=msrs.index)
        msrs["RawERAmean"] = profiles["ERA_GHI KWh/m2/yr"] / config.days_in_year
        msrs["CorAdderWh"] = profiles["BiasCorrection Adder Wh for solar hours"]
        msrs["CF"] = (
            config.solar_pv_loss_adjustment
            * profiles.iloc[:, - config.hours_in_year:].sum(axis=1)
            * 100
            / config.hours_in_year
            )
        msrs["Y_GWh"] = (
            msrs["CapacityMW"] 
            * msrs["CF"] 
            * config.hours_in_year 
            / 100000
        )
        LOGGER.info(
            f"Resource attributes inserted | region={context.region_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

        # Add cost attributes
        msrs = calculate_cost_attributes(
            msrs, 
            config,
            row="SolarPV",
            cf_column_name="CF",
        )
        LOGGER.info(
            f"Cost attributes inserted | region={context.region_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

    elif config.re_technology == "wind":

        # Add resource attributes
        msrs["MeanSpeed"] = pd.Series(stat[0]["mean"], index=msrs.index)
        msrs["IEC_Class"] = ""
        msrs.loc[msrs["MeanSpeed"] <= 7.5, "IEC_Class"] = "Class-3"
        msrs.loc[msrs["MeanSpeed"] >= 8.5, "IEC_Class"] = "Class-1"
        msrs.loc[
                (msrs["MeanSpeed"] > 7.5) 
                & (msrs["MeanSpeed"] < 8.5),
                "IEC_Class",
                ] = "Class-2"
        msrs["ERA_WSpeed"] = profiles["ERA-Raw Annual Mean Speed m/s"]
        cf_col_name = f"CF{config.wind_hub_height}m"
        msrs[cf_col_name] = (
            config.wind_loss_adjustment
            * profiles.iloc[:, - config.hours_in_year:].sum(axis=1)
            * 100
            / config.hours_in_year
        )
        
        msrs[f"Y_GWh{config.wind_hub_height}m"] = (
            msrs["CapacityMW"]
            * msrs[cf_col_name]
            * config.hours_in_year
            / 100000
        )
        LOGGER.info(
            f"Resource attributes inserted | region={context.region_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

        # Add cost attributes by IEC class
        iec_classes = ["Class-3", "Class-2", "Class-1"]
        rows = ["Wind_Class3", "Wind_Class2", "Wind_Class1"]

        for iec_class, row in zip(iec_classes, rows):
            class_filter = msrs["IEC_Class"] == iec_class
            msrs = calculate_cost_attributes(
                                    msrs,
                                    config,
                                    row=row,
                                    cf_column_name=cf_col_name,
                                    row_filter=class_filter
            )
        LOGGER.info(
            f"Cost attributes inserted | region={context.region_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

    # TODO: Add offshore wind and solar CSP attributes

    msrs.to_file(paths.output_path)

    plot_lcoe_composition(msrs, config, context)
    plot_grid_distance_composition(msrs, config, context)
    plot_cf_composition(msrs, config, context)

    LOGGER.info(
            f"Attribution complete | region={context.region_name_without_spaces} "
            f"| technology={config.re_technology} | path={paths.output_path}"
        )


def calculate_cost_attributes(
    msrs: gpd.GeoDataFrame,
    config: AttributorCombinerConfig,
    row: str,
    cf_column_name: str,
    row_filter=None
) -> gpd.GeoDataFrame:

    if row_filter is None:
        row_filter = msrs.index
    
    gen_cap_cost = float(config.cost_assumptions.loc[row].iloc[0])           # Generation capital cost ($/kW)
    gen_fixed_om = float(config.cost_assumptions.loc[row].iloc[1])           # Generation fixed O&M cost ($/kW/yr)
    gen_var_om = float(config.cost_assumptions.loc[row].iloc[2])             # Generation variable O&M cost ($/MWh)
    gen_cap_rec = float(config.cost_assumptions.loc[row].iloc[3])            # Generation capital recovery factor

    trans_cap_cost = float(config.cost_assumptions.loc[row].iloc[5])         # Transmission capital cost ($/MW/km)
    trans_fixed_om = float(config.cost_assumptions.loc[row].iloc[6])         # Transmission fixed O&M cost ($/km)
    grid_cap_rec = float(config.cost_assumptions.loc[row].iloc[8])           # Grid capital recovery factor
    substation_cap_cost = float(config.cost_assumptions.loc[row].iloc[7])    # Substation capital cost ($)

    road_cap_cost = float(config.cost_assumptions.loc[row].iloc[10])         # Road capital cost ($/km)
    road_fixed_om = float(config.cost_assumptions.loc[row].iloc[11])         # Road fixed O&M cost ($/km)
    road_cap_rec = float(config.cost_assumptions.loc[row].iloc[12])          # Road capital recovery factor

    msrs.loc[row_filter, "sLCOE-MWh"] = (
        1000
        * (
            (
                gen_cap_cost
                * gen_cap_rec
                + gen_fixed_om / 1000    
            )
            / (config.hours_in_year * msrs.loc[row_filter, cf_column_name] / 100)
        )
        + gen_var_om
    )

    msrs.loc[row_filter, "tLCOE-MWh"] = (
        (
            (
                trans_cap_cost
                * grid_cap_rec
                + trans_fixed_om
                )
                * msrs.loc[row_filter, "T_Dist_gf"]
                + substation_cap_cost
                * grid_cap_rec
        ) 
        / (config.hours_in_year * msrs.loc[row_filter, cf_column_name] / 100)
    )

    msrs.loc[row_filter, "tCAPEX-kW"] = (
        trans_cap_cost
        * msrs.loc[row_filter, "T_Dist_gf"] 
        + substation_cap_cost
    ) / 1000

    msrs.loc[row_filter, "rLCOE-MWh"] = (
        (
            road_cap_cost
            * road_cap_rec
            + road_fixed_om
        )
        * msrs["RoadDist"]
    ) / (
        config.hours_in_year
        * config.reference_plant_capacity
        * msrs[cf_column_name]
        / 100
    )
    
    msrs.loc[row_filter, "rCAPEX-kW"] = (
        road_cap_cost
        * msrs["RoadDist"]
    ) / (config.reference_plant_capacity * 1000)
    
    msrs.loc[row_filter, "LCOE-MWh"] = (
        msrs.loc[row_filter, "tLCOE-MWh"] + msrs.loc[row_filter, "rLCOE-MWh"] + msrs.loc[row_filter, "sLCOE-MWh"]
    )
    
    msrs.loc[row_filter, "trCAPEX-kW"] = msrs.loc[row_filter, "rCAPEX-kW"] + msrs.loc[row_filter, "tCAPEX-kW"]
    return msrs

def plot_lcoe_composition(
    msrs: pd.DataFrame,
    config: MSRAttributorCombinerConfig,
    context: RegionContext,
) -> None:
    """Plot region-level composition of MSR area by LCOE class.

    """

    if config.re_technology == "wind":
        re_name = "Wind"
        colormap = "Greens"
        bins = [-np.inf, 50, 65, 80, 95, np.inf]
        labels = ["≤ 50", "50–65", "65–80", "80–95", "> 95"]
    elif config.re_technology == "solarpv":
        re_name = "Solar PV"
        colormap = "Oranges"
        bins = [-np.inf, 105, 110, 115, 120, np.inf]
        labels = ["≤ 105", "105–110", "110–115", "115–120", "> 120"]

    output_path = (
        context.paths.output_folder_attributor_combiner
        / f"{config.re_technology}_lcoe_composition.png"
    )

    msrs = msrs.copy()

    msrs["LCOE_bin"] = pd.cut(
        msrs["LCOE-MWh"],
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=True,
    )

    area_by_lcoe = (
        msrs.groupby(
            ["CtryName", "LCOE_bin"],
            observed=False,
        )["AreakM2"]
        .sum()
        .unstack(fill_value=0)
    )

    area_totals = area_by_lcoe.sum(axis=1)
    area_totals = area_totals.replace(0, np.nan)

    lcoe_percentage = (
        area_by_lcoe.div(area_totals, axis=0) * 100
    ).fillna(0)

    lcoe_percentage = lcoe_percentage.loc[
        :,
        lcoe_percentage.sum(axis=0) > 0,
    ]

    cmap = plt.get_cmap(colormap)
    color_map = {
        label: cmap(i / (len(labels) - 1))
        for i, label in enumerate(labels)
    }


    fig, ax = plt.subplots(
        figsize=(6, 3),
    )

    colors = [
        color_map[column]
        for column in lcoe_percentage.columns
    ]

    lcoe_percentage.plot(
        kind="barh",
        stacked=True,
        ax=ax,
        width=0.6,
        color=colors,
        legend=False,
    )

    ax.set_xlim(0, 100)
    ax.set_xlabel(f"Share of {re_name} MSR area (%)")
    ax.set_ylabel("")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    handles = [
        Patch(
            facecolor=color_map[column],
            label=column,
        )
        for column in lcoe_percentage.columns
    ]

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),
        ncol=2,
        frameon=False,
        fontsize=8,
        title="LCOE (USD/MWh)",
    )

    fig.suptitle(
        f"{context.region_name_with_spaces} LCOE composition of "
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
        "LCOE composition plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )

def plot_grid_distance_composition(
    msrs: pd.DataFrame,
    config: AttributorCombinerConfig,
    context: RegionContext,
) -> None:
    """Plot region-level composition of MSR area by grid-distance class."""

    if config.re_technology == "wind":
        re_name = "Wind"
        colormap_name = "Greens"
    elif config.re_technology == "solarpv":
        re_name = "Solar PV"
        colormap_name = "Oranges"
    else:
        LOGGER.warning(
            f"Grid-distance composition plot skipped | "
            f"unsupported technology={config.re_technology}"
        )
        return

    output_path = (
        context.paths.output_folder_attributor_combiner
        / f"{config.re_technology}_grid_distance_composition.png"
    )

    msrs = msrs.copy()

    bins = [-np.inf, 10, 25, 50, 100, np.inf]
    labels = ["≤ 10", "10–25", "25–50", "50–100", "> 100"]

    msrs = msrs.copy()

    msrs["dist_bin"] = pd.cut(
        msrs["T_Dist_gf"],
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=True,
    )

    area_by_distance = (
        msrs.groupby(
            ["CtryName", "dist_bin"],
            observed=False,
        )["AreakM2"]
        .sum()
        .unstack(fill_value=0)
    )

    area_totals = area_by_distance.sum(axis=1).replace(0, np.nan)

    distance_percentage = (
        area_by_distance.div(area_totals, axis=0) * 100
    ).fillna(0)

    distance_percentage = distance_percentage.loc[
        :,
        distance_percentage.sum(axis=0) > 0,
    ]

    cmap = plt.get_cmap(colormap_name)

    color_map = {
        label: cmap(i / (len(labels) - 1))
        for i, label in enumerate(labels)
    }

    colors = [
        color_map[column]
        for column in distance_percentage.columns
    ]

    fig, ax = plt.subplots(
        figsize=(6, 3),
    )

    distance_percentage.plot(
        kind="barh",
        stacked=True,
        ax=ax,
        width=0.6,
        color=colors,
        legend=False,
    )

    ax.set_xlim(0, 100)
    ax.set_xlabel(f"Share of {re_name} MSR area (%)")
    ax.set_ylabel("")

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    handles = [
        Patch(
            facecolor=color_map[column],
            label=column,
        )
        for column in distance_percentage.columns
    ]

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),
        ncol=2,
        frameon=False,
        fontsize=8,
        title="Distance to transmission grid (km)",
    )

    fig.suptitle(
        f"{context.region_name_with_spaces} grid-distance composition of "
        f"{re_name} MSRs"
    )

    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Grid-distance composition plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )


def plot_cf_composition(
    msrs: pd.DataFrame,
    config: AttributorCombinerConfig,
    context: RegionContext,
) -> None:
    """Plot region-level composition of MSR area by capacity factor class."""

    if config.re_technology == "wind":
        re_name = "Wind"
        colormap_name = "Greens"
        cf_column_name = f"CF{config.wind_hub_height}m"
        bins = [-np.inf, 30, 35, 40, 45, 50, 55]
        labels = ["≤ 30", "30–35", "40–45", "45–50", "50–55", "> 55"]
    elif config.re_technology == "solarpv":
        re_name = "Solar PV"
        colormap_name = "Oranges"
        cf_column_name = "CF"
        bins = [-np.inf, 10, 14, 16, 18, 22, 100]
        labels = ["≤ 10", "10–14", "14–16", "16–18", "18–22", "> 22"]

    output_path = (
        context.paths.output_folder_attributor_combiner
        / f"{config.re_technology}_capacity_factor_composition.png"
    )

    msrs = msrs.copy()

    msrs["CF_bin"] = pd.cut(
        msrs[cf_column_name],
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=True,
    )

    area_by_distance = (
        msrs.groupby(
            ["CtryName", "CF_bin"],
            observed=False,
        )["AreakM2"]
        .sum()
        .unstack(fill_value=0)
    )

    area_totals = area_by_distance.sum(axis=1).replace(0, np.nan)

    cf_percentage = (
        area_by_distance.div(area_totals, axis=0) * 100
    ).fillna(0)

    cf_percentage = cf_percentage.loc[
        :,
        cf_percentage.sum(axis=0) > 0,
    ]

    cmap = plt.get_cmap(colormap_name)

    color_map = {
        label: cmap(i / (len(labels) - 1))
        for i, label in enumerate(labels)
    }

    colors = [
        color_map[column]
        for column in cf_percentage.columns
    ]

    fig, ax = plt.subplots(
        figsize=(6, 3),
    )

    cf_percentage.plot(
        kind="barh",
        stacked=True,
        ax=ax,
        width=0.6,
        color=colors,
        legend=False,
    )

    ax.set_xlim(0, 100)
    ax.set_xlabel(f"Share of {re_name} MSR area (%)")
    ax.set_ylabel("")

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    handles = [
        Patch(
            facecolor=color_map[column],
            label=column,
        )
        for column in cf_percentage.columns
    ]

    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),
        ncol=2,
        frameon=False,
        fontsize=8,
        title="Capacity Factor (%)",
    )

    fig.suptitle(
        f"{context.region_name_with_spaces} capacity factor composition of "
        f"{re_name} MSRs"
    )

    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Capacity factor composition plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )

def process_region(
    context: RegionContext, 
    config: AttributorCombinerConfig
) -> None:
    
    """Run enabled workflow stages for a single region."""
    
    LOGGER.info(
        f"Starting region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )
    add_attributes(context, config)

    LOGGER.info(
        f"Finished region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

def process_all_regions(
    config: AttributorCombinerConfig
) -> None:
    
    """Prepare shared region-boundary data and process configured regions.

    """
    
    LOGGER.info(
        f"Processing regions | count={len(config.regions)} "
        f"| technology={config.re_technology}"
    )

    for region_counter in range(0, len(config.regions)):
        region_name_with_spaces = config.regions.region[region_counter]
        context = prepare_region_context(region_name_with_spaces, config)
        process_region(context, config)


def main() -> None:
    """Load control inputs and run the Attributor Combiner workflow."""

    configure_logging()
    LOGGER.info("Attributor Combiner workflow started")
    try:
        control_file = Path(CONTROL_FILE_NAME)
        control = load_control_workbook(control_file)
        LOGGER.info(f"Control workbook loaded | path={control_file}")
        config = build_attributor_combiner_config(control_file, control)
        
        LOGGER.info(
                f"Configuration prepared | technologies={config.technologies_to_run} "
                f"| regions={len(config.regions)} | time_profile={config.time_profile} "
            )
        for tech in config.technologies_to_run:
            config.re_technology = tech

            process_all_regions(config)
    except Exception:
        LOGGER.exception("Attributor Combiner workflow failed")
        raise
    LOGGER.info("Attributor Combiner workflow completed")


if __name__ == "__main__":
    main()
