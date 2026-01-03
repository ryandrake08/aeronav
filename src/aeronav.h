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
#define MAX_TILESETS 32       /* Max tilesets on command line */
#define MAX_JOBS 64           /* Max parallel jobs/workers */
#define MAX_GCPS 16           /* Max GCPs per dataset */

/* ============================================================================
 * Constants
 * ============================================================================ */

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

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
    int max_lod;          /* Max level of detail (determines resolution) */
} Dataset;

/* Tileset definition */
typedef struct {
    char *name;           /* Tileset name (e.g., "VFR Sectional Charts") */
    char *tile_path;      /* Output subdirectory (e.g., "sec") */
    int zoom_min;               /* Minimum zoom level */
    int zoom_max;               /* Maximum zoom level */
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
    bool cleanup;               /* Remove tmppath after processing */
    bool tile_only;             /* Skip processing, use existing reprojected files */
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
 * Process all datasets from the given tilesets in parallel.
 *
 * Collects all datasets across all tilesets, creates processing jobs,
 * and executes them using a parallel worker pool.
 *
 * Returns 0 on success (all jobs completed), -1 on error.
 */
int process_datasets_parallel(
    const Tileset **tilesets,
    int tileset_count,
    const char *zippath,
    const char *tmppath,
    int num_workers,
    int threads_per_job,
    int epsg,
    const char *resampling
);

/* ============================================================================
 * VRT Building (vrt.c)
 * ============================================================================ */

/*
 * Build a zoom-specific VRT for a tileset.
 *
 * Includes only datasets where max_lod >= zoom, ordered by max_lod
 * descending so that smaller max_lod datasets (more appropriate for
 * this zoom level) appear last and render on top.
 *
 * vrt_path_out must be at least PATH_SIZE bytes.
 *
 * Returns 0 on success, -1 on error or if no datasets qualify.
 */
int build_zoom_vrt(
    const Tileset *tileset,
    int zoom,
    const char *tmppath,
    char *vrt_path_out
);

/* ============================================================================
 * Tile Generation (tiling.c)
 * ============================================================================ */

/*
 * Calculate resolution (meters/pixel) for a given zoom level in EPSG:3857
 */
double resolution_for_zoom(int zoom);

/*
 * Generate tiles for all tilesets using parallel workers.
 *
 * For each tileset, opens the VRT at {tmppath}/__{tileset_name}.vrt,
 * determines tiles to generate, and generates them using worker processes.
 *
 * Returns 0 on success, -1 on error.
 */
int generate_tileset_tiles_parallel(
    const Tileset **tilesets,
    int tileset_count,
    const char *tmppath,
    const char *outpath,
    const char *format,
    const char *resampling,
    int num_workers
);

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
