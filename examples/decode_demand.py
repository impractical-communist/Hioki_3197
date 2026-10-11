#!/usr/bin/env python3
"""
Decode a downloaded HIOKI 3197 `97demand.dem` demand-data file into a CSV
(one row per logged demand interval: timestamp, Pdem+, Pdem-, Qdem_LAG,
Qdem_LEAD, all in real Watts/var).

Usage:
    python3 examples/decode_demand.py <demand_dir> [out.csv]

<demand_dir> must contain both 97demand.dem and the matching 3197set.set
(both come from hioki3197.Hioki3197.download_stored_data()).
"""

import csv
import datetime as dt
import os
import sys

from hioki3197.demandfile import decode_demand_file, demand_period_seconds
from hioki3197.setfile import decode_set_file


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    demand_dir = sys.argv[1]
    out_csv = sys.argv[2] if len(sys.argv) > 2 else os.path.join(demand_dir, "demand.csv")

    set97 = decode_set_file(open(os.path.join(demand_dir, "3197set.set"), "rb").read())
    d = decode_demand_file(open(os.path.join(demand_dir, "97demand.dem"), "rb").read())

    print(f"{d['num_intervals']} demand interval(s) logged")
    print("Session average:", ", ".join(f"{k}={v:.2f}" for k, v in d["average"].items()))

    if d["num_intervals"] == 0:
        print("\nNo intervals logged yet (demand logging needs a full "
              "period -- 15/30/60 min -- to elapse); nothing to write.")
        return

    period = demand_period_seconds(set97)
    t0 = d["start_time"]
    start = dt.datetime(t0["year"], t0["month"], t0["day"], t0["hour"], t0["minute"], t0["second"])

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["interval", "time"] + d["item_names"])
        for i in range(d["num_intervals"]):
            ts = start + dt.timedelta(seconds=i * period)
            w.writerow([i, ts.isoformat(sep=" ")] + [
                round(d["intervals"][item][i], 3) for item in range(len(d["item_names"]))
            ])
    print(f"\nWritten -> {out_csv}")


if __name__ == "__main__":
    main()
