#!/usr/bin/env python


"""
SPA452 Assessment 4 script - Detect forest change project

Tim Hackwood 09/09/2023

Script takes sentinel 2 imagery...
"""

# Imports
import warnings
warnings.filterwarnings("ignore")
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
    
    p.add_argument("--date", required=True, type=str, 
        help="Date to begin searching Sentinel 2 catalog (yyyy-mm-dd)")
    p.add_argument("--weeks", required=False, default=52, type=int, 
        help="Gap in weeks between analysis date and reference date. Default = 52 weeks")
    p.add_argument("--AOI", required=True,
        help="Input AOI polygon (.shp, .gpkg)")
    p.add_argument("--buffer", required=False, default=90, type=int, 
        help="Buffer window (days) to search Sentinel catalog. Default = 90 days, increase or decrease depending on local conditions")
    p.add_argument("--epsg", required=False, default=3577, type=int, 
        help="epsg code for mosaic (optional, defaults to Australian Albers)")
    p.add_argument("--out", required=True,
        help="Output file (.shp, .gpkg)")
    p.add_argument("--ndvi", required=False, 
        help="file to write a geotiff with NDVIs create (.tif, optional)")
    p.add_argument("--trigger", required=False, default=2500, type=int, 
        help="Option to set trigger value for NDVI drop scaled to 16 bit integer (0-20000). Default is 2500 (0.25 in traditional NDVI)")
    
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

    #print(f'Will search for imagery between {dateRange} using a bounding box of\n{bbox}')
    
    client = Client.open("https://earth-search.aws.element84.com/v1")
    client.add_conforms_to('ITEM_SEARCH')
    s2Search = client.search(
        collections=['sentinel-2-l2a'],
        datetime = dateRange,
        bbox=bbox,
        query = {'eo:cloud_cover':{'lt':5},
        }
    )

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

    mask = np.logical_or((NIR == 0) ,(red == 0))
        
    # calculate ndvi. Must specify floats for decimals
    ndvi = (NIR.astype(float)-red.astype(float))/(NIR.astype(float)+red.astype(float))
    # Mask any nodata
    ndvi[mask] = np.nan
    
    # Assign final nodata value
    ndvi = np.nan_to_num(ndvi, nan=65535)    
    
    ndvi = (10000 + 10000*ndvi) # rescale to uint16 
    
    ndvi = np.nan_to_num(ndvi, nan=65535).astype(np.uint16)
          
    return ndvi

def daterange(date, buffer):
    """
    Function takes string date input (yyyy-mm-dd) and returns a date rage of 3 months to input in stac query.
    """
   
    window = (date - timedelta(days=buffer)).strftime("%Y-%m-%d")
    
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
        chunksize='auto',
        epsg=epsg, # project to Australian albers 
        bounds_latlon=bbox, # clip to AOI extent
        gdal_env=stackstac.DEFAULT_GDAL_ENV.updated(
                               {'GDAL_HTTP_MAX_RETRY': 3,
                                'GDAL_HTTP_RETRY_DELAY': 5,
                               }),
        resolution=100
    )
    .where(lambda x: x > 0, other=np.nan)  # sentinel-2 uses 0 as nodata
    
    )
    
    print(f'Making cloud-free mosaic...')
    
    with ProgressBar():

        median = data.median(dim="time", skipna=True).compute() # Compute median of all pixels across time series, assumming clouds are transient
    
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

def notiffpipe(AOI, daterange, buffer, epsg):
    """
    Pipe to run process but not export NDVI tiffs
    """
  
    stack = searchSTAC(AOI, daterange)
    
    print(f'{len(stack)} tiles found date range {daterange}')
    
    array, affine = mosaic(stack, AOI, epsg)
    
    ndvi = NDVI(array)
    
    outstats = zonestats(AOI, ndvi, affine)
        
    return outstats

def tiffpipe(AOI, daterange, buffer, epsg):
    """
    Pipe to run process exporting NDVI tiffs
    """
  
    stack = searchSTAC(AOI, daterange)
    
    print(f'{len(stack)} tiles found date range {daterange}')
    
    array, affine = mosaic(stack, AOI, epsg)
    
    ndvi = NDVI(array)
    
    outstats = zonestats(AOI, ndvi, affine)
        
    return outstats, ndvi, affine

def cloudtest(AOI, daterange1, daterange2):
    
    stack = searchSTAC(AOI, daterange1)
    
    stack2 = searchSTAC(AOI, daterange2)
        
    cloud = min(len(stack), len(stack2))
    
    return cloud

def main():
    
    """
    Main function.
    """
    cmdargs = GetCmdArgs()
    
    tempDir = tempfile.mkdtemp(prefix='zstats')
    
    poly = gp.read_file(cmdargs.AOI)
    
    hex = getHexagons(poly, 8, cmdargs.epsg)

    # Work out dates 12 months apart
    firstdate = datetime.strptime(cmdargs.date, '%Y-%m-%d')
    
    oneyear = firstdate - timedelta(weeks=cmdargs.weeks)
    
    daterange1 = daterange(oneyear, cmdargs.buffer)
    
    daterange2 = daterange(firstdate, cmdargs.buffer)

    print(f'Analysis date range: {daterange2}, reference date range: {daterange1}')
    
    print('Checking for valid Sentinel 2 tiles...')
    
    tiles = cloudtest(hex, daterange1, daterange2)
        
    if tiles < 20: 
        raise SystemExit('Not enough tiles for mosaic, try increasing date buffer argument')
     
    # Process dates with tiff output pipeline
          
    elif cmdargs.ndvi is not None:
        
        print('Processing reference date...')
        
        outpoly1, ndvi1, affine = tiffpipe(hex, daterange1, cmdargs.buffer, cmdargs.epsg)
        
        hex[f'{oneyear.year}_{oneyear.month}_mean'] = outpoly1['mean']  
        
        print('Processing analysis date...')
        
        outpoly2, ndvi2, affine = tiffpipe(hex, daterange2, cmdargs.buffer, cmdargs.epsg)
        
        stack = np.stack((ndvi1, ndvi2), axis=0)
        
        print(f'Writing {cmdargs.ndvi}...')
        
        with rasterio.open(f'{cmdargs.ndvi}',
            "w",
            driver='GTiff',
            height=stack.shape[1],
            width=stack.shape[2],
            count=stack.shape[0],
            dtype='uint16', 
            crs= cmdargs.epsg,
            transform= affine,
            nodata=65535) as dst:
            dst.write(stack, [1, 2])
        
            colname = f'{firstdate.year}_{firstdate.month}_mean'
        
            hex[f'{firstdate.year}_{firstdate.month}_mean'] = outpoly2['mean']
    
    # Process dates without exporting tiffs
                      
    else:
        
        print('Processing reference date...')
        
        outpoly1 = notiffpipe(hex, daterange1, cmdargs.buffer, cmdargs.epsg)
        
        hex[f'{oneyear.year}_{oneyear.month}_mean'] = outpoly1['mean']  
        
        print('Processing analysis date...')
        
        outpoly2 = notiffpipe(hex, daterange2, cmdargs.buffer, cmdargs.epsg)
        
        colname = f'{firstdate.year}_{firstdate.month}_mean'
        
        hex[f'{firstdate.year}_{firstdate.month}_mean'] = outpoly2['mean']
        
    # Set trigger for disturbance and apply to geodataframe
    
    hex['trigger'] = hex[f'{oneyear.year}_{oneyear.month}_mean'] - cmdargs.trigger
    
    hex['Disturbance'] = 0

    hex.loc[hex[f'{firstdate.year}_{firstdate.month}_mean'] < hex['trigger'], 'Disturbance'] = 1
    
    hex = hex.drop(columns= ['trigger'])
    
    area = int(hex['Disturbance'].sum() * 73.73)
    
    print(f'Approximately {area} hectares triggered for disturbance')
    
    hex.to_file(f'{cmdargs.out}')
     
    
if __name__ == '__main__':
    main()