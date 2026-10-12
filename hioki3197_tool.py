#!/usr/bin/env python3
"""
===============================================================================
 HIOKI 3197 Power Quality Analyzer -- all-in-one tool
===============================================================================

 Get the measurements out of a HIOKI 3197 and into files you can open in a
 spreadsheet or a web browser. One file, nothing to install except `pyusb`.

 -----------------------------------------------------------------------------
 QUICK START
 -----------------------------------------------------------------------------

   1. Plug the HIOKI 3197 into this computer with its USB cable and switch
      it on.

   2. Open a terminal in the folder holding this file and run:

          python3 hioki3197_tool.py

   3. A menu appears. Pick what you want by typing its number and pressing
      Enter. If you're not sure, start with 1 ("Check the connection"),
      then 3 ("Download everything from the analyzer").

 That's it. Downloaded data lands in a `hioki_data` folder next to this
 file, one sub-folder per download, named by date and time.

 -----------------------------------------------------------------------------
 FIRST-TIME SETUP (once per computer)
 -----------------------------------------------------------------------------

 This tool is written for Linux.

   a) Install the USB library and pyusb. On Ubuntu / Debian / Mint:

          sudo apt install libusb-1.0-0 python3-usb

      (or, inside a Python virtual environment: pip install pyusb)

   b) Allow your user account to talk to the analyzer without sudo. Run
      the tool, choose "First-time setup" from the menu, and type your
      password when asked. Then unplug and replug the USB cable.

      (It installs this one-line rule as
       /etc/udev/rules.d/99-hioki3197.rules:
       SUBSYSTEM=="usb", ATTR{idVendor}=="108f", ATTR{idProduct}=="3197", MODE="0666")

 Optional extras (the tool works without them):
   - Pillow (sudo apt install python3-pil) also saves screenshots as PNG.
     Without it you get a BMP, which opens in any web browser or image
     viewer.
   - Java (sudo apt install default-jre) runs HIOKI's own viewer program,
     View3197.jar, if you have a copy of it.

 -----------------------------------------------------------------------------
 WHAT YOU GET FROM A DOWNLOAD
 -----------------------------------------------------------------------------

   README.txt              plain-English summary of this download
   settings.txt            the analyzer's serial number and ranges
   97intvl.csv             THE MAIN DATA: one row per logging interval with
                           voltage, current, frequency, power, THD...
                           (open it in LibreOffice Calc or Excel)
   events_summary.csv      every recorded event (dip, swell, interruption,
                           transient...) with time, channel, volts, duration
   demand.csv              power demand readings (Pdem, Qdem)
   97evtNNN_waveform.csv   the waveform captured at each event, in V and A
   97dvNNN.csv             RMS voltage around each dip/swell/interruption
   97inrNNN.csv            current around each inrush event
   chart_*.svg             charts you make with menu option 5 (open in a
                           web browser)
   *.set *.itv *.evt ...   the original files from the analyzer, untouched.
                           These open in HIOKI's View3197 viewer.

 Column names in 97intvl.csv:
   U1 U2 U3        voltage on channel 1-3 (volts, RMS)
   I1 I2 I3 I4     current on channel 1-4 (amps, RMS)
   Freq            frequency (Hz)
   Psum Qsum Ssum  total real (W) / reactive (var) / apparent (VA) power
   PFsum           power factor
   THD1-3          voltage total harmonic distortion (%)
   Uunb            voltage unbalance (%)
   _Ave _Max _Min  average / highest / lowest within the interval
   Umax1 Umin1...  highest / lowest single-cycle voltage in the interval
   WP+ WP- ...     energy counters (Wh, varh) since recording started

 -----------------------------------------------------------------------------
 TROUBLESHOOTING
 -----------------------------------------------------------------------------

   "No HIOKI 3197 found"     Check the cable and that the analyzer is on.
                             Try another USB port. Run `lsusb`: a line
                             with "108f:3197" should appear.
   "Permission denied"       Run "First-time setup" from the menu, then
                             unplug and replug the cable.
   "pyusb is not installed"  See FIRST-TIME SETUP step a).
   "No backend available"    The libusb library is missing; see step a).
   Download stops halfway    Unplug, replug, try again. Don't press
                             buttons on the analyzer during a download.
   Currents are all zero     The current clamps weren't connected (or not
                             detected) while recording.

 Downloading only READS from the analyzer; nothing on it is changed or
 deleted.

 -----------------------------------------------------------------------------
 COMMAND-LINE USE (for scripts; the menu does all of this too)
 -----------------------------------------------------------------------------

   python3 hioki3197_tool.py check                  is the analyzer there?
   python3 hioki3197_tool.py screenshot [file]      save the current screen
   python3 hioki3197_tool.py download [folder]      download + decode all
         [--mem N]                                  memory bank (default 0)
         [--all-stats]                              add Max/Min columns
   python3 hioki3197_tool.py decode <folder>        decode an old download
   python3 hioki3197_tool.py chart <folder> [item]  chart, e.g. item U1, Freq
   python3 hioki3197_tool.py viewer [folder]        open HIOKI's View3197
   python3 hioki3197_tool.py setup                  USB permission setup

 -----------------------------------------------------------------------------

 Unofficial. Not affiliated with or endorsed by HIOKI E.E. Corporation;
 "HIOKI" and "3197" are their trademarks, used only to identify the
 hardware. The USB protocol and file formats were worked out from HIOKI's
 own unobfuscated Java programs (see docs/PROTOCOL.md in the
 hioki3197-linux project). No warranty; use at your own risk.
===============================================================================
"""

import csv
import datetime as dt
import glob
import math
import os
import shutil
import struct
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "hioki_data")

VENDOR_ID = 0x108F
PRODUCT_ID = 0x3197
UDEV_RULE_PATH = "/etc/udev/rules.d/99-hioki3197.rules"
UDEV_RULE = 'SUBSYSTEM=="usb", ATTR{idVendor}=="108f", ATTR{idProduct}=="3197", MODE="0666"\n'


class UserFacingError(Exception):
    """A problem with a plain-English explanation; shown without a traceback."""


# ===========================================================================
# Talking to the analyzer over USB
# ===========================================================================

_READ_CHUNK = 64  # the device's bulk endpoint packet size


def _import_usb():
    try:
        import usb.core
        import usb.util
    except ImportError:
        raise UserFacingError(
            "pyusb is not installed, so the tool can't talk to the analyzer.\n"
            "Install it with:   sudo apt install libusb-1.0-0 python3-usb\n"
            "(or inside a virtual environment:   pip install pyusb)\n"
            "Decoding and charting downloads you already have still work without it.")
    return usb.core, usb.util


class Analyzer:
    """A USB session with one HIOKI 3197. Use as: `with Analyzer() as dev:`."""

    def __init__(self):
        self.usb_core, self.usb_util = _import_usb()
        try:
            self.dev = self.usb_core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        except self.usb_core.NoBackendError:
            raise UserFacingError(
                "pyusb is installed but the libusb library isn't.\n"
                "Install it with:   sudo apt install libusb-1.0-0")
        if self.dev is None:
            raise UserFacingError(
                "No HIOKI 3197 found on USB.\n"
                "  - Is the USB cable plugged in at both ends?\n"
                "  - Is the analyzer switched on?\n"
                "  - Try another USB port, or unplug and replug the cable.\n"
                "  - Run `lsusb` in a terminal: a line containing 108f:3197\n"
                "    should be listed.")
        try:
            self.dev.set_configuration()
        except self.usb_core.USBError as e:
            raise _explain_usb_error(e)
        intf = self.dev.get_active_configuration()[(0, 0)]
        util = self.usb_util
        self.ep_out = util.find_descriptor(
            intf, custom_match=lambda e:
                util.endpoint_direction(e.bEndpointAddress) == util.ENDPOINT_OUT)
        self.ep_in = util.find_descriptor(
            intf, custom_match=lambda e:
                util.endpoint_direction(e.bEndpointAddress) == util.ENDPOINT_IN
                and e.bmAttributes & 0x3 == 0x2)  # bulk, not the interrupt endpoint
        if self.ep_out is None or self.ep_in is None:
            raise UserFacingError("The USB device doesn't look like a HIOKI 3197 "
                                  "(expected endpoints not found).")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.usb_util.dispose_resources(self.dev)

    def _flush(self):
        # The analyzer is a plain FIFO: unread bytes from an earlier reply
        # would bleed into the next one, so drain before every command.
        while True:
            try:
                self.ep_in.read(_READ_CHUNK, timeout=150)
            except self.usb_core.USBError:
                break

    def _write(self, command, timeout):
        self.ep_out.write((command + "\r\n").encode("ascii"), timeout=timeout)

    def query(self, command, timeout=5000):
        """Send a text command, return the text reply (e.g. ':MODE?' -> '4')."""
        self._flush()
        self._write(command, timeout)
        try:
            data = bytearray(self.ep_in.read(_READ_CHUNK, timeout=timeout))
        except self.usb_core.USBError:
            return ""
        # Full-sized packets mean more follows; a short one ends the reply.
        while len(data) and len(data) % _READ_CHUNK == 0:
            try:
                chunk = self.ep_in.read(_READ_CHUNK, timeout=800)
            except self.usb_core.USBError:
                break
            if not chunk:
                break
            data += chunk
        return data.decode("ascii", errors="replace").strip()

    def bquery(self, command, timeout=15000):
        """Binary reply: 8 ASCII length digits, 1 delimiter byte, then payload."""
        self._flush()
        self._write(command, timeout)
        data = bytearray(self.ep_in.read(_READ_CHUNK, timeout=timeout))
        if len(data) < 9 or not data[:8].isdigit():
            raise UserFacingError(
                f"The analyzer gave an unexpected answer to {command}.\n"
                "Unplug and replug the USB cable, then try again.")
        needed = int(data[:8].decode("ascii")) + 9
        while len(data) < needed:
            chunk = self.ep_in.read(min(4096, needed - len(data) + _READ_CHUNK), timeout=timeout)
            if not chunk:
                break
            data += chunk
        return bytes(data[9:needed])

    def status(self):
        return {
            "mode": self.query(":MODE?"),
            "memory": self.query(":BDATA:MEMORY? 0"),
        }

    def event_counts(self, mem_no):
        reply = self.query(f":BDATA:EVENT:NUM? {mem_no}")
        try:
            return tuple(int(p) for p in reply.split(","))
        except ValueError:
            raise UserFacingError(f"Unexpected reply when counting events: {reply!r}")

    def screenshot(self):
        """The live screen as BMP bytes (240x320, 256-colour, RLE8)."""
        return self.bquery(":BCOPY?")

    def download(self, dest, mem_no=0):
        """Save one memory bank using the filenames HIOKI's own software uses."""
        os.makedirs(dest, exist_ok=True)
        ev_n, wdu_n, inr_n = self.event_counts(mem_no)
        jobs = [("3197set.set", f":BDATA:SYSTEM? {mem_no}"),
                ("97intvl.itv", f":BDATA:TIMEPLOT? {mem_no}"),
                ("97demand.dem", f":BDATA:DEMAND? {mem_no}"),
                ("97evmoni.mon", f":BDATA:EVENT:MONI? {mem_no}")]
        jobs += [(f"97evt{i:03d}.evt", f":BDATA:EVENT:LIST? {mem_no},{i}") for i in range(ev_n)]
        jobs += [(f"97dv{i:03d}.wdu", f":BDATA:EVENT:VOLT? {mem_no},{i}") for i in range(wdu_n)]
        # HIOKI's own app writes every inrush event to 97inrush.inr (each
        # overwriting the last) and View3197 expects that name; we keep it,
        # plus a numbered copy per event so none are lost.
        jobs += [(f"97inr{i:03d}.inr", f":BDATA:EVENT:INRUSH? {mem_no},{i}") for i in range(inr_n)]

        for n, (name, cmd) in enumerate(jobs, 1):
            print(f"  [{n:>3}/{len(jobs)}] {name} ...", end="", flush=True)
            try:
                payload = self.bquery(cmd)
            except self.usb_core.USBError as e:
                print()
                raise _explain_usb_error(e)
            with open(os.path.join(dest, name), "wb") as f:
                f.write(payload)
            if name.startswith("97inr"):
                with open(os.path.join(dest, "97inrush.inr"), "wb") as f:
                    f.write(payload)
            print(f" {_human_size(len(payload))}")
        return ev_n, wdu_n, inr_n


def _explain_usb_error(e):
    errno = getattr(e, "errno", None)
    if errno == 13:
        return UserFacingError(
            "Permission denied: your user account isn't allowed to use the analyzer.\n"
            "Fix: choose 'First-time setup' from the menu (or run:\n"
            "     python3 hioki3197_tool.py setup), then unplug and replug the cable.")
    if errno == 16:
        return UserFacingError(
            "The analyzer is busy -- another program is using it.\n"
            "Close any other HIOKI program, then unplug and replug the cable.")
    if errno in (19, 5):
        return UserFacingError(
            "Lost contact with the analyzer (unplugged, or it switched off?).\n"
            "Replug the cable and try again.")
    if errno == 110:
        return UserFacingError(
            "The analyzer stopped answering (timeout).\n"
            "Unplug and replug the cable, wait a few seconds, and try again.")
    return UserFacingError(f"USB error: {e}\nUnplug and replug the cable and try again.")


# ===========================================================================
# Reading the analyzer's file formats
# (all big-endian, laid out like Java's DataInputStream)
# ===========================================================================

# Cumulative day-of-year totals used by the analyzer's calendar arithmetic
_DAY_MONTH = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365]
_DAY_MONTH_LEAP = [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366]


def _read_time(raw, off):
    """Timestamp: int32 seconds since 1980, int16 milliseconds, 2 reserved."""
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
        raise UserFacingError(f"{what} doesn't look like a HIOKI 3197 file "
                              f"(it starts with {raw[:40]!r}).")


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
    off += 16 + 4 + 16 + 16  # reference voltage, freq/clamp/range flags, PT, CT
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
    """Multiplier from a raw event-waveform count to V (ch 0-2) or A (ch 3-6)."""
    if ch < 3:
        return settings["u_max"] * settings["u_adj"][ch]
    return settings["i_max"] * settings["i_adj"][ch - 3]


def demand_period_seconds(settings):
    return {0: 900, 1: 1800}.get(settings["demand"], 3600)


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
    integ_start, off = _read_time(raw, off + 4)
    if len(raw) < off + n * _INTERVAL_RECORD:
        raise UserFacingError("97intvl.itv is incomplete (the download may have been "
                              "interrupted). Download again.")
    nitems = len(INTERVAL_ITEMS)
    records = []
    for _ in range(n):
        t, off = _read_time(raw, off)
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
    start, off = _read_time(raw, off + 4)
    average = struct.unpack_from(">4f", raw, off)
    off += 16
    rows = [struct.unpack_from(">4f", raw, off + 16 * i) for i in range(n)]
    return {"start": start, "average": average, "rows": rows}


EVENT_NAMES = ["EVENT", "TRANSIENT", "SWELL", "DIP", "INTERRUPT", "INRUSH",
               "START", "STOP", "TIMER", "MANUAL"]
CH_NAMES = ["CH1", "CH2", "CH3", ""]
INOUT_NAMES = ["", "IN", "OUT", "---"]
WAVE_CHANNELS = ["U1", "U2", "U3", "I1", "I2", "I3", "I4"]
_DETAIL_SLOTS = 231
_OUT_EVENT_SLOTS = 19


def decode_event(raw):
    _check_sig(raw, "This is HIOKI 3197 EVENT DATA FILE", "Event file")
    off = 34 + 94
    event_id, = struct.unpack_from(">h", raw, off)
    detail_num, = struct.unpack_from(">h", raw, off + 4)
    off += 8

    details = []
    for _ in range(detail_num):
        t, off = _read_time(raw, off)
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

    wave_time, off = _read_time(raw, off)
    wave_num, pre_num = struct.unpack_from(">hh", raw, off)
    off += 4
    nch = len(WAVE_CHANNELS)
    samples = [struct.unpack_from(f">{nch}h", raw, off + 2 * nch * i) for i in range(wave_num)]
    return {"event_id": event_id, "details": details, "wave_time": wave_time,
            "pre_trigger": pre_num, "wave_raw": samples}


def _decode_trend(raw, sig, pad, what):
    """Shared body of .wdu (voltage) and .inr (inrush current) files."""
    _check_sig(raw, sig, what)
    off = len(sig) + pad
    _id, wave_num, pre_num, evt_id = struct.unpack_from(">4h", raw, off)
    off += 8
    if len(raw) < off + 12 * wave_num:
        raise UserFacingError(f"{what} is incomplete; download again.")
    samples = [struct.unpack_from(">3f", raw, off + 12 * i) for i in range(wave_num)]
    return {"event_id": evt_id, "pre_trigger": pre_num, "samples": samples}


def decode_wdu(raw):
    return _decode_trend(raw, "This is HIOKI 3197 DELTA-V DATA FILE", 92, "Voltage-event file")


def decode_inrush(raw):
    return _decode_trend(raw, "This is HIOKI 3197 INRUSH DATA FILE", 93, "Inrush file")


# ===========================================================================
# Writing CSV files
# ===========================================================================

def _read(folder, name):
    with open(os.path.join(folder, name), "rb") as f:
        return f.read()


def _open_csv(path):
    f = open(path, "w", newline="")
    return f, csv.writer(f)


def decode_folder(folder, all_stats=False):
    """Turn every raw file in `folder` into CSVs; return a summary dict."""
    if not os.path.exists(os.path.join(folder, "3197set.set")):
        raise UserFacingError(
            f"{folder} has no 3197set.set file, so it isn't a HIOKI 3197 download.")
    summary = {"folder": folder}

    s = decode_settings(_read(folder, "3197set.set"))
    summary["settings"] = s
    with open(os.path.join(folder, "settings.txt"), "w") as f:
        f.write(f"Serial number:   {s['serial']}\n")
        f.write(f"Name (alias):    {s['alias'] or '(none)'}\n")
        f.write(f"Wiring code:     {s['wiring']}\n")
        f.write(f"Voltage range:   {s['u_max']:g} V\n")
        f.write(f"Current range:   {s['i_max']:g} A\n")
        f.write(f"Demand period:   {demand_period_seconds(s) // 60} min\n")
    print(f"  Analyzer serial {s['serial']}, ranges {s['u_max']:g} V / {s['i_max']:g} A")

    if os.path.exists(os.path.join(folder, "97intvl.itv")):
        d = decode_interval(_read(folder, "97intvl.itv"))
        recs = d["records"]
        stats = INTERVAL_STATS if all_stats else ["Ave"]
        idx = [INTERVAL_STATS.index(x) for x in stats]
        f, w = _open_csv(os.path.join(folder, "97intvl.csv"))
        with f:
            w.writerow(["time", "status"]
                       + [f"{item}_{x}" for item in INTERVAL_ITEMS for x in stats]
                       + DU_NAMES + INTEG_NAMES)
            for r in recs:
                row = [_iso(r["time"]), r["status"]]
                for k in range(len(INTERVAL_ITEMS)):
                    row += [r["stats"][INTERVAL_STATS[j]][k] for j in idx]
                w.writerow(row + list(r["du"]) + list(r["integ"]))
        summary["intervals"] = recs
        if recs:
            span = recs[-1]["time"] - recs[0]["time"]
            print(f"  Interval log: {len(recs)} readings from {recs[0]['time']:%Y-%m-%d %H:%M} "
                  f"to {recs[-1]['time']:%Y-%m-%d %H:%M} ({_human_span(span)})")
        else:
            print("  Interval log: empty (nothing was recorded)")

    if os.path.exists(os.path.join(folder, "97demand.dem")):
        d = decode_demand(_read(folder, "97demand.dem"))
        period = dt.timedelta(seconds=demand_period_seconds(s))
        f, w = _open_csv(os.path.join(folder, "demand.csv"))
        with f:
            w.writerow(["interval", "time"] + DEMAND_ITEMS)
            for i, row in enumerate(d["rows"]):
                w.writerow([i, (d["start"] + i * period).strftime("%Y-%m-%d %H:%M:%S")]
                           + [round(v, 3) for v in row])
        summary["demand"] = d
        print(f"  Demand: {len(d['rows'])} readings")

    gains = [waveform_gain(s, ch) for ch in range(len(WAVE_CHANNELS))]
    events = []
    evt_files = sorted(glob.glob(os.path.join(folder, "97evt[0-9][0-9][0-9].evt")))
    f, w = _open_csv(os.path.join(folder, "events_summary.csv"))
    with f:
        w.writerow(["file", "event_id", "time", "type", "channel", "in_out",
                    "duration_ms", "value_volts"])
        for path in evt_files:
            name = os.path.basename(path)
            ev = decode_event(_read(folder, name))
            for d in ev["details"]:
                w.writerow([name, ev["event_id"], _iso(d["time"]), d["type"], d["channel"],
                            d["in_out"],
                            "" if d["duration_ms"] is None else d["duration_ms"],
                            "" if d["value_volts"] is None else f"{d['value_volts']:.3f}"])
                events.append(d)
            wf, ww = _open_csv(path[:-4] + "_waveform.csv")
            with wf:
                ww.writerow(["sample"] + WAVE_CHANNELS)
                for i, sample in enumerate(ev["wave_raw"]):
                    ww.writerow([i] + [round(v * g, 3) for v, g in zip(sample, gains)])
    summary["events"] = events
    print(f"  Events: {len(notable_events(events))} recorded "
          f"(plus start/stop markers), from {len(evt_files)} event files")

    for pattern, decoder, channels, label in (
            ("97dv[0-9][0-9][0-9].wdu", decode_wdu, ["U1", "U2", "U3"], "Voltage-event RMS trends"),
            ("97inr[0-9][0-9][0-9].inr", decode_inrush, ["I1", "I2", "I3"], "Inrush current trends")):
        files = sorted(glob.glob(os.path.join(folder, pattern)))
        if pattern.startswith("97inr") and not files:
            # A download made by HIOKI's own app has only the last inrush event
            files = glob.glob(os.path.join(folder, "97inrush.inr"))
        for path in files:
            d = decoder(_read(folder, os.path.basename(path)))
            tf, tw = _open_csv(path[:-4] + ".csv")
            with tf:
                tw.writerow(["sample"] + channels)
                for i, sample in enumerate(d["samples"]):
                    tw.writerow([i] + [round(v, 3) for v in sample])
        if files:
            print(f"  {label}: {len(files)}")

    write_readme(folder, summary)
    return summary


def notable_events(events):
    """Event onsets worth showing a person: not START/STOP and not the 'end' half."""
    return [e for e in events if e["type"] not in ("START", "STOP") and e["in_out"] != "OUT"]


def write_readme(folder, summary):
    """A plain-English README.txt describing what's in this download."""
    s = summary["settings"]
    lines = [
        "HIOKI 3197 download",
        "=" * 19,
        "",
        f"Analyzer serial number: {s['serial']}",
        f"Decoded on: {dt.datetime.now():%Y-%m-%d %H:%M}",
        "",
    ]
    recs = summary.get("intervals") or []
    if recs:
        lines += [
            f"Recording: {recs[0]['time']:%Y-%m-%d %H:%M} to {recs[-1]['time']:%Y-%m-%d %H:%M} "
            f"({_human_span(recs[-1]['time'] - recs[0]['time'])}, {len(recs)} readings)",
            "",
            "Averages over the whole recording (channels with no signal left out):",
        ]
        for key, label, unit in CHART_ITEMS:
            vals = [r["stats"]["Ave"][INTERVAL_ITEMS.index(key)] for r in recs]
            if _has_signal(vals):
                lines.append(f"  {label:<32} average {sum(vals) / len(vals):9.3f} {unit:<4}"
                             f"  (lowest {min(vals):.3f}, highest {max(vals):.3f})")
        lines.append("")

    events = notable_events(summary.get("events", []))
    if events:
        lines.append(f"Events ({len(events)}):")
        for e in events:
            lines.append(f"  {_iso(e['time'])}  {e['type']:<10} {e['channel']}")
    else:
        lines.append("Events: none recorded.")
    lines += [
        "",
        "Files:",
        "  97intvl.csv           main data, one row per interval (open in a spreadsheet)",
        "  events_summary.csv    every event: time, type, channel, volts, duration",
        "  demand.csv            power demand readings",
        "  *_waveform.csv        waveform captured at each event (V and A)",
        "  97dvNNN.csv           RMS voltage around each dip/swell/interruption",
        "  97inrNNN.csv          current around each inrush event",
        "  settings.txt          analyzer serial number and ranges",
        "  *.set *.itv *.evt...  original files; these open in HIOKI's View3197 viewer",
        "",
        "Column names are explained at the top of hioki3197_tool.py.",
    ]
    with open(os.path.join(folder, "README.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


# ===========================================================================
# Charts (SVG: opens in any web browser, no extra software needed)
# ===========================================================================

# (interval-log item, friendly name, unit)
CHART_ITEMS = [
    ("U1", "Voltage, channel 1 (U1)", "V"),
    ("U2", "Voltage, channel 2 (U2)", "V"),
    ("U3", "Voltage, channel 3 (U3)", "V"),
    ("I1", "Current, channel 1 (I1)", "A"),
    ("I2", "Current, channel 2 (I2)", "A"),
    ("I3", "Current, channel 3 (I3)", "A"),
    ("I4", "Current, channel 4 (I4)", "A"),
    ("Freq", "Frequency", "Hz"),
    ("Psum", "Real power (Psum)", "W"),
    ("Qsum", "Reactive power (Qsum)", "var"),
    ("Ssum", "Apparent power (Ssum)", "VA"),
    ("PFsum", "Power factor (PFsum)", ""),
    ("THD1", "Voltage distortion, ch 1 (THD1)", "%"),
    ("THD2", "Voltage distortion, ch 2 (THD2)", "%"),
    ("THD3", "Voltage distortion, ch 3 (THD3)", "%"),
]


def _has_signal(vals):
    # Unconnected inputs read exactly zero
    return any(abs(v) > 1e-3 for v in vals)


def available_chart_items(recs):
    """CHART_ITEMS that actually carry data (all-zero channels were unconnected)."""
    out = []
    for key, label, unit in CHART_ITEMS:
        vals = [r["stats"]["Ave"][INTERVAL_ITEMS.index(key)] for r in recs]
        out.append((key, label, unit, _has_signal(vals)))
    return out


def _nice_step(span, target):
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _bucket(times, ave, lo, hi, max_points=2500):
    """Thin very long recordings down to max_points, keeping each bucket's extremes."""
    n = len(times)
    if n <= max_points:
        return times, ave, lo, hi
    size = math.ceil(n / max_points)
    t2, a2, l2, h2 = [], [], [], []
    for i in range(0, n, size):
        j = min(n, i + size)
        t2.append(times[i + (j - i) // 2])
        a2.append(sum(ave[i:j]) / (j - i))
        l2.append(min(lo[i:j]))
        h2.append(max(hi[i:j]))
    return t2, a2, l2, h2


def make_chart(folder, key, summary=None):
    """Write chart_<key>.svg for one interval-log item; return its path."""
    if summary is None:
        if not os.path.exists(os.path.join(folder, "97intvl.itv")):
            raise UserFacingError(f"{folder} has no interval log (97intvl.itv) to chart.")
        recs = decode_interval(_read(folder, "97intvl.itv"))["records"]
        events = []
        for path in sorted(glob.glob(os.path.join(folder, "97evt[0-9][0-9][0-9].evt"))):
            events += decode_event(_read(folder, os.path.basename(path)))["details"]
    else:
        recs, events = summary.get("intervals") or [], summary.get("events", [])
    if len(recs) < 2:
        raise UserFacingError("Not enough readings in the interval log to draw a chart.")

    matches = [c for c in CHART_ITEMS if c[0].lower() == key.lower()]
    if not matches:
        raise UserFacingError(f"Unknown item {key!r}. Choose one of: "
                              + ", ".join(c[0] for c in CHART_ITEMS))
    key, label, unit = matches[0]
    k = INTERVAL_ITEMS.index(key)
    times = [r["time"] for r in recs]
    ave = [r["stats"]["Ave"][k] for r in recs]
    lo = [r["stats"]["Min"][k] for r in recs]
    hi = [r["stats"]["Max"][k] for r in recs]
    mean = sum(ave) / len(ave)
    i_min = min(range(len(ave)), key=ave.__getitem__)
    i_max = max(range(len(ave)), key=ave.__getitem__)
    times, ave_b, lo, hi = _bucket(times, ave, lo, hi)

    W, H = 1000, 460
    L, R, T, B = 70, 90, 78, 70  # plot margins
    pw, ph = W - L - R, H - T - B
    t0, t1 = times[0], times[-1]
    tspan = max((t1 - t0).total_seconds(), 1)
    ymin, ymax = min(lo), max(hi)
    if ymax - ymin < 1e-9:
        ymin, ymax = ymin - 1, ymax + 1
    ystep = _nice_step(ymax - ymin, 5)
    ymin = math.floor(ymin / ystep) * ystep
    ymax = math.ceil(ymax / ystep) * ystep

    def x(t):
        return L + (t - t0).total_seconds() / tspan * pw

    def y(v):
        return T + (ymax - v) / (ymax - ymin) * ph

    decimals = max(0, -math.floor(math.log10(ystep)))
    if round(ystep * 10 ** decimals, 6) % 1:
        decimals += 1  # steps like 2.5 need one more digit

    def fmt(v):  # axis labels
        return f"{v:.{decimals}f}"

    def fmtv(v):  # data values: one digit finer than the axis
        return f"{v:.{min(decimals + 1, 3)}f}"

    out = []
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
               f'width="{W}" height="{H}" font-family="system-ui, sans-serif">')
    out.append("""<style>
  .bg{fill:#fcfcfb} .ink{fill:#0b0b0b} .ink2{fill:#52514e} .grid{stroke:#e4e3df}
  .band{fill:#cde2fb} .line{stroke:#2a78d6} .ref{stroke:#52514e} .evt{stroke:#52514e;fill:#52514e}
  @media (prefers-color-scheme: dark){
    .bg{fill:#1a1a19} .ink{fill:#f0efec} .ink2{fill:#c3c2b7} .grid{stroke:#383835}
    .band{fill:#1c3a63} .line{stroke:#3987e5} .ref{stroke:#c3c2b7} .evt{stroke:#c3c2b7;fill:#c3c2b7}
  }
</style>""")
    out.append(f'<rect class="bg" width="{W}" height="{H}"/>')
    span_fmt = "%d %b %Y %H:%M" + (":%S" if tspan < 3600 else "")
    out.append(f'<text class="ink" x="{L}" y="30" font-size="20" font-weight="700">{label}</text>')
    out.append(f'<text class="ink2" x="{L}" y="52" font-size="13">HIOKI 3197 interval log · '
               f'{len(recs)} readings · {t0.strftime(span_fmt)} to {t1.strftime(span_fmt)} · '
               f'average {fmtv(mean)} {unit}</text>')

    # y grid + labels
    v = ymin
    while v <= ymax + ystep / 2:
        yy = y(v)
        out.append(f'<line class="grid" x1="{L}" x2="{L + pw}" y1="{yy:.1f}" y2="{yy:.1f}"/>')
        out.append(f'<text class="ink2" x="{L - 8}" y="{yy + 4:.1f}" font-size="12" '
                   f'text-anchor="end">{fmt(v)}</text>')
        v += ystep
    if unit:
        out.append(f'<text class="ink2" x="{L - 8}" y="{T - 12}" font-size="12" '
                   f'text-anchor="end">{unit}</text>')

    # x ticks at round times
    steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 3 * 3600, 6 * 3600, 12 * 3600, 86400,
             2 * 86400, 7 * 86400, 14 * 86400, 30 * 86400]
    xstep = next((s for s in steps if tspan / s <= 8), steps[-1])
    midnight = dt.datetime(t0.year, t0.month, t0.day)
    n = math.ceil((t0 - midnight).total_seconds() / xstep)
    tick = midnight + dt.timedelta(seconds=n * xstep)
    if xstep >= 86400:
        tfmt = "%d %b"
    elif tspan > 86400:
        tfmt = "%a %H:%M"
    else:
        tfmt = "%H:%M:%S" if xstep < 60 else "%H:%M"
    while tick <= t1:
        xx = x(tick)
        out.append(f'<line class="grid" x1="{xx:.1f}" x2="{xx:.1f}" y1="{T + ph}" y2="{T + ph + 5}"/>')
        out.append(f'<text class="ink2" x="{xx:.1f}" y="{T + ph + 20}" font-size="12" '
                   f'text-anchor="middle">{tick.strftime(tfmt)}</text>')
        tick += dt.timedelta(seconds=xstep)

    # min-max band, then the average line
    pts_hi = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in zip(times, hi))
    pts_lo = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in reversed(list(zip(times, lo))))
    out.append(f'<polygon class="band" points="{pts_hi} {pts_lo}"/>')
    pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in zip(times, ave_b))
    out.append(f'<polyline class="line" fill="none" stroke-width="1.6" '
               f'stroke-linejoin="round" points="{pts}"/>')

    # mean reference line, labelled at the right
    ym = y(mean)
    out.append(f'<line class="ref" x1="{L}" x2="{L + pw}" y1="{ym:.1f}" y2="{ym:.1f}" '
               f'stroke-width="0.8" opacity="0.6"/>')
    out.append(f'<text class="ink2" x="{L + pw + 6}" y="{ym + 4:.1f}" font-size="12">'
               f'mean {fmtv(mean)}</text>')

    # label the overall highest and lowest averages
    for i, dy in ((i_max, -8), (i_min, 16)):
        t = recs[i]["time"]
        ly = min(max(y(ave[i]) + dy, T + 12), T + ph - 4)  # keep inside the plot
        out.append(f'<text class="ink" x="{x(t):.1f}" y="{ly:.1f}" font-size="12" '
                   f'text-anchor="middle">{fmtv(ave[i])} {unit}</text>')

    # event markers
    evs = [e for e in notable_events(events) if t0 <= e["time"] <= t1]
    for e in evs:
        xx = x(e["time"])
        out.append(f'<line class="evt" x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{T + ph}" '
                   f'stroke-width="0.8" stroke-dasharray="3 3"/>')
        out.append(f'<path class="evt" d="M{xx - 5:.1f},{T - 9} L{xx + 5:.1f},{T - 9} '
                   f'L{xx:.1f},{T - 1} Z"><title>{e["type"]} {e["channel"]} '
                   f'{_iso(e["time"])}</title></path>')

    # legend
    ly = H - 20
    out.append(f'<rect class="band" x="{L}" y="{ly - 10}" width="18" height="10"/>')
    out.append(f'<text class="ink2" x="{L + 24}" y="{ly}" font-size="12">'
               f'Lowest to highest within each interval</text>')
    out.append(f'<line class="line" x1="{L + 280}" x2="{L + 298}" y1="{ly - 5}" y2="{ly - 5}" '
               f'stroke-width="2"/>')
    out.append(f'<text class="ink2" x="{L + 304}" y="{ly}" font-size="12">Interval average</text>')
    if evs:
        out.append(f'<path class="evt" d="M{L + 430},{ly - 10} L{L + 440},{ly - 10} '
                   f'L{L + 435},{ly - 2} Z"/>')
        out.append(f'<text class="ink2" x="{L + 446}" y="{ly}" font-size="12">'
                   f'Event ({len(evs)}; hover for details)</text>')
    out.append("</svg>")

    path = os.path.join(folder, f"chart_{key}.svg")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return path


# ===========================================================================
# HIOKI's own viewer (View3197.jar)
# ===========================================================================

def find_viewer_jar():
    candidates = [os.environ.get("HIOKI_VIEWER_JAR", "")]
    for base in (SCRIPT_DIR, os.getcwd(), os.path.expanduser("~/Documents/HIOKI")):
        candidates += [os.path.join(base, "View3197.jar"),
                       os.path.join(base, "3197", "View3197.jar")]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def launch_viewer(jar, folder=None):
    if shutil.which("java") is None:
        raise UserFacingError("Java isn't installed, and HIOKI's viewer needs it.\n"
                              "Install it with:   sudo apt install default-jre")
    jar_dir = os.path.dirname(os.path.abspath(jar))
    ini = os.path.join(jar_dir, "view3197.ini")
    if folder and os.path.isfile(ini):
        # Make File > Open start in the chosen download folder
        with open(ini) as f:
            lines = f.readlines()
        lines = [f"OpenDir={os.path.abspath(folder)}\n" if ln.startswith("OpenDir=") else ln
                 for ln in lines]
        with open(ini, "w") as f:
            f.writelines(lines)
    subprocess.Popen(["java", "-jar", os.path.basename(jar)], cwd=jar_dir,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


# ===========================================================================
# Small helpers
# ===========================================================================

def _human_size(n):
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def _human_span(td):
    secs = int(td.total_seconds())
    if secs < 120:
        return f"{secs} s"
    if secs < 7200:
        return f"{secs // 60} min"
    if secs < 172800:
        return f"{secs / 3600:.1f} hours"
    return f"{secs / 86400:.1f} days"


def open_in_desktop(path):
    """Open a file or folder with the desktop's default app; quietly skip if impossible."""
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)
    except Exception:
        pass


def new_download_folder():
    return os.path.join(DATA_DIR, dt.datetime.now().strftime("%Y-%m-%d_%H%M%S"))


def find_download_folders():
    """Folders holding a HIOKI download, newest first."""
    found = set()
    for base in (DATA_DIR, os.path.join(SCRIPT_DIR, "downloaded_data"), os.getcwd()):
        for p in glob.glob(os.path.join(base, "*", "3197set.set")) + \
                glob.glob(os.path.join(base, "3197set.set")):
            found.add(os.path.dirname(os.path.abspath(p)))
    return sorted(found, key=os.path.getmtime, reverse=True)


# ===========================================================================
# The interactive menu
# ===========================================================================

def ask(prompt, default=""):
    try:
        answer = input(prompt).strip()
    except EOFError:
        answer = ""
    return answer or default


def ask_yes(prompt, default=True):
    hint = "[Y/n]" if default else "[y/N]"
    answer = ask(f"{prompt} {hint} ").lower()
    return default if not answer else answer.startswith("y")


def _clean_path(p):
    # Paths dragged into a terminal arrive quoted, sometimes with a file:// prefix
    p = p.strip().strip("'\"")
    if p.startswith("file://"):
        p = p[7:]
    return os.path.expanduser(p)


def pick_folder(purpose):
    """Let the person choose a download folder; returns a path or None."""
    folders = find_download_folders()
    print(f"\nWhich download do you want to {purpose}?")
    for i, f in enumerate(folders[:9], 1):
        print(f"  {i}. {os.path.relpath(f, os.getcwd()) if f.startswith(os.getcwd()) else f}")
    if folders:
        print("  Or type (or drag in) the path of another folder.")
        answer = ask("Number or path [1]: ", "1")
    else:
        print("  (No downloads found yet next to this tool.)")
        answer = ask("Type (or drag in) the folder's path, or press Enter to go back: ")
        if not answer:
            return None
    if answer.isdigit() and 1 <= int(answer) <= min(9, len(folders)):
        return folders[int(answer) - 1]
    path = _clean_path(answer)
    if not os.path.isdir(path):
        print(f"  '{path}' isn't a folder.")
        return None
    return path


def menu_check():
    print("\nLooking for the analyzer...")
    with Analyzer() as dev:
        st = dev.status()
        ev_n, wdu_n, inr_n = dev.event_counts(0)
    print("  Connected to the HIOKI 3197. Communication works.")
    print(f"  Stored in memory: {ev_n} event file(s), {wdu_n} voltage-event "
          f"trend(s), {inr_n} inrush trend(s).")
    print(f"  (Mode code {st['mode']}, memory code {st['memory']}; for reference only.)")
    print("\n  Next: option 3 downloads everything.")


def menu_screenshot(path=None):
    path = path or os.path.join(DATA_DIR, dt.datetime.now().strftime("screen_%Y-%m-%d_%H%M%S.bmp"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    print("\nAsking the analyzer for a picture of its screen...")
    with Analyzer() as dev:
        bmp = dev.screenshot()
    bmp_path = os.path.splitext(path)[0] + ".bmp"
    with open(bmp_path, "wb") as f:
        f.write(bmp)
    result = bmp_path
    try:
        from PIL import Image
        png_path = os.path.splitext(path)[0] + ".png"
        Image.open(bmp_path).convert("RGB").save(png_path)
        result = png_path
        print(f"  Saved {png_path}  (and the original {os.path.basename(bmp_path)})")
    except ImportError:
        print(f"  Saved {bmp_path}")
        print("  (Install Pillow to also get a PNG: sudo apt install python3-pil)")
    return result


def menu_download(folder=None, mem_no=0, all_stats=False, interactive=True):
    folder = folder or new_download_folder()
    print("\nDownloading everything stored in the analyzer.")
    print("This only reads data; nothing on the analyzer is changed or deleted.")
    print("Large recordings can take a minute or two. Please don't touch the")
    print("analyzer's buttons until it finishes.\n")
    with Analyzer() as dev:
        dev.download(folder, mem_no)
    print("\nDownload complete. Converting to spreadsheet files...")
    summary = decode_folder(folder, all_stats)
    after_decode(folder, summary, interactive)
    return folder


def after_decode(folder, summary, interactive):
    print(f"\nAll done. Everything is in:\n  {os.path.abspath(folder)}")
    print("  Start with README.txt (a summary) and 97intvl.csv (the main data).")
    events = notable_events(summary.get("events", []))
    if events:
        print(f"\n  {len(events)} event(s) were recorded:")
        for e in events[:10]:
            print(f"    {e['time']:%Y-%m-%d %H:%M:%S}  {e['type']:<10} {e['channel']}")
        if len(events) > 10:
            print(f"    ... and {len(events) - 10} more (see events_summary.csv)")
    if interactive and ask_yes("\nOpen the folder now?"):
        open_in_desktop(folder)


def menu_decode(folder=None, all_stats=False, interactive=True):
    folder = folder or pick_folder("convert")
    if not folder:
        return
    print(f"\nConverting {folder} ...")
    summary = decode_folder(folder, all_stats)
    after_decode(folder, summary, interactive)


def menu_chart(folder=None, key=None, interactive=True):
    folder = folder or pick_folder("chart")
    if not folder:
        return
    if not os.path.exists(os.path.join(folder, "97intvl.itv")):
        raise UserFacingError(f"{folder} has no interval log (97intvl.itv) to chart.")
    recs = decode_interval(_read(folder, "97intvl.itv"))["records"]
    if not key:
        items = available_chart_items(recs)
        print("\nWhat do you want to chart?")
        for i, (k, label, unit, ok) in enumerate(items, 1):
            note = "" if ok else "   (no data; probably not connected)"
            print(f"  {i:>2}. {label}{note}")
        answer = ask("Number [1]: ", "1")
        if not answer.isdigit() or not 1 <= int(answer) <= len(items):
            print("  That isn't one of the numbers listed.")
            return
        key = items[int(answer) - 1][0]
    path = make_chart(folder, key)
    print(f"\n  Chart saved: {path}")
    print("  It opens in any web browser. Hover over the triangles to see event details.")
    if interactive and ask_yes("  Open it now?"):
        open_in_desktop(path)


def menu_viewer(folder=None, interactive=True):
    jar = find_viewer_jar()
    if not jar and interactive:
        print("\nHIOKI's viewer (View3197.jar) wasn't found next to this tool.")
        print("It comes on the CD / download that ships with the analyzer.")
        answer = ask("Type (or drag in) the path of View3197.jar, or press Enter to go back: ")
        if answer:
            jar = _clean_path(answer)
            if not os.path.isfile(jar):
                raise UserFacingError(f"'{jar}' isn't a file.")
    if not jar:
        raise UserFacingError(
            "HIOKI's viewer (View3197.jar) wasn't found. Put it next to this tool,\n"
            "or set the HIOKI_VIEWER_JAR environment variable to its path.")
    if interactive and folder is None:
        folder = pick_folder("open in the viewer")
    launch_viewer(jar, folder)
    print(f"\n  Started HIOKI's viewer ({jar}). Its window should appear shortly.")
    if folder:
        print(f"  In the viewer, choose File > Open and pick:\n    {os.path.abspath(folder)}")


def menu_setup(interactive=True):
    print("\nFirst-time setup: allow your user account to use the analyzer over USB.")
    if not sys.platform.startswith("linux"):
        print("  This step is only needed (and only works) on Linux.")
        return
    if os.path.exists(UDEV_RULE_PATH):
        with open(UDEV_RULE_PATH) as f:
            if f.read() == UDEV_RULE:
                print(f"  Already done: {UDEV_RULE_PATH} is installed.")
                print("  If you still get 'Permission denied', unplug and replug the cable.")
                return
    print(f"  This installs a one-line permission rule as {UDEV_RULE_PATH}:")
    print(f"    {UDEV_RULE.strip()}")
    print("  It needs administrator rights, so you'll be asked for your password.")
    if interactive and not ask_yes("  Go ahead?"):
        return
    try:
        subprocess.run(["sudo", "tee", UDEV_RULE_PATH], input=UDEV_RULE.encode(),
                       stdout=subprocess.DEVNULL, check=True)
        subprocess.run(["sudo", "udevadm", "control", "--reload-rules"], check=True)
        subprocess.run(["sudo", "udevadm", "trigger"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise UserFacingError("Setup didn't complete (wrong password, or sudo isn't available).\n"
                              "Ask whoever administers this computer to run this tool's setup.")
    print("\n  Done. Now UNPLUG the analyzer's USB cable and plug it back in.")
    print("  Then choose option 1 to check the connection.")


MENU = [
    ("Check the connection", menu_check),
    ("Take a picture of the analyzer's screen", menu_screenshot),
    ("Download everything from the analyzer (and convert it to spreadsheets)", menu_download),
    ("Convert a download you already have", menu_decode),
    ("Make a chart (voltage, current, frequency...)", menu_chart),
    ("Open HIOKI's own viewer program", menu_viewer),
    ("First-time setup (USB permission, Linux)", menu_setup),
    ("Help", None),
]


def run_menu():
    print(__doc__.split("QUICK START")[0].rstrip("- \n"))
    while True:
        print("\n" + "-" * 60)
        print("What would you like to do?\n")
        for i, (label, _) in enumerate(MENU, 1):
            print(f"  {i}. {label}")
        print("  0. Quit")
        choice = ask("\nType a number and press Enter: ")
        if choice in ("0", "q", "quit", "exit"):
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            print("Please type one of the numbers shown.")
            continue
        label, action = MENU[int(choice) - 1]
        if action is None:
            print(__doc__)
            continue
        try:
            action()
        except UserFacingError as e:
            print(f"\nProblem: {e}")
        except KeyboardInterrupt:
            print("\n  Cancelled.")
        ask("\nPress Enter to return to the menu...")


# ===========================================================================
# Command line
# ===========================================================================

def run_command(args):
    def flag(name):
        if name in args:
            args.remove(name)
            return True
        return False

    def option(name):
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args):
                raise UserFacingError(f"{name} needs a value.")
            val = args[i + 1]
            del args[i:i + 2]
            return val
        return None

    cmd = args.pop(0)
    all_stats = flag("--all-stats")
    mem = option("--mem")
    try:
        mem = int(mem or 0)
    except ValueError:
        raise UserFacingError("--mem needs a number, e.g. --mem 0")

    if cmd in ("-h", "--help", "help"):
        print(__doc__)
    elif cmd == "check":
        menu_check()
    elif cmd == "screenshot":
        menu_screenshot(args[0] if args else None)
    elif cmd == "download":
        menu_download(args[0] if args else None, mem, all_stats, interactive=False)
    elif cmd == "decode":
        if not args:
            raise UserFacingError("Usage: hioki3197_tool.py decode <folder>")
        menu_decode(args[0], all_stats, interactive=False)
    elif cmd == "chart":
        if not args:
            raise UserFacingError("Usage: hioki3197_tool.py chart <folder> [item]\n"
                                  "Items: " + ", ".join(c[0] for c in CHART_ITEMS))
        menu_chart(args[0], args[1] if len(args) > 1 else "U1", interactive=False)
    elif cmd == "viewer":
        menu_viewer(args[0] if args else None, interactive=False)
    elif cmd == "setup":
        menu_setup(interactive=False)
    else:
        raise UserFacingError(f"Unknown command {cmd!r}. Run with --help to see the commands.")


def main():
    if sys.version_info < (3, 6):
        sys.exit("This tool needs Python 3.6 or newer.")
    args = sys.argv[1:]
    try:
        if args:
            run_command(args)
        elif sys.stdin.isatty():
            run_menu()
        else:
            print(__doc__)
    except UserFacingError as e:
        sys.exit(f"\nProblem: {e}")
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")


if __name__ == "__main__":
    main()
