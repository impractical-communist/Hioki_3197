#!/usr/bin/env python3
"""
Decode a downloaded HIOKI 3197 `.evt` event file into real voltage/current
numbers: event type, time, duration, and (for dip/swell/interrupt events)
the magnitude in volts, plus the full captured waveform scaled into volts
and amps.

Usage:
    python3 examples/decode_event.py <event_dir> [evt_filename]

<event_dir> must contain both the .evt file and the matching 3197set.set
(both come from hioki3197.Hioki3197.download_stored_data()).
"""

import csv
import os
import sys

from hioki3197.eventfile import decode_event_file, summarize
from hioki3197.setfile import decode_set_file, waveform_gain


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    event_dir = sys.argv[1]
    evt_name = sys.argv[2] if len(sys.argv) > 2 else "97evt000.evt"

    set97 = decode_set_file(open(os.path.join(event_dir, "3197set.set"), "rb").read())
    parsed = decode_event_file(open(os.path.join(event_dir, evt_name), "rb").read())

    print(f"Device serial: {set97['serial']}  "
          f"U range: {set97['u_max']:.0f}V  I range: {set97['i_max']:.0f}A\n")
    print(summarize(parsed))

    out_csv = os.path.join(event_dir, evt_name.replace(".evt", "_waveform.csv"))
    gains = [waveform_gain(set97, ch) for ch in range(len(parsed["waveform_channel_names"]))]
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample"] + parsed["waveform_channel_names"])
        for i in range(parsed["waveform_num_samples"]):
            w.writerow([i] + [
                round(parsed["waveform_raw"][ch][i] * gains[ch], 3)
                for ch in range(len(parsed["waveform_channel_names"]))
            ])
    print(f"\nScaled waveform (volts/amps) written to {out_csv}")


if __name__ == "__main__":
    main()
