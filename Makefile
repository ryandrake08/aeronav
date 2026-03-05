# aeronav2tiles and aeronav_download
# Requires: GDAL (for aeronav2tiles), libcurl, libxml2 (for aeronav_download)

CC = gcc
CFLAGS_BASE = -Wall -Wextra
GDAL_CFLAGS = $(shell gdal-config --cflags)
GDAL_LIBS = $(shell gdal-config --libs)
PNG_CFLAGS = $(shell pkg-config --cflags libpng)
PNG_LIBS = $(shell pkg-config --libs libpng)
JPEG_LIBS = -ljpeg
WEBP_LIBS = -lwebp
CURL_CFLAGS = $(shell pkg-config --cflags libcurl)
CURL_LIBS = $(shell pkg-config --libs libcurl)
XML2_CFLAGS = $(shell pkg-config --cflags libxml-2.0)
XML2_LIBS = $(shell pkg-config --libs libxml-2.0)

# Release flags (default)
CFLAGS_RELEASE = -O3 -flto
LDFLAGS_RELEASE = -flto

# Profile flags (optimized with frame pointers for perf call graph)
CFLAGS_PROFILE = -O3 -g -fno-omit-frame-pointer
LDFLAGS_PROFILE =

# Debug flags
CFLAGS_DEBUG = -g -O0 -fsanitize=address,undefined
LDFLAGS_DEBUG = -fsanitize=address,undefined

# Default to release build
CFLAGS = $(CFLAGS_BASE) $(CFLAGS_RELEASE)
LDFLAGS = $(LDFLAGS_RELEASE)

# Source directory
SRCDIR = src

# aeronav2tiles
TILES_SRCS = aeronav2tiles.c config.c processing.c tiling.c tile_encode.c jobqueue.c cJSON.c
TILES_OBJS = $(addprefix $(SRCDIR)/, $(TILES_SRCS:.c=.o))

# Source files for linting/formatting (exclude vendored cJSON)
LINT_SRCS = $(SRCDIR)/aeronav2tiles.c $(SRCDIR)/config.c $(SRCDIR)/processing.c \
            $(SRCDIR)/tiling.c $(SRCDIR)/tile_encode.c $(SRCDIR)/jobqueue.c $(SRCDIR)/aeronav_download.c
LINT_HDRS = $(SRCDIR)/aeronav.h $(SRCDIR)/jobqueue.h $(SRCDIR)/tile_encode.h

COMPDB = compile_commands.json
ALL_SRCS = $(addprefix $(SRCDIR)/, $(TILES_SRCS)) $(SRCDIR)/aeronav_download.c

.PHONY: all release profile debug clean tidy format compdb

all: release

release: aeronav2tiles aeronav_download

profile: CFLAGS = $(CFLAGS_BASE) $(CFLAGS_PROFILE)
profile: LDFLAGS = $(LDFLAGS_PROFILE)
profile: aeronav2tiles aeronav_download

debug: CFLAGS = $(CFLAGS_BASE) $(CFLAGS_DEBUG)
debug: LDFLAGS = $(LDFLAGS_DEBUG)
debug: aeronav2tiles aeronav_download

aeronav2tiles: $(TILES_OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(GDAL_LIBS) $(PNG_LIBS) $(JPEG_LIBS) $(WEBP_LIBS) -lm $(LDFLAGS)

aeronav_download: $(SRCDIR)/aeronav_download.o
	$(CC) $(CFLAGS) -o $@ $^ $(CURL_LIBS) $(XML2_LIBS) $(LDFLAGS)

# Object files that need GDAL
$(SRCDIR)/aeronav2tiles.o $(SRCDIR)/config.o $(SRCDIR)/processing.o $(SRCDIR)/tiling.o: $(SRCDIR)/%.o: $(SRCDIR)/%.c
	$(CC) $(CFLAGS) $(GDAL_CFLAGS) -c -o $@ $<

# tile_encode needs libpng/libjpeg/libwebp headers
$(SRCDIR)/tile_encode.o: $(SRCDIR)/tile_encode.c $(SRCDIR)/tile_encode.h
	$(CC) $(CFLAGS) $(PNG_CFLAGS) -c -o $@ $<

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
$(SRCDIR)/tiling.o: $(SRCDIR)/aeronav.h $(SRCDIR)/tile_encode.h

# Generate compile_commands.json for clangd
compdb: $(COMPDB)
$(COMPDB): Makefile
	@printf '[\n' > $@
	@sep=; \
	for src in $(ALL_SRCS); do \
		case $$src in \
			*/aeronav2tiles.c|*/config.c|*/processing.c|*/tiling.c) extra="$(GDAL_CFLAGS)" ;; \
			*/tile_encode.c) extra="$(PNG_CFLAGS)" ;; \
			*/cJSON.c) extra="-Wno-deprecated-declarations" ;; \
			*/aeronav_download.c) extra="$(CURL_CFLAGS) $(XML2_CFLAGS)" ;; \
			*) extra="" ;; \
		esac; \
		printf '%s\n  { "directory": "%s", "file": "%s", "arguments": ["%s"' "$$sep" "$(CURDIR)" "$$src" "$(CC)" >> $@; \
		for f in $(CFLAGS_BASE) $$extra -c $$src; do printf ', "%s"' "$$f" >> $@; done; \
		printf '] }' >> $@; \
		sep=,; \
	done
	@printf '\n]\n' >> $@

# Run clang-tidy on all source files (excludes vendored cJSON)
tidy:
	echo $(LINT_SRCS) | xargs -n1 | xargs -P$(shell nproc) -I{} clang-tidy {} -- $(GDAL_CFLAGS) $(CURL_CFLAGS) $(XML2_CFLAGS)

# Run clang-format on all source and header files (excludes vendored cJSON)
format:
	echo $(LINT_SRCS) $(LINT_HDRS) | xargs -n1 | xargs -P$(shell nproc) clang-format -i
