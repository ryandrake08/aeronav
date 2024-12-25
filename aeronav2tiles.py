#!/usr/bin/env python3
# pylint: disable=line-too-long

"""
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
"""

import argparse
import os
import shutil
import zipfile

import numpy
import rasterio.features
import rasterio.transform
import rasterio.warp
import rasterio.windows

import aeronav_datasets

# Replacement for rasterio.windows.bounds that works with rotated datasets
def bounds_with_rotation(window, transform):
    """Get the spatial bounds of a window, allowing for rotation

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
    """
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
    """
    Wraps rasterio.warp.calculate_default_transform to handle antimeridian crossing

    Whithout specifying a resolution, rasterio.warp.calculate_default_transform will use GDAL's method which
    attempts to preserve the whole resolution of the source dataset. This will lead to incorrect results when
    the source dataset crosses the antimeridian.

    This function calculates the correct destination resolution using a false CRS that is centered on the
    antimeridian, then recalculates the transformation using the correct destination CRS and the known resolution.

    I've only tried this with WebMercator (EPSG:3857) as the destination CRS.
    """
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

# Process a single dataset, outputting a dataset reprojected to EPSG:3857
# Input filename is given in aeronav_datasets.py
# Output filename is the input filename with a double underscore prefix
def process(dataset_name, tmppath, resampling='nearest', threads=4):
    print(f"Processing {dataset_name}")

    # Get the dataset definition
    dataset_def = aeronav_datasets.datasets[dataset_name]

    # Get the clipping definition for the dataset
    input_file = dataset_def.get("input_file", f"{dataset_name}.tif")
    output_file = f"__{dataset_name}.tif"
    rect = dataset_def["window"]
    masks = dataset_def.get("masks", [])
    geobounds = dataset_def.get("geobound", (None, None, None, None))
    gcps = dataset_def.get("gcps", [])
    antimeridian = dataset_def.get("antimeridian", False)

    # Define the destination coordinate system
    dst_crs = rasterio.crs.CRS.from_epsg(3857)

    # Define the coordinate system we use for raw lat/lon coordinates
    geo_crs = rasterio.crs.CRS.from_epsg(4326)

    # Step 1: Open the dataset and read what we need
    with rasterio.open(os.path.join(tmppath, input_file)) as dataset:
        # Verify that dataset contains projection information
        if not dataset.crs:
            raise ValueError(f"No projection information found in {dataset.name}")

        # Read the profile, crs, resolution
        profile = dataset.profile
        src_crs = dataset.crs
        src_res = dataset.res

        # Get the colormap if the dataset has one band
        colormap = dataset.colormap(1) if dataset.count == 1 else None

        # Get the clip region as a window
        window = rasterio.windows.Window(*rect)

        # Get bounds and transform for the window
        src_bounds = bounds_with_rotation(window, dataset.transform)
        src_transform = rasterio.windows.transform(window, dataset.transform)

        # Read all bands from the dataset into an array
        input_data = dataset.read(window=window)

    # Step 2: Clip the dataset to the specified (pixel coordinate system) window and masks
    # We will clip using a rectangular window and a list of mask shapes:
    # The rectangular window limits the area of the dataset to be processed.
    # The (optional) mask shapes are used to create an alpha mask that will be applied to the
    # rest of the dataset.

    # Get the transformation for shape drawing (shapes are specified in the original dataset coordinates)
    shape_transform = rasterio.Affine.translation(window.col_off, window.row_off)

    # Get the mask shapes into a format acceptable to rasterio
    shapes = [({'type': 'Polygon', 'coordinates': [mask]},0) for mask in masks]

    # Workaround bug: If any shapes have less than 4 points, add the first point to the end to close the polygon
    for shape in shapes:
        if len(shape[0]['coordinates'][0]) < 4:
            shape[0]['coordinates'][0].append(shape[0]['coordinates'][0][0])

    # Create an alpha band based on the shapes
    alpha_data = rasterio.features.rasterize(shapes, (window.height, window.width), 255, transform=shape_transform, dtype=numpy.uint8)

    # Step 3: Color expand paletted data to RGB
    if colormap:
        # Create new arrays for the expanded data
        expanded_data = numpy.zeros((4, input_data[0].shape[0], input_data[0].shape[1]), dtype=numpy.uint8)

        # Transpose color table into four 256 element arrays, one for each band
        colormap_lookup = numpy.array([colormap[i] for i in range(min(256, len(colormap)))]).T

        # Expand the single band to three RGB bands, line by line for good performance
        for band in range(3):
            for iy in range(input_data[0].shape[0]):
                expanded_data[band][iy] = numpy.take(colormap_lookup[band], input_data[0][iy])

        # Set output alpha band to a
        expanded_data[3] = alpha_data
    else:
        # Add the alpha band
        expanded_data = numpy.append(input_data, [alpha_data], axis=0)

    # Step 4: If GCPs are provided, override the source bounds and source transform with new ones calculated from the GCPs
    if gcps:
        xs, ys, lons, lats = zip(*gcps)

        # Adjust the gcp coordinates to the window
        win_xs = [x - window.col_off for x in xs]
        win_ys = [y - window.row_off for y in ys]

        # Transform the lat/lon coordinates to the dataset CRS
        src_xs, src_ys = rasterio.warp.transform(geo_crs, src_crs, lons, lats)

        # Create list of GroundControlPoints mapping win_x, win_y to dst_x, dst_y
        gcps_src = [rasterio.control.GroundControlPoint(win_y, win_x, src_x, src_y) for win_x, win_y, src_x, src_y in zip(win_xs, win_ys, src_xs, src_ys)]

        # Create a new transformation and bounds from the GCPs
        src_transform = rasterio.transform.from_gcps(gcps_src)
        src_bounds = bounds_with_rotation(rasterio.windows.Window(0, 0, window.width, window.height), src_transform)

    # Step 5: Calculate the size and transform of the reprojected dataset, and optionally clip it to the geobounds specified in Lat/Lon
    if antimeridian:
        dst_transform, dst_width, dst_height = calculate_default_transform_antimeridian(src_crs, dst_crs, window.width, window.height, *src_bounds)
    else:
        dst_transform, dst_width, dst_height = rasterio.warp.calculate_default_transform(src_crs, dst_crs, window.width, window.height, *src_bounds)

    # If geobounds are specified, we need to further clip the dataset to the specified bounds
    if not all(x is None for x in geobounds):
        # Calculate dataset bounds in Lat/Lon
        src_geobounds = rasterio.warp.transform_bounds(src_crs, geo_crs, *src_bounds)

        # Replace any None values in our input geobounds with the calculated values
        clip_geobounds = tuple(clip or bnd for clip, bnd in zip(geobounds, src_geobounds))

        # Calculate clipped bounds in WebMercator (EPSG:3857)
        dst_bounds = rasterio.warp.transform_bounds(geo_crs, dst_crs, *clip_geobounds)

        # Get the window corresponding to the clipped bounds
        dst_window = rasterio.windows.from_bounds(*dst_bounds, dst_transform)

        # Update the dst_transform to reflect the clipped bounds
        dst_transform = rasterio.windows.transform(dst_window, dst_transform)

        # Update the dst_width and dst_height to reflect the clipped bounds
        dst_width = int(dst_window.width)
        dst_height = int(dst_window.height)

    # Step 6: Reproject the clipped dataset to EPSG:3857 (WebMercator)

    # Create new arrays for the reprojected data
    output_data = numpy.zeros((len(expanded_data), dst_height, dst_width), dtype=input_data.dtype)

    # Convert resampling method string to rasterio.warp.Resampling enum
    resampling = getattr(rasterio.warp.Resampling, resampling)

    # Reproject each band to WebMercator (EPSG:3857)
    rasterio.warp.reproject(expanded_data, output_data, src_transform, src_crs=src_crs, dst_transform=dst_transform, dst_crs=dst_crs, dst_resolution=src_res, resampling=resampling, num_threads=threads)

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
    with rasterio.open(os.path.join(tmppath, output_file), 'w', **profile) as dst:
        dst.write(output_data)

def main():
    """
    Main function to download Aeronav data from www.faa.gov and create web map tiles.
    """

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Download Aeronav data from www.faa.gov and create web map tiles.')
    parser.add_argument('--current', action='store_true', help='Download the Current data.')
    parser.add_argument('--preview', action='store_true', help='Download the Preview data.')
    parser.add_argument('--zippath', help='Specify the directory to store downloaded Aeronav data.')
    parser.add_argument('--tmppath', default='/tmp/aeronav2tiles', help='Specify the directory to store temporary files.')
    parser.add_argument('--outpath', default='.', help='Tile output directory.')
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

    # Reproject each dataset
    for dataset_name in aeronav_datasets.datasets:
        process(dataset_name, args.tmppath, args.resampling, args.threads)

    # Remove the temporary directory and its contents if remove is True
    if args.cleanup:
        shutil.rmtree(args.tmppath)

if __name__ == '__main__':
    main()
