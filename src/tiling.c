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
#include <dirent.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/mman.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <stdatomic.h>
#include <gdal.h>

#include "aeronav.h"
#include "manifest.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Convert resampling string to GDAL enum */
static GDALRIOResampleAlg parse_resampling(const char *resampling) {
    if (strcmp(resampling, "nearest") == 0) return GRIORA_NearestNeighbour;
    if (strcmp(resampling, "bilinear") == 0) return GRIORA_Bilinear;
    if (strcmp(resampling, "cubic") == 0) return GRIORA_Cubic;
    if (strcmp(resampling, "cubicspline") == 0) return GRIORA_CubicSpline;
    if (strcmp(resampling, "lanczos") == 0) return GRIORA_Lanczos;
    if (strcmp(resampling, "average") == 0) return GRIORA_Average;
    if (strcmp(resampling, "mode") == 0) return GRIORA_Mode;
    return GRIORA_Bilinear;  /* Default */
}

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
 * Tile Generation from Source Raster
 * ============================================================================ */

static int generate_base_tile(GDALDatasetH ds,
                         int z, int x, int y,
                         const char *outpath,
                         const char *tile_path,
                         const char *format,
                         GDALRIOResampleAlg resample_alg) {
    /* Skip if tile already exists */
    char file_path[PATH_SIZE];
    snprintf(file_path, sizeof(file_path), "%s/%s/%d/%d/%d.%s", outpath, tile_path, z, x, y, format);
    struct stat st;
    if (stat(file_path, &st) == 0) {
        return 2;  /* Skipped - already exists */
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
     * GDALRasterIOEx resamples from (read_x, read_y, read_w, read_h) in source
     * to (tile_w, tile_h) in the output buffer using the specified resampling.
     */
    unsigned char band_buf[TILE_SIZE * TILE_SIZE];
    GDALRasterIOExtraArg extra_arg;
    INIT_RASTERIO_EXTRA_ARG(extra_arg);
    extra_arg.eResampleAlg = resample_alg;

    for (int b = 0; b < 4; b++) {
        int src_band = (b < 3) ? b + 1 : (band_count >= 4 ? 4 : 0);

        if (src_band > 0) {
            GDALRasterBandH band = GDALGetRasterBand(ds, src_band);
            if (GDALRasterIOEx(band, GF_Read, read_x, read_y, read_w, read_h,
                               band_buf, tile_w, tile_h, GDT_Byte, 0, 0, &extra_arg) != CE_None) {
                error("GDALRasterIOEx read failed for band %d", src_band);
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
    char dir_path[PATH_SIZE];
    snprintf(dir_path, sizeof(dir_path), "%s/%s/%d/%d", outpath, tile_path, z, x);
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
 * Overview Tile Generation (from child tiles)
 * ============================================================================ */

/*
 * Generate an overview tile by combining 4 child tiles.
 *
 * Reads child tiles at zoom+1, composites them into a 2x image,
 * then downsamples to TILE_SIZE. This is much faster than reading
 * from the source raster for low zoom levels.
 *
 * Returns: 0 on success, 1 if skipped (no children), -1 on error.
 */
static int generate_overview_tile(int z, int x, int y,
                                   const char *outpath,
                                   const char *tile_path,
                                   const char *format,
                                   GDALRIOResampleAlg resample_alg) {
    char file_path[PATH_SIZE];
    snprintf(file_path, sizeof(file_path), "%s/%s/%d/%d/%d.%s", outpath, tile_path, z, x, y, format);

    /* Always skip if tile already exists.
     * Base tiles at various zoom levels may have been generated from the VRT
     * in Phase 1 (for datasets with different max_lod). We don't want overview
     * generation to overwrite those with downsampled versions. */
    struct stat st;
    if (stat(file_path, &st) == 0) {
        return 2;  /* Skipped - already exists */
    }

    /* Child tile coordinates at zoom+1 */
    int child_zoom = z + 1;
    /* In XYZ scheme: children are at (2*x, 2*y), (2*x+1, 2*y), (2*x, 2*y+1), (2*x+1, 2*y+1) */
    int child_coords[4][2] = {
        {x * 2,     y * 2},      /* top-left */
        {x * 2 + 1, y * 2},      /* top-right */
        {x * 2,     y * 2 + 1},  /* bottom-left */
        {x * 2 + 1, y * 2 + 1},  /* bottom-right */
    };

    /* Quadrant positions in the 2x composite image
     * (qx, qy) where qx is column (0=left, 1=right), qy is row (0=top, 1=bottom)
     */
    int quadrant_pos[4][2] = {
        {0, 0},  /* top-left child -> left column, top row */
        {1, 0},  /* top-right child -> right column, top row */
        {0, 1},  /* bottom-left child -> left column, bottom row */
        {1, 1},  /* bottom-right child -> right column, bottom row */
    };

    /* Allocate 2x composite image (RGBA) */
    int composite_size = TILE_SIZE * 2;
    unsigned char *composite = calloc(composite_size * composite_size * 4, 1);
    if (!composite) {
        error("Failed to allocate composite buffer");
        return -1;
    }

    /* Load each child tile into its quadrant */
    int has_any_tile = 0;
    for (int i = 0; i < 4; i++) {
        char child_path[PATH_SIZE];
        snprintf(child_path, sizeof(child_path), "%s/%s/%d/%d/%d.%s",
                 outpath, tile_path, child_zoom, child_coords[i][0], child_coords[i][1], format);

        struct stat st;
        if (stat(child_path, &st) != 0) {
            continue;  /* Child tile doesn't exist */
        }

        GDALDatasetH child_ds = GDALOpen(child_path, GA_ReadOnly);
        if (!child_ds) {
            continue;  /* Could not open child tile */
        }

        has_any_tile = 1;

        int child_bands = GDALGetRasterCount(child_ds);
        int qx = quadrant_pos[i][0];
        int qy = quadrant_pos[i][1];
        int x_off = qx * TILE_SIZE;
        int y_off = qy * TILE_SIZE;

        /* Read each band and copy to composite */
        unsigned char band_buf[TILE_SIZE * TILE_SIZE];
        for (int b = 0; b < 4; b++) {
            int src_band = (b < child_bands) ? b + 1 : 0;

            if (src_band > 0) {
                GDALRasterBandH band = GDALGetRasterBand(child_ds, src_band);
                if (GDALRasterIO(band, GF_Read, 0, 0, TILE_SIZE, TILE_SIZE,
                                 band_buf, TILE_SIZE, TILE_SIZE, GDT_Byte, 0, 0) == CE_None) {
                    /* Copy to composite quadrant */
                    for (int py = 0; py < TILE_SIZE; py++) {
                        for (int px = 0; px < TILE_SIZE; px++) {
                            int comp_idx = ((y_off + py) * composite_size + (x_off + px)) * 4 + b;
                            composite[comp_idx] = band_buf[py * TILE_SIZE + px];
                        }
                    }
                }
            } else if (b == 3 && child_bands == 3) {
                /* RGB source - set full opacity in alpha quadrant */
                for (int py = 0; py < TILE_SIZE; py++) {
                    for (int px = 0; px < TILE_SIZE; px++) {
                        int comp_idx = ((y_off + py) * composite_size + (x_off + px)) * 4 + 3;
                        composite[comp_idx] = 255;
                    }
                }
            }
        }

        GDALClose(child_ds);
    }

    /* Skip if no child tiles exist */
    if (!has_any_tile) {
        free(composite);
        return 1;
    }

    /* Create MEM dataset for the composite */
    GDALDriverH mem_driver = GDALGetDriverByName("MEM");
    GDALDatasetH composite_ds = GDALCreate(mem_driver, "", composite_size, composite_size, 4, GDT_Byte, NULL);
    if (!composite_ds) {
        error("Failed to create composite MEM dataset");
        free(composite);
        return -1;
    }

    /* Write composite data to MEM dataset */
    unsigned char *band_data = malloc(composite_size * composite_size);
    if (!band_data) {
        error("Failed to allocate band buffer");
        GDALClose(composite_ds);
        free(composite);
        return -1;
    }

    for (int b = 0; b < 4; b++) {
        for (int i = 0; i < composite_size * composite_size; i++) {
            band_data[i] = composite[i * 4 + b];
        }
        GDALRasterBandH band = GDALGetRasterBand(composite_ds, b + 1);
        if (GDALRasterIO(band, GF_Write, 0, 0, composite_size, composite_size,
                         band_data, composite_size, composite_size, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to write composite band %d", b + 1);
            free(band_data);
            GDALClose(composite_ds);
            free(composite);
            return -1;
        }
    }
    free(band_data);
    free(composite);

    /* Create output tile by resampling composite down to TILE_SIZE */
    GDALDatasetH tile_ds = GDALCreate(mem_driver, "", TILE_SIZE, TILE_SIZE, 4, GDT_Byte, NULL);
    if (!tile_ds) {
        error("Failed to create tile MEM dataset");
        GDALClose(composite_ds);
        return -1;
    }

    /* Read from composite with resampling */
    unsigned char tile_buf[TILE_SIZE * TILE_SIZE];
    GDALRasterIOExtraArg extra_arg;
    INIT_RASTERIO_EXTRA_ARG(extra_arg);
    extra_arg.eResampleAlg = resample_alg;

    GDALColorInterp interp[] = { GCI_RedBand, GCI_GreenBand, GCI_BlueBand, GCI_AlphaBand };

    for (int b = 0; b < 4; b++) {
        GDALRasterBandH src_band = GDALGetRasterBand(composite_ds, b + 1);
        if (GDALRasterIOEx(src_band, GF_Read, 0, 0, composite_size, composite_size,
                           tile_buf, TILE_SIZE, TILE_SIZE, GDT_Byte, 0, 0, &extra_arg) != CE_None) {
            error("Failed to resample composite band %d", b + 1);
            GDALClose(tile_ds);
            GDALClose(composite_ds);
            return -1;
        }

        GDALRasterBandH dst_band = GDALGetRasterBand(tile_ds, b + 1);
        if (GDALRasterIO(dst_band, GF_Write, 0, 0, TILE_SIZE, TILE_SIZE,
                         tile_buf, TILE_SIZE, TILE_SIZE, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to write tile band %d", b + 1);
            GDALClose(tile_ds);
            GDALClose(composite_ds);
            return -1;
        }
        GDALSetRasterColorInterpretation(dst_band, interp[b]);
    }

    GDALClose(composite_ds);

    /* Check if tile is empty (all transparent) */
    GDALRasterBandH alpha_band = GDALGetRasterBand(tile_ds, 4);
    if (GDALRasterIO(alpha_band, GF_Read, 0, 0, TILE_SIZE, TILE_SIZE,
                     tile_buf, TILE_SIZE, TILE_SIZE, GDT_Byte, 0, 0) != CE_None) {
        error("Failed to read alpha band for empty check");
        GDALClose(tile_ds);
        return -1;
    }

    int is_empty = 1;
    for (int i = 0; i < TILE_SIZE * TILE_SIZE; i++) {
        if (tile_buf[i] != 0) {
            is_empty = 0;
            break;
        }
    }

    if (is_empty) {
        GDALClose(tile_ds);
        return 1;  /* Skip empty tile */
    }

    /* Create output directory */
    char dir_path[PATH_SIZE];
    snprintf(dir_path, sizeof(dir_path), "%s/%s/%d/%d", outpath, tile_path, z, x);
    if (mkdir_p(dir_path) != 0) {
        error("Failed to create directory: %s", dir_path);
        GDALClose(tile_ds);
        return -1;
    }

    /* Write output file */
    GDALDriverH out_driver = GDALGetDriverByName(format);
    if (!out_driver) {
        error("%s driver not available", format);
        GDALClose(tile_ds);
        return -1;
    }

    GDALDatasetH out_ds = GDALCreateCopy(out_driver, file_path, tile_ds, FALSE, NULL, NULL, NULL);
    if (!out_ds) {
        error("Failed to write overview tile: %s", file_path);
        GDALClose(tile_ds);
        return -1;
    }

    GDALClose(out_ds);
    GDALClose(tile_ds);

    return 0;
}

/* ============================================================================
 * Parallel Tile Generation
 * ============================================================================ */

/*
 * Build a list of base tiles to generate from all zoom levels in the manifest.
 *
 * The manifest contains tiles at each dataset's max_lod level. Base tiles are
 * generated from the VRT at each of these zoom levels. Overview tiles at lower
 * zooms are then generated by combining child tiles.
 *
 * Returns the number of tiles, or -1 on error.
 * If tiles is not NULL, it will be allocated and filled with tile coordinates.
 */
static int get_base_tile_list(const TileManifest *manifest, TileCoord **tiles) {
    if (!manifest) {
        error("Manifest required for base tile list");
        return -1;
    }

    /* Count total tiles across all zoom levels */
    int total = 0;
    for (int z = manifest->min_zoom; z <= manifest->max_zoom; z++) {
        ZoomTileSet *zts = &manifest->zooms[z - manifest->min_zoom];
        total += zts->count;
    }

    if (tiles == NULL) {
        return total;
    }

    /* Allocate tile list */
    *tiles = malloc(total * sizeof(TileCoord));
    if (!*tiles) {
        error("Failed to allocate tile list (%d tiles)", total);
        return -1;
    }

    /* Extract tiles from all zoom levels */
    int idx = 0;
    for (int z = manifest->min_zoom; z <= manifest->max_zoom; z++) {
        ZoomTileSet *zts = &manifest->zooms[z - manifest->min_zoom];
        for (int i = 0; i < zts->count; i++) {
            PackedTile pt = zts->tiles[i];
            (*tiles)[idx].z = z;
            (*tiles)[idx].x = (pt >> 16) & 0xFFFF;
            (*tiles)[idx].y = pt & 0xFFFF;
            idx++;
        }
    }

    return total;
}

/*
 * Generate overview tiles for a zoom level by scanning child tiles.
 *
 * Finds all parent tiles that have children at zoom+1, and generates
 * overview tiles for them.
 *
 * Returns 0 on success, -1 on error.
 */
static int generate_overview_tiles_for_zoom(
    int zoom,
    const char *outpath,
    const char *tile_path,
    const char *format,
    GDALRIOResampleAlg resample_alg
) {
    int child_zoom = zoom + 1;

    /* Build path to child zoom directory */
    char child_dir[PATH_SIZE];
    snprintf(child_dir, sizeof(child_dir), "%s/%s/%d", outpath, tile_path, child_zoom);

    /* Check if child directory exists */
    struct stat st;
    if (stat(child_dir, &st) != 0 || !S_ISDIR(st.st_mode)) {
        return 0;  /* No child tiles */
    }

    /* Scan child directories to find unique parent tiles */
    /* Use a simple dynamic array for parent tile coordinates */
    int parent_capacity = 1024;
    int parent_count = 0;
    TileCoord *parent_tiles = malloc(parent_capacity * sizeof(TileCoord));
    if (!parent_tiles) {
        error("Failed to allocate parent tiles array");
        return -1;
    }

    DIR *x_dir = opendir(child_dir);
    if (!x_dir) {
        free(parent_tiles);
        return 0;  /* No child tiles */
    }

    struct dirent *x_entry;
    while ((x_entry = readdir(x_dir)) != NULL) {
        if (x_entry->d_name[0] == '.') continue;

        char *endptr;
        long child_x = strtol(x_entry->d_name, &endptr, 10);
        if (*endptr != '\0') continue;

        char x_path[PATH_SIZE];
        snprintf(x_path, sizeof(x_path), "%s/%s", child_dir, x_entry->d_name);

        if (stat(x_path, &st) != 0 || !S_ISDIR(st.st_mode)) continue;

        DIR *y_dir = opendir(x_path);
        if (!y_dir) continue;

        struct dirent *y_entry;
        while ((y_entry = readdir(y_dir)) != NULL) {
            if (y_entry->d_name[0] == '.') continue;

            /* Extract Y from filename (e.g., "123.webp") */
            char *dot = strrchr(y_entry->d_name, '.');
            if (!dot) continue;

            char y_str[32];
            size_t y_len = dot - y_entry->d_name;
            if (y_len >= sizeof(y_str)) continue;
            strncpy(y_str, y_entry->d_name, y_len);
            y_str[y_len] = '\0';

            long child_y = strtol(y_str, &endptr, 10);
            if (*endptr != '\0') continue;

            /* Calculate parent tile coords (XYZ scheme) */
            int parent_x = (int)(child_x / 2);
            int parent_y = (int)(child_y / 2);

            /* Check if parent already in list */
            int found = 0;
            for (int i = 0; i < parent_count; i++) {
                if (parent_tiles[i].x == parent_x && parent_tiles[i].y == parent_y) {
                    found = 1;
                    break;
                }
            }

            if (!found) {
                if (parent_count >= parent_capacity) {
                    parent_capacity *= 2;
                    TileCoord *new_tiles = realloc(parent_tiles, parent_capacity * sizeof(TileCoord));
                    if (!new_tiles) {
                        error("Failed to expand parent tiles array");
                        closedir(y_dir);
                        closedir(x_dir);
                        free(parent_tiles);
                        return -1;
                    }
                    parent_tiles = new_tiles;
                }
                parent_tiles[parent_count].z = zoom;
                parent_tiles[parent_count].x = parent_x;
                parent_tiles[parent_count].y = parent_y;
                parent_count++;
            }
        }
        closedir(y_dir);
    }
    closedir(x_dir);

    /* Generate overview tiles */
    int generated = 0;
    int skipped = 0;
    int existing = 0;

    for (int i = 0; i < parent_count; i++) {
        int result = generate_overview_tile(
            parent_tiles[i].z, parent_tiles[i].x, parent_tiles[i].y,
            outpath, tile_path, format, resample_alg
        );
        if (result == 0) {
            generated++;
        } else if (result == 1) {
            skipped++;
        } else if (result == 2) {
            existing++;
        }
    }

    free(parent_tiles);

    if (existing > 0) {
        info("    Zoom %d: %d generated, %d skipped, %d existing (base tiles)",
             zoom, generated, skipped, existing);
    } else {
        info("    Zoom %d: %d generated, %d skipped", zoom, generated, skipped);
    }

    return 0;
}

int generate_tileset_tiles_parallel(
    const Tileset **tilesets,
    int tileset_count,
    const char *tmppath,
    const char *outpath,
    const char *format,
    const char *resampling,
    int num_workers
) {
    info("\nGenerating tiles...");

    GDALRIOResampleAlg resample_alg = parse_resampling(resampling);

    for (int t = 0; t < tileset_count; t++) {
        const Tileset *tileset = tilesets[t];

        char vrt_path[PATH_SIZE];
        snprintf(vrt_path, sizeof(vrt_path), "%s/__%s.vrt", tmppath, tileset->name);

        info("\n=== Tiles: %s ===", tileset->name);

        /* Build tile manifest based on dataset coverage and max_lod */
        TileManifest *manifest = build_tile_manifest(tileset, tmppath);
        if (!manifest) {
            error("Failed to build tile manifest for tileset: %s", tileset->name);
            return -1;
        }

        int zoom_min = manifest->min_zoom;
        int zoom_max = manifest->max_zoom;

        /* ================================================================
         * Phase 1: Generate base tiles at each dataset's max_lod level
         * ================================================================ */
        info("  Phase 1: Base tiles (zoom %d to %d)", zoom_min, zoom_max);

        /* Get list of base tiles to generate (all zoom levels with entries) */
        TileCoord *tiles = NULL;
        int total_tiles = get_base_tile_list(manifest, &tiles);
        free_tile_manifest(manifest);

        if (total_tiles < 0 || !tiles) {
            error("Failed to build tile list for tileset: %s", tileset->name);
            return -1;
        }

        info("    Generating %d base tiles with %d workers", total_tiles, num_workers);

        if (total_tiles > 0) {
            /* Limit workers to number of tiles */
            int actual_workers = num_workers;
            if (actual_workers > total_tiles) {
                actual_workers = total_tiles;
            }

            /* Create shared atomic counter for dynamic work distribution */
            atomic_int *next_tile = mmap(NULL, sizeof(atomic_int),
                                         PROT_READ | PROT_WRITE,
                                         MAP_SHARED | MAP_ANONYMOUS, -1, 0);
            if (next_tile == MAP_FAILED) {
                error("Failed to create shared memory for tile counter");
                free(tiles);
                return -1;
            }
            atomic_store(next_tile, 0);

            /* Fork worker processes */
            pid_t pids[MAX_JOBS];

            for (int w = 0; w < actual_workers; w++) {
                pid_t pid = fork();
                if (pid < 0) {
                    error("Failed to fork worker %d", w);
                    /* Kill already-started workers */
                    for (int i = 0; i < w; i++) {
                        kill(pids[i], SIGTERM);
                    }
                    munmap(next_tile, sizeof(atomic_int));
                    free(tiles);
                    return -1;
                }

                if (pid == 0) {
                    /* Child process - open own dataset handle */
                    GDALDatasetH worker_ds = GDALOpen(vrt_path, GA_ReadOnly);
                    if (!worker_ds) {
                        error("Worker %d: Failed to open dataset", w);
                        _exit(1);
                    }

                    int generated = 0;
                    int skipped = 0;
                    int existing = 0;

                    /* Dynamic work distribution: grab tiles until none remain */
                    while (1) {
                        int i = atomic_fetch_add(next_tile, 1);
                        if (i >= total_tiles) break;

                        int result = generate_base_tile(worker_ds, tiles[i].z, tiles[i].x, tiles[i].y,
                                                   outpath, tileset->tile_path, format,
                                                   resample_alg);
                        if (result == 0) {
                            generated++;
                        } else if (result == 1) {
                            skipped++;
                        } else if (result == 2) {
                            existing++;
                        }
                    }

                    GDALClose(worker_ds);

                    if (existing > 0) {
                        info("    Worker %d: %d generated, %d skipped, %d existing",
                             w, generated, skipped, existing);
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
            for (int w = 0; w < actual_workers; w++) {
                int status;
                waitpid(pids[w], &status, 0);
                if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
                    error("Worker %d failed", w);
                    munmap(next_tile, sizeof(atomic_int));
                    free(tiles);
                    return -1;
                }
            }

            munmap(next_tile, sizeof(atomic_int));
        }

        free(tiles);

        /* ================================================================
         * Phase 2: Generate overview tiles from child tiles
         * ================================================================ */
        if (zoom_max > zoom_min) {
            info("  Phase 2: Overview tiles (zoom %d to %d)", zoom_max - 1, zoom_min);

            for (int z = zoom_max - 1; z >= zoom_min; z--) {
                if (generate_overview_tiles_for_zoom(z, outpath, tileset->tile_path, format,
                                                     resample_alg) != 0) {
                    error("Failed to generate overview tiles at zoom %d", z);
                    return -1;
                }
            }
        }

        info("  Tile generation complete");
    }

    return 0;
}
