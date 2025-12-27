/*
 * aeronav2tiles - Convert FAA Aeronav charts to web map tiles
 *
 * Header file with type definitions and function declarations.
 */

#ifndef AERONAV_H
#define AERONAV_H

#include <stdbool.h>

/* ============================================================================
 * Limits
 * ============================================================================ */
#define MAX_DATASETS 128      /* Max datasets per tileset */
#define MAX_TILESETS 32       /* Max tilesets on command line */
#define MAX_JOBS 64           /* Max parallel jobs/workers */
#define MAX_GCPS 16           /* Max GCPs per dataset */

/* ============================================================================
 * Constants
 * ============================================================================ */

#define PATH_SIZE 512
#define TILE_SIZE 256
#define EARTH_RADIUS 6378137.0
#define ORIGIN_SHIFT 20037508.342789244  /* pi * EARTH_RADIUS */

/* ============================================================================
 * Data Structures
 * ============================================================================ */

/* Ground Control Point for georeferencing insets */
typedef struct {
    double pixel_x;
    double pixel_y;
    double lon;
    double lat;
} ControlPoint;

/* Set of ground control points */
typedef struct {
    ControlPoint *points;
    int count;
} GCP;

/* Polygon vertex for masking */
typedef struct {
    double x;
    double y;
} Vertex;

/* Polygon ring (for masks with holes) */
typedef struct {
    Vertex *vertices;
    int count;
} Ring;

/* Polygon mask (outer ring + optional holes) */
typedef struct {
    Ring *rings;      /* First ring is outer boundary (CCW), rest are holes (CW) */
    int count;
} Mask;

/* Geographic bounds for post-projection clipping */
typedef struct {
    double lon_min;   /* Use NAN for no constraint */
    double lat_min;
    double lon_max;
    double lat_max;
} GeoBounds;

/* Dataset definition */
typedef struct {
    char *name;           /* Dataset name (e.g., "Seattle SEC") */
    char *zip_file;       /* ZIP filename without .zip (e.g., "Seattle") */
    char *input_file;     /* TIF filename inside ZIP, or NULL to use name.tif */
    char *tmp_file;       /* Temp filename (e.g., "_Seattle_SEC.tif") */
    Mask *mask;           /* Pixel-space mask, or NULL */
    GeoBounds *geobound;  /* Geographic clip bounds, or NULL */
    GCP *gcps;            /* Ground control points, or NULL */
} Dataset;

/* Tileset definition */
typedef struct {
    char *name;           /* Tileset name (e.g., "VFR Sectional Charts") */
    char *tile_path;      /* Output subdirectory (e.g., "sec") */
    int zoom_min;               /* Minimum zoom level */
    int zoom_max;               /* Maximum zoom level */
    int maxlod_zoom;            /* Max level of detail (determines resolution) */
    char **datasets;      /* Array of dataset names */
    int dataset_count;          /* Number of datasets */
} Tileset;

/* Command-line options */
typedef struct {
    const char *zippath;        /* Directory containing ZIP files */
    const char *outpath;        /* Output directory for tiles */
    const char *tmppath;        /* Temporary directory */
    const char *format;         /* Tile format: png, jpeg, webp */
    const char *reproject_resampling;  /* Resampling for reprojection */
    const char *tile_resampling;       /* Resampling for tile generation */
    const char **tilesets;      /* Specific tilesets to process, or NULL for all */
    int tileset_count;
    int jobs;                   /* Concurrent dataset processes */
    int tile_workers;           /* Tile generation workers */
    int epsg;                   /* Target EPSG code (default 3857) */
    bool quiet;                 /* Suppress output */
    bool resume;                /* Skip existing tiles */
    bool cleanup;               /* Remove tmppath after processing */
} Options;

/* ============================================================================
 * Config Loading (config.c)
 * ============================================================================ */

/* Initialize config from JSON file - must be called before other config functions */
int config_init(const char *config_path);

/* Get dataset definition by name, returns NULL if not found */
const Dataset *get_dataset(const char *name);

/* Get tileset definition by name, returns NULL if not found */
const Tileset *get_tileset(const char *name);

/* Get all tileset names */
const char **get_all_tileset_names(int *count);

/* ============================================================================
 * Processing Functions (processing.c)
 * ============================================================================ */

/*
 * Process a single dataset through the full pipeline:
 * 1. Open from ZIP via /vsizip/
 * 2. Expand palette to RGB if needed
 * 3. Apply pixel-space mask
 * 4. Apply GCPs if provided
 * 5. Warp to target EPSG at specified resolution
 * 6. Clip to geographic bounds if specified
 * 7. Save to output file
 *
 * Returns 0 on success, -1 on error.
 */
int process_dataset(const char *zippath,
                    const Dataset *dataset,
                    double resolution,
                    const char *outpath,
                    int num_threads,
                    int epsg,
                    const char *resampling);

/* ============================================================================
 * VRT Building (processing.c)
 * ============================================================================ */

/*
 * Build a VRT from multiple input files and save to outpath.
 * Returns 0 on success, -1 on error.
 */
int build_vrt(const char *outpath, const char **input_files, int file_count);

/* ============================================================================
 * Tile Generation (tiling.c)
 * ============================================================================ */

/*
 * Calculate resolution (meters/pixel) for a given zoom level in EPSG:3857
 */
double resolution_for_zoom(int zoom);

/*
 * Generate tiles from a file path using parallel workers.
 */
int generate_tiles(const char *src_path,
                   const char *outpath,
                   const char *tile_path,
                   int zoom_min,
                   int zoom_max,
                   const char *format,
                   const char *resampling,
                   int num_workers,
                   bool resume);

/* ============================================================================
 * Utility Functions
 * ============================================================================ */

/* Print error message to stderr */
void error(const char *fmt, ...);

/* Print message if not quiet */
void info(const char *fmt, ...);

/* Create directory and parents (like mkdir -p) */
int mkdir_p(const char *path);

#endif /* AERONAV_H */
