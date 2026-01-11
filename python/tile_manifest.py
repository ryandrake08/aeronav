"""
Tile manifest computation for zoom-specific VRT tile generation.

Computes which tiles should be generated for a tileset based on
dataset coverage and max_lod constraints. At each zoom level Z, tiles
are generated from all datasets where max_lod >= Z. Datasets with
smaller max_lod values are preferred (rendered on top in VRT) as they
provide more appropriate resolution for that zoom level.
"""

import math
import os

import rasterio
from rasterio.warp import transform_bounds


def get_tile_at_zoom(lon, lat, zoom):
    """Get XYZ tile coordinates for a lon/lat at a given zoom level."""
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    lat_rad = math.radians(lat)
    y = int((1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * n)
    return x, y


def get_tile_range(lon_min, lat_min, lon_max, lat_max, zoom):
    """Get tile coordinate ranges for a bounding box at a zoom level.

    Returns list of (x_min, x_max, y_min, y_max) tuples. Usually one tuple,
    but two if the bounds cross the antimeridian.
    """
    # Clamp coordinates to valid ranges
    lon_min = max(-180, min(180, lon_min))
    lon_max = max(-180, min(180, lon_max))
    lat_min = max(-85, min(85, lat_min))
    lat_max = max(-85, min(85, lat_max))

    # Handle antimeridian crossing (lon_min > lon_max)
    if lon_min > lon_max:
        # Split into two ranges: [lon_min, 180] and [-180, lon_max]
        ranges_east = get_tile_range(lon_min, lat_min, 180, lat_max, zoom)
        ranges_west = get_tile_range(-180, lat_min, lon_max, lat_max, zoom)
        return ranges_east + ranges_west

    x_min, y_max = get_tile_at_zoom(lon_min, lat_min, zoom)
    x_max, y_min = get_tile_at_zoom(lon_max, lat_max, zoom)

    # Clamp to valid tile range
    n = 2 ** zoom
    x_min = max(0, min(n - 1, x_min))
    x_max = max(0, min(n - 1, x_max))
    y_min = max(0, min(n - 1, y_min))
    y_max = max(0, min(n - 1, y_max))

    return [(x_min, x_max, y_min, y_max)]


def add_tiles_to_set(tile_set, lon_min, lat_min, lon_max, lat_max, zoom):
    """Add all tiles covering a bounding box to a set."""
    for x_min, x_max, y_min, y_max in get_tile_range(lon_min, lat_min, lon_max, lat_max, zoom):
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tile_set.add((x, y))


def bounds_from_reprojected_tif(filepath):
    """Read geographic bounds from a reprojected TIF file.

    The reprojected TIFs are in EPSG:3857, so we transform to EPSG:4326
    for consistent tile coordinate calculation.

    Returns (lon_min, lat_min, lon_max, lat_max) or None if unavailable.
    """
    if not os.path.exists(filepath):
        return None

    try:
        with rasterio.open(filepath) as src:
            if src.crs is None:
                return None

            bounds = src.bounds
            lon_min, lat_min, lon_max, lat_max = transform_bounds(
                src.crs, 'EPSG:4326',
                bounds.left, bounds.bottom, bounds.right, bounds.top
            )
            return (lon_min, lat_min, lon_max, lat_max)
    except Exception:
        return None


def get_reprojected_tif_path(tmppath, dataset_name):
    """Get the path to a reprojected TIF file for a dataset."""
    # Reprojected files keep original names with underscore prefix
    return os.path.join(tmppath, f'_{dataset_name}.tif')


def get_tileset_zoom_range(tileset_def: dict, datasets: dict) -> tuple[int, int]:
    """
    Derive the zoom range for a tileset from its datasets.

    Returns (zoom_min, zoom_max) where:
      - zoom_min is always 0
      - zoom_max is max(max_lod) across all datasets in the tileset
    """
    zoom_min = 0
    zoom_max = max(
        datasets[ds_name].get('max_lod', 12)
        for ds_name in tileset_def['datasets']
        if ds_name in datasets
    )
    return zoom_min, zoom_max


def compute_tile_manifest(
    tileset_def: dict,
    datasets: dict,
    tmppath: str,
    zoom_min: int,
    zoom_max: int
) -> dict[int, set[tuple[int, int]]]:
    """
    Compute the set of tiles to generate for a tileset.

    For each zoom level Z from zoom_min to zoom_max:
      - Include tiles from all datasets where max_lod >= Z
      - This ensures each zoom level uses the most appropriate data sources

    At each zoom level, a zoom-specific VRT is used with datasets ordered
    so that smaller max_lod values (more appropriate for that zoom) are
    rendered on top.

    Args:
        tileset_def: Tileset definition with 'datasets' list
        datasets: Dict of dataset definitions with 'max_lod' values
        tmppath: Directory containing reprojected TIF files
        zoom_min: Minimum zoom level for the tileset
        zoom_max: Maximum zoom level for the tileset

    Returns:
        Dict mapping zoom level -> set of (x, y) tile coordinates in XYZ scheme
    """
    manifest = {z: set() for z in range(zoom_min, zoom_max + 1)}

    # Collect bounds for each dataset once
    dataset_bounds = {}
    for dataset_name in tileset_def['datasets']:
        if dataset_name not in datasets:
            continue

        tif_path = get_reprojected_tif_path(tmppath, dataset_name)
        bounds = bounds_from_reprojected_tif(tif_path)

        if bounds is not None:
            max_lod = datasets[dataset_name].get('max_lod', zoom_max)
            dataset_bounds[dataset_name] = (bounds, max_lod)

    # For each zoom level, add tiles from all datasets where max_lod >= zoom
    for z in range(zoom_min, zoom_max + 1):
        for dataset_name, (bounds, max_lod) in dataset_bounds.items():
            if max_lod >= z:
                lon_min, lat_min, lon_max, lat_max = bounds
                add_tiles_to_set(manifest[z], lon_min, lat_min, lon_max, lat_max, z)

    return manifest


def get_datasets_for_zoom(
    tileset_def: dict,
    datasets: dict,
    zoom: int
) -> list[str]:
    """
    Get list of dataset names appropriate for a specific zoom level.

    Returns datasets where max_lod >= zoom, ordered by max_lod DESCENDING
    (so that smaller max_lod datasets appear last and render on top in VRT).

    Args:
        tileset_def: Tileset definition with 'datasets' list
        datasets: Dict of dataset definitions with 'max_lod' values
        zoom: The zoom level to get datasets for

    Returns:
        List of dataset names ordered for VRT stacking (highest max_lod first)
    """
    eligible = []
    for dataset_name in tileset_def['datasets']:
        if dataset_name not in datasets:
            continue
        max_lod = datasets[dataset_name].get('max_lod', 15)
        if max_lod >= zoom:
            eligible.append((dataset_name, max_lod))

    # Sort by max_lod descending (highest first = bottom of VRT stack)
    eligible.sort(key=lambda x: x[1], reverse=True)

    return [name for name, _ in eligible]


def manifest_tile_count(manifest: dict[int, set[tuple[int, int]]]) -> int:
    """Count total tiles in a manifest."""
    return sum(len(tiles) for tiles in manifest.values())


def manifest_summary(manifest: dict[int, set[tuple[int, int]]]) -> str:
    """Generate a summary string for a manifest."""
    lines = []
    total = 0
    for zoom in sorted(manifest.keys()):
        count = len(manifest[zoom])
        total += count
        lines.append(f"  zoom {zoom:2}: {count:,} tiles")
    lines.append(f"  total: {total:,} tiles")
    return '\n'.join(lines)
