"""
Decoder for HIOKI 3197 `.inr` inrush-current files (downloaded via
`:BDATA:EVENT:INRUSH?`).

Reverse-engineered from `view3197/EventInrush.class` (decompiled with
`javap`). EventInrush subclasses EventWdu and only overrides the
signature/padding lengths and the channel labels, so the body is the
same as a `.wdu` file -- but the three channels are currents (I1, I2,
I3) instead of voltages. Values are already plain float32 amps:
EventWdu's CSV export passes them straight to `Set97.meas2a()` for
formatting with no scaling step.

Binary layout (big-endian, Java `DataInputStream` style):

    [35 bytes]  ASCII signature, "This is HIOKI 3197 INRUSH DATA FILE..."
    [93 bytes]  reserved
    int16       id
    int16       wave_num       -- number of samples
    int16       pre_num        -- pre-trigger sample count
    int16       evt_id         -- id of the parent .evt event this belongs to
    float32[wave_num][3]       -- waveform, sample-major, 3 channels (I1,I2,I3), amps
"""

import struct

HEADER_SIG_LEN = 35
HEADER_PAD_LEN = 93
INRUSH_CHANNEL_NAMES = ["I1", "I2", "I3"]


class InrushFileError(Exception):
    pass


def decode_inrush_file(raw):
    if len(raw) < HEADER_SIG_LEN:
        raise InrushFileError("File too short to contain a header")
    sig = raw[:HEADER_SIG_LEN].decode("ascii", errors="replace")
    if not sig.startswith("This is HIOKI 3197 INRUSH DATA FILE"):
        raise InrushFileError(f"Unexpected file signature: {sig!r}")

    off = HEADER_SIG_LEN + HEADER_PAD_LEN
    inr_id, wave_num, pre_num, evt_id = struct.unpack_from(">hhhh", raw, off)
    off += 8

    expected = off + wave_num * 3 * 4
    if len(raw) < expected:
        raise InrushFileError(
            f"File too short: expected >= {expected} bytes for wave_num={wave_num}, got {len(raw)}"
        )

    waveform = [[0.0] * wave_num for _ in range(3)]
    for sample in range(wave_num):
        for ch in range(3):
            (v,) = struct.unpack_from(">f", raw, off)
            off += 4
            waveform[ch][sample] = v

    return {
        "inrush_id": inr_id,
        "event_id": evt_id,  # links back to the .evt record this belongs to
        "num_samples": wave_num,
        "pre_trigger_samples": pre_num,
        "channel_names": INRUSH_CHANNEL_NAMES,
        "waveform_amps": waveform,  # already real amps, no scaling needed
    }
