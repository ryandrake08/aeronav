#!/usr/bin/env python3
'''
This script processes FAA Aeronav raster chart images and converts them into
web map tile pyramids suitable for tile servers.

Use aeronav_download.py to download the chart ZIP files first.

Command Line Arguments:
    --zippath: Specify the directory containing downloaded Aeronav ZIP files.
    --tmppath: Specify the directory to store temporary files (default: /tmp/aeronav2tiles).
    --outpath: Specify the directory to store the output tilesets.
    --all: Generate all tilesets.
    --tilesets: Specify the tilesets to generate.
    --existing: [DEVELOPMENT] Use existing reprojected datasets.
    --epsg: Specify the destination EPSG code (default: 3857 for Web Mercator).
    --reproject-resampling: Specify the resampling method to use when reprojecting the data (default: bilinear). Can be one of nearest, bilinear, cubic, cubicspline, lanczos, average, mode.
    --tile-resampling: Specify the resampling method to use when creating tiles (default: bilinear).
    --cleanup: Remove the temporary directory and its contents after processing.

Usage:
    python aeronav_download.py /path/to/zips
    python aeronav2tiles.py --all --zippath /path/to/zips --tmppath /path/to/tmp --outpath /path/to/output --cleanup

Raises:
    ValueError: If no projection information is found in the dataset.
'''

import argparse
import json
import math
import os
import shutil
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ProcessPoolExecutor

import numpy
import rasterio.control
import rasterio.crs
import rasterio.enums
import rasterio.features
import rasterio.transform
import rasterio.warp
import rasterio.windows

# Global config storage (loaded lazily)
datasets: dict = {}
tileset_datasets: dict = {}

def _default_config_path():
    """Return the default config path (aeronav.conf.json next to this script)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'aeronav.conf.json')

def load_config(config_path=None):
    """Load datasets and tilesets from JSON config file."""
    global datasets, tileset_datasets

    if config_path is None:
        config_path = _default_config_path()

    with open(config_path, 'r') as f:
        config = json.load(f)

    # Convert datasets to expected format
    datasets = {}
    for name, ds in config['datasets'].items():
        entry = {}
        if 'input_file' in ds:
            entry['input_file'] = ds['input_file']
        if 'mask' in ds:
            entry['mask'] = ds['mask']
        if 'geobound' in ds:
            # Convert list to tuple
            entry['geobound'] = tuple(ds['geobound'])
        if 'gcps' in ds:
            # Convert list of lists to list of tuples
            entry['gcps'] = [tuple(gcp) for gcp in ds['gcps']]
        if 'max_lod' in ds:
            entry['max_lod'] = ds['max_lod']
        datasets[name] = entry

    # Convert tilesets to expected format
    tileset_datasets = {}
    for name, ts in config['tilesets'].items():
        tileset_datasets[name] = {
            'tile_path': ts['tile_path'],
            'datasets': ts['datasets'],
        }

    return datasets, tileset_datasets

# ZIP extraction

def extract_zip(args_tuple):
    '''Extract a single zip file to a destination directory, skipping if all files already exist'''
    zip_path, zip_filename, dest_path, quiet = args_tuple
    zip_full_path = os.path.join(zip_path, zip_filename)

    # Check if all files in the zip already exist in the destination
    with zipfile.ZipFile(zip_full_path, 'r') as zip_archive:
        members = zip_archive.namelist()
        all_exist = all(os.path.exists(os.path.join(dest_path, member)) for member in members if not member.endswith('/'))

    if all_exist:
        return zip_filename

    if not quiet:
        print(f'  Extracting {zip_filename}')
    with zipfile.ZipFile(zip_full_path, 'r') as zip_archive:
        zip_archive.extractall(dest_path)
    if not quiet:
        print(f'  Extracted {zip_filename}')
    return zip_filename

# Preprocessing step

def expand_to_rgb(args_tuple):
    '''
    If the file contains a colormap band, expand it to RGB

    Parameters
    ----------
    args_tuple: tuple
        A tuple of (filename, quiet) where filename is the path to the dataset

    '''
    filename, quiet = args_tuple
    basename = os.path.basename(filename)

    with rasterio.open(filename, 'r') as dataset:
        # If the dataset is not a single paletted band, nothing to do
        if dataset.count > 1 or dataset.colorinterp[0] != rasterio.enums.ColorInterp.palette:
            return

        if not quiet:
            print(f'  Expanding {basename}')

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

    if not quiet:
        print(f'  Expanded {basename}')

# Major steps in the processing pipeline

def transform_from_gcps(gcps, src_crs):
    '''
    Calculate an affine transformation from a list of tuples in the dataset definition

    Parameters
    ----------
    gcps: list
        A list of tuples: (pixel_x, pixel_y, lon, lat)

    src_crs: CRS
        The dataset's CRS. We assume it is suitable for the new transformation

    Returns
    -------
    Affine
        An affine transformation matrix representing the new transformation
    '''
    # Coordinate system for raw lat/lon coordinates
    geo_crs = rasterio.crs.CRS.from_epsg(4326)

    # Get the GCP definitions into separate lists
    gcp_tuple = zip(*gcps)
    xs, ys, lons, lats = gcp_tuple  # type: ignore[misc]

    # Transform the lat/lon coordinates to the dataset CRS
    src_xs, src_ys = rasterio.warp.transform(geo_crs, src_crs, lons, lats)  # type: ignore[assignment]

    # Create list of GroundControlPoints mapping win_x, win_y to dst_x, dst_y
    gcps_src = [rasterio.control.GroundControlPoint(*point) for point in zip(ys, xs, src_xs, src_ys)]

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
    Calculate the clipped bounds and dimensions for a reprojection based on the geobounds specified in Lat/Lon

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
        The destination transformation adjusted for the clipped bounds
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

    # Calculate clipped bounds in destination CRS
    dst_bounds = rasterio.warp.transform_bounds(geo_crs, dst_crs, *clip_geobounds)

    # Get the window corresponding to the clipped bounds
    dst_window = rasterio.windows.from_bounds(*dst_bounds, dst_transform)  # type: ignore[call-arg]

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

            # Track the finest resolution (smallest pixel size) for the VRT
            if dataset.res[0] < xres:
                xres = dataset.res[0]
            if dataset.res[1] < yres:
                yres = dataset.res[1]

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
            src = rasterio.windows.Window(0, 0, dataset.width, dataset.height)  # type: ignore[call-arg]
            dst = rasterio.windows.from_bounds(*dataset.bounds, transform)  # type: ignore[call-arg]

        for i in indexes:
            # Build the Source element. GDAL handles resampling when source resolution differs from VRT resolution
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


def build_zoom_vrt(tileset_name, tileset_def, all_datasets, zoom, tmppath):
    """
    Build a VRT for a specific zoom level with appropriately ordered datasets.

    At each zoom level Z, we include datasets where max_lod >= Z. Datasets are
    ordered by max_lod DESCENDING so that smaller max_lod datasets (which are
    more appropriate for this zoom level) appear last and render on top.

    Parameters
    ----------
    tileset_name: str
        The name of the tileset (for VRT filename)
    tileset_def: dict
        Tileset definition with 'datasets' list
    all_datasets: dict
        Dict of all dataset definitions with 'max_lod' values
    zoom: int
        The zoom level to build the VRT for
    tmppath: str
        Directory containing reprojected TIF files

    Returns
    -------
    str or None
        Path to the VRT file, or None if no datasets are appropriate
    """
    from tile_manifest import get_datasets_for_zoom, get_reprojected_tif_path

    # Get datasets for this zoom, ordered by max_lod descending
    dataset_names = get_datasets_for_zoom(tileset_def, all_datasets, zoom)

    if not dataset_names:
        return None

    # Convert dataset names to file paths (datasets were ordered for VRT stacking)
    files = []
    for name in dataset_names:
        tif_path = get_reprojected_tif_path(tmppath, name)
        if os.path.exists(tif_path):
            files.append(tif_path)

    if not files:
        return None

    # Build the VRT file
    vrt_path = os.path.join(tmppath, f'__{tileset_name}__z{zoom}.vrt')
    return build_vrt(vrt_path, files)


def calculate_resolution_for_zoom(zoom_level, epsg_code):
    '''
    Calculate the resolution in meters per pixel for a given zoom level and EPSG code.

    This calculation is based on the standard web map tile pyramid, where the world
    is divided into 256x256 pixel tiles, with 2^zoom tiles at each zoom level.

    Parameters
    ----------
    zoom_level: int
        The zoom level (0 = world, higher = more detailed)
    epsg_code: int
        The EPSG code of the coordinate system (e.g., 3857 for Web Mercator, 3395 for Mercator)

    Returns
    -------
    float
        The resolution in meters per pixel
    '''
    # Use WGS84 equatorial radius for both Web Mercator (3857) and Mercator (3395)
    # Both projections use the same basic calculation for resolution at zoom levels
    earth_radius = 6378137  # WGS84 equatorial radius in meters
    return 2 * math.pi * earth_radius / 256 / 2 ** zoom_level

def get_center_latitude_from_bounds(bounds, src_crs):
    '''
    Get the center latitude of a bounding box in radians.

    Transforms the center point from source CRS to WGS84 to get the latitude.
    This is used to adjust the output resolution for Web Mercator distortion.
    At high latitudes, the same EPSG:3857 resolution represents finer ground
    resolution, so we coarsen the output to avoid upscaling.

    Parameters
    ----------
    bounds: tuple
        The bounding box (left, bottom, right, top) in source CRS coordinates
    src_crs: CRS
        The source coordinate reference system

    Returns
    -------
    float
        The center latitude in radians
    '''
    from rasterio.warp import transform as warp_transform

    left, bottom, right, top = bounds
    center_x = (left + right) / 2.0
    center_y = (bottom + top) / 2.0

    # Transform center point to WGS84
    wgs84 = rasterio.crs.CRS.from_epsg(4326)
    xs, ys = warp_transform(src_crs, wgs84, [center_x], [center_y])

    # rasterio returns (x, y) = (lon, lat) for geographic CRS
    lat = ys[0]

    # Validate latitude is in reasonable range
    if lat < -90.0 or lat > 90.0:
        raise ValueError(f"Invalid latitude {lat:.2f} from coordinate transform")

    return math.radians(lat)

def process(input_full_path, output_full_path, dataset_def, resolution, resampling, dst_epsg=3857, num_threads=None, dataset_name=None, quiet=False):
    '''
    Process a single dataset, outputting a dataset reprojected to the specified EPSG coordinate system

    Parameters
    ----------
    input_full_path: str
        The full path to the input dataset
    output_full_path: str
        The full path to the output reprojected dataset
    dataset_def: dict
        The dataset definition containing mask, gcps, and geobound information
    resolution: float
        The target resolution in meters per pixel
    resampling: str
        The resampling method to use when reprojecting the data. Can be one of nearest, bilinear, cubic, cubicspline, lanczos, average, mode
    dst_epsg: int
        The destination EPSG code (default: 3857 for Web Mercator)
    num_threads: int or None
        Number of threads for reprojection (default: None uses os.cpu_count())
    dataset_name: str or None
        Name of the dataset for progress reporting
    quiet: bool
        If True, suppress progress output

    Returns
    -------
    None
        The reprojected dataset is written to output_full_path
    '''
    if num_threads is None:
        num_threads = os.cpu_count() or 1

    if not quiet:
        print(f'  Reprojecting {dataset_name}')

    # Open the dataset and read what we need
    with rasterio.open(input_full_path) as dataset:
        # Read the profile, crs, and transform
        profile = dataset.profile
        src_crs = dataset.crs
        dataset_transform = dataset.transform

    # Raise ValueError if crs is missing
    if not src_crs:
        raise ValueError(f'No projection information found in {dataset.name}')

    # Get the dataset's mask shape
    mask = dataset_def['mask']

    # Determine a bounding window from the first ring in the mask polygon
    outer_ring = mask[0]
    left = min(pt[0] for pt in outer_ring)
    top = min(pt[1] for pt in outer_ring)
    right = max(pt[0] for pt in outer_ring)
    bottom = max(pt[1] for pt in outer_ring)
    window = rasterio.windows.Window(left, top, right-left, bottom-top)  # type: ignore[call-arg]

    # Override the dataset's transform if GCPs are provided
    gcps = dataset_def.get('gcps', None)
    if gcps:
        dataset_transform = transform_from_gcps(gcps, src_crs)

    # Calculate source bounds from the dataset's transform, adjusting for the window
    src_bounds = bounds_with_rotation(window, dataset_transform)

    # Adjust resolution for latitude (Web Mercator distortion).
    # The input 'resolution' is equatorial; we coarsen it at high latitudes
    # to avoid upscaling the source data.
    center_lat = get_center_latitude_from_bounds(src_bounds, src_crs)
    adjusted_resolution = resolution / math.cos(center_lat)

    # Adjust the dataset transform to the window
    src_transform = rasterio.windows.transform(window, dataset_transform)

    # Define the destination coordinate system
    dst_crs = rasterio.crs.CRS.from_epsg(dst_epsg)

    # Calculate the size and transform of the reprojected dataset
    dst_transform, dst_width, dst_height = rasterio.warp.calculate_default_transform(src_crs, dst_crs, window.width, window.height, *src_bounds, resolution=adjusted_resolution)

    # Ensure dst_width and dst_height are integers (they should be from calculate_default_transform)
    assert isinstance(dst_width, int) and isinstance(dst_height, int)

    # If geobounds are specified, we need to further clip the dataset to the specified bounds
    geobounds = dataset_def.get('geobound', None)
    if geobounds:
        dst_transform, dst_width, dst_height = clip_to_geobounds(geobounds, src_bounds, src_crs, dst_crs, dst_transform)

    # Read all bands from the dataset
    with rasterio.open(input_full_path) as dataset:
        src_data = dataset.read(window=window)

    # Create a shape suitable to rasterize
    shape = {'type': 'Polygon', 'coordinates': mask}

    # Get the transformation for shape drawing (shapes are specified in pixel coordinates relative to the full image)
    shape_transform = rasterio.Affine.translation(window.col_off, window.row_off)

    # Rasterize an alpha band based on the shapes
    alpha_data = rasterio.features.rasterize([shape], (window.height, window.width), transform=shape_transform, default_value=255, dtype='uint8')

    # Add the alpha band to the source data
    rgba_data = numpy.append(src_data, [alpha_data], axis=0)

    # Create new arrays for the reprojected data
    output_data = numpy.zeros((len(rgba_data), dst_height, dst_width), dtype=src_data.dtype)

    # Convert resampling method string to rasterio.warp.Resampling enum
    resampling = getattr(rasterio.warp.Resampling, 'cubic_spline' if resampling == 'cubicspline' else resampling)

    # Reproject each band to the destination CRS
    rasterio.warp.reproject(rgba_data, output_data, src_transform, src_crs=src_crs, dst_transform=dst_transform, dst_crs=dst_crs, resampling=resampling, num_threads=num_threads)

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

    if not quiet:
        print(f'  Reprojected {dataset_name}')

def main():
    '''
    Main function to process Aeronav data and create web map tiles. How this works:

    1. Unzip the downloaded files

        Arguments used: --zippath, --tmppath

        The ZIP files contain a bunch of GeoTIFF files, which we extract to a temporary directory.

    2. Choose which tilesets to generate

        Arguments used: --all, --tilesets

        The main organizational unit for our output tiles is the "tileset". Each tileset is a collection of related
        datasets that represent a chart series. All data in a given tileset is to be mosaiced together in the final
        map, and each tileset can be enabled or disabled separately. This program defines the following tilesets:

            VFR Planning Charts
            VFR Sectional Charts
            VFR Sectional Detail Charts
            VFR Terminal Area Charts
            VFR Flyway Charts
            Helicopter Route Charts
            Helicopter Route Detail Charts
            IFR Planning Charts U.S.
            IFR Enroute High And Low Altitude Pacific
            IFR Enroute High Altitude Alaska
            IFR Enroute High Altitude Alaska Detail
            IFR Enroute High Altitude Caribbean And South America
            IFR Enroute High Altitude U.S.
            IFR Enroute Low Altitude Alaska
            IFR Enroute Low Altitude Caribbean And South America
            IFR Enroute Low Altitude Pacific
            IFR Enroute Low Altitude U.S.
            IFR Area Charts Alaska
            IFR Area Charts Caribbean And South America
            IFR Area Charts Pacific
            IFR Area Charts U.S.
            North Atlantic Route Planning Chart
            North Pacific Route Planning Chart
            North Pacific Route Planning Detail Chart
            West Atlantic Route System Planning Chart
            IFR Gulf Of Mexico Chart

        Importantly, each tileset has a range of zoom levels at which the data will be visible, and the output map
        tiles are converted to the resolution of each tileset's maximum zoom level. This means that merging datasets
        within the same tileset requires no resampling, and that building the tiles for the highest level of detail
        requires no resampling, either.

    3. Process each dataset within the chosen tilesets

        Steps 3a through 3d are applied to each dataset in the chosen tilesets. A "dataset" represents a single
        chunk of data to include in the map for each tileset. Most datasets correspond 1:1 with the input GeoTIFF
        files, but some datasets represent subsets of a GeoTIFF file. For example, the "IFR High Altitude U.S."
        tileset includes the following datasets:

            ENR_H01
            ENR_H02
            ENR_H03
            ENR_H04
            ENR_H05
            ENR_H06
            ENR_H07
            ENR_H08
            ENR_H09
            ENR_H10
            ENR_H11
            ENR_H12

        Each of those datasets correspond to a single GeoTIFF file from the FAA.

    3a. Expand all input files to RGB if they contain a colormap band

        Some files, as downloaded from the FAA, contain a single band with a colormap palette. We need to expand
        these to three bands (RGB) so that they can be reprojected and tiled properly. If we didn't do this, or
        did this after reprojection, the resulting colors would be incorrect. This step is done only if needed, and
        is done in-place with the original files overwritten. Therefore, subsequent runs of this program do not
        need to repeat this (io-expensive) step.

    3b. Clip invalid data from the datasets

        Each dataset is a rasterized paper chart, and as such, contains descriptive material, ledgends, insets, and
        other non-map data. We use hard-coded polygons to clip this data out of the datasets. Clipping reduces the
        size of the final map tiles and allows all datasets in a tileset to seamlessly blend together. Clipping is
        done both in pixel coordinates and (in some cases) in Lat/Lon coordinates.

    3c. Re-georeference some of the datasets

        The GeoTIFF files from the FAA are georeferenced, but we also take data from insets within those files, and
        these insets are not georeferenced. We use hard coded Ground Control Points (GCPs) to georeference these.

    3d. Reproject the datasets to the destination CRS

        Arguments used: --reproject-resampling, --epsg

        The FAA data is in a variety of projections, so we reproject everything to the destination
        coordinate system specified by --epsg (default: 3857 for Web Mercator)

    4. Combine each tileset's datasets into a single file

        We use a single .vrt file for each tileset, since from now on we treat all datasets in a tileset as a single
        entity. The .vrt file is a virtual raster file that points to all the individual datasets in the tileset.

    5. Generate map tiles for each tileset

        Arguments used: --outpath, --tile-resampling

        Finally, we generate map tiles for each tileset, for each zoom level, and save each tileset's tiles to a
        separate directory using rasterio_tiles.generate_tiles().

    '''

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Process Aeronav data and create web map tiles.')
    # Config file
    parser.add_argument('-c', '--config', default=_default_config_path(), help='Path to config file.')
    # Where to put the data
    parser.add_argument('--zippath', help='Directory containing downloaded Aeronav ZIP files.')
    parser.add_argument('-t', '--tmppath', default='/tmp/aeronav2tiles', help='Directory for temporary files. Default: /tmp/aeronav2tiles.')
    parser.add_argument('-o', '--outpath', help='Output directory for tilesets.')
    # What to do
    parser.add_argument('-s', '--tilesets', default='all', help='Comma-separated tileset names. Default: all.')
    parser.add_argument('-l', '--list', action='store_true', help='List available tilesets and exit.')
    parser.add_argument('--existing', action='store_true', help='[DEV] Use existing reprojected datasets.')
    parser.add_argument('--single', help='[DEV] Process a single dataset.')
    parser.add_argument('-C', '--cleanup', action='store_true', help='Remove temp directory after processing.')
    # How to do it
    parser.add_argument('-e', '--epsg', type=int, default=3857, help='Target EPSG code. Default: 3857.')
    parser.add_argument('--reproject-resampling', default='bilinear', help='Resampling for reprojection. Default: bilinear.')
    parser.add_argument('--tile-resampling', default='bilinear', help='Resampling for tile generation. Default: bilinear.')
    parser.add_argument('-f', '--format', default='webp', choices=['png', 'jpeg', 'webp'], help='Tile format. Default: webp.')
    parser.add_argument('-q', '--quiet', action='store_true', help='Suppress output.')
    # Parallel processing
    parser.add_argument('-j', '--jobs', type=int, default=os.cpu_count(), help=f'Concurrent dataset processes. Default: {os.cpu_count()}.')
    parser.add_argument('-w', '--tile-workers', type=int, default=os.cpu_count(), help=f'Parallel workers for tile generation. Default: {os.cpu_count()}.')
    args = parser.parse_args()

    # Load config file
    load_config(args.config)

    # Number of CPUs present
    cpu_count = os.cpu_count() or 1

    # List the available tilesets and exit
    if args.list:
        for tileset in tileset_datasets.keys():
            print(f'{tileset}')
        return

    # Unzip the downloaded files
    if args.zippath:
        # Create the temporary directory if it does not exist
        os.makedirs(args.tmppath, exist_ok=True)

        # Collect zip files to extract
        zip_files = [f for f in os.listdir(args.zippath) if f.endswith('.zip') and not f.startswith('._')]

        if zip_files:
            if not args.quiet:
                print(f'Extracting {len(zip_files)} zip files using {cpu_count} parallel processes')

            # Create work items as tuples for the module-level extract_zip function
            work_items = [(args.zippath, f, args.tmppath, args.quiet) for f in zip_files]

            with ProcessPoolExecutor(max_workers=cpu_count) as executor:
                list(executor.map(extract_zip, work_items))

    # Determine which tilesets to generate
    if args.single:
        tilesets = [tileset_name for tileset_name, tileset_def in tileset_datasets.items() if args.single in tileset_def['datasets']]
    elif args.tilesets.lower() == 'all':
        tilesets = tileset_datasets.keys()
    else:
        tilesets = [t.strip() for t in args.tilesets.split(',')]

    # Phase 1: Collect all work items from all tilesets
    all_work_items = []
    tileset_reprojected_files = {}  # tileset_name -> list of reprojected file paths

    for tileset_name in tilesets:
        tileset_def = tileset_datasets[tileset_name]

        reprojected_files = []
        dataset_names = tileset_def['datasets']

        for dataset_name in dataset_names:
            # If we are only doing a single dataset, skip the rest
            if args.single and args.single != dataset_name:
                continue

            # Determine the output file path
            output_file = f'_{dataset_name}.tif'
            output_full_path = os.path.join(args.tmppath, output_file)

            if not args.existing:
                # Get the dataset definition
                dataset_def = datasets[dataset_name]

                # Determine the input file path
                input_file = dataset_def.get('input_file', f'{dataset_name}.tif')
                input_full_path = os.path.join(args.tmppath, input_file)

                # Calculate equatorial resolution - will be adjusted for latitude in process()
                dataset_max_lod = dataset_def.get('max_lod', 12)
                equatorial_resolution = calculate_resolution_for_zoom(dataset_max_lod, args.epsg)

                # Collect work item for parallel processing
                all_work_items.append({
                    'dataset_name': dataset_name,
                    'input_full_path': input_full_path,
                    'output_full_path': output_full_path,
                    'dataset_def': dataset_def,
                    'resolution': equatorial_resolution,
                })

            reprojected_files.append(output_full_path)

        tileset_reprojected_files[tileset_name] = reprojected_files

    # Phase 2: Process all datasets from all tilesets in one batch
    if all_work_items:
        # Sort work items by estimated work (largest first) to reduce straggler effect
        # Use mask area if available, otherwise fall back to file dimensions
        def estimate_work(item):
            mask = item['dataset_def'].get('mask')
            if mask:
                outer_ring = mask[0]
                width = max(pt[0] for pt in outer_ring) - min(pt[0] for pt in outer_ring)
                height = max(pt[1] for pt in outer_ring) - min(pt[1] for pt in outer_ring)
                return width * height
            else:
                with rasterio.open(item['input_full_path']) as ds:
                    return ds.width * ds.height

        all_work_items.sort(key=estimate_work, reverse=True)

        # Pre-expand all unique input files to RGB in parallel
        unique_inputs = list(set(item['input_full_path'] for item in all_work_items))
        if not args.quiet:
            print(f'Expanding {len(unique_inputs)} input files to RGB using {cpu_count} parallel processes')
        expand_work_items = [(f, args.quiet) for f in unique_inputs]
        with ProcessPoolExecutor(max_workers=cpu_count) as executor:
            list(executor.map(expand_to_rgb, expand_work_items))

        # Reproject all datasets in parallel
        concurrent_processes = min(args.jobs, len(all_work_items))
        num_threads = max(1, cpu_count // concurrent_processes)

        if not args.quiet:
            print(f'Reprojecting {len(all_work_items)} datasets using {concurrent_processes} parallel processes ({num_threads} threads each)')
        with ProcessPoolExecutor(max_workers=args.jobs) as executor:
            futures = []
            for item in all_work_items:
                future = executor.submit(
                    process,
                    item['input_full_path'],
                    item['output_full_path'],
                    item['dataset_def'],
                    item['resolution'],
                    args.reproject_resampling,
                    args.epsg,
                    num_threads=num_threads,
                    dataset_name=item['dataset_name'],
                    quiet=args.quiet,
                )
                futures.append(future)

            # Wait for all futures to complete
            for future in futures:
                future.result()  # Raises any exception that occurred

    # Phase 3: Build zoom-specific VRTs and generate tiles for each tileset
    for tileset_name in tilesets:
        tileset_def = tileset_datasets[tileset_name]
        reprojected_files = tileset_reprojected_files[tileset_name]

        if not reprojected_files:
            continue

        # Create the tileset from zoom-specific VRTs
        if args.outpath:
            # Create the output tileset directory if it does not exist
            tile_path = os.path.join(args.outpath, tileset_def['tile_path'])
            os.makedirs(tile_path, exist_ok=True)

            from tile_manifest import compute_tile_manifest, manifest_summary, get_tileset_zoom_range
            from rasterio_tiles import generate_tiles_multi_zoom

            # Derive zoom range from datasets: min=0, max=max(max_lod)
            min_zoom, max_zoom = get_tileset_zoom_range(tileset_def, datasets)

            # Compute tile manifest based on dataset coverage and max_lod
            tile_manifest = compute_tile_manifest(
                tileset_def=tileset_def,
                datasets=datasets,
                tmppath=args.tmppath,
                zoom_min=min_zoom,
                zoom_max=max_zoom,
            )

            if not args.quiet:
                print(f'Building tiles for {tileset_name}')
                print(manifest_summary(tile_manifest))

            # Build all zoom-specific VRTs upfront
            # Each zoom level Z uses a VRT containing only datasets where max_lod >= Z,
            # ordered so that smaller max_lod datasets (more appropriate for that zoom)
            # are rendered on top.
            vrt_paths = {}
            for zoom in range(min_zoom, max_zoom + 1):
                # Skip zoom levels with no tiles
                if zoom not in tile_manifest or not tile_manifest[zoom]:
                    continue

                # Build zoom-specific VRT
                vrt_path = build_zoom_vrt(tileset_name, tileset_def, datasets, zoom, args.tmppath)
                if vrt_path is not None:
                    vrt_paths[zoom] = vrt_path

            # Generate all tiles in a single parallel phase
            generate_tiles_multi_zoom(
                vrt_paths=vrt_paths,
                output_path=tile_path,
                tile_manifest=tile_manifest,
                resampling=args.tile_resampling,
                tile_format=args.format.upper(),
                num_processes=args.tile_workers,
                quiet=args.quiet,
            )

    # Remove the temporary directory and its contents if remove is True
    if args.cleanup:
        if not args.quiet:
            print('Cleaning up temporary files...')

        shutil.rmtree(args.tmppath)

if __name__ == '__main__':
    main()
