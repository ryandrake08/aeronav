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
  -f, --format FORMAT     Tile format: webp, png (default: webp)
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
ZIP files ──► /vsizip/ ──► RGB Expand ──► Mask ──► GCPs ──► Warp ──► VRT ──► Tiles
                │              │           │        │        │
                │              │           │        │        └─ EPSG:3857 reprojection
                │              │           │        └─ Apply ground control points
                │              │           └─ Extract mask bounding box window
                │              └─ Palette to RGB (windowed if masked)
                └─ Direct ZIP access, no extraction
```

### Key Architectural Decisions

1. **No unzipping**: Uses GDAL's `/vsizip/` virtual filesystem to read TIFFs directly from ZIP files, eliminating disk I/O for intermediate files.

2. **In-memory processing**: Intermediate datasets use GDAL's MEM driver, avoiding temporary file writes until the final reprojected output.

3. **Two-phase tiling**:
   - *Phase 1*: Generate base tiles at max zoom from source raster (parallelized)
   - *Phase 2*: Generate overview tiles by combining child tiles (currently sequential)

4. **Dynamic work distribution**: Workers grab jobs via atomic counter rather than static assignment, ensuring good load balancing.

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

#### 4. Progress Display

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
ZIP files ──► Unzip ──► RGB Expand ──► Mask ──► GCPs ──► Warp ──► VRT ──► Tiles
                │           │           │        │        │
                │           │           │        │        └─ rasterio reproject
                │           │           │        └─ Affine transform from GCPs
                │           │           └─ rasterio mask/clip
                │           └─ In-place palette expansion
                └─ Extract to tmppath
```

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
| **Work sorting** | Yes | No |
| **Tile format** | WebP, PNG | WebP |
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
        456.webp
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
