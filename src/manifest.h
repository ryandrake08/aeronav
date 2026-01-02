/*
 * manifest.h - Tile manifest for Strategy 3 tile generation
 *
 * Computes which tiles should be generated for a tileset based on
 * dataset coverage and max_lod constraints. Each dataset only contributes
 * tiles up to its max_lod.
 */

#ifndef MANIFEST_H
#define MANIFEST_H

#include <stdint.h>
#include "aeronav.h"

/* Packed tile coordinate for efficient storage and binary search */
typedef uint32_t PackedTile;  /* x in upper 16 bits, y in lower 16 bits */

/* Tiles for a single zoom level */
typedef struct {
    PackedTile *tiles;  /* Sorted array for binary search */
    int count;
    int capacity;
} ZoomTileSet;

/* Complete manifest for a tileset */
typedef struct {
    ZoomTileSet *zooms;  /* Array indexed by (zoom - min_zoom) */
    int min_zoom;
    int max_zoom;
} TileManifest;

/*
 * Build a tile manifest for a tileset.
 *
 * For each dataset in the tileset:
 *   - Reads bounds from reprojected TIF at tmppath
 *   - Adds tiles covering those bounds for zoom levels up to dataset's max_lod
 *
 * Returns newly allocated manifest, or NULL on error.
 * Caller must free with free_tile_manifest().
 */
TileManifest *build_tile_manifest(const Tileset *tileset, const char *tmppath);

/*
 * Check if a tile is in the manifest.
 *
 * Returns true if the tile (z, x, y) should be generated.
 */
bool manifest_contains(const TileManifest *m, int z, int x, int y);

/*
 * Get total tile count in manifest.
 */
int manifest_tile_count(const TileManifest *m);

/*
 * Free a tile manifest.
 */
void free_tile_manifest(TileManifest *m);

#endif /* MANIFEST_H */
