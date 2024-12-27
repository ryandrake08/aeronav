#!/usr/bin/env python3
# pylint: disable=line-too-long, too-many-arguments, too-many-locals

'''
This script downloads Aeronav raster images from the FAA, unzips them, and
process them into a format suitable for use with a web map tile server.

Command Line Arguments:
    --zippath: Specify the directory to store downloaded Aeronav data.
    --tmppath: Specify the directory to store temporary files (default: /tmp/aeronav2tiles).
    --outpath: Tile output directory (default: .).
    --resampling: Specify the resampling method to use when reprojecting the data (default: nearest).
    --threads: Specify the number of threads to use when reprojecting the data (default: 4).
    --cleanup: Remove the temporary directory and its contents after processing.

Usage:
    python aeronav2tiles.py --current --zippath /path/to/zips --tmppath /path/to/tmp --outpath /path/to/output --resampling bilinear --threads 8 --cleanup

Raises:
    ValueError: If no projection information is found in the dataset.
'''

import argparse
import os
import shutil
import subprocess
import zipfile

import numpy
import rasterio.features
import rasterio.transform
import rasterio.warp
import rasterio.windows

import aeronav_datasets

def rasterize_shape_masks(masks, window):
    '''
    Rasterize a shape mask for a dataset

    Parameters
    ----------
    masks: list
        A list of masks, where each mask is a list of tuples representing the vertices of a polygon
        Vertices are given in pixel coordinates, with the origin at the top left of the dataset.
        For example, two four-sided shapes would look like:
        [
          [(x1, y1), (x2, y2), (x3, y3), (x4, y4)],
          [(x5, y5), (x6, y6), (x7, y7), (x8, y8)]
        ]

    window: Window
        The window (dataset pixel coordinates) to rasterize the mask into

    Returns
    -------
    numpy.ndarray
        A 2D numpy array with the mask rasterized into it
    '''
    # Get the transformation for shape drawing (shapes are specified in the original dataset coordinates)
    shape_transform = rasterio.Affine.translation(window.col_off, window.row_off)

    # Get the mask shapes into a format acceptable to rasterio
    shapes = [({'type': 'Polygon', 'coordinates': [mask]},0) for mask in masks]

    # Workaround bug: If any shapes have less than 4 points, add the first point to the end to close the polygon
    for shape in shapes:
        if len(shape[0]['coordinates'][0]) < 4:
            shape[0]['coordinates'][0].append(shape[0]['coordinates'][0][0])

    # Rasterize an alpha band based on the shapes
    return rasterio.features.rasterize(shapes, (window.height, window.width), 255, transform=shape_transform, dtype=numpy.uint8)

def color_expand(src_data, colormap):
    '''
    Expand a single band of paletted data to RGB

    Parameters
    ----------
    src_data: numpy.ndarray
        A 2D numpy array with the paletted data
    colormap: dict
        A dictionary mapping palette indexes to RGB values

    Returns
    -------
    numpy.ndarray
        A 3D numpy array with the RGB data
    '''
    # Create new arrays for the expanded data
    expanded_data = numpy.zeros((3, src_data[0].shape[0], src_data[0].shape[1]), dtype=src_data.dtype)

    # Transpose color table into four 256 element arrays, one for each band
    colormap_lookup = numpy.array([colormap[i] for i in range(min(256, len(colormap)))]).T

    # Expand the single band to three RGB bands, line by line for good performance
    for band in range(3):
        for iy in range(src_data[0].shape[0]):
            expanded_data[band][iy] = numpy.take(colormap_lookup[band], src_data[0][iy])

    return expanded_data

def transform_from_gcps(gcps, src_crs, window):
    '''
    Calculate an affine transformation from a list of tuples in aeronav_datasets.py

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

    # Get the GCPs into separate lists
    xs, ys, lons, lats = zip(*gcps)

    # Adjust the gcp coordinates to the window
    win_xs = [x - window.col_off for x in xs]
    win_ys = [y - window.row_off for y in ys]

    # Transform the lat/lon coordinates to the dataset CRS
    src_xs, src_ys = rasterio.warp.transform(geo_crs, src_crs, lons, lats)

    # Create list of GroundControlPoints mapping win_x, win_y to dst_x, dst_y
    gcps_src = [rasterio.control.GroundControlPoint(win_y, win_x, src_x, src_y) for win_x, win_y, src_x, src_y in zip(win_xs, win_ys, src_xs, src_ys)]

    # Return a new transformation from the GCPs
    return rasterio.transform.from_gcps(gcps_src)

# Replacement for rasterio.windows.bounds that works with rotated datasets
def bounds_with_rotation(window, transform):
    '''Get the spatial bounds of a window, allowing for rotation

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

# Calculate default transform and dimensions for a reprojection, handling antimeridian crossing
def calculate_default_transform_antimeridian(src_crs, dst_crs, width, height, left, bottom, right, top):
    '''
    Wraps rasterio.warp.calculate_default_transform to handle antimeridian crossing

    Whithout specifying a resolution, rasterio.warp.calculate_default_transform will use GDAL's method which
    attempts to preserve the whole resolution of the source dataset. This will lead to incorrect results when
    the source dataset crosses the antimeridian.

    This function calculates the correct destination resolution using a false CRS that is centered on the
    antimeridian, then recalculates the transformation using the correct destination CRS and the known resolution.

    I've only tried this with WebMercator (EPSG:3857) as the destination CRS.
    '''
    # Create a false CRS that is centered on the antimeridian
    crs_parameters = dst_crs.to_dict()
    crs_parameters['lon_0'] = 180
    false_dst_crs = rasterio.crs.CRS.from_dict(crs_parameters)

    # Calculate dimensions for the dataset based on this false CRS
    dst_transform, _, _ = rasterio.warp.calculate_default_transform(src_crs, false_dst_crs, width, height, left, bottom, right, top)

    # Determine the correct resolution from the returned transformation.
    # This will not work if the calculated transform contains a rotation, which is not the case for WebMercator.
    dst_res = (dst_transform.a, dst_transform.a)

    # Recalculate the transformation, specifying the real destination CRS, but using the correct height
    return rasterio.warp.calculate_default_transform(src_crs, dst_crs, width, height, left, bottom, right, top, resolution=dst_res)

def clip_to_geobounds(geobounds, src_bounds, src_crs, dst_crs, dst_transform):
    '''
    Calculate the clipped bounds and dimensions for a reprojection to EPSG:3857, based on the geobounds specified in Lat/Lon

    Parameters
    ----------
    geobounds: tuple
        A tuple of (left, bottom, right, top) in Lat/Lon coordinates from aerona_datasets.py
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

# Process a single dataset, outputting a dataset reprojected to EPSG:3857
# Input filename is given in aeronav_datasets.py
# Output filename is the input filename with a double underscore prefix
def process(dataset_name, tmppath, resampling='nearest', threads=4):
    print(f'Processing {dataset_name}')

    # Get the dataset definition
    dataset_def = aeronav_datasets.datasets[dataset_name]

    # Get the clip region as a rasterio window
    window = dataset_def['window']
    window = rasterio.windows.Window(*window)

    # Open the dataset and read what we need
    input_file = dataset_def.get('input_file', f'{dataset_name}.tif')
    input_full_path = os.path.join(tmppath, input_file)
    with rasterio.open(input_full_path) as dataset:
        # Read the crs and raise ValueError if it is missing
        src_crs = dataset.crs
        if not src_crs:
            raise ValueError(f'No projection information found in {dataset.name}')

        # Store the profile to help build the output dataset
        profile = dataset.profile

        # Get the colormap if the dataset has one band
        colormap = dataset.colormap(1) if dataset.count == 1 else None

        # Get transform adjusted for the window
        dataset_transform = dataset.transform

        # Read all bands from the dataset into an array
        src_data = dataset.read(window=window)

    # Build an alpha band to clip the dataset to any provided mask shapes
    masks = dataset_def.get('masks', [])
    if masks:
        alpha_data = rasterize_shape_masks(masks, window)
    else:
        alpha_data = numpy.ones((window.height, window.width), dtype=numpy.uint8) * 255

    # Color expand paletted data to RGB
    if colormap:
        expanded_data = color_expand(src_data, colormap)
    else:
        expanded_data = src_data

    # Add the alpha band
    rgba_data = numpy.append(expanded_data, [alpha_data], axis=0)

    # Get the source transform, either from provided GCPs or calculated from the dataset's transform
    gcps = dataset_def.get('gcps', [])
    if gcps:
        src_transform = transform_from_gcps(gcps, src_crs, window)
    else:
        src_transform = rasterio.windows.transform(window, dataset_transform)

    # Calculate source bounds from the source transform and window size
    src_bounds = bounds_with_rotation(rasterio.windows.Window(0, 0, window.width, window.height), src_transform)

    # Define the destination coordinate system
    dst_crs = rasterio.crs.CRS.from_epsg(3857)

    # Calculate the size and transform of the reprojected dataset, and optionally clip it to the geobounds specified in Lat/Lon
    antimeridian = dataset_def.get('antimeridian', False)
    if antimeridian:
        dst_transform, dst_width, dst_height = calculate_default_transform_antimeridian(src_crs, dst_crs, window.width, window.height, *src_bounds)
    else:
        dst_transform, dst_width, dst_height = rasterio.warp.calculate_default_transform(src_crs, dst_crs, window.width, window.height, *src_bounds)

    # If geobounds are specified, we need to further clip the dataset to the specified bounds
    geobounds = dataset_def.get('geobound', None)
    if geobounds:
        dst_transform, dst_width, dst_height = clip_to_geobounds(geobounds, src_bounds, src_crs, dst_crs, dst_transform)

    # Create new arrays for the reprojected data
    output_data = numpy.zeros((len(rgba_data), dst_height, dst_width), dtype=src_data.dtype)

    # Convert resampling method string to rasterio.warp.Resampling enum
    resampling = getattr(rasterio.warp.Resampling, resampling)

    # Reproject each band to WebMercator (EPSG:3857)
    rasterio.warp.reproject(rgba_data, output_data, src_transform, src_crs=src_crs, dst_transform=dst_transform, dst_crs=dst_crs, resampling=resampling, num_threads=threads)

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
    output_file = f'_{dataset_name}.tif'
    output_full_path = os.path.join(tmppath, output_file)
    with rasterio.open(output_full_path, 'w', **profile) as dst:
        dst.write(output_data)

    # Return path to the reprojected dataset
    return output_full_path

def main():
    '''
    Main function to download Aeronav data from www.faa.gov and create web map tiles.
    '''

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Download Aeronav data from www.faa.gov and create web map tiles.')
    parser.add_argument('--current', action='store_true', help='Download the Current data.')
    parser.add_argument('--preview', action='store_true', help='Download the Preview data.')
    parser.add_argument('--zippath', help='Specify the directory to store downloaded Aeronav data.')
    parser.add_argument('--tmppath', default='/tmp/aeronav2tiles', help='Specify the directory to store temporary files.')
    parser.add_argument('--outpath', help='Specify the directory to store the output tilesets.')
    parser.add_argument('--all', action='store_true', help='Generate all tilesets')
    parser.add_argument('--tilesets', nargs='*', help='Specify the tilesets to generate.')
    parser.add_argument('--resampling', default='nearest', help='Specify the resampling method to use when reprojecting the data.')
    parser.add_argument('--threads', default=4, type=int, help='Specify the number of threads to use when reprojecting the data.')
    parser.add_argument('--cleanup', action='store_true', help='Remove the temporary directory and its contents after processing.')
    args = parser.parse_args()

    # TODO: Downloading. For now, use already downloaded ZIPs

    # Create the temporary directory if it does not exist
    os.makedirs(args.tmppath, exist_ok=True)

    if args.zippath:
        # Unzip all the files in the specified directory to the temporary directory
        for zip_filename in os.listdir(args.zippath):
            if zip_filename.endswith('.zip'):
                with zipfile.ZipFile(os.path.join(args.zippath, zip_filename), 'r') as zip_archive:
                    zip_archive.extractall(args.tmppath)

    # Determine which tilesets to generate
    tilesets = aeronav_datasets.tileset_datasets.keys() if args.all else args.tilesets or []

    # Process each tileset worth of data
    for tileset_name in tilesets:
        # Get the list of dataset names required by the tileset and the tileset's zoom level
        tileset_def = aeronav_datasets.tileset_datasets[tileset_name]

        # Create a list to hold the file paths of the reprojected datasets
        reprojected_files = []

        # Process each source dataset required by the tileset
        dataset_names = tileset_def['datasets']
        for dataset_name in dataset_names:
            # Reproject the dataset and return the path to the reprojected dataset
            reprojected_file = process(dataset_name, args.tmppath, args.resampling, args.threads)
            #reprojected_file = os.path.join(args.tmppath, f'_{dataset_name}.tif')

            # Add the reprojected dataset to the list
            reprojected_files.append(reprojected_file)

        vrt_path = os.path.join(args.tmppath, f'__{tileset_name}.vrt')

        # Build a VRT file from the reprojected datasets
        # For now, shell out to call gdalbuildvrt until rasterio supports VRT creation
        resampling = 'cubicspline' if args.resampling == 'cubic_spline' else args.resampling
        subprocess.run(['gdalbuildvrt', '-q', '-overwrite', '-strict', '-r', args.resampling, vrt_path] + reprojected_files, check=True)

        if args.outpath:
            tile_path = os.path.join(args.outpath, tileset_name)
            os.makedirs(tile_path, exist_ok=True)

            # Get the zoom levels needed for the tileset
            zoom = tileset_def['zoom']

            # Build the tile pyramid from the VRT file
            # For now, shell out to call gdal2tiles until rasterio supports tile creation
            resampling = 'cubicspline' if args.resampling == 'cubic_spline' else 'near' if args.resampling == 'nearest' else args.resampling
            subprocess.run(['gdal2tiles.py', '-q', '-x', '-z', zoom, '-w', 'leaflet', '-r', resampling, f'--processes={args.threads}', '--tiledriver=WEBP', '--webp-quality=50', vrt_path, tile_path], check=True)

    # Remove the temporary directory and its contents if remove is True
    if args.cleanup:
        shutil.rmtree(args.tmppath)

if __name__ == '__main__':
    main()
