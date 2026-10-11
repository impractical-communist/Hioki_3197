#!/usr/bin/env python3
"""
Poll the live screen of a connected HIOKI 3197 every few seconds and keep
overwriting one image file -- point an image viewer at it for a crude
live dashboard.

Usage:
    python3 examples/live_poll.py [out.bmp] [interval_seconds]
"""

import sys
import time

from hioki3197 import Hioki3197

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "live.bmp"
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    with Hioki3197() as dev:
        print(f"Polling every {interval}s -> {out} (Ctrl+C to stop)")
        while True:
            dev.capture_screen(out)
            print(f"updated {out} at {time.strftime('%H:%M:%S')}")
            time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
