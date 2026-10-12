#!/usr/bin/env python3
"""
Standalone extractor for the HIOKI 3197 Power Quality Analyzer.

One file, no dependency on the `hioki3197` package: copy it to any Linux
machine and run it. It downloads everything stored in the instrument's
memory over USB, then decodes every format into CSV in real engineering
units (volts, amps, watts, Hz, %).

Only `pyusb` is needed (plus libusb-1.0 at the OS level), and only for the
download step -- decoding an already-downloaded folder with --from needs
nothing beyond the Python standard library.

Usage:
    python3 hioki3197_extract.py [out_dir] [--mem N] [--all-stats]
    python3 hioki3197_extract.py --from <downloaded_dir> [--all-stats]

    out_dir      where to save (default: hioki3197_YYYYmmdd_HHMMSS)
    --mem N      memory bank to download (default 0)
    --from DIR   skip the USB download; just decode the raw files in DIR
    --all-stats  include Max and Min per interval item, not just Ave

Outputs, alongside the raw files (which still open in HIOKI's View3197.jar):
    settings.txt            device serial, ranges, interval/demand settings
    97intvl.csv             interval/trend log, one row per interval
    demand.csv              demand readings (Pdem+/-, Qdem LAG/LEAD)
    events_summary.csv      every event record (time, type, channel, V, ms)
    97evtNNN_waveform.csv   captured waveform per event, scaled to V/A
    97dvNNN.csv             per-cycle RMS trend around each voltage event
    97inrNNN.csv            current trend around each inrush event (I1-I3, A)

Protocol and formats were reverse-engineered from HIOKI's own unobfuscated
Java apps; see docs/PROTOCOL.md in the hioki3197-linux project. Not
affiliated with or endorsed by HIOKI E.E. Corporation. No warranty.
"""

import csv
import datetime as dt
import glob
import os
import struct
import sys

# ---------------------------------------------------------------------------
# USB download
# ---------------------------------------------------------------------------

VENDOR_ID = 0x108F
PRODUCT_ID = 0x3197
_READ_CHUNK = 64  # the device's bulk endpoint wMaxPacketSize


class Hioki3197:
    """Minimal USB session: ASCII commands out, text or binary blocks back."""

    def __init__(self):
        try:
            import usb.core
            import usb.util
        except ImportError:
            sys.exit("pyusb is required for downloading: pip install pyusb "
                     "(and install libusb-1.0, e.g. sudo apt install libusb-1.0-0)")
        self._usb_core, self._usb_util = usb.core, usb.util
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            sys.exit("No HIOKI 3197 found on USB. Is it plugged in and switched on? "
                     "Without sudo you need a udev rule granting access to 108f:3197.")
        self.dev.set_configuration()
        intf = self.dev.get_active_configuration()[(0, 0)]
        out_dir, in_dir = usb.util.ENDPOINT_OUT, usb.util.ENDPOINT_IN
        self.ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == out_dir)
        self.ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == in_dir
            and e.bmAttributes & 0x3 == 0x2)  # bulk, not the interrupt endpoint
        if self.ep_out is None or self.ep_in is None:
            sys.exit("Unexpected USB descriptor layout; endpoints not found")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._usb_util.dispose_resources(self.dev)

    def _flush(self):
        # The device is a plain FIFO: unread bytes from an earlier reply
        # would bleed into the next one, so drain before every command.
        while True:
            try:
                self.ep_in.read(_READ_CHUNK, timeout=150)
            except self._usb_core.USBError:
                break

    def _write(self, command, timeout):
        self.ep_out.write((command + "\r\n").encode("ascii"), timeout=timeout)

    def query(self, command, timeout=5000):
        self._flush()
        self._write(command, timeout)
        try:
            data = bytearray(self.ep_in.read(_READ_CHUNK, timeout=timeout))
        except self._usb_core.USBError:
            return ""
        # Full-sized packets mean more follows; a short one ends the reply.
        while len(data) and len(data) % _READ_CHUNK == 0:
            try:
                chunk = self.ep_in.read(_READ_CHUNK, timeout=800)
            except self._usb_core.USBError:
                break
            if not chunk:
                break
            data += chunk
        return data.decode("ascii", errors="replace").strip()

    def bquery(self, command, timeout=15000):
        """Binary reply: 8 ASCII length digits, 1 delimiter byte, payload."""
        self._flush()
        self._write(command, timeout)
        data = bytearray(self.ep_in.read(_READ_CHUNK, timeout=timeout))
        if len(data) < 9 or not data[:8].isdigit():
            raise RuntimeError(f"Expected a binary block header to {command}, got {bytes(data)!r}")
        needed = int(data[:8].decode("ascii")) + 9
        while len(data) < needed:
            chunk = self.ep_in.read(min(4096, needed - len(data) + _READ_CHUNK), timeout=timeout)
            if not chunk:
                break
            data += chunk
        return bytes(data[9:needed])

    def download(self, dest, mem_no=0):
        """Save one memory bank using the filenames HIOKI's own software uses."""
        os.makedirs(dest, exist_ok=True)
        reply = self.query(f":BDATA:EVENT:NUM? {mem_no}")
        try:
            ev_n, wdu_n, inr_n = (int(p) for p in reply.split(","))
        except ValueError:
            raise RuntimeError(f"Unexpected :BDATA:EVENT:NUM? reply: {reply!r}")

        def save(name, cmd):
            payload = self.bquery(cmd)
            with open(os.path.join(dest, name), "wb") as f:
                f.write(payload)
            print(f"  {name}: {len(payload)} bytes")

        save("3197set.set", f":BDATA:SYSTEM? {mem_no}")
        save("97intvl.itv", f":BDATA:TIMEPLOT? {mem_no}")
        save("97demand.dem", f":BDATA:DEMAND? {mem_no}")
        save("97evmoni.mon", f":BDATA:EVENT:MONI? {mem_no}")
        for i in range(ev_n):
            save(f"97evt{i:03d}.evt", f":BDATA:EVENT:LIST? {mem_no},{i}")
        for i in range(wdu_n):
            save(f"97dv{i:03d}.wdu", f":BDATA:EVENT:VOLT? {mem_no},{i}")
        for i in range(inr_n):
            # HIOKI's app reuses one filename for every inrush event (each
            # overwrites the last), and View3197.jar expects it -- so keep
            # that, plus a numbered copy so none are lost.
            save(f"97inr{i:03d}.inr", f":BDATA:EVENT:INRUSH? {mem_no},{i}")
            with open(os.path.join(dest, f"97inr{i:03d}.inr"), "rb") as src, \
                    open(os.path.join(dest, "97inrush.inr"), "wb") as dst:
                dst.write(src.read())
        return ev_n, wdu_n, inr_n


# ---------------------------------------------------------------------------
# File decoders (all big-endian, Java DataInputStream style)
# ---------------------------------------------------------------------------

# Cumulative day-of-year totals used by the firmware's calendar decomposition
_DAY_MONTH = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365]
_DAY_MONTH_LEAP = [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366]


def _read_eptime(raw, off):
    """Eptime: int32 seconds since 1980 + int16 milliseconds + 2 reserved."""
    sec, ms = struct.unpack_from(">ih", raw, off)
    day = sec // 86400
    year = day * 100 // 36525
    day -= year * 365 + (year + 3) * 25 // 100
    year += 1980
    table = _DAY_MONTH_LEAP if year % 4 == 0 else _DAY_MONTH
    month = 1
    while month <= 12 and day >= table[month]:
        month += 1
    day = day - table[month - 1] + 1
    s = sec % 86400
    t = dt.datetime(year, month, day, s // 3600, s % 3600 // 60, s % 60, ms * 1000)
    return t, off + 8


def _iso(t):
    return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}"


def _check_sig(raw, sig, what):
    if not raw.startswith(sig.encode("ascii")):
        raise ValueError(f"{what}: unexpected file signature {raw[:40]!r}")


def decode_settings(raw):
    _check_sig(raw, "This is HIOKI 3197 SYSTEM DATA FILE", "3197set.set")

    def text(off, n):
        return raw[off:off + n].decode("ascii", errors="replace").strip("\x00").strip()

    off = 35 + 13
    alias = text(off, 16)
    off += 16 + 32
    serial = text(off, 22)
    off += 22 + 10
    off += 2  # status
    wiring, = struct.unpack_from(">b", raw, off)
    off += 2
    off += 16 + 4 + 16 + 16  # uRef, freq/clamp/range flags, PT, CT
    u_max, i_max, p_max, sum_max = struct.unpack_from(">4f", raw, off)
    off += 16 + 20
    interval, = struct.unpack_from(">i", raw, off)
    off += 4 + 1 + 1 + 3
    demand, = struct.unpack_from(">b", raw, off)
    off += 1 + 2
    u_adj = list(struct.unpack_from(">3f", raw, off))
    off += 12
    i_adj = list(struct.unpack_from(">3f", raw, off))
    off += 12 + 12
    i_adj.append(struct.unpack_from(">f", raw, off)[0])
    return {
        "alias": alias, "serial": serial, "wiring": wiring,
        "u_max": u_max, "i_max": i_max, "p_max": p_max, "sum_max": sum_max,
        "u_adj": u_adj, "i_adj": i_adj, "interval": interval, "demand": demand,
    }


def waveform_gain(settings, ch):
    """Raw .evt waveform count -> V (ch 0-2) or A (ch 3-6)."""
    if ch < 3:
        return settings["u_max"] * settings["u_adj"][ch]
    return settings["i_max"] * settings["i_adj"][ch - 3]


INTERVAL_ITEMS = [
    "Freq", "U1", "I1", "U2", "I2", "U3", "I3", "I4",
    "U1peak+", "U1peak-", "I1peak+", "I1peak-",
    "U2peak+", "U2peak-", "I2peak+", "I2peak-",
    "U3peak+", "U3peak-", "I3peak+", "I3peak-",
    "I4peak+", "I4peak-",
    "Uave", "Iave", "Psum", "Qsum", "Ssum", "PFsum",
    "THD1", "THD2", "THD3", "Uunb",
]
INTERVAL_STATS = ["Max", "Min", "Ave"]
DU_NAMES = ["Umax1", "Umax2", "Umax3", "Umin1", "Umin2", "Umin3"]
INTEG_NAMES = ["WP+", "WP-", "WQ_LAG", "WQ_LEAD"]
_INTERVAL_RECORD = 436


def decode_interval(raw):
    _check_sig(raw, "This is HIOKI 3197 TIME_PLOT DATA FILE", "97intvl.itv")
    off = 38 + 90
    n, = struct.unpack_from(">i", raw, off)
    integ_start, off = _read_eptime(raw, off + 4)
    if len(raw) < off + n * _INTERVAL_RECORD:
        raise ValueError(f"97intvl.itv truncated: {n} records declared, {len(raw)} bytes")
    nitems = len(INTERVAL_ITEMS)
    records = []
    for _ in range(n):
        t, off = _read_eptime(raw, off)
        status, = struct.unpack_from(">i", raw, off)
        du = struct.unpack_from(">6f", raw, off + 4)
        off += 28
        stats = {}
        for stat in INTERVAL_STATS:
            stats[stat] = struct.unpack_from(f">{nitems}f", raw, off)
            off += 4 * nitems
        integ = struct.unpack_from(">4f", raw, off)
        off += 16
        records.append({"time": t, "status": status, "du": du, "stats": stats, "integ": integ})
    return {"integ_start": integ_start, "records": records}


DEMAND_ITEMS = ["Pdem+", "Pdem-", "Qdem_LAG", "Qdem_LEAD"]


def decode_demand(raw):
    _check_sig(raw, "This is HIOKI 3197 DEMAND DATA FILE", "97demand.dem")
    off = 35 + 93
    n, = struct.unpack_from(">i", raw, off)
    start, off = _read_eptime(raw, off + 4)
    average = struct.unpack_from(">4f", raw, off)
    off += 16
    rows = [struct.unpack_from(">4f", raw, off + 16 * i) for i in range(n)]
    return {"start": start, "average": average, "rows": rows}


def demand_period_seconds(settings):
    return {0: 900, 1: 1800}.get(settings["demand"], 3600)


EVENT_NAMES = ["EVENT", "TRANSIENT", "SWELL", "DIP", "INTERRUPT", "INRUSH",
               "START", "STOP", "TIMER", "MANUAL"]
CH_NAMES = ["CH1", "CH2", "CH3", ""]
INOUT_NAMES = ["", "IN", "OUT", "---"]
WAVE_CHANNELS = ["U1", "U2", "U3", "I1", "I2", "I3", "I4"]
_DETAIL_SLOTS = 231
_OUT_EVENT_SLOTS = 19


def decode_event(raw):
    _check_sig(raw, "This is HIOKI 3197 EVENT DATA FILE", ".evt")
    off = 34 + 94
    event_id, = struct.unpack_from(">h", raw, off)
    detail_num, = struct.unpack_from(">h", raw, off + 4)
    off += 8

    details = []
    for _ in range(detail_num):
        t, off = _read_eptime(raw, off)
        item, _out_id, packed = struct.unpack_from(">bbh", raw, off)
        off += 4
        inout = (packed & 0x3000) >> 12
        ch = packed >> 14
        if ch < 0:
            ch = 2
        in_out_item = item in (1, 2, 3, 4)  # TRANSIENT/SWELL/DIP/INTERRUPT
        details.append({
            "time": t,
            "type": EVENT_NAMES[item] if 0 <= item < len(EVENT_NAMES) else f"?{item}",
            "channel": CH_NAMES[ch] if in_out_item and inout != 0 else "ALL",
            "in_out": INOUT_NAMES[inout],
            "is_out": in_out_item and inout == 2,
            "duration_ms": None,
            "value_volts": None,
        })
    off += 12 * (_DETAIL_SLOTS - detail_num)

    n_out = 0
    for d in details:
        if d["is_out"]:
            term, value = struct.unpack_from(">if", raw, off)
            off += 8
            d["duration_ms"] = (term & 0x3FFFFFFF) * (10 if term & 0x40000000 else 1)
            d["value_volts"] = value
            n_out += 1
    off += 8 * (_OUT_EVENT_SLOTS - n_out)

    wave_time, off = _read_eptime(raw, off)
    wave_num, pre_num = struct.unpack_from(">hh", raw, off)
    off += 4
    nch = len(WAVE_CHANNELS)
    samples = [struct.unpack_from(f">{nch}h", raw, off + 2 * nch * i) for i in range(wave_num)]
    return {"event_id": event_id, "details": details, "wave_time": wave_time,
            "pre_trigger": pre_num, "wave_raw": samples}


WDU_CHANNELS = ["U1", "U2", "U3"]


def decode_wdu(raw):
    _check_sig(raw, "This is HIOKI 3197 DELTA-V DATA FILE", ".wdu")
    off = 36 + 92
    _wdu_id, wave_num, pre_num, evt_id = struct.unpack_from(">4h", raw, off)
    off += 8
    samples = [struct.unpack_from(">3f", raw, off + 12 * i) for i in range(wave_num)]
    return {"event_id": evt_id, "pre_trigger": pre_num, "samples": samples}


INRUSH_CHANNELS = ["I1", "I2", "I3"]


def decode_inrush(raw):
    """Same body as .wdu (EventInrush subclasses EventWdu), but currents in amps."""
    _check_sig(raw, "This is HIOKI 3197 INRUSH DATA FILE", ".inr")
    off = 35 + 93
    _inr_id, wave_num, pre_num, evt_id = struct.unpack_from(">4h", raw, off)
    off += 8
    if len(raw) < off + 12 * wave_num:
        raise ValueError(f".inr truncated: {wave_num} samples declared, {len(raw)} bytes")
    samples = [struct.unpack_from(">3f", raw, off + 12 * i) for i in range(wave_num)]
    return {"event_id": evt_id, "pre_trigger": pre_num, "samples": samples}


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _read(folder, name):
    with open(os.path.join(folder, name), "rb") as f:
        return f.read()


def _writer(path):
    f = open(path, "w", newline="")
    return f, csv.writer(f)


def export_settings(folder):
    s = decode_settings(_read(folder, "3197set.set"))
    with open(os.path.join(folder, "settings.txt"), "w") as f:
        f.write(f"serial:            {s['serial']}\n")
        f.write(f"alias:             {s['alias']}\n")
        f.write(f"wiring code:       {s['wiring']}\n")
        f.write(f"voltage range:     {s['u_max']:g} V\n")
        f.write(f"current range:     {s['i_max']:g} A\n")
        f.write(f"interval code:     {s['interval']}\n")
        f.write(f"demand period:     {demand_period_seconds(s) // 60} min\n")
    print(f"Settings: serial {s['serial']}, U range {s['u_max']:g} V, I range {s['i_max']:g} A")
    return s


def export_interval(folder, stats):
    d = decode_interval(_read(folder, "97intvl.itv"))
    recs = d["records"]
    idx = [INTERVAL_STATS.index(s) for s in stats]
    f, w = _writer(os.path.join(folder, "97intvl.csv"))
    with f:
        w.writerow(["time", "status"]
                   + [f"{item}_{s}" for item in INTERVAL_ITEMS for s in stats]
                   + DU_NAMES + INTEG_NAMES)
        for r in recs:
            row = [_iso(r["time"]), r["status"]]
            for k in range(len(INTERVAL_ITEMS)):
                row += [r["stats"][INTERVAL_STATS[j]][k] for j in idx]
            w.writerow(row + list(r["du"]) + list(r["integ"]))
    if recs:
        print(f"Interval log: {len(recs)} intervals, "
              f"{_iso(recs[0]['time'])} -> {_iso(recs[-1]['time'])}")
        u1 = [r["stats"]["Ave"][1] for r in recs]
        print(f"  U1 average {sum(u1) / len(u1):.1f} V (min {min(u1):.1f}, max {max(u1):.1f})")
    else:
        print("Interval log: empty")


def export_demand(folder, settings):
    d = decode_demand(_read(folder, "97demand.dem"))
    period = dt.timedelta(seconds=demand_period_seconds(settings))
    f, w = _writer(os.path.join(folder, "demand.csv"))
    with f:
        w.writerow(["interval", "time"] + DEMAND_ITEMS)
        for i, row in enumerate(d["rows"]):
            w.writerow([i, (d["start"] + i * period).strftime("%Y-%m-%d %H:%M:%S")]
                       + [round(v, 3) for v in row])
    print(f"Demand: {len(d['rows'])} intervals, session average "
          + ", ".join(f"{k}={v:.2f}" for k, v in zip(DEMAND_ITEMS, d["average"])))


def export_events(folder, settings):
    files = sorted(glob.glob(os.path.join(folder, "97evt*.evt")))
    gains = [waveform_gain(settings, ch) for ch in range(len(WAVE_CHANNELS))]
    f, w = _writer(os.path.join(folder, "events_summary.csv"))
    with f:
        w.writerow(["file", "event_id", "time", "type", "channel", "in_out",
                    "duration_ms", "value_volts"])
        n = 0
        for path in files:
            ev = decode_event(_read(folder, os.path.basename(path)))
            name = os.path.basename(path)
            for d in ev["details"]:
                w.writerow([name, ev["event_id"], _iso(d["time"]), d["type"], d["channel"],
                            d["in_out"],
                            "" if d["duration_ms"] is None else d["duration_ms"],
                            "" if d["value_volts"] is None else f"{d['value_volts']:.3f}"])
                n += 1
                if d["type"] not in ("START", "STOP") and d["in_out"] != "OUT":
                    print(f"  {_iso(d['time'])}  {d['type']:<9s} {d['channel']}")
            wf, ww = _writer(path[:-4] + "_waveform.csv")
            with wf:
                ww.writerow(["sample"] + WAVE_CHANNELS)
                for i, sample in enumerate(ev["wave_raw"]):
                    ww.writerow([i] + [round(v * g, 3) for v, g in zip(sample, gains)])
    print(f"Events: {n} records from {len(files)} files "
          f"(+ one scaled waveform CSV per file)")


def export_wdu(folder):
    files = sorted(glob.glob(os.path.join(folder, "97dv*.wdu")))
    for path in files:
        d = decode_wdu(_read(folder, os.path.basename(path)))
        f, w = _writer(path[:-4] + ".csv")
        with f:
            w.writerow(["sample"] + WDU_CHANNELS)
            for i, sample in enumerate(d["samples"]):
                w.writerow([i] + [round(v, 3) for v in sample])
    if files:
        print(f"Voltage-event RMS trends: {len(files)} files")


def export_inrush(folder):
    # Numbered copies hold every event; a lone 97inrush.inr (e.g. from
    # HIOKI's own app) holds only the last one.
    files = sorted(glob.glob(os.path.join(folder, "97inr[0-9][0-9][0-9].inr")))
    if not files:
        files = glob.glob(os.path.join(folder, "97inrush.inr"))
    for path in files:
        d = decode_inrush(_read(folder, os.path.basename(path)))
        f, w = _writer(path[:-4] + ".csv")
        with f:
            w.writerow(["sample"] + INRUSH_CHANNELS)
            for i, sample in enumerate(d["samples"]):
                w.writerow([i] + [round(v, 3) for v in sample])
        peak = max((abs(v) for s in d["samples"] for v in s), default=0.0)
        print(f"  {os.path.basename(path)}: event #{d['event_id']}, "
              f"{len(d['samples'])} samples, peak {peak:.2f} A")
    if files:
        print(f"Inrush current trends: {len(files)} files")


def decode_folder(folder, stats):
    settings = export_settings(folder)
    if os.path.exists(os.path.join(folder, "97intvl.itv")):
        export_interval(folder, stats)
    if os.path.exists(os.path.join(folder, "97demand.dem")):
        export_demand(folder, settings)
    export_events(folder, settings)
    export_wdu(folder)
    export_inrush(folder)


# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        return

    def take(flag):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                sys.exit(f"{flag} needs a value")
            val = args[i + 1]
            del args[i:i + 2]
            return val
        return None

    src = take("--from")
    mem = int(take("--mem") or 0)
    stats = INTERVAL_STATS if "--all-stats" in args else ["Ave"]
    args = [a for a in args if a != "--all-stats"]

    if src:
        if not os.path.exists(os.path.join(src, "3197set.set")):
            sys.exit(f"{src} has no 3197set.set; is it a HIOKI 3197 download folder?")
        folder = src
    else:
        folder = args[0] if args else dt.datetime.now().strftime("hioki3197_%Y%m%d_%H%M%S")
        with Hioki3197() as dev:
            print(f"Downloading memory bank {mem} -> {folder}")
            ev_n, wdu_n, inr_n = dev.download(folder, mem)
            print(f"Downloaded {ev_n} event, {wdu_n} voltage-event and {inr_n} inrush file(s)\n")

    decode_folder(folder, stats)
    print(f"\nDone. CSVs are in {folder}/ "
          "(the raw files also open in HIOKI's View3197.jar)")


if __name__ == "__main__":
    main()
