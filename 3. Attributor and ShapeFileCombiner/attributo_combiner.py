"""Combine and attribute country-level MSR shapefiles.

This script is part of the Model Supply Regions (MSR) workflow. It reads
country-level MSR shapefiles created by the MSR Creator, adds resource,
capacity-factor, yield, and cost attributes, and combines the results
into technology-level prescreen shapefiles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from matplotlib.style import context
import pandas as pd
from rasterstats import zonal_stats


CONTROL_FILE_NAME = "control_file_attributor_combiner.xlsx"
PATHS_SHEET = "paths"
CONFIGURATIONS_SHEET = "configurations"
PARAMETERS_SHEET = "parameters"
LOGGER = logging.getLogger(__name__)

@dataclass
class AttributorCombinerConfig:
    """Run-wide settings loaded from the Attributor Combiner control files."""

    control_file: Path
    control_paths: pd.DataFrame
    control_parameters: pd.DataFrame
    control_configurations: pd.DataFrame
    countries: pd.DataFrame
    technologies_to_run: list[str]
    re_technology: str
    time_profile: str
    hours_in_year: int
    days_in_year: int
    wind_hub_height: int
    reference_plant_capacity: int
    solar_pv_loss_adjustment: float
    wind_loss_adjustment: float


@dataclass
class CountryPaths:
    """Output paths for one country and technology."""

    output_folder: Path
    output_path: Path
    output_profile_generator: Path
    output_resource_raster: Path
    output_msr_creator: Path
@dataclass
class CountryContext:
    """Technology-specific names and paths used during attribution."""

    country_name_with_spaces: str
    country_name_without_spaces: str
    paths: CountryPaths
    stop_processing: bool = False

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

    countries = pd.read_csv(
        control_paths.loc["file_address_country_names_list"][0],
        names=["country"],
    )

    re_technology = ""
    technologies_to_run = []
    if bool(control_configurations.loc["run_solar_pv"][0]):
        technologies_to_run.append("solarpv")
    if bool(control_configurations.loc["run_solar_csp"][0]):
        technologies_to_run.append("solarcsp")
    if bool(control_configurations.loc["run_wind"][0]):
        technologies_to_run.append("wind")
    if bool(control_configurations.loc["run_offshore_wind"][0]):
        technologies_to_run.append("offshorewind")

    local_time_profile = bool(control_configurations.loc["local_time_profile"][0])
    if local_time_profile:
        time_profile = "local_time_profiles"
    else:
        time_profile = "utc_profiles"

    wind_hub_height = int(control_configurations.loc["wind_hub_height"][0])
    hours_in_year = int(control_configurations.loc["hours_in_year"][0])
    days_in_year = int(control_configurations.loc["days_in_year"][0])
    reference_plant_capacity = int(control_configurations.loc["reference_plant_capacity"][0])
    solar_pv_loss_adjustment = float(control_configurations.loc["solarpv_loss_adjustment"][0]) / 100
    wind_loss_adjustment = float(control_configurations.loc["wind_loss_adjustment"][0]) / 100

    return AttributorCombinerConfig(
        control_file=control_file,
        control_paths=control_paths,
        control_configurations=control_configurations,
        control_parameters=control_parameters,
        countries=countries,
        re_technology=re_technology,
        technologies_to_run=technologies_to_run,
        time_profile=time_profile,
        wind_hub_height=wind_hub_height,
        hours_in_year=hours_in_year,
        days_in_year=days_in_year,
        reference_plant_capacity=reference_plant_capacity,
        solar_pv_loss_adjustment=solar_pv_loss_adjustment,
        wind_loss_adjustment=wind_loss_adjustment,
    )

def prepare_country_context(
    country_name: str,
    config: AttributorCombinerConfig,
) -> CountryContext:
    """Prepare country-specific output folders."""

    country_name_without_spaces = country_name.replace(" ", "")

    output_subfolder_resource_raster = Path(
            Path(str(config.control_paths.loc["output_folder_msr_creator"][0]))
            / country_name_without_spaces
            / "stage1_input_datasets"
    )
    matches = list(output_subfolder_resource_raster.glob(f"{config.re_technology}_ECU*_projected.tif"))
    if not matches:
        raise FileNotFoundError(
            f"No projected resource raster found for {country_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_resource_raster}: {matches}"
        )
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple projected resource rasters found for {country_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_resource_raster}: {matches}"
        )
    output_resource_raster = matches[0]

    output_msr_creator = Path(
        Path(str(config.control_paths.loc["output_folder_msr_creator"][0]))
        / country_name_without_spaces
        / "stage4_attribution"
        / f"{config.re_technology}_final_msrs.shp"
    )
    output_subfolder_profile_generator = Path(
        Path(str(config.control_paths.loc["output_folder_profile_generator"][0]))
        / country_name_without_spaces
        / config.time_profile
    )
    matches = list(output_subfolder_profile_generator.glob(f"{config.re_technology}*CF_profiles.csv"))
    if not matches:
        raise FileNotFoundError(
            f"No CF profile found for {country_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_profile_generator}"
        )
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple CF profiles found for {country_name_without_spaces} "
            f"and technology {config.re_technology} in {output_subfolder_profile_generator}"
        )
    output_profile_generator = matches[0]

    output_folder = Path(
        Path(str(config.control_paths.loc["output_folder_attributor_combiner"][0]))
        / country_name_without_spaces
    )

    paths = CountryPaths(
        output_folder=output_folder,
        output_path=output_folder / f"{config.re_technology}_prescreen.shp",
        output_profile_generator=output_profile_generator,
        output_resource_raster=output_resource_raster,
        output_msr_creator=output_msr_creator,
    )
    output_folder.mkdir(parents=True, exist_ok=True)

    LOGGER.info(
        f"Prepared country context | country={country_name_without_spaces} " 
        f"| output={output_folder}"
    )
    return CountryContext(
        country_name_with_spaces=country_name,
        country_name_without_spaces=country_name_without_spaces,
        paths=paths,
    )

def add_attributes(
    context: CountryContext,
    config: AttributorCombinerConfig,
) -> gpd.GeoDataFrame:
    
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
    msrs = gpd.read_file(paths.output_msr_creator)
    msrs = msrs.sort_values(by=["FID"])
    msrs["CtryName"] = context.country_name_without_spaces


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
            f"Resource attributes inserted | country={context.country_name_without_spaces} "
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
            f"Cost attributes inserted | country={context.country_name_without_spaces} "
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
            f"Resource attributes inserted | country={context.country_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

        # Add cost attributes by IEC class
        iec_classes = ["Class-3", "Class-2", "Class-1"]
        rows = ["Wind_Class3", "Wind_Class2", "Wind_Class1"]

        for iec_class, row in zip(iec_classes, rows):
            class_filter = msrs["IEC_Class"] == iec_class
            msrs.loc[class_filter] = calculate_cost_attributes(
                                            msrs.loc[class_filter].copy(),
                                            config,
                                            row=row,
                                            cf_column_name=cf_col_name
            )
        LOGGER.info(
            f"Cost attributes inserted | country={context.country_name_without_spaces} "
            f"| technology={config.re_technology}"
        )

    # TODO: Add offshore wind and solar CSP attributes


    msrs.to_file(paths.output_path)

    LOGGER.info(
            f"Attribution complete | country={context.country_name_without_spaces} "
            f"| technology={config.re_technology} | path={paths.output_path}"
        )

    return msrs


def calculate_cost_attributes(
    msrs: gpd.GeoDataFrame,
    config: AttributorCombinerConfig,
    row: str,
    cf_column_name: str
) -> gpd.GeoDataFrame:

    gen_cap_cost = float(config.control_parameters.loc[row].iloc[0])           # Generation capital cost ($/kW)
    gen_fixed_om = float(config.control_parameters.loc[row].iloc[1])           # Generation fixed O&M cost ($/kW/yr)
    gen_var_om = float(config.control_parameters.loc[row].iloc[2])             # Generation variable O&M cost ($/MWh)
    gen_cap_rec = float(config.control_parameters.loc[row].iloc[3])            # Generation capital recovery factor

    trans_cap_cost = float(config.control_parameters.loc[row].iloc[5])         # Transmission capital cost ($/MW/km)
    trans_fixed_om = float(config.control_parameters.loc[row].iloc[6])         # Transmission fixed O&M cost ($/km)
    grid_cap_rec = float(config.control_parameters.loc[row].iloc[8])           # Grid capital recovery factor
    substation_cap_cost = float(config.control_parameters.loc[row].iloc[7])    # Substation capital cost ($)

    road_cap_cost = float(config.control_parameters.loc[row].iloc[10])         # Road capital cost ($/km)
    road_fixed_om = float(config.control_parameters.loc[row].iloc[11])         # Road fixed O&M cost ($/km)
    road_cap_rec = float(config.control_parameters.loc[row].iloc[12])          # Road capital recovery factor

    msrs["sLCOE-MWh"] = (
        1000
        * (
            (
                gen_cap_cost
                * gen_cap_rec
                + gen_fixed_om / 1000    
            )
            / (config.hours_in_year * msrs[cf_column_name] / 100)
        )
        + gen_var_om
    )

    msrs["tLCOE-MWh"] = (
        (
            (
                trans_cap_cost
                * grid_cap_rec
                + trans_fixed_om
                )
                * msrs["T_Dist_gf"]
                + substation_cap_cost
                * grid_cap_rec
        ) 
        / (config.hours_in_year * msrs[cf_column_name] / 100)
    )

    msrs["tCAPEX-kW"] = (
        trans_cap_cost
        * msrs["T_Dist_gf"] 
        + substation_cap_cost
    ) / 1000

    msrs["rLCOE-MWh"] = (
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
    
    msrs["rCAPEX-kW"] = (
        road_cap_cost
        * msrs["RoadDist"]
    ) / (config.reference_plant_capacity * 1000)
    
    msrs["LCOE-MWh"] = (
        msrs["tLCOE-MWh"] + msrs["rLCOE-MWh"] + msrs["sLCOE-MWh"]
    )
    
    msrs["trCAPEX-kW"] = msrs["rCAPEX-kW"] + msrs["tCAPEX-kW"]

    return msrs

def process_country(
    context: CountryContext, 
    config: AttributorCombinerConfig
) -> None:
    
    """Run enabled workflow stages for a single country."""
    
    LOGGER.info(
        f"Starting country workflow | country={context.country_name_without_spaces} "
        f"| technology={config.re_technology}"
    )
    msrs = add_attributes(context, config)

    LOGGER.info(
        f"Finished country workflow | country={context.country_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

def process_all_countries(
    config: AttributorCombinerConfig
) -> None:
    
    """Prepare shared country-boundary data and process configured countries.

    """
    
    LOGGER.info(
        f"Processing countries | count={len(config.countries)} "
        f"| technology={config.re_technology}"
    )

    for country_counter in range(0, len(config.countries)):
        country_name_with_spaces = config.countries.country[country_counter]
        context = prepare_country_context(country_name_with_spaces, config)
        process_country(context, config)

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
                f"| countries={len(config.countries)} | time_profile={config.time_profile} "
            )
        for tech in config.technologies_to_run:
            config.re_technology = tech

            process_all_countries(config)
    except Exception:
        LOGGER.exception("Attributor Combiner workflow failed")
        raise
    LOGGER.info("Attributor Combiner workflow completed")


if __name__ == "__main__":
    main()
