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

# Source directory
SRCDIR = src

# aeronav2tiles
TILES_SRCS = aeronav2tiles.c config.c processing.c tiling.c jobqueue.c cJSON.c
TILES_OBJS = $(addprefix $(SRCDIR)/, $(TILES_SRCS:.c=.o))

# Source files for linting/formatting (exclude vendored cJSON)
LINT_SRCS = $(SRCDIR)/aeronav2tiles.c $(SRCDIR)/config.c $(SRCDIR)/processing.c \
            $(SRCDIR)/tiling.c $(SRCDIR)/jobqueue.c $(SRCDIR)/aeronav_download.c
LINT_HDRS = $(SRCDIR)/aeronav.h $(SRCDIR)/jobqueue.h

.PHONY: all release debug clean tidy format

all: release

release: aeronav2tiles aeronav_download

debug: CFLAGS = $(CFLAGS_BASE) $(CFLAGS_DEBUG)
debug: LDFLAGS = $(LDFLAGS_DEBUG)
debug: aeronav2tiles aeronav_download

aeronav2tiles: $(TILES_OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(GDAL_LIBS) -lm $(LDFLAGS)

aeronav_download: $(SRCDIR)/aeronav_download.o
	$(CC) $(CFLAGS) -o $@ $^ $(CURL_LIBS) $(XML2_LIBS) $(LDFLAGS)

# Object files that need GDAL
$(SRCDIR)/aeronav2tiles.o $(SRCDIR)/config.o $(SRCDIR)/processing.o $(SRCDIR)/tiling.o: $(SRCDIR)/%.o: $(SRCDIR)/%.c
	$(CC) $(CFLAGS) $(GDAL_CFLAGS) -c -o $@ $<

# Object files without special flags
$(SRCDIR)/jobqueue.o: $(SRCDIR)/jobqueue.c $(SRCDIR)/jobqueue.h
	$(CC) $(CFLAGS) -c -o $@ $<

# Vendored cJSON - suppress sprintf deprecation warnings
$(SRCDIR)/cJSON.o: $(SRCDIR)/cJSON.c $(SRCDIR)/cJSON.h
	$(CC) $(CFLAGS) -Wno-deprecated-declarations -c -o $@ $<

# aeronav_download needs curl and xml2
$(SRCDIR)/aeronav_download.o: $(SRCDIR)/aeronav_download.c
	$(CC) $(CFLAGS) $(CURL_CFLAGS) $(XML2_CFLAGS) -c -o $@ $<

clean:
	rm -f $(TILES_OBJS) $(SRCDIR)/aeronav_download.o aeronav2tiles aeronav_download

# Header dependencies
$(SRCDIR)/aeronav2tiles.o: $(SRCDIR)/aeronav.h
$(SRCDIR)/config.o: $(SRCDIR)/aeronav.h $(SRCDIR)/cJSON.h
$(SRCDIR)/processing.o: $(SRCDIR)/aeronav.h $(SRCDIR)/jobqueue.h
$(SRCDIR)/tiling.o: $(SRCDIR)/aeronav.h

# Run clang-tidy on all source files (excludes vendored cJSON)
tidy:
	echo $(LINT_SRCS) | xargs -n1 | xargs -P$(shell nproc) -I{} clang-tidy {} -- $(GDAL_CFLAGS) $(CURL_CFLAGS) $(XML2_CFLAGS)

# Run clang-format on all source and header files (excludes vendored cJSON)
format:
	echo $(LINT_SRCS) $(LINT_HDRS) | xargs -n1 | xargs -P$(shell nproc) clang-format -i
