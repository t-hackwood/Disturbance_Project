# Detect Disturbance

03/10/2023
Author: Tim Hackwood

https://github.com/t-hackwood/Disturbance_Project

OVERVIEW:

Detect Disturbance detects significant vegetation disturbances over any given AOI for a specified date range. 

The script utilises a cloudless mosaic of Sential 2 imagery from the Spatio Temporal Asset Catalog (STAC) and the H3 Hexagon library 
to determine the mean NDVI for each hexagon grid cell over an AOI, for two dates. The values for each hexagon are then compared
with trigger value to determin if any significant disturbances have been detected. The H3 Hexagon grid system is utilised for
the way that hexagons follow the outlines of natural features, and becuase the hexagon labels are stable, meaning that future 
analyses over the same AOI can be easily joined. 

Usual caveats for NDVI analysis apply. Primarily, this means that this script will only detect significant loss in photosynthetic
vegetation (i.e., complete clearing to bare soil) and as such, a clearing event that has regnerated pasture in high rainfall areas
may not be triggerd. The trigger parameter can be adjusted to suit your specific usecase. Alternatively, the mean NDVIs within each hexagon grid cell can be visualised in a GIS.

ENVIORNMENT: 

A conda environment with the required libraries can be created and activated with the following commands:

conda create --name "MyEnv" python pystac-client rasterio stackstac geopandas pandas gdal h3-py tobler rasterstats dask

conda activate "MyEnv"

USAGE:

Script is run through a terminal, in the environment created above. To access help menu use: 

python DetectDisturbance.py -h

The example test area over Tennant Creek, Northern Teritory, Australia can be run using:

python DetectDisturbance.py --date 2023-09-25 --AOI /home/thackwood/uni/Disturbance_Project/TennantCreek_example/tennantCreek.shp  --out /home/thackwood/uni/out/tennantCk.gpkg

Where:

--date (mandatory) = 	The date you would like to run your analysis from (i.e., after a suspected disturbance event). Must be yyyy-mm-dd

--AOI (mandatory) = 	Your AOI as a single polygon. Can be any Geopandas compatible file (e.g., .shp, .json, .gpkg)

--out (mandatory) = 	The full file path where you would like to write your output hexagons. Can be any Geopandas compatable file as above

However, you can optionally include the following parameters as below:  

python DetectDisturbance.py --date 2023-09-25 --AOI /home/thackwood/uni/Disturbance_Project/TennantCreek_example/tennantCreek.shp  --out /home/thackwood/uni/out/tennantCk.gpkg  --ndvi /home/thackwood/uni/out/tennantCk.tif --buffer 30 --trigger 2500 --weeks 12 --epsg 3577

Where:

--ndvi (optional) = 	The fulll file path where you can optionally write a tiff file of the NDVIs used to detect disturbance. Optional argument,
			default is no tiff output however, this option is helpful if you suspect erroneous data from clouds etc.
					
--buffer (optional) = 	Buffer date range you would like to sample Sentinel 2 imagery. Optional argument, default is 90 days. For cloudy regions/
			periods, you may like to increase this to include more tiles to mosaic but this will increase compute time. NOTE: Sentinel
			images are filtered to only include less than 5% cloud cover however, enough images to make a completly cloudless mosaic
			of the study area is requred for best results. 
					  
--trigger (optional) =	The NDVI drop you would like to trigger a detected disturbance. Optional argument, default is 2500 (or 0.25 in typical NDVI).
			You may like to adjust this depending on your specific use case. NDVI values are scaled using the formula 1000+1000*NDVI, so
			typical -1 to 1 values are now 0-20000 in the output. 

--weeks (optional) =	How many weeks prior to your analysis date you would like to search Sentinel 2 to compare with. Optional argument, default is
			52 weeks. This can be adjusted to suit your specific region. For example, comparing the disturbance from fure to the 
			conditions 1 year prior to the analysis date yeilds very differnt results to those of only 3 months prior.

--epsg (optional) = 	The CRS (as an EPSG number) you would like to expor your final results as. Optional argument, default is Australian Albers
			(EPSG:3577). NOTE: A projected, equal area CRS is reccomended for best results.
						
OUTPUTS:

The vector output is a grid of hexagons covering the extent of your AOI plus a 1km buffer. Output fields are:

		- Hexagon ID
		- NDVI value for the reference date
		- NDVI value for the analysis date
		- Disturbance triggered where 1 = triggered, 0 = not triggered

The optional raster output is a 2 band Uint16 geotiff, covering the extent of the hexagon grid with a 100m cell size. Band 1 is the reference date, Band
2 is the analysis date. 
