# Hioki_3197

An unofficial USB client for the **HIOKI 3197 Power Quality Analyzer**,
for **Linux and Windows**.

HIOKI only ships Windows tooling for this instrument (a native DLL + kernel
driver, "3197Communicator"/"3197DataViewer"). This project talks to the
device directly over USB with plain Python -- on Linux with no vendor
driver at all (just a short udev rule for USB permissions), and on Windows
through Microsoft's generic WinUSB driver.

**Just want your data out? Jump to [Easiest way](#easiest-way-the-all-in-one-tool)**
(Windows users: [step-by-step guide](#windows-step-by-step)).

It can:
- Grab a **live screenshot** of the instrument's display (`:BCOPY?`), which
  shows whatever it's currently measuring.
- Query basic **live status** (mode, memory usage, alias, etc.).
- **Download stored measurement data** (interval logs, demand data, voltage
  dip/swell events, inrush events) off the instrument's internal memory, in
  a layout that opens directly in HIOKI's own `View3197.jar` viewer.
- **Decode every one of those formats natively in Python** into real
  volts/amps/watts and CSV -- no Java or GUI needed at all.

See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for how the protocol was
reverse-engineered (by decompiling HIOKI's own unobfuscated Java app) and a
full command reference.

> Not affiliated with or endorsed by HIOKI E.E. Corporation. "HIOKI" and
> "3197" are trademarks of their respective owner, used here only to
> identify compatible hardware. Provided for interoperability; use at your
> own risk, no warranty. This repo does **not** include any HIOKI software,
> drivers, or documentation -- only original, independently-written client
> code.

## Easiest way: the all-in-one tool

[`hioki3197_tool.py`](hioki3197_tool.py) is a single file that does
everything from a simple numbered menu -- no Python knowledge required.
It works on **Windows** and **Linux**.

```
What would you like to do?

  1. Check the connection
  2. Take a picture of the analyzer's screen
  3. Download everything from the analyzer (and convert it to spreadsheets)
  4. Convert a download you already have
  5. Make a chart (voltage, current, frequency...)
  6. Open HIOKI's own viewer program
  7. First-time setup (Python packages, USB driver / permission)
  8. Help
  0. Quit
```

It checks the connection (and explains how to fix it if needed), takes
screenshots, downloads and converts everything to spreadsheet-ready CSVs
with a plain-English `README.txt` summary, draws charts that open in any
web browser, and launches HIOKI's View3197 viewer. Downloading only
*reads* from the analyzer; nothing on it is changed or deleted. Data is
saved in a `hioki_data` folder next to the tool, one sub-folder per
download.

Full instructions, an explanation of every CSV column, and
troubleshooting are at the top of the file. The same actions are also
available as commands for scripting (`--help` lists them).

- **Linux:** `python3 hioki3197_tool.py` -- choose 7 the first time to
  set up USB permissions (needs `sudo apt install libusb-1.0-0 python3-usb`).
- **Windows:** see below.

### Windows, step by step

> **Status:** Windows support is new and has not yet been confirmed on a
> real Windows machine (it's developed and tested against a real 3197 on
> Linux). If something doesn't work, please
> [open an issue](https://github.com/impractical-communist/Hioki_3197/issues)
> with the message the tool shows.

**1. Install Python** (once). Download it from
<https://www.python.org/downloads/> and run the installer. On its first
screen, **tick "Add python.exe to PATH"**, then click "Install Now".

**2. Get the tool.** On this GitHub page, click the green **Code** button
-> **Download ZIP**, then right-click the downloaded ZIP -> **Extract
All**. (Or download just
[`hioki3197_tool.py`](https://github.com/impractical-communist/Hioki_3197/raw/main/hioki3197_tool.py):
right-click the link -> "Save link as"; make sure it's saved ending in
`.py`, not `.txt`.) Put it wherever you'd like your data to be saved.

**3. Run first-time setup** (once). Plug in and switch on the analyzer.
Double-click `hioki3197_tool.py`, type `7` and press Enter. It:

- offers to install the two small Python packages it needs
  ([`pyusb`](https://pypi.org/project/pyusb/) and
  [`libusb-package`](https://pypi.org/project/libusb-package/)) -- type
  `y`;
- then checks whether Windows can talk to the analyzer, and if not, shows
  the driver steps below and offers to open Zadig's web page.

**4. Switch the analyzer to the WinUSB driver** (once). Windows can't
talk to the analyzer through HIOKI's own driver, so this tool uses
Microsoft's built-in generic driver, WinUSB, installed with the free
program [Zadig](https://zadig.akeo.ie):

1. Download Zadig from <https://zadig.akeo.ie> and run it (click **Yes**
   if Windows asks for permission).
2. In Zadig's menu choose **Options > List All Devices**.
3. In the drop-down list pick the analyzer: the entry whose **USB ID**
   boxes show **108F** and **3197** (it may be called "3197",
   "HIOKI 3197" or "Unknown Device"). *Check the USB ID carefully -- picking
   the wrong device (e.g. your keyboard) would switch its driver instead.*
4. Make sure the box to the right of the green arrow says **WinUSB**, then
   click **Replace Driver** (or **Install Driver**).
5. When it finishes, unplug and replug the analyzer's USB cable.

**5. Use it.** Double-click `hioki3197_tool.py` whenever you need it.
Choose `1` to check the connection, then `3` to download everything.
When it's done it offers to open the folder: start with `README.txt` (a
summary) and `97intvl.csv` (the main data, opens in Excel).

**Going back to HIOKI's own software.** After step 4, HIOKI's
*3197 Communicator* can't reach the analyzer, because it needs HIOKI's
driver. To switch back: open **Device Manager**, right-click the analyzer
-> **Update driver** -> **Browse my computer for drivers** -> **Let me pick
from a list** -> choose HIOKI's driver. (Or right-click -> **Uninstall
device**, tick "delete the driver software", and replug -- then reinstall
HIOKI's driver from its CD/download.) HIOKI's *View3197* viewer is
unaffected either way, since it only reads files; this tool's option 6
opens it if Java is installed (<https://adoptium.net>).

**Windows troubleshooting**

| What you see | What to do |
|---|---|
| The window flashes and closes immediately | Python isn't installed, or wasn't added to PATH. Reinstall it (step 1), or open a Command Prompt in the tool's folder and run `py hioki3197_tool.py` to see the message. |
| Double-clicking opens the file in Notepad | Right-click the file -> **Open with** -> **Python**. |
| "pyusb is not installed" | Run option 7 again, or in a Command Prompt: `py -m pip install pyusb libusb-package` |
| "driver isn't set up for this tool yet" | Do step 4 (Zadig), then unplug and replug the cable. |
| "No HIOKI 3197 found" | Check the cable and that the analyzer is on; try another USB port. |
| "The analyzer is busy" | Close HIOKI's 3197 Communicator or anything else using it, then replug. |
| Screenshots are `.bmp` not `.png` | Optional: `py -m pip install pillow` |

## The Python library and example scripts (Linux)

Everything from here on is for developers using the `hioki3197` Python
package and the scripts in `examples/`. These are Linux-only for now
(the all-in-one tool above is what supports Windows).

### Requirements

- Linux, with the HIOKI 3197 connected via USB
- Python 3.8+
- [`pyusb`](https://github.com/pyusb/pyusb) (needs `libusb-1.0` installed
  at the OS level -- on Debian/Ubuntu: `sudo apt install libusb-1.0-0`)
- [`Pillow`](https://pillow.readthedocs.io/), optional, for converting
  captured screenshots to PNG

### Setup

```bash
# 1. One-time: let your user account access the device without sudo
./setup_usb_permissions.sh
# then unplug and replug the 3197's USB cable

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[image]"
```

### Usage

```bash
# Live screenshot of the instrument's display right now
python3 examples/capture_screen.py screen.png

# Poll the live screen every few seconds into one file (crude live dashboard)
python3 examples/live_poll.py live.bmp 3

# Download everything stored in the instrument's memory
python3 examples/download_stored_data.py downloaded_data/session_01

# Decode a downloaded event file into real volts/amps (no Java/GUI needed)
python3 examples/decode_event.py downloaded_data/session_01 97evt000.evt

# Summarize every event file in a folder into one spreadsheet-friendly CSV
python3 examples/events_to_csv.py downloaded_data/session_01

# Decode a .wdu voltage-event waveform (per-cycle RMS trend) into a CSV
python3 examples/decode_wdu.py downloaded_data/session_01/97dv000.wdu

# Decode demand data (periodic Pdem/Qdem readings) into a CSV
python3 examples/decode_demand.py downloaded_data/session_01

# Decode the interval/trend log (the big one) into a CSV
python3 examples/decode_interval.py downloaded_data/session_01/97intvl.itv

# Decode inrush-current trends (one CSV per 97inr###.inr) into CSVs
python3 examples/decode_inrush.py downloaded_data/session_01
```

Or use it as a library:

```python
from hioki3197 import Hioki3197

with Hioki3197() as dev:
    print(dev.status())
    dev.capture_screen("screen.bmp")
    dev.download_stored_data("downloaded_data/session_01")
```

### Example

Two live captures, pulled straight off the USB connection with
`examples/capture_screen.py`.

With nothing connected, voltages/currents correctly read 0.0 (confirms the
capture pipeline end-to-end, including the live clock matching real time):

![example capture, idle](examples/live_screen_capture.png)

With a real 240V source clamped onto U1, the instrument reads it correctly
(U1 = 238.9V; U2/U3 show induced crosstalk from the energized phase since
those leads are left floating, and I1-I4 are 0.0A since no current clamps
were attached -- all expected):

![example capture, 240V on U1](examples/live_screen_capture_240v.png)

## How this works, briefly

The 3197 enumerates as a USB "Test & Measurement"-class device
(`108f:3197`), but with a vendor-specific protocol byte that stops Linux's
generic `usbtmc` driver from binding it. Despite that, the actual wire
protocol is simple: plain ASCII commands terminated with `\r\n` over bulk
endpoints, with binary replies (screenshots, stored data) wrapped in an
8-digit length header. Full details and the complete command set are in
[`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Known limitations / ideas for contribution

- Every stored-data format HIOKI's own app downloads now decodes natively
  in Python -- `.evt` events, `3197set.set`, `.wdu` voltage-event
  waveforms, `97demand.dem` demand data, `97intvl.itv` interval logs and
  `.inr` inrush trends (`hioki3197/eventfile.py`, `setfile.py`,
  `wdufile.py`, `demandfile.py`, `intervalfile.py`, `inrushfile.py`) --
  all in real volts/amps/watts, no Java or GUI
  needed anywhere.
- The `status` bitfield in each `97intvl.itv` record is captured but not
  decoded (see `docs/PROTOCOL.md`'s "Known gaps").
- The interrupt endpoint (async notifications?) is not used/characterized.
- Windows support (all-in-one tool only) is not yet confirmed on real
  Windows hardware; the `hioki3197` package and `examples/` are Linux-only.
- Inrush (`.inr`) decoding is checked against files built to the
  disassembled layout, not yet a real inrush capture.
- Only tested against a single 3197 unit; other HIOKI instruments in the
  same family may use a similar/identical protocol but haven't been tried.

## License

MIT (for the code in this repository -- see [`LICENSE`](LICENSE)). This
does not grant any rights to HIOKI's own software, trademarks, or
documentation, none of which is included here.
