# aeronav2tiles

Tools for downloading FAA Aeronav raster charts and converting them into web map tile pyramids (XYZ format) suitable for use with Leaflet, OpenLayers, MapLibre, and other mapping applications.

## Implementations

This project provides two implementations:

| Implementation | Description | Best For |
|----------------|-------------|----------|
| **C** (`src/`) | High-performance GDAL C API implementation | Production use, large-scale processing |
| **Python** | Rasterio-based reference implementation | Development, debugging, prototyping |

Both implementations read from the same configuration file (`aeronav.conf.json`) and produce identical output tiles.

## Quick Start (C Implementation)

```bash
# 1. Download current charts from FAA
python aeronav_download.py ./zips

# 2. Build the C program
cd src && make && cd ..

# 3. Generate all tilesets
src/aeronav2tiles ./zips --tmppath /tmp/aeronav --outpath ./tiles --tilesets all
```

## Quick Start (Python Implementation)

```bash
# 1. Set up Python environment
python -m venv env && source env/bin/activate
pip install -r requirements.txt

# 2. Download and process
python aeronav_download.py ./zips
python aeronav2tiles.py --zippath ./zips --tmppath /tmp/aeronav --outpath ./tiles --tilesets all
```

---

## Concepts

### Tilesets

A **tileset** is the main organizational unit for output tiles. Each tileset represents a collection of related chart datasets mosaiced into a seamless map layer.

Available tilesets:
- **VFR**: Sectional Charts, Terminal Area Charts, Flyway Charts, Helicopter Routes
- **IFR**: Enroute High/Low Altitude (U.S., Alaska, Pacific), Area Charts
- **Planning**: Route Planning Charts (North Atlantic, North Pacific)

```bash
# List all available tilesets
src/aeronav2tiles --list
```

### Datasets

A **dataset** represents a single chart or chart inset. The 272 datasets are defined in `aeronav.conf.json` with:

- **mask**: Pixel-coordinate polygons to clip legends and non-map content
- **geobound**: Geographic bounds for lat/lon clipping
- **gcps**: Ground Control Points for georeferencing chart insets
- **max_lod**: Maximum level-of-detail zoom (determines output resolution)

### Configuration File

Both implementations read `aeronav.conf.json`:

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

---

## C Implementation

The C implementation in `src/` is optimized for high-throughput processing of large chart collections.

### Building

```bash
cd src
make
```

**Requirements**: GDAL development libraries (`libgdal-dev` on Debian/Ubuntu)

### Usage

```bash
src/aeronav2tiles [OPTIONS] <zippath>

Options:
  -o, --outpath PATH      Output directory for tiles (default: ./tiles)
  -t, --tmppath PATH      Temporary directory (default: /tmp/aeronav2tiles)
  -c, --config PATH       Config file (default: aeronav.conf.json)
  -T, --tilesets LIST     Comma-separated tilesets, or "all"
  -w, --workers N         Number of parallel workers (default: CPU count)
  -f, --format FORMAT     Tile format: png, jpeg, webp (default: png)
  -r, --resampling METHOD Resampling: nearest, bilinear, cubic, lanczos (default: bilinear)
  -l, --list              List available tilesets and exit
  -q, --quiet             Suppress progress output
  -h, --help              Show help
```

### Examples

```bash
# Process all tilesets with 24 workers
src/aeronav2tiles ./zips -o ./tiles -T all -w 24

# Process specific tilesets
src/aeronav2tiles ./zips -o ./tiles -T "vfr,ifr_low,route"

# List available tilesets
src/aeronav2tiles --list
```

### Source Code Organization

| File | Description |
|------|-------------|
| `main.c` | Entry point, CLI argument parsing |
| `config.c` | JSON configuration loading via cJSON |
| `processing.c` | Dataset processing pipeline (mask, GCPs, warp) |
| `tiling.c` | XYZ tile generation from VRT mosaics |
| `manifest.c` | Tile manifest generation based on dataset coverage |
| `jobqueue.c` | Parallel job queue with fork/pipe IPC |
| `vrt.c` | VRT (Virtual Raster) file building |
| `cJSON.c/h` | Vendored JSON parser (MIT license) |
| `aeronav.h` | Shared types and function declarations |

### Processing Pipeline

```
ZIP files ──► /vsizip/ ──► RGB Expand ──► Mask ──► GCPs ──► Warp ──► Overviews ──► VRTs ──► Tiles
                │              │           │        │        │          │           │
                │              │           │        │        │          │           └─ Zoom-specific VRTs
                │              │           │        │        │          └─ GDALBuildOverviews
                │              │           │        │        └─ EPSG:3857 reprojection
                │              │           │        └─ Apply GCPs with offset adjustment
                │              │           └─ Extract mask bounding box, track offset
                │              └─ Palette to RGB (windowed if masked)
                └─ Direct ZIP access, no extraction
```

### Key Architectural Decisions

1. **No unzipping**: Uses GDAL's `/vsizip/` virtual filesystem to read TIFFs directly from ZIP files, eliminating disk I/O for intermediate files.

2. **In-memory processing**: Intermediate datasets use GDAL's MEM driver, avoiding temporary file writes until the final reprojected output.

3. **Zoom-specific VRTs**: Each zoom level uses a VRT containing only datasets where `max_lod >= zoom`. This prevents "patchwork" artifacts where lower-resolution datasets would appear at higher zoom levels.

4. **Inline overview building**: After reprojection, `GDALBuildOverviews()` pre-computes image pyramids (levels 2-64) with AVERAGE resampling. This allows efficient tile generation at any zoom level.

5. **Single-phase parallel tiling**: All tiles across all zoom levels are collected upfront, workers fork once, and each caches VRT handles by zoom level. No per-zoom-level forking overhead.

6. **Dynamic work distribution**: Workers grab jobs via atomic counter rather than static assignment, ensuring good load balancing.

### Performance Optimizations

The C implementation includes several optimizations that dramatically improve processing speed and memory efficiency:

#### 1. Latitude-Normalized Resolution

Web Mercator (EPSG:3857) distortion causes high-latitude datasets to be massively upscaled if using equatorial resolution. For example, at 70°N (Alaska), equatorial resolution of 38 m/pixel represents only ~13 m/pixel on the ground, causing 9× pixel inflation.

**Solution**: Divide target resolution by `cos(center_latitude)` to maintain consistent ground resolution across all latitudes.

```
Before: Point Barrow SEC → 2.1 GB output, 3+ GB RAM per worker
After:  Point Barrow SEC → 230 MB output, ~300 MB RAM per worker
```

#### 2. Windowed Processing

Instead of reading entire source images then applying masks, the C implementation:
- Calculates mask bounding box first
- Reads only the required window from source
- Applies mask relative to window coordinates

For inset datasets (e.g., chart insets covering 12% of source file), this reduces I/O by 8×.

```
Before: Read 168M pixels, mask to 21M pixels
After:  Read only 21M pixels directly
```

#### 3. Work Estimation and Sorting

Datasets are sorted by estimated work (mask bounding box area) before processing. Large jobs start first, reducing the "straggler" effect where small jobs finish quickly leaving one large job running alone.

#### 4. GCP Offset Tracking

Inset datasets use Ground Control Points (GCPs) with pixel coordinates specified in the original full-image space. After windowing (RGB expansion + mask extraction), these coordinates must be adjusted:

- `expand_to_rgb()` outputs window offset if it extracts a sub-region
- `apply_mask()` tracks cumulative offset through both windowing stages
- `apply_gcps()` subtracts the cumulative offset from GCP pixel coordinates

Without this adjustment, insets would be georeferenced to incorrect locations.

#### 5. BIGTIFF Support

Reprojected datasets with embedded overviews can exceed 4GB. The implementation uses `BIGTIFF=IF_SAFER` for automatic format selection and `COMPRESS_OVERVIEW=LZW` for overview compression.

#### 6. Progress Display

Real-time display showing each worker's current dataset, using ANSI escape codes for in-place updates:

```
Processing: 45/272 complete
  W0: Seattle SEC
  W1: Portland SEC
  W2: San Francisco SEC
  ...
```

---

## Python Implementation

The Python implementation serves as the reference implementation and is useful for development and debugging.

### Requirements

- Python 3.13+
- Dependencies: `beautifulsoup4`, `numpy`, `rasterio`

### Usage

```bash
python aeronav2tiles.py [OPTIONS]

Options:
  --zippath PATH              Directory containing ZIP files
  --tmppath PATH              Temporary directory
  --outpath PATH              Output directory
  --tilesets NAMES            Comma-separated tileset names, or "all"
  --list-tilesets             List available tilesets
  --single DATASET            Process single dataset (development)
  --existing                  Use existing reprojected files (development)
  --reproject-resampling M    Resampling for reprojection (default: bilinear)
  --tile-resampling M         Resampling for tiles (default: bilinear)
  --quiet                     Suppress output
  --cleanup                   Remove temp directory after processing
```

### Processing Pipeline

```
ZIP files ──► Unzip ──► RGB Expand ──► Mask ──► GCPs ──► Warp ──► Zoom VRTs ──► Tiles
                │           │           │        │        │           │
                │           │           │        │        │           └─ VRT per zoom level
                │           │           │        │        └─ rasterio reproject
                │           │           │        └─ Affine transform from GCPs
                │           │           └─ rasterio mask/clip
                │           └─ In-place palette expansion
                └─ Extract to tmppath
```

The Python implementation uses `rasterio.windows.transform()` to adjust the GCP-derived transform for windowing, rather than explicitly tracking pixel offsets like the C implementation.

---

## Implementation Comparison

| Aspect | C Implementation | Python Implementation |
|--------|------------------|----------------------|
| **ZIP handling** | Direct via `/vsizip/` | Extracts to temp directory |
| **Intermediate storage** | In-memory (MEM driver) | Disk files in tmppath |
| **Parallelization** | fork() with IPC | multiprocessing |
| **Progress display** | ANSI terminal updates | Print statements |
| **Latitude normalization** | Yes | Yes |
| **Windowed mask/RGB** | Yes | No (full image) |
| **Inline overviews** | Yes (GDALBuildOverviews) | No |
| **Zoom-specific VRTs** | Yes | Yes |
| **GCP offset tracking** | Yes (explicit) | N/A (handled by transform adjustment) |
| **Work sorting** | Yes | No |
| **Tile format** | PNG, JPEG, WebP | PNG, JPEG, WebP |
| **Dependencies** | GDAL C library | rasterio, numpy |

**When to use C**: Production processing, large datasets, memory-constrained systems

**When to use Python**: Debugging dataset issues, prototyping new features, platforms without C compiler

---

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

### Testing Tiles

A Leaflet viewer is generated at `outpath/leaflet.html` for visual verification. Open in a browser to test all tilesets interactively.

---

## Chart Download

The `aeronav_download.py` script downloads current charts from FAA:

```bash
python aeronav_download.py ./zips [--quiet]
```

Features:
- Scrapes aeronav.faa.gov for current chart URLs
- Uses If-Modified-Since for incremental updates
- Downloads VFR, IFR, and planning chart ZIPs

---

## Development

### Adding New Datasets

1. Download the chart ZIP and examine with `gdalinfo`
2. Determine mask polygon coordinates (pixel space) to clip non-map content
3. For insets without georeferencing, identify GCPs (pixel x,y → lon,lat)
4. Add dataset definition to `aeronav.conf.json`
5. Add dataset to appropriate tileset(s)

### Debugging

```bash
# Process single dataset (Python)
python aeronav2tiles.py --single "Seattle SEC" --zippath ./zips --tmppath /tmp/debug --outpath ./debug

# Check intermediate files
gdalinfo /tmp/debug/Seattle_SEC_reprojected.tif
```

### Code Style

- C: K&R style, 4-space indentation
- Python: PEP 8

---

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

Chart data is produced by the FAA and is in the public domain.
