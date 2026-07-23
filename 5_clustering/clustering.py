"""Cluster screened MSR profiles.

This script is part of the Model Supply Regions (MSR) workflow. It reads
screened MSR profile CSV files, clusters MSRs by their hourly capacity-factor
profiles, aggregates attributes and profiles to cluster level, and writes
clustered and unclustered outputs per country.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import re

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tslearn.clustering import TimeSeriesKMeans


CONTROL_FILE_NAME = "control_file_clustering.xlsx"
PATHS_SHEET = "paths"
CONFIGURATIONS_SHEET = "configurations"
PARAMETERS_SHEET = "parameters"
DATASETS_SHEET = "datasets"

LOGGER = logging.getLogger(__name__)


@dataclass
class ClusterConfig:
    """Run-wide settings loaded from the Clustering control files."""

    control_file: Path
    control_paths: pd.DataFrame
    region_clusters: pd.DataFrame
    area_cutoff_perc: float
    technologies_to_run: list[str]
    re_technology: str
    metric: str
    iterations: int
    attribute_aggregation: pd.DataFrame

@dataclass
class RegionPaths:
    """Output paths for one region and technology."""

    output_folder: Path
    output_clustered_csv: Path
    output_unclustered_csv: Path
    output_screener: Path
    output_plot: Path


@dataclass
class RegionContext:
    """Region-specific settings and paths used during clustering."""

    region_name_with_spaces: str
    region_name_without_spaces: str
    n_clusters: int
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


def build_cluster_config(
    control_file: Path, 
    control: dict[str, pd.DataFrame]
) -> ClusterConfig:
    """Build run-wide clustering configuration from the control DataFrames.

    Returns:
        ClusterConfig: Run-wide settings used by all countries.
    """

    control_paths = control["control_paths"]
    control_parameters = control["control_parameters"]
    control_configurations = control["control_configurations"]
    control_datasets = control["control_datasets"]

    region_clusters = pd.read_csv(
        Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
        str(control_datasets.loc["file_name_region_clusters"][0])),
        names=["region", "n_clusters"],
        sep=";"
    )

    attribute_aggregation = pd.read_csv(
        Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
        str(control_datasets.loc["file_name_attribute_aggregation"][0])),
        names=["re_technology", "param", "mode"],
        sep=";"
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

    area_cutoff_perc = float(control_parameters.loc["area_cutoff_perc"][0])
    iterations = int(control_parameters.loc["iterations"][0])
    metric = str(control_parameters.loc["metric"][0])

    return ClusterConfig(
        control_file=control_file,
        control_paths=control_paths,
        region_clusters=region_clusters,
        attribute_aggregation=attribute_aggregation,
        re_technology=re_technology,
        technologies_to_run=technologies_to_run,
        area_cutoff_perc=area_cutoff_perc,
        iterations=iterations,
        metric=metric,
    )



def prepare_region_context(
    region_name_with_spaces: str,
    config: ClusterConfig,
) -> RegionContext:
    
    """Prepare region-specific output folders and file paths."""

    region_name_without_spaces = region_name_with_spaces.replace(" ", "")

    n_clusters = int(
        config.region_clusters.loc[
            config.region_clusters["region"] == region_name_without_spaces, 
            "n_clusters"
            ].iloc[0]
    )

    output_subfolder_screener = Path(
        Path(str(config.control_paths.loc["output_folder_screener"][0]))
        / region_name_without_spaces
    )

    matches = list(output_subfolder_screener.glob(f"{config.re_technology}_{config.area_cutoff_perc}%_screened_msrs.csv"))
    if not matches:
        raise FileNotFoundError(
            f"No screened MSR file found for region={region_name_with_spaces} "
            f"and technology={config.re_technology} in {output_subfolder_screener}"
        )
    if len(matches) > 1:
        raise FileExistsError(
            f"Multiple screened MSR files found for region={region_name_with_spaces} "
            f"and technology={config.re_technology} in {output_subfolder_screener}"
        )
    output_screener = matches[0]

    output_folder = Path(
        Path(str(config.control_paths.loc["output_folder"][0]))
        / region_name_with_spaces
    )
    output_folder.mkdir(parents=True, exist_ok=True)

    paths = RegionPaths(
        output_folder=output_folder,
        output_clustered_csv=output_folder / f"{config.re_technology}_{n_clusters}_clustered_msrs.csv",
        output_unclustered_csv=output_folder / f"{config.re_technology}_unclustered_msrs.csv",
        output_plot=output_folder / f"{config.re_technology}_{n_clusters}_clustered.png",
        output_screener=output_screener,
    )

    LOGGER.info(
        f"Prepared region context | region={region_name_with_spaces} "
        f"| clusters={n_clusters} | output={output_folder}"
    )

    return RegionContext(
        region_name_with_spaces=region_name_with_spaces,
        region_name_without_spaces=region_name_without_spaces,
        n_clusters=n_clusters,
        paths=paths,
    )

def split_attributes_and_profiles(
    context: RegionContext,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate non-hourly attributes from hourly CF profiles."""

    screener = pd.read_csv(context.paths.output_screener, sep=";")
    hour_columns = [col for col in screener.columns if col.startswith("H") and col[1:].isdigit()]
    profiles = screener[hour_columns].astype(float)
    attributes = screener.drop(columns=hour_columns).copy()

    return attributes, profiles


def cluster_msrs(
    profiles: pd.DataFrame,
    context: RegionContext,
    config: ClusterConfig,
) -> np.ndarray:
    """Cluster MSRs based on their hourly profile time series using k-means algorithm."""

    if len(profiles) < context.n_clusters:
        raise ValueError(
            f"Region {context.region_name_without_spaces} has {len(profiles)} MSRs, "
            f"but {context.n_clusters} clusters were requested."
        )

    model = TimeSeriesKMeans(
        n_clusters=context.n_clusters,
        metric=config.metric,
        max_iter=config.iterations,
        verbose=1,
    )
    labels = model.fit_predict(profiles)

    LOGGER.info(
        f"Clustering complete | region={context.region_name_with_spaces} "
        f"| clusters={context.n_clusters} | metric={config.metric}"
    )

    return labels


def aggregate_attributes(
    config: ClusterConfig,
    context: RegionContext,
    attributes: pd.DataFrame,
    profiles: pd.DataFrame,
    labels: np.ndarray,
) -> None:
    """Aggregate non-hourly MSR attributes to cluster level."""

    re_attribute_aggregation = config.attribute_aggregation[
        (config.attribute_aggregation["re_technology"] == "all")
        | (config.attribute_aggregation["re_technology"] == config.re_technology)
    ]
    
    sum_attr = re_attribute_aggregation.loc[
        re_attribute_aggregation["mode"] == "sum", "param"
        ].tolist()
    max_attr = re_attribute_aggregation.loc[
        re_attribute_aggregation["mode"] == "max", "param"
        ].tolist()
    min_attr = re_attribute_aggregation.loc[
        re_attribute_aggregation["mode"] == "min", "param"
        ].tolist()
    mode_attr = re_attribute_aggregation.loc[
        re_attribute_aggregation["mode"] == "mode", "param"
        ].tolist()
    list_attr = re_attribute_aggregation.loc[
        re_attribute_aggregation["mode"] == "list", "param"
        ].tolist()
    wmean_attr = re_attribute_aggregation.loc[
        re_attribute_aggregation["mode"] == "wmean", "param"
        ].tolist()
    wmean_iec_attr = re_attribute_aggregation.loc[
        re_attribute_aggregation["mode"] == "wmean_IEC", "param"
        ].tolist()
    
    attributes = attributes.copy()
    attributes["Cluster"] = labels

    clusters_attr = pd.DataFrame(index=range(context.n_clusters))

    clusters_attr["MSRCnt"] = attributes.groupby("Cluster")["MSR_ID"].count()

    for attr in sum_attr:
        clusters_attr[attr] = attributes.groupby("Cluster")[attr].sum()
    for attr in max_attr:
        clusters_attr[attr] = attributes.groupby("Cluster")[attr].max()
    for attr in min_attr:
        clusters_attr[attr] = attributes.groupby("Cluster")[attr].min()
    for attr in mode_attr:
        clusters_attr[attr] = attributes.groupby("Cluster")[attr].agg(lambda x: x.mode().iloc[0])
    for attr in list_attr:
        clusters_attr[attr] = attributes.groupby("Cluster")[attr].agg(lambda x: list(x))
    
    capacity = pd.to_numeric(attributes["CapacityMW"])
    cluster_capacity = capacity.groupby(attributes["Cluster"]).sum()

    attributes["Weights"] = 0.0
    for c in range(context.n_clusters):

        attributes.loc[attributes["Cluster"] == c, "Weights"] = (
            capacity.loc[attributes["Cluster"] == c] 
            / cluster_capacity.loc[c]
        )

    for attr in wmean_attr:
        clusters_attr[attr] = (
            attributes[attr]
            .multiply(attributes["Weights"], axis=0)
            .groupby(attributes["Cluster"])
            .sum()
        )

    for attr in wmean_iec_attr:
        w_iec_attr = (
            attributes[attr].str.replace(r"\D", "").astype(float)
            .multiply(attributes["Weights"], axis=0)
            .groupby(attributes["Cluster"])
            .sum()
        )
        clusters_attr[attr] = "Class-" + w_iec_attr.round().astype(int).astype(str)

    
    profiles = profiles.copy()
    
    clusters_prof = pd.DataFrame(index=range(context.n_clusters))
    wprofiles = profiles.multiply(attributes["Weights"], axis=0)
    wprofiles["Cluster"] = labels
    clusters_prof = wprofiles.groupby("Cluster").sum()

    LOGGER.info(
        f"Attributes and profiles aggregated | region={context.region_name_with_spaces} |"
        f"technology={config.re_technology} | clusters={context.n_clusters}"
    )

    plot_cluster_profiles(profiles, clusters_prof, labels, context)

    clustered_msrs = pd.concat([clusters_attr, clusters_prof], axis=1)
    unclustered_msrs = pd.concat([attributes, profiles], axis=1)

    clustered_msrs.to_csv(context.paths.output_clustered_csv, sep=";", index=False)
    unclustered_msrs.to_csv(context.paths.output_unclustered_csv, sep=";", index=False)

    LOGGER.info(
        f"Clustered and unclustered CSV files created | region={context.region_name_with_spaces} "
        f"| technology={config.re_technology} | clusters={context.n_clusters} "
        f"| clustered_path={context.paths.output_clustered_csv} "
        f"| unclustered_path={context.paths.output_unclustered_csv}"
    ) 
    

def plot_cluster_profiles(
    profiles: pd.DataFrame,
    clusters_prof: pd.DataFrame,
    labels: np.ndarray,
    context: RegionContext,
) -> None:
    """Plot four 72-hour windows of individual and clustered profiles."""

    frac = [0, 1 / 4, 2 / 4, 3 / 4]
    hours_in_year = len(profiles.columns)
    plot_hrs = [int(hours_in_year * i) for i in frac]

    fig, axes = plt.subplots(
        len(frac), 
        context.n_clusters, 
        figsize=(40, 25), 
        sharex=False, 
        sharey=True)
    
    if context.n_clusters == 1:
        axes = np.array(axes).reshape(4, 1)

    profile_values = profiles.reset_index(drop=True)

    for label in set(labels):
        col_idx = int(label)
        cluster_filter = labels == label
        cluster_members = profile_values.loc[cluster_filter]

        for row_idx, start in enumerate(plot_hrs):
            end = min(start + 72, hours_in_year)
            x_values = range(start + 1, end + 1)

            for _, profile in cluster_members.iterrows():
                axes[row_idx, col_idx].plot(
                    x_values,
                    profile.iloc[start:end]*100,
                    c="gray",
                    alpha=0.4,
                )

            axes[row_idx, col_idx].plot(
                x_values,
                clusters_prof.loc[label].iloc[start:end]*100,
                c="red",
                linewidth=2
            )
            axes[row_idx, col_idx].set_title(f"Cluster {label + 1}")
            axes[row_idx, col_idx].set_ylim(0, 100)
            axes[row_idx, col_idx].set_ylabel("Capacity Factor (%)")

    fig.suptitle(f"Clusters")
    fig.tight_layout()
    fig.savefig(context.paths.output_plot)
    plt.close(fig)

    LOGGER.info(
        f"Cluster profiles figure created | country={context.region_name_with_spaces} "
        f"| path={context.paths.output_plot}"
    )

def process_region(
        context: RegionContext,
        config: ClusterConfig,
) -> None:
    """Run enabled workflow stages for a single region."""

    LOGGER.info(
        f"starting region workflow | region={context.region_name_with_spaces} "
        f"| technology={config.re_technology} | clusters={context.n_clusters}"
    )

    attributes, profiles = split_attributes_and_profiles(context)
    labels = cluster_msrs(profiles, context, config)
    aggregate_attributes(config, context, attributes, profiles, labels)



    LOGGER.info(
        f"Finished region workflow | region={context.region_name_with_spaces} "
        f"| technology={config.re_technology}"
    )

def process_all_regions(
    config: ClusterConfig
) -> None:
    
    """Prepare shared region-boundary data and process configured regions.
    """
    
    LOGGER.info(
        f"Processing regions | count={len(config.region_clusters)} "
        f"| technology={config.re_technology}"
    )

    for region_counter in range(0, len(config.region_clusters)):
        region_name_with_spaces = config.region_clusters.region[region_counter]
        context = prepare_region_context(region_name_with_spaces, config)
        process_region(context, config)


def main() -> None:
    """Load control inputs and run the clustering workflow."""

    configure_logging()
    LOGGER.info("Clustering workflow started")

    try:
        control_file = Path(CONTROL_FILE_NAME)
        control = load_control_workbook(control_file)
        LOGGER.info(f"Control file loaded | path={control_file}")

        config = build_cluster_config(control_file, control)
        LOGGER.info(
            f"Configuration prepared | technology={config.technologies_to_run} "
            f"| regions={len(config.region_clusters)} | metric={config.metric} "
            f"| iterations={config.iterations}"
        )

        for tech in config.technologies_to_run:
            config.re_technology = tech

            process_all_regions(config)

    except Exception:
        LOGGER.exception("Clustering workflow failed")
        raise

    LOGGER.info("Clustering workflow completed")


if __name__ == "__main__":
    main()
