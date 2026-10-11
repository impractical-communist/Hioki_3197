"""
Decoder for HIOKI 3197 `97intvl.itv` time-plot (interval log) files
(downloaded via `:BDATA:TIMEPLOT?`).

Reverse-engineered from `view3197/TpData.class` (decompiled with
`javap`). This is the biggest of the stored-data formats: one row per
logging interval, each holding 32 measurement items (voltage, current,
peaks, power, THD, unbalance...) x 3 statistics (Max/Min/Ave over the
interval), plus per-channel voltage extremes and cumulative energy.

Binary layout (big-endian, Java `DataInputStream` style):

    [38 bytes]  ASCII signature, "This is HIOKI 3197 TIME_PLOT DATA FILE..."
    [90 bytes]  reserved
    int32       wave_num         -- number of logged intervals
    Eptime      integ_start_time -- 8 bytes, energy-integration start time

    -- wave_num interval records, each 436 bytes:
    for i in range(wave_num):
        Eptime  time             -- this interval's timestamp
        int32   status           -- status/flags bitfield (not decoded)
        float32[6]  du           -- Umax(ch1-3), Umin(ch1-3), volts
        float32[3][32] data      -- stat-major (Max,Min,Ave) x ITEM_NAMES
        float32[4]  integ        -- WP+, WP-, WQ_LAG, WQ_LEAD (Wh/varh, cumulative)

Verified against three real downloaded files of very different sizes --
3192, 13656, and 794096 bytes -- each resolves to an exact integer
`wave_num` (7, 31, and 1821 respectively) under this 436-bytes-per-record
layout, with zero leftover bytes.

Values are already real engineering units (V, A, W, var, VA, %, Hz,
Wh/varh) -- same pre-scaled-float convention as every other HIOKI 3197
data file decoded in this project; no gain lookup needed.
"""

import struct

from .eventfile import _read_eptime

HEADER_SIG_LEN = 38
HEADER_PAD_LEN = 90
RECORD_SIZE = 436

ITEM_NAMES = [
    "Freq", "U1", "I1", "U2", "I2", "U3", "I3", "I4",
    "U1peak+", "U1peak-", "I1peak+", "I1peak-",
    "U2peak+", "U2peak-", "I2peak+", "I2peak-",
    "U3peak+", "U3peak-", "I3peak+", "I3peak-",
    "I4peak+", "I4peak-",
    "Uave", "Iave", "Psum", "Qsum", "Ssum", "PFsum",
    "THD1", "THD2", "THD3", "Uunb",
]
STAT_NAMES = ["Max", "Min", "Ave"]
DU_NAMES = ["Umax1", "Umax2", "Umax3", "Umin1", "Umin2", "Umin3"]
INTEG_NAMES = ["WP+", "WP-", "WQ_LAG", "WQ_LEAD"]


class IntervalFileError(Exception):
    pass


def decode_interval_file(raw):
    if len(raw) < HEADER_SIG_LEN:
        raise IntervalFileError("File too short to contain a header")
    sig = raw[:HEADER_SIG_LEN].decode("ascii", errors="replace")
    if not sig.startswith("This is HIOKI 3197 TIME_PLOT DATA FILE"):
        raise IntervalFileError(f"Unexpected file signature: {sig!r}")

    off = HEADER_SIG_LEN + HEADER_PAD_LEN
    wave_num, = struct.unpack_from(">i", raw, off)
    off += 4
    integ_start_time, off = _read_eptime(raw, off)

    expected = off + wave_num * RECORD_SIZE
    if len(raw) < expected:
        raise IntervalFileError(
            f"File too short: expected >= {expected} bytes for wave_num={wave_num}, got {len(raw)}"
        )

    records = []
    for _ in range(wave_num):
        t, off = _read_eptime(raw, off)
        status, = struct.unpack_from(">i", raw, off)
        off += 4
        du = dict(zip(DU_NAMES, struct.unpack_from(">6f", raw, off)))
        off += 24

        items = {name: {} for name in ITEM_NAMES}
        for stat in STAT_NAMES:
            vals = struct.unpack_from(f">{len(ITEM_NAMES)}f", raw, off)
            off += 4 * len(ITEM_NAMES)
            for name, v in zip(ITEM_NAMES, vals):
                items[name][stat] = v

        integ = dict(zip(INTEG_NAMES, struct.unpack_from(">4f", raw, off)))
        off += 16

        records.append({
            "time": t,
            "status": status,
            "du": du,
            "items": items,
            "integ": integ,
        })

    return {
        "wave_num": wave_num,
        "integ_start_time": integ_start_time,
        "item_names": ITEM_NAMES,
        "stat_names": STAT_NAMES,
        "records": records,
    }
