#!/usr/bin/env python3
"""Grab a live screenshot from a connected HIOKI 3197 and save it as PNG."""

import sys

from hioki3197 import Hioki3197

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "screen.png"

    with Hioki3197() as dev:
        print("Status:", dev.status())
        bmp_path = out.rsplit(".", 1)[0] + ".bmp"
        dev.capture_screen(bmp_path)
        print(f"Saved raw BMP -> {bmp_path}")

    if HAVE_PIL and out.lower().endswith(".png"):
        Image.open(bmp_path).convert("RGB").save(out)
        print(f"Saved PNG -> {out}")
    elif not HAVE_PIL:
        print("(install pillow to also get a .png: pip install -r requirements.txt)")


if __name__ == "__main__":
    main()
