/*
 * tile_encode - Direct tile encoding via libpng/libjpeg/libwebp
 *
 * Encodes raw RGBA tile buffers to image files without GDAL overhead.
 */

#ifndef TILE_ENCODE_H
#define TILE_ENCODE_H

#define TILE_ENCODE_SIZE 256

/*
 * Tile encoding function type.
 * Encodes a 256×256 RGBA pixel buffer to an image file.
 *
 * Parameters:
 *   tile_data  - 256×256×4 byte RGBA buffer (row-major, top-to-bottom)
 *   file_path  - Output file path
 *
 * Returns 0 on success, -1 on error.
 */
typedef int (*tile_encode_fn)(const unsigned char *tile_data, const char *file_path);

/*
 * Get the encoder function for a given format.
 *
 * Parameters:
 *   format  - "png", "jpeg", or "webp"
 *
 * Returns encoder function pointer, or NULL if format is unknown.
 */
tile_encode_fn tile_encode_get(const char *format);

#endif /* TILE_ENCODE_H */
