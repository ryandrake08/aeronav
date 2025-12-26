"""
XYZ tile generation using rasterio.

This module generates XYZ map tiles from georeferenced rasters in EPSG:3857
(Web Mercator) projection. It is a simplified replacement for gdal2tiles
tailored to the needs of aeronav2tiles.py.
"""

import math
import os
from typing import Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds


# Resampling method mapping from string names to rasterio enums
RESAMPLING_METHODS = {
    'nearest': Resampling.nearest,
    'near': Resampling.nearest,
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
        resume: bool = False,
    ):
        """
        Initialize the tile generator.

        Args:
            input_path: Path to input raster (VRT, GeoTIFF, etc.) in EPSG:3857
            output_path: Directory for output tiles
            zoom_range: Tuple of (min_zoom, max_zoom)
            resampling: Resampling method name
            tile_size: Size of output tiles in pixels
            tile_format: Output tile format (WEBP or PNG)
            quiet: Suppress progress output
            resume: Skip existing tiles
        """
        self.input_path = input_path
        self.output_path = output_path
        self.min_zoom, self.max_zoom = zoom_range
        self.resampling = get_resampling(resampling)
        self.tile_size = tile_size
        self.tile_format = tile_format.upper()
        self.tile_ext = '.webp' if self.tile_format == 'WEBP' else '.png'
        self.quiet = quiet
        self.resume = resume
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
        """Generate tiles at the maximum zoom level from the source raster."""
        if not self.quiet:
            print(f"Generating base tiles at zoom {self.max_zoom}...")

        with rasterio.open(self.input_path) as src:
            # Get tile range at max zoom
            tminx, tminy, tmaxx, tmaxy = self._get_tile_range(src, self.max_zoom)

            total_tiles = (tmaxx - tminx + 1) * (tmaxy - tminy + 1)
            tiles_done = 0

            # Create directories upfront
            for tx in range(tminx, tmaxx + 1):
                tile_dir = os.path.join(self.output_path, str(self.max_zoom), str(tx))
                os.makedirs(tile_dir, exist_ok=True)

            # Generate each tile
            for ty in range(tmaxy, tminy - 1, -1):
                for tx in range(tminx, tmaxx + 1):
                    self._create_base_tile(src, tx, ty, self.max_zoom)
                    tiles_done += 1

                    if not self.quiet and tiles_done % 100 == 0:
                        print(f"  {tiles_done}/{total_tiles} tiles")

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

        # Resume mode: skip if tile already exists
        if self.resume and os.path.exists(tile_path):
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

    def generate_overview_tiles(self) -> None:
        """Generate overview tiles by combining child tiles."""
        for zoom in range(self.max_zoom - 1, self.min_zoom - 1, -1):
            if not self.quiet:
                print(f"Generating overview tiles at zoom {zoom}...")

            # Calculate tile range at this zoom from child tiles
            child_zoom = zoom + 1

            # Find which tiles we need by looking at existing child tiles
            child_dir = os.path.join(self.output_path, str(child_zoom))
            if not os.path.exists(child_dir):
                continue

            # Get unique parent tiles from child tile coordinates
            parent_tiles = set()
            for tx_str in os.listdir(child_dir):
                tx_path = os.path.join(child_dir, tx_str)
                if not os.path.isdir(tx_path):
                    continue
                try:
                    child_tx = int(tx_str)
                except ValueError:
                    continue

                for ty_file in os.listdir(tx_path):
                    if not ty_file.endswith(self.tile_ext):
                        continue
                    try:
                        # Convert XYZ Y back to TMS Y for child
                        xyz_y = int(ty_file[:ty_file.rfind('.')])
                        child_ty = (2 ** child_zoom - 1) - xyz_y

                        # Calculate parent tile coords
                        parent_tx = child_tx // 2
                        parent_ty = child_ty // 2
                        parent_tiles.add((parent_tx, parent_ty))
                    except ValueError:
                        continue

            tiles_done = 0
            for tx, ty in sorted(parent_tiles):
                self._create_overview_tile(tx, ty, zoom)
                tiles_done += 1

            if not self.quiet:
                print(f"  Completed {tiles_done} overview tiles")

    def _create_overview_tile(self, tx: int, ty: int, zoom: int) -> None:
        """
        Create an overview tile by combining 4 child tiles.

        Args:
            tx: Tile X coordinate (TMS scheme)
            ty: Tile Y coordinate (TMS scheme)
            zoom: Zoom level
        """
        tile_path = self._tile_path(tx, ty, zoom)

        # Resume mode: skip if tile already exists
        if self.resume and os.path.exists(tile_path):
            return

        # Ensure directory exists
        os.makedirs(os.path.dirname(tile_path), exist_ok=True)

        # Child tile coordinates at zoom+1
        child_zoom = zoom + 1
        # In TMS, children are at (2*tx, 2*ty), (2*tx+1, 2*ty), (2*tx, 2*ty+1), (2*tx+1, 2*ty+1)
        child_tiles = [
            (tx * 2,     ty * 2),      # bottom-left
            (tx * 2 + 1, ty * 2),      # bottom-right
            (tx * 2,     ty * 2 + 1),  # top-left
            (tx * 2 + 1, ty * 2 + 1),  # top-right
        ]

        # Quadrant positions in the 2x composite image
        # (qx, qy) where qx is column (0=left, 1=right), qy is row (0=top, 1=bottom)
        # In image coordinates, Y increases downward, but in TMS, Y increases upward
        quadrant_positions = [
            (0, 1),  # bottom-left child -> left column, bottom row
            (1, 1),  # bottom-right child -> right column, bottom row
            (0, 0),  # top-left child -> left column, top row
            (1, 0),  # top-right child -> right column, top row
        ]

        # Create 2*tile_size composite image (RGBA)
        composite = np.zeros((4, self.tile_size * 2, self.tile_size * 2), dtype=np.uint8)

        # Load each child tile into its quadrant
        has_any_tile = False
        for (cx, cy), (qx, qy) in zip(child_tiles, quadrant_positions):
            child_path = self._tile_path(cx, cy, child_zoom)
            if os.path.exists(child_path):
                has_any_tile = True
                with rasterio.open(child_path) as child:
                    child_data = child.read()
                    # Ensure child data has 4 bands
                    if child_data.shape[0] < 4:
                        # Expand to RGBA
                        expanded = np.zeros((4, self.tile_size, self.tile_size), dtype=np.uint8)
                        expanded[:child_data.shape[0]] = child_data
                        if child_data.shape[0] == 3:
                            # RGB -> add full opacity alpha
                            expanded[3] = 255
                        child_data = expanded

                    x_off = qx * self.tile_size
                    y_off = qy * self.tile_size
                    composite[:, y_off:y_off+self.tile_size, x_off:x_off+self.tile_size] = child_data

        # Skip if no child tiles exist
        if not has_any_tile:
            return

        # Resample composite down to tile_size using PIL for proper resampling
        from PIL import Image

        # Convert to PIL Image (need to transpose for PIL's format)
        # PIL expects (height, width, channels) or separate mode
        rgba = np.transpose(composite, (1, 2, 0))  # (H, W, C)
        img = Image.fromarray(rgba, mode='RGBA')

        # Resize with appropriate resampling
        pil_resampling = {
            Resampling.nearest: Image.Resampling.NEAREST,
            Resampling.bilinear: Image.Resampling.BILINEAR,
            Resampling.cubic: Image.Resampling.BICUBIC,
            Resampling.lanczos: Image.Resampling.LANCZOS,
        }.get(self.resampling, Image.Resampling.BILINEAR)

        img_resized = img.resize((self.tile_size, self.tile_size), pil_resampling)

        # Convert back to numpy
        resampled = np.array(img_resized)
        resampled = np.transpose(resampled, (2, 0, 1))  # (C, H, W)

        # Check if tile is transparent - skip if fully transparent
        if self._is_transparent(resampled):
            return

        # Write tile
        self._write_tile(tile_path, resampled, {})

    def generate_tiles_parallel(self, num_processes: int) -> None:
        """
        Generate tiles using multiple worker processes.

        Args:
            num_processes: Number of parallel workers
        """
        from concurrent.futures import ProcessPoolExecutor
        from functools import partial

        if not self.quiet:
            print(f"Generating base tiles at zoom {self.max_zoom} with {num_processes} workers...")

        with rasterio.open(self.input_path) as src:
            # Get tile range at max zoom
            tminx, tminy, tmaxx, tmaxy = self._get_tile_range(src, self.max_zoom)

            # Build list of tile coordinates
            tile_coords = [
                (tx, ty, self.max_zoom)
                for ty in range(tmaxy, tminy - 1, -1)
                for tx in range(tminx, tmaxx + 1)
            ]

            # Create directories upfront
            for tx in range(tminx, tmaxx + 1):
                tile_dir = os.path.join(self.output_path, str(self.max_zoom), str(tx))
                os.makedirs(tile_dir, exist_ok=True)

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
            resume=self.resume,
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

        # Generate overview tiles (sequential, as they depend on base tiles)
        self.generate_overview_tiles()


def _create_tile_worker(
    coords: Tuple[int, int, int],
    input_path: str,
    output_path: str,
    resampling: Resampling,
    tile_size: int,
    tile_format: str,
    tile_ext: str,
    resume: bool,
) -> None:
    """
    Worker function for parallel tile generation.

    Args:
        coords: Tuple of (tx, ty, zoom)
        input_path: Path to input raster
        output_path: Directory for output tiles
        resampling: Resampling method
        tile_size: Size of output tiles
        tile_format: Output format driver (WEBP or PNG)
        tile_ext: File extension (.webp or .png)
        resume: Skip existing tiles
    """
    tx, ty, zoom = coords
    mercator = GlobalMercator(tile_size)

    # Calculate tile path
    xyz_y = (2 ** zoom - 1) - ty
    tile_path = os.path.join(output_path, str(zoom), str(tx), f"{xyz_y}{tile_ext}")

    # Resume mode: skip if tile already exists
    if resume and os.path.exists(tile_path):
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
    resume: bool = False,
) -> None:
    """
    Generate XYZ tiles from a georeferenced raster in EPSG:3857.

    Args:
        input_path: Path to input raster (VRT, GeoTIFF, etc.) in EPSG:3857
        output_path: Directory for output tiles
        min_zoom: Minimum zoom level
        max_zoom: Maximum zoom level
        resampling: Resampling method name (nearest, bilinear, cubic, etc.)
        tile_format: Output tile format (WEBP or PNG)
        num_processes: Number of parallel workers
        quiet: Suppress progress output
        resume: Skip existing tiles
    """
    generator = TileGenerator(
        input_path=input_path,
        output_path=output_path,
        zoom_range=(min_zoom, max_zoom),
        resampling=resampling,
        tile_format=tile_format,
        quiet=quiet,
        resume=resume,
    )

    if num_processes > 1:
        generator.generate_tiles_parallel(num_processes)
    else:
        generator.generate_base_tiles()
        generator.generate_overview_tiles()
