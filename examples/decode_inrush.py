#!/usr/bin/env python3
"""
Decode downloaded HIOKI 3197 `.inr` inrush-current files (the current
trend captured around an inrush event) into CSVs. Values are already
real amps -- no 3197set.set/gain lookup needed.

Usage:
    python3 examples/decode_inrush.py <inr_file> [out.csv]
    python3 examples/decode_inrush.py <download_dir>

Given a folder, decodes every 97inr###.inr in it (one per inrush event),
or the single 97inrush.inr if no numbered copies are present.
"""

import csv
import glob
import os
import sys

from hioki3197.inrushfile import decode_inrush_file


def decode_one(inr_path, out_csv):
    d = decode_inrush_file(open(inr_path, "rb").read())
    print(
        f"{os.path.basename(inr_path)}: linked to event #{d['event_id']}, "
        f"{d['num_samples']} samples ({d['pre_trigger_samples']} pre-trigger)"
    )
    for ch, name in enumerate(d["channel_names"]):
        vals = d["waveform_amps"][ch]
        if vals:
            print(f"  {name}: {min(vals):.2f}A - {max(vals):.2f}A")

    with open(out_csv, "w", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["sample"] + d["channel_names"])
        for i in range(d["num_samples"]):
            cw.writerow([i] + [round(d["waveform_amps"][ch][i], 3) for ch in range(3)])
    print(f"  Written -> {out_csv}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    target = sys.argv[1]
    if os.path.isdir(target):
        files = sorted(glob.glob(os.path.join(target, "97inr[0-9][0-9][0-9].inr")))
        if not files:
            files = glob.glob(os.path.join(target, "97inrush.inr"))
        if not files:
            print(f"No inrush files found in {target}")
            sys.exit(1)
        for path in files:
            decode_one(path, path[:-4] + ".csv")
    else:
        out_csv = sys.argv[2] if len(sys.argv) > 2 else target.replace(".inr", ".csv")
        decode_one(target, out_csv)


if __name__ == "__main__":
    main()
