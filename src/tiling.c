/*
 * aeronav2tiles - Tile generation
 *
 * Generates XYZ web map tiles from a processed raster dataset.
 * Uses the GlobalMercator scheme (EPSG:3857).
 */

#include <dirent.h>
#include <errno.h>
#include <math.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cpl_conv.h>
#include <gdal.h>
#include "aeronav.h"
#include "tile_encode.h"

/* Standard web map tile size in pixels */
#define TILE_SIZE ((size_t)256)

/* Half-width of EPSG:3857 (Web Mercator) in meters: π × Earth radius.
 * The full map extent is -ORIGIN_SHIFT to +ORIGIN_SHIFT on both axes. */
#define ORIGIN_SHIFT 20037508.342789244

/* ============================================================================
 * Dataset Info Collection
 *
 * Pre-computes metadata for each dataset in a tileset (bounds, geotransform,
 * dimensions) for direct GeoTIFF reading during tile generation. Datasets are
 * ordered by max_lod descending so that lower max_lod datasets (more
 * appropriate for the zoom level) render on top.
 * ============================================================================ */

/* Pre-computed metadata for a reprojected dataset */
typedef struct {
    char path[PATH_SIZE];
    double min_x, min_y, max_x, max_y; /* EPSG:3857 bounds */
    double gt[6];                       /* Geotransform */
    int width, height;
    int band_count;
    int max_lod;
} TileDatasetInfo;

/* Comparison function for sorting TileDatasetInfo by max_lod descending */
static int compare_ds_info_by_max_lod_desc(const void *a, const void *b) {
    const TileDatasetInfo *ea = (const TileDatasetInfo *)a;
    const TileDatasetInfo *eb = (const TileDatasetInfo *)b;
    return eb->max_lod - ea->max_lod;
}

/*
 * Collect metadata for all reprojected datasets in a tileset.
 *
 * Opens each reprojected TIF, reads geotransform/dimensions/band count,
 * and stores in a TileDatasetInfo array sorted by max_lod descending.
 * Skips datasets whose TIF files don't exist.
 *
 * Returns 0 on success, -1 on allocation failure.
 * Sets *out_infos and *out_count. Caller must free *out_infos.
 */
static int collect_dataset_infos(const Tileset *tileset, const char *tmppath, TileDatasetInfo **out_infos,
                                 int *out_count) {
    TileDatasetInfo *infos = malloc(tileset->dataset_count * sizeof(TileDatasetInfo));
    if (!infos) return -1;

    int count = 0;
    for (size_t d = 0; d < tileset->dataset_count; d++) {
        const Dataset *dataset = get_dataset(tileset->datasets[d]);
        if (!dataset) continue;

        TileDatasetInfo *info = &infos[count];
        snprintf(info->path, PATH_SIZE, "%s/%s", tmppath, dataset->tmp_file);

        GDALDatasetH ds = GDALOpen(info->path, GA_ReadOnly);
        if (!ds) continue;

        if (GDALGetGeoTransform(ds, info->gt) != CE_None) {
            GDALClose(ds);
            continue;
        }

        info->width = GDALGetRasterXSize(ds);
        info->height = GDALGetRasterYSize(ds);
        info->band_count = GDALGetRasterCount(ds);
        info->max_lod = dataset->max_lod;

        info->min_x = info->gt[0];
        info->max_x = info->gt[0] + info->width * info->gt[1];
        info->max_y = info->gt[3];
        info->min_y = info->gt[3] + info->height * info->gt[5];

        GDALClose(ds);
        count++;
    }

    /* Sort by max_lod descending (highest first = background, lowest last = foreground) */
    qsort(infos, count, sizeof(TileDatasetInfo), compare_ds_info_by_max_lod_desc);

    *out_infos = infos;
    *out_count = count;
    return 0;
}

/* ============================================================================
 * Tile Manifest
 *
 * Computes which tiles should be generated based on dataset coverage
 * and max_lod constraints.
 * ============================================================================ */

/* Packed tile coordinate for efficient storage and binary search */
typedef unsigned int PackedTile; /* x in upper 16 bits, y in lower 16 bits */

/* Tiles for a single zoom level */
typedef struct {
    PackedTile *tiles; /* Sorted array for binary search */
    int count;
    int capacity;
} ZoomTileSet;

/* Complete manifest for a tileset */
typedef struct {
    ZoomTileSet *zooms; /* Array indexed by (zoom - min_zoom) */
    int min_zoom;
    int max_zoom;
} TileManifest;

/* Pack x,y into a single 32-bit value for efficient storage */
static inline PackedTile pack_tile(int x, int y) {
    return ((unsigned int)x << 16) | (unsigned int)y;
}

/* Comparison function for qsort */
static int compare_tiles(const void *a, const void *b) {
    PackedTile ta = *(const PackedTile *)a;
    PackedTile tb = *(const PackedTile *)b;
    if (ta < tb) return -1;
    if (ta > tb) return 1;
    return 0;
}

/* Get XYZ tile coordinates for a lon/lat at a given zoom level */
static void get_tile_at_zoom(double lon, double lat, int zoom, int *x, int *y) {
    int n = 1 << zoom;
    *x = (int)((lon + 180.0) / 360.0 * n);
    double lat_rad = lat * M_PI / 180.0;
    *y = (int)((1.0 - asinh(tan(lat_rad)) / M_PI) / 2.0 * n);

    /* Clamp to valid range */
    if (*x < 0) *x = 0;
    if (*x >= n) *x = n - 1;
    if (*y < 0) *y = 0;
    if (*y >= n) *y = n - 1;
}

/* Add a tile to a zoom level's tile set */
static int add_tile(ZoomTileSet *zts, int x, int y) {
    PackedTile pt = pack_tile(x, y);

    /* Grow array if needed */
    if (zts->count >= zts->capacity) {
        int new_cap = zts->capacity == 0 ? 256 : zts->capacity * 2;
        PackedTile *new_tiles = realloc(zts->tiles, new_cap * sizeof(PackedTile));
        if (!new_tiles) return -1;
        zts->tiles = new_tiles;
        zts->capacity = new_cap;
    }

    zts->tiles[zts->count++] = pt;
    return 0;
}

/* Add all tiles covering a bounding box to a zoom level */
static int add_tiles_for_bounds(ZoomTileSet *zts, double lon_min, double lat_min, double lon_max, double lat_max,
                                int zoom) {
    /* Clamp to valid ranges */
    if (lon_min < -180) lon_min = -180;
    if (lon_max > 180) lon_max = 180;
    if (lat_min < -85) lat_min = -85;
    if (lat_max > 85) lat_max = 85;

    /* Handle antimeridian crossing */
    if (lon_min > lon_max) {
        /* Split into two ranges */
        if (add_tiles_for_bounds(zts, lon_min, lat_min, 180, lat_max, zoom) < 0) return -1;
        return add_tiles_for_bounds(zts, -180, lat_min, lon_max, lat_max, zoom);
    }

    int x_min, y_max, x_max, y_min;
    get_tile_at_zoom(lon_min, lat_min, zoom, &x_min, &y_max);
    get_tile_at_zoom(lon_max, lat_max, zoom, &x_max, &y_min);

    /* Add all tiles in range */
    for (int x = x_min; x <= x_max; x++) {
        for (int y = y_min; y <= y_max; y++) {
            if (add_tile(zts, x, y) < 0) return -1;
        }
    }

    return 0;
}

/* Read geographic bounds from a reprojected TIF file (EPSG:3857 -> EPSG:4326) */
static int bounds_from_tif(const char *filepath, double *lon_min, double *lat_min, double *lon_max, double *lat_max) {
    GDALDatasetH ds = GDALOpen(filepath, GA_ReadOnly);
    if (!ds) return -1;

    double gt[6];
    if (GDALGetGeoTransform(ds, gt) != CE_None) {
        GDALClose(ds);
        return -1;
    }

    int width = GDALGetRasterXSize(ds);
    int height = GDALGetRasterYSize(ds);
    GDALClose(ds);

    /* Get bounds in EPSG:3857 */
    double mx_min = gt[0];
    double mx_max = gt[0] + width * gt[1];
    double my_max = gt[3];
    double my_min = gt[3] + height * gt[5]; /* gt[5] is negative */

    /* Convert EPSG:3857 to EPSG:4326 */
    *lon_min = mx_min * 180.0 / ORIGIN_SHIFT;
    *lon_max = mx_max * 180.0 / ORIGIN_SHIFT;

    /* Inverse Mercator projection for latitude */
    *lat_max = atan(sinh(my_max * M_PI / ORIGIN_SHIFT)) * 180.0 / M_PI;
    *lat_min = atan(sinh(my_min * M_PI / ORIGIN_SHIFT)) * 180.0 / M_PI;

    return 0;
}

/* Sort and deduplicate tiles in a zoom level */
static void finalize_zoom(ZoomTileSet *zts) {
    if (zts->count <= 1) return;

    /* Sort tiles */
    qsort(zts->tiles, zts->count, sizeof(PackedTile), compare_tiles);

    /* Remove duplicates */
    int write = 1;
    for (int read = 1; read < zts->count; read++) {
        if (zts->tiles[read] != zts->tiles[write - 1]) {
            zts->tiles[write++] = zts->tiles[read];
        }
    }
    zts->count = write;
}

static void free_tile_manifest(TileManifest *m) {
    if (!m) return;

    if (m->zooms) {
        for (int i = 0; i <= m->max_zoom - m->min_zoom; i++) {
            free(m->zooms[i].tiles);
        }
        free(m->zooms);
    }
    free(m);
}

static TileManifest *build_tile_manifest(const Tileset *tileset, const char *tmppath) {
    TileManifest *m = calloc(1, sizeof(TileManifest));
    if (!m) return NULL;

    m->min_zoom = tileset->zoom_min;
    m->max_zoom = tileset->zoom_max;

    int zoom_count = m->max_zoom - m->min_zoom + 1;
    m->zooms = calloc(zoom_count, sizeof(ZoomTileSet));
    if (!m->zooms) {
        free(m);
        return NULL;
    }

    /* Process each dataset in the tileset */
    for (size_t d = 0; d < tileset->dataset_count; d++) {
        const char *dataset_name = tileset->datasets[d];
        const Dataset *dataset = get_dataset(dataset_name);
        if (!dataset) continue;

        /* Build path to reprojected TIF */
        char tif_path[PATH_SIZE];
        snprintf(tif_path, sizeof(tif_path), "%s/%s", tmppath, dataset->tmp_file);

        /* Read bounds from TIF */
        double lon_min, lat_min, lon_max, lat_max;
        if (bounds_from_tif(tif_path, &lon_min, &lat_min, &lon_max, &lat_max) < 0) {
            /* TIF doesn't exist yet or can't be read - skip */
            continue;
        }

        /* Dataset's effective max zoom (clamped to tileset range) */
        int ds_max_zoom = dataset->max_lod;
        if (ds_max_zoom > m->max_zoom) ds_max_zoom = m->max_zoom;
        if (ds_max_zoom < m->min_zoom) ds_max_zoom = m->min_zoom;

        /* Add tiles at EVERY zoom level from min_zoom to dataset's max_lod.
         * At each zoom level Z, only datasets where max_lod >= Z contribute.
         * This dataset qualifies for all zoom levels from min_zoom to its
         * max_lod. */
        for (int z = m->min_zoom; z <= ds_max_zoom; z++) {
            ZoomTileSet *zts = &m->zooms[z - m->min_zoom];
            if (add_tiles_for_bounds(zts, lon_min, lat_min, lon_max, lat_max, z) < 0) {
                free_tile_manifest(m);
                return NULL;
            }
        }
    }

    /* Finalize all zoom levels (sort and dedupe) */
    for (int i = 0; i < zoom_count; i++) {
        finalize_zoom(&m->zooms[i]);
    }

    return m;
}

/* ============================================================================
 * Tile Generation Helpers
 * ============================================================================ */

/* Convert resampling string to GDAL enum */
static GDALRIOResampleAlg parse_resampling(const char *resampling) {
    if (strcmp(resampling, "nearest") == 0) return GRIORA_NearestNeighbour;
    if (strcmp(resampling, "bilinear") == 0) return GRIORA_Bilinear;
    if (strcmp(resampling, "cubic") == 0) return GRIORA_Cubic;
    if (strcmp(resampling, "cubicspline") == 0) return GRIORA_CubicSpline;
    if (strcmp(resampling, "lanczos") == 0) return GRIORA_Lanczos;
    if (strcmp(resampling, "average") == 0) return GRIORA_Average;
    if (strcmp(resampling, "mode") == 0) return GRIORA_Mode;
    return GRIORA_Bilinear; /* Default */
}

/* ============================================================================
 * GlobalMercator Calculations
 * ============================================================================ */

double resolution_for_zoom(int zoom) {
    /* Resolution in meters/pixel at given zoom level */
    /* At zoom 0, the world is 256 pixels, at zoom n, it's 256 * 2^n pixels */
    /* World circumference at equator = 2 * pi * 6378137 meters */
    double world_size = 2 * ORIGIN_SHIFT; /* Full extent in meters */
    double tile_count = pow(2, zoom);
    return world_size / (tile_count * TILE_SIZE);
}

static void tile_bounds(int z, int x, int y, double *min_x, double *min_y, double *max_x, double *max_y) {
    /* Get the bounds of a tile in EPSG:3857 coordinates */
    double res = resolution_for_zoom(z) * TILE_SIZE; /* Tile size in meters */

    *min_x = -ORIGIN_SHIFT + x * res;
    *max_x = -ORIGIN_SHIFT + (x + 1) * res;

    /*
     * XYZ tiles have y=0 at north (top), but EPSG:3857 has y=0 at equator
     * with positive values going north. Convert XYZ y to TMS y for bounds.
     */
    int max_tile = (1 << z) - 1;
    int tms_y = max_tile - y;

    *min_y = -ORIGIN_SHIFT + tms_y * res;
    *max_y = -ORIGIN_SHIFT + (tms_y + 1) * res;
}

/* Tile coordinate for parallel processing */
typedef struct {
    int z;
    int x;
    int y;
} TileCoord;

/* ============================================================================
 * Tile Generation from Source Raster
 * ============================================================================ */

/*
 * Read from a single dataset into a tile buffer region.
 *
 * Computes the source window and destination position using pre-computed
 * dataset metadata. Writes into the specified buffer at the correct offset.
 * Fills alpha to 255 if the source has no alpha band.
 *
 * Returns 1 if the dataset contributed pixels, 0 if it didn't (no intersection,
 * empty region, or read failure).
 */
static int read_dataset_into_tile(GDALDatasetH ds, const TileDatasetInfo *info, double tile_min_x, double tile_min_y,
                                  double tile_max_x, double tile_max_y, GDALRIOResampleAlg resample_alg,
                                  unsigned char *buf) {
    /* Calculate pixel coordinates in source dataset */
    double src_x0 = (tile_min_x - info->gt[0]) / info->gt[1];
    double src_y0 = (tile_max_y - info->gt[3]) / info->gt[5]; /* gt[5] is negative */
    double src_x1 = (tile_max_x - info->gt[0]) / info->gt[1];
    double src_y1 = (tile_min_y - info->gt[3]) / info->gt[5];

    /* Clamp to dataset bounds */
    if (src_x0 < 0) src_x0 = 0;
    if (src_y0 < 0) src_y0 = 0;
    if (src_x1 > info->width) src_x1 = info->width;
    if (src_y1 > info->height) src_y1 = info->height;

    int read_x = (int)src_x0;
    int read_y = (int)src_y0;
    int read_w = (int)(src_x1 - src_x0 + 0.5);
    int read_h = (int)(src_y1 - src_y0 + 0.5);

    if (read_w <= 0 || read_h <= 0) return 0;

    /* Calculate where in the tile this data goes */
    int tile_x0 = 0, tile_y0 = 0, tile_w = TILE_SIZE, tile_h = TILE_SIZE;

    if (tile_min_x < info->min_x) {
        tile_x0 = (int)((info->min_x - tile_min_x) / (tile_max_x - tile_min_x) * TILE_SIZE);
        tile_w = TILE_SIZE - tile_x0;
    }
    if (tile_max_x > info->max_x) {
        tile_w = (int)((info->max_x - tile_min_x) / (tile_max_x - tile_min_x) * TILE_SIZE) - tile_x0;
    }
    if (tile_max_y > info->max_y) {
        tile_y0 = (int)((tile_max_y - info->max_y) / (tile_max_y - tile_min_y) * TILE_SIZE);
        tile_h = TILE_SIZE - tile_y0;
    }
    if (tile_min_y < info->min_y) {
        tile_h = (int)((tile_max_y - info->min_y) / (tile_max_y - tile_min_y) * TILE_SIZE) - tile_y0;
    }

    if (tile_w <= 0 || tile_h <= 0) return 0;

    /* Read all bands at once into pixel-interleaved RGBA layout */
    int read_bands = (info->band_count >= 4) ? 4 : 3;
    int band_map[] = {1, 2, 3, 4};

    GDALRasterIOExtraArg extra_arg;
    INIT_RASTERIO_EXTRA_ARG(extra_arg);
    extra_arg.eResampleAlg = resample_alg;

    unsigned char *dst = buf + ((size_t)tile_y0 * TILE_SIZE + tile_x0) * 4;

    if (GDALDatasetRasterIOEx(ds, GF_Read, read_x, read_y, read_w, read_h, dst, tile_w, tile_h, GDT_Byte, read_bands,
                              band_map, 4, (GSpacing)TILE_SIZE * 4, 1, &extra_arg) != CE_None) {
        return 0;
    }

    /* Fill alpha channel if source has no alpha band */
    if (info->band_count < 4) {
        for (int y_off = 0; y_off < tile_h; y_off++) {
            for (int x_off = 0; x_off < tile_w; x_off++) {
                buf[((tile_y0 + y_off) * TILE_SIZE + (tile_x0 + x_off)) * 4 + 3] = 255;
            }
        }
    }

    return 1;
}

/*
 * Generate a single tile by reading directly from reprojected GeoTIFFs.
 *
 * Iterates datasets in max_lod descending order (highest first = background).
 * The first contributing dataset reads directly into tile_data (zero-copy).
 * Subsequent datasets read into blend_buf and composite onto tile_data
 * using painter's algorithm (overwrite where alpha > 0).
 */
static int generate_base_tile(const TileDatasetInfo *ds_infos, int ds_count, GDALDatasetH *ds_handles, int z, int x,
                               int y, const char *outpath, const char *tile_path, const char *format_ext,
                               tile_encode_fn encoder, GDALRIOResampleAlg resample_alg, unsigned char *tile_data,
                               unsigned char *blend_buf) {
    /* Skip if tile already exists */
    char file_path[PATH_SIZE];
    snprintf(file_path, sizeof(file_path), "%s/%s/%d/%d/%d.%s", outpath, tile_path, z, x, y, format_ext);
    struct stat st;
    if (stat(file_path, &st) == 0) {
        return 2; /* Skipped - already exists */
    }

    /* Get tile bounds in EPSG:3857 */
    double tile_min_x, tile_min_y, tile_max_x, tile_max_y;
    tile_bounds(z, x, y, &tile_min_x, &tile_min_y, &tile_max_x, &tile_max_y);

    /* Clear tile buffer to transparent black */
    memset(tile_data, 0, TILE_SIZE * TILE_SIZE * 4);

    int has_data = 0;

    /* Iterate datasets in max_lod descending order (highest first = background,
     * lowest last = foreground). Later datasets overwrite earlier ones. */
    for (int i = 0; i < ds_count; i++) {
        const TileDatasetInfo *info = &ds_infos[i];

        /* Skip datasets that don't qualify for this zoom level */
        if (info->max_lod < z) continue;

        /* Quick bounds check */
        if (tile_max_x <= info->min_x || tile_min_x >= info->max_x || tile_max_y <= info->min_y ||
            tile_min_y >= info->max_y) {
            continue;
        }

        /* Lazy-open dataset handle */
        if (!ds_handles[i]) {
            ds_handles[i] = GDALOpen(info->path, GA_ReadOnly);
            if (!ds_handles[i]) continue;
        }

        if (!has_data) {
            /* First contributing dataset: read directly into tile_data */
            has_data = read_dataset_into_tile(ds_handles[i], info, tile_min_x, tile_min_y, tile_max_x, tile_max_y,
                                              resample_alg, tile_data);
        } else {
            /* Subsequent datasets: read into blend_buf, then composite */
            memset(blend_buf, 0, TILE_SIZE * TILE_SIZE * 4);

            if (read_dataset_into_tile(ds_handles[i], info, tile_min_x, tile_min_y, tile_max_x, tile_max_y,
                                       resample_alg, blend_buf)) {
                /* Composite: overwrite tile_data wherever blend_buf has alpha > 0 */
                for (size_t px = 0; px < TILE_SIZE * TILE_SIZE * 4; px += 4) {
                    if (blend_buf[px + 3] > 0) {
                        memcpy(&tile_data[px], &blend_buf[px], 4);
                    }
                }
            }
        }
    }

    if (!has_data) {
        return 1; /* No data for this tile */
    }

    /* Check if tile is empty (all transparent) */
    int is_empty = 1;
    for (size_t i = 3; i < TILE_SIZE * TILE_SIZE * 4; i += 4) {
        if (tile_data[i] != 0) {
            is_empty = 0;
            break;
        }
    }

    if (is_empty) {
        return 1; /* Skip empty tile */
    }

    /* Create output directory */
    char dir_path[PATH_SIZE];
    snprintf(dir_path, sizeof(dir_path), "%s/%s/%d/%d", outpath, tile_path, z, x);
    if (mkdir_p(dir_path) != 0) {
        error("Failed to create directory: %s", dir_path);
        return -1;
    }

    /* Encode tile directly via libpng/libjpeg/libwebp */
    if (encoder(tile_data, file_path) != 0) {
        error("Failed to encode tile: %s", file_path);
        return -1;
    }

    return 0;
}

/* ============================================================================
 * Parallel Tile Generation
 * ============================================================================ */

/* Maximum parallel workers for tile generation */
#define MAX_TILE_WORKERS 64

int generate_tileset_tiles_parallel(const Tileset **tilesets, int tileset_count, const char *tmppath,
                                    const char *outpath, const char *format, const char *resampling, int num_workers) {
    /* Initialize GDAL in parent (needed for manifest and dataset info collection) */
    GDALAllRegister();

    info("\nGenerating tiles...");

    /* Resolve tile encoder once before any workers */
    tile_encode_fn encoder = tile_encode_get(format);
    if (!encoder) {
        error("Unknown tile format: %s", format);
        return -1;
    }

    GDALRIOResampleAlg resample_alg = parse_resampling(resampling);

    for (int t = 0; t < tileset_count; t++) {
        const Tileset *tileset = tilesets[t];

        info("\n=== Tiles: %s ===", tileset->name);

        /* Collect dataset metadata for direct GeoTIFF reading */
        TileDatasetInfo *ds_infos = NULL;
        int ds_count = 0;
        if (collect_dataset_infos(tileset, tmppath, &ds_infos, &ds_count) != 0) {
            error("Failed to collect dataset info for tileset: %s", tileset->name);
            return -1;
        }

        if (ds_count == 0) {
            info("  No datasets available");
            free(ds_infos);
            continue;
        }

        /* Build tile manifest based on dataset coverage and max_lod */
        TileManifest *manifest = build_tile_manifest(tileset, tmppath);
        if (!manifest) {
            error("Failed to build tile manifest for tileset: %s", tileset->name);
            free(ds_infos);
            return -1;
        }

        int zoom_min = manifest->min_zoom;
        int zoom_max = manifest->max_zoom;

        /* Count total tiles across all zoom levels */
        int total_tiles = 0;

        for (int z = zoom_min; z <= zoom_max; z++) {
            ZoomTileSet *zts = &manifest->zooms[z - zoom_min];
            if (zts->count == 0) continue;
            total_tiles += zts->count;
            info("    Zoom %d: %d tiles", z, zts->count);
        }

        if (total_tiles == 0) {
            info("  No tiles to generate");
            free_tile_manifest(manifest);
            free(ds_infos);
            continue;
        }

        info("  Total: %d tiles across %d zoom levels (%d datasets)", total_tiles, zoom_max - zoom_min + 1, ds_count);

        /* Collect all tiles from all zoom levels into a single list */
        TileCoord *tiles = malloc(total_tiles * sizeof(TileCoord));
        if (!tiles) {
            error("Failed to allocate tile list");
            free_tile_manifest(manifest);
            free(ds_infos);
            return -1;
        }

        int tile_idx = 0;
        for (int z = zoom_min; z <= zoom_max; z++) {
            ZoomTileSet *zts = &manifest->zooms[z - zoom_min];
            for (int i = 0; i < zts->count; i++) {
                PackedTile pt = zts->tiles[i];
                tiles[tile_idx].z = z;
                tiles[tile_idx].x = (int)((pt >> 16) & 0xFFFF);
                tiles[tile_idx].y = (int)(pt & 0xFFFF);
                tile_idx++;
            }
        }

        /* Limit workers to number of tiles and max workers */
        int actual_workers = num_workers;
        if (actual_workers > MAX_TILE_WORKERS) {
            actual_workers = MAX_TILE_WORKERS;
        }
        if (actual_workers > total_tiles) {
            actual_workers = total_tiles;
        }

        /* Create shared atomic counter for dynamic work distribution */
        void *map_result = mmap(NULL, sizeof(atomic_int), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
        if (map_result == MAP_FAILED) { // NOLINT(performance-no-int-to-ptr)
            error("Failed to create shared memory for tile counter");
            free(tiles);
            free_tile_manifest(manifest);
            free(ds_infos);
            return -1;
        }
        atomic_int *next_tile = (atomic_int *)map_result;
        atomic_store(next_tile, 0);

        /* Fork worker processes - single parallel phase for all zoom levels.
         * Each worker inherits ds_infos (read-only) via fork() and maintains
         * its own lazily-opened dataset handles. */
        pid_t pids[MAX_TILE_WORKERS];

        for (int w = 0; w < actual_workers; w++) {
            pid_t pid = fork();
            if (pid < 0) {
                error("Failed to fork worker %d", w);
                for (int i = 0; i < w; i++) {
                    kill(pids[i], SIGTERM);
                }
                munmap(next_tile, sizeof(atomic_int));
                free(tiles);
                free_tile_manifest(manifest);
                free(ds_infos);
                return -1;
            }

            if (pid == 0) {
                /* Child process - initialize GDAL and limit cache */
                GDALAllRegister();
                GDALSetCacheMax64(32LL * 1024 * 1024); /* 32 MB absolute limit */
                CPLSetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR");

                /* Per-worker dataset handle cache (opened lazily on first use) */
                GDALDatasetH *ds_handles = calloc(ds_count, sizeof(GDALDatasetH));

                /* Allocate tile buffer and blend buffer, reused across all tiles */
                unsigned char *tile_data = malloc(TILE_SIZE * TILE_SIZE * 4);
                unsigned char *blend_buf = malloc(TILE_SIZE * TILE_SIZE * 4);
                if (!ds_handles || !tile_data || !blend_buf) {
                    error("Worker %d: Failed to allocate buffers", w);
                    free(ds_handles);
                    free(tile_data);
                    free(blend_buf);
                    _exit(1);
                }

                /* Dynamic work distribution: grab tiles until none remain */
                while (1) {
                    int i = atomic_fetch_add(next_tile, 1);
                    if (i >= total_tiles) break;

                    generate_base_tile(ds_infos, ds_count, ds_handles, tiles[i].z, tiles[i].x, tiles[i].y, outpath,
                                       tileset->tile_path, format, encoder, resample_alg, tile_data, blend_buf);
                }

                /* Close all cached dataset handles */
                for (int j = 0; j < ds_count; j++) {
                    if (ds_handles[j]) GDALClose(ds_handles[j]);
                }
                free(ds_handles);
                free(tile_data);
                free(blend_buf);

                _exit(0);
            }

            pids[w] = pid;
        }

        /* Wait for all workers */
        int failed = 0;
        for (int w = 0; w < actual_workers; w++) {
            int status;
            waitpid(pids[w], &status, 0);
            if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
                error("Worker %d failed", w);
                failed = 1;
            }
        }

        munmap(next_tile, sizeof(atomic_int));
        free(tiles);
        free_tile_manifest(manifest);
        free(ds_infos);

        if (failed) {
            return -1;
        }

        info("  Tile generation complete");
    }

    return 0;
}
