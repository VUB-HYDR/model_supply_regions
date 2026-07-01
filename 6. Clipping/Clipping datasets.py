# This simple code is for clipping the required datasets for MSR illustration All Africa
# The dataset naming needs not to be change, otherwise it will break the QGIS code that reads it

import openpyxl as openpyxl
import os
import json
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, mapping
from colorama import Fore

import warnings
warnings.filterwarnings("ignore")


#Read control input file
ControlDataSetNames=pd.read_excel('Control Inputs Clipping.xlsx', sheet_name="input dataset names", index_col=0)
ControlPaths=pd.read_excel('Control Inputs Clipping.xlsx', sheet_name="Paths", index_col=0)
AllCountries=pd.read_csv(ControlPaths.loc["path to csv file carrying country names to run analysis on"][0],names=["Ct"])

HomeDirectory=str(ControlPaths.loc["Home directory"][0])
InputSpatialDatasetsFolder = HomeDirectory + ControlPaths.loc["Where to find input spatial datasets"][0]

Flag_AnalyseWithExactCutToRegionBoundary= 1
ClipBoundaryBuffer_meters= 0

#assign file names
FileName_Roads = ControlDataSetNames.loc["FileName_Roads"][0]
FileName_TransmissionGrid=ControlDataSetNames.loc["FileName_TransmissionGrid"][0]
FileName_DistributionGrid=ControlDataSetNames.loc["FileName_DistributionGrid"][0]
FileName_CountryBoundaries=ControlDataSetNames.loc["FileName_CountryBoundaries"][0]
FileName_WaterRivers=ControlDataSetNames.loc["FileName_WaterRivers"][0]
FileName_WaterBodies=ControlDataSetNames.loc["FileName_WaterBodies"][0]
FileName_MajorCities=ControlDataSetNames.loc["FileName_MajorCities"][0]
FileName_SolarPV=ControlDataSetNames.loc["FileName_SolarPV"][0]
FileName_SolarPVwithClusterId=ControlDataSetNames.loc["FileName_SolarPVwithClusterId"][0]
FileName_OnShoreWind=ControlDataSetNames.loc["FileName_OnShoreWind"][0]
FileName_WindwithClusterId=ControlDataSetNames.loc["FileName_WindwithClusterId"][0]
#FileName_SolarCSP=ControlDataSetNames.loc["FileName_SolarCSP"][0]

gdf_CountryBoundaries=gpd.read_file(InputSpatialDatasetsFolder+FileName_CountryBoundaries+".shp")
SubfolderCountryMapsForClipping=HomeDirectory

pd_LogFile=pd.DataFrame()
for CountryCounter in range(0,len(AllCountries)):#country wise loop
    RegionName_withSpaces=AllCountries.Ct[CountryCounter]
    RegionName_withoutSpaces = AllCountries.Ct[CountryCounter].replace(" ", "")
    print(Fore.GREEN+"Running clipping script for %s"%RegionName_withSpaces)

    #assign and create folders
    SubfolderStage1_Clipping = HomeDirectory +ControlPaths.loc["Where to put outputs"][0] + RegionName_withoutSpaces + "/"
    if not os.path.isdir(SubfolderStage1_Clipping):
        os.makedirs(SubfolderStage1_Clipping)

    #prepare country boundary shapefile
    gdf_SingleCountry=gdf_CountryBoundaries[gdf_CountryBoundaries.name == RegionName_withSpaces]

    if RegionName_withoutSpaces == 'MoroccoW':  # This code segment, allows user to freely name the Morocc+Western Sahara as 'MoroccoW' in the csv file carrying country names
        gdf_SingleCountry = gdf_CountryBoundaries[(gdf_CountryBoundaries.name == 'Morocco') | (gdf_CountryBoundaries.name == 'Western Sahara')]

    if not os.path.isdir(SubfolderCountryMapsForClipping):
        os.makedirs(SubfolderCountryMapsForClipping)
    if ClipBoundaryBuffer_meters > 0:
        gdf_SingleCountry = gdf_SingleCountry.to_crs('ESRI:54009')
        gdf_SingleCountry = gdf_SingleCountry.buffer(ClipBoundaryBuffer_meters)
        gdf_SingleCountry = gdf_SingleCountry.to_crs('EPSG:4326')
    gdf_SingleCountry.to_crs('EPSG:4326').to_file(SubfolderCountryMapsForClipping+'/' + RegionName_withSpaces + '.shp')

    UpperLeftX,LowerRightY, LowerRightX, UpperLeftY=gdf_SingleCountry.total_bounds
    MinX, MinY, MaxX, MaxY = gdf_SingleCountry.total_bounds
    ClipGeometry=json.dumps(mapping(box(UpperLeftX, UpperLeftY, LowerRightX, LowerRightY)))

    #[FileName_Roads, FileName_TransmissionGrid, FileName_DistributionGrid, FileName_MajorCities, FileName_WaterRivers, FileName_WaterBodies, FileName_OnShoreWind, FileName_SolarPV, FileName_SolarCSP]:
    for Vector in [FileName_Roads, FileName_TransmissionGrid, FileName_DistributionGrid, FileName_MajorCities, FileName_WaterRivers, FileName_WaterBodies]:
        gdf_ClippedVector = gpd.read_file("%s%s.shp" % (InputSpatialDatasetsFolder, Vector), bbox=tuple(
            gdf_SingleCountry.total_bounds))  # bbox (bounding box) used to avoid reading unwanted data. Note that this
        # is does not clip features that extend beyond bbox boundary.

        if Flag_AnalyseWithExactCutToRegionBoundary:
            gdf_ClippedVector = gpd.clip(gdf_ClippedVector, gdf_SingleCountry.geometry)
            if gdf_ClippedVector.empty:  # Check again after clipping to borders that if the dataset is again empty then
                # repeat the procedure of self loading appropriate polygon in order for next scoring step to function
                # properly
                gdf_ClippedVector = gpd.GeoDataFrame({'geometry': gdf_SingleCountry.geometry}, geometry='geometry')
        gdf_ClippedVector = gdf_ClippedVector.to_crs('EPSG:4326')  # All CRS projections must be ensured to have same crs before rasterizing
        gdf_ClippedVector.to_file("%s%s_%s.shp" % (SubfolderStage1_Clipping, RegionName_withSpaces, Vector))
        print("clipped %s vector dataset" % Vector)
        del (gdf_ClippedVector)

    for Vector2 in [FileName_OnShoreWind, FileName_SolarPV, FileName_SolarPVwithClusterId, FileName_WindwithClusterId]:
        # Wind relaxation cases (add exception): Rwanda, Liberia, Sierra Leone, Guiné-Bissau, Equatorial Guinea, Gabon, Burundi
        gdf_toSave = gpd.read_file("%s%s.shp" % (InputSpatialDatasetsFolder, Vector2))
        CountryFile = gdf_toSave[gdf_toSave.CtryName == RegionName_withoutSpaces]

        if RegionName_withoutSpaces=='MoroccoW': # This code segment, allows user to freely name the Morocc+Western Sahara as 'MoroccoW' in the csv file carrying country names
            CountryFile = gdf_toSave[(gdf_toSave.CtryName == 'Morocco' ) | (gdf_toSave.CtryName == 'WesternSahara')]

        try:
            CountryFile= CountryFile.to_crs('EPSG:4326')
            CountryFile.to_file("%s%s_%s.shp" % (SubfolderStage1_Clipping, RegionName_withSpaces, Vector2))
            print("clipped %s vector dataset" % Vector2)
        except:
            print("%s vector dataset is empty, could not be saved!" % Vector2)


