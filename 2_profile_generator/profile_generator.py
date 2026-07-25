"""Generator renewable profiles for MSRs.

"""

import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from rasterstats import zonal_stats

CONTROL_FILE_NAME = "control_file_profile_generator.xlsx"
PATHS_SHEET = "paths"
CONFIGURATIONS_SHEET = "configurations"
PARAMETERS_SHEET = "parameters"
DATASETS_SHEET = "datasets"

LOGGER = logging.getLogger(__name__)

@dataclass
class ProfileGeneratorConfig:
    """Run-wide settings loaded from the Profile Generator control files."""

    control_file: Path
    control_parameters: pd.DataFrame
    control_configurations: pd.DataFrame
    control_datasets: pd.DataFrame
    input_folder_datasets: Path
    output_folder: Path
    regions: pd.DataFrame
    technologies_to_run: list[str]
    re_technology: str
    # produce_diagnostics: bool
    wind_hub_height: int
    reference_year: int
    weather_years: list[int]
    file_name_UTC_offsets: str
    file_name_era5_wind_10m: str
    file_name_era5_wind_100m: str
    file_name_era5_temperature_2m: str
    file_name_era5_geopotential: str
    file_name_era5_ssrd: str
    file_name_IEC_power_curves: str
    file_name_resource_raster: str

@dataclass
class RegionPaths:
    """Output paths for one region and technology."""

    output_folder_profile_generator_utc: Path
    output_folder_profile_generator_lt: Path
    output_path_utc: Path
    output_path_lt: Path
    output_folder_profile_generator_diagnostics: Path
    output_msr_creator: Path
    output_resource_raster: Path

@dataclass
class RegionContext:
    """Region-specific names and paths used during profile generation."""

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
    """Read the Profile Generator control workbook.

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

def build_profile_generator_config(
    control_file: Path,
    control: dict[str, pd.DataFrame],
) -> ProfileGeneratorConfig:
    """Build run-wide Profile Generator configuration from control DataFrames.

    Returns:
        ProfileGeneratorConfig: Run-wide settings used by all regions and technologies.
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

    # produce_diagnosics = bool(control_configurations.loc["produce_diagnostics"][0])

    # datasets
    regions = pd.read_csv(
        Path(Path(str(control_paths.loc["input_folder_datasets"][0])) /
        f"{control_datasets.loc['file_name_regions'][0]}.csv"),
        names=["region"],
        sep=";"
    )

    file_name_UTC_offsets = str(control_datasets.loc["file_name_region_utc_offset"][0])
    file_name_IEC_power_curves = str(control_datasets.loc["file_name_IEC_power_curves"][0])
    file_name_era5_wind_10m = str(control_datasets.loc["10m_component_of_wind"][0])
    file_name_era5_wind_100m = str(control_datasets.loc["100m_component_of_wind"][0])
    file_name_era5_temperature_2m = str(control_datasets.loc["2m_temperature"][0])
    file_name_era5_geopotential = str(control_datasets.loc["geopotential"][0])
    file_name_era5_ssrd = str(control_datasets.loc["surface_solar_radiation_downwards"][0])
    if bool(control_configurations.loc["run_code_for_solar_pv"][0]):
        file_name_resource_raster = str(control_datasets.loc["file_name_resource_raster_solarpv"][0])
    if bool(control_configurations.loc["run_code_for_wind"][0]):
        file_name_resource_raster = str(control_datasets.loc["file_name_resource_raster_wind"][0])

    # parameters
    wind_hub_height = int(control_parameters.loc["wind_hub_height"][0])
    reference_year = int(control_parameters.loc["reference_year"][0])
    weather_years = [int(year) 
                     for year in str(
                         control_parameters.loc["weather_years"][0]).split(";")
                    ]

    if reference_year not in weather_years:
        raise ValueError(
            f"Reference year {reference_year} is not included in the list of weather years: {weather_years}"
        )
    weather_years = [reference_year] + [year for year in weather_years if year != reference_year]

    return ProfileGeneratorConfig(
        control_file=control_file,
        control_parameters=control_parameters,
        control_configurations=control_configurations,
        control_datasets=control_datasets,
        input_folder_datasets=input_folder_datasets,
        output_folder=output_folder,
        re_technology=re_technology,
        technologies_to_run=technologies_to_run,
        regions=regions,
        file_name_UTC_offsets=file_name_UTC_offsets,
        file_name_IEC_power_curves=file_name_IEC_power_curves,
        file_name_era5_wind_10m=file_name_era5_wind_10m,
        file_name_era5_wind_100m=file_name_era5_wind_100m,
        file_name_era5_temperature_2m=file_name_era5_temperature_2m,
        file_name_era5_geopotential=file_name_era5_geopotential,
        file_name_era5_ssrd=file_name_era5_ssrd,
        file_name_resource_raster=file_name_resource_raster,
        wind_hub_height=wind_hub_height,
        reference_year=reference_year,
        weather_years=weather_years
    )

def prepare_region_context(
    region_name: str,
    config: ProfileGeneratorConfig,
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

    output_subfolder_msr_creator = Path(
        Path(str(config.output_folder))
        / "1_msr_creator"
        / region_name_without_spaces
        / "stage5_attribution"
    )
    output_msr_creator = Path(
        output_subfolder_msr_creator
        / f"{config.re_technology}_final_msrs.shp"
    )

    output_folder_profile_generator_lt = Path(
        Path(str(config.output_folder))
        / "2_profile_generator"
        / region_name_without_spaces
        / "local_time_profiles"
    )   
    output_folder_profile_generator_utc = Path(
        Path(str(config.output_folder))
        / "2_profile_generator"
        / region_name_without_spaces
        / "UTC_profiles"
    )

    output_folder_profile_generator_diagnostics = Path(
        Path(str(config.output_folder))
        / "2_profile_generator"
        / region_name_without_spaces
        / "diagnostics"
    )

    output_path_utc = output_folder_profile_generator_utc / f"{config.re_technology}_CF_profiles.csv"
    output_path_lt = output_folder_profile_generator_lt / f"{config.re_technology}_CF_profiles.csv"

    paths = RegionPaths(
        output_folder_profile_generator_lt=output_folder_profile_generator_lt,
        output_folder_profile_generator_utc=output_folder_profile_generator_utc,
        output_path_lt=output_path_lt,
        output_path_utc=output_path_utc,
        output_folder_profile_generator_diagnostics=output_folder_profile_generator_diagnostics,
        output_resource_raster=output_resource_raster,
        output_msr_creator=output_msr_creator
    )

    output_folder_profile_generator_lt.mkdir(parents=True, exist_ok=True)
    output_folder_profile_generator_utc.mkdir(parents=True, exist_ok=True)
    output_folder_profile_generator_diagnostics.mkdir(parents=True, exist_ok=True)

    return RegionContext(
        region_name_with_spaces=region_name,
        region_name_without_spaces=region_name_without_spaces,
        paths=paths
    )

def process_region(
    config: ProfileGeneratorConfig,
    context: RegionContext,
) -> None:
    """Run enabled workflow stages for a single region."""
    
    LOGGER.info(
        f"Starting region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )
    generate_profiles(context, config)

    LOGGER.info(
        f"Finished region workflow | region={context.region_name_without_spaces} "
        f"| technology={config.re_technology}"
    )

def process_all_regions(
    config: ProfileGeneratorConfig,
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
        process_region(config, context)

def generate_profiles(
    context: RegionContext,
    config: ProfileGeneratorConfig
) -> None:
    
    """Generate UTC and local time renewable profiles for a single region and technology."""

    LOGGER.info(
        f"Generating profiles | region={context.region_name_with_spaces} "
        f"| technology={config.re_technology}"
    )
    
    if config.re_technology == "solarpv":

        gsa_mean = calculate_resource_mean_at_msrs(context)
        era5 = open_era5(config)
        ds = select_nearest_era5_at_msrs(context, era5)
        ds = ds.assign(gsa_ghi_mean=gsa_mean)
        ds = bias_correct_solar_ghi(config, ds)
        ds = calculate_solarpv_capacity_factor(ds)
        ds_lt = create_local_time_profiles(ds, config, context)
        write_output(ds, ds_lt, config, context)

    elif config.re_technology == "wind":

        gwa_mean = calculate_resource_mean_at_msrs(context)
        era5 = open_era5(config)
        ds = select_nearest_era5_at_msrs(context, era5)
        ds = ds.assign(gwa_mean_wind_speed_100m=gwa_mean)
        ds = calculate_wind_speed(ds)
        a_r, b_r, y_r = fit_wind_bias_correction(ds, config)
        ds = bias_correct_wind_speed(ds, a_r, b_r, y_r)
        ds = calculate_wind_shear(ds)
        ds = extrapolate_wind_speed_to_hub_height(ds, config)
        ds = air_density_correction(ds)
        ds = assign_iec_class(ds)
        ds = calculate_wind_capacity_factor(ds, config)
        ds_lt = create_local_time_profiles(ds, config, context)
        write_output(ds, ds_lt, config, context)
            
    LOGGER.info(
        f"Profiles generation complete | region={context.region_name_with_spaces} "
        f"| technology={config.re_technology} "
    )
    ds.close()


def open_era5(
    config: ProfileGeneratorConfig,
) -> xr.Dataset:
    """Open and merge ERA5 variables."""

    if config.re_technology == "solarpv":
        filenames = [
            config.file_name_era5_ssrd,
            config.file_name_era5_temperature_2m,
        ]
    elif config.re_technology == "wind":
        filenames = [
            config.file_name_era5_wind_10m,
            config.file_name_era5_wind_100m,
            config.file_name_era5_temperature_2m,
            config.file_name_era5_geopotential,
        ]
    
    ds_yrs = []

    for year in config.weather_years:
        
        ds_vars = []
    
        for fn in filenames:
            
            era5_path = (
                config.input_folder_datasets
                / f"{year}_{fn}.nc"
            )

            ds_var = xr.open_dataset(era5_path)
            
            ds_vars.append(ds_var)                  # collect variables
        
        ds_yr = xr.merge(ds_vars, join="exact")     # combine variables for the year
        ds_yrs.append(ds_yr)                        # collect years

    ds = xr.concat(                                 # combine years
        ds_yrs,
        dim="valid_time",
        data_vars="minimal",
        coords="minimal",
        compat="override",
        join="exact",
    )
    ds = ds.sortby("valid_time")

    yrs = np.unique(ds["valid_time"].dt.year.values).tolist()
    missing_yrs = sorted(set(config.weather_years) - set(yrs))
    if missing_yrs:
        raise ValueError(
            f"Missing ERA5 data for years {missing_yrs}"
        )
    
    LOGGER.info(
        f"ERA5 datasets opened |"
        f" years={np.unique(ds['valid_time'].dt.year.values).tolist()} |"
        f" variables={list(ds.data_vars)} |"
        f" timesteps={ds.sizes['valid_time']} |"
        f" latitudes=[{ds['latitude'].min().item():.2f}, {ds['latitude'].max().item():.2f}] |"
        f" longitudes=[{ds['longitude'].min().item():.2f}, {ds['longitude'].max().item():.2f}]"
    )

    return ds

def select_nearest_era5_at_msrs(
    context: RegionContext,
    ds: xr.Dataset
) -> xr.Dataset:
    """Extract ERA5 values from the nearest grid cell to each MSR centroid."""

    paths = context.paths

    msrs = gpd.read_file(paths.output_msr_creator)
    msrs = msrs.rename(columns={"FID": "MSR_ID"})
    centroids = msrs.to_crs("EPSG:4326").geometry.centroid
    msrs["Latitude"] = centroids.y
    msrs["Longitude"] = centroids.x

    msr_ids = xr.DataArray(
        msrs["MSR_ID"].to_numpy(),
        dims="msr",
        name="msr",
    )

    msr_lats = xr.DataArray(
        msrs["Latitude"].to_numpy(),
        dims="msr",
        coords={"msr": msr_ids},
    )

    msr_lons = xr.DataArray(
        msrs["Longitude"].to_numpy(),
        dims="msr",
        coords={"msr": msr_ids},
    )

    era5_at_msrs = ds.sel(
        latitude=msr_lats,
        longitude=msr_lons,
        method="nearest",
    )

    LOGGER.info(
        f"ERA5 values extracted at MSR centroids | region={context.region_name_with_spaces} "
        f"| msrs={era5_at_msrs.sizes['msr']}"
    )

    return era5_at_msrs.assign_coords(
        msr_latitude=("msr", msrs["Latitude"].to_numpy()),
        msr_longitude=("msr", msrs["Longitude"].to_numpy()),
        era5_latitude=("msr", era5_at_msrs["latitude"].to_numpy()),
        era5_longitude=("msr", era5_at_msrs["longitude"].to_numpy()),
    )


def calculate_resource_mean_at_msrs(
    context: RegionContext,
) -> xr.DataArray:
    """Calculate the mean resource-raster value within each MSR."""

    paths = context.paths
    msrs = gpd.read_file(paths.output_msr_creator)
    msrs = msrs.rename(columns={"FID": "MSR_ID"})
    rr = zonal_stats(msrs, paths.output_resource_raster, stats="mean")
    
    rr_mean = np.array([r["mean"] for r in rr])

    # TODO: Include fallback for missing values

    LOGGER.info(
        f"Resource raster mean calculated at MSRs | region={context.region_name_with_spaces} "
    )

    return xr.DataArray(
        rr_mean,
        dims="msr",
        coords={"msr": msrs["MSR_ID"].to_numpy()},
        name="resource_mean",
    )

def bias_correct_solar_ghi(
    config: ProfileGeneratorConfig,
    ds: xr.Dataset,
) -> xr.Dataset:
    """Apply additive bias-correction."""
    
    # TODO: How to apply multi-year bisd-correction?

    ghi = ds["ssrd"]                               # J/m2
    ghi_kwh_m2 = (ghi / 3600000).clip(min=0)       # J/m2 -> kWh/m2
    era5_annual_ghi = (                            # kWh/m2/yr
        ghi_kwh_m2
        .sum(dim="valid_time")
        .rename("era5_annual_ghi")
        )

    hours_in_year = ds.sizes["valid_time"]
    gsa_ghi = ds["gsa_ghi_mean"]                   # kWh/m2/day
    gsa_annual_ghi = (                             # kWh/m2/yr
        gsa_ghi 
        * (hours_in_year / 24)
    ).rename("gsa_annual_ghi")

    annual_bias_ghi = gsa_annual_ghi - era5_annual_ghi

    provisional = ghi_kwh_m2 + annual_bias_ghi / hours_in_year

    eligible = (
        (ghi_kwh_m2 != 0)
        & (provisional > 0)
        & (provisional < 1)
    )

    elegible_hours = eligible.sum(dim="valid_time")
    bc_adder = annual_bias_ghi / elegible_hours

    bc_ghi = xr.where(
        eligible,
        ghi_kwh_m2 + bc_adder,
        ghi_kwh_m2,
    ).clip(min=0).rename("bc_ghi")

    LOGGER.info(
        f"Solar GHI bias-correction applied "
        f"| mean ERA5 GHI={era5_annual_ghi.mean().values:.2f} kWh/m2/yr"
        f"| mean GSA GHI={gsa_annual_ghi.mean().values:.2f} kWh/m2/yr"
        f"| mean adder={bc_adder.mean().values:.2f} kWh/m2"
    )

    return ds.assign(
        bc_ghi=bc_ghi * 1000,                       # kWh/m2 -> W/m2
        bc_adder=bc_adder * 1000,                   # kWh/m2 -> W/m2
        era5_annual_ghi=era5_annual_ghi,
        gsa_annual_ghi=gsa_annual_ghi,
    )


def calculate_solarpv_capacity_factor(
    ds: xr.Dataset,
) -> xr.DataArray:
    """
    Capacity factor calculation for solar PV based on the Huld et al. (2011) model.

    Reference: Huld T., et. al., 2011,
    A power-rating model for crystalline silicon PV modules,
    Solar Energy Materials and Solar Cells,
    Volume 95, Issue 12,
    Pages 3359-3369,
    ISSN 0927-0248,
    https://doi.org/10.1016/j.solmat.2011.07.026.
    """

    ghi = ds["bc_ghi"] # W/m2
    t_2m = ds["t2m"] # K
    
    g_stc = 1000 # W/m2
    t_stc = 25 + 273.15 # K
    k_t = 0.035 # 1/K
    k = np.array([
        -0.017162,
        -0.040289,
        -0.004681,
        0.000148,
        0.000169,
        0.000005,  
    ])

    g_norm = ghi / g_stc               # normalised irradiance
    t_norm = (                         # normalised module temperature
        k_t * ghi
        + t_2m
        - t_stc
    )

    log_g = xr.where(
        ghi > 0,
        np.log(g_norm),                # TODO: check if log(0) is handled correctly in original code
        0,
    )

    eff = (
        1
        + k[0] * log_g
        + k[1] * log_g**2
        + t_norm * (
            k[2]
            + k[3] * log_g
            + k[4] * log_g**2
        )
        + k[5] * t_norm**2
    )

    capacity_factor = (
        eff * g_norm
    ).fillna(0).clip(min=0).rename("capacity_factor")

    LOGGER.info(
        f"Solar PV capacity factor calculated "
        f"| mean capacity factor={capacity_factor.mean().values:.2f}"
    )

    return ds.assign(
        capacity_factor=capacity_factor
    )

def calculate_wind_speed(
    ds: xr.Dataset
) -> xr.Dataset:
    """Calculate wind speed from u and v components at 10m and 100m heights."""

    wind_speed_10m = np.hypot(ds["u10"], ds["v10"]).rename("wind_speed_10m")
    wind_speed_100m = np.hypot(ds["u100"], ds["v100"]).rename("wind_speed_100m")
    mean_wind_speed_100m = wind_speed_100m.mean(dim="valid_time").rename("mean_wind_speed_100m")

    return ds.assign(
        wind_speed_10m=wind_speed_10m,
        wind_speed_100m=wind_speed_100m,
        mean_wind_speed_100m=mean_wind_speed_100m,
    )

def fit_wind_bias_correction(
    ds: xr.Dataset,
    config: ProfileGeneratorConfig
):
    """Fit linear regression for empirical quantile mapping bias-correction.

    Fit one linear regression for each rank of the sorted wind speeds across all MSRs in the region in the reference year.
    """
    # Select reference year
    ds_ref = ds.sel(valid_time=ds.valid_time.dt.year == config.reference_year)
    
    # ERA5 wind speed time series at 100m for each MSR (msr, valid_time)
    raw = (
        ds_ref["wind_speed_100m"]
        .transpose("msr", "valid_time")
        .to_numpy()
    )

    # annual ERA5 mean wind speed for each MSR (msr,)
    x = raw.mean(axis=1)

    # sorted wind speeds for each MSR (msr, valid_time)
    # y[i ,k] = wind speed at rank k for MSR i
    y = np.sort(raw, axis=1)

    # mean annual ERA5 wind speed across all MSRs (scalar)
    x_mean = x.mean()

    # mean wind speed across MSRs for each sorted rank (valid_time,)
    y_mean = y.mean(axis=0)

    # slope 
    a = (
        np.sum(
            (x - x_mean)[:, None] * (y - y_mean[None, :]),
            axis=0)
        / np.sum((x - x_mean) ** 2)
    )

    # intercept
    b = y_mean - a * x_mean

    LOGGER.info(
        f"Wind speed bias-correction fitted "
        f"| reference year={config.reference_year} "
    )

    return a, b, y

def bias_correct_wind_speed(
    ds: xr.Dataset,
    a_r: np.ndarray, b_r: np.ndarray, y_r: np.ndarray
) -> xr.Dataset:
    """Apply empirical quantile mapping bias-correction to the target year.

    Interpolate the slope and intercept for each MSR and target-year hour 
    based on the fitted regression from the reference year.    
    """

    raw = (
        ds["wind_speed_100m"]
        .transpose("msr", "valid_time")
        .to_numpy()
    )
    
    gwa_mean = ds["gwa_mean_wind_speed_100m"].to_numpy()
    
    # TODO: Include fallback for missing GWA mean values

    # Regression across MSRs cannot be applied for one MSR
    # Instead, apply simple scaling  
    if raw.shape[0] == 1:
        scale = gwa_mean[0] / raw[0].mean()
        bc_values = raw * scale
    
    else:

        # interpolated slopes (a) and intercepts (b) for each MSR and target-year hour
        a_t = np.empty_like(raw, dtype=float)
        b_t = np.empty_like(raw, dtype=float)

        for msr_idx in range(raw.shape[0]):
            
            # sorted wind speeds for one MSR from reference year (valid_time_r,)
            y_r_idx = y_r[msr_idx]
            
            # wind speeds for one MSR from target year (valid_time_t,)
            z_t_idx = raw[msr_idx]

            # interpolate slope for each hour in target year.
            a_t[msr_idx] =np.interp(
                x=z_t_idx,
                xp=y_r_idx,
                fp=a_r,
            )

            # interpolate intercept for each hour in target year
            b_t[msr_idx] = np.interp(
                x=z_t_idx,
                xp=y_r_idx,
                fp=b_r,
            )


        bc_values = (
            a_t * gwa_mean[:, None] 
            + b_t
        ).clip(min=0)

    bc_wind_speed_100m = xr.DataArray(
        bc_values,
        dims=("msr", "valid_time"),
        coords={
            "msr": ds["msr"],
            "valid_time": ds["valid_time"]
        },
        name="bc_wind_speed_100m"
    ).transpose("valid_time", "msr")

    LOGGER.info(
        f"Wind speed bias-correction applied "
        # f"| mean ERA5 wind speed={raw.mean(dim='valid_time').mean():.2f} m/s "
        f"| mean GWA wind speed={gwa_mean.mean():.2f} m/s"
    )

    return ds.assign(
        bc_wind_speed_100m=bc_wind_speed_100m
    )

def calculate_wind_shear(
    ds: xr.Dataset
) -> xr.Dataset:
    """Calculate wind shear exponent from 10m and 100m wind speeds."""

    wind_speed_10m = ds["wind_speed_10m"]
    wind_speed_100m = ds["wind_speed_100m"]

    valid = (
        (wind_speed_10m > 0)
        & (wind_speed_100m > 0)
        & np.isfinite(wind_speed_10m)
        & np.isfinite(wind_speed_100m)
    )

    ratio = xr.where(
        valid,
        wind_speed_100m / wind_speed_10m,
        np.nan
    )

    alpha = (
        np.log(ratio)
        / np.log(100 / 10)
    ).fillna(0).rename("wind_shear_exponent")

    LOGGER.info(
        f"Wind shear exponent calculated "
        f"| mean wind shear exponent={alpha.mean().values:.2f}"
    )

    return ds.assign(wind_shear_exponent=alpha)

def extrapolate_wind_speed_to_hub_height(
    ds: xr.Dataset,
    config: ProfileGeneratorConfig,
) -> xr.DataArray:
    """Power law to extrapolate wind speed to the specified hub height."""
    
    h0 = 100       # reference extrapolation height [m]
    h_hub = config.wind_hub_height
    
    scale = (
        h_hub / h0
    ) ** ds["wind_shear_exponent"]

    hub_bc_wind_speed = (ds["bc_wind_speed_100m"] * scale).rename("hub_bc_wind_speed")
    hub_wind_speed = (ds["wind_speed_100m"] * scale).rename("hub_wind_speed")

    return ds.assign(
        hub_bc_wind_speed=hub_bc_wind_speed,
        hub_wind_speed=hub_wind_speed
    )

def air_density_correction(
    ds: xr.Dataset
) -> xr.Dataset:
    """Correct for air density at hub-height."""

    rho_0 = 1.225   # kg/m3
    R = 287.058     # J/(kgK)
    p_0 = 101325    # Pa

    z = ds["z"]
    t_2m = ds["t2m"]

    P = (
        p_0
        / np.exp(
            z
            / (R * t_2m)
        )
    ).rename("air_pressure")

    rho = (
        P
        / (R * t_2m)
    ).rename("air_density")

    ad_hub_bc_wind_speed = (
        ds["hub_bc_wind_speed"]
        * (
            rho_0 / rho                         # TODO: check inconsistency in original code using rho_0 / rho or rho / rho_0
        ) ** (1 / 3)
    ).rename("ad_hub_bc_wind_speed")

    return ds.assign(
        ad_hub_bc_wind_speed=ad_hub_bc_wind_speed
    )

def assign_iec_class(
    ds: xr.Dataset
) -> xr.Dataset:
    """Assign IEC class based on mean hub-height wind speed."""

    mean_hub_wind_speed = ds["hub_bc_wind_speed"].mean(dim="valid_time").rename("mean_hub_bc_wind_speed")

    iec_class = xr.where(
        mean_hub_wind_speed <= 7.5,
        "IEC Class 3",
        xr.where(
            mean_hub_wind_speed < 8.5,
            "IEC Class 2",
            "IEC Class 1"
        )
    ).rename("iec_class")

    LOGGER.info(
        f"IEC class assigned "
        f"| IEC Class 1 count={int((iec_class == 'IEC Class 1').sum().values)} "
        f"| IEC Class 2 count={int((iec_class == 'IEC Class 2').sum().values)} "
        f"| IEC Class 3 count={int((iec_class == 'IEC Class 3').sum().values)} "
    )

    return ds.assign(
        iec_class=iec_class)

def calculate_wind_capacity_factor(
    ds: xr.Dataset,
    config: ProfileGeneratorConfig
) -> xr.DataArray:
    """Calculate wind capacity factor based on IEC power curves."""
    
    wind_speed = ds["ad_hub_bc_wind_speed"]
    capacity_factor = xr.zeros_like(wind_speed, dtype=float).rename("capacity_factor")

    power_curve = pd.read_csv(
            Path(config.input_folder_datasets) /
            f"{config.file_name_IEC_power_curves}.csv",
            sep=","
        )
    curve_wind_speed = power_curve.iloc[:,0].to_numpy(dtype=float)

    for class_name in (
        "IEC Class 1",
        "IEC Class 2",
        "IEC Class 3",
    ):

        curve_capacity_factor = power_curve[class_name].to_numpy(dtype=float)

        # Interpolate the capacity factor for the given wind speed for the specified IEC class using the power curve
        class_cf = xr.apply_ufunc(
            np.interp,
            wind_speed,
            input_core_dims=[[]],
            output_core_dims=[[]],
            kwargs={
                "xp": curve_wind_speed,
                "fp": curve_capacity_factor,
                "left": 0,
                "right": 0,
            },
            dask="parallelized",
            output_dtypes=[float]
        )
        
        mask = ds["iec_class"] == class_name
        capacity_factor = xr.where(
            mask,
            class_cf,
            capacity_factor
        )
    
    capacity_factor = capacity_factor.fillna(0).clip(min=0, max=1)

    LOGGER.info(
        f"Wind capacity factor calculated "
        f"| mean capacity factor={capacity_factor.mean().values:.2f}"
    )

    return ds.assign(
        capacity_factor=capacity_factor
    )

def create_local_time_profiles(
    ds: xr.Dataset,
    config: ProfileGeneratorConfig,
    context: RegionContext
) -> xr.Dataset:
    """Convert UTC profiles to local time profiles based on the UTC offsets dataset."""

    utc_offsets = pd.read_csv(
        Path(config.input_folder_datasets) /
        f"{config.file_name_UTC_offsets}.csv",
        sep=";",
    )

    offset = utc_offsets[utc_offsets.Country == context.region_name_with_spaces].Hours.iloc[0]

    # TODO: roll function rotates values along the entire multi-year time-axis, 
    # which may not be correct for multi-year datasets.
    ds_local = ds.roll(valid_time=offset, roll_coords=False)
    # Instead, use assign_coords to shift the valid_time coordinate by the offset in hours.
    #ds_local = ds.assign_coords(
    #    valid_time=ds["valid_time"] + pd.to_timedelta(offset, unit="h")
    #)

    LOGGER.info(
        f"Local time profiles created "
        f"| offset={offset} hours"
    )

    return ds_local

def write_output(
    ds: xr.Dataset,
    ds_lt: xr.Dataset,
    config: ProfileGeneratorConfig,
    context: RegionContext,
):
    """Combine attributes and profiles into pandas DataFrames."""


    profiles = ds["capacity_factor"].transpose("msr", "valid_time").to_pandas().reset_index(drop=True).round(3)
    profiles_lt = ds_lt["capacity_factor"].transpose("msr", "valid_time").to_pandas().reset_index(drop=True).round(3)

    profiles.columns = pd.to_datetime(ds["valid_time"].to_numpy()).strftime("%Y-%m-%d %H:%M")
    profiles_lt.columns = pd.to_datetime(ds_lt["valid_time"].to_numpy()).strftime("%Y-%m-%d %H:%M")

    attributes = (
        gpd.read_file(context.paths.output_msr_creator)
        .drop(columns="geometry")
        .rename(columns={"FID": "MSR_ID"})
        .set_index("MSR_ID")
        .loc[ds["msr"].to_numpy()]
        .reset_index()
    )

    if config.re_technology == "solarpv":
        attributes["Latitude"] = ds["msr_latitude"].to_numpy()
        attributes["Longitude"] = ds["msr_longitude"].to_numpy()
        attributes["ERA5Latitude"] = ds["era5_latitude"].to_numpy()
        attributes["ERA5Longitude"] = ds["era5_longitude"].to_numpy()
        attributes["ERA5_GHI kWh/m2/yr"] = ds["era5_annual_ghi"].to_numpy()
        attributes["GSA_GHI kWh/m2/yr"] = ds["gsa_annual_ghi"].to_numpy()
        attributes["BiasCorrection Adder Wh for solar hours"] = ds["bc_adder"].to_numpy()

    if config.re_technology == "wind":
        attributes["Latitude"] = ds["msr_latitude"].to_numpy()
        attributes["Longitude"] = ds["msr_longitude"].to_numpy()
        attributes["ERA5Latitude"] = ds["era5_latitude"].to_numpy()
        attributes["ERA5Longitude"] = ds["era5_longitude"].to_numpy()
        attributes["GWA Annual MSR Mean m/s"] = ds["gwa_mean_wind_speed_100m"].to_numpy()
        attributes["ERA-Raw Annual Mean Speed m/s"] = ds["mean_wind_speed_100m"].to_numpy()

    
    output_utc = pd.concat(
        [
            attributes.reset_index(drop=True),
            profiles.reset_index(drop=True),
        ],
        axis=1
    )
    
    output_lt = pd.concat(
        [
            attributes.reset_index(drop=True),
            profiles_lt.reset_index(drop=True),
        ],
        axis=1
    )
    
    output_utc.to_csv(
        context.paths.output_path_utc, 
        index=False, 
        sep=";"
    )
    output_lt.to_csv(
        context.paths.output_path_lt,  
        index=False, 
        sep=";"
    )


    # plot_duration_curve(output_utc, config, context)
    # plot_fourier_spectrum(output_utc, config, context)
    plot_daily_profiles(output_lt, config, context)
    plot_seasonal_profiles(output_lt, config, context)


def weighted_percentile_ts(df, weights, percentile):
    """
    Calculate weighted percentile of multi-location time series for each time step
    """
    # order weights
    w = np.array(weights).reshape(-1, 1)
    w = w / np.sum(w)
    
    # extract time series data
    data = df.values
    
    # extract data and weights associated, sort in ascending order
    sorted_idx = np.argsort(data, axis = 0)
    sorted_data = np.take_along_axis(data, sorted_idx, axis = 0)
    
    # alignment of weights
    w_broadcast = np.broadcast_to(w, data.shape)
    sorted_weights = np.take_along_axis(w_broadcast, sorted_idx, axis = 0)
    
    # cumulation of weights
    cum_weights = np.cumsum(sorted_weights, axis = 0) - 0.5 * sorted_weights
    
    # interpolate to find the value
    p_val = percentile / 100
    result = [np.interp(p_val, cum_weights[:, j], sorted_data[:, j]) for j in range(data.shape[1])]
    
    return pd.Series(result, index = df.columns)


def plot_duration_curve(
    df: pd.DataFrame,
    config: ProfileGeneratorConfig,
    context: RegionContext,
) -> None:
    """Plot duration curve for first MSR in the output DataFrame."""

    if config.re_technology == "solarpv":
        color = "orange"
        name = "Solar PV"
    elif config.re_technology == "wind":
        color = "green"
        name = "Wind"

    output_path = (
        context.paths.output_folder_profile_generator_diagnostics
        / f"{config.re_technology}_duration_curve.png"
    )

    df = df.copy()
    hour_col = [c for c in df.columns if c.startswith("H")]
    profile = df.loc[0, hour_col].to_numpy()
    
    y = np.sort(profile)[::-1] * 100
    x = np.arange(1, len(y) + 1) / len(y) * 100

    fig, ax = plt.subplots(
        figsize=(6, 3),
    )
    ax.plot(
        x, 
        y, 
        color=color,
        linewidth=2,
    )

    ax.set_xlabel("Percentage of hours in year (%)")
    ax.set_ylabel("Capacity factor (%)")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    
    ax.set_title(
        f"{context.region_name_with_spaces} duration curve {name} profile"
        )
    
    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Duration curve plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )

def plot_fourier_spectrum(
    df: pd.DataFrame,
    config: ProfileGeneratorConfig,
    context: RegionContext,
) -> None:
    """Plot fourier spectrum for first MSR in the output DataFrame."""

    if config.re_technology == "solarpv":
        color = "orange"
        name = "Solar PV"
    elif config.re_technology == "wind":
        color = "green"
        name = "Wind"

    output_path = (
        context.paths.output_folder_profile_generator_diagnostics
        / f"{config.re_technology}_fourier_spectrum.png"
    )

    df = df.copy()
    hour_col = [c for c in df.columns if c.startswith("H")]
    profile = df.loc[0, hour_col].to_numpy(dtype=float)
    
    signal = profile - np.mean(profile)
    dt = 1
    n = len(signal)

    fft = np.fft.rfft(signal)
    freq = np.fft.rfftfreq(n, dt)
    
    ampl = (
        np.abs(fft) / n * 2
    )

    frequencies = freq[freq > 0]
    amplitudes = ampl[freq > 0]
    periods = 1 / frequencies

    fig, ax = plt.subplots(
        figsize=(6, 3),
    )

    ax.plot(
        periods, 
        amplitudes, 
        color=color,
        alpha=0.7,
    )

    ax.set_xscale("log")

    ax.set_xlabel("Period (hrs)")
    ax.set_ylabel("Amplitude")
    
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    pds = {
        12: "12\nhrs",
        24: "Daily",
        168: "Weekly",
        720: "Monthly",
        8760: "Yearly"
    }

    ax.set_xticks(list(pds.keys()))
    ax.set_xticklabels(list(pds.values()))
   
    ax.set_title(
        f"{context.region_name_with_spaces} fourier spectrum {name} profile"
    )
    
    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Fourier spectrum plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )
    

def plot_daily_profiles(
    df: pd.DataFrame,
    config: ProfileGeneratorConfig,
    context: RegionContext,
) -> None:
    """Plot daily profiles across MSRs in the output DataFrame."""

    if config.re_technology == "solarpv":
        color = "orange"
        name = "Solar PV"
    elif config.re_technology == "wind":
        color = "green"
        name = "Wind"

    output_path = (
        context.paths.output_folder_profile_generator_diagnostics
        / f"{config.re_technology}_daily_profiles.png"
    )

    df = df.copy()
    profile_col = df.filter(
        regex=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"
    ).columns
    timestamps = pd.to_datetime(
        profile_col,
        format="%Y-%m-%d %H:%M",
    )

    capacities = df["CapacityMW"].to_numpy(dtype=float)

    # weighted mean and percentiles
    w_profile_median = weighted_percentile_ts(df[profile_col], capacities, 50).to_numpy(dtype = float) * 100
    w_profile_25 = weighted_percentile_ts(df[profile_col], capacities, 25).to_numpy(dtype = float) * 100
    w_profile_75 = weighted_percentile_ts(df[profile_col], capacities, 75).to_numpy(dtype = float) * 100
    
    # 75th percentile profile across MSRs
    #w_profile_75 = np.percentile(
    #    df[profile_col].to_numpy(dtype=float),
    #    75,
    #    axis=0
    #) * 100

    profile_median = pd.DataFrame(
        {
            "capacity_factor": w_profile_median,
            "month": timestamps.month,
            "hour": timestamps.hour,
        },
        index=timestamps
    )
    
    profile_25 = pd.DataFrame(
        {
            "capacity_factor": w_profile_25,
            "month": timestamps.month,
            "hour": timestamps.hour,
        },
        index=timestamps
    )
    
    profile_75 = pd.DataFrame(
        {
            "capacity_factor": w_profile_75,
            "month": timestamps.month,
            "hour": timestamps.hour,
        },
        index=timestamps
    )

    stats_median = (
        profile_median.groupby(["month", "hour"])["capacity_factor"]
        .agg(['mean']))
    
    stats_25 = (
        profile_25.groupby(["month", "hour"])["capacity_factor"]
        .agg(['mean']))
    
    stats_75 = (
        profile_75.groupby(["month", "hour"])["capacity_factor"]
        .agg(['mean']))

    month_name = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
    ]

    fig, axes = plt.subplots(
        nrows = 3,
        ncols = 4,
        figsize = (12, 8),
        sharex = True,
        sharey = True,
    )

    for month, ax in enumerate(axes.flat, start = 1):

        monthly_median = stats_median.loc[month]
        monthly_25 = stats_25.loc[month]
        monthly_75 = stats_75.loc[month]

        ax.plot(
            monthly_median.index,
            monthly_median["mean"],
            color = color,
            linewidth = 2,
            label = "Median (weighted)"
        )

        ax.fill_between(
            monthly_median.index,
            monthly_25["mean"],
            monthly_75["mean"],
            color = color,
            alpha = 0.2,
            label = "Interquartile range"
        )

        ax.set_title(month_name[month - 1])
        ax.set_xlim(0, 23)
        ax.set_ylim(0, 110)
        ax.set_xticks(range(0, 24, 3))

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    
    fig.supxlabel("Hour of day")
    fig.supylabel("Capacity factor across MSRs (%)")
    fig.suptitle(
        f"{context.region_name_with_spaces} daily {name} profile"
        )
    
    plt.legend()
    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Monthly daily profile plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )

def plot_seasonal_profiles(
    df: pd.DataFrame,
    config: ProfileGeneratorConfig,
    context: RegionContext,
) -> None:
    """Plot seasonal profiles for first MSR in the output DataFrame."""

    if config.re_technology == "solarpv":
        color = "orange"
        name = "Solar PV"
    elif config.re_technology == "wind":
        color = "green"
        name = "Wind"

    output_path = (
        context.paths.output_folder_profile_generator_diagnostics
        / f"{config.re_technology}_seasonal_profiles.png"
    )

    df = df.copy()
    profile_col = df.filter(
        regex=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"
    ).columns
    timestamps = pd.to_datetime(
        profile_col,
        format="%Y-%m-%d %H:%M",
    )

    capacities = df["CapacityMW"].to_numpy(dtype=float)
    
    # weighted mean and percentiles
    w_profile_median = weighted_percentile_ts(df[profile_col], capacities, 50).to_numpy(dtype = float) * 100
    w_profile_25 = weighted_percentile_ts(df[profile_col], capacities, 25).to_numpy(dtype = float) * 100
    w_profile_75 = weighted_percentile_ts(df[profile_col], capacities, 75).to_numpy(dtype = float) * 100

    series_median = pd.Series(w_profile_median, index=timestamps, dtype=float)
    series_25 = pd.Series(w_profile_25, index=timestamps, dtype=float)
    series_75 = pd.Series(w_profile_75, index=timestamps, dtype=float)

    monthly_median = (
        series_median.groupby(series_median.index.month)
        .agg(['mean'])
        .reindex(range(1, 13))
    )
    
    monthly_25 = (
        series_25.groupby(series_25.index.month)
        .agg(['mean'])
        .reindex(range(1, 13))
    )
    
    monthly_75 = (
        series_75.groupby(series_75.index.month)
        .agg(['mean'])
        .reindex(range(1, 13))
    )

    fig, ax = plt.subplots(
        figsize=(6, 3),
    )

    ax.plot(
        np.arange(1,13),
        monthly_median["mean"],
        color=color,
        linewidth=2,
        label="Median (weighted)"
    )
    ax.fill_between(
        np.arange(1,13),
        monthly_25["mean"],
        monthly_75["mean"],
        color=color,
        alpha=0.2,
        label="Interquartile range",
    )

    ax.set_xlabel("Month of year")
    ax.set_ylabel("Capacity factor (%)")
    ax.set_xlim(1, 12)
    ax.set_ylim(0, np.min([np.max(monthly_75["mean"])*1.25, 110]))
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels([
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
    ])

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    
    ax.set_title(
        f"{context.region_name_with_spaces} seasonal {name} profile"
        )
    
    ax.legend(frameon=False, loc="upper left")
    
    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
    )

    plt.close(fig)

    LOGGER.info(
        f"Seasonal profile plot written | "
        f"region={context.region_name_with_spaces} | "
        f"technology={config.re_technology} | "
        f"path={output_path}"
    )

def main() -> None:
    """Load control inputs and run the Profile Generator workflow."""

    configure_logging()
    LOGGER.info("Profile Generator workflow started")
    try:
        control_file = Path(CONTROL_FILE_NAME)
        control = load_control_workbook(control_file)
        LOGGER.info(f"Control workbook loaded | path={control_file}")
        config = build_profile_generator_config(control_file, control)
        
        LOGGER.info(
                f"Configuration prepared | technologies={config.technologies_to_run} "
                f"| regions={len(config.regions)}"
            )
        for tech in config.technologies_to_run:
            config.re_technology = tech

            process_all_regions(config)
    except Exception:
        LOGGER.exception("Profile Generator workflow failed")
        raise
    LOGGER.info("Profile Generator workflow completed")


if __name__ == "__main__":
    main()  
