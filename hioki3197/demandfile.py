"""
Decoder for HIOKI 3197 `97demand.dem` demand-data files (downloaded via
`:BDATA:DEMAND?`).

Reverse-engineered from `view3197/TpDemData.class` (decompiled with
`javap`).

Binary layout (big-endian, Java `DataInputStream` style):

    [35 bytes]  ASCII signature, "This is HIOKI 3197 DEMAND DATA FILE..."
    [93 bytes]  reserved
    int32       wave_num        -- number of logged demand intervals (0 if none yet)
    Eptime      start_time      -- 8 bytes, see eventfile.py
    float32[4]  dem_ave         -- session-average demand, one per ITEM_NAMES
    if wave_num > 0:
        float32[wave_num][4]    -- per-interval demand, sample-major

Each interval's timestamp is `start_time + i * demand_period_seconds`,
where `demand_period_seconds` comes from the device's demand-period
setting in 3197set.set (Set97.getDemandSec(): 900/1800/3600s -- see
`setfile.decode_set_file()`'s `demand` field).

Values are already real engineering units (Watts / var) in the file --
confirmed the same way as the `.evt` event magnitude: `TpDemData.saveData()`
passes them straight into `Set97.meas2a()` for display formatting with no
separate scaling step.
"""

import struct

from .eventfile import _read_eptime

HEADER_SIG_LEN = 35
HEADER_PAD_LEN = 93
ITEM_NAMES = ["Pdem+", "Pdem-", "Qdem_LAG", "Qdem_LEAD"]

DEMAND_PERIOD_SECONDS = {0: 900, 1: 1800}  # anything else -> 3600, see Set97.getDemandSec()


class DemandFileError(Exception):
    pass


def demand_period_seconds(set97):
    return DEMAND_PERIOD_SECONDS.get(set97["demand"], 3600)


def decode_demand_file(raw):
    if len(raw) < HEADER_SIG_LEN:
        raise DemandFileError("File too short to contain a header")
    sig = raw[:HEADER_SIG_LEN].decode("ascii", errors="replace")
    if not sig.startswith("This is HIOKI 3197 DEMAND DATA FILE"):
        raise DemandFileError(f"Unexpected file signature: {sig!r}")

    off = HEADER_SIG_LEN + HEADER_PAD_LEN
    wave_num, = struct.unpack_from(">i", raw, off)
    off += 4
    start_time, off = _read_eptime(raw, off)

    dem_ave = list(struct.unpack_from(f">{len(ITEM_NAMES)}f", raw, off))
    off += 4 * len(ITEM_NAMES)

    dem_data = [[0.0] * wave_num for _ in ITEM_NAMES]
    for sample in range(wave_num):
        for item in range(len(ITEM_NAMES)):
            (v,) = struct.unpack_from(">f", raw, off)
            off += 4
            dem_data[item][sample] = v

    return {
        "start_time": start_time,
        "num_intervals": wave_num,
        "item_names": ITEM_NAMES,
        "average": dict(zip(ITEM_NAMES, dem_ave)),
        "intervals": dem_data,  # [item][sample], Watts/var
    }
