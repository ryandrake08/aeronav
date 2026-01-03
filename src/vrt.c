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
