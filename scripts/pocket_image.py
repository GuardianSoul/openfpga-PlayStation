#!/usr/bin/env python3
"""Convert a greyscale PNG to the Pocket's image format and back.

Core icons (36x36, Cores/<core>/icon.bin) and platform images (521x165,
Platforms/_images/<platform>.bin) store one little-endian 16-bit word per
pixel with the brightness in the low byte read as ink (the PNG's 255 - grey),
column by column of the image rotated 90 degrees counter-clockwise. The
decoder mirrors the openFPGA Library's, so the round trip checks both.

  python3 scripts/pocket_image.py in.png out.bin 521 165
"""
import sys
from PIL import Image


def encode(png, size):
    image = Image.open(png).convert('L')
    if image.size != size: raise SystemExit(f'{png}: expected {size[0]}x{size[1]}, got {image.size}')
    ink = Image.eval(image, lambda v: 255 - v).rotate(90, expand=True)
    data = bytes(b for v in ink.tobytes() for b in (v, 0))
    if decode(data, size).tobytes() != image.tobytes(): raise SystemExit(f'{png}: round trip failed')
    return data


def decode(data, size):
    w, h = size
    return Image.frombytes('L', (h, w), bytes(255 - data[i] for i in range(0, len(data), 2))).rotate(-90, expand=True)


if __name__ == '__main__':
    src, dst, w, h = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    open(dst, 'wb').write(encode(src, (w, h)))
