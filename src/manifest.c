/*
 * manifest.c - Tile manifest for Strategy 3 tile generation
 *
 * Computes which tiles should be generated based on dataset coverage
 * and max_lod constraints.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <gdal.h>

#include "manifest.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Pack x,y into a single 32-bit value for efficient storage */
static inline PackedTile pack_tile(int x, int y) {
    return ((uint32_t)x << 16) | (uint32_t)y;
}

/* Comparison function for qsort and bsearch */
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
static int add_tiles_for_bounds(ZoomTileSet *zts, double lon_min, double lat_min,
                                 double lon_max, double lat_max, int zoom) {
    /* Clamp to valid ranges */
    if (lon_min < -180) lon_min = -180;
    if (lon_max > 180) lon_max = 180;
    if (lat_min < -85) lat_min = -85;
    if (lat_max > 85) lat_max = 85;

    /* Handle antimeridian crossing */
    if (lon_min > lon_max) {
        /* Split into two ranges */
        if (add_tiles_for_bounds(zts, lon_min, lat_min, 180, lat_max, zoom) < 0)
            return -1;
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
static int bounds_from_tif(const char *filepath,
                            double *lon_min, double *lat_min,
                            double *lon_max, double *lat_max) {
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
    double my_min = gt[3] + height * gt[5];  /* gt[5] is negative */

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

TileManifest *build_tile_manifest(const Tileset *tileset, const char *tmppath) {
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
    for (int d = 0; d < tileset->dataset_count; d++) {
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
         * At each zoom level Z, tiles are generated from a zoom-specific VRT
         * containing datasets where max_lod >= Z. This dataset qualifies for
         * all zoom levels from min_zoom to its max_lod. */
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

bool manifest_contains(const TileManifest *m, int z, int x, int y) {
    if (!m) return true;  /* No manifest = generate all tiles */

    if (z < m->min_zoom || z > m->max_zoom) return false;

    ZoomTileSet *zts = &m->zooms[z - m->min_zoom];
    if (zts->count == 0) return false;

    PackedTile target = pack_tile(x, y);
    return bsearch(&target, zts->tiles, zts->count, sizeof(PackedTile), compare_tiles) != NULL;
}

int manifest_tile_count(const TileManifest *m) {
    if (!m) return 0;

    int total = 0;
    for (int z = m->min_zoom; z <= m->max_zoom; z++) {
        total += m->zooms[z - m->min_zoom].count;
    }
    return total;
}

void free_tile_manifest(TileManifest *m) {
    if (!m) return;

    if (m->zooms) {
        for (int i = 0; i <= m->max_zoom - m->min_zoom; i++) {
            free(m->zooms[i].tiles);
        }
        free(m->zooms);
    }
    free(m);
}
