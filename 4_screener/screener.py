"""Screen MSRs.

This script is part of the Model Supply Rgions (MSR) workflow. It screens
MSRs based on user-defined criteria and combines the results into technology-level
screened shapefiles and Excel files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt

CONTROL_FILE_NAME = "control_file_screener.xlsx"
PATHS_SHEET = "paths"
CONFIGURATIONS_SHEET = "configurations"
DATASETS_SHEET = "datasets"
PARAMETERS_SHEET = "parameters"

LOGGER = logging.getLogger(__name__)

@dataclass
class ScreenerConfig:
    """Run-wide settings loaded from the Screener control files."""

    control_file: Path
    control_paths: pd.DataFrame
    control_configurations: pd.DataFrame
    regions: pd.DataFrame
    file_name_region_boundaries: str
    output_folder: Path
    technologies_to_run: list[str]
    re_technology: str
    time_profile: str
    area_cutoff_perc: dict[str, pd.DataFrame]
    add_cf_profiles: bool
    elevation_threshold: int

@dataclass
class RegionPaths:
    """Output paths for one region and technology."""

    output_folder_screener: Path
    output_shp_path: Path
    output_csv_path: Path
    output_profile_generator: Path
    output_attributor_combiner: Path

@dataclass
class RegionContext:
    """Technology-specific names and paths used during attribution."""

    region_name_with_spaces: str
    region_name_without_spaces: str
    region_area_cutoff_perc: dict[str, float]
    paths: RegionPaths

def configure_logging(level: int = logging.INFO) -> None:
    """Configure terminal logging for MSR workflow progress."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

def load_control_workbook(control_file: Path) -> dict[str, pd.DataFrame]:
    """Read the Screener control workbook.

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

def build_screener_config(
    control_file: Path,
    control: dict[str, pd.DataFrame],
) -> ScreenerConfig:
    """Build run-wide Screener configuration from control DataFrames.

    Returns:
        ScreenerConfig: Run-wide settings used by all regions and stages.
    """

    control_paths = control["control_paths"]
    control_parameters = control["control_parameters"]
    control_configurations = control["control_configurations"]
    control_datasets = control["control_datasets"]

    output_folder = Path(str(control_paths.loc["output_folder"][0]))
    file_name_region_boundaries = (
        Path(Path(str(control_paths.loc["input_folder_datasets"][0]))) /
        str(control_datasets.loc["region_boundaries"][0])
    )
    regions = pd.read_csv(
        Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
        str(control_datasets.loc["regions"][0])),
        names=["region"],
    )

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

    area_cutoff_perc = {}
    for tech in technologies_to_run:
        area_cutoff_perc[tech] = pd.read_csv(
            Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
            str(control_datasets.loc[f"{tech}_region_area_cutoff_perc"][0])), 
            names=["region", "area_cutoff_perc"],
            sep=";"
        )

    elevation_threshold = int(control_parameters.loc["elevation_threshold"][0])
    add_cf_profiles = bool(control_configurations.loc["add_cf_profiles"][0])
    local_time_profile = bool(control_configurations.loc["local_time_profile"][0])
    if local_time_profile:
        time_profile = "local_time_profiles"
    else:
        time_profile = "utc_profiles"


    return ScreenerConfig(
        control_file=control_file,
        control_paths=control_paths,
        control_configurations=control_configurations,
        file_name_region_boundaries=file_name_region_boundaries,
        regions=regions,
        output_folder=output_folder,
        re_technology=re_technology,
        technologies_to_run=technologies_to_run,
        area_cutoff_perc=area_cutoff_perc,
        add_cf_profiles=add_cf_profiles,
        time_profile=time_profile,
        elevation_threshold=elevation_threshold,
    )

def prepare_region_context(
    region_name: str,
    config: ScreenerConfig,
) -> RegionContext:
    """Prepare region-specific output folders."""

    region_name_without_spaces = region_name.replace(" ", "")

    
    tech_area_cutoff_perc = config.area_cutoff_perc[config.re_technology]
    region_area_cutoff_perc = float(
        tech_area_cutoff_perc[tech_area_cutoff_perc["region"] == region_name]
        .iloc[0]["area_cutoff_perc"]
        )
    
    output_folder_profile_generator = Path(
        Path(str(config.output_folder))
        / "2_profile_generator"
        / region_name_without_spaces
        / config.time_profile
    )
    matches = list(output_folder_profile_generator.glob(f"{config.re_technology}*CF_profiles.csv"))
    if not matches:
        raise FileNotFoundError(
            f"No CF profile found for {region_name} "
            f"and technology {config.re_technology} in {output_folder_profile_generator}"
        )
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple CF profiles found for {region_name} "
            f"and technology {config.re_technology} in {output_folder_profile_generator}"
        )
    output_profile_generator = matches[0]

    output_folder_attributor_combiner = Path(
        Path(str(config.output_folder))
        / "3_attributor_combiner"
        / region_name_without_spaces
    )


    if config.re_technology == "solarpv":
        matches = list(output_folder_attributor_combiner.glob(f"{config.re_technology}_prescreen.shp"))
    elif config.re_technology == "wind":
        matches = list(output_folder_attributor_combiner.glob(f"{config.re_technology}_{config.elevation_threshold}_prescreen.shp"))
    if not matches:
        raise FileNotFoundError(
            f"No msr shapefile found for {region_name_without_spaces} "
            f"and technology {config.re_technology} in {output_folder_attributor_combiner}"
        )
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple msr shapefiles found for {region_name_without_spaces} "
            f"and technology {config.re_technology} in {output_folder_attributor_combiner}"
        )
    output_attributor_combiner = matches[0]

    output_folder_screener = Path(
        Path(str(config.output_folder))
        / "4_screener"
        / region_name_without_spaces
    )

    paths = RegionPaths(
        output_folder_screener=output_folder_screener,
        output_shp_path=output_folder_screener / f"{config.re_technology}_{region_area_cutoff_perc}%_screened_msrs.shp",
        output_csv_path=output_folder_screener / f"{config.re_technology}_{region_area_cutoff_perc}%_screened_msrs.csv",
        output_profile_generator=output_profile_generator,
        output_attributor_combiner=output_attributor_combiner,
    )
    output_folder_screener.mkdir(parents=True, exist_ok=True)
    LOGGER.info(
        f"Prepared region context | region={region_name_without_spaces} " 
        f"| output={output_folder_screener}"
    )
    return RegionContext(
        region_name_with_spaces=region_name,
        region_name_without_spaces=region_name_without_spaces,
        region_area_cutoff_perc=region_area_cutoff_perc,
        paths=paths,
    )

def calculate_cutoff(
    context: RegionContext,
    config: ScreenerConfig
):
    gdf_region_boundaries = gpd.read_file(config.file_name_region_boundaries)
    region_area_km2 = gdf_region_boundaries[gdf_region_boundaries.name == context.region_name_with_spaces].to_crs("ESRI:54009").area.iloc[0] / 1000000
    cutoff = region_area_km2 * context.region_area_cutoff_perc / 100

    return cutoff

def screen_lcoe(
    context: RegionContext,
    config: ScreenerConfig,
    cutoff: float
) -> None:
    
    paths = context.paths
    msrs = gpd.read_file(paths.output_attributor_combiner)
    msrs = msrs.rename(columns={"FID": "MSR_ID"})
    msrs = msrs.sort_values(by=["LCOE-MWh"], ascending=True, ignore_index=True)
    

    msrs["CumAreakM2"] = msrs.AreakM2.cumsum()
    msrs = msrs[msrs["CumAreakM2"] <= cutoff]
    msrs = msrs.drop(columns=["CumAreakM2"])

    msrs.to_file(paths.output_shp_path)
    export_csv(context, config, msrs)
    plot_lcoe_cf(msrs, context, config)

    LOGGER.info(
        f"Screening complete | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology} | path_shp={paths.output_shp_path} |"
        f"| path_csv={paths.output_csv_path}"
    )


def export_csv(
    context: RegionContext,
    config: ScreenerConfig,
    msrs: gpd.GeoDataFrame
) -> None:

    paths = context.paths
    if not config.add_cf_profiles:
        if "geometry" in msrs.columns:
            msrs = msrs.drop(columns=["geometry"])
            msrs.to_csv(paths.output_csv_path, index=False, sep=";")
        return
    
    profiles = pd.read_csv(paths.output_profile_generator)
    profile_columns = [col for col in profiles.columns if col.startswith("H")]
    profiles = profiles[["MSR_ID"] + ["Longitude"] + ["Latitude"] + profile_columns]

    output = msrs.merge(profiles, on="MSR_ID", how="left")
    if "geometry" in output.columns:
        output = output.drop(columns=["geometry"])
    other_columns = [col for col in output.columns if col not in ["MSR_ID", "Longitude", "Latitude"]]
    output = output[["MSR_ID"] + ["Longitude"] + ["Latitude"] + other_columns]
    output.to_csv(paths.output_csv_path, index=False, sep=";")


def plot_lcoe_cf(
    msrs: gpd.GeoDataFrame,
    context: RegionContext,
    config: ScreenerConfig,
) -> None:
    """Plot LCOE vs capacity factor.
    """


    if config.re_technology == "solarpv":
        cmap = 'Oranges_r'
        cf = 'CF'
        re_name = "Solar PV"
    elif config.re_technology == "wind":
        cmap = 'Greens_r'
        cf = 'CF100m'
        re_name = "Wind"

    output_path = (
        context.paths.output_folder_screener
        / f"{config.re_technology}_lcoe_vs_cf.png"
    )

    fig, ax = plt.subplots(
        figsize=(6, 3)
    )

    ax.scatter(
        msrs[cf], 
        msrs['LCOE-MWh'], 
        c=msrs['LCOE-MWh'], 
        cmap=cmap, 
        alpha=0.5,
    )
    ax.set_title(f'{context.region_name_with_spaces} LCOE versus CF for {re_name} MSRs')
    ax.set_xlabel(f'Average {re_name} capacity factor (%)')
    ax.set_ylabel('LCOE (USD/MWh)')
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout()
    fig.savefig(output_path, dpi=300)
    plt.close(fig)

    LOGGER.info(
        f"LCOE vs CF plot written | "
        f"region={context.region_name_with_spaces} |"
        f"technology={config.re_technology} | path={output_path}"
    )


def process_region(
    context: RegionContext, 
    config: ScreenerConfig
) -> None:
    
    """Run enabled workflow stages for a single region."""
    
    LOGGER.info(
        f"Starting region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology} | area_cutoff_perc={context.region_area_cutoff_perc}"
    )
    
    cutoff = calculate_cutoff(context, config)
    screen_lcoe(context, config, cutoff)

    LOGGER.info(
        f"Finished region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

def process_all_regions(
    config: ScreenerConfig
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
    """Load control inputs and run the Screener workflow."""

    configure_logging()
    LOGGER.info("Screener workflow started")
    try:
        control_file = Path(CONTROL_FILE_NAME)
        control = load_control_workbook(control_file)
        LOGGER.info(f"Control workbook loaded | path={control_file}")
        config = build_screener_config(control_file, control)
        
        LOGGER.info(
                f"Configuration prepared | technologies={config.technologies_to_run} "
                f"| regions={len(config.regions)} | time_profile={config.time_profile} "
            )
        for tech in config.technologies_to_run:
            config.re_technology = tech

            process_all_regions(config)
    except Exception:
        LOGGER.exception("Screener workflow failed")
        raise
    LOGGER.info("Attributor Combiner workflow completed")


if __name__ == "__main__":
    main()