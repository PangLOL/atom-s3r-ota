# Host shared library used by host_compare_v16.py.
# The device module is a separate natmod. From this directory, with
# xtensa-esp32-elf-gcc on PATH and a MicroPython v1.27.0 tree:
#   make -f $(MPY_DIR)/py/dynruntime.mk MPY_DIR=$(MPY_DIR) \
#     MOD=gh_render SRC="gh_mod.c gh_blit.c" ARCH=xtensawin
# Copy the resulting gh_render.mpy next to main.py. See the README.

CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -std=c11 -fPIC

.PHONY: all clean

all: libgh_blit.so

libgh_blit.so: gh_blit.c gh_blit.h
	$(CC) $(CFLAGS) -shared -o $@ gh_blit.c

clean:
	rm -f libgh_blit.so gh_render.mpy
	rm -rf build-xtensawin build-x64
