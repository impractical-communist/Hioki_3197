# HIOKI 3197 USB protocol notes

Reverse-engineered by decompiling HIOKI's own `app3197.jar` ("3197Communicator",
part of the official "3197Applications" bundle) -- it's plain, unobfuscated
Java bytecode, so the command strings and framing logic were readable directly
from the `.class` files with `javap`. The low-level transport was then
confirmed empirically against real hardware with `pyusb`.

Not affiliated with or endorsed by HIOKI. Shared for interoperability.

## USB identity

- VID:PID `108f:3197`
- USB interface: class `0xFE` (Application Specific), subclass `0x03`
  (Test & Measurement) -- the same class/subclass used by the standard
  USBTMC spec, but with a vendor-specific protocol byte (`0xFF`) instead of
  USBTMC's `0x00`/`0x01`. That's enough to stop Linux's `usbtmc` kernel
  driver from auto-binding it, but the actual wire protocol turns out to be
  much simpler than USBTMC anyway (see below) -- no kernel driver is needed.
- Endpoints on interface 0:
  - `0x01` bulk OUT (commands)
  - `0x82` bulk IN (replies)
  - `0x83` interrupt IN, 8 bytes, unused by this client (not characterized)
- `bMaxPacketSize0` on bulk endpoints is 64 bytes.

## Transport

No envelope, no framing beyond a line terminator. Write ASCII text to the
bulk OUT endpoint, terminated with `\r\n`; read the ASCII (or binary, see
below) reply from the bulk IN endpoint.

```
:MODE?\r\n   -->  "4"
```

The device is a simple FIFO with no request/response tagging. If a
previous reply isn't fully drained before the next command is sent, the
leftover bytes bleed into the next read and corrupt alignment. Always:

1. Flush (non-blocking-ish read with a short timeout, discard anything
   that comes back) before writing a new command.
2. Fully read the current reply (to a short/final packet, or to its
   declared length for binary blocks) before moving on.

`hioki3197/client.py` implements both.

## Reply framing

Two kinds of replies:

- **Plain text** -- just the ASCII answer, e.g. `:MODE?` -> `"4"`,
  `:DISP:NAME?` -> `"0"` (no alias set) or `"1,<name>"`.
- **Binary block** -- an 8-ASCII-digit decimal length, one delimiter byte,
  then that many bytes of payload:

  ```
  "00000316" + <1 byte> + <316 bytes of payload>
  ```

  Used by `:BCOPY?`, `:COPY:DATA?`, and all `:BDATA:...?` commands.

## Command reference

Recovered from `app3197/Dev3197.class`, `app3197/Main*.class`, and
`app3197/Info97.class`. `n` below is a memory bank number (`0` on a
single, non-partitioned unit).

| Command | Reply | Meaning |
|---|---|---|
| `:MODE?` | text | Current operating mode (small integer) |
| `:KEY <n>` | - | Remotely "press" a front-panel key |
| `:DISP:NAME?` | text | Query the unit's display alias |
| `:DISP:NAME <name>` | text | Set the unit's display alias (<=12 chars) |
| `:DRESET A` | - | Remote reset |
| `:BCOPY?` | binary (BMP) | Live screenshot of the display right now |
| `:COPY:DATA? <n>` | binary (BMP) | Download stored screen-copy image #n |
| `:COPY:STAT?` | text | Stored screen-copy stats |
| `:COPY:CLE` | - | Clear stored screen copies |
| `:BDATA:MEMORY? <n>` | text | Memory bank usage info |
| `:BDATA:SYSTEM? <n>` | binary | Device setup/config block -> `3197set.set` |
| `:BDATA:TIMEPLOT? <n>` | binary | Stored interval/time-plot log -> `97intvl.itv` |
| `:BDATA:DEMAND? <n>` | binary | Stored demand data -> `97demand.dem` |
| `:BDATA:EVENT:NUM? <n>` | text | `"ev_count, voltage_event_count, inrush_count"` |
| `:BDATA:EVENT:MONI? <n>` | binary | Event monitor data -> `97evmoni.mon` |
| `:BDATA:EVENT:LIST? <n>,<i>` | binary | Event record #i -> `97evt<i:03d>.evt` |
| `:BDATA:EVENT:VOLT? <n>,<i>` | binary | Voltage-event waveform #i -> `97dv<i:03d>.wdu` |
| `:BDATA:EVENT:INRUSH? <n>,<i>` | binary | Inrush-event record #i -> `97inrush.inr` |

`:BDATA:TIMEPLOT?` is the one command sent via the app's "big query" path
(a larger read buffer), but it uses the exact same 8-digit-length framing --
no special handling needed beyond allowing for a bigger payload (interval
logs were ~800KB in testing).

## `:BCOPY?` / `:COPY:DATA?` image format

The payload is a complete, standard Windows BMP file: signature `BM`,
8-bit indexed color (256-entry palette), RLE8-compressed pixel data,
240x320 resolution. Any standard BMP decoder (e.g. Pillow) opens it
directly -- no custom parsing needed:

```python
from PIL import Image
Image.open("screen.bmp").convert("RGB").save("screen.png")
```

## Downloading a full stored-data set

HIOKI's own app always downloads system, timeplot, demand, and event-monitor
data together, then loops over the three event counts from
`:BDATA:EVENT:NUM?` to pull individual event records. `Hioki3197.download_stored_data()`
replicates this sequence and uses the same filenames, so the result opens
directly in HIOKI's own `View3197.jar` viewer (File > Open > the folder).

## Decoding `.evt` event files and `3197set.set`

`hioki3197/eventfile.py` and `hioki3197/setfile.py` decode these binary
formats natively in Python -- no Java/GUI needed. Reverse-engineered the
same way as the USB protocol: decompiling `view3197/EventData.class`,
`EventDetail.class`, `Eptime.class`, `Set97.class`, and `VariableType.class`.
Each module's docstring has the full byte-level layout. In short:

- `.evt` files hold a list of event records (START/STOP/DIP/SWELL/
  INTERRUPT/TRANSIENT/...) each with a timestamp, and -- for dip/swell/
  interrupt events specifically -- a duration (ms) and magnitude that's
  already a plain float in volts (`EventDetail.value`, confirmed via
  `getValueText()`, which formats it directly against the voltage range
  with no extra scaling step). Each file also carries one captured
  waveform: 7 channels (U1, U2, U3, I1, I2, I3, I4) x N samples, stored as
  raw int16 ADC counts.
- `3197set.set` holds the device's range/calibration settings, including
  per-channel waveform gain factors (`u_adj`, `i_adj`). Multiplying a raw
  waveform sample by `u_max * u_adj[channel]` (voltage channels) or
  `i_max * i_adj[channel]` (current channels) converts it to volts/amps --
  this is what `Set97.getUWaveGain()`/`getIWaveGain()` compute internally.
  `setfile.waveform_gain()` does the same.
- `.wdu` files (`hioki3197/wdufile.py`, from `:BDATA:EVENT:VOLT?`) hold a
  per-cycle RMS *trend* around one voltage event -- 3 channels (U1-U3),
  typically ~300 samples spanning a pre-trigger window through the event.
  `evt_id` in the header links each `.wdu` file back to the `.evt` record
  it belongs to. Values are already plain float32 volts in the file --
  no gain lookup needed (reverse-engineered from `view3197/EventWdu.class`).
- `97demand.dem` (`hioki3197/demandfile.py`, from `:BDATA:DEMAND?`) holds
  periodic demand readings -- 4 channels (`Pdem+`, `Pdem-`, `Qdem_LAG`,
  `Qdem_LEAD`, real Watts/var, no scaling needed), one row per demand
  period (15/30/60 min, from `3197set.set`'s `demand` field via
  `demandfile.demand_period_seconds()`), starting at the file's recorded
  start time (reverse-engineered from `view3197/TpDemData.class`).
- `97intvl.itv` (`hioki3197/intervalfile.py`, from `:BDATA:TIMEPLOT?`) is
  the interval/trend log, and the biggest format -- one 436-byte record
  per logged interval, holding 32 measurement items (`Freq`, `U1-U3`,
  `I1-I4`, voltage/current peaks per channel, `Uave`/`Iave`,
  `Psum`/`Qsum`/`Ssum`/`PFsum`, `THD1-3`, `Uunb`) x 3 statistics
  (Max/Min/Ave over the interval), plus 6 per-channel voltage extremes
  (`Umax1-3`/`Umin1-3`) and the same 4 cumulative-energy fields as the
  interval log's energy columns (`WP+`/`WP-`/`WQ_LAG`/`WQ_LEAD`, Wh/varh).
  Reverse-engineered from `view3197/TpData.class` -- item names and the
  `WP+`/`WQ_LAG`-style energy labels came straight out of its
  `makeHeader()` CSV-header-writing logic. All values are already real
  engineering units, same pre-scaled-float convention as everything else.

All five formats are checked against real hardware. The decoded
`3197set.set` serial number matches the physical unit exactly. A captured
`.evt` waveform's U1 channel peaks at the expected magnitude (~sqrt(2) x
the live RMS voltage reading) once scaled. A real disconnect/reconnect
test produced a `.wdu` trend that smoothly falls from 239V to ~0V over
the interruption and a second one that climbs back from ~0V to 239V on
reconnect, both `evt_id`-linked to the matching `INTERRUPT` records in
the `.evt` files. `97demand.dem` parses cleanly at both zero and non-zero
interval counts, with correctly-spaced interval timestamps. And
`97intvl.itv`, decoded from that same test, shows U1 at ~239V across all
31 one-second intervals (matching the live screen and the `.evt` RMS
values), a `U1peak+` of ~326V (matching the independently-derived `.evt`
waveform peak almost exactly), and timestamps spanning exactly the
recording session's START-to-STOP window -- three-way cross-validation
across completely independent file formats, all agreeing. Byte-size math
also checks out exactly: three real `97intvl.itv` files of very different
sizes (3192, 13656, 794096 bytes) all divide out to an exact integer
`wave_num` under the 436-bytes-per-record layout, with zero bytes left
over.

`examples/decode_event.py`, `examples/decode_wdu.py`,
`examples/decode_demand.py`, and `examples/decode_interval.py` tie it all
together -- human-readable summaries plus CSV export for each format;
`examples/events_to_csv.py` flattens every `.evt` in a folder into one
spreadsheet-friendly summary.

With this, every file HIOKI's own app downloads decodes natively in
Python -- no Java, no GUI, no vendor driver needed anywhere in the chain.

## Known gaps

- The interrupt endpoint (`0x83`) was not characterized -- it may carry
  async notifications (e.g. button presses, mode changes) but wasn't
  needed for the read-only/download use cases this client covers.
- The `status` field in each `97intvl.itv` record (a bitfield) is
  captured but not decoded -- likely flags things like "interval
  incomplete" or "sensor overload", similar in spirit to `Set97`'s
  event-detection flags, but not reverse-engineered here.
- Waveform scaling (`u_adj`/`i_adj`) is validated by order-of-magnitude
  and polarity against a live reading, not against a byte-exact reference
  from HIOKI. Treat absolute precision with appropriate caution for
  anything safety- or compliance-critical.
