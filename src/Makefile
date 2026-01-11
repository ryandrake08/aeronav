# aeronav2tiles and aeronav_download
# Requires: GDAL (for aeronav2tiles), libcurl, libxml2 (for aeronav_download)

CC = gcc
CFLAGS_BASE = -Wall -Wextra
GDAL_CFLAGS = $(shell gdal-config --cflags)
GDAL_LIBS = $(shell gdal-config --libs)
CURL_CFLAGS = $(shell pkg-config --cflags libcurl)
CURL_LIBS = $(shell pkg-config --libs libcurl)
XML2_CFLAGS = $(shell pkg-config --cflags libxml-2.0)
XML2_LIBS = $(shell pkg-config --libs libxml-2.0)

# Release flags (default)
CFLAGS_RELEASE = -O3 -flto
LDFLAGS_RELEASE = -flto

# Debug flags
CFLAGS_DEBUG = -g -O0 -fsanitize=address,undefined
LDFLAGS_DEBUG = -fsanitize=address,undefined

# Default to release build
CFLAGS = $(CFLAGS_BASE) $(CFLAGS_RELEASE)
LDFLAGS = $(LDFLAGS_RELEASE)

# aeronav2tiles
TILES_SRCS = aeronav2tiles.c config.c processing.c tiling.c jobqueue.c cJSON.c
TILES_OBJS = $(TILES_SRCS:.c=.o)

.PHONY: all release debug clean

all: release

release: aeronav2tiles aeronav_download

debug: CFLAGS = $(CFLAGS_BASE) $(CFLAGS_DEBUG)
debug: LDFLAGS = $(LDFLAGS_DEBUG)
debug: aeronav2tiles aeronav_download

aeronav2tiles: $(TILES_OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(GDAL_LIBS) -lm $(LDFLAGS)

aeronav_download: aeronav_download.o
	$(CC) $(CFLAGS) -o $@ $^ $(CURL_LIBS) $(XML2_LIBS) $(LDFLAGS)

# Object files that need GDAL
aeronav2tiles.o config.o processing.o tiling.o: %.o: %.c
	$(CC) $(CFLAGS) $(GDAL_CFLAGS) -c -o $@ $<

# Object files without special flags
jobqueue.o: jobqueue.c jobqueue.h
	$(CC) $(CFLAGS) -c -o $@ $<

# Vendored cJSON - suppress sprintf deprecation warnings
cJSON.o: cJSON.c cJSON.h
	$(CC) $(CFLAGS) -Wno-deprecated-declarations -c -o $@ $<

# aeronav_download needs curl and xml2
aeronav_download.o: aeronav_download.c
	$(CC) $(CFLAGS) $(CURL_CFLAGS) $(XML2_CFLAGS) -c -o $@ $<

clean:
	rm -f $(TILES_OBJS) aeronav_download.o aeronav2tiles aeronav_download

# Header dependencies
aeronav2tiles.o: aeronav.h
config.o: aeronav.h cJSON.h
processing.o: aeronav.h jobqueue.h
tiling.o: aeronav.h
