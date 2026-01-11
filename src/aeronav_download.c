/*
 * aeronav_download.c - Download FAA Aeronav chart data
 *
 * Downloads current GeoTIFF chart ZIP files from aeronav.faa.gov.
 * Supports incremental updates via If-Modified-Since headers.
 *
 * Dependencies: libcurl, libxml2
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <unistd.h>
#include <sys/stat.h>
#include <time.h>
#include <getopt.h>
#include <errno.h>
#include <ctype.h>

#include <curl/curl.h>
#include <libxml/HTMLparser.h>
#include <libxml/xpath.h>

/* FAA Aeronav index URLs */
#define AERONAV_VFR_URL "https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/vfr/"
#define AERONAV_IFR_URL "https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/ifr/"

/* Maximum number of URLs we can handle */
#define MAX_URLS 256

/* Chart type div IDs on the FAA website */
static const char *VFR_CHART_TYPES[] = {
    "sectional", "terminalArea", "helicopter", "grandCanyon", "Planning", "caribbean", NULL
};
static const char *IFR_CHART_TYPES[] = {
    "lowsHighsAreas", "planning", "caribbean", "gulf", NULL
};

/* Global quiet flag */
static bool quiet = false;

/* ============================================================================
 * Curl callback
 * ============================================================================ */

static size_t write_callback(void *contents, size_t size, size_t nmemb, void *userp) {
    return fwrite(contents, size, nmemb, (FILE *)userp);
}

/* ============================================================================
 * HTTP functions
 * ============================================================================ */

/*
 * Fetch a URL and return the response body.
 * Returns NULL on error. Caller must free the returned string.
 */
static char *fetch_url(CURL *curl, const char *url) {
    char *buf = NULL;
    size_t size = 0;
    FILE *stream = open_memstream(&buf, &size);
    if (!stream) {
        fprintf(stderr, "Error creating memory stream\n");
        return NULL;
    }

    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, stream);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "aeronav_download/1.0");

    CURLcode res = curl_easy_perform(curl);
    fclose(stream);

    if (res != CURLE_OK) {
        fprintf(stderr, "Error fetching %s: %s\n", url, curl_easy_strerror(res));
        free(buf);
        return NULL;
    }

    return buf;
}

/*
 * Download a file with If-Modified-Since support.
 * Returns: 1 = downloaded, 0 = not modified, -1 = error
 */
static int download_file(CURL *curl, const char *url, const char *filepath) {
    struct stat st;
    bool file_exists = (stat(filepath, &st) == 0);
    struct curl_slist *headers = NULL;

    /* Set If-Modified-Since header if file exists */
    if (file_exists) {
        char timebuf[64];
        struct tm *tm = gmtime(&st.st_mtime);
        strftime(timebuf, sizeof(timebuf), "If-Modified-Since: %a, %d %b %Y %H:%M:%S GMT", tm);
        headers = curl_slist_append(headers, timebuf);
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    }

    /* Create temp file for download */
    char tmppath[4096];
    snprintf(tmppath, sizeof(tmppath), "%s.tmp", filepath);
    FILE *fp = fopen(tmppath, "wb");
    if (!fp) {
        fprintf(stderr, "Error creating %s: %s\n", tmppath, strerror(errno));
        if (headers) curl_slist_free_all(headers);
        return -1;
    }

    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, fp);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "aeronav_download/1.0");

    CURLcode res = curl_easy_perform(curl);
    fclose(fp);

    if (headers) {
        curl_slist_free_all(headers);
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, NULL);
    }

    if (res != CURLE_OK) {
        fprintf(stderr, "Error downloading %s: %s\n", url, curl_easy_strerror(res));
        unlink(tmppath);
        return -1;
    }

    long response_code;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &response_code);

    if (response_code == 304) {
        /* Not modified */
        unlink(tmppath);
        return 0;
    } else if (response_code == 200) {
        /* Downloaded successfully - move temp file to final location */
        if (rename(tmppath, filepath) != 0) {
            fprintf(stderr, "Error moving %s to %s: %s\n", tmppath, filepath, strerror(errno));
            unlink(tmppath);
            return -1;
        }
        return 1;
    } else {
        fprintf(stderr, "Unexpected response code %ld for %s\n", response_code, url);
        unlink(tmppath);
        return -1;
    }
}

/* ============================================================================
 * HTML parsing
 * ============================================================================ */

/*
 * Check if a string matches case-insensitively, trimming whitespace.
 */
static bool text_matches(const char *text, const char *target) {
    if (!text) return false;

    /* Skip leading whitespace */
    while (*text && isspace((unsigned char)*text)) text++;

    /* Compare case-insensitively */
    size_t target_len = strlen(target);
    if (strncasecmp(text, target, target_len) != 0) return false;

    /* Check trailing is only whitespace */
    text += target_len;
    while (*text) {
        if (!isspace((unsigned char)*text)) return false;
        text++;
    }
    return true;
}

/*
 * Get text content of a node (concatenated text of all descendants).
 */
static char *get_node_text(xmlNodePtr node) {
    xmlChar *content = xmlNodeGetContent(node);
    if (!content) return NULL;
    char *result = strdup((char *)content);
    xmlFree(content);
    return result;
}

/*
 * Recursively find anchor tags with "Geo-TIFF" text and add their hrefs to the array.
 */
static void find_geotiff_links(xmlNodePtr node, char *urls[], int *count, int max) {
    for (xmlNodePtr child = node->children; child; child = child->next) {
        if (child->type == XML_ELEMENT_NODE) {
            if (xmlStrcmp(child->name, (xmlChar *)"a") == 0) {
                /* Check if link text is "Geo-TIFF" (case-insensitive) */
                char *text = get_node_text(child);
                if (text && text_matches(text, "Geo-TIFF")) {
                    xmlChar *href = xmlGetProp(child, (xmlChar *)"href");
                    if (href) {
                        if (*count < max) {
                            urls[(*count)++] = strdup((char *)href);
                        }
                        xmlFree(href);
                    }
                }
                free(text);
            } else {
                /* Recurse into other elements */
                find_geotiff_links(child, urls, count, max);
            }
        }
    }
}

/*
 * Scrape chart URLs from an FAA index page.
 */
static void scrape_chart_urls(const char *html, const char **chart_types,
                              char *urls[], int *count, int max) {
    /* Parse HTML */
    htmlDocPtr doc = htmlReadMemory(html, strlen(html), NULL, NULL,
                                     HTML_PARSE_NOERROR | HTML_PARSE_NOWARNING);
    if (!doc) {
        fprintf(stderr, "Error parsing HTML\n");
        return;
    }

    xmlXPathContextPtr ctx = xmlXPathNewContext(doc);
    if (!ctx) {
        xmlFreeDoc(doc);
        return;
    }

    /* For each chart type div */
    for (int i = 0; chart_types[i]; i++) {
        /* Find the div with this ID */
        char xpath[256];
        snprintf(xpath, sizeof(xpath), "//div[@id='%s']//table//tr", chart_types[i]);

        xmlXPathObjectPtr result = xmlXPathEvalExpression((xmlChar *)xpath, ctx);
        if (!result) continue;

        xmlNodeSetPtr nodes = result->nodesetval;
        if (!nodes) {
            xmlXPathFreeObject(result);
            continue;
        }

        /* For each table row */
        for (int j = 0; j < nodes->nodeNr; j++) {
            xmlNodePtr row = nodes->nodeTab[j];

            /* Find all td elements in this row */
            int td_count = 0;
            xmlNodePtr second_td = NULL;
            for (xmlNodePtr child = row->children; child; child = child->next) {
                if (child->type == XML_ELEMENT_NODE &&
                    xmlStrcmp(child->name, (xmlChar *)"td") == 0) {
                    td_count++;
                    if (td_count == 2) {
                        second_td = child;
                        break;
                    }
                }
            }

            if (!second_td) continue;

            /* Recursively find all Geo-TIFF links in the second td */
            find_geotiff_links(second_td, urls, count, max);
        }

        xmlXPathFreeObject(result);
    }

    xmlXPathFreeContext(ctx);
    xmlFreeDoc(doc);
}

/* ============================================================================
 * URL fixing
 * ============================================================================ */

/*
 * Find the date pattern in a URL (e.g., "/01-23-2025/").
 * Returns pointer to start of date, or NULL if not found.
 * Sets *end to point past the date.
 */
static const char *find_date_in_url(const char *url, const char **end) {
    /* Look for pattern: /DD-DD-DDDD/ */
    const char *p = url;
    while ((p = strchr(p, '/')) != NULL) {
        p++; /* Skip the slash */
        /* Check for DD-DD-DDDD pattern */
        if (strlen(p) >= 10 &&
            isdigit((unsigned char)p[0]) && isdigit((unsigned char)p[1]) &&
            p[2] == '-' &&
            isdigit((unsigned char)p[3]) && isdigit((unsigned char)p[4]) &&
            p[5] == '-' &&
            isdigit((unsigned char)p[6]) && isdigit((unsigned char)p[7]) &&
            isdigit((unsigned char)p[8]) && isdigit((unsigned char)p[9])) {
            *end = p + 10;
            return p;
        }
    }
    return NULL;
}

/*
 * Fix incorrect URLs by normalizing dates within a range.
 * The FAA website sometimes has inconsistent dates in URLs.
 */
static void fix_faa_incorrect_urls(char *urls[], int start, int end) {
    int n = end - start;
    if (n == 0) return;

    /* Extract (before_date, after_date) for each URL */
    char **before_date = malloc(n * sizeof(char *));
    char **after_date = malloc(n * sizeof(char *));

    for (int i = 0; i < n; i++) {
        const char *date_end;
        const char *date_start = find_date_in_url(urls[start + i], &date_end);
        if (!date_start) {
            fprintf(stderr, "Warning: Could not find date in URL: %s\n", urls[start + i]);
            before_date[i] = strdup(urls[start + i]);
            after_date[i] = strdup("");
        } else {
            /* before_date includes up to and including the date */
            size_t before_len = date_end - urls[start + i];
            before_date[i] = malloc(before_len + 1);
            memcpy(before_date[i], urls[start + i], before_len);
            before_date[i][before_len] = '\0';

            after_date[i] = strdup(date_end);
        }
    }

    /* Count occurrences of each before_date */
    int max_count = 0;
    const char *most_common = NULL;

    for (int i = 0; i < n; i++) {
        int count = 0;
        for (int j = 0; j < n; j++) {
            if (strcmp(before_date[i], before_date[j]) == 0) {
                count++;
            }
        }
        if (count > max_count) {
            max_count = count;
            most_common = before_date[i];
        }
    }

    /* If there are multiple different before_dates, fix them */
    if (most_common) {
        for (int i = 0; i < n; i++) {
            if (strcmp(before_date[i], most_common) != 0) {
                /* Rebuild URL with the most common base */
                free(urls[start + i]);
                size_t new_len = strlen(most_common) + strlen(after_date[i]) + 1;
                urls[start + i] = malloc(new_len);
                snprintf(urls[start + i], new_len, "%s%s", most_common, after_date[i]);
            }
        }
    }

    /* Cleanup */
    for (int i = 0; i < n; i++) {
        free(before_date[i]);
        free(after_date[i]);
    }
    free(before_date);
    free(after_date);
}

/* ============================================================================
 * Main
 * ============================================================================ */

static void usage(const char *prog) {
    fprintf(stderr, "Usage: %s [OPTIONS] <zippath>\n", prog);
    fprintf(stderr, "\n");
    fprintf(stderr, "Download FAA Aeronav chart data from aeronav.faa.gov.\n");
    fprintf(stderr, "\n");
    fprintf(stderr, "Arguments:\n");
    fprintf(stderr, "  zippath             Directory to store downloaded ZIP files\n");
    fprintf(stderr, "\n");
    fprintf(stderr, "Options:\n");
    fprintf(stderr, "  -q, --quiet         Suppress progress output\n");
    fprintf(stderr, "  -h, --help          Show this help message\n");
}

int main(int argc, char *argv[]) {
    static struct option long_options[] = {
        {"quiet", no_argument, 0, 'q'},
        {"help",  no_argument, 0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "qh", long_options, NULL)) != -1) {
        switch (opt) {
            case 'q':
                quiet = true;
                break;
            case 'h':
                usage(argv[0]);
                return 0;
            default:
                usage(argv[0]);
                return 1;
        }
    }

    if (optind >= argc) {
        fprintf(stderr, "Error: zippath is required\n\n");
        usage(argv[0]);
        return 1;
    }

    const char *zippath = argv[optind];

    /* Initialize libxml2 */
    xmlInitParser();
    LIBXML_TEST_VERSION

    /* Initialize curl */
    curl_global_init(CURL_GLOBAL_DEFAULT);
    CURL *curl = curl_easy_init();
    if (!curl) {
        fprintf(stderr, "Error initializing curl\n");
        return 1;
    }

    if (!quiet) {
        printf("Scraping aeronav.faa.gov...\n");
    }

    /* Single array for all URLs */
    char *urls[MAX_URLS];
    int count = 0;

    /* Fetch and parse VFR page */
    char *vfr_html = fetch_url(curl, AERONAV_VFR_URL);
    if (vfr_html) {
        scrape_chart_urls(vfr_html, VFR_CHART_TYPES, urls, &count, MAX_URLS);
        free(vfr_html);
    }
    int vfr_count = count;

    /* Fix VFR URLs */
    fix_faa_incorrect_urls(urls, 0, vfr_count);

    /* Fetch and parse IFR page */
    char *ifr_html = fetch_url(curl, AERONAV_IFR_URL);
    if (ifr_html) {
        scrape_chart_urls(ifr_html, IFR_CHART_TYPES, urls, &count, MAX_URLS);
        free(ifr_html);
    }

    /* Fix IFR URLs (only the IFR portion) */
    fix_faa_incorrect_urls(urls, vfr_count, count);

    if (!quiet) {
        printf("Found %d chart files to download.\n", count);
    }

    /* Create output directory */
    if (mkdir(zippath, 0755) != 0 && errno != EEXIST) {
        fprintf(stderr, "Error creating directory %s: %s\n", zippath, strerror(errno));
        for (int i = 0; i < count; i++) free(urls[i]);
        curl_easy_cleanup(curl);
        curl_global_cleanup();
        xmlCleanupParser();
        return 1;
    }

    /* Download all files */
    int downloaded = 0;
    int skipped = 0;
    int errors = 0;

    for (int i = 0; i < count; i++) {
        const char *url = urls[i];

        /* Extract filename from URL */
        const char *filename = strrchr(url, '/');
        filename = filename ? filename + 1 : url;

        char filepath[4096];
        snprintf(filepath, sizeof(filepath), "%s/%s", zippath, filename);

        if (!quiet) {
            printf("Downloading %s... ", filename);
            fflush(stdout);
        }

        int result = download_file(curl, url, filepath);
        if (result == 1) {
            downloaded++;
            if (!quiet) printf("done\n");
        } else if (result == 0) {
            skipped++;
            if (!quiet) printf("(not modified)\n");
        } else {
            errors++;
            /* Error message already printed */
        }
    }

    if (!quiet) {
        printf("\nDownload complete: %d downloaded, %d already up to date",
               downloaded, skipped);
        if (errors > 0) {
            printf(", %d errors", errors);
        }
        printf(".\n");
    }

    /* Cleanup */
    for (int i = 0; i < count; i++) free(urls[i]);
    curl_easy_cleanup(curl);
    curl_global_cleanup();
    xmlCleanupParser();

    return errors > 0 ? 1 : 0;
}
