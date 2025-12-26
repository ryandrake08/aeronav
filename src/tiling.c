/*
 * aeronav2tiles - Tile generation
 *
 * Generates XYZ web map tiles from a processed raster dataset.
 * Uses the GlobalMercator scheme (EPSG:3857).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <gdal.h>

#include "aeronav.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ============================================================================
 * GlobalMercator Calculations
 * ============================================================================ */

double resolution_for_zoom(int zoom) {
    /* Resolution in meters/pixel at given zoom level */
    /* At zoom 0, the world is 256 pixels, at zoom n, it's 256 * 2^n pixels */
    /* World circumference at equator = 2 * pi * 6378137 meters */
    double world_size = 2 * ORIGIN_SHIFT;  /* Full extent in meters */
    double tile_count = pow(2, zoom);
    return world_size / (tile_count * TILE_SIZE);
}

static void tile_bounds(int z, int x, int y,
                        double *min_x, double *min_y,
                        double *max_x, double *max_y) {
    /* Get the bounds of a tile in EPSG:3857 coordinates */
    double res = resolution_for_zoom(z) * TILE_SIZE;  /* Tile size in meters */

    *min_x = -ORIGIN_SHIFT + x * res;
    *max_x = -ORIGIN_SHIFT + (x + 1) * res;

    /* Y is flipped in TMS vs XYZ. We use XYZ (origin at top-left) */
    int max_tile = (1 << z) - 1;
    int tms_y = max_tile - y;  /* Convert XYZ y to TMS y */

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
 * Tile Generation
 * ============================================================================ */

static int generate_tile(GDALDatasetH ds,
                         int z, int x, int y,
                         const char *outpath,
                         const char *tile_path,
                         const char *format,
                         bool resume) {
    /* Build output path early for resume check */
    char dir_path[PATH_SIZE];
    char file_path[PATH_SIZE];
    snprintf(dir_path, sizeof(dir_path), "%s/%s/%d/%d", outpath, tile_path, z, x);
    snprintf(file_path, sizeof(file_path), "%s/%d.%s", dir_path, y, format);

    /* Resume mode: skip if tile already exists */
    if (resume) {
        struct stat st;
        if (stat(file_path, &st) == 0) {
            return 2;  /* Skipped - already exists */
        }
    }

    /* Get tile bounds in EPSG:3857 */
    double tile_min_x, tile_min_y, tile_max_x, tile_max_y;
    tile_bounds(z, x, y, &tile_min_x, &tile_min_y, &tile_max_x, &tile_max_y);

    /* Get dataset bounds and geotransform */
    double gt[6];
    if (GDALGetGeoTransform(ds, gt) != CE_None) {
        error("Failed to get geotransform for tile %d/%d/%d", z, x, y);
        return -1;
    }

    int ds_width = GDALGetRasterXSize(ds);
    int ds_height = GDALGetRasterYSize(ds);

    double ds_min_x = gt[0];
    double ds_max_x = gt[0] + ds_width * gt[1];
    double ds_max_y = gt[3];
    double ds_min_y = gt[3] + ds_height * gt[5];  /* gt[5] is negative */

    /* Check if tile intersects dataset */
    if (tile_max_x <= ds_min_x || tile_min_x >= ds_max_x ||
        tile_max_y <= ds_min_y || tile_min_y >= ds_max_y) {
        return 1;  /* Skip - no intersection */
    }

    /* Calculate pixel coordinates in source dataset */
    double src_x0 = (tile_min_x - gt[0]) / gt[1];
    double src_y0 = (tile_max_y - gt[3]) / gt[5];  /* Note: gt[5] is negative */
    double src_x1 = (tile_max_x - gt[0]) / gt[1];
    double src_y1 = (tile_min_y - gt[3]) / gt[5];

    /* Clamp to dataset bounds */
    if (src_x0 < 0) src_x0 = 0;
    if (src_y0 < 0) src_y0 = 0;
    if (src_x1 > ds_width) src_x1 = ds_width;
    if (src_y1 > ds_height) src_y1 = ds_height;

    int read_x = (int)src_x0;
    int read_y = (int)src_y0;
    int read_w = (int)(src_x1 - src_x0 + 0.5);
    int read_h = (int)(src_y1 - src_y0 + 0.5);

    if (read_w <= 0 || read_h <= 0) {
        return 1;  /* Skip - empty read region */
    }

    /* Calculate where in the tile this data goes */
    int tile_x0 = 0, tile_y0 = 0, tile_w = TILE_SIZE, tile_h = TILE_SIZE;

    /* If source doesn't cover full tile, adjust */
    if (tile_min_x < ds_min_x) {
        tile_x0 = (int)((ds_min_x - tile_min_x) / (tile_max_x - tile_min_x) * TILE_SIZE);
        tile_w = TILE_SIZE - tile_x0;
    }
    if (tile_max_x > ds_max_x) {
        tile_w = (int)((ds_max_x - tile_min_x) / (tile_max_x - tile_min_x) * TILE_SIZE) - tile_x0;
    }
    if (tile_max_y > ds_max_y) {
        tile_y0 = (int)((tile_max_y - ds_max_y) / (tile_max_y - tile_min_y) * TILE_SIZE);
        tile_h = TILE_SIZE - tile_y0;
    }
    if (tile_min_y < ds_min_y) {
        tile_h = (int)((tile_max_y - ds_min_y) / (tile_max_y - tile_min_y) * TILE_SIZE) - tile_y0;
    }

    if (tile_w <= 0 || tile_h <= 0) {
        return 1;  /* Skip */
    }

    /* Allocate tile buffer */
    int band_count = GDALGetRasterCount(ds);
    if (band_count < 3) {
        error("Expected at least 3 bands, got %d", band_count);
        return -1;
    }

    /* Create RGBA tile buffer */
    unsigned char *tile_data = calloc(TILE_SIZE * TILE_SIZE * 4, 1);
    if (!tile_data) {
        error("Failed to allocate tile buffer");
        return -1;
    }

    /* Read and resample each band.
     * GDALRasterIO resamples from (read_x, read_y, read_w, read_h) in source
     * to (tile_w, tile_h) in the output buffer.
     */
    unsigned char band_buf[TILE_SIZE * TILE_SIZE];

    for (int b = 0; b < 4; b++) {
        int src_band = (b < 3) ? b + 1 : (band_count >= 4 ? 4 : 0);

        if (src_band > 0) {
            GDALRasterBandH band = GDALGetRasterBand(ds, src_band);
            if (GDALRasterIO(band, GF_Read, read_x, read_y, read_w, read_h,
                             band_buf, tile_w, tile_h, GDT_Byte, 0, 0) != CE_None) {
                error("GDALRasterIO read failed for band %d", src_band);
                free(tile_data);
                return -1;
            }

            /* Copy to tile buffer */
            for (int y_off = 0; y_off < tile_h; y_off++) {
                for (int x_off = 0; x_off < tile_w; x_off++) {
                    int tile_idx = ((tile_y0 + y_off) * TILE_SIZE + (tile_x0 + x_off)) * 4 + b;
                    tile_data[tile_idx] = band_buf[y_off * tile_w + x_off];
                }
            }
        } else if (b == 3) {
            /* No alpha band in source - set opaque where we have data */
            for (int y_off = 0; y_off < tile_h; y_off++) {
                for (int x_off = 0; x_off < tile_w; x_off++) {
                    int tile_idx = ((tile_y0 + y_off) * TILE_SIZE + (tile_x0 + x_off)) * 4 + 3;
                    tile_data[tile_idx] = 255;
                }
            }
        }
    }

    /* Check if tile is empty (all transparent) */
    int is_empty = 1;
    for (int i = 3; i < TILE_SIZE * TILE_SIZE * 4; i += 4) {
        if (tile_data[i] != 0) {
            is_empty = 0;
            break;
        }
    }

    if (is_empty) {
        free(tile_data);
        return 1;  /* Skip empty tile */
    }

    /* Create output directory */
    if (mkdir_p(dir_path) != 0) {
        error("Failed to create directory: %s", dir_path);
        free(tile_data);
        return -1;
    }

    /* Write tile using GDAL */
    GDALDriverH out_driver = GDALGetDriverByName(format);
    if (!out_driver) {
        error("%s driver not available", format);
        free(tile_data);
        return -1;
    }

    /* Create MEM dataset first, then translate to PNG */
    GDALDriverH mem_driver = GDALGetDriverByName("MEM");
    GDALDatasetH mem_ds = GDALCreate(mem_driver, "", TILE_SIZE, TILE_SIZE, 4, GDT_Byte, NULL);
    if (!mem_ds) {
        error("Failed to create MEM dataset for tile");
        free(tile_data);
        return -1;
    }

    /* Write tile data to MEM dataset */
    unsigned char band_data[TILE_SIZE * TILE_SIZE];
    GDALColorInterp interp[] = { GCI_RedBand, GCI_GreenBand, GCI_BlueBand, GCI_AlphaBand };

    for (int b = 0; b < 4; b++) {
        GDALRasterBandH band = GDALGetRasterBand(mem_ds, b + 1);

        for (int i = 0; i < TILE_SIZE * TILE_SIZE; i++) {
            band_data[i] = tile_data[i * 4 + b];
        }

        if (GDALRasterIO(band, GF_Write, 0, 0, TILE_SIZE, TILE_SIZE,
                         band_data, TILE_SIZE, TILE_SIZE, GDT_Byte, 0, 0) != CE_None) {
            error("GDALRasterIO write failed for band %d", b + 1);
            GDALClose(mem_ds);
            free(tile_data);
            return -1;
        }

        GDALSetRasterColorInterpretation(band, interp[b]);
    }

    /* Create output file */
    GDALDatasetH out_ds = GDALCreateCopy(out_driver, file_path, mem_ds, FALSE, NULL, NULL, NULL);
    if (!out_ds) {
        error("Failed to write tile: %s", file_path);
        GDALClose(mem_ds);
        free(tile_data);
        return -1;
    }

    GDALClose(out_ds);
    GDALClose(mem_ds);
    free(tile_data);

    return 0;
}

/* ============================================================================
 * Parallel Tile Generation
 * ============================================================================ */

/*
 * Build a list of all tiles that need to be generated for the given zoom range.
 * Returns the number of tiles, or -1 on error.
 * If tiles is not NULL, it will be allocated and filled with tile coordinates.
 */
static int get_tile_list(GDALDatasetH ds,
                         int zoom_min,
                         int zoom_max,
                         TileCoord **tiles) {
    /* Get dataset bounds */
    double gt[6];
    if (GDALGetGeoTransform(ds, gt) != CE_None) {
        error("Failed to get geotransform for tile list");
        return -1;
    }

    int ds_width = GDALGetRasterXSize(ds);
    int ds_height = GDALGetRasterYSize(ds);

    double ds_min_x = gt[0];
    double ds_max_x = gt[0] + ds_width * gt[1];
    double ds_max_y = gt[3];
    double ds_min_y = gt[3] + ds_height * gt[5];

    /* First pass: count tiles */
    int total_tiles = 0;
    for (int z = zoom_min; z <= zoom_max; z++) {
        double res = resolution_for_zoom(z);

        int tx_min = (int)((ds_min_x + ORIGIN_SHIFT) / (res * TILE_SIZE));
        int tx_max = (int)((ds_max_x + ORIGIN_SHIFT) / (res * TILE_SIZE));
        int ty_min = (int)((ORIGIN_SHIFT - ds_max_y) / (res * TILE_SIZE));
        int ty_max = (int)((ORIGIN_SHIFT - ds_min_y) / (res * TILE_SIZE));

        int max_tile = (1 << z) - 1;
        if (tx_min < 0) tx_min = 0;
        if (ty_min < 0) ty_min = 0;
        if (tx_max > max_tile) tx_max = max_tile;
        if (ty_max > max_tile) ty_max = max_tile;

        total_tiles += (tx_max - tx_min + 1) * (ty_max - ty_min + 1);
    }

    if (tiles == NULL) {
        return total_tiles;
    }

    /* Allocate tile list */
    *tiles = malloc(total_tiles * sizeof(TileCoord));
    if (!*tiles) {
        error("Failed to allocate tile list (%d tiles)", total_tiles);
        return -1;
    }

    /* Second pass: fill tile list */
    int idx = 0;
    for (int z = zoom_min; z <= zoom_max; z++) {
        double res = resolution_for_zoom(z);

        int tx_min = (int)((ds_min_x + ORIGIN_SHIFT) / (res * TILE_SIZE));
        int tx_max = (int)((ds_max_x + ORIGIN_SHIFT) / (res * TILE_SIZE));
        int ty_min = (int)((ORIGIN_SHIFT - ds_max_y) / (res * TILE_SIZE));
        int ty_max = (int)((ORIGIN_SHIFT - ds_min_y) / (res * TILE_SIZE));

        int max_tile = (1 << z) - 1;
        if (tx_min < 0) tx_min = 0;
        if (ty_min < 0) ty_min = 0;
        if (tx_max > max_tile) tx_max = max_tile;
        if (ty_max > max_tile) ty_max = max_tile;

        for (int tx = tx_min; tx <= tx_max; tx++) {
            for (int ty = ty_min; ty <= ty_max; ty++) {
                (*tiles)[idx].z = z;
                (*tiles)[idx].x = tx;
                (*tiles)[idx].y = ty;
                idx++;
            }
        }
    }

    return total_tiles;
}

int generate_tiles(const char *src_path,
                   const char *outpath,
                   const char *tile_path,
                   int zoom_min,
                   int zoom_max,
                   const char *format,
                   int num_workers,
                   bool resume) {
    (void)resume;  /* TODO: implement resume */

    /* Open dataset to get tile list */
    GDALDatasetH ds = GDALOpen(src_path, GA_ReadOnly);
    if (!ds) {
        error("Failed to open dataset: %s", src_path);
        return -1;
    }

    /* Get list of tiles to generate */
    TileCoord *tiles = NULL;
    int total_tiles = get_tile_list(ds, zoom_min, zoom_max, &tiles);
    GDALClose(ds);

    if (total_tiles < 0 || !tiles) {
        error("Failed to build tile list");
        return -1;
    }

    info("  Generating %d tiles with %d workers", total_tiles, num_workers);

    if (total_tiles == 0) {
        free(tiles);
        return 0;
    }

    /* Limit workers to number of tiles */
    if (num_workers > total_tiles) {
        num_workers = total_tiles;
    }

    /* Fork worker processes */
    pid_t pids[MAX_JOBS];
    int tiles_per_worker = (total_tiles + num_workers - 1) / num_workers;

    for (int w = 0; w < num_workers; w++) {
        int start = w * tiles_per_worker;
        int end = start + tiles_per_worker;
        if (end > total_tiles) end = total_tiles;
        if (start >= total_tiles) break;

        pid_t pid = fork();
        if (pid < 0) {
            error("Failed to fork worker %d", w);
            /* Kill already-started workers */
            for (int i = 0; i < w; i++) {
                kill(pids[i], SIGTERM);
            }
            free(tiles);
            return -1;
        }

        if (pid == 0) {
            /* Child process - open own dataset handle */
            GDALDatasetH worker_ds = GDALOpen(src_path, GA_ReadOnly);
            if (!worker_ds) {
                error("Worker %d: Failed to open dataset", w);
                _exit(1);
            }

            int generated = 0;
            int skipped = 0;
            int resumed = 0;

            for (int i = start; i < end; i++) {
                int result = generate_tile(worker_ds, tiles[i].z, tiles[i].x, tiles[i].y,
                                           outpath, tile_path, format, resume);
                if (result == 0) {
                    generated++;
                } else if (result == 1) {
                    skipped++;
                } else if (result == 2) {
                    resumed++;
                }
            }

            GDALClose(worker_ds);

            if (resumed > 0) {
                info("    Worker %d: %d generated, %d skipped, %d existing",
                     w, generated, skipped, resumed);
            } else {
                info("    Worker %d: %d generated, %d skipped", w, generated, skipped);
            }

            free(tiles);
            _exit(0);
        }

        /* Parent - save child PID */
        pids[w] = pid;
    }

    /* Wait for all workers */
    int success = 1;
    for (int w = 0; w < num_workers; w++) {
        if (pids[w] == 0) continue;  /* Worker not started */

        int status;
        waitpid(pids[w], &status, 0);
        if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            error("Worker %d failed", w);
            success = 0;
        }
    }

    free(tiles);

    info("  Tile generation complete");

    return success ? 0 : -1;
}
