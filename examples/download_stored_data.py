#!/usr/bin/env python3
"""
Download all stored measurement data off a connected HIOKI 3197 into a
folder, in the same layout HIOKI's own View3197.jar viewer expects.

Usage:
    python3 examples/download_stored_data.py [dest_dir] [mem_no]
"""

import sys

from hioki3197 import Hioki3197


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "downloaded_data/session_01"
    mem_no = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    with Hioki3197() as dev:
        print("Status:", dev.status())
        print(f"Downloading memory bank {mem_no} -> {dest}")
        summary = dev.download_stored_data(dest, mem_no=mem_no, progress=print)
        print("Done:", summary)
        print(
            f"\nOpen this folder in HIOKI's View3197.jar "
            f"(java -jar View3197.jar, then File > Open > {dest}) to inspect it."
        )


if __name__ == "__main__":
    main()
