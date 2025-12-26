/*
 * aeronav2tiles - Processing functions
 *
 * Implements the dataset processing pipeline:
 * 1. Open from ZIP via /vsizip/
 * 2. Expand palette to RGB if needed
 * 3. Apply pixel-space mask
 * 4. Apply GCPs if provided
 * 5. Warp to EPSG:3857 at specified resolution
 * 6. Clip to geographic bounds if specified
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "aeronav.h"
#include <gdal.h>
#include <gdal_utils.h>
#include <gdal_alg.h>
#include <ogr_srs_api.h>
#include <cpl_conv.h>
#include <cpl_string.h>

/*
 * Expand paletted image to RGB.
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if expansion was performed, NULL if no-op.
 */
static int expand_to_rgb(GDALDatasetH src, GDALDatasetH *out) {
    if (!out) {
        error("expand_to_rgb: NULL output parameter");
        return -1;
    }
    *out = NULL;

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

    info("    Expanding palette to RGB...");

    /* Use GDALTranslate with -expand rgb */
    char **options = NULL;
    options = CSLAddString(options, "-of");
    options = CSLAddString(options, "MEM");
    options = CSLAddString(options, "-expand");
    options = CSLAddString(options, "rgb");

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

    *out = result;
    return 0;
}

/*
 * Apply pixel-space mask to dataset.
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if mask was applied, NULL if no-op.
 */
static int apply_mask(GDALDatasetH src, const Mask *mask, GDALDatasetH *out) {
    if (!out) {
        error("apply_mask: NULL output parameter");
        return -1;
    }
    *out = NULL;

    if (!mask || mask->count == 0) {
        return 0;  /* No mask to apply */
    }

    info("    Applying pixel mask (%d ring(s))...", mask->count);

    int width = GDALGetRasterXSize(src);
    int height = GDALGetRasterYSize(src);
    int src_band_count = GDALGetRasterCount(src);

    /* Create new dataset with RGBA (add alpha if not present) */
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

    GDALDatasetH dst = GDALCreate(mem_driver, "", width, height, dst_band_count, GDT_Byte, NULL);
    if (!dst) {
        error("Failed to create masked dataset");
        return -1;
    }

    /* Copy source bands */
    unsigned char *band_data = malloc(width * height);
    if (!band_data) {
        error("Failed to allocate band buffer");
        GDALClose(dst);
        return -1;
    }

    for (int i = 1; i <= src_band_count; i++) {
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

    /* Set up alpha band */
    GDALRasterBandH alpha_band = GDALGetRasterBand(dst, alpha_band_num);
    GDALSetRasterColorInterpretation(alpha_band, GCI_AlphaBand);

    /* Initialize alpha to 0 (transparent outside mask) */
    unsigned char *alpha_data = calloc(width * height, 1);
    if (!alpha_data) {
        error("Failed to allocate alpha buffer");
        GDALClose(dst);
        return -1;
    }
    if (GDALRasterIO(alpha_band, GF_Write, 0, 0, width, height,
                     alpha_data, width, height, GDT_Byte, 0, 0) != CE_None) {
        error("Failed to write alpha band");
        free(alpha_data);
        GDALClose(dst);
        return -1;
    }
    free(alpha_data);

    /* Copy geotransform and projection */
    double gt[6];
    if (GDALGetGeoTransform(src, gt) == CE_None) {
        GDALSetGeoTransform(dst, gt);
    }
    const char *proj = GDALGetProjectionRef(src);
    if (proj && proj[0]) {
        GDALSetProjection(dst, proj);
    }

    /* Create OGR polygon geometry from mask.
     * Mask polygons are in pixel coordinates.
     * First ring is outer boundary (CCW), subsequent rings are holes (CW).
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
            OGR_G_AddPoint_2D(ring, mask_ring->vertices[v].x, mask_ring->vertices[v].y);
        }

        OGR_G_AddGeometryDirectly(polygon, ring);
    }

    /* Use GDALRasterizeGeometries to burn the mask into the alpha band.
     * We set a pixel-space geotransform for the rasterization since
     * the mask coordinates are in pixel space.
     */
    double pixel_gt[6] = { 0, 1, 0, 0, 0, 1 };  /* Identity: pixel coords = geo coords */

    /* Temporarily set pixel-space geotransform for rasterization */
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

    /* Restore original geotransform */
    if (GDALGetGeoTransform(src, gt) == CE_None) {
        GDALSetGeoTransform(dst, gt);
    }

    OGR_G_DestroyGeometry(polygon);

    if (err != CE_None) {
        error("GDALRasterizeGeometries failed");
        GDALClose(dst);
        return -1;
    }

    *out = dst;
    return 0;
}

/*
 * Apply ground control points to dataset.
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if GCPs were applied, NULL if no-op.
 */
static int apply_gcps(GDALDatasetH src, const GCP *gcps, GDALDatasetH *out) {
    if (!out) {
        error("apply_gcps: NULL output parameter");
        return -1;
    }
    *out = NULL;

    if (!gcps || gcps->count == 0) {
        return 0;  /* No GCPs to apply */
    }

    info("    Applying %d GCPs...", gcps->count);

    int width = GDALGetRasterXSize(src);
    int height = GDALGetRasterYSize(src);
    int band_count = GDALGetRasterCount(src);
    int result = -1;

    /* Resources to clean up on exit */
    GDALDatasetH dst = NULL;
    unsigned char *band_data = NULL;
    GDAL_GCP gdal_gcps[MAX_GCPS];
    OGRSpatialReferenceH src_srs = NULL;
    OGRSpatialReferenceH wgs84_srs = NULL;
    OGRCoordinateTransformationH transform = NULL;

    /*
     * Get the source dataset's CRS and create a transformation from WGS84.
     * GCPs are specified as lon/lat but the affine transform must be computed
     * in the source CRS to avoid distortion from lat/lon convergence at high
     * latitudes (e.g., in Lambert Conformal Conic the grid is more regular).
     */
    const char *src_wkt = GDALGetProjectionRef(src);
    if (src_wkt && strlen(src_wkt) > 0) {
        src_srs = OSRNewSpatialReference(src_wkt);
        if (src_srs) {
            OSRSetAxisMappingStrategy(src_srs, OAMS_TRADITIONAL_GIS_ORDER);

            wgs84_srs = OSRNewSpatialReference(NULL);
            OSRImportFromEPSG(wgs84_srs, 4326);
            OSRSetAxisMappingStrategy(wgs84_srs, OAMS_TRADITIONAL_GIS_ORDER);

            transform = OCTNewCoordinateTransformation(wgs84_srs, src_srs);
        }
    }

    /* Create a copy of the dataset since we need to modify georeferencing */
    GDALDriverH mem_driver = GDALGetDriverByName("MEM");
    if (!mem_driver) {
        error("MEM driver not available");
        goto cleanup;
    }

    dst = GDALCreate(mem_driver, "", width, height, band_count, GDT_Byte, NULL);
    if (!dst) {
        error("Failed to create dataset for GCPs");
        goto cleanup;
    }

    /* Copy bands */
    band_data = malloc(width * height);
    if (!band_data) {
        error("Failed to allocate band buffer");
        goto cleanup;
    }

    for (int i = 1; i <= band_count; i++) {
        GDALRasterBandH src_band = GDALGetRasterBand(src, i);
        GDALRasterBandH dst_band = GDALGetRasterBand(dst, i);

        if (GDALRasterIO(src_band, GF_Read, 0, 0, width, height,
                         band_data, width, height, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to read source band %d", i);
            goto cleanup;
        }
        if (GDALRasterIO(dst_band, GF_Write, 0, 0, width, height,
                         band_data, width, height, GDT_Byte, 0, 0) != CE_None) {
            error("Failed to write destination band %d", i);
            goto cleanup;
        }

        GDALSetRasterColorInterpretation(dst_band, GDALGetRasterColorInterpretation(src_band));
    }

    free(band_data);
    band_data = NULL;

    /* Create GDAL_GCP array, transforming lon/lat to source CRS */
    for (int i = 0; i < gcps->count; i++) {
        gdal_gcps[i].pszId = "";
        gdal_gcps[i].pszInfo = "";
        gdal_gcps[i].dfGCPPixel = gcps->points[i].pixel_x;
        gdal_gcps[i].dfGCPLine = gcps->points[i].pixel_y;

        double x = gcps->points[i].lon;
        double y = gcps->points[i].lat;
        if (transform) {
            OCTTransform(transform, 1, &x, &y, NULL);
        }
        gdal_gcps[i].dfGCPX = x;
        gdal_gcps[i].dfGCPY = y;
        gdal_gcps[i].dfGCPZ = 0;
    }

    /* Compute best-fit affine geotransform from GCPs */
    double geotransform[6];
    if (!GDALGCPsToGeoTransform(gcps->count, gdal_gcps, geotransform, TRUE)) {
        error("Failed to compute geotransform from GCPs");
        goto cleanup;
    }

    /* Set geotransform and CRS on output dataset */
    if (GDALSetGeoTransform(dst, geotransform) != CE_None) {
        error("Failed to set geotransform");
        goto cleanup;
    }

    if (src_srs) {
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

    /* Success */
    *out = dst;
    dst = NULL;  /* Prevent cleanup from closing it */
    result = 0;

cleanup:
    if (dst) GDALClose(dst);
    free(band_data);
    if (transform) OCTDestroyCoordinateTransformation(transform);
    if (wgs84_srs) OSRDestroySpatialReference(wgs84_srs);
    if (src_srs) OSRDestroySpatialReference(src_srs);
    return result;
}

/*
 * Warp dataset to EPSG:3857 (Web Mercator) at specified resolution.
 * Returns 0 on success, -1 on error.
 * Always sets *out to new dataset on success (no no-op case).
 */
static int warp_to_mercator(GDALDatasetH src, double resolution, int num_threads, GDALDatasetH *out) {
    if (!out) {
        error("warp_to_mercator: NULL output parameter");
        return -1;
    }
    *out = NULL;

    info("    Warping to EPSG:3857 at %.2f m/pixel...", resolution);

    /* Build warp options */
    char **options = NULL;
    options = CSLAddString(options, "-of");
    options = CSLAddString(options, "MEM");
    options = CSLAddString(options, "-t_srs");
    options = CSLAddString(options, "EPSG:3857");
    options = CSLAddString(options, "-tr");

    char res_str[32];
    snprintf(res_str, sizeof(res_str), "%.10f", resolution);
    options = CSLAddString(options, res_str);
    options = CSLAddString(options, res_str);

    options = CSLAddString(options, "-r");
    options = CSLAddString(options, "bilinear");

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
 * Clip dataset to geographic bounds (after warp to EPSG:3857).
 * Returns 0 on success, -1 on error.
 * Sets *out to new dataset if clipping was applied, NULL if no-op.
 */
static int clip_to_bounds(GDALDatasetH src, const GeoBounds *bounds, GDALDatasetH *out) {
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

    info("    Clipping to geographic bounds...");

    /* Get source bounds in its CRS (should be EPSG:3857) */
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

    /* Convert bounds from lat/lon to EPSG:3857 */
    OGRSpatialReferenceH src_srs = OSRNewSpatialReference(NULL);
    OGRSpatialReferenceH dst_srs = OSRNewSpatialReference(NULL);
    OSRImportFromEPSG(src_srs, 4326);  /* WGS84 */
    OSRImportFromEPSG(dst_srs, 3857);  /* Web Mercator */

    /* Force traditional GIS (lon, lat) order for WGS84 */
    OSRSetAxisMappingStrategy(src_srs, OAMS_TRADITIONAL_GIS_ORDER);
    OSRSetAxisMappingStrategy(dst_srs, OAMS_TRADITIONAL_GIS_ORDER);

    OGRCoordinateTransformationH ct = OCTNewCoordinateTransformation(src_srs, dst_srs);
    if (!ct) {
        error("Failed to create coordinate transformation");
        OSRDestroySpatialReference(src_srs);
        OSRDestroySpatialReference(dst_srs);
        return -1;
    }

    double clip_min_x = src_min_x;
    double clip_max_x = src_max_x;
    double clip_min_y = src_min_y;
    double clip_max_y = src_max_y;

    /* Transform bounds that are specified.
     * For lon, we use a valid lat (45) to avoid projection issues.
     * For lat, we use a valid lon (-100) to avoid projection issues.
     */
    if (!isnan(bounds->lon_min)) {
        double x = bounds->lon_min, y = 45.0;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_min_x = x;
        }
    }
    if (!isnan(bounds->lon_max)) {
        double x = bounds->lon_max, y = 45.0;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_max_x = x;
        }
    }
    if (!isnan(bounds->lat_min)) {
        double x = -100.0, y = bounds->lat_min;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_min_y = y;
        }
    }
    if (!isnan(bounds->lat_max)) {
        double x = -100.0, y = bounds->lat_max;
        if (OCTTransform(ct, 1, &x, &y, NULL)) {
            clip_max_y = y;
        }
    }

    OCTDestroyCoordinateTransformation(ct);
    OSRDestroySpatialReference(src_srs);
    OSRDestroySpatialReference(dst_srs);

    /* Intersect with source bounds */
    if (clip_min_x < src_min_x) clip_min_x = src_min_x;
    if (clip_max_x > src_max_x) clip_max_x = src_max_x;
    if (clip_min_y < src_min_y) clip_min_y = src_min_y;
    if (clip_max_y > src_max_y) clip_max_y = src_max_y;

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
 * Save in-memory dataset to compressed GTiff file.
 * Returns 0 on success, -1 on error.
 */
static int save_to_file(GDALDatasetH ds, const char *outpath) {
    if (!ds || !outpath) {
        error("save_to_file: NULL argument");
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

    char **options = NULL;
    options = CSLSetNameValue(options, "COMPRESS", "LZW");
    options = CSLSetNameValue(options, "TILED", "YES");

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

        GDALSetRasterColorInterpretation(dst_band,
            GDALGetRasterColorInterpretation(src_band));
    }

    free(scanline);
    GDALClose(out);
    return 0;
}

int process_dataset(const char *zippath,
                    const Dataset *dataset,
                    double resolution,
                    const char *outpath,
                    int num_threads) {
    info("  Opening %s from ZIP...", dataset->name);

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

    info("    Opened: %dx%d, %d bands",
         GDALGetRasterXSize(src), GDALGetRasterYSize(src), GDALGetRasterCount(src));

    GDALDatasetH tmp;

    /* Step 1: RGB expansion if needed */
    if (expand_to_rgb(src, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
        info("    Expanded to %d bands", GDALGetRasterCount(src));
    }

    /* Step 2: Apply pixel-space mask */
    if (apply_mask(src, dataset->mask, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
        info("    Masked: %dx%d, %d bands",
             GDALGetRasterXSize(src), GDALGetRasterYSize(src), GDALGetRasterCount(src));
    }

    /* Step 3: Apply GCPs if present */
    if (apply_gcps(src, dataset->gcps, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
    }

    /* Step 4: Warp to EPSG:3857 */
    if (warp_to_mercator(src, resolution, num_threads, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    GDALClose(src);
    src = tmp;

    info("    Warped: %dx%d",
         GDALGetRasterXSize(src), GDALGetRasterYSize(src));

    /* Step 5: Clip to geographic bounds */
    if (clip_to_bounds(src, dataset->geobound, &tmp) != 0) {
        GDALClose(src);
        return -1;
    }
    if (tmp) {
        GDALClose(src);
        src = tmp;
        info("    Clipped: %dx%d",
             GDALGetRasterXSize(src), GDALGetRasterYSize(src));
    }

    /* Step 6: Save to output file */
    info("    Saving to %s...", outpath);
    int result = save_to_file(src, outpath);
    GDALClose(src);

    return result;
}

/* ============================================================================
 * VRT Building
 * ============================================================================ */

int build_vrt(const char *outpath, const char **input_files, int file_count) {
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
