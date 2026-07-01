from qgis.PyQt import QtGui

FolderPath = 'C:\\Users\\adm_sesterl\\OneDrive - Vrije Universiteit Brussel\\Documenten\\VUB Work Files\\22010 MSR code\\Local repo\\6. Clipping code\\Clipping output datasets'      #The folder where the datasets exists

#Control inputs 
DrawDistributionGrid = True
FixedLegendRange = True
ZoneID = False

opacity = 1
NumberOfRanges = 10
NameSuffix = ""

def LayerLegendGradual(MinValue, MaxValue, Step, Layer, rangeColors, NumberOfRanges, opacity,FixedLegendRange, ZoneID, AttributeName,IsCLUSTER):
    RangeList = []
    InitialValue = MinValue
    for i in range(1,NumberOfRanges+1):
        symbol1 = QgsSymbol.defaultSymbol(Layer.geometryType())
        symbol1.setColor(rangeColors[i-1])
        symbol1.symbolLayer(0).setStrokeWidth(0.01)
        symbol1.setOpacity(opacity)
        if IsCLUSTER:
            if FixedLegendRange:
                if i <= NumberOfRanges:
                    testValue = MinValue - 1 + i
                    range1 = QgsRendererRange(0, testValue, symbol1, '%.f' %testValue) 
                    InitialValue = InitialValue - Step
        else:
            if FixedLegendRange:
                if i == 1:
                    range1 = QgsRendererRange(0, MinValue, symbol1, '< %.f%%' %MinValue) 
                    InitialValue = InitialValue - Step
                elif i == NumberOfRanges:
                    range1 = QgsRendererRange(MaxValue, 10000, symbol1, '> %.f%%' %MaxValue) 
                else:
                    range1 = QgsRendererRange(InitialValue, InitialValue + Step, symbol1, '%.f - %.f%%'%(InitialValue, InitialValue + Step)) 
            else:
                if i == NumberOfRanges:
                    range1 = QgsRendererRange(InitialValue, InitialValue + Step + 0.001, symbol1, '%.2f - %.2f%%'%(InitialValue, InitialValue + Step))
                else:
                    range1 = QgsRendererRange(InitialValue, InitialValue + Step, symbol1, '%.2f - %.2f%%'%(InitialValue, InitialValue + Step))

        RangeList.append(range1)
        InitialValue = InitialValue + Step

    #Create the renderer
    groupRenderer = QgsGraduatedSymbolRenderer('', RangeList)
    groupRenderer.setMode(QgsGraduatedSymbolRenderer.EqualInterval)
    groupRenderer.setClassAttribute(AttributeName)

    #Apply renderer to layer
    Layer.setRenderer(groupRenderer)

    #Add MSRs' ID as a label
    if ZoneID:
        text_format = QgsTextFormat()
        text_format.setSize(2)
        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(0.2)
        buffer_settings.setColor(QColor.fromRgb( 255, 245, 240 ))
        text_format.setBuffer(buffer_settings)
        label = QgsPalLayerSettings()
        label.setFormat(text_format)
        label.fieldName = 'FID'
        label.placement = QgsPalLayerSettings.OverPoint
        #label.centroidWhole = True
        label.enabled = True
        label.displayAll = True
        labeler = QgsVectorLayerSimpleLabeling(label)
        Layer.setLabelsEnabled(True)
        Layer.setLabeling(labeler)
    

def LayerBackgroundColor(Layer, Color, IsBlue):
    ColorLayer = QgsSymbol.defaultSymbol(Layer.geometryType())    # create a new symbol from the layer characteristic
    ColorLayer.setColor(Color)       #Blue colour
    if IsBlue:
        ColorLayer.symbolLayer(0).setStrokeColor(Color)
    Layer.renderer().setSymbol(ColorLayer)    # apply symbol to layer renderer
    
def TotalMWPerRange(Layer,RangeValues,IsCLUSTER):
    iterOne = Layer.getFeatures()
    RangeMW = [0,0,0,0,0,0,0,0,0,0]
    if IsCLUSTER:
        x = 32      #Column of the CLUSTER
    else:
        x = 18      #Column of the capacity factor
    
    for feature in iterOne: 
        if (feature.attributes()[x] >= RangeValues[0]) and (feature.attributes()[x] <= RangeValues[1]):
            RangeMW[0] = RangeMW[0] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[2]) and (feature.attributes()[x] <= RangeValues[3]):
            RangeMW[1] = RangeMW[1] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[4]) and (feature.attributes()[x] <= RangeValues[5]):
            RangeMW[2] = RangeMW[2] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[6]) and (feature.attributes()[x] <= RangeValues[7]):
            RangeMW[3] = RangeMW[3] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[8]) and (feature.attributes()[x] <= RangeValues[9]):
            RangeMW[4] = RangeMW[4] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[10]) and (feature.attributes()[x] <= RangeValues[11]):
            RangeMW[5] = RangeMW[5] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[12]) and (feature.attributes()[x] <= RangeValues[13]):
            RangeMW[6] = RangeMW[6] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[14]) and (feature.attributes()[x] <= RangeValues[15]):
            RangeMW[7] = RangeMW[7] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[16]) and (feature.attributes()[x] <= RangeValues[17]):
            RangeMW[8] = RangeMW[8] + feature.attributes()[2]
        elif (feature.attributes()[x] > RangeValues[18]) and (feature.attributes()[x] <= RangeValues[19]):
            RangeMW[9] = RangeMW[9] + feature.attributes()[2]
    
    return RangeMW

if FixedLegendRange:
    NameSuffix = NameSuffix + "_FixedRange"
if ZoneID:
    NameSuffix = NameSuffix + "_WithMSRID"



#Starting of the loop

#Countries = ['Algeria', 'Angola', 'Benin', 'Botswana', 'Burkina Faso', 'Burundi', 'Cameroon', 'Central African Republic', 'Chad', 'Congo', 'Democratic Republic of the Congo', 'Djibouti', 'Egypt', 'Equatorial Guinea', 'Eritrea', 'Eswatini', 'Ethiopia', 'Gabon', 'Gambia', 'Ghana', 'Guinea', 'Guinea-Bissau', 'Ivory Coast', 'Kenya', 'Lesotho', 'Liberia', 'Libya', 'Madagascar', 'Malawi', 'Mali', 'Mauritania', 'Morocco', 'MoroccoW','Mozambique', 'Namibia', 'Niger', 'Nigeria', 'Rwanda', 'Senegal', 'Sierra Leone', 'Somalia', 'South Africa', 'South Sudan', 'Sudan', 'Togo', 'Tunisia', 'Uganda', 'United Republic of Tanzania', 'Western Sahara', 'Zambia', 'Zimbabwe']
Countries = ['Argentina']

#------------------------------------------------------------------------------------------

for CountryName in Countries:
        
    #Loading the datasets to QGIS
    CountryBoundary = QgsVectorLayer(FolderPath + '\%s.shp' % CountryName,'Country boundaries','ogr')
    
    if CountryName != 'Burundi' or CountryName != 'Equatorial Guinea' or CountryName != 'Gabon' or CountryName != 'Guinea-Bissau' or CountryName != 'Liberia' or CountryName != 'Rwanda' or CountryName != 'Sierra Leone':
        CountryWind5Perc = QgsVectorLayer(FolderPath + '\%s_Wind_withCluster.shp' % CountryName,'Capacity factor % (total MW)','ogr')
        CountryWindCLUSTER = QgsVectorLayer(FolderPath + '\%s_Wind_withCluster.shp' % CountryName,'Cluster number (total MW)','ogr')
    CountrySolarPV5Perc = QgsVectorLayer(FolderPath + '\%s_SolarPV_withCluster.shp' % CountryName,'Capacity factor % (total MW)','ogr')
    CountrySolarPVCLUSTER = QgsVectorLayer(FolderPath + '\%s_SolarPV_withCluster.shp' % CountryName,'Cluster number (total MW)','ogr')

    CountryTGrid = QgsVectorLayer(FolderPath + '\%s_gridfinder_T.shp' % CountryName,'Transmission lines','ogr')
    CountryDGrid = QgsVectorLayer(FolderPath + '\%s_gridfinder_D.shp' % CountryName,'Distribution lines','ogr')
    CountryRoads = QgsVectorLayer(FolderPath + '\%s_GRIP4_region2.shp' % CountryName,'Road','ogr')

    CountryLakes = QgsVectorLayer(FolderPath + '\%s_WaterBodiesComplete.shp' % CountryName,'Lakes','ogr')
    CountryCenterLinesRivers = QgsVectorLayer(FolderPath + '\%s_ne_10m_rivers_lake_centerlines_scale_rank.shp' % CountryName,'Rivers','ogr')

    CountryMajorCities = QgsVectorLayer(FolderPath + '\%s_ne_10m_populated_places.shp' % CountryName,'Major cities','ogr')

    #Checking if one of the dataset failed to load
#    if not (CountryBoundary and CountryLakes and CountryCenterLinesRivers and CountryTGrid and CountryDGrid and CountryRoads and CountryMajorCities and CountrySolarPV5Perc and CountryWind5Perc ):
#      print("Layer failed to load!", 'red')

    #Adding a vertical indent for long name title
    if CountryName in ['Central African Republic','Democratic Republic of the Congo','Equatorial Guinea','United Republic of Tanzania', 'Western Sahara']:
        LongTitleIndent = 12
    else:
        LongTitleIndent = 0

#------------------------------------------------------------------------------------------

    #Changing the layers symbology (how they appear)

    #Changing background colour
    LayerBackgroundColor(CountryBoundary, QColor.fromRgb(232, 232, 232),False)    #Grey
    QgsProject.instance().addMapLayer(CountryBoundary)       #Add the layer

    #Changing road layers colour
    LayerBackgroundColor(CountryRoads, QColor.fromRgb(191, 191, 171),False)       #Grey-ish
    if (CountryRoads.featureCount()) != 1 and (len(CountryRoads.attributeList()) != 1):
        QgsProject.instance().addMapLayer(CountryRoads)

    #Changing water layers to blue
    LayerBackgroundColor(CountryCenterLinesRivers, QColor.fromRgb(128, 177, 211),True)       #Blue
    if (CountryCenterLinesRivers.featureCount()) != 1 and (len(CountryCenterLinesRivers.attributeList()) != 1):
        QgsProject.instance().addMapLayer(CountryCenterLinesRivers)

    LayerBackgroundColor(CountryLakes, QColor.fromRgb(128, 177, 211),True)       #Blue
    if (CountryLakes.featureCount()) != 1 and (len(CountryLakes.attributeList()) != 1):
        QgsProject.instance().addMapLayer(CountryLakes)
    
    #Changing Grid layers to blue
    LayerBackgroundColor(CountryDGrid, QColor.fromRgb(82, 0, 234),False)       #Purple
    if (CountryDGrid.featureCount()) != 1 and (len(CountryDGrid.attributeList()) != 1):
        if DrawDistributionGrid:
            QgsProject.instance().addMapLayer(CountryDGrid)
            
    LayerBackgroundColor(CountryTGrid, QColor.fromRgb(231, 58, 0),False)       #Orange
    if (CountryTGrid.featureCount()) != 1 and (len(CountryTGrid.attributeList()) != 1):   #Check if the layer is empty
        QgsProject.instance().addMapLayer(CountryTGrid)

#------------------------------------------------------------------------------------------

    #Changing cities symbology

    #Define SVG properties
    svgStyle = {}
    svgStyle['fill'] = '#000000'
    svgStyle['name'] = 'C:\\Users\\adm_sesterl\\OneDrive - Vrije Universiteit Brussel\\Documenten\\VUB Work Files\\22010 MSR code\\Local repo\\7. PDF generator code\\Maps input vectors\\white-home-svgrepo-com.svg'
    svgStyle['outline'] = '#000000'
    svgStyle['outline-width'] = '0'
    svgStyle['size'] = '5'

    #Create SVG symbol
    symbol = QgsSvgMarkerSymbolLayer.create(svgStyle)
    CountryMajorCities.renderer().symbol().changeSymbolLayer(0, symbol)

    #Display city names
    #Text format
    text_format = QgsTextFormat()
    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(1)
    buffer_settings.setColor(QColor.fromRgb( 255, 245, 240 ))
    text_format.setBuffer(buffer_settings)

    #Label format
    label = QgsPalLayerSettings()
    label.setFormat(text_format)
    label.fieldName = 'NAMEASCII'
    label.placement = QgsPalLayerSettings.OverPoint
    label.quadOffset = 2
    label.xOffset = 2
    label.yOffset = 2
    label.fontBold = True
    label.enabled = True

    labeler = QgsVectorLayerSimpleLabeling(label)
    CountryMajorCities.setLabelsEnabled(True)
    CountryMajorCities.setLabeling(labeler)

    if (CountryMajorCities.featureCount()) != 1 and (len(CountryMajorCities.attributeList()) != 1):
        QgsProject.instance().addMapLayer(CountryMajorCities)

#------------------------------------------------------------------------------------------

    #Changing the MSR layer based on graduated symbology

    #Wind layer
    if CountryName != 'Burundi' and CountryName != 'Equatorial Guinea' and CountryName != 'Gabon' and CountryName != 'Guinea-Bissau' and CountryName != 'Liberia' and CountryName != 'Rwanda' and CountryName != 'Sierra Leone':
        WindCF100 = 'CF100m'
        WindRangeList = []
        WindCFRangeValues = []

        if FixedLegendRange:
            MinValue = 25
            MaxValue = 65
            Step = 5
            WindCFRangeValues = [0,25,25,30,30,35,35,40,40,45,45,50,50,55,55,60,60,65,65,10000]
        else:     
            MinValue = CountryWind5Perc.minimumValue(18)
            MaxValue = CountryWind5Perc.maximumValue(18)
            Step = (MaxValue - MinValue)/NumberOfRanges
            WindCFRangeValues = [MinValue,MinValue+Step,MinValue+Step,MinValue+2*Step,MinValue+2*Step,MinValue+3*Step,MinValue+3*Step,MinValue+4*Step,MinValue+4*Step,MaxValue]
          
        if NumberOfRanges == 5:
            rangeColors = [QtGui.QColor('#d0e9ca'),QtGui.QColor('#93d98b'),QtGui.QColor('#41ab5d'),QtGui.QColor('#007730'),QtGui.QColor('#00441b')]
        if NumberOfRanges == 10:
            rangeColors = [QtGui.QColor('#ffffff'),QtGui.QColor('#f7fcfd'),QtGui.QColor('#e5f5f9'),QtGui.QColor('#ccece6'),QtGui.QColor('#99d8c9'),QtGui.QColor('#66c2a4'),QtGui.QColor('#41ae76'),QtGui.QColor('#238b45'),QtGui.QColor('#006d2c'),QtGui.QColor('#00441b')]

        LayerLegendGradual(MinValue, MaxValue, Step, CountryWind5Perc, rangeColors, NumberOfRanges, opacity,FixedLegendRange, ZoneID, WindCF100, False)

        #Wind CLUSTER layer
        WindCLUSTERColumn = 'join_win_4'
        WindRangeList = []
        WindCLUSTERRangeValues = []

        if FixedLegendRange:
            MinValue = 1
            MaxValue = 10
            Step = 1
            WindCLUSTERRangeValues = [0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10]
        else:    
            MinValue = CountryWindCLUSTER.minimumValue(25)
            MaxValue = CountryWindCLUSTER.maximumValue(25)
            Step = (MaxValue - MinValue)/NumberOfRanges
            WindCLUSTERRangeValues = [MinValue,MinValue+Step,MinValue+Step,MinValue+2*Step,MinValue+2*Step,MinValue+3*Step,MinValue+3*Step,MinValue+4*Step,MinValue+4*Step,MaxValue]
        
        if NumberOfRanges == 5:
            rangeColors = [QtGui.QColor('#00441b'),QtGui.QColor('#007730'),QtGui.QColor('#41ab5d'),QtGui.QColor('#93d98b'),QtGui.QColor('#d0e9ca')]
        if NumberOfRanges == 10:
            rangeColors = [QtGui.QColor('#a6cee3'),QtGui.QColor('#1f78b4'),QtGui.QColor('#b2df8a'),QtGui.QColor('#33a02c'),QtGui.QColor('#fb9a99'),QtGui.QColor('#e31a1c'),QtGui.QColor('#fdbf6f'),QtGui.QColor('#ff7f00'),QtGui.QColor('#cab2d6'),QtGui.QColor('#6a3d9a')]

        LayerLegendGradual(MinValue, MaxValue, Step, CountryWindCLUSTER, rangeColors, NumberOfRanges, opacity,FixedLegendRange, ZoneID, WindCLUSTERColumn, True)

    #Solar layer
    SolarCF = 'CF'
    SolarRangeList = []
    SolarCFRangeValues = []

    if FixedLegendRange:
        MinValue = 15
        MaxValue = 23
        Step = 1
        SolarCFRangeValues = [0,15,15,16,16,17,17,18,18,19,19,20,20,21,21,22,22,23,23,10000]
    else:
        MinValue = CountrySolarPV5Perc.minimumValue(18)
        MaxValue = CountrySolarPV5Perc.maximumValue(18)
        Step = (MaxValue - MinValue)/NumberOfRanges
        SolarCFRangeValues = [MinValue,MinValue+Step,MinValue+Step,MinValue+2*Step,MinValue+2*Step,MinValue+3*Step,MinValue+3*Step,MinValue+4*Step,MinValue+4*Step,MaxValue]

    if NumberOfRanges == 5:
        rangeColors = [QtGui.QColor('#fdd7b1'),QtGui.QColor('#fdae76'),QtGui.QColor('#f17a30'),QtGui.QColor('#ff601c'),QtGui.QColor('#b42a04')]
    if NumberOfRanges == 10:
        rangeColors = [QtGui.QColor('#ffffff'),QtGui.QColor('#ffffcc'),QtGui.QColor('#ffeda0'),QtGui.QColor('#fed976'),QtGui.QColor('#feb24c'),QtGui.QColor('#fd8d3c'),QtGui.QColor('#fc4e2a'),QtGui.QColor('#e31a1c'),QtGui.QColor('#bd0026'),QtGui.QColor('#800026')]

    LayerLegendGradual(MinValue, MaxValue, Step, CountrySolarPV5Perc, rangeColors, NumberOfRanges, opacity,FixedLegendRange, ZoneID, SolarCF, False)

    #Solar CLUSTER layer
    SolarCLUSTERColumn = 'join_sol_4'
    SolarRangeList = []
    SolarCLUSTERRangeValues = []

    if FixedLegendRange:
        MinValue = 1
        MaxValue = 10
        Step = 1
        SolarCLUSTERRangeValues = [0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10]
    else:
        MinValue = CountrySolarPVCLUSTER.minimumValue(25)
        MaxValue = CountrySolarPVCLUSTER.maximumValue(25)
        Step = (MaxValue - MinValue)/NumberOfRanges
        SolarCLUSTERRangeValues = [MinValue,MinValue+Step,MinValue+Step,MinValue+2*Step,MinValue+2*Step,MinValue+3*Step,MinValue+3*Step,MinValue+4*Step,MinValue+4*Step,MaxValue]
    
    if NumberOfRanges == 5:
        rangeColors = [QtGui.QColor('#b42a04'),QtGui.QColor('#ff601c'),QtGui.QColor('#f17a30'),QtGui.QColor('#fdae76'),QtGui.QColor('#fdd7b1')]
    if NumberOfRanges == 10:
        rangeColors = [QtGui.QColor('#a6cee3'),QtGui.QColor('#1f78b4'),QtGui.QColor('#b2df8a'),QtGui.QColor('#33a02c'),QtGui.QColor('#fb9a99'),QtGui.QColor('#e31a1c'),QtGui.QColor('#fdbf6f'),QtGui.QColor('#ff7f00'),QtGui.QColor('#cab2d6'),QtGui.QColor('#6a3d9a')]

    LayerLegendGradual(MinValue, MaxValue, Step, CountrySolarPVCLUSTER, rangeColors, NumberOfRanges, opacity,FixedLegendRange, ZoneID, SolarCLUSTERColumn, True)

    #Calculate the MW per range
    if CountryName != 'Burundi' and CountryName != 'Equatorial Guinea' and CountryName != 'Gabon' and CountryName != 'Guinea-Bissau' and CountryName != 'Liberia' and CountryName != 'Rwanda' and CountryName != 'Sierra Leone':
        WindCFRangeMW = TotalMWPerRange(CountryWind5Perc, WindCFRangeValues, False)
        WindCLUSTERRangeMW = TotalMWPerRange(CountryWindCLUSTER, WindCLUSTERRangeValues, True)
    SolarCFRangeMW = TotalMWPerRange(CountrySolarPV5Perc, SolarCFRangeValues, False)
    SolarCLUSTERRangeMW = TotalMWPerRange(CountrySolarPVCLUSTER, SolarCLUSTERRangeValues, True)

#------------------------------------------------------------------------------------------

    #Importing existing layout
    CurrentProject = QgsProject.instance()
    LayoutManager = CurrentProject.layoutManager()
    LayoutName = 'Solar_PV_clusters'
    LayoutName2 = 'Wind_clusters'
    Layouts_list = LayoutManager.printLayouts()

    #Remove any duplicate layout
    for layout in Layouts_list:
        if (layout.name() == LayoutName) or (layout.name() == LayoutName2):
            LayoutManager.removeLayout(layout)

    SolarLayout = QgsPrintLayout(CurrentProject)
    WindLayout = QgsPrintLayout(CurrentProject)


    #Read template layouts
    #Solar
    document = QDomDocument()
    template_file = open(FolderPath +'\Solar_PV_clusters.qpt')
    template_content = template_file.read()
    template_file.close()
    document.setContent(template_content)

    #Load layout from template and add to Layout Manager
    SolarLayout.loadFromTemplate(document, QgsReadWriteContext()) 
    SolarLayout.setName(LayoutName)

    #Wind
    document = QDomDocument()
    template_file = open(FolderPath +'\Wind_clusters.qpt')
    template_content = template_file.read()
    template_file.close()
    document.setContent(template_content)

    #Load layout from template and add to Layout Manager
    WindLayout.loadFromTemplate(document, QgsReadWriteContext()) 
    WindLayout.setName(LayoutName2)

    #Add country name as a title
    title = QgsLayoutItemLabel(SolarLayout)
    title2 = QgsLayoutItemLabel(WindLayout)
    if CountryName == 'MoroccoW':
        title.setText('Morocco')
        title2.setText('Morocco')
    else:
        title.setText(CountryName.upper())
        title2.setText(CountryName.upper())
    Tfont = QFont('Arial', 26)
    Tfont.setBold(True)
    title.setFont(Tfont)
    title2.setFont(Tfont)
    title.adjustSizeToText()
    title2.adjustSizeToText()
    SolarLayout.addLayoutItem(title)
    WindLayout.addLayoutItem(title2)

    title.attemptMove(QgsLayoutPoint(5, 10, QgsUnitTypes.LayoutMillimeters))
    title2.attemptMove(QgsLayoutPoint(5, 10, QgsUnitTypes.LayoutMillimeters))

    #Adding the MW part of the legend
    MWLabel = QgsLayoutItemLabel(SolarLayout)
    MWLabel2 = QgsLayoutItemLabel(WindLayout)
    MWtext = '(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)' % (f"{round(SolarCFRangeMW[0]):,}",f"{round(SolarCFRangeMW[1]):,}",f"{round(SolarCFRangeMW[2]):,}",f"{round(SolarCFRangeMW[3]):,}",f"{round(SolarCFRangeMW[4]):,}",f"{round(SolarCFRangeMW[5]):,}",f"{round(SolarCFRangeMW[6]):,}",f"{round(SolarCFRangeMW[7]):,}",f"{round(SolarCFRangeMW[8]):,}",f"{round(SolarCFRangeMW[9]):,}")
    if CountryName != 'Burundi' and CountryName != 'Equatorial Guinea' and CountryName != 'Gabon' and CountryName != 'Guinea-Bissau' and CountryName != 'Liberia' and CountryName != 'Rwanda' and CountryName != 'Sierra Leone':
        MWtext2 = '(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)' % (f"{round(WindCFRangeMW[0]):,}",f"{round(WindCFRangeMW[1]):,}",f"{round(WindCFRangeMW[2]):,}",f"{round(WindCFRangeMW[3]):,}",f"{round(WindCFRangeMW[4]):,}",f"{round(WindCFRangeMW[5]):,}",f"{round(WindCFRangeMW[6]):,}",f"{round(WindCFRangeMW[7]):,}",f"{round(WindCFRangeMW[8]):,}",f"{round(WindCFRangeMW[9]):,}")
    else:
        MWtext2 = 'None'
    Tfont2 = QFont('Arial', 14)
    MWLabel.setFont(Tfont2)
    MWLabel2.setFont(Tfont2)
    MWLabel.setText(MWtext)
    MWLabel2.setText(MWtext2)
    MWLabel.setFixedSize(QgsLayoutSize(100, 120))
    MWLabel2.setFixedSize(QgsLayoutSize(100, 120))
    SolarLayout.addLayoutItem(MWLabel)
    WindLayout.addLayoutItem(MWLabel2)
    MWLabel.attemptMove(QgsLayoutPoint(40, 61, QgsUnitTypes.LayoutMillimeters))     #CF coordination
    MWLabel2.attemptMove(QgsLayoutPoint(40, 61, QgsUnitTypes.LayoutMillimeters))
    
    #Adding map
    map = QgsLayoutItemMap(SolarLayout)
    map2 = QgsLayoutItemMap(WindLayout)
    map.setRect(30, 30, 30, 30)
    map2.setRect(30, 30, 30, 30)

    # set the map extent
    ms = QgsMapSettings()
    ms.setLayers([CountryBoundary]) # set layers to be mapped
    rect = QgsRectangle(ms.fullExtent())
    rect.scale(1)
    rect.grow(0.3)
    ms.setExtent(rect)
    map.setExtent(rect)
    map2.setExtent(rect)
    map.setFrameEnabled(True)
    map2.setFrameEnabled(True)
    map.setBackgroundColor(QColor(255, 255, 255, 0))
    map2.setBackgroundColor(QColor(255, 255, 255, 0))
    SolarLayout.addLayoutItem(map)
    WindLayout.addLayoutItem(map2)
    
    map.grid().setEnabled(True)
    map2.grid().setEnabled(True)
    map.grid().setCrossLength(2.0)
    map2.grid().setCrossLength(2.0)
    map.grid().setIntervalX(2)
    map2.grid().setIntervalX(2)
    map.grid().setIntervalY(2)
    map2.grid().setIntervalY(2)
    map.grid().setGridLineColor(QColor(0, 0, 0))
    map2.grid().setGridLineColor(QColor(0, 0, 0))
    map.grid().setGridLineWidth(0.3)
    map2.grid().setGridLineWidth(0.3)
    map.grid().setAnnotationEnabled(True)
    map2.grid().setAnnotationEnabled(True)
    map.grid().setGridLineColor(QColor(149, 149, 149))
    map2.grid().setGridLineColor(QColor(149, 149, 149))
    map.grid().setGridLineWidth(0.3)
    map2.grid().setGridLineWidth(0.3)
    
    #map.grid().setAnnotationFont(QgsFontUtils.getStandardTestFont())
    map.grid().setAnnotationFont(QFont('MS Shell Dlg 2', 15))
    map.grid().setAnnotationFormat(3)
    map.grid().setAnnotationPrecision(0)
    map.grid().setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Left)
    map.grid().setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Right)
    map.grid().setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Top)
    map.grid().setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Bottom)
    map.grid().setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Right)
    map.grid().setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Bottom)
    map.grid().setAnnotationFontColor(QColor(0, 0, 0))
    map.grid().setBlendMode(QPainter.CompositionMode_SourceOver)
    map.updateBoundingRect()
    map2.grid().setAnnotationFont(QFont('MS Shell Dlg 2', 15))
    map2.grid().setAnnotationFormat(3)
    map2.grid().setAnnotationPrecision(0)
    map2.grid().setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Left)
    map2.grid().setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Right)
    map2.grid().setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Top)
    map2.grid().setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Bottom)
    map2.grid().setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Right)
    map2.grid().setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Bottom)
    map2.grid().setAnnotationFontColor(QColor(0, 0, 0))
    map2.grid().setBlendMode(QPainter.CompositionMode_SourceOver)
    map2.updateBoundingRect()

    #Position and resize the map (around the manuel indent of (88,10) and W,H (281,246)
    #layout width 383.104 and height 300.109 mm
    #The left legends take 88 mm from the left and the annotation on the right takes 14.04(295.104)
    NewHeight = 246
    NewWidth = 281
    if rect.width() < rect.height():
        NewWidth = 246*rect.width()/rect.height()
    else:
        if rect.width()/rect.height() > 281/246:
            NewHeight = 281*rect.height()/rect.width()
            NewWidth = NewHeight*rect.width()/rect.height()
        elif rect.height()/rect.width() > 246/281:
            NewWidth = 246*rect.width()/rect.height()        
        else:
            NewHeight = 281*rect.height()/rect.width()
    
    xIndent = 88
    yIndent = 10
    if NewWidth < 275 :
        xIndent =  88+(281-NewWidth)/2       #Position in the middle
    if NewHeight < 240 :
        yIndent = 10+(246-NewHeight)/2
    
    map.attemptMove(QgsLayoutPoint(xIndent, yIndent+LongTitleIndent, QgsUnitTypes.LayoutMillimeters))
    map2.attemptMove(QgsLayoutPoint(xIndent, yIndent+LongTitleIndent, QgsUnitTypes.LayoutMillimeters))
    map2.attemptResize(QgsLayoutSize(NewWidth, NewHeight, QgsUnitTypes.LayoutMillimeters))
    map.attemptResize(QgsLayoutSize(NewWidth, NewHeight, QgsUnitTypes.LayoutMillimeters))
           
    #Adding the scalebar
    scalebar = QgsLayoutItemScaleBar(SolarLayout)
    scalebar.setStyle('Single Box')
    scalebar.setUnits(QgsUnitTypes.DistanceKilometers)
    scalebar.setNumberOfSegments(3)
    scalebar.setNumberOfSegmentsLeft(0)
    scalebar.setFixedWidth = 20
    if rect.width() < rect.height():
        scalebar.setUnitsPerSegment(round(30*rect.height()/5))
    else:
        scalebar.setUnitsPerSegment(round(30*rect.width()/5))
    scalebar.setLinkedMap(map)
    scalebar.setUnitLabel('km')
    scalebar.setFont(QFont('Arial', 14))
    scalebar.update()
    SolarLayout.addLayoutItem(scalebar)
    scalebar.attemptMove(QgsLayoutPoint(2, 255, QgsUnitTypes.LayoutMillimeters))
    
    scalebar2 = QgsLayoutItemScaleBar(WindLayout)
    scalebar2.setStyle('Single Box')
    scalebar2.setUnits(QgsUnitTypes.DistanceKilometers)
    scalebar2.setNumberOfSegments(3)
    scalebar2.setNumberOfSegmentsLeft(0)
    scalebar2.setFixedWidth = 20
    if rect.width() < rect.height():
        scalebar2.setUnitsPerSegment(round(30*rect.height()/5))
    else:
        scalebar2.setUnitsPerSegment(round(30*rect.width()/5))
    scalebar2.setLinkedMap(map2)
    scalebar2.setUnitLabel('km')
    scalebar2.setFont(QFont('Arial', 14))
    scalebar2.update()
    WindLayout.addLayoutItem(scalebar2)
    scalebar2.attemptMove(QgsLayoutPoint(2, 255, QgsUnitTypes.LayoutMillimeters))
    
    
    #Adding the layout
    LayoutManager.addLayout(SolarLayout)
    LayoutManager.addLayout(WindLayout)

#------------------------------------------------------------------------------------------

    #Print layout
    exporter1 = QgsLayoutExporter(SolarLayout)
    exporter2 = QgsLayoutExporter(WindLayout)
    
    if CountryName != 'Burundi' and CountryName != 'Equatorial Guinea' and CountryName != 'Gabon' and CountryName != 'Guinea-Bissau' and CountryName != 'Liberia' and CountryName != 'Rwanda' and CountryName != 'Sierra Leone':
        QgsProject.instance().addMapLayer(CountryWind5Perc)
        fn = FolderPath + '\Print\%s_wind_CF%s.pdf' %(CountryName, NameSuffix)
        PDFSettings = QgsLayoutExporter.PdfExportSettings()
        PDFSettings.simplifyGeometries = False
        #PDFSettings.exportMetadata = True
        exporter2.exportToPdf(fn, PDFSettings)
        QgsProject.instance().removeMapLayer(CountryWind5Perc)

    #Replace with solar
    QgsProject.instance().addMapLayer(CountrySolarPV5Perc)
    fn = FolderPath + '\Print\%s_solar_CF%s.pdf' %(CountryName, NameSuffix)
    exporter1.exportToPdf(fn, PDFSettings)
    QgsProject.instance().removeMapLayer(CountrySolarPV5Perc)
    
    #Replace with CLUSTER
    MWtext = '(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)' % (f"{round(SolarCLUSTERRangeMW[0]):,}",f"{round(SolarCLUSTERRangeMW[1]):,}",f"{round(SolarCLUSTERRangeMW[2]):,}",f"{round(SolarCLUSTERRangeMW[3]):,}",f"{round(SolarCLUSTERRangeMW[4]):,}",f"{round(SolarCLUSTERRangeMW[5]):,}",f"{round(SolarCLUSTERRangeMW[6]):,}",f"{round(SolarCLUSTERRangeMW[7]):,}",f"{round(SolarCLUSTERRangeMW[8]):,}",f"{round(SolarCLUSTERRangeMW[9]):,}")
    if CountryName != 'Burundi' and CountryName != 'Equatorial Guinea' and CountryName != 'Gabon' and CountryName != 'Guinea-Bissau' and CountryName != 'Liberia' and CountryName != 'Rwanda' and CountryName != 'Sierra Leone':
        MWtext2 = '(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)\n\n(%s MW)' % (f"{round(WindCLUSTERRangeMW[0]):,}",f"{round(WindCLUSTERRangeMW[1]):,}",f"{round(WindCLUSTERRangeMW[2]):,}",f"{round(WindCLUSTERRangeMW[3]):,}",f"{round(WindCLUSTERRangeMW[4]):,}",f"{round(WindCLUSTERRangeMW[5]):,}",f"{round(WindCLUSTERRangeMW[6]):,}",f"{round(WindCLUSTERRangeMW[7]):,}",f"{round(WindCLUSTERRangeMW[8]):,}",f"{round(WindCLUSTERRangeMW[9]):,}")
    MWLabel.setText(MWtext)
    MWLabel2.setText(MWtext2)
    MWLabel.attemptMove(QgsLayoutPoint(40, 61, QgsUnitTypes.LayoutMillimeters))     #CLUSTER coordination
    MWLabel2.attemptMove(QgsLayoutPoint(40, 61, QgsUnitTypes.LayoutMillimeters))

    if CountryName != 'Burundi' and CountryName != 'Equatorial Guinea' and CountryName != 'Gabon' and CountryName != 'Guinea-Bissau' and CountryName != 'Liberia' and CountryName != 'Rwanda' and CountryName != 'Sierra Leone':
        QgsProject.instance().addMapLayer(CountryWindCLUSTER)
        fn = FolderPath + '\Print\%s_wind_CLUSTER%s.pdf' %(CountryName, NameSuffix)
        exporter2.exportToPdf(fn, PDFSettings)

        QgsProject.instance().removeMapLayer(CountryWindCLUSTER)
    QgsProject.instance().addMapLayer(CountrySolarPVCLUSTER)
    fn = FolderPath + '\Print\%s_solar_CLUSTER%s.pdf' %(CountryName, NameSuffix)
    exporter1.exportToPdf(fn, PDFSettings)
    
    #Clear the map
    QgsProject.instance().removeMapLayer(CountrySolarPVCLUSTER)
    QgsProject.instance().removeMapLayer(CountryBoundary)
    QgsProject.instance().removeMapLayer(CountryTGrid)
    QgsProject.instance().removeMapLayer(CountryDGrid)
    QgsProject.instance().removeMapLayer(CountryRoads)
    QgsProject.instance().removeMapLayer(CountryLakes)
    QgsProject.instance().removeMapLayer(CountryCenterLinesRivers)
    QgsProject.instance().removeMapLayer(CountryMajorCities)
