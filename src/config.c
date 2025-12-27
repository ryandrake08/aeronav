/*
 * config.c - Load dataset and tileset definitions from datasets.json
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "aeronav.h"
#include "cJSON.h"

/* Storage for loaded config */
static Dataset *g_datasets = NULL;
static int g_dataset_count = 0;
static Tileset *g_tilesets = NULL;
static int g_tileset_count = 0;
static int g_initialized = 0;

/* Helper to duplicate a string */
static char *strdup_safe(const char *s) {
    if (!s) return NULL;
    char *dup = malloc(strlen(s) + 1);
    if (dup) strcpy(dup, s);
    return dup;
}

/* Helper to create tmp_file name from dataset name */
static char *make_tmp_file(const char *name) {
    size_t len = strlen(name) + 6;  /* "_" + name + ".tif" + null */
    char *tmp = malloc(len);
    if (!tmp) return NULL;
    snprintf(tmp, len, "_%s.tif", name);
    return tmp;
}

/* Parse a mask from JSON */
static Mask *parse_mask(cJSON *mask_json) {
    if (!mask_json || !cJSON_IsArray(mask_json)) return NULL;

    int ring_count = cJSON_GetArraySize(mask_json);
    if (ring_count == 0) return NULL;

    Mask *mask = malloc(sizeof(Mask));
    if (!mask) return NULL;

    mask->rings = malloc(ring_count * sizeof(Ring));
    if (!mask->rings) {
        free(mask);
        return NULL;
    }
    mask->count = ring_count;

    int r = 0;
    cJSON *ring_json;
    cJSON_ArrayForEach(ring_json, mask_json) {
        int vertex_count = cJSON_GetArraySize(ring_json);
        mask->rings[r].vertices = malloc(vertex_count * sizeof(Vertex));
        if (!mask->rings[r].vertices) {
            /* Free previously allocated rings */
            for (int i = 0; i < r; i++) {
                free(mask->rings[i].vertices);
            }
            free(mask->rings);
            free(mask);
            return NULL;
        }
        mask->rings[r].count = vertex_count;

        int v = 0;
        cJSON *vertex_json;
        cJSON_ArrayForEach(vertex_json, ring_json) {
            cJSON *x = cJSON_GetArrayItem(vertex_json, 0);
            cJSON *y = cJSON_GetArrayItem(vertex_json, 1);
            mask->rings[r].vertices[v].x = cJSON_GetNumberValue(x);
            mask->rings[r].vertices[v].y = cJSON_GetNumberValue(y);
            v++;
        }
        r++;
    }

    return mask;
}

/* Parse a geobound from JSON */
static GeoBounds *parse_geobound(cJSON *gb_json) {
    if (!gb_json || !cJSON_IsArray(gb_json)) return NULL;

    GeoBounds *gb = malloc(sizeof(GeoBounds));
    if (!gb) return NULL;

    cJSON *lon_min = cJSON_GetArrayItem(gb_json, 0);
    cJSON *lat_min = cJSON_GetArrayItem(gb_json, 1);
    cJSON *lon_max = cJSON_GetArrayItem(gb_json, 2);
    cJSON *lat_max = cJSON_GetArrayItem(gb_json, 3);

    gb->lon_min = cJSON_IsNull(lon_min) ? NAN : cJSON_GetNumberValue(lon_min);
    gb->lat_min = cJSON_IsNull(lat_min) ? NAN : cJSON_GetNumberValue(lat_min);
    gb->lon_max = cJSON_IsNull(lon_max) ? NAN : cJSON_GetNumberValue(lon_max);
    gb->lat_max = cJSON_IsNull(lat_max) ? NAN : cJSON_GetNumberValue(lat_max);

    return gb;
}

/* Parse GCPs from JSON */
static GCP *parse_gcps(cJSON *gcps_json) {
    if (!gcps_json || !cJSON_IsArray(gcps_json)) return NULL;

    int count = cJSON_GetArraySize(gcps_json);
    if (count == 0) return NULL;

    GCP *gcp = malloc(sizeof(GCP));
    if (!gcp) return NULL;

    gcp->points = malloc(count * sizeof(ControlPoint));
    if (!gcp->points) {
        free(gcp);
        return NULL;
    }
    gcp->count = count;

    int i = 0;
    cJSON *pt_json;
    cJSON_ArrayForEach(pt_json, gcps_json) {
        gcp->points[i].pixel_x = cJSON_GetNumberValue(cJSON_GetArrayItem(pt_json, 0));
        gcp->points[i].pixel_y = cJSON_GetNumberValue(cJSON_GetArrayItem(pt_json, 1));
        gcp->points[i].lon = cJSON_GetNumberValue(cJSON_GetArrayItem(pt_json, 2));
        gcp->points[i].lat = cJSON_GetNumberValue(cJSON_GetArrayItem(pt_json, 3));
        i++;
    }

    return gcp;
}

/* Load config from JSON file */
static int load_config(const char *config_path) {
    /* Read file */
    FILE *f = fopen(config_path, "rb");
    if (!f) {
        error("Failed to open config file: %s", config_path);
        return -1;
    }

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);

    char *json_str = malloc(len + 1);
    if (!json_str) {
        fclose(f);
        error("Failed to allocate memory for config file");
        return -1;
    }

    size_t bytes_read = fread(json_str, 1, len, f);
    fclose(f);

    if (bytes_read != (size_t)len) {
        error("Failed to read config file");
        free(json_str);
        return -1;
    }
    json_str[len] = '\0';

    /* Parse JSON */
    cJSON *root = cJSON_Parse(json_str);
    free(json_str);

    if (!root) {
        error("Failed to parse config JSON: %s", cJSON_GetErrorPtr());
        return -1;
    }

    /* Parse datasets */
    cJSON *datasets_json = cJSON_GetObjectItem(root, "datasets");
    if (!datasets_json) {
        error("Config missing 'datasets' object");
        cJSON_Delete(root);
        return -1;
    }

    g_dataset_count = cJSON_GetArraySize(datasets_json);
    g_datasets = calloc(g_dataset_count, sizeof(Dataset));
    if (!g_datasets) {
        error("Failed to allocate datasets array");
        cJSON_Delete(root);
        return -1;
    }

    int i = 0;
    cJSON *ds_json;
    cJSON_ArrayForEach(ds_json, datasets_json) {
        Dataset *ds = &g_datasets[i];

        ds->name = strdup_safe(ds_json->string);
        ds->tmp_file = make_tmp_file(ds_json->string);

        cJSON *zip_file = cJSON_GetObjectItem(ds_json, "zip_file");
        ds->zip_file = strdup_safe(cJSON_GetStringValue(zip_file));

        cJSON *input_file = cJSON_GetObjectItem(ds_json, "input_file");
        if (input_file && cJSON_IsString(input_file)) {
            ds->input_file = strdup_safe(cJSON_GetStringValue(input_file));
        } else {
            /* Default: name + .tif */
            size_t len = strlen(ds->name) + 5;
            char *def = malloc(len);
            if (!def) {
                error("Failed to allocate input_file for dataset %s", ds->name);
                cJSON_Delete(root);
                return -1;
            }
            snprintf(def, len, "%s.tif", ds->name);
            ds->input_file = def;
        }

        ds->mask = parse_mask(cJSON_GetObjectItem(ds_json, "mask"));
        ds->geobound = parse_geobound(cJSON_GetObjectItem(ds_json, "geobound"));
        ds->gcps = parse_gcps(cJSON_GetObjectItem(ds_json, "gcps"));

        i++;
    }

    /* Parse tilesets */
    cJSON *tilesets_json = cJSON_GetObjectItem(root, "tilesets");
    if (!tilesets_json) {
        error("Config missing 'tilesets' object");
        cJSON_Delete(root);
        return -1;
    }

    g_tileset_count = cJSON_GetArraySize(tilesets_json);
    g_tilesets = calloc(g_tileset_count, sizeof(Tileset));
    if (!g_tilesets) {
        error("Failed to allocate tilesets array");
        cJSON_Delete(root);
        return -1;
    }

    i = 0;
    cJSON *ts_json;
    cJSON_ArrayForEach(ts_json, tilesets_json) {
        Tileset *ts = &g_tilesets[i];

        ts->name = strdup_safe(ts_json->string);

        cJSON *tile_path = cJSON_GetObjectItem(ts_json, "tile_path");
        ts->tile_path = strdup_safe(cJSON_GetStringValue(tile_path));

        cJSON *zoom = cJSON_GetObjectItem(ts_json, "zoom");
        ts->zoom_min = (int)cJSON_GetNumberValue(cJSON_GetArrayItem(zoom, 0));
        ts->zoom_max = (int)cJSON_GetNumberValue(cJSON_GetArrayItem(zoom, 1));

        cJSON *maxlod = cJSON_GetObjectItem(ts_json, "maxlod_zoom");
        ts->maxlod_zoom = (int)cJSON_GetNumberValue(maxlod);

        cJSON *ds_array = cJSON_GetObjectItem(ts_json, "datasets");
        ts->dataset_count = cJSON_GetArraySize(ds_array);
        ts->datasets = malloc(ts->dataset_count * sizeof(char *));
        if (!ts->datasets) {
            error("Failed to allocate datasets array for tileset %s", ts->name);
            cJSON_Delete(root);
            return -1;
        }

        int j = 0;
        cJSON *ds_name;
        cJSON_ArrayForEach(ds_name, ds_array) {
            ts->datasets[j++] = strdup_safe(cJSON_GetStringValue(ds_name));
        }

        i++;
    }

    cJSON_Delete(root);
    g_initialized = 1;
    return 0;
}

/* Initialize config - call once at startup */
int config_init(const char *config_path) {
    if (g_initialized) return 0;
    return load_config(config_path);
}

/* Lookup functions */
const Dataset *get_dataset(const char *name) {
    for (int i = 0; i < g_dataset_count; i++) {
        if (strcmp(g_datasets[i].name, name) == 0) {
            return &g_datasets[i];
        }
    }
    return NULL;
}

const Tileset *get_tileset(const char *name) {
    for (int i = 0; i < g_tileset_count; i++) {
        if (strcmp(g_tilesets[i].name, name) == 0 ||
            strcmp(g_tilesets[i].tile_path, name) == 0) {
            return &g_tilesets[i];
        }
    }
    return NULL;
}

const char **get_all_tileset_names(int *count) {
    static const char **names = NULL;

    if (!names) {
        names = malloc(g_tileset_count * sizeof(char *));
        if (!names) {
            *count = 0;
            return NULL;
        }
        for (int i = 0; i < g_tileset_count; i++) {
            names[i] = g_tilesets[i].name;
        }
    }

    *count = g_tileset_count;
    return names;
}
