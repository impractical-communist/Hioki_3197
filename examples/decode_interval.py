#!/usr/bin/env python3
"""
Decode a downloaded HIOKI 3197 `97intvl.itv` interval-log file into a CSV:
one row per logged interval, with the real-engineering-unit values for
every measurement item (Freq, U1-U3, I1-I4, peaks, Psum/Qsum/Ssum/PFsum,
THD, unbalance), plus per-channel voltage extremes and cumulative energy.

Usage:
    python3 examples/decode_interval.py <itv_file> [out.csv] [--stats Max,Min,Ave]

By default only the Ave (average-over-interval) stat is exported per item,
to keep the CSV a reasonable width -- pass --stats to include Max/Min too.
"""

import csv
import sys

from hioki3197.intervalfile import decode_interval_file


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stats_arg = next((a for a in sys.argv[1:] if a.startswith("--stats")), None)
    stats = stats_arg.split("=", 1)[1].split(",") if stats_arg and "=" in stats_arg else ["Ave"]

    if not args:
        print(__doc__)
        sys.exit(1)

    itv_path = args[0]
    out_csv = args[1] if len(args) > 1 else itv_path.replace(".itv", ".csv")

    d = decode_interval_file(open(itv_path, "rb").read())
    print(f"{d['wave_num']} interval(s) logged, "
          f"integration start: {d['integ_start_time']['iso']}")
    if d["wave_num"]:
        print(f"  from {d['records'][0]['time']['iso']} to {d['records'][-1]['time']['iso']}")

    item_cols = [f"{item}_{stat}" for item in d["item_names"] for stat in stats]
    du_cols = list(next(iter(d["records"]), {}).get("du", {}).keys()) if d["records"] else []
    integ_cols = list(next(iter(d["records"]), {}).get("integ", {}).keys()) if d["records"] else []

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "status"] + item_cols + du_cols + integ_cols)
        for r in d["records"]:
            row = [r["time"]["iso"], r["status"]]
            row += [r["items"][item][stat] for item in d["item_names"] for stat in stats]
            row += [r["du"][k] for k in du_cols]
            row += [r["integ"][k] for k in integ_cols]
            w.writerow(row)

    print(f"\nWritten -> {out_csv}")


if __name__ == "__main__":
    main()
