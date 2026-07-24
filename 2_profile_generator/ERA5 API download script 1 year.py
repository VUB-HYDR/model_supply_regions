import cdsapi

# "100m_u_component_of_wind",
# "100m_v_component_of_wind",
# "10m_v_component_of_wind",
# "10m_u_component_of_wind",
# "clear_sky_direct_solar_radiation_at_surface",
# "near_ir_albedo_for_diffuse_radiation",
# "surface_solar_radiation_downwards",
# "total_sky_direct_solar_radiation_at_surface",
# "uv_visible_albedo_for_diffuse_radiation",
# "2m_temperature",
# "geopotential"

dataset = "reanalysis-era5-single-levels"
request = {
    "product_type": ["reanalysis"],
    "variable": [
        "100m_u_component_of_wind",
        "100m_v_component_of_wind",
    ],
    "year": ["2013"],
    "month": [
        "01", "02", "03",
        "04", "05", "06",
        "07", "08", "09",
        "10", "11", "12"
    ],
    "day": [
        "01", "02", "03",
        "04", "05", "06",
        "07", "08", "09",
        "10", "11", "12",
        "13", "14", "15",
        "16", "17", "18",
        "19", "20", "21",
        "22", "23", "24",
        "25", "26", "27",
        "28", "29", "30",
        "31"
    ],
    "time": [
        "00:00", "01:00", "02:00",
        "03:00", "04:00", "05:00",
        "06:00", "07:00", "08:00",
        "09:00", "10:00", "11:00",
        "12:00", "13:00", "14:00",
        "15:00", "16:00", "17:00",
        "18:00", "19:00", "20:00",
        "21:00", "22:00", "23:00"
    ],
    "data_format": "netcdf",
    "download_format": "unarchived",
    "area": [1.5, -81, -5, -75.25]
}

output_folder = "C:/Users/mastt/OneDrive - VITO/Documents/21_WP1/RawData/model_supply_regions/workflow/inputs/2022 MSR Toolset Inputs"
output_file = f"{output_folder}/2013_100m.nc"

client = cdsapi.Client()
client.retrieve(dataset, request, str(output_file))

print(f"Saved to: {output_file}")
client = cdsapi.Client()
client.retrieve(dataset, request).download()