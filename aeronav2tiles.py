#!/usr/bin/env python3
'''
This script downloads Aeronav raster images from the FAA, unzips them, and
processes them into a format suitable for use with a web map tile server.

Command Line Arguments:
    --zippath: Specify the directory to store downloaded Aeronav data.
    --tmppath: Specify the directory to store temporary files (default: /tmp/aeronav2tiles).
    --outpath: Specify the directory to store the output tilesets.
    --download: Download the current data into zippath from aeronav.faa.gov before processing.
    --all: Generate all tilesets.
    --tilesets: Specify the tilesets to generate.
    --existing: [DEVELOPMENT] Use existing reprojected datasets.
    --resampling: Specify the resampling method to use when reprojecting the data (default: nearest). Can be one of nearest, bilinear, cubic, cubicspline, lanczos, average, mode.
    --cleanup: Remove the temporary directory and its contents after processing.

Usage:
    python aeronav2tiles.py --current --zippath /path/to/zips --tmppath /path/to/tmp --outpath /path/to/output --resampling bilinear --cleanup

Raises:
    ValueError: If no projection information is found in the dataset.
'''

import argparse
import math
import os
import re
import shutil
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import bs4
import numpy
import osgeo_utils.gdal2tiles
import rasterio.features
import rasterio.warp

'''
Dictionary of FAA Aeronav datasets.

Key: Dataset name.
Value: Dictionary of dataset information.
    input_file: str
        Name of the input file for this dataset.
        Multiple datasets may share a single file, in the case of a file with one or more insets.

    window: tuple (xoff, yoff, width, height)
        Rectangular window, in pixel coordinates, containing valid map data. This is used as an
        initial (and fast) crop to remove any extraneous data around the edges of the image.

    masks: [optional] list of shapes, each shape represented as a list of (x, y) tuples
        List of masks to apply to the dataset. This is used to further crop non-map data from the
        image, such as insets, legends, and other extraneous information.

    geobound: [optional] tuple (longitude_min, latitude_min, longitude_max, latitude_max)
        Geographic bounds of the dataset. Some datasets are not bounded by straight lines or
        shapes, but by latitude and/or longitude lines. This cropping must happen post-reprojection.
        Any of the four values can be None, indicating no bound in that direction.

    gcps: [optional] list of (x, y, lon, lat) tuples
        List of GCPs to apply to the dataset. Normally the GeoTIFF's built-in georeferencing is
        used, but for insets, we must manually re-georeference the image.

'''
datasets = {
    "Alaska Wall Planning Chart British Columbia Coast Inset": {
        "input_file": "Alaska Wall Planning Chart.tif",
        "window": (14778, 170, 2918, 7417),
        "gcps": [(14830, 356, -135, 55), (16322, 1481, -130, 55), (15198, 3098, -130, 52), (16905, 4174, -125, 52), (16239, 5304, -125, 50)],
    },
    "Alaska Wall Planning Chart": {
        "window": (81, 101, 17684, 12284),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[1371,4970], [2875,4969], [2875,7972], [1371,7973], [1371,4970]]] ,[[[2928,6658], [4278,6658], [4537,7445], [4535,7513], [2928,7513], [2928,6658]]] ,[[[14691,101], [17765,101], [17765,7674], [14691,7674], [14691,101]]] ,[[[8873,10432], [15041,10432], [15041,12385], [8873,12385], [8873,10432]]]] } ],
    },
    "Alaska Wall Planning Chart Western Aleutian Islands Inset": {
        "input_file": "Alaska Wall Planning Chart.tif",
        "window": (8961, 10521, 5993, 1793),
        "gcps": [(9321, 10705, 173, 53), (10099, 10772, 175, 53), (10880, 10818, 177, 53), (11661, 10840, 179, 53), (12444, 10840, -179, 53), (13225, 10818, -177, 53), (14005, 10773, -175, 53), (14784, 10705, -173, 53), (9190, 11995, 173, 51), (10006, 12066, 175, 51), (10823, 12113, 177, 51), (11642, 12136, 179, 51), (12462, 12136, -179, 51), (13281, 12113, -177, 51), (14098, 12066, -175, 51), (14914, 11995, -173, 51)],
    },
    "Albuquerque SEC": {
        "window": (2202, 34, 15698, 11308),
        "geobound": (-109, 32, None, None),
    },
    "Anchorage SEC": {
        "window": (1743, 61, 14877, 11160),
        "geobound": (-151.5, 60, None, None),
    },
    "Anchorage TAC": {
        "window": (3158, 97, 8881, 5810),
    },
    "Atlanta FLY": {
        "window": (1630, 188, 7419, 6960),
    },
    "Atlanta SEC": {
        "window": (2200, 934, 15723, 11176),
        "geobound": (-88, 32, None, None),
    },
    "Atlanta TAC": {
        "window": (1546, 555, 7421, 6962),
    },
    "Baltimore HEL": {
        "window": (3561, 526, 5932, 5544),
    },
    "Baltimore-Washington FLY": {
        "window": (1614, 187, 10413, 8414),
    },
    "Baltimore-Washington TAC": {
        "window": (1612, 184, 10410, 8414),
    },
    "Bethel SEC": {
        "window": (1073, 31, 15593, 12042),
        "geobound": (-173, None, None, None),
    },
    "Billings SEC": {
        "window": (2169, 6, 15701, 12101),
        "geobound": (-109, None, None, None),
    },
    "Boston Downtown HEL": {
        "window": (1549, 293, 7404, 8242),
    },
    "Boston HEL": {
        "window": (1528, 299, 7406, 8242),
    },
    "Boston TAC": {
        "window": (1614, 316, 8908, 8456),
    },
    "Brownsville SEC": {
        "window": (1457, 4, 15145, 11301),
        "geobound": (-103, 24, None, None),
    },
    "Cape Lisburne SEC": {
        "window": (1974, 12, 14586, 11417),
        "geobound": (-171.5, 68, None, None),
    },
    "Caribbean 1 VFR Chart": {
        "window": (1763, 0, 16190, 15240),
        "geobound": (None, 16, None, None),
    },
    "Caribbean 2 VFR Chart": {
        "window": (1975, 0, 15839, 11003),
        "geobound": (None, 14, None, None),
    },
    "Charlotte FLY": {
        "window": (1642, 240, 7401, 6865),
    },
    "Charlotte SEC": {
        "window": (1358, 40, 15342, 11275),
        "geobound": (-82, 32, None, None),
    },
    "Charlotte TAC": {
        "window": (1563, 519, 7401, 6864),
    },
    "Cheyenne SEC": {
        "window": (1620, 0, 16299, 12139),
        "geobound": (-109, 40, None, None),
    },
    "Chicago FLY": {
        "window": (1535, 145, 5898, 5386),
    },
    "Chicago HEL": {
        "window": (1529, 58, 11906, 8812),
    },
    "Chicago O'Hare Inset HEL": {
        "window": (145, 177, 3541, 3883),
    },
    "Chicago SEC": {
        "window": (1640, 841, 16284, 11219),
        "geobound": (-93, 40, None, None),
    },
    "Chicago TAC": {
        "window": (1546, 536, 5897, 5386),
    },
    "Cincinnati FLY": {
        "window": (1549, 147, 7407, 8315),
    },
    "Cincinnati SEC": {
        "window": (2850, 6, 15060, 11364),
        "geobound": (-85, 36, None, None),
    },
    "Cincinnati TAC": {
        "window": (1551, 316, 7402, 8314),
    },
    "Cleveland TAC": {
        "window": (1669, 565, 5909, 5391),
    },
    "Cold Bay SEC": {
        "window": (1623, 4, 14937, 6083),
        "geobound": (-164, None, None, None),
    },
    "Colorado Springs TAC": {
        "window": (3189, 59, 8240, 7645),
    },
    "Dallas-Ft Worth FLY": {
        "window": (1608, 127, 10277, 8468),
    },
    "Dallas-Ft Worth HEL": {
        "window": (1524, 122, 11857, 8713),
    },
    "Dallas-Ft Worth SEC": {
        "window": (2210, 7, 15712, 11306),
        "geobound": (-102, 32, None, None),
    },
    "Dallas-Ft Worth TAC": {
        "window": (1607, 127, 10277, 8468),
    },
    "Dallas-Love Inset HEL": {
        "window": (1992, 371, 5515, 5573),
    },
    "Dawson SEC": {
        "window": (1470, 0, 15113, 11197),
        "geobound": (-145, 64, None, None),
    },
    "Denver FLY": {
        "window": (1597, 325, 7408, 6896),
    },
    "Denver SEC": {
        "window": (1501, 16, 15114, 11854),
        "geobound": (-111, None, None, None),
    },
    "Denver TAC": {
        "window": (1543, 320, 7409, 6896),
    },
    "Detroit FLY": {
        "window": (3071, 232, 7342, 6402),
    },
    "Detroit HEL": {
        "window": (3275, 63, 10156, 8781),
    },
    "Detroit SEC": {
        "window": (1622, 0, 16303, 11336),
        "geobound": (-85, 40, None, None),
    },
    "Detroit TAC": {
        "window": (3082, 84, 7340, 6402),
    },
    "Downtown Manhattan HEL": {
        "window": (1578, 232, 4271, 3958),
    },
    "Dutch Harbor SEC": {
        "window": (1340, 8, 15233, 11283),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[5048,351], [7428,351], [7428,4041], [5048,4041], [5048,351]]] ,[[[10193,332], [13652,332], [13652,3541], [10193,3541], [10193,332]]]] } ],
        "geobound": (-173, 52, None, None),
    },
    "Dutch Harbor SEC Dutch Harbor Inset": {
        "input_file": "Dutch Harbor SEC.tif",
        "window": (10288, 427, 3268, 3018),
        "gcps": [(10356, 470, -167, 54.25), (11896, 475, -166.5, 54.25), (13435, 470, -166, 54.25), (10347, 1785, -167, 54), (11896, 1790, -166.5, 54), (13444, 1785, -166, 54), (10337, 3099, -167, 53.75), (11896, 3104, -166.5, 53.75), (13454, 3099, -166, 53.75)],
    },
    "Dutch Harbor SEC Pribilof Islands Inset": {
        "input_file": "Dutch Harbor SEC.tif",
        "window": (5142, 445, 2192, 3500),
        "gcps": [(5411, 884, -170.5, 57.5), (6119, 886, -170, 57.5), (6827, 884, -169.5, 57.5), (5401, 2199, -170.5, 57), (6119, 2202, -170, 57), (6836, 2199, -169.5, 57), (5391, 3515, -170.5, 56.5), (6119, 3518, -170, 56.5), (6848, 3515, -169.5, 56.5)],
    },
    "Eastern Long Island HEL": {
        "window": (3267, 220, 6893, 3906),
    },
    "El Paso SEC": {
        "window": (1669, 12, 14876, 11273),
        "geobound": (-109, 28, None, None),
    },
    "ENR_A01_ATL": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A01_DCA": {
        "window": (125, 205, 9751, 7591),
    },
    "ENR_A01_DET": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A01_JAX": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A01_MIA": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A01_MSP": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A01_STL": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A02_DEN": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A02_DFW": {
        "window": (125, 205, 5751, 7591),
    },
    "ENR_A02_LAX": {
        "window": (125, 145, 7751, 3671),
    },
    "ENR_A02_MKC": {
        "window": (125, 145, 3751, 3671),
    },
    "ENR_A02_ORD": {
        "window": (125, 205, 3751, 7591),
    },
    "ENR_A02_PHX": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_A02_SFO": {
        "window": (125, 205, 3751, 3671),
    },
    "ENR_AKH01": {
        "window": (2200, 263, 19590, 7471),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[12856,4274], [21790,4272], [21790,7735], [12856,7735], [12856,4274]]]] } ],
    },
    "ENR_AKH01_SEA": {
        "window": (128, 127, 8728, 3250),
    },
    "ENR_AKH02": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_AKL01": {
        "window": (2205, 274, 19583, 7461),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[7743,4020], [12261,4021], [12261,7735], [7743,7735], [7743,4020]]] ,[[[15188,4216], [21788,4216], [21788,7735], [15187,7735], [15188,4216]]]] } ],
    },
    "ENR_AKL01_JNU": {
        "window": (85, 153, 4171, 3531),
    },
    "ENR_AKL01_VR": {
        "window": (109, 145, 6431, 3349),
    },
    "ENR_AKL02C": {
        "window": (89, 105, 4448, 7470),
    },
    "ENR_AKL02E": {
        "window": (89, 105, 4450, 7471),
    },
    "ENR_AKL02W": {
        "window": (96, 111, 4466, 7465),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[96,111], [1402,111], [1402,4019], [96,4019], [96,111]]]] } ],
    },
    "ENR_AKL03": {
        "window": (6085, 265, 15711, 7470),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[6085,265], [7485,265], [7485,4084], [6085,4084], [6085,265]]]] } ],
    },
    "ENR_AKL03_FAI": {
        "window": (86, 105, 5110, 3649),
    },
    "ENR_AKL03_OME": {
        "window": (85, 85, 3711, 3651),
    },
    "ENR_AKL04": {
        "window": (6529, 269, 15262, 7463),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[6529,269], [10102,269], [10102,3810], [8947,3810], [6529,7239], [6529,269]]]] } ],
    },
    "ENR_AKL04_ANC": {
        "window": (209, 269, 7713, 7462),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[6853,3631], [7922,3630], [7922,7730], [4351,7730], [4351,7183], [6853,3631]]]] } ],
    },
    "ENR_CA01_ANTIGUA": {
        "window": (22, 124, 5695, 2621),
    },
    "ENR_CA01_BUENOS": {
        "window": (77, 130, 2695, 2615),
    },
    "ENR_CA01_GUAT": {
        "window": (68, 126, 3409, 2623),
    },
    "ENR_CA01_LIMA": {
        "window": (96, 130, 2695, 2621),
    },
    "ENR_CA01_RIO": {
        "window": (113, 130, 2695, 2617),
    },
    "ENR_CA01_SANTIAGO": {
        "window": (122, 130, 2695, 2620),
    },
    "ENR_CA02_BOGOTA": {
        "window": (123, 100, 2695, 2621),
    },
    "ENR_CA02_CENT-PAC": {
        "window": (105, 100, 4189, 5583),
    },
    "ENR_CA02_MEX": {
        "window": (100, 123, 2695, 2624),
    },
    "ENR_CA02_MIA-NAS": {
        "window": (92, 146, 4177, 2622),
    },
    "ENR_CA02_PANAMA": {
        "window": (68, 105, 4177, 2642),
    },
    "ENR_CA03_MEX_BORDER": {
        "window": (102, 126, 2799, 5497),
    },
    "ENR_CA03_PR": {
        "window": (77, 176, 8696, 5507),
    },
    "ENR_CH01": {
        "window": (1652, 184, 11695, 5619),
    },
    "ENR_CH02": {
        "window": (1652, 197, 11695, 5606),
    },
    "ENR_CH07": {
        "window": (1652, 184, 11695, 5619),
    },
    "ENR_CH08": {
        "window": (1652, 197, 11695, 5606),
    },
    "ENR_CL01": {
        "window": (1652, 189, 11695, 5614),
    },
    "ENR_CL02": {
        "window": (1652, 173, 11695, 5630),
    },
    "ENR_CL03": {
        "window": (1652, 197, 11695, 5606),
    },
    "ENR_CL05": {
        "window": (1652, 197, 11695, 5604),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[8599,199], [13347,199], [13347,2262], [10095,2262], [10095,2093], [8599,1293], [8599,199]]]] } ],
    },
    "ENR_CL05 Charleston-Bermuda Inset": {
        "input_file": "ENR_CL05.tif",
        "window": (8702, 200, 4644, 1985),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[8702,1251], [10190,2056], [10190,2185], [8702,2185], [8702,1251]]]] } ],
        "gcps": [(8797, 432, -82, 33), (11020, 316, -72, 33), (13245, 255, -62, 33), (9067, 1193, -81, 30), (11051, 1092, -72, 30), (13256, 1031, -62, 30), (9988, 1889, -77, 27), (11737, 1814, -69, 27), (13269, 1777, -62, 27)],
    },
    "ENR_CL06": {
        "window": (1652, 182, 11695, 5640),
    },
    "ENR_H01": {
        "window": (2204, 205, 21691, 7590),
    },
    "ENR_H02": {
        "window": (2105, 205, 19791, 7591),
    },
    "ENR_H03": {
        "window": (2206, 105, 21690, 7691),
    },
    "ENR_H04": {
        "window": (204, 105, 21691, 7630),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[204,3054], [2364,3054], [2364,7735], [204,7735], [204,3054]]]] } ],
    },
    "ENR_H05": {
        "window": (2105, 105, 19791, 7689),
    },
    "ENR_H06": {
        "window": (2105, 105, 19791, 7689),
    },
    "ENR_H07": {
        "window": (2205, 105, 19690, 7631),
    },
    "ENR_H08": {
        "window": (2105, 105, 19791, 7631),
    },
    "ENR_H09": {
        "window": (2205, 105, 19691, 7691),
    },
    "ENR_H10": {
        "window": (2105, 105, 19691, 7691),
    },
    "ENR_H11": {
        "window": (4105, 205, 17691, 7591),
    },
    "ENR_H12": {
        "window": (104, 105, 23791, 7691),
    },
    "ENR_L01": {
        "window": (2204, 265, 19588, 7469),
    },
    "ENR_L02": {
        "window": (2206, 266, 17589, 7470),
    },
    "ENR_L03": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L04": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L05": {
        "window": (6205, 264, 15591, 7471),
    },
    "ENR_L06N": {
        "window": (205, 264, 13591, 7471),
    },
    "ENR_L06S": {
        "window": (205, 264, 7591, 7471),
    },
    "ENR_L07": {
        "window": (2204, 265, 19591, 7471),
    },
    "ENR_L08": {
        "window": (2204, 265, 17591, 7471),
    },
    "ENR_L09": {
        "window": (2205, 265, 19589, 7471),
    },
    "ENR_L10": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L11": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L12": {
        "window": (4203, 263, 17594, 7472),
    },
    "ENR_L13": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L14": {
        "window": (4205, 265, 17589, 7471),
    },
    "ENR_L15": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L16": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L17": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L18": {
        "window": (4205, 265, 19591, 7471),
    },
    "ENR_L19": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L20": {
        "window": (2205, 265, 17591, 7471),
    },
    "ENR_L21": {
        "window": (2205, 265, 15591, 7471),
    },
    "ENR_L22": {
        "window": (205, 265, 21591, 7471),
    },
    "ENR_L23": {
        "window": (5185, 265, 16611, 7471),
    },
    "ENR_L23 Wilmington-Bimini Inset": {
        "input_file": "ENR_L23.tif",
        "window": (2205, 265, 2769, 7471),
        "gcps": [(2398, 461, -81, 35), (3655, 291, -79, 35), (4401, 958, -78, 34), (2683, 2760, -81, 32), (3337, 2675, -80, 32), (4643, 2484, -78, 32), (2552, 7488, -82, 26), (3963, 7312, -80, 26), (4667, 7213, -79, 26)],
    },
    "ENR_L24": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L25": {
        "window": (2204, 265, 17591, 7471),
    },
    "ENR_L26": {
        "window": (204, 262, 19591, 7474),
    },
    "ENR_L27": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L28": {
        "window": (2205, 265, 17591, 7471),
    },
    "ENR_L29": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L30": {
        "window": (2205, 265, 19591, 7471),
    },
    "ENR_L31": {
        "window": (4205, 265, 17591, 7471),
    },
    "ENR_L32": {
        "window": (4205, 265, 19591, 7471),
    },
    "ENR_L33": {
        "window": (2206, 265, 15590, 7466),
    },
    "ENR_L34": {
        "window": (205, 260, 23591, 7472),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[21595,260], [23796,260], [23796,4641], [22344,4254], [21738,3121], [21595,260]]]] } ],
    },
    "ENR_L34 Boston-Yarmouth Inset": {
        "input_file": "ENR_L34.tif",
        "window": (21827, 274, 1971, 4151),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[21827,274], [21969,3082], [22502,4080], [23798,4425], [21827,4425], [21827,274]]]] } ],
        "gcps": [(22662, 874, -70, 42), (23666, 1207, -70, 43), (22436, 1608, -69, 42), (23424, 1932, -69, 43), (21969, 3082, -67, 42), (22965, 3387, -67, 43)],
    },
    "ENR_L35": {
        "window": (2205, 265, 15589, 7466),
    },
    "ENR_L36": {
        "window": (205, 265, 21590, 7466),
    },
    "ENR_P01": {
        "window": (2204, 254, 15587, 7479),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[2204,4765], [5161,4765], [5161,6436], [7001,6436], [7001,7733], [2204,7733], [2204,4765]]]] } ],
    },
    "ENR_P01_GUA": {
        "window": (103, 107, 2794, 2794),
    },
    "ENR_P02": {
        "window": (204, 265, 17591, 7471),
    },
    "Fairbanks SEC": {
        "window": (1510, 33, 15116, 11427),
        "geobound": (-158, 64, None, None),
    },
    "Fairbanks TAC": {
        "window": (3103, 59, 8072, 5704),
    },
    "GOM_CN": {
        "window": (1754, 75, 13922, 12451),
    },
    "GOM_CS": {
        "window": (74, 74, 15601, 8100),
    },
    "GOM_WN": {
        "window": (74, 74, 15602, 12452),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[74,74], [3504,74], [3504,5986], [74,5986], [74,74]]]] } ],
    },
    "GOM_WS": {
        "window": (74, 74, 15603, 8136),
    },
    "Grand Canyon Air Tour Operators": {
        "window": (1646, 536, 11910, 5364),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[12117,536], [13556,536], [13556,2067], [12114,2070], [12117,536]]]] } ],
    },
    "Grand Canyon General Aviation": {
        "window": (3111, 564, 11910, 5364),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[13576,564], [15021,564], [15021,2096], [13578,2099], [13576,564]]]] } ],
    },
    "Grand Canyon General Aviation Marble Canyon Inset": {
        "input_file": "Grand Canyon General Aviation.tif",
        "window": (13653, 640, 1294, 1374),
        "gcps": [(14067, 978, -111.75, 36.833), (13719, 1415, -111.833, 36.75), (14068, 1413, -111.75, 36.75), (14419, 1412, -111.666, 36.75), (14769, 1411, -111.583, 36.75), (14070, 1848, -111.75, 36.666)],
    },
    "Great Falls SEC": {
        "window": (2170, 15, 15700, 12080),
        "geobound": (-117, None, None, None),
    },
    "Green Bay SEC": {
        "window": (2121, 32, 15788, 11668),
        "geobound": (-93, 44, None, None),
    },
    "Halifax SEC": {
        "window": (1338, 0, 15260, 12258),
        "geobound": (-69, 44, None, None),
    },
    "Halifax SEC Yarmouth Extension": {
        "input_file": "Halifax SEC.tif",
        "window": (6239, 11250, 1716, 988),
        "geobound": (-66.4, None, -65.5, 44),
    },
    "Hawaiian Islands SEC": {
        "window": (1845, 1009, 16760, 13718),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[1845,1009], [4862,1009], [1845,5508], [1845,1009]]] ,[[[4862,1009], [18605,1009], [18605,10228], [4862,1009]]] ,[[[18605,10228], [18605,14727], [15588,14727], [18605,10228]]] ,[[[1845,5508], [15588,14727],  [1845,14727], [1845,5508]]]] } ],
    },
    "Honolulu Inset SEC": {
        "window": (68, 47, 5561, 4427),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[5629,3216], [5629,4474], [4200,4474], [5629,3216]]]] } ],
    },
    "Houston FLY": {
        "window": (1537, 497, 7441, 7467),
    },
    "Houston North HEL": {
        "window": (1792, 74, 8555, 7190),
    },
    "Houston SEC": {
        "window": (1673, 23, 14878, 11266),
        "geobound": (-97, 28, None, None),
    },
    "Houston South HEL": {
        "window": (1788, 75, 8557, 7421),
    },
    "Houston TAC": {
        "window": (1537, 497, 7441, 7468),
    },
    "Jacksonville SEC": {
        "window": (1698, 0, 14919, 11315),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[13614,0], [16617,0], [16617,3477], [13614,3477], [13614,0]]] ,[[[2250,7804], [4651,7804], [4651,10641], [2250,10641], [2250,7804]]]] } ],
        "geobound": (-85, 28, None, None),
    },
    "Jacksonville SEC Jacksonville Inset": {
        "input_file": "Jacksonville SEC.tif",
        "window": (13677, 6, 2916, 3346),
        "gcps": [(14794, 270, -81.75, 30.666), (15925, 270, -81.5, 30.666), (14038, 1142, -81.916, 30.5), (14794, 1142, -81.75, 30.5), (15927, 1142, -81.5, 30.5), (14026, 2451, -81.916, 30.25), (14793, 2452, -81.75, 30.25), (15929, 2451, -81.5, 30.25)],
    },
    "Juneau SEC": {
        "window": (871, 59, 15637, 11301),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[871,4217], [1460,4217], [1460,5051], [871,5051], [871,4217]]] ,[[[1588,2705], [4447,2705], [4447,3958], [1588,3958], [1588,2705]]] ,[[[1616,4018], [4227,4018], [4227,5859], [1616,5859], [1616,4018]]]] } ],
        "geobound": (-141, 56, None, None),
    },
    "Juneau SEC Juneau Inset": {
        "input_file": "Juneau SEC.tif",
        "window": (1695, 4107, 2452, 1659),
        "gcps": [(2294, 4536, -134.75, 58.416), (2984, 4527, -134.5, 58.416), (3674, 4516, -134.25, 58.416), (2303, 5412, -134.75, 58.25), (2297, 5404, -134.5, 58.25), (3690, 5392, -134.25, 58.25)],
    },
    "Juneau SEC Seward Glacier Area Inset": {
        "input_file": "Juneau SEC.tif",
        "window": (1686, 2802, 2668, 2964),
        "geobound": (None, 60, None, None),
        "gcps": [(1699, 2938, -141, 60.333), (3002, 2944, -140, 60.333), (4306, 2931, -139, 60.333), (1688, 3814, -141, 60), (3005, 3820, -140, 60), (4322, 3805, -139, 60)],
    },
    "Kansas City SEC": {
        "window": (1518, 39, 15065, 11296),
        "geobound": (-97, 36, None, None),
    },
    "Kansas City TAC": {
        "window": (1554, 728, 7400, 6576),
    },
    "Ketchikan SEC": {
        "window": (1522, 21, 14987, 11246),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[4648,912], [7226,912], [7226,4425], [4648,4425], [4648,912]]]] } ],
        "geobound": (-139, 52, None, None),
    },
    "Ketchikan SEC Ketchikan Inset": {
        "input_file": "Ketchikan SEC.tif",
        "window": (4730, 1007, 2415, 3322),
        "gcps": [(4844, 1615, -132, 55.5), (5590, 1618, -131.75, 55.5), (7083, 1617, -131.25, 55.5), (4836, 2930, -132, 55.25), (6337, 2933, -131.5, 55.25), (7089, 2931, -131.25, 55.25), (4873, 4245, -132, 55), (5584, 4248, -131.75, 55), (7095, 4246, -131.25, 55)],
    },
    "Klamath Falls SEC": {
        "window": (1628, 0, 16278, 12150),
        "geobound": (-125, 40, None, None),
    },
    "Kodiak SEC": {
        "window": (945, 5, 15651, 11358),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[13777,8497], [16467,8497], [16488,11363], [13777,11363], [13777,8497]]] ,[[[945,4266], [1479,4266], [1479,5100], [945,5100], [945,4266]]]] } ],
        "geobound": (-162, 56, None, None),
    },
    "Kodiak SEC Cape Newenham": {
        "input_file": "Kodiak SEC.tif",
        "window": (1218, 3755, 303, 426),
        "geobound": (None, None, -162, None),
    },
    "Kodiak SEC Carter Spit": {
        "input_file": "Kodiak SEC.tif",
        "window": (1540, 2212, 100, 441),
        "geobound": (None, None, -162, None),
    },
    "Kodiak SEC Kodiak Inset": {
        "input_file": "Kodiak SEC.tif",
        "window": (13811, 8576, 2648, 3416),
        "geobound": (None, None, -152, None),
        "gcps": [(14342, 8840, -152.75, 58), (15040, 8840, -152.5, 58), (15740, 8840, -152.25, 58), (14337, 10154, -152.75, 57.75), (15743, 10154, -152.25, 57.75), (14332, 11470, -152.75, 57.5), (15040, 11471, -152.5, 57.5), (15748, 11470, -152.25, 57.5)],
    },
    "Lake Huron SEC": {
        "window": (2116, 28, 15754, 11375),
        "geobound": (-85, 44, None, None),
    },
    "Las Vegas FLY": {
        "window": (3114, 225, 7378, 5389),
    },
    "Las Vegas SEC": {
        "window": (2696, 0, 15153, 11757),
        "geobound": (-118, None, None, None),
    },
    "Las Vegas TAC": {
        "window": (3100, 596, 7378, 5388),
    },
    "Los Angeles East HEL": {
        "window": (1553, 96, 9357, 8743),
    },
    "Los Angeles FLY": {
        "window": (1608, 121, 10206, 5785),
    },
    "Los Angeles SEC": {
        "window": (535, 1373, 16110, 10976),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[535,1373], [1591,1373], [1582,6255], [535,6256], [535,1373]]] ,[[[525,8082], [4688,8087], [4688,12349], [535,12349], [525,8082]]]] } ],
        "geobound": (-122, 32, None, None),
    },
    "Los Angeles TAC": {
        "window": (4672, 105, 10205, 5785),
    },
    "Los Angeles West HEL": {
        "window": (1531, 87, 10388, 8736),
    },
    "Mariana Islands Inset SEC": {
        "window": (27, 34, 6125, 5974),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[27,34], [3689,34], [3687,3235], [2116,4618], [27,4618], [27,34]]]] } ],
    },
    "McGrath SEC": {
        "window": (1710, 0, 14787, 11374),
        "geobound": (-162, 60, None, None),
    },
    "Memphis SEC": {
        "window": (2181, 53, 15713, 11298),
        "geobound": (-95, 32, None, None),
    },
    "Memphis TAC": {
        "window": (1673, 556, 7441, 6821),
    },
    "Miami FLY": {
        "window": (1575, 73, 8885, 8777),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[7282,6938], [10404,6938], [10404,8799], [6359,8799], [6359,7324], [7282,7324], [7282,6938]]]] } ],
    },
    "Miami FLY Florida Keys Inset": {
        "input_file": "Miami FLY.tif",
        "window": (6427, 7000, 3914, 1734),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[6427,7000], [7344,7000], [7344,7386], [6427,7386], [6427,7000]]]] } ],
        "gcps": [(7687, 7329, -81.5, 25), (8879, 7323, -81, 25), (10073, 7311, -80.5, 25), (6494, 8640, -82, 24.5), (7692, 8638, -81.5, 24.5), (8890, 8631, -81, 24.5), (10089, 8619, -80.5, 24.5)],
    },
    "Miami SEC": {
        "window": (1497, 0, 15562, 12018),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[4107,0], [17059,0], [17059,1051], [4089,1041], [4107,0]]]] } ],
        "geobound": (-83, 24, None, 28.5),
    },
    "Miami TAC": {
        "window": (1575, 72, 8890, 8783),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[7285,6943], [10408,6944], [10408,8804], [6361,8803], [6361,7329], [7285,7329], [7285,6943]]]] } ],
    },
    "Miami TAC Florida Keys Inset": {
        "input_file": "Miami TAC.tif",
        "window": (6429, 7006, 3917, 1732),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[6429,7006], [7384,7006], [7384,7392], [6429,7392], [6429,7006]]]] } ],
        "gcps": [(7687, 7329, -81.5, 25), (8879, 7323, -81, 25), (10073, 7311, -80.5, 25), (6494, 8640, -82, 24.5), (7692, 8638, -81.5, 24.5), (8890, 8631, -81, 24.5), (10089, 8619, -80.5, 24.5)],
    },
    "Minneapolis-St Paul TAC": {
        "window": (1801, 556, 5891, 5384),
    },
    "Montreal SEC": {
        "window": (2101, 60, 15762, 11355),
        "geobound": (-77, 44, None, None),
    },
    "NARC": {
        "window": (82, 82, 13525, 9558),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[82,82], [5308,82], [5308,2065], [4561,2065], [4561,2257], [3815,2257], [3815,3026], [3068,3026], [3068,3411], [2322,3411], [2322,3680], [820,3680], [820,3487], [82,3787], [82,82]]] ,[[[82,8695], [1283,8695], [1283,9640], [82,9640], [82,8695]]]] } ],
    },
    "New Orleans FLY": {
        "window": (1546, 574, 7414, 5389),
    },
    "New Orleans SEC": {
        "window": (1721, 0, 14896, 11289),
        "geobound": (-91, 28, None, None),
    },
    "New Orleans TAC": {
        "window": (1551, 518, 7414, 5389),
    },
    "New York HEL": {
        "window": (2989, 1074, 10407, 7769),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[2989,1074], [8387,1074], [8387,3333], [2989,3315], [2989,1074]]] ,[[[11824,1074], [13396,1074], [13396,3309], [11836,3318], [11824,1074]]] ,[[[2989,5936], [4445,5945], [4445,8843], [2989,8843], [2989,5936]]]] } ],
    },
    "New York SEC": {
        "window": (1605, 0, 16257, 11363),
        "geobound": (-77, 40, None, None),
    },
    "New York TAC": {
        "window": (3061, 568, 8906, 5457),
    },
    "Nome SEC": {
        "window": (883, 0, 15717, 11442),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[2143,104], [4241,105], [4240,3013], [2142,3012], [2143,104]]]] } ],
        "geobound": (-171.5, 64, None, None),
    },
    "Nome SEC Lavrentiya-Provideniya Inset": {
        "input_file": "Nome SEC.tif",
        "window": (2302, 261, 1785, 2580),
        "gcps": [(2708, 313, -173, 66), (3245, 321, -172, 66), (3780, 320, -171, 66), (2678, 1629, -173, 65), (3235, 1637, -172, 65), (3793, 1637, -171, 65), (3228, 2735, -172, 64.166)],
    },
    "Omaha SEC": {
        "window": (1634, 52, 16287, 12090),
        "geobound": (-101, 40, None, None),
    },
    "Orlando FLY": {
        "window": (137, 370, 8751, 7171),
    },
    "Orlando TAC": {
        "window": (3009, 443, 8750, 7170),
    },
    "Philadelphia TAC": {
        "window": (1735, 639, 5905, 5854),
    },
    "Phoenix FLY": {
        "window": (3064, 119, 7407, 6811),
    },
    "Phoenix SEC": {
        "window": (1987, 662, 15898, 11589),
        "geobound": (-116, None, None, None),
    },
    "Phoenix TAC": {
        "window": (3055, 585, 7407, 6810),
    },
    "Pittsburgh TAC": {
        "window": (1620, 548, 5897, 5386),
    },
    "Point Barrow SEC": {
        "window": (709, 22, 15846, 11533),
        "geobound": (-157, 68, None, None),
    },
    "PORC_COMP": {
        "window": (62, 63, 17693, 12293),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[9907,11846], [13344,11846], [13344,10336], [14976,10336], [14976,9665], [16373,9665], [16373, 9931], [17755,9931], [17755,12356], [9907,12356], [9907,11846]]]] } ],
    },
    "PORC_NE": {
        "window": (62, 63, 17693, 12293),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[13453,63], [17755,63], [17755,4140], [16366,4140], [16366,1998], [14734,1998], [14734,488], [13453,488], [13453,63]]]] } ],
    },
    "PORC_NW": {
        "window": (62, 63, 17693, 12293),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[62,63], [5325,63], [5325,530], [3085,530], [3085,2040], [2843,2040], [2843,3930], [1446,3930], [1446,2445], [62,2445], [62,63]]]] } ],
    },
    "PORC_SE": {
        "window": (62, 63, 17693, 12293),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[62,6097], [614,6097], [614,9334], [2124,9334], [2124,10966], [3354,10966], [3354,12356], [62,12356], [62,6097]]]] } ],
    },
    "PORC_SW": {
        "window": (62, 63, 17693, 12293),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[14388,10966], [15778,10966], [15778,9334], [17288,9334], [17288,6099], [17755,6099], [17755,12356], [14388,12356], [14388,10966]]]] } ],
    },
    "Portland TAC": {
        "window": (1845, 3094, 4118, 4288),
    },
    "Puerto Rico-VI TAC": {
        "window": (1489, 68, 16281, 5974),
    },
    "Salt Lake City FLY": {
        "window": (1595, 159, 7408, 6754),
    },
    "Salt Lake City SEC": {
        "window": (1603, 15, 16294, 12085),
        "geobound": (-117, 40, None, None),
    },
    "Salt Lake City TAC": {
        "window": (1593, 699, 7408, 6755),
    },
    "Samoan Islands Inset SEC": {
        "window": (33, 53, 8928, 3877),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[6996,53], [8961,53], [8961,2542], [6996,2542], [6996,53]]]] } ],
    },
    "San Antonio SEC": {
        "window": (1738, 0, 15117, 11290),
        "geobound": (-103, 28, -96.5, None),
    },
    "San Diego FLY": {
        "window": (307, 218, 7411, 5815),
    },
    "San Diego TAC": {
        "window": (4626, 178, 7411, 5816),
    },
    "San Francisco FLY": {
        "window": (3218, 693, 7377, 6158),
    },
    "San Francisco SEC": {
        "window": (1449, 1001, 15160, 11197),
        "geobound": (-125, 36, None, None),
    },
    "San Francisco TAC": {
        "window": (3069, 362, 7377, 6157),
    },
    "Seattle FLY": {
        "window": (1625, 272, 5915, 6920),
    },
    "Seattle SEC": {
        "window": (2155, 0, 15703, 12104),
        "geobound": (-125, None, None, None),
    },
    "Seattle TAC": {
        "window": (1674, 547, 5916, 6919),
    },
    "Seward SEC": {
        "window": (1568, 11, 16290, 6181),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[3152,6104], [17858,6089], [17858,6192], [3152,6192], [3152,6104]]]] } ],
        "geobound": (-152.5, None, None, None),
    },
    "St Louis FLY": {
        "window": (1614, 556, 5900, 5394),
    },
    "St Louis SEC": {
        "window": (2918, 41, 15017, 11336),
        "geobound": (-91, 36, None, None),
    },
    "St Louis TAC": {
        "window": (1561, 560, 5901, 5394),
    },
    "Tampa FLY": {
        "window": (100, 483, 5919, 6902),
    },
    "Tampa TAC": {
        "window": (1531, 484, 5921, 6902),
    },
    "Twin Cities SEC": {
        "window": (2201, 45, 15705, 12087),
        "geobound": (-101, None, None, None),
    },
    "Twin Cities SEC Lake Of The Woods Inset": {
        "input_file": "Twin Cities SEC.tif",
        "window": (233, 9389, 1836, 2272),
        "gcps": [(291, 9476, -95.5, 49.5), (1149, 9479, -95, 49.5), (2008, 9476, -94.5, 49.5), (282, 10792, -95.5, 49), (1149, 10796, -95, 49), (2016, 10793, -94.5, 49)],
    },
    "U.S. Gulf Coast HEL": {
        "window": (1534, 0, 15017, 6601),
        "geobound": (None, 26, None, None),
    },
    "U.S. VFR Wall Planning Chart": {
        "window": (466, 297, 17636, 10698),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[593,7940], [2099,7932], [2116,10939], [601,10947], [593,7940]]]] } ],
    },
    "US_IFR_PLAN_EAST": {
        "window": (2, 260, 9794, 12032),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[2,11714], [5675,11714], [5675,12292], [2,12292], [2,11714]]] ,[[[8326,4794], [9796,3438], [9796,12292], [8326,12292], [8326,4794]]]] } ],
    },
    "US_IFR_PLAN_WEST": {
        "window": (3854, 260, 9794, 12032),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[3854,7477], [12760,11931], [12400,12292], [3854,12292], [3854,7477]]]] } ],
    },
    "Washington HEL": {
        "window": (3029, 314, 10391, 5579),
    },
    "Washington Inset HEL": {
        "window": (50, 142, 2872, 3596),
    },
    "Washington SEC": {
        "window": (1512, 25, 15150, 11302),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[14042,6502], [16549,6490], [16563,9079], [14053,9091], [14042,6502]]]] } ],
        "geobound": (-79, 36, None, None),
    },
    "Washington SEC Norfolk Inset": {
        "input_file": "Washington SEC.tif",
        "window": (14118, 6565, 2426, 2388),
        "gcps": [(14183, 6713, -76.5, 37.166), (15230, 6712, -76.25, 37.166), (16278, 6708, -76, 37.166), (14182, 7586, -76.5, 37), (15232, 7585, -76.25, 37), (16283, 7582, -76, 37), (14181, 8895, -76.5, 36.75), (15235, 8894, -76.25, 36.75), (16289, 8891, -76, 36.75)],
    },
    "WATRS": {
        "window": (72, 1788, 7167, 8426),
        "masks": [ { "type": "MultiPolygon", "coordinates": [[[[72,1788], [2803,1788], [2803,2027], [2243,2027], [2243,2316], [562,2316], [562,3229], [72,3229], [72,1788]]]] } ],
    },
    "Western Aleutian Islands East SEC": {
        "window": (968, 211, 15573, 5902),
        "geobound": (178, 51, None, None),
    },
    "Western Aleutian Islands West SEC": {
        "window": (1557, 145, 14987, 5888),
        "geobound": (None, 51, None, None),
    },
    "Wichita SEC": {
        "window": (1437, 60, 15204, 11311),
        "geobound": (-104, 36, None, None),
    },
}

'''
Dictionary of named tilesets to process. A tileset is a list of datasets of similar scale and function
that should be merged and displayed together on the final map.

Key: Name of the tileset
Value: Dictionary of tileset information

    zoom: str
        Zoom levels to build

    datasets: list of str
        List of datasets to include in the tileset
'''

tileset_datasets = {
    "VFR Planning Charts": {
        "tile_path" : "vfr_planning",
        "zoom": "0-9",
        "maxlod_zoom": 9,
        "datasets": [
            "Alaska Wall Planning Chart British Columbia Coast Inset",
            "U.S. VFR Wall Planning Chart",
            "Alaska Wall Planning Chart",
            "Alaska Wall Planning Chart Western Aleutian Islands Inset",
        ],
    },
    "VFR Sectional Charts": {
        "tile_path" : "vfr_sectional",
        "zoom": "10-12",
        "maxlod_zoom": 12,
        "datasets": [
            "U.S. Gulf Coast HEL",
            "Caribbean 2 VFR Chart",
            "Caribbean 1 VFR Chart",
            "Mariana Islands Inset SEC",
            "Samoan Islands Inset SEC",
            "Hawaiian Islands SEC",
            "Brownsville SEC",
            "Miami SEC",
            "El Paso SEC",
            "San Antonio SEC",
            "Houston SEC",
            "New Orleans SEC",
            "Jacksonville SEC",
            "Los Angeles SEC",
            "Phoenix SEC",
            "Albuquerque SEC",
            "Dallas-Ft Worth SEC",
            "Memphis SEC",
            "Atlanta SEC",
            "Charlotte SEC",
            "San Francisco SEC",
            "Las Vegas SEC",
            "Denver SEC",
            "Wichita SEC",
            "Kansas City SEC",
            "St Louis SEC",
            "Cincinnati SEC",
            "Washington SEC",
            "Klamath Falls SEC",
            "Salt Lake City SEC",
            "Cheyenne SEC",
            "Omaha SEC",
            "Chicago SEC",
            "Detroit SEC",
            "New York SEC",
            "Seattle SEC",
            "Great Falls SEC",
            "Billings SEC",
            "Twin Cities SEC",
            "Twin Cities SEC Lake Of The Woods Inset",
            "Green Bay SEC",
            "Lake Huron SEC",
            "Montreal SEC",
            "Halifax SEC",
            "Halifax SEC Yarmouth Extension",
            "Western Aleutian Islands East SEC",
            "Western Aleutian Islands West SEC",
            "Dutch Harbor SEC",
            "Dutch Harbor SEC Pribilof Islands Inset",
            "Cold Bay SEC",
            "Ketchikan SEC",
            "Kodiak SEC",
            "Kodiak SEC Cape Newenham",
            "Kodiak SEC Carter Spit",
            "Seward SEC",
            "Juneau SEC",
            "Juneau SEC Seward Glacier Area Inset",
            "Bethel SEC",
            "McGrath SEC",
            "Anchorage SEC",
            "Nome SEC Lavrentiya-Provideniya Inset",
            "Nome SEC",
            "Fairbanks SEC",
            "Dawson SEC",
            "Cape Lisburne SEC",
            "Point Barrow SEC",
        ],
    },
    "VFR Sectional Detail Charts": {
        "tile_path" : "vfr_sectional",
        "zoom": "13",
        "maxlod_zoom": 13,
        "datasets": [
            "Juneau SEC Juneau Inset",
            "Dutch Harbor SEC Dutch Harbor Inset",
            "Grand Canyon General Aviation",
            "Grand Canyon General Aviation Marble Canyon Inset",
            "Honolulu Inset SEC",
            "Jacksonville SEC Jacksonville Inset",
            "Ketchikan SEC Ketchikan Inset",
            "Kodiak SEC Kodiak Inset",
            "Washington SEC Norfolk Inset",
        ],
    },
    "VFR Terminal Area Charts": {
        "tile_path" : "vfr_tac",
        "zoom": "13",
        "maxlod_zoom": 13,
        "datasets": [
            "Miami TAC Florida Keys Inset",
            "Anchorage TAC",
            "Atlanta TAC",
            "Baltimore-Washington TAC",
            "Boston TAC",
            "Charlotte TAC",
            "Chicago TAC",
            "Cincinnati TAC",
            "Cleveland TAC",
            "Colorado Springs TAC",
            "Dallas-Ft Worth TAC",
            "Denver TAC",
            "Detroit TAC",
            "Fairbanks TAC",
            "Houston TAC",
            "Kansas City TAC",
            "Las Vegas TAC",
            "Los Angeles TAC",
            "Memphis TAC",
            "Miami TAC",
            "Minneapolis-St Paul TAC",
            "New Orleans TAC",
            "New York TAC",
            "Orlando TAC",
            "Philadelphia TAC",
            "Phoenix TAC",
            "Pittsburgh TAC",
            "Portland TAC",
            "Puerto Rico-VI TAC",
            "Salt Lake City TAC",
            "San Diego TAC",
            "San Francisco TAC",
            "Seattle TAC",
            "St Louis TAC",
            "Tampa TAC",
        ],
    },
    "VFR Flyway Charts": {
        "tile_path" : "vfr_flyway",
        "zoom": "13",
        "maxlod_zoom": 13,
        "datasets": [
            "Miami FLY Florida Keys Inset",
            "Atlanta FLY",
            "Baltimore-Washington FLY",
            "Charlotte FLY",
            "Chicago FLY",
            "Cincinnati FLY",
            "Dallas-Ft Worth FLY",
            "Denver FLY",
            "Detroit FLY",
            "Houston FLY",
            "Las Vegas FLY",
            "Los Angeles FLY",
            "Miami FLY",
            "New Orleans FLY",
            "Orlando FLY",
            "Phoenix FLY",
            "Salt Lake City FLY",
            "San Diego FLY",
            "San Francisco FLY",
            "Seattle FLY",
            "St Louis FLY",
            "Tampa FLY",
        ],
    },
    "Helicopter Route Charts": {
        "tile_path" : "vfr_helicopter",
        "zoom": "14",
        "maxlod_zoom": 14,
        "datasets": [
            "Eastern Long Island HEL",
            "Baltimore HEL",
            "Boston HEL",
            "Chicago HEL",
            "Dallas-Ft Worth HEL",
            "Detroit HEL",
            "Houston North HEL",
            "Houston South HEL",
            "Los Angeles East HEL",
            "Los Angeles West HEL",
            "New York HEL",
            "Washington HEL",
        ],
    },
    "Helicopter Route Detail Charts": {
        "tile_path" : "vfr_helicopter_inset",
        "zoom": "15",
        "maxlod_zoom": 15,
        "datasets": [
            "Chicago O'Hare Inset HEL",
            "Dallas-Love Inset HEL",
            "Washington Inset HEL",
            "Boston Downtown HEL",
            "Downtown Manhattan HEL",
        ],
    },
    "IFR Planning Charts U.S.": {
        "tile_path" : "ifr_planning",
        "zoom": "0-9",
        "maxlod_zoom": 9,
        "datasets": [
            "US_IFR_PLAN_EAST",
            "US_IFR_PLAN_WEST",
        ],
    },
    "IFR Enroute High And Low Altitude Pacific": {
        "tile_path" : "ifr_planning",
        "zoom": "0-8",
        "maxlod_zoom": 8,
        "datasets": [
            "ENR_P01",
        ],
    },
    "IFR Enroute High Altitude Alaska": {
        "tile_path" : "ifr_high_ak",
        "zoom": "0-9",
        "maxlod_zoom": 9,
        "datasets": [
            "ENR_AKH01",
            "ENR_AKH02",
        ],
    },
    "IFR Enroute High Altitude Alaska Detail": {
        "tile_path" : "ifr_high_ak_detail",
        "zoom": "0-10",
        "maxlod_zoom": 10,
        "datasets": [
            "ENR_AKH01_SEA",
        ],
    },
    "IFR Enroute High Altitude Caribbean And South America": {
        "tile_path" : "ifr_high_carib",
        "zoom": "0-11",
        "maxlod_zoom": 11,
        "datasets": [
            "ENR_CH02",
            "ENR_CA03_MEX_BORDER",
            "ENR_CA02_CENT-PAC",
            "ENR_CH01",
            "ENR_CH08",
            "ENR_CH07",
        ],
    },
    "IFR Enroute High Altitude U.S.": {
        "tile_path" : "ifr_high_us",
        "zoom": "0-11",
        "maxlod_zoom": 11,
        "datasets": [
            "ENR_H01",
            "ENR_H02",
            "ENR_H03",
            "ENR_H04",
            "ENR_H05",
            "ENR_H06",
            "ENR_H07",
            "ENR_H08",
            "ENR_H09",
            "ENR_H10",
            "ENR_H11",
            "ENR_H12",
        ],
    },
    "IFR Enroute Low Altitude Alaska": {
        "tile_path" : "ifr_low_ak",
        "zoom": "0-10",
        "maxlod_zoom": 10,
        "datasets": [
            "ENR_AKL01",
            "ENR_AKL02C",
            "ENR_AKL02E",
            "ENR_AKL02W",
            "ENR_AKL03",
            "ENR_AKL04",
        ],
    },
    "IFR Enroute Low Altitude Caribbean And South America": {
        "tile_path" : "ifr_low_carib",
        "zoom": "0-11",
        "maxlod_zoom": 11,
        "datasets": [
            "ENR_CL05 Charleston-Bermuda Inset",
            "ENR_CL03",
            "ENR_CL02",
            "ENR_CL05",
            "ENR_CL06",
            "ENR_CL01",
        ],
    },
    "IFR Enroute Low Altitude Pacific": {
        "tile_path" : "ifr_low_pac",
        "zoom": "9-11",
        "maxlod_zoom": 11,
        "datasets": [
            "ENR_P02",
        ],
    },
    "IFR Enroute Low Altitude U.S.": {
        "tile_path" : "ifr_low_us",
        "zoom": "10-12",
        "maxlod_zoom": 12,
        "datasets": [
            "ENR_L23 Wilmington-Bimini Inset",
            "ENR_L21",
            "ENR_L34 Boston-Yarmouth Inset",
            "ENR_L13",
            "ENR_L27",
            "ENR_L09",
            "ENR_L11",
            "ENR_L12",
            "ENR_L14",
            "ENR_L28",
            "ENR_L32",
            "ENR_L10",
            "ENR_L31",
            "ENR_L05",
            "ENR_L06N",
            "ENR_L06S",
            "ENR_L08",
            "ENR_L15",
            "ENR_L16",
            "ENR_L17",
            "ENR_L18",
            "ENR_L19",
            "ENR_L20",
            "ENR_L22",
            "ENR_L23",
            "ENR_L24",
            "ENR_L01",
            "ENR_L02",
            "ENR_L03",
            "ENR_L04",
            "ENR_L07",
            "ENR_L25",
            "ENR_L26",
            "ENR_L29",
            "ENR_L30",
            "ENR_L33",
            "ENR_L34",
            "ENR_L35",
            "ENR_L36",
        ],
    },
    "IFR Area Charts Alaska": {
        "tile_path" : "ifr_area_ak",
        "zoom": "11",
        "maxlod_zoom": 11,
        "datasets": [
            "ENR_AKL01_JNU",
            "ENR_AKL04_ANC",
            "ENR_AKL03_FAI",
            "ENR_AKL03_OME",
            "ENR_AKL01_VR",
        ],
    },
    "IFR Area Charts Caribbean And South America": {
        "tile_path" : "ifr_area_carib",
        "zoom": "12",
        "maxlod_zoom": 12,
        "datasets": [
            "ENR_CA02_MEX",
            "ENR_CA01_SANTIAGO",
            "ENR_CA02_MIA-NAS",
            "ENR_CA01_GUAT",
            "ENR_CA01_LIMA",
            "ENR_CA03_PR",
            "ENR_CA01_BUENOS",
            "ENR_CA02_BOGOTA",
            "ENR_CA01_RIO",
            "ENR_CA02_PANAMA",
        ],
    },
    "IFR Area Charts Pacific": {
        "tile_path" : "ifr_area_pac",
        "zoom": "9",
        "maxlod_zoom": 9,
        "datasets": [
            "ENR_P01_GUA",
        ],
    },
    "IFR Area Charts U.S.": {
        "tile_path" : "ifr_area_us",
        "zoom": "13",
        "maxlod_zoom": 13,
        "datasets": [
            "ENR_A02_PHX",
            "ENR_A01_MSP",
            "ENR_A01_DCA",
            "ENR_A02_DEN",
            "ENR_A02_DFW",
            "ENR_A01_ATL",
            "ENR_A01_JAX",
            "ENR_A01_MIA",
            "ENR_A01_STL",
            "ENR_A02_ORD",
            "ENR_A02_SFO",
            "ENR_A01_DET",
            "ENR_A02_LAX",
            "ENR_A02_MKC",
        ],
    },
    "North Atlantic Route Planning Chart": {
        "tile_path" : "narc",
        "zoom": "0-8",
        "maxlod_zoom": 8,
        "datasets": [
            "NARC",
        ],
    },
    "North Pacific Route Planning Chart": {
        "tile_path" : "porc",
        "zoom": "0-7",
        "maxlod_zoom": 7,
        "datasets": [
            "PORC_COMP",
        ],
    },
    "North Pacific Route Planning Detail Chart": {
        "tile_path" : "porc_detail",
        "zoom": "8",
        "maxlod_zoom": 8,
        "datasets": [
            "PORC_NE",
            "PORC_NW",
            "PORC_SE",
            "PORC_SW",
        ],
    },
    "West Atlantic Route System Planning Chart": {
        "tile_path" : "watrs",
        "zoom": "0-9",
        "maxlod_zoom": 9,
        "datasets": [
            "WATRS",
        ],
    },
    "IFR Gulf Of Mexico Chart": {
        "tile_path" : "gom",
        "zoom": "0-12",
        "maxlod_zoom": 12,
        "datasets": [
            "GOM_CN",
            "GOM_CS",
            "GOM_WN",
            "GOM_WS",
        ],
    },
}

# Download support

_AERONAV_VFR_URL = 'https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/vfr/'
_AERONAV_IFR_URL = 'https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/ifr/'

def download(url, path='.', filename=None):
    '''
    Downloads a file from the given URL and saves it to the specified filename.
    If the filename is not provided, the file will be saved with the basename of the URL.
    If the file already exists, the function will add an 'If-Modified-Since' header to the request
    to avoid downloading the file again if it has not been modified.

    Args:
        url (str): The URL of the file to download.
        filename (str, optional): The name of the file to save. Defaults to None.

    Returns:
        str: The filename of the downloaded file.

    Raises:
        urllib.error.HTTPError: If an HTTP error occurs other than a 304 Not Modified response.
    '''
    # Create a request to retrieve the file
    request = urllib.request.Request(url)

    # Set filename
    filename = os.path.join(path, filename or os.path.basename(url))

    # Check if the file already exists on the filesystem and add the If-Modified-Since header to the request
    if os.path.exists(filename):
        last_modified_time = os.path.getmtime(filename)
        last_modified_time_str = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(last_modified_time))
        request.add_header('If-Modified-Since', last_modified_time_str)

    # Download the file
    try:
        with urllib.request.urlopen(request) as response:
            if response.status == 200:
                with open(filename, 'wb') as f:
                    f.write(response.read())

    except urllib.error.HTTPError as e:
        # Check if the server returned a 304 Not Modified response, if so, just skip the download
        if e.code != 304:
            raise

    return filename

def get_current_aeronav_urls(index_url, chart_types):
    '''
    Scrapes the aeronav.faa.gov website for the current URLs of the specified chart types.
    '''

    # Read the page for scraping
    with urllib.request.urlopen(index_url) as response:
        html = response.read().decode('utf-8')

    soup = bs4.BeautifulSoup(html, 'html.parser')

    urls = []
    for chart_type in chart_types:
        # Each chart type has its own div
        chart = soup.find('div', id=chart_type)
        if chart:
            # Any table describes a set of charts
            tables = chart.find_all('table')
            for table in tables:
                # Each row in the table describes a chart
                rows = table.find_all('tr')
                for row in rows:
                    # The second cell in each row contains the current chart info
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        # Find all <a> elements in the cell
                        a_tags = cells[1].find_all('a')
                        for a_tag in a_tags:
                            # Check if the link text is 'Geo-TIFF'
                            if a_tag and a_tag.get_text(strip=True).lower() == 'geo-tiff':
                                # Finally, there it is
                                geo_tiff_url = a_tag['href']
                                urls.append(geo_tiff_url)
    return urls

def fix_faa_incorrect_urls(urls):
    '''
    Fixes incorrect URLs on the aeronav.faa.gov website by finding the most common "before date" part of the URL
    and using that as the base URL for all URLs. This is necessary because the FAA's web site doesn't always
    link to the correct file. FAA, get your shit together.
    '''
    url_tuples = []
    date_pattern = re.compile(r'/(\d{2}-\d{2}-\d{4})/')

    # Examine each URL, and find the date in the URL
    for url in urls:
        match = date_pattern.search(url)
        if match:
            # Store everything up to and including the date, then everything after the date
            before_date = url[:match.end(1)]
            after_date = url[match.end(1):]
            url_tuples.append((before_date, after_date))
        else:
            raise ValueError(f"Could not find date in aeronav URL {url}")

    # All "before date" parts should be the same. Tally up the counts of each "before date" part
    baseurl_counts = {}
    for before_date, _ in url_tuples:
        if before_date not in baseurl_counts:
            baseurl_counts[before_date] = 0
        baseurl_counts[before_date] += 1

    # If they are not all the same, use the most common one
    if len(baseurl_counts) > 1:
        most_common_baseurl = max(baseurl_counts, key=baseurl_counts.get)
        cleaned_urls = []
        for before_date, after_date in url_tuples:
            if before_date != most_common_baseurl:
                cleaned_urls.append(most_common_baseurl + after_date)
            else:
                cleaned_urls.append(before_date + after_date)
        return cleaned_urls
    else:
        return urls

# Preprocessing step

def expand_to_rgb(filename):
    '''
    If the file contains a colormap band, expand it to RGB

    Parameters
    ----------
    filename: str
        The filename of the dataset to preprocess

    '''
    with rasterio.open(filename, 'r') as dataset:
        # If the dataset is not a single paletted band, nothing to do
        if dataset.count > 1 or dataset.colorinterp[0] != rasterio.enums.ColorInterp.palette:
            return

        # Read the profile, palette and data
        profile = dataset.profile
        colormap = dataset.colormap(1)
        src_data = dataset.read()

    # Create new arrays for the expanded data
    expanded_data = numpy.zeros((3, src_data[0].shape[0], src_data[0].shape[1]), dtype=src_data.dtype)

    # Transpose color table into four 256 element arrays, one for each band
    colormap_lookup = numpy.array([colormap[i] for i in range(min(256, len(colormap)))]).T

    # Expand the single band to three RGB bands, line by line for good performance
    for band in range(3):
        for iy in range(src_data[0].shape[0]):
            expanded_data[band][iy] = numpy.take(colormap_lookup[band], src_data[0][iy])

    # Write the expanded data back to the dataset
    profile['count'] = 3
    with rasterio.open(filename, 'w', **profile) as dataset:
        dataset.write(expanded_data)

# Major steps in the processing pipeline

def transform_from_gcps(gcps, src_crs, window):
    '''
    Calculate an affine transformation from a list of tuples in the dataset definition

    Parameters
    ----------
    gcps: list
        A list of tuples: (pixel_x, pixel_y, lon, lat)

    Returns
    -------
    Affine
        An affine transformation matrix representing the new transformation
    '''
    # Coordinate system for raw lat/lon coordinates
    geo_crs = rasterio.crs.CRS.from_epsg(4326)

    # Get the GCP definitions into separate lists
    xs, ys, lons, lats = zip(*gcps)

    # Adjust the gcp x/y coordinates to the window
    win_ys, win_xs = [y - window.row_off for y in ys], [x - window.col_off for x in xs]

    # Transform the lat/lon coordinates to the dataset CRS
    src_xs, src_ys = rasterio.warp.transform(geo_crs, src_crs, lons, lats)

    # Create list of GroundControlPoints mapping win_x, win_y to dst_x, dst_y
    gcps_src = [rasterio.control.GroundControlPoint(*point) for point in zip(win_ys, win_xs, src_xs, src_ys)]

    # Return a new transformation from the GCPs
    return rasterio.transform.from_gcps(gcps_src)

# Replacement for rasterio.windows.bounds that works with rotated datasets
def bounds_with_rotation(window, transform):
    '''Get the spatial bounds of a window, allowing for rotation

    This is a workaround for a bug in rasterio.windows.bounds, which does not account for rotation.
    See https://github.com/rasterio/rasterio/issues/3280

    Parameters
    ----------
    window: Window
        The input window.
    transform: Affine
        an affine transform matrix.

    Returns
    -------
    left, bottom, right, top: float
        A tuple of spatial coordinate bounding values.
    '''
    row_min = window.row_off
    row_max = row_min + window.height
    col_min = window.col_off
    col_max = col_min + window.width

    x0, y0 = transform * (col_min, row_max)
    x1, y1 = transform * (col_max, row_max)
    x2, y2 = transform * (col_min, row_min)
    x3, y3 = transform * (col_max, row_min)

    left = min(x0, x1, x2, x3)
    right = max(x0, x1, x2, x3)
    bottom = min(y0, y1, y2, y3)
    top = max(y0, y1, y2, y3)
    return left, bottom, right, top

def clip_to_geobounds(geobounds, src_bounds, src_crs, dst_crs, dst_transform):
    '''
    Calculate the clipped bounds and dimensions for a reprojection to EPSG:3857, based on the geobounds specified in Lat/Lon

    Parameters
    ----------
    geobounds: tuple
        A tuple of (left, bottom, right, top) in Lat/Lon coordinates from the dataset definition
    src_bounds: tuple
        A tuple of (left, bottom, right, top) in the source dataset's CRS
    src_crs: CRS
        The source coordinate reference system
    dst_crs: CRS
        The destination coordinate reference system
    dst_transform: Affine
        The destination transformation

    Returns
    -------
    dst_transform: Affine
        The destination transformation
    dst_width: int
        The width of the clipped destination dataset
    dst_height: int
        The height of the clipped destination dataset
    '''

    # Coordinate system for raw lat/lon coordinates
    geo_crs = rasterio.crs.CRS.from_epsg(4326)

    # Calculate dataset bounds in Lat/Lon
    src_geobounds = rasterio.warp.transform_bounds(src_crs, geo_crs, *src_bounds)

    # Replace any None values in our input geobounds with the calculated values
    clip_geobounds = tuple(clip or bnd for clip, bnd in zip(geobounds, src_geobounds))

    # Calculate clipped bounds in WebMercator (EPSG:3857)
    dst_bounds = rasterio.warp.transform_bounds(geo_crs, dst_crs, *clip_geobounds)

    # Get the window corresponding to the clipped bounds
    dst_window = rasterio.windows.from_bounds(*dst_bounds, dst_transform)

    # Update the transform to reflect the clipped bounds
    dst_transform = rasterio.windows.transform(dst_window, dst_transform)

    # Return the transform, width and height
    return dst_transform, int(dst_window.width), int(dst_window.height)

def build_vrt(vrtfile, files):
    '''
    Build a VRT file from a list of input files
    Based on https://pypi.org/project/rio-vrt/

    Parameters
    ----------
    vrtfile: str
        The path to the output VRT file
    files: list
        A list of input files to include in the VRT
    '''
    # Read global informations from the first file. None of these may change from file to file.
    with rasterio.open(files[0]) as dataset:
        crs = dataset.crs
        count = dataset.count
        dtypes = dataset.dtypes
        colorinterps = dataset.colorinterp
        indexes = dataset.indexes
        xres = dataset.res[0]
        yres = dataset.res[1]

    # Read all files to check attributes that must be invariant, and extract information on the spatial extend of the VRT
    lefts, bottoms, rights, tops = [], [], [], []
    for file in files:
        with rasterio.open(file) as dataset:
            # Ensure the crs and count match the first file
            if dataset.crs != crs:
                raise ValueError(f'The crs ({dataset.crs}) from file "{file}" is not {crs}')

            # Ensure the count matches the first file
            if dataset.count != count:
                raise ValueError(f'The band count ({dataset.count}) from file "{file}" is not {count}')

            # Ensure the dtypes match the first file
            if dataset.dtypes != dtypes:
                raise ValueError(f'The dtypes ({dataset.dtypes}) from file "{file}" do not match the first file')

            # Ensure the colorinterps match the first file
            if dataset.colorinterp != colorinterps:
                raise ValueError(f'The colorinterps ({dataset.colorinterp}) from file "{file}" do not match the first file')

            # Ensure the indexes match the first file
            if dataset.indexes != indexes:
                raise ValueError(f'The indexes ({dataset.indexes}) from file "{file}" do not match the first file')

            # Ensure the reolution matches the first file
            if dataset.res[0] != xres or dataset.res[1] != yres:
                raise ValueError(f'The resolution ({dataset.res}) from file "{file}" does not match the first file')

            # Append the spatial extend of the dataset
            lefts.append(dataset.bounds.left)
            rights.append(dataset.bounds.right)
            tops.append(dataset.bounds.top)
            bottoms.append(dataset.bounds.bottom)

    # Calculate the spatial extend of the merged dataset
    left = min(lefts)
    bottom = min(bottoms)
    right = max(rights)
    top = max(tops)

    # Start the tree with the VRTDataset element
    total_width = round((right - left) / xres)
    total_height = round((top - bottom) / yres)
    attr = {"rasterXSize": str(total_width), "rasterYSize": str(total_height)}
    vrt_dataset_xml = ET.Element("VRTDataset", attr)

    # Build the SRS element
    ET.SubElement(vrt_dataset_xml, "SRS").text = crs.wkt

    # Build the GeoTransform element
    transform = rasterio.Affine.from_gdal(left, xres, 0, top, 0, -yres)
    ET.SubElement(vrt_dataset_xml, "GeoTransform").text = ", ".join([str(i) for i in transform.to_gdal()])

    # Convert the rasterio dtypes to GDAL data types
    data_types = [{
        "byte": "Byte",
        "uint8": "Byte",
        "uint16": "UInt16",
        "int16": "Int16",
        "uint32": "UInt32",
        "int32": "Int32",
        "uint64": "UInt64",
        "int64": "Int64",
        "float32": "Float32",
        "float63": "Float64",
        "cint16": "CInt16",
        "cint32": "CInt32",
        "cfloat32": "CFloat32",
        "cfloat64": "CFloat64",
    }[dtypes[i-1]] for i in indexes]

    # Mosaic VRT file is organized by band first, then file
    vrt_raster_bands = {}
    for i in indexes:
        # Build the VRTRasterBand element
        attr = {"dataType": data_types[i-1], "band": str(i)}
        vrt_raster_bands[i] = ET.SubElement(vrt_dataset_xml, "VRTRasterBand", attr)

        # Add the ColorInterp element
        if colorinterps[i-1] != rasterio.enums.ColorInterp.undefined:
            ET.SubElement(vrt_raster_bands[i], "ColorInterp").text = colorinterps[i-1].name.capitalize()

    # Add each file's bands to the VRT
    for file in files:
        with rasterio.open(file) as dataset:
            src = rasterio.windows.Window(0, 0, dataset.width, dataset.height)
            dst = rasterio.windows.from_bounds(*dataset.bounds, transform)

        for i in indexes:
            # Build the Source element. No resampling needed as the source and destination resolutions are the same
            source = ET.SubElement(vrt_raster_bands[i], "ComplexSource")

            # Add the SourceFilename element
            ET.SubElement(source, "SourceFilename").text = file

            # Add the SourceBand element
            ET.SubElement(source, "SourceBand").text = str(i)

            # Add the SourceProperties element
            attr = { "RasterXSize": str(src.width), "RasterYSize": str(src.height), "DataType": data_types[i-1] }
            ET.SubElement(source, "SourceProperties", attr)

            # Add the SrcRect element representing the full size of the source dataset
            attr = {"xOff": str(src.col_off), "yOff": str(src.row_off), "xSize": str(src.width), "ySize": str(src.height)}
            ET.SubElement(source, "SrcRect", attr)

            # Add the DstRect element
            attr = {"xOff": str(dst.col_off), "yOff": str(dst.row_off), "xSize": str(dst.width), "ySize": str(dst.height)}
            ET.SubElement(source, "DstRect", attr)

            # Add the UseMaskBand element so overlapping datasets blend properly
            ET.SubElement(source, "UseMaskBand").text = "true"

    # Write the XML file
    with open(vrtfile, 'wb') as f:
        tree = ET.ElementTree(vrt_dataset_xml)
        ET.indent(tree, space="  ")
        tree.write(f)

    return vrtfile

def process(input_full_path, output_full_path, dataset_def, resolution, resampling):
    '''
    Process a single dataset, outputting a dataset reprojected to EPSG:3857

    Parameters
    ----------
    dataset_name: str
        The name of the dataset to process
    tmppath: str
        The path to the temporary directory
    resampling: str
        The resampling method to use when reprojecting the data. Can be one of nearest, bilinear, cubic, cubicspline, lanczos, average, mode

    Returns
    -------
    str
        The path to the reprojected dataset
    '''
    # Pre-process the file to expand any paletted bands to RGB.
    # This will overwrite the input file so we don't have to do it again.
    expand_to_rgb(input_full_path)

    # Open the dataset and read what we need
    with rasterio.open(input_full_path) as dataset:
        # Read the profile, crs, and transform
        profile = dataset.profile
        src_crs = dataset.crs
        dataset_transform = dataset.transform

    # Raise ValueError if crs is missing
    if not src_crs:
        raise ValueError(f'No projection information found in {dataset.name}')

    # Get the clip region as a rasterio window
    window = dataset_def['window']
    window = rasterio.windows.Window(*window)

    # Get the source transform, either from provided GCPs or calculated from the dataset's transform
    gcps = dataset_def.get('gcps', None)
    if gcps:
        src_transform = transform_from_gcps(gcps, src_crs, window)
    else:
        src_transform = rasterio.windows.transform(window, dataset_transform)

    # Calculate source bounds from the source transform and window size
    src_bounds = bounds_with_rotation(rasterio.windows.Window(0, 0, window.width, window.height), src_transform)

    # Define the destination coordinate system
    dst_crs = rasterio.crs.CRS.from_epsg(3857)

    # Calculate the size and transform of the reprojected dataset
    dst_transform, dst_width, dst_height = rasterio.warp.calculate_default_transform(src_crs, dst_crs, window.width, window.height, *src_bounds, resolution=resolution)

    # If geobounds are specified, we need to further clip the dataset to the specified bounds
    geobounds = dataset_def.get('geobound', None)
    if geobounds:
        dst_transform, dst_width, dst_height = clip_to_geobounds(geobounds, src_bounds, src_crs, dst_crs, dst_transform)

    # Read all bands from the dataset
    with rasterio.open(input_full_path) as dataset:
        src_data = dataset.read(window=window)

    # Build an alpha band to clip the dataset to any provided mask shapes
    masks = dataset_def.get('masks', None)
    if masks:
        # Get the transformation for shape drawing (shapes are specified in the original dataset coordinates)
        shape_transform = rasterio.Affine.translation(window.col_off, window.row_off)

        # Rasterize an alpha band based on the shapes
        alpha_data = rasterio.features.rasterize(masks, (window.height, window.width), 255, transform=shape_transform, dtype=src_data.dtype)
    else:
        # No mask. Entire source dataset is used. Create an alpha band with all values set to 255
        alpha_data = numpy.ones((window.height, window.width), dtype=src_data.dtype) * 255

    # Add the alpha band to the source data
    rgba_data = numpy.append(src_data, [alpha_data], axis=0)

    # Create new arrays for the reprojected data
    output_data = numpy.zeros((len(rgba_data), dst_height, dst_width), dtype=src_data.dtype)

    # Convert resampling method string to rasterio.warp.Resampling enum
    resampling = getattr(rasterio.warp.Resampling, 'cubic_spline' if resampling == 'cubicspline' else resampling)

    # Reproject each band to WebMercator (EPSG:3857)
    rasterio.warp.reproject(rgba_data, output_data, src_transform, src_crs=src_crs, dst_transform=dst_transform, dst_crs=dst_crs, resampling=resampling, num_threads=os.cpu_count())

    # Create a new dataset on disk with the reprojected data
    profile.update({
        'count': len(output_data),
        'crs': dst_crs,
        'transform': dst_transform,
        'width': dst_width,
        'height': dst_height,
        'compress': 'lzw',
    })

    # Write the reprojected data to disk
    with rasterio.open(output_full_path, 'w', **profile) as dst:
        dst.write(output_data)

def main():
    '''
    Main function to download Aeronav data from www.faa.gov and create web map tiles.
    '''

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Download Aeronav data from aeronav.faa.gov and create web map tiles.')
    # Where to put the data
    parser.add_argument('--zippath', help='Specify the directory to store downloaded Aeronav data.')
    parser.add_argument('--tmppath', default='/tmp/aeronav2tiles', help='Specify the directory to store temporary files. Default is /tmp/aeronav2tiles.')
    parser.add_argument('--outpath', help='Specify the directory to store the output tilesets.')
    # What to do
    parser.add_argument('--download', action='store_true', help='Download the Aeronav data from aeronav.faa.gov. If not specified, all zip files must already exist in zippath.')
    parser.add_argument('--all', action='store_true', help='Generate all tilesets, ignoring the --tilesets argument.')
    parser.add_argument('--tilesets', nargs='*', help='Specify the tilesets to generate.')
    parser.add_argument('--list-tilesets', action='store_true', help='List the available tilesets.')
    parser.add_argument('--existing', action='store_true', help='[DEVELOPMENT] Use existing reprojected datasets.')
    parser.add_argument('--single', help='[DEVELOPMENT] Process a single dataset.')
    parser.add_argument('--cleanup', action='store_true', help='Remove the temporary directory and its contents after all processing.')
    # How to do it
    parser.add_argument('--reproject-resampling', default='bilinear', help='Specify the resampling method to use when reprojecting the data. Can be one of nearest,bilinear,cubic,cubicspline,lanczos,average,mode. Default is bilinear.')
    parser.add_argument('--tile-resampling', default='bilinear', help='Specify the resampling method to use when creating the tiles. Can be one of nearest,bilinear,cubic,cubicspline,lanczos,average,mode. Default is bilinear.')
    parser.add_argument('--quiet', action='store_true', help='Suppress output and progress.')
    args = parser.parse_args()

    # List the available tilesets and exit
    if args.list_tilesets:
        for tileset in tileset_datasets.keys():
            print(f'{tileset}')
        return

    # Download the Aeronav data if the download flag is set
    if args.download and args.zippath:
        if not args.quiet:
            print('Scraping aeronav.faa.gov...')

        # Scrape all chart file URLs
        vfr_urls = get_current_aeronav_urls(_AERONAV_VFR_URL, ['sectional', 'terminalArea', 'helicopter', 'grandCanyon', 'Planning', 'caribbean'])
        ifr_urls = get_current_aeronav_urls(_AERONAV_IFR_URL, ['lowsHighsAreas', 'planning', 'caribbean', 'gulf'])

        # aeronav.faa.gov may have some incorrect URLs, so we need to clean them up
        vfr_urls = fix_faa_incorrect_urls(vfr_urls)
        ifr_urls = fix_faa_incorrect_urls(ifr_urls)

        # Create the zippath directory if it does not exist
        os.makedirs(args.zippath, exist_ok=True)

        # Download all the files
        for url in vfr_urls + ifr_urls:
            if not args.quiet:
                print(f'Downloading {url}...')
            download(url, os.path.join(args.zippath))

    # Unzip the downloaded files
    if args.zippath:
        # Create the temporary directory if it does not exist
        os.makedirs(args.tmppath, exist_ok=True)

        # Unzip all the files in the specified directory to the temporary directory
        for zip_filename in os.listdir(args.zippath):
            if zip_filename.endswith('.zip') and not zip_filename.startswith('._'):
                if not args.quiet:
                    print(f'Extracting {zip_filename}...')

                # Unzip everything in the zip file
                with zipfile.ZipFile(os.path.join(args.zippath, zip_filename), 'r') as zip_archive:
                    zip_archive.extractall(args.tmppath)

    # Process a single dataset if the single argument is set
    if args.single:
        # Get the dataset definition
        dataset_def = datasets[args.single]

        # Determine the input file path
        input_file = dataset_def.get('input_file', f'{args.single}.tif')
        input_full_path = os.path.join(args.tmppath, input_file)

        # Determine the output file path
        output_file = f'_{args.single}.tif'
        output_full_path = os.path.join(args.tmppath, output_file)

        # Find the tileset_def that contains this dataset
        tileset_def = next(tileset_def for tileset_def in tileset_datasets.values() if args.single in tileset_def['datasets'])

        # Calculate the reprojection resolution for this tileset. Always derived from a zoom level so no resampling is needed when merging.
        maxlod_zoom = tileset_def['maxlod_zoom']
        resolution = 2 * math.pi * 6378137 / 256 / 2 ** maxlod_zoom

        # Reproject the dataset
        process(input_full_path, output_full_path, dataset_def, resolution, args.reproject_resampling)
        return

    # Determine which tilesets to generate
    tilesets = tileset_datasets.keys() if args.all else args.tilesets or []

    # Process each tileset worth of data
    for tileset_name in tilesets:
        if not args.quiet:
            print(f'Processing tileset {tileset_name}...')

        # Get the list of dataset names required by the tileset and the tileset's zoom level
        tileset_def = tileset_datasets[tileset_name]

        # Calculate the reprojection resolution for this tileset. Always derived from a zoom level so no resampling is needed when merging.
        maxlod_zoom = tileset_def['maxlod_zoom']
        resolution = 2 * math.pi * 6378137 / 256 / 2 ** maxlod_zoom

        # Create a list to hold the file paths of the reprojected datasets
        reprojected_files = []

        # Process each source dataset required by the tileset
        dataset_names = tileset_def['datasets']
        for dataset_name in dataset_names:

            # Determine the output file path
            output_file = f'_{dataset_name}.tif'
            output_full_path = os.path.join(args.tmppath, output_file)

            if not args.existing:
                # Get the dataset definition
                dataset_def = datasets[dataset_name]

                # Determine the input file path
                input_file = dataset_def.get('input_file', f'{dataset_name}.tif')
                input_full_path = os.path.join(args.tmppath, input_file)

                if not args.quiet:
                    print(f'Reprojecting {dataset_name}')

                # Reproject the dataset and return the path to the reprojected dataset
                process(input_full_path, output_full_path, dataset_def, resolution, args.reproject_resampling)

            # Add the reprojected dataset to the list
            reprojected_files.append(output_full_path)

        if not args.quiet:
            print(f'Building VRT for {tileset_name}')

        # Build a VRT file from the reprojected datasets
        vrt_path = os.path.join(args.tmppath, f'__{tileset_name}.vrt')
        build_vrt(vrt_path, reprojected_files)

        # Create the tileset from the VRT file
        if args.outpath:
            # Create the output tileset directory if it does not exist
            tile_path = os.path.join(args.outpath, tileset_def['tile_path'])
            os.makedirs(tile_path, exist_ok=True)

            # Get the zoom levels needed for the tileset
            zoom = tileset_def['zoom']

            if not args.quiet:
                print(f'Building tiles for {tileset_name}')

            # Build the tile pyramid from the VRT file
            resampling = 'near' if args.tile_resampling == 'nearest' else args.tile_resampling
            quiet = '-q' if args.quiet else ''

            # For now, call gdal2tiles until rasterio supports tile creation
            osgeo_utils.gdal2tiles.main([quiet, '-x', '-z', zoom, '-w', 'leaflet', '-r', resampling, f'--processes={os.cpu_count()}', '--tiledriver=WEBP', vrt_path, tile_path])

    # Remove the temporary directory and its contents if remove is True
    if args.cleanup:
        if not args.quiet:
            print('Cleaning up temporary files...')

        shutil.rmtree(args.tmppath)

if __name__ == '__main__':
    main()
