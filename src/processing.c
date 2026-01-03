/*
 * aeronav2tiles - Processing functions
 *
 * Implements the dataset processing pipeline:
 * 1. Open from ZIP via /vsizip/
 * 2. Expand palette to RGB if needed
 * 3. Apply pixel-space mask
 * 4. Apply GCPs if provided
 * 5. Warp to target EPSG at specified resolution
 * 6. Clip to geographic bounds if specified
 * 7. Save to file and build overviews
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "aeronav.h"
#include "jobqueue.h"
#include <gdal.h>
#include <gdal_utils.h>
#include <gdal_alg.h>
#include <ogr_srs_api.h>
#include <cpl_conv.h>
#include <cpl_string.h>

/*
 * Calculate mask bounding box.
 * Returns 1 if valid bbox found, 0 if no mask or empty.
 */
static int get_mask_bbox(const Mask *mask, int src_width, int src_height,
                         int *out_min_x, int *out_min_y,
                         int *out_width, int *out_height) {
    if (!mask || mask->count == 0) {
        return 0;
    }

    const Ring *outer = &mask->rings[0];
    if (outer->count == 0) {
        return 0;
    }

    int min_x = (int)outer->vertices[0].x;
    int max_x = (int)outer->vertices[0].x;
    int min_y = (int)outer->vertices[0].y;
    int max_y = (int)outer->vertices[0].y;

    for (int i = 1; i < outer->count; i++) {
        int x = (int)outer->vertices[i].x;
        int y = (int)outer->vertices[i].y;
        if (x < min_x) min_x = x;
        if (x > max_x) max_x = x;
        if (y < min_y) min_y = y;
        if (y > max_y) max_y = y;
    }

    /* Clamp to source image bounds */
    if (min_x < 0) min_x = 0;
    if (min_y < 0) min_y = 0;
    if (max_x > src_width) max_x = src_width;
    if (max_y > src_height) max_y = src_height;

    int width = max_x - min_x;
    int height = max_y - min_y;

    if (width <= 0 || height <= 0) {
        return 0;
    }

    *out_min_x = min_x;
    *out_min_y = min_y;
    *out_width = width;
    *out_height = height;
    return 1;
}

/*
 * Expand paletted image to RGB.
 * If mask is provided, only expands the mask bounding box window.
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if expansion was performed, NULL if no-op.
 * Sets *win_offset_x and *win_offset_y to the window offset if srcwin was used.
 */
static int expand_to_rgb(GDALDatasetH src, const Mask *mask, GDALDatasetH *out,
                         int *win_offset_x, int *win_offset_y) {
    if (!out) {
        error("expand_to_rgb: NULL output parameter");
        return -1;
    }
    *out = NULL;
    if (win_offset_x) *win_offset_x = 0;
    if (win_offset_y) *win_offset_y = 0;

    GDALRasterBandH band = GDALGetRasterBand(src, 1);
    if (!band) {
        error("Failed to get raster band 1");
        return -1;
    }

    GDALColorTableH ct = GDALGetRasterColorTable(band);
    if (!ct) {
        /* Already RGB or grayscale, no expansion needed */
        return 0;
    }

    /* Use GDALTranslate with -expand rgb */
    char **options = NULL;
    options = CSLAddString(options, "-of");
    options = CSLAddString(options, "MEM");
    options = CSLAddString(options, "-expand");
    options = CSLAddString(options, "rgb");

    /* If mask provided, add -srcwin to only expand the needed window */
    int bbox_min_x, bbox_min_y, bbox_width, bbox_height;
    int has_bbox = get_mask_bbox(mask,
                                 GDALGetRasterXSize(src),
                                 GDALGetRasterYSize(src),
                                 &bbox_min_x, &bbox_min_y,
                                 &bbox_width, &bbox_height);

    if (has_bbox) {
        char buf[64];
        options = CSLAddString(options, "-srcwin");
        snprintf(buf, sizeof(buf), "%d", bbox_min_x);
        options = CSLAddString(options, buf);
        snprintf(buf, sizeof(buf), "%d", bbox_min_y);
        options = CSLAddString(options, buf);
        snprintf(buf, sizeof(buf), "%d", bbox_width);
        options = CSLAddString(options, buf);
        snprintf(buf, sizeof(buf), "%d", bbox_height);
        options = CSLAddString(options, buf);
    }

    GDALTranslateOptions *translate_opts = GDALTranslateOptionsNew(options, NULL);
    CSLDestroy(options);

    if (!translate_opts) {
        error("Failed to create translate options");
        return -1;
    }

    int err = 0;
    GDALDatasetH result = GDALTranslate("", src, translate_opts, &err);
    GDALTranslateOptionsFree(translate_opts);

    if (!result || err) {
        error("GDALTranslate failed for RGB expansion");
        return -1;
    }

    /* If we used srcwin, adjust geotransform and report offset */
    if (has_bbox) {
        double gt[6];
        if (GDALGetGeoTransform(src, gt) == CE_None) {
            double new_gt[6];
            new_gt[0] = gt[0] + bbox_min_x * gt[1] + bbox_min_y * gt[2];
            new_gt[1] = gt[1];
            new_gt[2] = gt[2];
            new_gt[3] = gt[3] + bbox_min_x * gt[4] + bbox_min_y * gt[5];
            new_gt[4] = gt[4];
            new_gt[5] = gt[5];
            GDALSetGeoTransform(result, new_gt);
        }
        if (win_offset_x) *win_offset_x = bbox_min_x;
        if (win_offset_y) *win_offset_y = bbox_min_y;
    }

    *out = result;
    return 0;
}

/*
 * Apply pixel-space mask to dataset.
 * win_offset_x/y: if the source was already windowed (e.g., by expand_to_rgb),
 *                 these values indicate the offset so mask coords can be adjusted.
 * out_offset_x/y: if not NULL, outputs the cumulative offset from original image
 *                 to the masked output (for adjusting GCP coordinates).
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if mask was applied, NULL if no-op.
 */
static int apply_mask(GDALDatasetH src, const Mask *mask,
                      int win_offset_x, int win_offset_y, GDALDatasetH *out,
                      int *out_offset_x, int *out_offset_y) {
    if (!out) {
        error("apply_mask: NULL output parameter");
        return -1;
    }
    *out = NULL;

    /* Initialize output offsets to input offsets (in case of no-op) */
    if (out_offset_x) *out_offset_x = win_offset_x;
    if (out_offset_y) *out_offset_y = win_offset_y;

    if (!mask || mask->count == 0) {
        return 0;  /* No mask to apply */
    }

    int src_width = GDALGetRasterXSize(src);
    int src_height = GDALGetRasterYSize(src);
    int src_band_count = GDALGetRasterCount(src);

    /* Calculate mask bounding box, adjusted for any prior windowing */
    const Ring *outer = &mask->rings[0];
    if (outer->count == 0) {
        return 0;  /* Empty mask */
    }

    int min_x = (int)outer->vertices[0].x - win_offset_x;
    int max_x = (int)outer->vertices[0].x - win_offset_x;
    int min_y = (int)outer->vertices[0].y - win_offset_y;
    int max_y = (int)outer->vertices[0].y - win_offset_y;

    for (int i = 1; i < outer->count; i++) {
        int x = (int)outer->vertices[i].x - win_offset_x;
        int y = (int)outer->vertices[i].y - win_offset_y;
        if (x < min_x) min_x = x;
        if (x > max_x) max_x = x;
        if (y < min_y) min_y = y;
        if (y > max_y) max_y = y;
    }

    /* Clamp to source image bounds */
    if (min_x < 0) min_x = 0;
    if (min_y < 0) min_y = 0;
    if (max_x > src_width) max_x = src_width;
    if (max_y > src_height) max_y = src_height;

    int window_width = max_x - min_x;
    int window_height = max_y - min_y;

    if (window_width <= 0 || window_height <= 0) {
        error("Invalid mask bounding box");
        return -1;
    }

    /* Create new dataset with only the window dimensions */
    GDALDriverH mem_driver = GDALGetDriverByName("MEM");
    if (!mem_driver) {
        error("MEM driver not available");
        return -1;
    }

    /* Determine if source already has alpha */
    int has_alpha = 0;
    for (int i = 1; i <= src_band_count; i++) {
        if (GDALGetRasterColorInterpretation(GDALGetRasterBand(src, i)) == GCI_AlphaBand) {
            has_alpha = 1;
            break;
        }
    }

    int dst_band_count = has_alpha ? src_band_count : src_band_count + 1;
    int alpha_band_num = dst_band_count;

    GDALDatasetH dst = GDALCreate(mem_driver, "", window_width, window_height, dst_band_count, GDT_Byte, NULL);
    if (!dst) {
        error("Failed to create masked dataset");
        return -1;
    }

    /* Copy only the window from source bands */
    unsigned char *band_data = malloc(window_width * window_height);
    if (!band_data) {
        error("Failed to allocate band buffer");
        GDALClose(dst);
        return -1;
    }

    for (int i = 1; i <= src_band_count; i++) {
        GDALRasterBandH src_band = GDALGetRasterBand(src, i);
        GDALRasterBandH dst_band = GDALGetRasterBand(dst, i);

        if (GDALRasterIO(src_band, GF_Read, min_x, min_y, window_width, window_height,
                         band_data, window_width, window_height, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to read source band %d", i);
            free(band_data);
            GDALClose(dst);
            return -1;
        }
        if (GDALRasterIO(dst_band, GF_Write, 0, 0, window_width, window_height,
                         band_data, window_width, window_height, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to write destination band %d", i);
            free(band_data);
            GDALClose(dst);
            return -1;
        }

        GDALSetRasterColorInterpretation(dst_band, GDALGetRasterColorInterpretation(src_band));
    }

    free(band_data);

    /* Set up alpha band */
    GDALRasterBandH alpha_band = GDALGetRasterBand(dst, alpha_band_num);
    GDALSetRasterColorInterpretation(alpha_band, GCI_AlphaBand);

    /* Initialize alpha to 0 (transparent outside mask) */
    unsigned char *alpha_data = calloc(window_width * window_height, 1);
    if (!alpha_data) {
        error("Failed to allocate alpha buffer");
        GDALClose(dst);
        return -1;
    }
    if (GDALRasterIO(alpha_band, GF_Write, 0, 0, window_width, window_height,
                     alpha_data, window_width, window_height, GDT_Byte, 0, 0) != CE_None) {
        error("Failed to write alpha band");
        free(alpha_data);
        GDALClose(dst);
        return -1;
    }
    free(alpha_data);

    /* Compute adjusted geotransform for the window */
    double gt[6];
    if (GDALGetGeoTransform(src, gt) == CE_None) {
        /* Adjust origin to window corner */
        double new_gt[6];
        new_gt[0] = gt[0] + min_x * gt[1] + min_y * gt[2];  /* new X origin */
        new_gt[1] = gt[1];  /* pixel width unchanged */
        new_gt[2] = gt[2];  /* rotation unchanged */
        new_gt[3] = gt[3] + min_x * gt[4] + min_y * gt[5];  /* new Y origin */
        new_gt[4] = gt[4];  /* rotation unchanged */
        new_gt[5] = gt[5];  /* pixel height unchanged */
        GDALSetGeoTransform(dst, new_gt);
    }
    const char *proj = GDALGetProjectionRef(src);
    if (proj && proj[0]) {
        GDALSetProjection(dst, proj);
    }

    /* Create OGR polygon geometry from mask, adjusted for window offset.
     * Mask polygons are in pixel coordinates relative to original image.
     */
    OGRGeometryH polygon = OGR_G_CreateGeometry(wkbPolygon);
    if (!polygon) {
        error("Failed to create polygon geometry");
        GDALClose(dst);
        return -1;
    }

    for (int r = 0; r < mask->count; r++) {
        OGRGeometryH ring = OGR_G_CreateGeometry(wkbLinearRing);
        const Ring *mask_ring = &mask->rings[r];

        for (int v = 0; v < mask_ring->count; v++) {
            /* Translate coordinates to window-relative.
             * Mask coords are in original image space. We need to subtract:
             * - win_offset: offset from prior windowing (e.g., expand_to_rgb)
             * - min_x/min_y: offset from current window extraction */
            double x = mask_ring->vertices[v].x - win_offset_x - min_x;
            double y = mask_ring->vertices[v].y - win_offset_y - min_y;
            OGR_G_AddPoint_2D(ring, x, y);
        }

        OGR_G_AddGeometryDirectly(polygon, ring);
    }

    /* Use GDALRasterizeGeometries to burn the mask into the alpha band.
     * Use pixel-space geotransform for rasterization.
     */
    double pixel_gt[6] = { 0, 1, 0, 0, 0, 1 };  /* Identity: pixel coords = geo coords */

    /* Temporarily set pixel-space geotransform for rasterization */
    double saved_gt[6];
    GDALGetGeoTransform(dst, saved_gt);
    GDALSetGeoTransform(dst, pixel_gt);

    int band_list[1] = { alpha_band_num };
    double burn_value[1] = { 255.0 };  /* Opaque where mask covers */
    OGRGeometryH geom_list[1] = { polygon };

    CPLErr err = GDALRasterizeGeometries(
        dst,
        1,              /* nBandCount */
        band_list,      /* panBandList */
        1,              /* nGeomCount */
        geom_list,      /* pahGeometries */
        NULL,           /* pfnTransformer */
        NULL,           /* pTransformArg */
        burn_value,     /* padfGeomBurnValue */
        NULL,           /* papszOptions */
        NULL,           /* pfnProgress */
        NULL            /* pProgressArg */
    );

    /* Restore saved geotransform */
    GDALSetGeoTransform(dst, saved_gt);

    OGR_G_DestroyGeometry(polygon);

    if (err != CE_None) {
        error("GDALRasterizeGeometries failed");
        GDALClose(dst);
        return -1;
    }

    /* Output cumulative offset from original image to masked output.
     * This is needed for adjusting GCP coordinates which are specified
     * in the original image's pixel space. */
    if (out_offset_x) *out_offset_x = win_offset_x + min_x;
    if (out_offset_y) *out_offset_y = win_offset_y + min_y;

    *out = dst;
    return 0;
}

/*
 * Apply ground control points to dataset.
 * offset_x/y: cumulative offset from original image, used to adjust
 *             GCP pixel coordinates to match the windowed input.
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if GCPs were applied, NULL if no-op.
 */
static int apply_gcps(GDALDatasetH src, const GCP *gcps,
                      int offset_x, int offset_y, GDALDatasetH *out) {
    if (!out) {
        error("apply_gcps: NULL output parameter");
        return -1;
    }
    *out = NULL;

    if (!gcps || gcps->count == 0) {
        return 0;  /* No GCPs to apply */
    }

    int width = GDALGetRasterXSize(src);
    int height = GDALGetRasterYSize(src);
    int band_count = GDALGetRasterCount(src);
    const char *src_wkt = GDALGetProjectionRef(src);
    bool has_src_crs = src_wkt && strlen(src_wkt) > 0;

    /* Create a copy of the dataset since we need to modify georeferencing */
    GDALDriverH mem_driver = GDALGetDriverByName("MEM");
    if (!mem_driver) {
        error("MEM driver not available");
        return -1;
    }

    GDALDatasetH dst = GDALCreate(mem_driver, "", width, height, band_count, GDT_Byte, NULL);
    if (!dst) {
        error("Failed to create dataset for GCPs");
        return -1;
    }

    /* Copy bands */
    unsigned char *band_data = malloc(width * height);
    if (!band_data) {
        error("Failed to allocate band buffer");
        GDALClose(dst);
        return -1;
    }

    for (int i = 1; i <= band_count; i++) {
        GDALRasterBandH src_band = GDALGetRasterBand(src, i);
        GDALRasterBandH dst_band = GDALGetRasterBand(dst, i);

        if (GDALRasterIO(src_band, GF_Read, 0, 0, width, height,
                         band_data, width, height, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to read source band %d", i);
            free(band_data);
            GDALClose(dst);
            return -1;
        }
        if (GDALRasterIO(dst_band, GF_Write, 0, 0, width, height,
                         band_data, width, height, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to write destination band %d", i);
            free(band_data);
            GDALClose(dst);
            return -1;
        }

        GDALSetRasterColorInterpretation(dst_band, GDALGetRasterColorInterpretation(src_band));
    }

    free(band_data);

    /*
     * Create coordinate transformation from WGS84 to source CRS.
     * GCPs are specified as lon/lat but the affine transform must be computed
     * in the source CRS to avoid distortion from lat/lon convergence at high
     * latitudes (e.g., in Lambert Conformal Conic the grid is more regular).
     */
    OGRCoordinateTransformationH transform = NULL;
    if (has_src_crs) {
        OGRSpatialReferenceH src_srs = OSRNewSpatialReference(src_wkt);
        if (!src_srs) {
            error("Failed to parse source CRS");
            GDALClose(dst);
            return -1;
        }
        OSRSetAxisMappingStrategy(src_srs, OAMS_TRADITIONAL_GIS_ORDER);

        OGRSpatialReferenceH wgs84_srs = OSRNewSpatialReference(NULL);
        OSRImportFromEPSG(wgs84_srs, 4326);
        OSRSetAxisMappingStrategy(wgs84_srs, OAMS_TRADITIONAL_GIS_ORDER);

        transform = OCTNewCoordinateTransformation(wgs84_srs, src_srs);
        OSRDestroySpatialReference(wgs84_srs);
        OSRDestroySpatialReference(src_srs);

        if (!transform) {
            error("Failed to create coordinate transformation");
            GDALClose(dst);
            return -1;
        }
    }

    /* Create GDAL_GCP array, transforming lon/lat to source CRS.
     * GCP pixel coordinates are specified in original image space, so we
     * subtract the cumulative offset from windowing (expand_to_rgb + apply_mask). */
    GDAL_GCP gdal_gcps[MAX_GCPS];
    for (int i = 0; i < gcps->count; i++) {
        gdal_gcps[i].pszId = "";
        gdal_gcps[i].pszInfo = "";
        gdal_gcps[i].dfGCPPixel = gcps->points[i].pixel_x - offset_x;
        gdal_gcps[i].dfGCPLine = gcps->points[i].pixel_y - offset_y;

        double x = gcps->points[i].lon;
        double y = gcps->points[i].lat;
        if (transform) {
            OCTTransform(transform, 1, &x, &y, NULL);
        }
        gdal_gcps[i].dfGCPX = x;
        gdal_gcps[i].dfGCPY = y;
        gdal_gcps[i].dfGCPZ = 0;
    }

    if (transform) {
        OCTDestroyCoordinateTransformation(transform);
    }

    /* Compute best-fit affine geotransform from GCPs */
    double geotransform[6];
    if (!GDALGCPsToGeoTransform(gcps->count, gdal_gcps, geotransform, TRUE)) {
        error("Failed to compute geotransform from GCPs");
        GDALClose(dst);
        return -1;
    }

    /* Set geotransform and CRS on output dataset */
    if (GDALSetGeoTransform(dst, geotransform) != CE_None) {
        error("Failed to set geotransform");
        GDALClose(dst);
        return -1;
    }

    if (has_src_crs) {
        GDALSetProjection(dst, src_wkt);
    } else {
        /* Fallback to WGS84 if source has no CRS */
        OGRSpatialReferenceH fallback = OSRNewSpatialReference(NULL);
        OSRImportFromEPSG(fallback, 4326);
        OSRSetAxisMappingStrategy(fallback, OAMS_TRADITIONAL_GIS_ORDER);
        char *fallback_wkt = NULL;
        OSRExportToWkt(fallback, &fallback_wkt);
        GDALSetProjection(dst, fallback_wkt);
        CPLFree(fallback_wkt);
        OSRDestroySpatialReference(fallback);
    }

    *out = dst;
    return 0;
}

/*
 * Warp dataset to target EPSG at specified resolution.
 * Returns 0 on success, -1 on error.
 * Always sets *out to new dataset on success (no no-op case).
 */
static int warp_to_target(GDALDatasetH src, double resolution, int num_threads,
                          int epsg, const char *resampling, GDALDatasetH *out) {
    if (!out) {
        error("warp_to_target: NULL output parameter");
        return -1;
    }
    *out = NULL;

    /* Build warp options */
    char **options = NULL;
    options = CSLAddString(options, "-of");
    options = CSLAddString(options, "MEM");
    options = CSLAddString(options, "-t_srs");

    char epsg_str[16];
    snprintf(epsg_str, sizeof(epsg_str), "EPSG:%d", epsg);
    options = CSLAddString(options, epsg_str);
    options = CSLAddString(options, "-tr");

    char res_str[32];
    snprintf(res_str, sizeof(res_str), "%.10f", resolution);
    options = CSLAddString(options, res_str);
    options = CSLAddString(options, res_str);

    options = CSLAddString(options, "-r");
    options = CSLAddString(options, resampling);

    /* Multi-threading */
    if (num_threads > 1) {
        options = CSLAddString(options, "-wo");
        char threads_opt[32];
        snprintf(threads_opt, sizeof(threads_opt), "NUM_THREADS=%d", num_threads);
        options = CSLAddString(options, threads_opt);
    }

    /* Source dataset alpha handling */
    options = CSLAddString(options, "-dstalpha");

    GDALWarpAppOptions *warp_opts = GDALWarpAppOptionsNew(options, NULL);
    CSLDestroy(options);

    if (!warp_opts) {
        error("Failed to create warp options");
        return -1;
    }

    int err = 0;
    GDALDatasetH src_array[1] = { src };
    GDALDatasetH result = GDALWarp("", NULL, 1, src_array, warp_opts, &err);
    GDALWarpAppOptionsFree(warp_opts);

    if (!result || err) {
        error("GDALWarp failed");
        return -1;
    }

    *out = result;
    return 0;
}

/*
 * Clip dataset to geographic bounds (post-warp).
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if clipping was applied, NULL if no-op.
 */
static int clip_to_bounds(GDALDatasetH src, const GeoBounds *bounds,
                          int epsg, GDALDatasetH *out) {
    if (!out) {
        error("clip_to_bounds: NULL output parameter");
        return -1;
    }
    *out = NULL;

    if (!bounds) {
        return 0;  /* No bounds specified */
    }

    /* Check if any bounds are specified */
    int has_bounds = !isnan(bounds->lon_min) || !isnan(bounds->lat_min) ||
                     !isnan(bounds->lon_max) || !isnan(bounds->lat_max);
    if (!has_bounds) {
        return 0;  /* No bounds specified */
    }

    /* Get source bounds in its CRS */
    double gt[6];
    if (GDALGetGeoTransform(src, gt) != CE_None) {
        error("Failed to get geotransform");
        return -1;
    }

    int width = GDALGetRasterXSize(src);
    int height = GDALGetRasterYSize(src);

    double src_min_x = gt[0];
    double src_max_x = gt[0] + width * gt[1];
    double src_max_y = gt[3];
    double src_min_y = gt[3] + height * gt[5];  /* gt[5] is negative */

    /* Convert bounds from lat/lon to target EPSG */
    OGRSpatialReferenceH wgs84 = OSRNewSpatialReference(NULL);
    OGRSpatialReferenceH target = OSRNewSpatialReference(NULL);
    OSRImportFromEPSG(wgs84, 4326);
    OSRImportFromEPSG(target, epsg);
    OSRSetAxisMappingStrategy(wgs84, OAMS_TRADITIONAL_GIS_ORDER);
    OSRSetAxisMappingStrategy(target, OAMS_TRADITIONAL_GIS_ORDER);

    OGRCoordinateTransformationH ct = OCTNewCoordinateTransformation(wgs84, target);
    if (!ct) {
        error("Failed to create coordinate transformation");
        OSRDestroySpatialReference(wgs84);
        OSRDestroySpatialReference(target);
        return -1;
    }

    double clip_min_x = src_min_x;
    double clip_max_x = src_max_x;
    double clip_min_y = src_min_y;
    double clip_max_y = src_max_y;

    /* Transform bounds that are specified.
     * Use center of source dataset for the dummy coordinate to ensure
     * accurate projection at any location (not just mid-latitudes). */
    double center_x = (src_min_x + src_max_x) / 2.0;
    double center_y = (src_min_y + src_max_y) / 2.0;

    /* Inverse transform center point to get WGS84 coords for dummy values */
    OGRCoordinateTransformationH ct_inv = OCTNewCoordinateTransformation(target, wgs84);
    double dummy_lat = 45.0, dummy_lon = -100.0;
    if (ct_inv) {
        double cx = center_x, cy = center_y;
        if (OCTTransform(ct_inv, 1, &cx, &cy, NULL)) {
            dummy_lon = cx;
            dummy_lat = cy;
        }
        OCTDestroyCoordinateTransformation(ct_inv);
    }

    if (!isnan(bounds->lon_min)) {
        double x = bounds->lon_min, y = dummy_lat;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_min_x = x;
        }
    }
    if (!isnan(bounds->lon_max)) {
        double x = bounds->lon_max, y = dummy_lat;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_max_x = x;
        }
    }
    if (!isnan(bounds->lat_min)) {
        double x = dummy_lon, y = bounds->lat_min;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_min_y = y;
        }
    }
    if (!isnan(bounds->lat_max)) {
        double x = dummy_lon, y = bounds->lat_max;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_max_y = y;
        }
    }

    OCTDestroyCoordinateTransformation(ct);
    OSRDestroySpatialReference(wgs84);
    OSRDestroySpatialReference(target);

    /* Intersect with source bounds */
    if (clip_min_x < src_min_x) clip_min_x = src_min_x;
    if (clip_max_x > src_max_x) clip_max_x = src_max_x;
    if (clip_min_y < src_min_y) clip_min_y = src_min_y;
    if (clip_max_y > src_max_y) clip_max_y = src_max_y;

    /* Check if clipping actually changes anything */
    if (clip_min_x == src_min_x && clip_max_x == src_max_x &&
        clip_min_y == src_min_y && clip_max_y == src_max_y) {
        return 0;  /* No change needed */
    }

    /* Use GDALTranslate with -projwin */
    char **options = NULL;
    options = CSLAddString(options, "-of");
    options = CSLAddString(options, "MEM");
    options = CSLAddString(options, "-projwin");

    char coord[32];
    snprintf(coord, sizeof(coord), "%.10f", clip_min_x);
    options = CSLAddString(options, coord);
    snprintf(coord, sizeof(coord), "%.10f", clip_max_y);
    options = CSLAddString(options, coord);
    snprintf(coord, sizeof(coord), "%.10f", clip_max_x);
    options = CSLAddString(options, coord);
    snprintf(coord, sizeof(coord), "%.10f", clip_min_y);
    options = CSLAddString(options, coord);

    GDALTranslateOptions *translate_opts = GDALTranslateOptionsNew(options, NULL);
    CSLDestroy(options);

    if (!translate_opts) {
        error("Failed to create translate options for clipping");
        return -1;
    }

    int err = 0;
    GDALDatasetH result = GDALTranslate("", src, translate_opts, &err);
    GDALTranslateOptionsFree(translate_opts);

    if (!result || err) {
        error("GDALTranslate failed for clipping");
        return -1;
    }

    *out = result;
    return 0;
}

/*
 * Save in-memory dataset to compressed GTiff file with overviews.
 * Returns 0 on success, -1 on error.
 */
static int save_with_overviews(GDALDatasetH ds, const char *outpath) {
    if (!ds || !outpath) {
        error("save_with_overviews: NULL argument");
        return -1;
    }

    int width = GDALGetRasterXSize(ds);
    int height = GDALGetRasterYSize(ds);
    int band_count = GDALGetRasterCount(ds);

    GDALDriverH gtiff_driver = GDALGetDriverByName("GTiff");
    if (!gtiff_driver) {
        error("GTiff driver not available");
        return -1;
    }

    /* Configure overview compression before creating file */
    CPLSetConfigOption("COMPRESS_OVERVIEW", "LZW");
    CPLSetConfigOption("BIGTIFF_OVERVIEW", "IF_SAFER");

    char **options = NULL;
    options = CSLSetNameValue(options, "COMPRESS", "LZW");
    options = CSLSetNameValue(options, "TILED", "YES");
    options = CSLSetNameValue(options, "BIGTIFF", "IF_SAFER");

    GDALDatasetH out = GDALCreate(gtiff_driver, outpath, width, height,
                                   band_count, GDT_Byte, options);
    CSLDestroy(options);

    if (!out) {
        error("Failed to create file: %s", outpath);
        return -1;
    }

    /* Copy geotransform and projection */
    double geotransform[6];
    if (GDALGetGeoTransform(ds, geotransform) == CE_None) {
        GDALSetGeoTransform(out, geotransform);
    }

    const char *proj = GDALGetProjectionRef(ds);
    if (proj && strlen(proj) > 0) {
        GDALSetProjection(out, proj);
    }

    /* Copy bands using scanline I/O */
    unsigned char *scanline = malloc(width);
    if (!scanline) {
        error("Failed to allocate scanline buffer");
        GDALClose(out);
        return -1;
    }

    /* Set color interpretation before writing any data */
    for (int i = 1; i <= band_count; i++) {
        GDALRasterBandH src_band = GDALGetRasterBand(ds, i);
        GDALRasterBandH dst_band = GDALGetRasterBand(out, i);
        GDALSetRasterColorInterpretation(dst_band,
            GDALGetRasterColorInterpretation(src_band));
    }

    /* Copy band data */
    for (int i = 1; i <= band_count; i++) {
        GDALRasterBandH src_band = GDALGetRasterBand(ds, i);
        GDALRasterBandH dst_band = GDALGetRasterBand(out, i);

        for (int y = 0; y < height; y++) {
            if (GDALRasterIO(src_band, GF_Read, 0, y, width, 1,
                             scanline, width, 1, GDT_Byte, 0, 0) != CE_None) {
                error("Failed to read scanline %d of band %d", y, i);
                free(scanline);
                GDALClose(out);
                return -1;
            }

            if (GDALRasterIO(dst_band, GF_Write, 0, y, width, 1,
                             scanline, width, 1, GDT_Byte, 0, 0) != CE_None) {
                error("Failed to write scanline %d of band %d", y, i);
                free(scanline);
                GDALClose(out);
                return -1;
            }
        }
    }

    free(scanline);

    /* Flush data before building overviews */
    GDALFlushCache(out);

    /* Build overviews on the open file handle.
     * Overview levels: 2, 4, 8, 16, 32, 64 cover zoom differences of 1-6 levels */
    int levels[] = {2, 4, 8, 16, 32, 64};
    int num_levels = sizeof(levels) / sizeof(levels[0]);

    CPLErr err = GDALBuildOverviews(
        out,
        "AVERAGE",      /* Resampling method - good for imagery */
        num_levels,     /* Number of overview levels */
        levels,         /* Overview decimation factors */
        0,              /* Number of bands (0 = all bands) */
        NULL,           /* Band list (NULL = all bands) */
        NULL,           /* Progress function */
        NULL            /* Progress data */
    );

    GDALClose(out);

    if (err != CE_None) {
        error("Failed to build overviews for: %s", outpath);
        return -1;
    }

    return 0;
}

/*
 * Get the center latitude of a georeferenced GDAL dataset in radians.
 * Transforms the center point to WGS84 to get the latitude.
 * Returns NAN if the transformation fails.
 *
 * This is used to adjust the output resolution for Web Mercator distortion.
 * At high latitudes, the same EPSG:3857 resolution represents finer ground
 * resolution, so we coarsen the output to avoid upscaling.
 */
static double get_center_latitude_from_dataset(GDALDatasetH ds) {
    double gt[6];
    if (GDALGetGeoTransform(ds, gt) != CE_None) {
        return NAN;
    }

    /* Get center point in native coordinates */
    int width = GDALGetRasterXSize(ds);
    int height = GDALGetRasterYSize(ds);
    double center_x = gt[0] + (width / 2.0) * gt[1] + (height / 2.0) * gt[2];
    double center_y = gt[3] + (width / 2.0) * gt[4] + (height / 2.0) * gt[5];

    /* Get source CRS */
    const char *src_wkt = GDALGetProjectionRef(ds);
    if (!src_wkt || strlen(src_wkt) == 0) {
        return NAN;
    }

    OGRSpatialReferenceH src_srs = OSRNewSpatialReference(src_wkt);
    if (!src_srs) {
        return NAN;
    }

    /* Create WGS84 CRS with traditional GIS axis order (lon, lat) */
    OGRSpatialReferenceH wgs84 = OSRNewSpatialReference(NULL);
    OSRSetWellKnownGeogCS(wgs84, "WGS84");
    OSRSetAxisMappingStrategy(wgs84, OAMS_TRADITIONAL_GIS_ORDER);
    OSRSetAxisMappingStrategy(src_srs, OAMS_TRADITIONAL_GIS_ORDER);

    /* Create coordinate transformation */
    OGRCoordinateTransformationH transform = OCTNewCoordinateTransformation(src_srs, wgs84);
    OSRDestroySpatialReference(src_srs);

    if (!transform) {
        OSRDestroySpatialReference(wgs84);
        return NAN;
    }

    /* Transform center point to WGS84 */
    double lon = center_x;
    double lat = center_y;
    if (!OCTTransform(transform, 1, &lon, &lat, NULL)) {
        OCTDestroyCoordinateTransformation(transform);
        OSRDestroySpatialReference(wgs84);
        return NAN;
    }

    OCTDestroyCoordinateTransformation(transform);
    OSRDestroySpatialReference(wgs84);

    /* Validate latitude is in reasonable range */
    if (lat < -90.0 || lat > 90.0) {
        error("Invalid latitude %.2f from coordinate transform", lat);
        return NAN;
    }

    /* Return latitude in radians */
    return lat * M_PI / 180.0;
}

static int process_dataset(const char *zippath,
                           const Dataset *dataset,
                           double resolution,
                           const char *outpath,
                           int num_threads,
                           int epsg,
                           const char *resampling) {
    /* Build vsizip path: /vsizip/zippath/name.zip/input_file */
    char vsi_path[PATH_SIZE];
    snprintf(vsi_path, sizeof(vsi_path), "/vsizip/%s/%s.zip/%s",
             zippath, dataset->zip_file, dataset->input_file);

    /* Open the dataset */
    GDALDatasetH src = GDALOpen(vsi_path, GA_ReadOnly);
    if (!src) {
        error("Failed to open: %s", vsi_path);
        return -1;
    }

    GDALDatasetH tmp;
    int win_offset_x = 0, win_offset_y = 0;
    int cumulative_offset_x = 0, cumulative_offset_y = 0;

    /* Step 1: RGB expansion if needed (also extracts mask window if paletted) */
    if (expand_to_rgb(src, dataset->mask, &tmp, &win_offset_x, &win_offset_y) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
    }

    /* Step 2: Apply pixel-space mask.
     * Captures cumulative offset for GCP coordinate adjustment. */
    if (apply_mask(src, dataset->mask, win_offset_x, win_offset_y, &tmp,
                   &cumulative_offset_x, &cumulative_offset_y) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
    }

    /* Step 3: Apply GCPs if present.
     * Pass cumulative offset so GCP pixel coords are adjusted for windowing. */
    if (apply_gcps(src, dataset->gcps, cumulative_offset_x, cumulative_offset_y, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
    }

    /* Adjust resolution for latitude (Web Mercator distortion).
     * The input 'resolution' is equatorial; we coarsen it at high latitudes
     * to avoid upscaling the source data. */
    double center_lat = get_center_latitude_from_dataset(src);
    if (isnan(center_lat)) {
        error("Failed to determine center latitude for %s", dataset->name);
        GDALClose(src);
        return -1;
    }
    double adjusted_resolution = resolution / cos(center_lat);

    /* Step 4: Warp to target EPSG */
    if (warp_to_target(src, adjusted_resolution, num_threads, epsg, resampling, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    GDALClose(src);
    src = tmp;

    /* Step 5: Clip to geographic bounds */
    if (clip_to_bounds(src, dataset->geobound, epsg, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
    }

    /* Step 6: Save to output file with overviews */
    int result = save_with_overviews(src, outpath);
    GDALClose(src);
    return result;
}

/* ============================================================================
 * Parallel Dataset Processing
 * ============================================================================ */

/*
 * Job data for processing a single dataset.
 * An array of these is passed to the job queue.
 */
typedef struct {
    const char *zippath;          /* Directory containing ZIP files */
    const Dataset *dataset;       /* Dataset to process */
    double resolution;            /* Target resolution (from tileset's max max_lod) */
    char temp_file[PATH_SIZE];    /* Output temp file path */
    int num_threads;              /* Threads per job for warping */
    int epsg;                     /* Target EPSG code */
    const char *resampling;       /* Resampling method */
    double estimated_work;        /* Estimated work for sorting (larger = more work) */
} DatasetJob;

/*
 * Estimate work for a dataset based on mask bounding box.
 * Datasets with larger areas are processed first to reduce job starvation.
 */
static double estimate_work(const Dataset *dataset) {
    if (!dataset->mask || dataset->mask->count == 0) {
        return 0.0;  /* No mask = unknown size, sort to end */
    }

    /* Compute bounding box of outer ring */
    const Ring *outer = &dataset->mask->rings[0];
    if (outer->count == 0) {
        return 0.0;
    }

    double min_x = outer->vertices[0].x;
    double max_x = outer->vertices[0].x;
    double min_y = outer->vertices[0].y;
    double max_y = outer->vertices[0].y;

    for (int i = 1; i < outer->count; i++) {
        if (outer->vertices[i].x < min_x) min_x = outer->vertices[i].x;
        if (outer->vertices[i].x > max_x) max_x = outer->vertices[i].x;
        if (outer->vertices[i].y < min_y) min_y = outer->vertices[i].y;
        if (outer->vertices[i].y > max_y) max_y = outer->vertices[i].y;
    }

    return (max_x - min_x) * (max_y - min_y);
}

/* Comparison function for qsort - sort by descending estimated work */
static int compare_jobs_by_work(const void *a, const void *b) {
    const DatasetJob *ja = (const DatasetJob *)a;
    const DatasetJob *jb = (const DatasetJob *)b;

    if (jb->estimated_work > ja->estimated_work) return 1;
    if (jb->estimated_work < ja->estimated_work) return -1;
    return 0;
}

static int dataset_worker_init(int worker_id, void *init_data) {
    (void)worker_id;
    (void)init_data;

    /* Initialize GDAL in this worker process */
    GDALAllRegister();

    /* Use GeoTIFF embedded CRS parameters instead of EPSG registry */
    CPLSetConfigOption("GTIFF_SRS_SOURCE", "GEOKEYS");

    return 0;
}

static int dataset_job_func(int job_index, void *job_data) {
    DatasetJob *jobs = (DatasetJob *)job_data;
    DatasetJob *job = &jobs[job_index];

    return process_dataset(
        job->zippath,
        job->dataset,
        job->resolution,
        job->temp_file,
        job->num_threads,
        job->epsg,
        job->resampling
    );
}

int process_datasets_parallel(
    const Tileset **tilesets,
    int tileset_count,
    const char *zippath,
    const char *tmppath,
    int num_workers,
    int threads_per_job,
    int epsg,
    const char *resampling
) {
    /* Count total datasets */
    int total_datasets = 0;
    for (int t = 0; t < tileset_count; t++) {
        total_datasets += tilesets[t]->dataset_count;
    }

    if (total_datasets == 0) {
        return 0;
    }

    /* Allocate job array */
    DatasetJob *jobs = calloc(total_datasets, sizeof(DatasetJob));
    if (!jobs) {
        error("Failed to allocate job array");
        return -1;
    }

    /* Populate jobs from all tilesets */
    int job_index = 0;
    for (int t = 0; t < tileset_count; t++) {
        const Tileset *tileset = tilesets[t];

        info("\n=== Tileset: %s ===", tileset->name);
        info("  Output path: %s", tileset->tile_path);
        info("  Zoom range: %d-%d", tileset->zoom_min, tileset->zoom_max);
        info("  Datasets: %d", tileset->dataset_count);

        for (int d = 0; d < tileset->dataset_count; d++) {
            const Dataset *dataset = get_dataset(tileset->datasets[d]);
            if (!dataset) {
                error("Unknown dataset: %s", tileset->datasets[d]);
                continue;
            }

            /* Use equatorial resolution - will be adjusted for latitude in worker */
            double equatorial_resolution = resolution_for_zoom(dataset->max_lod);

            DatasetJob *job = &jobs[job_index++];
            job->zippath = zippath;
            job->dataset = dataset;
            job->resolution = equatorial_resolution;
            job->num_threads = threads_per_job;
            job->epsg = epsg;
            job->resampling = resampling;
            job->estimated_work = estimate_work(dataset);
            snprintf(job->temp_file, PATH_SIZE, "%s/%s", tmppath, dataset->tmp_file);
        }
    }

    int actual_job_count = job_index;

    /* Sort jobs by estimated work (largest first) to reduce straggler effect */
    qsort(jobs, actual_job_count, sizeof(DatasetJob), compare_jobs_by_work);

    /* Build job names array for progress display (must be after sorting) */
    const char **job_names = malloc(actual_job_count * sizeof(char *));
    if (!job_names) {
        error("Failed to allocate job names array");
        free(jobs);
        return -1;
    }
    for (int i = 0; i < actual_job_count; i++) {
        job_names[i] = jobs[i].dataset->name;
    }

    info("\nProcessing %d datasets with %d parallel workers...",
         actual_job_count, num_workers);

    /* Configure and run the job queue */
    JobQueueConfig config = {
        .num_jobs = actual_job_count,
        .max_workers = num_workers,
        .job_data = jobs,
        .job_func = dataset_job_func,
        .worker_init = dataset_worker_init,
        .init_data = NULL,
        .job_names = job_names,
    };

    JobQueueResult jq_result;
    int queue_result = jobqueue_run(&config, &jq_result);

    info("\nDataset processing complete: %d succeeded, %d failed",
         jq_result.completed, jq_result.failed);

    free(job_names);
    free(jobs);

    return queue_result;
}
