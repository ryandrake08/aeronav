# aeronav2tiles C implementation
# Requires GDAL development libraries

CC = gcc
CFLAGS_BASE = -Wall -Wextra $(shell gdal-config --cflags)
LDFLAGS_BASE = $(shell gdal-config --libs) -lm

# Release flags (default)
CFLAGS_RELEASE = -O3 -flto
LDFLAGS_RELEASE = -flto

# Debug flags
CFLAGS_DEBUG = -g -O0 -fsanitize=address,undefined
LDFLAGS_DEBUG = -fsanitize=address,undefined

# Default to release build
CFLAGS = $(CFLAGS_BASE) $(CFLAGS_RELEASE)
LDFLAGS = $(LDFLAGS_BASE) $(LDFLAGS_RELEASE)

# Source files
SRCS = main.c config.c processing.c vrt.c tiling.c manifest.c jobqueue.c cJSON.c
OBJS = $(SRCS:.c=.o)
TARGET = aeronav2tiles

.PHONY: all release debug clean

all: release

release: $(TARGET)

debug: CFLAGS = $(CFLAGS_BASE) $(CFLAGS_DEBUG)
debug: LDFLAGS = $(LDFLAGS_BASE) $(LDFLAGS_DEBUG)
debug: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET)

# Dependencies
main.o: main.c aeronav.h
config.o: config.c aeronav.h cJSON.h
processing.o: processing.c aeronav.h jobqueue.h
vrt.o: vrt.c aeronav.h
tiling.o: tiling.c aeronav.h manifest.h
manifest.o: manifest.c manifest.h aeronav.h
jobqueue.o: jobqueue.c jobqueue.h

# Vendored cJSON uses sprintf - suppress deprecation warnings
cJSON.o: cJSON.c cJSON.h
	$(CC) $(CFLAGS) -Wno-deprecated-declarations -c -o $@ $<
