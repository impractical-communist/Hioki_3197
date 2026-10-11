"""
Decoder for HIOKI 3197 `3197set.set` device-setup files (downloaded via
`:BDATA:SYSTEM?`). Needed to interpret the scale of `.evt` waveform data
in real engineering units -- see eventfile.py.

Reverse-engineered from `view3197/Set97.class` and `VariableType.class`
inside HIOKI's own jars (decompiled with `javap`).

Binary layout (big-endian, Java `DataInputStream` style). Field names
match the Java source where known:

    [35 bytes]  ASCII signature, "This is HIOKI 3197 SYSTEM DATA FILE..."
    [13 bytes]  reserved
    [16 bytes]  alias (trimmed ASCII)
    [32 bytes]  reserved
    [22 bytes]  serial (trimmed ASCII)
    [10 bytes]  reserved
    int8        status
    [1 byte]    reserved
    int8        wiring
    [1 byte]    reserved
    VariableType uRef      -- 16 bytes: index(1) + label(8) + reserved(3) + value(f4)
    int8        freq_set
    int8        freq
    int8        clmp
    bool        isIRngLow
    VariableType pt        -- 16 bytes (potential-transformer ratio)
    VariableType ct        -- 16 bytes (current-transformer ratio)
    float32     uMax       -- voltage range full-scale, volts
    float32     iMax       -- current range full-scale, amps
    float32     pMax
    float32     sumMax
    [20 bytes]  reserved
    int32       interval
    int8        interval_set
    bool        memDiv
    [3 bytes]   reserved
    int8        demand
    [2 bytes]   reserved
    float32[3]  uAdj       -- per-channel voltage waveform gain adjustment (U1-U3)
    float32[3]  iAdj[0:3]  -- per-channel current waveform gain adjustment (I1-I3)
    [12 bytes]  reserved
    float32     iAdj[3]    -- 4th current channel adjustment (I4)
    ... (event thresholds etc, not needed here)
"""

import struct

HEADER_SIG_LEN = 35


class SetFileError(Exception):
    pass


def _read_str(raw, off, n):
    return raw[off : off + n].decode("ascii", errors="replace").strip("\x00").strip()


def decode_set_file(raw):
    if len(raw) < HEADER_SIG_LEN:
        raise SetFileError("File too short to contain a header")
    sig = raw[:HEADER_SIG_LEN].decode("ascii", errors="replace")
    if not sig.startswith("This is HIOKI 3197 SYSTEM DATA FILE"):
        raise SetFileError(f"Unexpected file signature: {sig!r}")

    off = HEADER_SIG_LEN + 13
    alias = _read_str(raw, off, 16)
    off += 16 + 32
    serial = _read_str(raw, off, 22)
    off += 22 + 10

    status, = struct.unpack_from(">b", raw, off); off += 1 + 1
    wiring, = struct.unpack_from(">b", raw, off); off += 1 + 1

    off += 16  # uRef VariableType (not decoded -- not needed for waveform scaling)

    freq_set, freq, clmp = struct.unpack_from(">bbb", raw, off); off += 3
    is_i_rng_low, = struct.unpack_from(">?", raw, off); off += 1

    off += 16  # pt VariableType
    off += 16  # ct VariableType

    u_max, i_max, p_max, sum_max = struct.unpack_from(">ffff", raw, off)
    off += 16

    off += 20
    interval, = struct.unpack_from(">i", raw, off); off += 4
    interval_set, = struct.unpack_from(">b", raw, off); off += 1
    mem_div, = struct.unpack_from(">?", raw, off); off += 1
    off += 3
    demand, = struct.unpack_from(">b", raw, off); off += 1
    off += 2

    u_adj = list(struct.unpack_from(">fff", raw, off)); off += 12
    i_adj = list(struct.unpack_from(">fff", raw, off)); off += 12
    off += 12
    i_adj_3, = struct.unpack_from(">f", raw, off); off += 4
    i_adj.append(i_adj_3)

    return {
        "alias": alias,
        "serial": serial,
        "wiring": wiring,
        "u_max": u_max,
        "i_max": i_max,
        "p_max": p_max,
        "sum_max": sum_max,
        "u_adj": u_adj,   # per-channel gain multipliers, U1-U3
        "i_adj": i_adj,   # per-channel gain multipliers, I1-I4
        "interval": interval,
        "demand": demand,
    }


def waveform_gain(set97, channel_index):
    """
    Return the (volts or amps) full-scale gain for one waveform channel
    (0-2 = U1-U3, 3-6 = I1-I4), matching Set97.getUWaveGain()/getIWaveGain().
    """
    if channel_index < 3:
        return set97["u_max"] * set97["u_adj"][channel_index]
    return set97["i_max"] * set97["i_adj"][channel_index - 3]
