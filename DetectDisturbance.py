#!/usr/bin/env python


"""
SPA452 Assessment 4 script - Detect forest change project

Tim Hackwood 09/09/2023

Script takes sentinel 2 imagery...
"""



# Imports
import os
os.environ['USE_PYGEOS'] = '0'
#os.environ['GDAL_DATA'] = '/usr/share/gdal/'
import argparse
from datetime import timedelta
from datetime import datetime
from pystac_client import Client 
import numpy as np
import rasterio
import stackstac
import geopandas as gp
import pandas as pd
from osgeo import gdal
import tempfile
import shutil
from shapely.geometry import Polygon
import h3
from tobler.util import h3fy
from rasterstats import zonal_stats
from dask.diagnostics import ProgressBar
gdal.UseExceptions()
gdal.PushErrorHandler('CPLQuietErrorHandler')

def GetCmdArgs():
    """     
    Get the command line arguments.
    """
    p = argparse.ArgumentParser()
    
    p.add_argument("--start", required=True, type=str, 
        help="Date to begin searching Sentinel 2 catalog (yyyy-mm-dd)")
    p.add_argument("--AOI", required=True,
        help="Input AOI polygon (.shp, .gpkg)")
    p.add_argument("--buffer", required=False, default=90, type=int, 
        help="Buffer window (days) to search Sentinel catalog. Default = 90 days, increase or decrease depending on local conditions")
    p.add_argument("--epsg", required=False, default=3577, type=int, 
        help="epsg code for mosaic (optional, defaults to Australian Albers)")
    p.add_argument("--out", required=True,
        help="Output file (.shp, .gpkg)")
    p.add_argument("--ndvi", required=False, 
        help="Write a geotiff with NDVIs create (optional)")
    
    cmdargs = p.parse_args()
    
    return cmdargs

def getHexagons(AOI, resolution, crs):
    
    AOI = AOI.to_crs(f'EPSG:{crs}')
    
    hex = h3fy(AOI.buffer(1000), resolution=resolution) # apply 1km buffer around AOI

    AOI_hex = hex.reset_index ()

    return AOI_hex


def searchSTAC(AOI, dateRange):
    """
    Function searches Sentine 2 stack within a bbox and date range
    """
    
    AOI = AOI.to_crs('EPSG:4283') # need a geographic crs for sentinel
    
    bbox = AOI.total_bounds

    print(f'Will search for imagery between {dateRange} using a bounding box of\n{bbox}')
    
    client = Client.open("https://earth-search.aws.element84.com/v1")
    client.add_conforms_to('ITEM_SEARCH')
    s2Search = client.search(
        collections=['sentinel-2-l2a'],
        datetime = dateRange,
        bbox=bbox,
        query = {'eo:cloud_cover':{'lt':25},
           's2:nodata_pixel_percentage':{'lt': 10}
        }
    )
# Show the results of the search
    print(f"{s2Search.matched()} items found")

    return s2Search.item_collection()

def NDVI(array):
    """
    Function to calculate NDVI over raster and return as a rescaled uint16 array.
    """    
    print('Calulating NDVI...')  
    img = array
    # ignore '0' and Nan pixels to avoid dividing errors
    np.seterr(divide='ignore', invalid='ignore')

    # Assign bands for calculation. Note that these are indexed from 0, not band name.
    NIR = img[1] # NIR
    red = img[0] # red band
    nodata = np.logical_or((NIR == 0) ,(red == 0))
    # calculate ndvi. Must specify floats for decimals
    ndvi = (NIR.astype(float)-red.astype(float))/(NIR.astype(float)+red.astype(float))
    
    ndvi = (10000 + 10000*ndvi).astype(np.uint16) # rescale to uint16
    ndvi[nodata] ==65535 #assign no data
    
    return ndvi

def daterange(date):
    """
    Function takes string date input (yyyy-mm-dd) and returns a date rage of 3 months to input in stac query.
    """
   
    window = (date - timedelta(days=90)).strftime("%Y-%m-%d")
    
    date=date.strftime("%Y-%m-%d")
    
    daterange = f"{window}/{date}"
    
    return daterange

def mosaic(tiles, AOI, epsg):
    """
    Function to mosaic tiles returned from Sentinel query and return median across time dimenstion. Also returns affine for zone stats
    """
    AOI = AOI.to_crs('EPSG:4283')
    
    bbox = AOI.total_bounds
    
    data = (
    stackstac.stack(
        tiles,
        assets=['red', 'nir'],  # red, green, blue, NIR
        chunksize=4096,
        epsg=epsg, # project to Australian albers 
        bounds_latlon=bbox, # clip to AOI extent
        resolution=100
    )
    .where(lambda x: x > 0, other=np.nan)  # sentinel-2 uses 0 as nodata
    
    )
       
    print(f'Making cloud-free mosaic for {daterange}')
    
    with ProgressBar():

        median = data.median(dim="time").compute() # Compute median of all pixels across time series, assumming clouds are transient
    
    affine = data.transform
    
    medarray = median.values
    
    return medarray, affine

def zonestats(polygons, array, affine):
    """
    Function to calculate zone stats using an aoi and array
    """
    array = array
    affine = affine
    
    stats = zonal_stats(polygons, array, nodata=65535, stats='mean', affine=affine)
    
    outstats = polygons.join(pd.DataFrame(stats))
    
    return outstats

def noTiff(AOI, daterange, buffer, epsg):
    """
    Option for no NDVI tiffs
    """
  
    stack = searchSTAC(AOI, daterange)
    
    array, affine = mosaic(stack, AOI, epsg)
    
    ndvi = NDVI(array)
    
    outstats = zonestats(AOI, ndvi, affine)
        
    return outstats


def main():
    
    """
    Main function.
    """
    cmdargs = GetCmdArgs()
    
    tempDir = tempfile.mkdtemp(prefix='zstats')
    
    poly = gp.read_file(cmdargs.AOI)
    
    hex = getHexagons(poly, 8, cmdargs.epsg)

    # Work out dates 12 months apart
    firstdate = datetime.strptime(cmdargs.start, '%Y-%m-%d')
    
    oneyear = firstdate - timedelta(weeks=52)
    
    daterange1 = daterange(oneyear)
    
    daterange2 = daterange(firstdate)

    print(f'Query daterage: {daterange2}, reference daterage: {daterange1}')
    
    try:
        if cmdargs.ndvi is not None:
            print('making ndvi')
            
    except:
        print("No NDVI tiff will be exported")

    else:
        
        print('Processing reference date...')
        
        outpoly1 = noTiff(hex, daterange1, cmdargs.buffer, cmdargs.epsg)
        
        hex[f'{oneyear.year}_{oneyear.month}_mean'] = outpoly1['mean']  
        
        print('Processing analysis date...')
        
        outpoly2 = noTiff(hex, daterange2, cmdargs.buffer, cmdargs.epsg)
        
        colname = f'{firstdate.year}_{firstdate.month}_mean'
        
        hex[f'{firstdate.year}_{firstdate.month}_mean'] = outpoly2['mean']
        
        hex.to_file(f'{cmdargs.out}')
        
    finally: 

        shutil.rmtree(tempDir)

if __name__ == '__main__':
    main()