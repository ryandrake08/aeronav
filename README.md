# aeronav2tiles

Python tools for downloading FAA Aeronav raster charts and converting them into web map tile pyramids (XYZ format) suitable for use with tile servers and mapping applications.

## Features

- Downloads current chart data directly from aeronav.faa.gov
- Supports VFR Sectional Charts, IFR Enroute Charts, Terminal Area Charts, and more
- Generates tiles in WEBP format with Web Mercator (EPSG:3857) projection
- Handles chart georeferencing, masking, and seamless mosaicing

## Requirements

- Python 3.13+

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/aeronav.git
   cd aeronav
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv env
   source env/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Dependencies

| Package | Description |
|---------|-------------|
| `beautifulsoup4` | Parses HTML to scrape chart download URLs (used by aeronav_download.py) |
| `numpy` | Array operations for raster data manipulation |
| `rasterio` | Geospatial raster I/O, reprojection, and tile generation |

## Concepts

### Tilesets

A **tileset** is the main organizational unit for output tiles. Each tileset represents a collection of related chart datasets that are mosaiced together into a seamless map layer. Tilesets can be enabled or disabled independently, allowing you to generate only the chart types you need.

Each tileset defines:
- A zoom level range at which the data is visible
- A maximum level-of-detail zoom that determines output resolution
- A list of datasets to include

Available tilesets include:
- VFR Planning Charts, Sectional Charts, Terminal Area Charts, Flyway Charts
- Helicopter Route Charts
- IFR Enroute High/Low Altitude charts (U.S., Alaska, Pacific, Caribbean)
- IFR Area Charts
- Route Planning Charts (North Atlantic, North Pacific, West Atlantic)

Run `--list-tilesets` to see all available tilesets.

### Datasets

A **dataset** represents a single chunk of map data within a tileset. Most datasets correspond 1:1 with GeoTIFF files downloaded from the FAA, but some represent subsets of a file (such as chart insets that need separate georeferencing).

Each dataset may define:
- Pixel-coordinate masks to clip legends, insets, and non-map content
- Geographic bounds for lat/lon boundary constraints
- Ground Control Points (GCPs) for georeferencing chart insets

## Processing Pipeline

The tools implement a two-stage workflow:

### Stage 1: Download (`aeronav_download.py`)

Scrapes aeronav.faa.gov for current chart URLs and downloads ZIP files.

### Stage 2: Process (`aeronav2tiles.py`)

See the `main()` function docstring for a comprehensive description.

1. **Unzip**: Extracts GeoTIFF files to a temporary directory
2. **Select Tilesets**: Determines which tilesets to generate based on command-line arguments
3. **Process Datasets**: For each dataset in the selected tilesets:
   - **Expand to RGB**: Converts paletted images to RGB (one-time preprocessing, modifies source files in-place)
   - **Clip**: Applies pixel-coordinate masks to remove legends, insets, and non-map content
   - **Georeference**: Applies GCPs for datasets without built-in georeferencing (e.g., chart insets)
   - **Reproject**: Transforms to Web Mercator (EPSG:3857) at the tileset's target resolution
4. **Merge**: Combines datasets into VRT (virtual raster) files per tileset
5. **Generate Tiles**: Creates XYZ tile pyramids

## Usage

### Download Charts

```bash
python aeronav_download.py /path/to/zips
```

### Process Charts into Tiles

```bash
python aeronav2tiles.py --zippath /path/to/zips --tmppath /path/to/tmp --outpath /path/to/output --all
```

### Complete Workflow

```bash
# Download current charts
python aeronav_download.py ./zips

# Process all tilesets
python aeronav2tiles.py --all --zippath ./zips --tmppath /tmp/aeronav2tiles --outpath ./tiles --cleanup
```

### Generate Specific Tilesets

```bash
# List available tilesets
python aeronav2tiles.py --list-tilesets

# Generate specific tilesets
python aeronav2tiles.py --tilesets "VFR Sectional Charts" "IFR Enroute Low Altitude U.S." --zippath ./zips --tmppath /tmp/aeronav2tiles --outpath ./tiles
```

### Command-Line Options

**aeronav_download.py**:

| Option | Description |
|--------|-------------|
| `zippath` | Directory for downloaded ZIP files (positional or --zippath) |
| `--quiet` | Suppress output messages |

**aeronav2tiles.py**:

| Option | Description |
|--------|-------------|
| `--all` | Generate all available tilesets |
| `--tilesets <names>` | Generate specific tilesets (space-separated) |
| `--list-tilesets` | List available tilesets and exit |
| `--zippath <path>` | Directory containing downloaded ZIP files |
| `--tmppath <path>` | Directory for temporary processing files (default: /tmp/aeronav2tiles) |
| `--outpath <path>` | Directory for output tiles |
| `--epsg <code>` | Destination EPSG code (default: 3857 for Web Mercator) |
| `--reproject-resampling <method>` | Resampling method for reprojection (default: bilinear) |
| `--tile-resampling <method>` | Resampling method for tile generation (default: bilinear) |
| `--quiet` | Suppress output messages |
| `--cleanup` | Remove temporary directory after processing |

**Resampling methods**: nearest, bilinear, cubic, cubicspline, lanczos, average, mode

## Output

Tiles are generated in XYZ format with the following structure:

```
outpath/
  tileset-name/
    zoom/
      x/
        y.webp
```

The tiles can be served using any standard tile server or used directly with mapping libraries like Leaflet, OpenLayers, or MapLibre.

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.
