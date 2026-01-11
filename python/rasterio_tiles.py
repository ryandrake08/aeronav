"""
XYZ tile generation using rasterio.

This module generates XYZ map tiles from georeferenced rasters in EPSG:3857
(Web Mercator) projection. It is a simplified replacement for gdal2tiles
tailored to the needs of aeronav2tiles.py.
"""

import math
import os
from typing import Optional, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds


# Resampling method mapping from string names to rasterio enums
RESAMPLING_METHODS = {
    'nearest': Resampling.nearest,
    'bilinear': Resampling.bilinear,
    'cubic': Resampling.cubic,
    'cubicspline': Resampling.cubic_spline,
    'lanczos': Resampling.lanczos,
    'average': Resampling.average,
    'mode': Resampling.mode,
}


def get_resampling(method: str) -> Resampling:
    """Convert string resampling method to rasterio enum."""
    if method not in RESAMPLING_METHODS:
        raise ValueError(f"Unknown resampling method: {method}. "
                        f"Valid options: {', '.join(RESAMPLING_METHODS.keys())}")
    return RESAMPLING_METHODS[method]


class GlobalMercator:
    """
    TMS Global Mercator Profile for EPSG:3857.

    Handles coordinate conversions between EPSG:3857 meters, pixel coordinates,
    and tile coordinates for Web Mercator tiles.

    Based on the gdal2tiles.py GlobalMercator class.
    """

    def __init__(self, tile_size: int = 256) -> None:
        """
        Initialize the TMS Global Mercator pyramid.

        Args:
            tile_size: Size of tiles in pixels (default 256)
        """
        self.tile_size = tile_size
        # Initial resolution at zoom 0: 156543.03392804062 for 256px tiles
        self.initial_resolution = 2 * math.pi * 6378137 / self.tile_size
        # Origin shift: 20037508.342789244
        self.origin_shift = 2 * math.pi * 6378137 / 2.0

    def resolution(self, zoom: int) -> float:
        """
        Resolution (meters/pixel) for given zoom level (measured at Equator).

        Args:
            zoom: Zoom level

        Returns:
            Resolution in meters per pixel
        """
        return self.initial_resolution / (2 ** zoom)

    def pixels_to_meters(self, px: float, py: float, zoom: int) -> Tuple[float, float]:
        """
        Convert pixel coordinates at given zoom level to EPSG:3857 meters.

        Args:
            px: Pixel X coordinate
            py: Pixel Y coordinate
            zoom: Zoom level

        Returns:
            Tuple of (mx, my) in EPSG:3857 meters
        """
        res = self.resolution(zoom)
        mx = px * res - self.origin_shift
        my = py * res - self.origin_shift
        return mx, my

    def meters_to_pixels(self, mx: float, my: float, zoom: int) -> Tuple[float, float]:
        """
        Convert EPSG:3857 meters to pixel coordinates at given zoom level.

        Args:
            mx: X coordinate in EPSG:3857 meters
            my: Y coordinate in EPSG:3857 meters
            zoom: Zoom level

        Returns:
            Tuple of (px, py) in pixel coordinates
        """
        res = self.resolution(zoom)
        px = (mx + self.origin_shift) / res
        py = (my + self.origin_shift) / res
        return px, py

    def pixels_to_tile(self, px: float, py: float) -> Tuple[int, int]:
        """
        Return tile coordinates covering the given pixel coordinates.

        Args:
            px: Pixel X coordinate
            py: Pixel Y coordinate

        Returns:
            Tuple of (tx, ty) tile coordinates (TMS scheme, origin bottom-left)
        """
        tx = int(math.ceil(px / float(self.tile_size)) - 1)
        ty = int(math.ceil(py / float(self.tile_size)) - 1)
        return tx, ty

    def meters_to_tile(self, mx: float, my: float, zoom: int) -> Tuple[int, int]:
        """
        Return tile coordinates for given EPSG:3857 meters at zoom level.

        Args:
            mx: X coordinate in EPSG:3857 meters
            my: Y coordinate in EPSG:3857 meters
            zoom: Zoom level

        Returns:
            Tuple of (tx, ty) tile coordinates (TMS scheme, origin bottom-left)
        """
        px, py = self.meters_to_pixels(mx, my, zoom)
        return self.pixels_to_tile(px, py)

    def tile_bounds(self, tx: int, ty: int, zoom: int) -> Tuple[float, float, float, float]:
        """
        Return bounds of the given tile in EPSG:3857 meters.

        Args:
            tx: Tile X coordinate (TMS scheme)
            ty: Tile Y coordinate (TMS scheme)
            zoom: Zoom level

        Returns:
            Tuple of (minx, miny, maxx, maxy) in EPSG:3857 meters
        """
        minx, miny = self.pixels_to_meters(
            tx * self.tile_size,
            ty * self.tile_size,
            zoom
        )
        maxx, maxy = self.pixels_to_meters(
            (tx + 1) * self.tile_size,
            (ty + 1) * self.tile_size,
            zoom
        )
        return (minx, miny, maxx, maxy)

    def zoom_for_pixel_size(self, pixel_size: float) -> int:
        """
        Find the zoom level closest to the given pixel size.

        Args:
            pixel_size: Desired pixel size in meters

        Returns:
            Zoom level (will not scale up, so returns level with >= pixel_size)
        """
        for i in range(32):
            if pixel_size > self.resolution(i):
                return max(0, i - 1)
        return 31


class TileGenerator:
    """
    Generates XYZ tiles from a georeferenced raster in EPSG:3857.
    """

    def __init__(
        self,
        input_path: str,
        output_path: str,
        zoom_range: Tuple[int, int],
        resampling: str = 'bilinear',
        tile_size: int = 256,
        tile_format: str = 'WEBP',
        quiet: bool = False,
        tile_manifest: Optional[dict] = None,
    ):
        """
        Initialize the tile generator.

        Args:
            input_path: Path to input raster (VRT, GeoTIFF, etc.) in EPSG:3857
            output_path: Directory for output tiles
            zoom_range: Tuple of (min_zoom, max_zoom)
            resampling: Resampling method name
            tile_size: Size of output tiles in pixels
            tile_format: Output tile format (PNG, JPEG, or WEBP)
            quiet: Suppress progress output
            tile_manifest: Optional dict mapping zoom -> set of (x, y) XYZ coordinates.
                          When provided, only tiles in the manifest are generated.
        """
        self.input_path = input_path
        self.output_path = output_path
        self.min_zoom, self.max_zoom = zoom_range
        self.resampling = get_resampling(resampling)
        self.tile_size = tile_size
        self.tile_format = tile_format.upper()
        self.tile_ext = {'WEBP': '.webp', 'JPEG': '.jpg', 'PNG': '.png'}.get(self.tile_format, '.png')
        self.quiet = quiet
        self.tile_manifest = tile_manifest
        self.mercator = GlobalMercator(tile_size)

    def _tile_path(self, tx: int, ty: int, zoom: int) -> str:
        """
        Get the file path for a tile, converting TMS Y to XYZ Y.

        Args:
            tx: Tile X coordinate (TMS scheme)
            ty: Tile Y coordinate (TMS scheme)
            zoom: Zoom level

        Returns:
            Path to the tile file
        """
        # Convert TMS Y to XYZ Y
        # XYZ: Y=0 at top; TMS: Y=0 at bottom
        xyz_y = (2 ** zoom - 1) - ty
        return os.path.join(self.output_path, str(zoom), str(tx), f"{xyz_y}{self.tile_ext}")

    def _is_transparent(self, data: np.ndarray) -> bool:
        """
        Check if tile data is fully transparent (should be skipped).

        Args:
            data: Tile data array (bands, height, width)

        Returns:
            True if tile is fully transparent
        """
        # If we have an alpha band (4 bands for RGBA), check if all alpha values are 0
        if data.shape[0] == 4:
            return bool(np.all(data[3] == 0))
        # If we have 2 bands (grayscale + alpha), check alpha
        if data.shape[0] == 2:
            return bool(np.all(data[1] == 0))
        # For RGB without alpha, check if all pixels are zero (black)
        return bool(np.all(data == 0))

    def _write_tile(self, tile_path: str, data: np.ndarray, profile: dict) -> None:
        """
        Write tile data to a file.

        Args:
            tile_path: Output file path
            data: Tile data array (bands, height, width)
            profile: Rasterio profile for the output
        """
        # Ensure directory exists
        os.makedirs(os.path.dirname(tile_path), exist_ok=True)

        # Create output profile
        out_profile = {
            'driver': self.tile_format,
            'dtype': data.dtype,
            'width': self.tile_size,
            'height': self.tile_size,
            'count': data.shape[0],
        }

        with rasterio.open(tile_path, 'w', **out_profile) as dst:
            dst.write(data)

    def _get_tile_range(self, src, zoom: int) -> Tuple[int, int, int, int]:
        """
        Calculate the tile range for the source at given zoom level.

        Args:
            src: Open rasterio dataset
            zoom: Zoom level

        Returns:
            Tuple of (tminx, tminy, tmaxx, tmaxy) in TMS coordinates
        """
        bounds = src.bounds

        # Get tile coordinates for corners
        tminx, tminy = self.mercator.meters_to_tile(bounds.left, bounds.bottom, zoom)
        tmaxx, tmaxy = self.mercator.meters_to_tile(bounds.right, bounds.top, zoom)

        # Clamp to valid tile range
        max_tile = 2 ** zoom - 1
        tminx = max(0, tminx)
        tminy = max(0, tminy)
        tmaxx = min(max_tile, tmaxx)
        tmaxy = min(max_tile, tmaxy)

        return tminx, tminy, tmaxx, tmaxy

    def generate_base_tiles(self) -> None:
        """Generate base tiles at each zoom level in the manifest from the source raster."""
        if not self.quiet:
            print(f"Generating base tiles (zoom {self.min_zoom} to {self.max_zoom})...")

        with rasterio.open(self.input_path) as src:
            # Collect tiles directly from manifest (efficient) or from raster bounds (fallback)
            tile_coords = []
            if self.tile_manifest is not None:
                # Iterate directly through manifest - O(n) where n is manifest size
                for zoom in range(self.min_zoom, self.max_zoom + 1):
                    manifest_tiles = self.tile_manifest.get(zoom, set())
                    for x, y in manifest_tiles:
                        # Manifest uses XYZ coordinates, convert to TMS for internal use
                        tms_y = (2 ** zoom - 1) - y
                        tile_coords.append((x, tms_y, zoom))
            else:
                # No manifest - generate all tiles in raster bounds at max_zoom only
                tminx, tminy, tmaxx, tmaxy = self._get_tile_range(src, self.max_zoom)
                for ty in range(tmaxy, tminy - 1, -1):
                    for tx in range(tminx, tmaxx + 1):
                        tile_coords.append((tx, ty, self.max_zoom))

            if not self.quiet:
                print(f"  {len(tile_coords)} base tiles to generate")

            # Create directories upfront for tiles we're generating
            dirs_created = set()
            for tx, ty, zoom in tile_coords:
                dir_key = (zoom, tx)
                if dir_key not in dirs_created:
                    tile_dir = os.path.join(self.output_path, str(zoom), str(tx))
                    os.makedirs(tile_dir, exist_ok=True)
                    dirs_created.add(dir_key)

            # Generate each tile
            tiles_done = 0
            for tx, ty, zoom in tile_coords:
                self._create_base_tile(src, tx, ty, zoom)
                tiles_done += 1

                if not self.quiet and tiles_done % 100 == 0:
                    print(f"  {tiles_done}/{len(tile_coords)} tiles")

            if not self.quiet:
                print(f"  Completed {tiles_done} base tiles")

    def _create_base_tile(self, src, tx: int, ty: int, zoom: int) -> None:
        """
        Create a single base tile by reading and resampling source data.

        Args:
            src: Open rasterio dataset
            tx: Tile X coordinate (TMS scheme)
            ty: Tile Y coordinate (TMS scheme)
            zoom: Zoom level
        """
        tile_path = self._tile_path(tx, ty, zoom)

        # Skip if tile already exists
        if os.path.exists(tile_path):
            return

        # Get tile bounds in EPSG:3857
        minx, miny, maxx, maxy = self.mercator.tile_bounds(tx, ty, zoom)

        # Calculate window in source coordinates
        window = from_bounds(minx, miny, maxx, maxy, src.transform)

        # Read and resample to tile size
        # Use boundless=True to handle tiles at edges that extend beyond source
        data = src.read(
            window=window,
            out_shape=(src.count, self.tile_size, self.tile_size),
            resampling=self.resampling,
            boundless=True,
            fill_value=0,
        )

        # Check for transparent tiles - skip if fully transparent
        if self._is_transparent(data):
            return

        # Write tile
        self._write_tile(tile_path, data, src.profile)

    def generate_tiles_parallel(self, num_processes: int) -> None:
        """
        Generate tiles using multiple worker processes.

        Args:
            num_processes: Number of parallel workers
        """
        from concurrent.futures import ProcessPoolExecutor
        from functools import partial

        if not self.quiet:
            print(f"Generating base tiles (zoom {self.min_zoom} to {self.max_zoom}) with {num_processes} workers...")

        with rasterio.open(self.input_path) as src:
            # Collect tiles directly from manifest (efficient) or from raster bounds (fallback)
            tile_coords = []
            if self.tile_manifest is not None:
                # Iterate directly through manifest - O(n) where n is manifest size
                for zoom in range(self.min_zoom, self.max_zoom + 1):
                    manifest_tiles = self.tile_manifest.get(zoom, set())
                    for x, y in manifest_tiles:
                        # Manifest uses XYZ coordinates, convert to TMS for internal use
                        tms_y = (2 ** zoom - 1) - y
                        tile_coords.append((x, tms_y, zoom))
            else:
                # No manifest - generate all tiles in raster bounds at max_zoom only
                tminx, tminy, tmaxx, tmaxy = self._get_tile_range(src, self.max_zoom)
                for ty in range(tmaxy, tminy - 1, -1):
                    for tx in range(tminx, tmaxx + 1):
                        tile_coords.append((tx, ty, self.max_zoom))

            if not self.quiet:
                print(f"  {len(tile_coords)} base tiles to generate")

            # Create directories upfront for tiles we're generating
            dirs_created = set()
            for tx, ty, zoom in tile_coords:
                dir_key = (zoom, tx)
                if dir_key not in dirs_created:
                    tile_dir = os.path.join(self.output_path, str(zoom), str(tx))
                    os.makedirs(tile_dir, exist_ok=True)
                    dirs_created.add(dir_key)

        if not self.quiet:
            print(f"  Processing {len(tile_coords)} tiles...")

        # Create worker function
        worker = partial(
            _create_tile_worker,
            input_path=self.input_path,
            output_path=self.output_path,
            resampling=self.resampling,
            tile_size=self.tile_size,
            tile_format=self.tile_format,
            tile_ext=self.tile_ext,
        )

        # Process tiles in parallel
        with ProcessPoolExecutor(max_workers=num_processes) as executor:
            tiles_done = 0
            for _ in executor.map(worker, tile_coords, chunksize=32):
                tiles_done += 1
                if not self.quiet and tiles_done % 500 == 0:
                    print(f"  {tiles_done}/{len(tile_coords)} tiles")

        if not self.quiet:
            print(f"  Completed {len(tile_coords)} base tiles")


def _create_tile_worker(
    coords: Tuple[int, int, int],
    input_path: str,
    output_path: str,
    resampling: Resampling,
    tile_size: int,
    tile_format: str,
    tile_ext: str,
) -> None:
    """
    Worker function for parallel tile generation.

    Args:
        coords: Tuple of (tx, ty, zoom)
        input_path: Path to input raster
        output_path: Directory for output tiles
        resampling: Resampling method
        tile_size: Size of output tiles
        tile_format: Output format driver (PNG, JPEG, or WEBP)
        tile_ext: File extension (.png, .jpg, or .webp)
    """
    tx, ty, zoom = coords
    mercator = GlobalMercator(tile_size)

    # Calculate tile path
    xyz_y = (2 ** zoom - 1) - ty
    tile_path = os.path.join(output_path, str(zoom), str(tx), f"{xyz_y}{tile_ext}")

    # Skip if tile already exists
    if os.path.exists(tile_path):
        return

    # Get tile bounds
    minx, miny, maxx, maxy = mercator.tile_bounds(tx, ty, zoom)

    # Read and resample
    with rasterio.open(input_path) as src:
        window = from_bounds(minx, miny, maxx, maxy, src.transform)

        data = src.read(
            window=window,
            out_shape=(src.count, tile_size, tile_size),
            resampling=resampling,
            boundless=True,
            fill_value=0,
        )

    # Check for transparent tiles
    if data.shape[0] == 4:
        if np.all(data[3] == 0):
            return
    elif data.shape[0] == 2:
        if np.all(data[1] == 0):
            return
    elif np.all(data == 0):
        return

    # Write tile
    out_profile = {
        'driver': tile_format,
        'dtype': data.dtype,
        'width': tile_size,
        'height': tile_size,
        'count': data.shape[0],
    }

    with rasterio.open(tile_path, 'w', **out_profile) as dst:
        dst.write(data)


def generate_tiles(
    input_path: str,
    output_path: str,
    min_zoom: int,
    max_zoom: int,
    resampling: str = 'bilinear',
    tile_format: str = 'WEBP',
    num_processes: int = 1,
    quiet: bool = False,
    tile_manifest: Optional[dict] = None,
) -> None:
    """
    Generate XYZ tiles from a georeferenced raster in EPSG:3857.

    Args:
        input_path: Path to input raster (VRT, GeoTIFF, etc.) in EPSG:3857
        output_path: Directory for output tiles
        min_zoom: Minimum zoom level
        max_zoom: Maximum zoom level
        resampling: Resampling method name (nearest, bilinear, cubic, etc.)
        tile_format: Output tile format (PNG, JPEG, or WEBP)
        num_processes: Number of parallel workers
        quiet: Suppress progress output
        tile_manifest: Optional dict mapping zoom -> set of (x, y) XYZ coordinates.
                      When provided, only tiles in the manifest are generated.
    """
    generator = TileGenerator(
        input_path=input_path,
        output_path=output_path,
        zoom_range=(min_zoom, max_zoom),
        resampling=resampling,
        tile_format=tile_format,
        quiet=quiet,
        tile_manifest=tile_manifest,
    )

    if num_processes > 1:
        generator.generate_tiles_parallel(num_processes)
    else:
        generator.generate_base_tiles()


def _create_tile_worker_multi_vrt(
    args: Tuple[int, int, int, str],
    output_path: str,
    resampling: Resampling,
    tile_size: int,
    tile_format: str,
    tile_ext: str,
) -> None:
    """
    Worker function for parallel tile generation with zoom-specific VRTs.

    Args:
        args: Tuple of (zoom, tx, ty, vrt_path) where tx/ty are in TMS coordinates
        output_path: Directory for output tiles
        resampling: Resampling method
        tile_size: Size of output tiles
        tile_format: Output format driver (PNG, JPEG, or WEBP)
        tile_ext: File extension (.png, .jpg, or .webp)
    """
    zoom, tx, ty, vrt_path = args
    mercator = GlobalMercator(tile_size)

    # Calculate tile path (convert TMS to XYZ)
    xyz_y = (2 ** zoom - 1) - ty
    tile_path = os.path.join(output_path, str(zoom), str(tx), f"{xyz_y}{tile_ext}")

    # Skip if tile already exists
    if os.path.exists(tile_path):
        return

    # Get tile bounds
    minx, miny, maxx, maxy = mercator.tile_bounds(tx, ty, zoom)

    # Read and resample from zoom-specific VRT
    with rasterio.open(vrt_path) as src:
        window = from_bounds(minx, miny, maxx, maxy, src.transform)

        data = src.read(
            window=window,
            out_shape=(src.count, tile_size, tile_size),
            resampling=resampling,
            boundless=True,
            fill_value=0,
        )

    # Check for transparent tiles
    if data.shape[0] == 4:
        if np.all(data[3] == 0):
            return
    elif data.shape[0] == 2:
        if np.all(data[1] == 0):
            return
    elif np.all(data == 0):
        return

    # Write tile
    out_profile = {
        'driver': tile_format,
        'dtype': data.dtype,
        'width': tile_size,
        'height': tile_size,
        'count': data.shape[0],
    }

    with rasterio.open(tile_path, 'w', **out_profile) as dst:
        dst.write(data)


def generate_tiles_multi_zoom(
    vrt_paths: dict[int, str],
    output_path: str,
    tile_manifest: dict[int, set[Tuple[int, int]]],
    resampling: str = 'bilinear',
    tile_format: str = 'WEBP',
    num_processes: int = 1,
    quiet: bool = False,
) -> None:
    """
    Generate XYZ tiles from zoom-specific VRTs in a single parallel phase.

    This is more efficient than calling generate_tiles() for each zoom level
    because it uses a single process pool for all tiles across all zoom levels.

    Args:
        vrt_paths: Dict mapping zoom level -> path to zoom-specific VRT
        output_path: Directory for output tiles
        tile_manifest: Dict mapping zoom level -> set of (x, y) XYZ tile coordinates
        resampling: Resampling method name (nearest, bilinear, cubic, etc.)
        tile_format: Output tile format (PNG, JPEG, or WEBP)
        num_processes: Number of parallel workers
        quiet: Suppress progress output
    """
    from concurrent.futures import ProcessPoolExecutor
    from functools import partial

    tile_size = 256
    tile_ext = {'WEBP': '.webp', 'JPEG': '.jpg', 'PNG': '.png'}.get(tile_format.upper(), '.png')
    resampling_enum = get_resampling(resampling)

    # Collect all tiles from all zoom levels into a single list
    # Each item is (zoom, tx, ty, vrt_path) where tx/ty are TMS coordinates
    all_tiles = []
    for zoom, tiles in sorted(tile_manifest.items()):
        if zoom not in vrt_paths:
            continue
        vrt_path = vrt_paths[zoom]
        for x, y in tiles:
            # Convert XYZ y to TMS y
            tms_y = (2 ** zoom - 1) - y
            all_tiles.append((zoom, x, tms_y, vrt_path))

    if not all_tiles:
        if not quiet:
            print("No tiles to generate")
        return

    if not quiet:
        print(f"Generating {len(all_tiles)} tiles across {len(vrt_paths)} zoom levels with {num_processes} workers...")

    # Create directories upfront
    dirs_created = set()
    for zoom, tx, ty, _ in all_tiles:
        dir_key = (zoom, tx)
        if dir_key not in dirs_created:
            tile_dir = os.path.join(output_path, str(zoom), str(tx))
            os.makedirs(tile_dir, exist_ok=True)
            dirs_created.add(dir_key)

    # Create worker function with fixed parameters
    worker = partial(
        _create_tile_worker_multi_vrt,
        output_path=output_path,
        resampling=resampling_enum,
        tile_size=tile_size,
        tile_format=tile_format.upper(),
        tile_ext=tile_ext,
    )

    # Process tiles in parallel
    with ProcessPoolExecutor(max_workers=num_processes) as executor:
        tiles_done = 0
        for _ in executor.map(worker, all_tiles, chunksize=32):
            tiles_done += 1
            if not quiet and tiles_done % 500 == 0:
                print(f"  {tiles_done}/{len(all_tiles)} tiles")

    if not quiet:
        print(f"  Completed {len(all_tiles)} tiles")
