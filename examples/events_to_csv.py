#!/usr/bin/env python3
"""
Summarize every .evt event file in a downloaded-data folder into one CSV:
one row per event record (time, type, channel, in/out, duration, voltage).

Usage:
    python3 examples/events_to_csv.py <event_dir> [out.csv]
"""

import csv
import glob
import os
import sys

from hioki3197.eventfile import decode_event_file


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    event_dir = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(event_dir, "events_summary.csv")

    files = sorted(glob.glob(os.path.join(event_dir, "97evt*.evt")))
    if not files:
        print(f"No 97evt*.evt files found in {event_dir}")
        sys.exit(1)

    rows = []
    for path in files:
        parsed = decode_event_file(open(path, "rb").read())
        fname = os.path.basename(path)
        for d in parsed["details"]:
            rows.append({
                "file": fname,
                "event_id": parsed["event_id"],
                "time": d["time"]["iso"],
                "type": d["event_name"],
                "channel": d["channel"],
                "in_out": d["inout"],
                "duration_ms": d["term_ms"] if d["term_ms"] is not None else "",
                "value_volts": f"{d['value_volts']:.3f}" if d["value_volts"] is not None else "",
            })

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "file", "event_id", "time", "type", "channel", "in_out", "duration_ms", "value_volts",
        ])
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} event record(s) from {len(files)} file(s) -> {out_path}")


if __name__ == "__main__":
    main()
