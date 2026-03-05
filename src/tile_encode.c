/*
 * tile_encode - Direct tile encoding via libpng/libjpeg/libwebp
 *
 * Bypasses GDAL's generic I/O layer to encode RGBA tile buffers directly,
 * eliminating MEM dataset creation, band shuffling, and float promotion.
 */

#include <stdio.h>
#include <string.h>

#include <jpeglib.h>
#include <png.h>
#include <webp/encode.h>

#include "tile_encode.h"

/* --------------------------------------------------------------------------
 * PNG encoder
 * -------------------------------------------------------------------------- */

static int encode_png(const unsigned char *tile_data, const char *file_path) {
    FILE *fp = fopen(file_path, "wb");
    if (!fp) return -1;

    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    if (!png) {
        fclose(fp);
        return -1;
    }

    png_infop info = png_create_info_struct(png);
    if (!info) {
        png_destroy_write_struct(&png, NULL);
        fclose(fp);
        return -1;
    }

    if (setjmp(png_jmpbuf(png))) {
        png_destroy_write_struct(&png, &info);
        fclose(fp);
        return -1;
    }

    png_init_io(png, fp);
    png_set_compression_level(png, 6);
    png_set_filter(png, 0, PNG_FILTER_NONE);

    png_set_IHDR(png, info, TILE_ENCODE_SIZE, TILE_ENCODE_SIZE, 8, PNG_COLOR_TYPE_RGBA, PNG_INTERLACE_NONE,
                 PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png, info);

    for (int y = 0; y < TILE_ENCODE_SIZE; y++) {
        png_write_row(png, tile_data + (size_t)y * TILE_ENCODE_SIZE * 4);
    }

    png_write_end(png, NULL);
    png_destroy_write_struct(&png, &info);
    fclose(fp);
    return 0;
}

/* --------------------------------------------------------------------------
 * JPEG encoder
 * -------------------------------------------------------------------------- */

static int encode_jpeg(const unsigned char *tile_data, const char *file_path) {
    FILE *fp = fopen(file_path, "wb");
    if (!fp) return -1;

    struct jpeg_compress_struct cinfo;
    struct jpeg_error_mgr jerr;

    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_compress(&cinfo);
    jpeg_stdio_dest(&cinfo, fp);

    cinfo.image_width = TILE_ENCODE_SIZE;
    cinfo.image_height = TILE_ENCODE_SIZE;
    cinfo.input_components = 3;
    cinfo.in_color_space = JCS_RGB;

    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, 85, TRUE);
    jpeg_start_compress(&cinfo, TRUE);

    /* Strip alpha channel per row */
    unsigned char row_buf[TILE_ENCODE_SIZE * 3];

    for (int y = 0; y < TILE_ENCODE_SIZE; y++) {
        const unsigned char *src = tile_data + (size_t)y * TILE_ENCODE_SIZE * 4;
        for (int x = 0; x < TILE_ENCODE_SIZE; x++) {
            row_buf[x * 3 + 0] = src[x * 4 + 0];
            row_buf[x * 3 + 1] = src[x * 4 + 1];
            row_buf[x * 3 + 2] = src[x * 4 + 2];
        }
        unsigned char *row_ptr = row_buf;
        jpeg_write_scanlines(&cinfo, &row_ptr, 1);
    }

    jpeg_finish_compress(&cinfo);
    jpeg_destroy_compress(&cinfo);
    fclose(fp);
    return 0;
}

/* --------------------------------------------------------------------------
 * WebP encoder
 * -------------------------------------------------------------------------- */

static int encode_webp(const unsigned char *tile_data, const char *file_path) {
    uint8_t *output = NULL;
    size_t size = WebPEncodeRGBA(tile_data, TILE_ENCODE_SIZE, TILE_ENCODE_SIZE, TILE_ENCODE_SIZE * 4, 75.0F, &output);
    if (size == 0) return -1;

    FILE *fp = fopen(file_path, "wb");
    if (!fp) {
        WebPFree(output);
        return -1;
    }

    size_t written = fwrite(output, 1, size, fp);
    fclose(fp);
    WebPFree(output);

    return (written == size) ? 0 : -1;
}

/* --------------------------------------------------------------------------
 * Format lookup
 * -------------------------------------------------------------------------- */

tile_encode_fn tile_encode_get(const char *format) {
    if (strcmp(format, "png") == 0) return encode_png;
    if (strcmp(format, "jpeg") == 0) return encode_jpeg;
    if (strcmp(format, "webp") == 0) return encode_webp;
    return NULL;
}
