/*
 * vrt.c - VRT (Virtual Raster) processing
 *
 * Builds Virtual Rasters (VRTs) for tilesets. For zoom-specific VRTs,
 * datasets are ordered by max_lod descending so that smaller max_lod
 * datasets (more appropriate for that zoom level) appear last in the
 * VRT and render on top.
 */

#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "aeronav.h"
#include <gdal.h>
#include <gdal_utils.h>

/* Structure for sorting datasets by max_lod */
typedef struct {
    const char *tmp_file_path;
    int max_lod;
} DatasetSortEntry;

static int build_vrt(const char *outpath, const char **input_files, int file_count) {
    if (!input_files || file_count == 0) {
        error("build_vrt: no input files");
        return -1;
    }

    info("  Building VRT from %d dataset(s)...", file_count);

    GDALBuildVRTOptions *vrt_options = GDALBuildVRTOptionsNew(NULL, NULL);
    if (!vrt_options) {
        error("Failed to create VRT options");
        return -1;
    }

    int error_flag = 0;
    GDALDatasetH vrt = GDALBuildVRT(
        outpath,
        file_count,
        NULL,
        input_files,
        vrt_options,
        &error_flag
    );

    GDALBuildVRTOptionsFree(vrt_options);

    if (!vrt || error_flag) {
        error("Failed to build VRT");
        return -1;
    }

    info("    VRT: %dx%d, %d bands",
         GDALGetRasterXSize(vrt), GDALGetRasterYSize(vrt), GDALGetRasterCount(vrt));

    GDALClose(vrt);
    return 0;
}

int build_tilesets_vrt(const Tileset **tilesets, int tileset_count, const char *tmppath) {
    GDALAllRegister();

    info("\nBuilding VRTs...");

    for (int t = 0; t < tileset_count; t++) {
        const Tileset *tileset = tilesets[t];

        info("\n=== VRT: %s ===", tileset->name);

        /* Collect temp file paths for this tileset */
        char (*temp_file_buf)[PATH_SIZE] = malloc(tileset->dataset_count * PATH_SIZE);
        const char **temp_files = malloc(tileset->dataset_count * sizeof(char *));

        if (!temp_file_buf || !temp_files) {
            error("Failed to allocate temp file arrays");
            free(temp_file_buf);
            free(temp_files);
            return -1;
        }

        for (int d = 0; d < tileset->dataset_count; d++) {
            const Dataset *dataset = get_dataset(tileset->datasets[d]);
            if (!dataset) {
                error("Unknown dataset: %s", tileset->datasets[d]);
                free(temp_file_buf);
                free(temp_files);
                return -1;
            }
            snprintf(temp_file_buf[d], PATH_SIZE, "%s/%s", tmppath, dataset->tmp_file);
            temp_files[d] = temp_file_buf[d];

            /* Verify file exists */
            struct stat st;
            if (stat(temp_files[d], &st) != 0) {
                error("Missing output file: %s", temp_files[d]);
                free(temp_file_buf);
                free(temp_files);
                return -1;
            }
        }

        /* Build VRT from temp files */
        char vrt_path[PATH_SIZE];
        snprintf(vrt_path, sizeof(vrt_path), "%s/__%s.vrt", tmppath, tileset->name);

        if (build_vrt(vrt_path, temp_files, tileset->dataset_count) != 0) {
            error("Failed to build VRT for tileset: %s", tileset->name);
            free(temp_file_buf);
            free(temp_files);
            return -1;
        }

        free(temp_file_buf);
        free(temp_files);
    }

    return 0;
}

/* Comparison function for sorting datasets by max_lod descending */
static int compare_by_max_lod_desc(const void *a, const void *b) {
    const DatasetSortEntry *ea = (const DatasetSortEntry *)a;
    const DatasetSortEntry *eb = (const DatasetSortEntry *)b;
    /* Descending order: higher max_lod first */
    return eb->max_lod - ea->max_lod;
}

int build_zoom_vrt(const Tileset *tileset, int zoom, const char *tmppath, char *vrt_path_out) {
    /*
     * Build a VRT for a specific zoom level.
     *
     * Includes only datasets where max_lod >= zoom, ordered by max_lod
     * descending so that smaller max_lod datasets (more appropriate for
     * this zoom level) appear last and render on top.
     *
     * Returns 0 on success, -1 on error or if no datasets qualify.
     */

    /* First pass: count qualifying datasets and collect info */
    DatasetSortEntry *entries = malloc(tileset->dataset_count * sizeof(DatasetSortEntry));
    char (*path_buf)[PATH_SIZE] = malloc(tileset->dataset_count * PATH_SIZE);

    if (!entries || !path_buf) {
        error("Failed to allocate memory for zoom VRT");
        free(entries);
        free(path_buf);
        return -1;
    }

    int entry_count = 0;
    for (int d = 0; d < tileset->dataset_count; d++) {
        const Dataset *dataset = get_dataset(tileset->datasets[d]);
        if (!dataset) continue;

        /* Only include datasets where max_lod >= zoom */
        if (dataset->max_lod < zoom) continue;

        /* Build path and check existence */
        snprintf(path_buf[entry_count], PATH_SIZE, "%s/%s", tmppath, dataset->tmp_file);

        struct stat st;
        if (stat(path_buf[entry_count], &st) != 0) continue;

        entries[entry_count].tmp_file_path = path_buf[entry_count];
        entries[entry_count].max_lod = dataset->max_lod;
        entry_count++;
    }

    if (entry_count == 0) {
        free(entries);
        free(path_buf);
        return -1;
    }

    /* Sort by max_lod descending (highest first = bottom of VRT stack) */
    qsort(entries, entry_count, sizeof(DatasetSortEntry), compare_by_max_lod_desc);

    /* Build array of file paths in sorted order */
    const char **sorted_files = malloc(entry_count * sizeof(char *));
    if (!sorted_files) {
        error("Failed to allocate sorted file array");
        free(entries);
        free(path_buf);
        return -1;
    }

    for (int i = 0; i < entry_count; i++) {
        sorted_files[i] = entries[i].tmp_file_path;
    }

    /* Build VRT path */
    snprintf(vrt_path_out, PATH_SIZE, "%s/__%s__z%d.vrt", tmppath, tileset->name, zoom);

    /* Build the VRT */
    GDALBuildVRTOptions *vrt_options = GDALBuildVRTOptionsNew(NULL, NULL);
    if (!vrt_options) {
        error("Failed to create VRT options");
        free(sorted_files);
        free(entries);
        free(path_buf);
        return -1;
    }

    int error_flag = 0;
    GDALDatasetH vrt = GDALBuildVRT(
        vrt_path_out,
        entry_count,
        NULL,
        sorted_files,
        vrt_options,
        &error_flag
    );

    GDALBuildVRTOptionsFree(vrt_options);
    free(sorted_files);
    free(entries);
    free(path_buf);

    if (!vrt || error_flag) {
        error("Failed to build zoom VRT for z%d", zoom);
        return -1;
    }

    GDALClose(vrt);
    return 0;
}
