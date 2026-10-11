"""
Decoder for HIOKI 3197 `.evt` event-record files (downloaded via
`:BDATA:EVENT:LIST?`).

Reverse-engineered from `view3197/EventData.class`, `EventDetail.class`,
and `Eptime.class` inside HIOKI's own `View3197.jar` / `app3197.jar`
(decompiled with `javap` -- plain, unobfuscated bytecode). See
docs/PROTOCOL.md for the USB side of things.

File layout (all multi-byte fields big-endian, Java `DataInputStream`
style):

    [34 bytes]  ASCII signature, "This is HIOKI 3197 EVENT DATA FILE..."
    [94 bytes]  header padding/reserved (serial number, model, etc.)
    int16       id
    [2 bytes]   reserved
    int16       detail_num          -- number of event records that follow
    [1 byte]    reserved
    int8        wdu_id

    -- `detail_num` event records (fixed slot capacity 231, rest padding):
    for i in range(detail_num):
        Eptime  time                -- 8 bytes, see below
        int8    item                -- index into EVENT_NAMES
        int8    out_id              -- sequence number among "out" events
        int16   packed              -- pos(12 bits) | inout(2 bits) | ch(2 bits)
    for i in range(231 - detail_num):
        [12 bytes] padding (unused slots)

    -- for each of the `detail_num` records, in order, the ones that are
    -- "out" events (inout == OUT) additionally carry a duration+magnitude:
    for i in range(detail_num):
        if detail[i].is_out_event:
            int32  term             -- packed duration, see decode_term_ms()
            float32 value           -- magnitude in volts (confirmed via
                                        EventDetail.getValueText(), which
                                        formats it directly against the
                                        voltage range -- no extra scaling)
    for i in range(19 - detail_num):
        [8 bytes] padding (unused slots)

    Eptime   wtime                  -- waveform capture start time
    int16    wave_num                -- number of waveform samples
    int16    preNum                  -- pre-trigger sample count
    int16[wave_num][7]                -- waveform, sample-major, 7 channels
                                         per sample (U1, U2, U3, I1, I2, I3, I4)
"""

import struct

HEADER_SIG_LEN = 34
HEADER_PAD_LEN = 94
DETAIL_SLOTS = 231          # fixed on-disk capacity for event records
OUT_EVENT_SLOTS = 19        # fixed on-disk capacity for duration+value records
WAVE_CHANNELS = 7           # U1, U2, U3, I1, I2, I3, I4
WAVE_CHANNEL_NAMES = ["U1", "U2", "U3", "I1", "I2", "I3", "I4"]

EVENT_NAMES = [
    "EVENT", "TRANSIENT", "SWELL", "DIP", "INTERRUPT", "INRUSH",
    "START", "STOP", "TIMER", "MANUAL", "", "", "", "", "", "",
]
CH_NAMES = ["CH1", "CH2", "CH3", ""]
INOUT_NAMES = ["", "IN", "OUT", "---"]

# day-of-year cumulative totals HIOKI's firmware uses for its (slightly
# non-standard, but internally consistent) calendar decomposition
_DAY_MONTH = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365]
_DAY_MONTH_LEAP = [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366]


class EventFileError(Exception):
    pass


def _eptime_to_dict(sec, sec_64):
    """Replicate Eptime.secToDay(): seconds-since-1980 -> calendar fields."""
    day = sec // 86400
    year = day * 100 // 36525
    day -= year * 365 + (year + 3) * 25 // 100
    year += 1980
    table = _DAY_MONTH_LEAP if year % 4 == 0 else _DAY_MONTH
    month = 1
    while month <= 12 and day >= table[month]:
        month += 1
    day = day - table[month - 1] + 1

    day_sec = sec % 86400
    hour = day_sec // 3600
    day_sec -= hour * 3600
    minute = day_sec // 60
    day_sec -= minute * 60

    return {
        "year": year,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": minute,
        "second": day_sec,
        "sec_64": sec_64,
    }


def _read_eptime(buf, off):
    sec, sec_64 = struct.unpack_from(">ih", buf, off)
    buf_off = off + 8  # int32 + int16 + 2 reserved bytes
    t = _eptime_to_dict(sec, sec_64)
    t["iso"] = (
        f"{t['year']:04d}-{t['month']:02d}-{t['day']:02d} "
        f"{t['hour']:02d}:{t['minute']:02d}:{t['second']:02d}.{t['sec_64']:03d}"
    )
    return t, buf_off


def decode_term_ms(term):
    """Replicate EventDetail.getTermText()'s unpacking: duration in ms."""
    base = term & 0x3FFFFFFF
    if term & 0x40000000:
        base *= 10
    return base


def decode_event_file(raw):
    """Parse a `.evt` file's raw bytes into a structured dict."""
    if len(raw) < HEADER_SIG_LEN:
        raise EventFileError("File too short to contain a header")

    sig = raw[:HEADER_SIG_LEN].decode("ascii", errors="replace")
    if not sig.startswith("This is HIOKI 3197 EVENT DATA FILE"):
        raise EventFileError(f"Unexpected file signature: {sig!r}")

    off = HEADER_SIG_LEN + HEADER_PAD_LEN
    event_id, = struct.unpack_from(">h", raw, off)
    off += 2 + 2  # id, then 2 reserved bytes
    detail_num, = struct.unpack_from(">h", raw, off)
    off += 2
    off += 1  # reserved
    wdu_id, = struct.unpack_from(">b", raw, off)
    off += 1

    details = []
    for _ in range(detail_num):
        t, off = _read_eptime(raw, off)
        item, out_id = struct.unpack_from(">bb", raw, off)
        off += 2
        packed, = struct.unpack_from(">h", raw, off)
        off += 2

        pos = packed & 0x0FFF
        inout = (packed & 0x3000) >> 12
        ch = packed >> 14
        if ch < 0:
            ch = 2

        is_out_event = inout == 2  # EventDetail.isOutEvent(): isInOut() && inout==OUT
        is_in_out_item = item in (1, 2, 3, 4)  # TRANSIENT/SWELL/DIP/INTERRUPT
        is_out_event = is_out_event and is_in_out_item

        details.append({
            "time": t,
            "event_code": item,
            "event_name": EVENT_NAMES[item] if 0 <= item < len(EVENT_NAMES) else f"?{item}",
            "out_id": out_id,
            "pos": pos,
            "channel": CH_NAMES[ch] if is_in_out_item and inout != 0 else "ALL",
            "inout": INOUT_NAMES[inout],
            "is_out_event": is_out_event,
            "term_ms": None,
            "value_volts": None,
        })

    off += 12 * (DETAIL_SLOTS - detail_num)  # skip unused detail slots

    n_out_events = 0
    for d in details:
        if d["is_out_event"]:
            term, value = struct.unpack_from(">if", raw, off)
            off += 8
            d["term_ms"] = decode_term_ms(term)
            d["value_volts"] = value
            n_out_events += 1

    # Padding here is sized against the actual out-event count (not
    # detail_num) -- real + padding always sums to the fixed OUT_EVENT_SLOTS
    # capacity, matching the firmware's `19 - <out-event counter>` loop bound.
    off += 8 * (OUT_EVENT_SLOTS - n_out_events)

    wtime, off = _read_eptime(raw, off)
    wave_num, pre_num = struct.unpack_from(">hh", raw, off)
    off += 4

    waveform = [[0] * wave_num for _ in range(WAVE_CHANNELS)]
    for sample in range(wave_num):
        for ch in range(WAVE_CHANNELS):
            (v,) = struct.unpack_from(">h", raw, off)
            off += 2
            waveform[ch][sample] = v

    return {
        "event_id": event_id,
        "wdu_id": wdu_id,
        "details": details,
        "waveform_time": wtime,
        "waveform_num_samples": wave_num,
        "waveform_pre_trigger_samples": pre_num,
        "waveform_channel_names": WAVE_CHANNEL_NAMES,
        "waveform_raw": waveform,  # raw int16 ADC counts; see docs/PROTOCOL.md
    }


def summarize(parsed):
    """Return a short human-readable multi-line summary of a decoded event file."""
    lines = [f"Event #{parsed['event_id']}, {len(parsed['details'])} record(s):"]
    for d in parsed["details"]:
        bits = [f"  {d['time']['iso']}  {d['event_name']:<10s} {d['channel']:<4s} {d['inout']}"]
        if d["is_out_event"]:
            bits.append(f"-> {d['value_volts']:.1f} V for {d['term_ms']} ms")
        lines.append(" ".join(bits))
    lines.append(
        f"Waveform: {parsed['waveform_num_samples']} samples x "
        f"{len(parsed['waveform_channel_names'])} channels "
        f"({', '.join(parsed['waveform_channel_names'])}), "
        f"captured at {parsed['waveform_time']['iso']} "
        f"(raw ADC counts; see docs/PROTOCOL.md for scaling caveats)"
    )
    return "\n".join(lines)
