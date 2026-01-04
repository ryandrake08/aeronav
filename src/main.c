/*
 * aeronav2tiles - Convert FAA Aeronav charts to web map tiles
 *
 * Main entry point and command-line parsing.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <getopt.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
#include <dirent.h>

#include "aeronav.h"

/* Maximum tilesets that can be specified on command line */
#define MAX_TILESETS 32

/* Global quiet flag for info() */
static bool g_quiet = false;

void error(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    fprintf(stderr, "Error: ");
    vfprintf(stderr, fmt, args);
    fprintf(stderr, "\n");
    va_end(args);
}

void info(const char *fmt, ...) {
    if (g_quiet) return;
    va_list args;
    va_start(args, fmt);
    vprintf(fmt, args);
    printf("\n");
    va_end(args);
}

static void print_usage(const char *prog) {
    printf("Usage: %s [options]\n", prog);
    printf("\n");
    printf("Convert FAA Aeronav charts to web map tiles.\n");
    printf("\n");
    printf("Options:\n");
    printf("  -c, --config <path>  Config file (default: aeronav.conf.json)\n");
    printf("  -z, --zippath <path> Directory containing ZIP files\n");
    printf("  -t, --tmppath <path> Temp directory (default: /tmp/aeronav2tiles)\n");
    printf("  -o, --outpath <path> Output directory for tiles (if omitted, no tiles generated)\n");
    printf("  -s, --tilesets <names>  Comma-separated tileset names (default: all)\n");
    printf("  -l, --list           List available tilesets and exit\n");
    printf("  -C, --cleanup        Remove temp directory after processing\n");
    printf("  -T, --tile-only      Skip processing, reuse existing reprojected files\n");
    printf("  -e, --epsg <code>    Target EPSG code (default: 3857)\n");
    printf("  --reproject-resampling <method>  Resampling for reprojection (default: bilinear)\n");
    printf("  --tile-resampling <method>       Resampling for tile generation (default: bilinear)\n");
    printf("  -q, --quiet          Suppress progress output\n");
    printf("  -j, --jobs <N>       Concurrent dataset processes (default: auto)\n");
    printf("  -w, --tile-workers <N>  Tile generation workers (default: auto)\n");
    printf("  -f, --format <fmt>   Tile format: png, jpeg, webp (default: webp)\n");
    printf("  -h, --help           Show this help message\n");
    printf("\n");
    printf("Resampling methods: nearest, bilinear, cubic, cubicspline, lanczos, average, mode\n");
    printf("\n");
    printf("Examples:\n");
    printf("  %s -z ./zips -o ./tiles\n", prog);
    printf("  %s -s sec,tac -z ./zips -o ./tiles\n", prog);
    printf("  %s -z ./zips                       # Process only, no tile generation\n", prog);
}

static void list_tilesets(void) {
    int count;
    const char **names = get_all_tileset_names(&count);
    printf("Available tilesets:\n");
    for (int i = 0; i < count; i++) {
        const Tileset *ts = get_tileset(names[i]);
        if (ts) {
            printf("  %-40s (%s, zoom %d-%d)\n",
                   ts->name, ts->tile_path, ts->zoom_min, ts->zoom_max);
        }
    }
}

static int get_cpu_count(void) {
    long count = sysconf(_SC_NPROCESSORS_ONLN);
    if (count < 1) {
        error("sysconf(_SC_NPROCESSORS_ONLN) failed, defaulting to 1 CPU");
        return 1;
    }
    return (int)count;
}

int mkdir_p(const char *path) {
    char tmp[PATH_SIZE];
    snprintf(tmp, sizeof(tmp), "%s", path);

    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (mkdir(tmp, 0755) != 0 && errno != EEXIST) {
                error("Failed to create directory: %s", tmp);
                return -1;
            }
            *p = '/';
        }
    }
    if (mkdir(tmp, 0755) != 0 && errno != EEXIST) {
        error("Failed to create directory: %s", tmp);
        return -1;
    }
    return 0;
}

static int rmdir_r(const char *path) {
    DIR *dir = opendir(path);
    if (!dir) {
        return (errno == ENOENT) ? 0 : -1;
    }

    struct dirent *entry;
    char child[PATH_SIZE];

    while ((entry = readdir(dir)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
            continue;
        }

        snprintf(child, sizeof(child), "%s/%s", path, entry->d_name);

        struct stat st;
        if (lstat(child, &st) != 0) {
            closedir(dir);
            return -1;
        }

        if (S_ISDIR(st.st_mode)) {
            if (rmdir_r(child) != 0) {
                closedir(dir);
                return -1;
            }
        } else {
            if (unlink(child) != 0) {
                closedir(dir);
                return -1;
            }
        }
    }

    closedir(dir);
    return rmdir(path);
}

int main(int argc, char *argv[]) {
    Options opts = {
        .zippath = NULL,
        .outpath = NULL,
        .tmppath = "/tmp/aeronav2tiles",
        .format = "png",
        .reproject_resampling = "bilinear",
        .tile_resampling = "bilinear",
        .tilesets = NULL,
        .tileset_count = 0,
        .jobs = 0,
        .tile_workers = 0,
        .epsg = 3857,
        .quiet = false,
        .cleanup = false,
        .tile_only = false,
    };
    const char *config_path = "aeronav.conf.json";
    bool do_list = false;

    static struct option long_options[] = {
        {"config",              required_argument, 0, 'c'},
        {"zippath",             required_argument, 0, 'z'},
        {"tmppath",             required_argument, 0, 't'},
        {"outpath",             required_argument, 0, 'o'},
        {"tilesets",            required_argument, 0, 's'},
        {"format",              required_argument, 0, 'f'},
        {"jobs",                required_argument, 0, 'j'},
        {"tile-workers",        required_argument, 0, 'w'},
        {"epsg",                required_argument, 0, 'e'},
        {"reproject-resampling", required_argument, 0, 'R'},
        {"tile-resampling",     required_argument, 0, 'S'},
        {"cleanup",             no_argument,       0, 'C'},
        {"tile-only",           no_argument,       0, 'T'},
        {"quiet",               no_argument,       0, 'q'},
        {"list",                no_argument,       0, 'l'},
        {"help",                no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "c:z:t:o:s:f:j:w:e:R:S:CTqlh", long_options, NULL)) != -1) {
        switch (opt) {
            case 'o':
                opts.outpath = optarg;
                break;
            case 'c':
                config_path = optarg;
                break;
            case 'z':
                opts.zippath = optarg;
                break;
            case 't':
                opts.tmppath = optarg;
                break;
            case 's':
                /* Parse comma-separated tilesets */
                {
                    static const char *tileset_args[MAX_TILESETS];
                    opts.tilesets = tileset_args;
                    opts.tileset_count = 0;

                    char *token = strtok(optarg, ",");
                    while (token && opts.tileset_count < MAX_TILESETS) {
                        opts.tilesets[opts.tileset_count++] = token;
                        token = strtok(NULL, ",");
                    }
                }
                break;
            case 'f':
                opts.format = optarg;
                break;
            case 'j':
                opts.jobs = atoi(optarg);
                break;
            case 'w':
                opts.tile_workers = atoi(optarg);
                break;
            case 'e':
                opts.epsg = atoi(optarg);
                break;
            case 'R':
                opts.reproject_resampling = optarg;
                break;
            case 'S':
                opts.tile_resampling = optarg;
                break;
            case 'C':
                opts.cleanup = true;
                break;
            case 'T':
                opts.tile_only = true;
                break;
            case 'q':
                opts.quiet = true;
                g_quiet = true;
                break;
            case 'l':
                do_list = true;
                break;
            case 'h':
                print_usage(argv[0]);
                return 0;
            default:
                print_usage(argv[0]);
                return 1;
        }
    }

    /* Load config file */
    if (config_init(config_path) != 0) {
        return 1;
    }

    /* Handle --list option */
    if (do_list) {
        list_tilesets();
        return 0;
    }

    /* Set defaults based on CPU count */
    int cpu_count = get_cpu_count();
    if (opts.jobs == 0) {
        opts.jobs = cpu_count > 4 ? 4 : cpu_count;  /* Default: 4 concurrent datasets */
    }
    int threads_per_job = cpu_count / opts.jobs;
    if (threads_per_job < 1) threads_per_job = 1;
    if (opts.tile_workers == 0) {
        opts.tile_workers = cpu_count;
    }

    info("aeronav2tiles - FAA chart tile generator");
    info("  zippath: %s", opts.zippath ? opts.zippath : "(none - datasets will not be processed)");
    info("  outpath: %s", opts.outpath ? opts.outpath : "(none - tiles will not be generated)");
    info("  tmppath: %s", opts.tmppath);
    info("  CPUs: %d, jobs: %d, threads/job: %d, tile workers: %d",
         cpu_count, opts.jobs, threads_per_job, opts.tile_workers);

    /* Ensure output and temp directories exist */
    if (opts.outpath && mkdir_p(opts.outpath) != 0) return 1;
    if (mkdir_p(opts.tmppath) != 0) return 1;

    /* Get tilesets to process */
    int tileset_count;
    const char **tileset_names;

    if (opts.tileset_count > 0) {
        tileset_names = opts.tilesets;
        tileset_count = opts.tileset_count;
    } else {
        tileset_names = get_all_tileset_names(&tileset_count);
    }

    /* Validate and collect tilesets */
    const Tileset **tilesets = malloc(tileset_count * sizeof(Tileset *));
    if (!tilesets) {
        error("Failed to allocate tileset array");
        return 1;
    }

    int valid_tileset_count = 0;
    int total_datasets = 0;

    for (int i = 0; i < tileset_count; i++) {
        const Tileset *ts = get_tileset(tileset_names[i]);
        if (!ts) {
            error("Unknown tileset: %s", tileset_names[i]);
            continue;
        }
        tilesets[valid_tileset_count++] = ts;
        total_datasets += ts->dataset_count;
    }

    if (valid_tileset_count == 0) {
        error("No valid tilesets to process");
        free(tilesets);
        return 1;
    }

    info("Processing %d tileset(s) with %d total dataset(s)...",
         valid_tileset_count, total_datasets);

    if (opts.zippath && !opts.tile_only) {
        if (process_datasets_parallel(
                tilesets,
                valid_tileset_count,
                opts.zippath,
                opts.tmppath,
                opts.jobs,
                threads_per_job,
                opts.epsg,
                opts.reproject_resampling) != 0) {
            error("Dataset processing had failures");
        }
    }

    /* Generate tiles for all tilesets */
    if (opts.outpath) {
        generate_tileset_tiles_parallel(
            tilesets,
            valid_tileset_count,
            opts.tmppath,
            opts.outpath,
            opts.format,
            opts.tile_resampling,
            opts.tile_workers
        );
    }

    free(tilesets);

    /* Cleanup temp directory if requested */
    if (opts.cleanup) {
        info("Cleaning up temp directory: %s", opts.tmppath);
        if (rmdir_r(opts.tmppath) != 0) {
            error("Failed to remove temp directory: %s", opts.tmppath);
        }
    }

    info("\nDone.");
    return 0;
}
