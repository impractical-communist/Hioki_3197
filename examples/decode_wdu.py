#!/usr/bin/env python3
"""
Decode a downloaded HIOKI 3197 `.wdu` voltage-event waveform file (the
per-cycle RMS trend captured around a dip/swell/interrupt event) into a CSV.
Values are already real volts -- no 3197set.set/gain lookup needed.

Usage:
    python3 examples/decode_wdu.py <wdu_file> [out.csv]
"""

import csv
import sys

from hioki3197.wdufile import decode_wdu_file


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    wdu_path = sys.argv[1]
    out_csv = sys.argv[2] if len(sys.argv) > 2 else wdu_path.replace(".wdu", ".csv")

    w = decode_wdu_file(open(wdu_path, "rb").read())
    print(
        f"Linked to event #{w['event_id']}, {w['num_samples']} samples "
        f"({w['pre_trigger_samples']} pre-trigger), channels: {', '.join(w['channel_names'])}"
    )
    for ch, name in enumerate(w["channel_names"]):
        vals = w["waveform_volts"][ch]
        print(f"  {name}: {min(vals):.1f}V - {max(vals):.1f}V")

    with open(out_csv, "w", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["sample"] + w["channel_names"])
        for i in range(w["num_samples"]):
            cw.writerow([i] + [round(w["waveform_volts"][ch][i], 3) for ch in range(3)])
    print(f"\nWritten -> {out_csv}")


if __name__ == "__main__":
    main()
