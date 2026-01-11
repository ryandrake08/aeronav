# aeronav2tiles

Tools for downloading FAA Aeronav raster charts and converting them into web map tile pyramids (XYZ format) suitable for use with Leaflet, OpenLayers, MapLibre, and other mapping applications.

## Quick Start

```bash
# 1. Build
make

# 2. Download current charts from FAA
./aeronav_download ./zips

# 3. Generate all tilesets
./aeronav2tiles -z ./zips -t /tmp/aeronav -o ./tiles
```

## Building

**Requirements** (Debian/Ubuntu):
```bash
sudo apt install libgdal-dev libcurl4-openssl-dev libxml2-dev
```

**Requirements** (macOS with Homebrew):
```bash
brew install gdal curl libxml2
```

**Requirements** (macOS with MacPorts):
```bash
sudo port install gdal +curl libcurl libxml2
```

**Build**:
```bash
make           # Release build (optimized)
make debug     # Debug build (with sanitizers)
make clean     # Remove build artifacts
```

## Usage

### Downloading Charts

```bash
./aeronav_download <output_directory> [-q]
```

Downloads current VFR, IFR, and planning charts from aeronav.faa.gov. Uses If-Modified-Since headers for efficient incremental updates.

### Generating Tiles

```bash
./aeronav2tiles [OPTIONS]

Options:
  -c, --config <path>    Config file (default: aeronav.conf.json)
  -z, --zippath <path>   Directory containing ZIP files
  -t, --tmppath <path>   Temp directory (default: /tmp/aeronav2tiles)
  -o, --outpath <path>   Output directory for tiles
  -s, --tilesets <names> Comma-separated tilesets, or "all" (default: all)
  -l, --list             List available tilesets and exit
  -C, --cleanup          Remove temp directory after processing
  -T, --tile-only        Skip processing, reuse existing reprojected files
  -e, --epsg <code>      Target EPSG code (default: 3857)
  -j, --jobs <N>         Concurrent dataset processes (default: auto)
  -w, --tile-workers <N> Tile generation workers (default: auto)
  -f, --format <fmt>     Tile format: png, jpeg, webp (default: png)
  -q, --quiet            Suppress progress output
  --reproject-resampling <method>  Resampling for reprojection (default: bilinear)
  --tile-resampling <method>       Resampling for tiles (default: bilinear)
```

**Resampling methods**: nearest, bilinear, cubic, cubicspline, lanczos, average, mode

**Examples**:
```bash
# Process all tilesets with default settings
./aeronav2tiles -z ./zips -o ./tiles

# Process specific tilesets
./aeronav2tiles -z ./zips -o ./tiles -s "vfr,ifr_low"

# List available tilesets
./aeronav2tiles --list

# Process only, no tile generation (for debugging)
./aeronav2tiles -z ./zips -t /tmp/aeronav
```

## Concepts

### Tilesets

A **tileset** is the main organizational unit for output tiles. Each tileset represents a collection of related chart datasets mosaiced into a seamless map layer.

Available tilesets:
- **VFR**: Sectional Charts, Terminal Area Charts, Flyway Charts, Helicopter Routes
- **IFR**: Enroute High/Low Altitude (U.S., Alaska, Pacific), Area Charts
- **Planning**: Route Planning Charts (North Atlantic, North Pacific)

### Datasets

A **dataset** represents a single chart or chart inset. The 272 datasets are defined in `aeronav.conf.json` with:

- **mask**: Pixel-coordinate polygons to clip legends and non-map content
- **geobound**: Geographic bounds for lat/lon clipping
- **gcps**: Ground Control Points for georeferencing chart insets
- **max_lod**: Maximum level-of-detail zoom (determines output resolution)

### Configuration File

Dataset and tileset definitions are stored in `aeronav.conf.json`:

```json
{
  "datasets": {
    "Seattle SEC": {
      "zip_file": "Seattle",
      "mask": [[[x,y], [x,y], ...]],
      "max_lod": 12
    }
  },
  "tilesets": {
    "VFR Sectional Charts": {
      "tile_path": "vfr",
      "zoom": [10, 12],
      "datasets": ["Seattle SEC", "Portland SEC", ...]
    }
  }
}
```

## Processing Pipeline

```
ZIP files --> /vsizip/ --> RGB Expand --> Mask --> GCPs --> Warp --> Overviews --> VRTs --> Tiles
                |              |           |        |        |          |           |
                |              |           |        |        |          |           +-- Zoom-specific VRTs
                |              |           |        |        |          +-- GDALBuildOverviews
                |              |           |        |        +-- EPSG:3857 reprojection
                |              |           |        +-- Apply GCPs with offset adjustment
                |              |           +-- Extract mask bounding box, track offset
                |              +-- Palette to RGB
                +-- Direct ZIP access, no extraction
```

### Key Architectural Decisions

1. **No unzipping**: Uses GDAL's `/vsizip/` virtual filesystem to read TIFFs directly from ZIP files, eliminating disk I/O for intermediate files.

2. **In-memory processing**: Intermediate datasets use GDAL's MEM driver, avoiding temporary file writes until the final reprojected output.

3. **Zoom-specific VRTs**: Each zoom level uses a VRT containing only datasets where `max_lod >= zoom`. This prevents "patchwork" artifacts where lower-resolution datasets would appear at higher zoom levels.

4. **Inline overview building**: After reprojection, `GDALBuildOverviews()` pre-computes image pyramids (levels 2-64) with AVERAGE resampling. This allows efficient tile generation at any zoom level.

5. **Single-phase parallel tiling**: All tiles across all zoom levels are collected upfront, workers fork once, and each caches VRT handles by zoom level. No per-zoom-level forking overhead.

6. **Dynamic work distribution**: Workers grab jobs via atomic counter rather than static assignment, ensuring good load balancing.

### Performance Optimizations

#### Latitude-Normalized Resolution

Web Mercator (EPSG:3857) distortion causes high-latitude datasets to be massively upscaled if using equatorial resolution. For example, at 70N (Alaska), equatorial resolution of 38 m/pixel represents only ~13 m/pixel on the ground, causing 9x pixel inflation.

**Solution**: Divide target resolution by `cos(center_latitude)` to maintain consistent ground resolution across all latitudes.

```
Before: Point Barrow SEC -> 2.1 GB output, 3+ GB RAM per worker
After:  Point Barrow SEC -> 230 MB output, ~300 MB RAM per worker
```

#### Windowed Processing

Instead of reading entire source images then applying masks, the implementation:
- Calculates mask bounding box first
- Reads only the required window from source
- Applies mask relative to window coordinates

For inset datasets (e.g., chart insets covering 12% of source file), this reduces I/O by 8x.

#### Work Estimation and Sorting

Datasets are sorted by estimated work (mask bounding box area) before processing. Large jobs start first, reducing the "straggler" effect where small jobs finish quickly leaving one large job running alone.

## Source Code

| File | Description |
|------|-------------|
| `src/aeronav2tiles.c` | Tile generator entry point and CLI parsing |
| `src/aeronav_download.c` | Chart downloader using libcurl and libxml2 |
| `src/config.c` | JSON configuration loading via cJSON |
| `src/processing.c` | Dataset processing pipeline (mask, GCPs, warp) |
| `src/tiling.c` | XYZ tile generation from VRT mosaics |
| `src/jobqueue.c` | Parallel job queue with fork/pipe IPC |
| `src/cJSON.c/h` | Vendored JSON parser (MIT license) |
| `src/aeronav.h` | Shared types and function declarations |

## Output

Tiles are generated in XYZ format:

```
outpath/
  vfr/
    10/
      123/
        456.png
  ifr_low/
    ...
```

## Python Implementation

A Python implementation is available in the `python/` directory. It serves as a reference implementation useful for development and debugging.

```bash
cd python
python -m venv env && source env/bin/activate
pip install -r requirements.txt

python aeronav_download.py ../zips
python aeronav2tiles.py --zippath ../zips --tmppath /tmp/aeronav --outpath ../tiles --tilesets all
```

The Python implementation uses rasterio instead of the GDAL C API. It produces identical output tiles but extracts ZIPs to disk rather than using `/vsizip/`.

## Development

### Adding New Datasets

1. Download the chart ZIP and examine with `gdalinfo`
2. Determine mask polygon coordinates (pixel space) to clip non-map content
3. For insets without georeferencing, identify GCPs (pixel x,y -> lon,lat)
4. Add dataset definition to `aeronav.conf.json`
5. Add dataset to appropriate tileset(s)

### Code Style

- C: K&R style, 4-space indentation
- Python: PEP 8

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

Chart data is produced by the FAA and is in the public domain.
