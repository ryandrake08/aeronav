/*
 * vrt.c - VRT (Virtual Raster) processing
 */

#include <stdlib.h>
#include <sys/stat.h>

#include "aeronav.h"
#include <gdal.h>
#include <gdal_utils.h>

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
