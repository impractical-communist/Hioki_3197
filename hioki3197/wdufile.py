"""
Decoder for HIOKI 3197 `.wdu` ("Delta-V") voltage-event waveform files
(downloaded via `:BDATA:EVENT:VOLT?`).

Reverse-engineered from `view3197/EventWdu.class` (decompiled with
`javap`). Unlike the waveform embedded in `.evt` files, this one stores
already-scaled float32 values directly -- no gain lookup against
`3197set.set` needed.

Binary layout (big-endian, Java `DataInputStream` style):

    [36 bytes]  ASCII signature, "This is HIOKI 3197 DELTA-V DATA FILE..."
    [92 bytes]  reserved
    int16       id
    int16       wave_num       -- number of samples
    int16       pre_num        -- pre-trigger sample count
    int16       evt_id         -- id of the parent .evt event this belongs to
    float32[wave_num][3]       -- waveform, sample-major, 3 channels (U1,U2,U3), volts
"""

import struct

HEADER_SIG_LEN = 36
HEADER_PAD_LEN = 92
WDU_CHANNEL_NAMES = ["U1", "U2", "U3"]


class WduFileError(Exception):
    pass


def decode_wdu_file(raw):
    if len(raw) < HEADER_SIG_LEN:
        raise WduFileError("File too short to contain a header")
    sig = raw[:HEADER_SIG_LEN].decode("ascii", errors="replace")
    if not sig.startswith("This is HIOKI 3197 DELTA-V DATA FILE"):
        raise WduFileError(f"Unexpected file signature: {sig!r}")

    off = HEADER_SIG_LEN + HEADER_PAD_LEN
    wdu_id, wave_num, pre_num, evt_id = struct.unpack_from(">hhhh", raw, off)
    off += 8

    waveform = [[0.0] * wave_num for _ in range(3)]
    for sample in range(wave_num):
        for ch in range(3):
            (v,) = struct.unpack_from(">f", raw, off)
            off += 4
            waveform[ch][sample] = v

    return {
        "wdu_id": wdu_id,
        "event_id": evt_id,  # links back to the .evt record this belongs to
        "num_samples": wave_num,
        "pre_trigger_samples": pre_num,
        "channel_names": WDU_CHANNEL_NAMES,
        "waveform_volts": waveform,  # already real volts, no scaling needed
    }
