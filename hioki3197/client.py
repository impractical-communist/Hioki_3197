"""
Unofficial Linux USB client for the HIOKI 3197 Power Quality Analyzer.

HIOKI ships Windows-only tooling for this instrument (a native DLL +
kernel driver). This module talks to the device directly over USB using
plain ASCII commands, reverse-engineered by decompiling HIOKI's own
"3197Communicator" Java application (app3197.jar, which ships with the
instrument and contains no obfuscation) and confirming the wire format
against the real hardware. See docs/PROTOCOL.md for the full write-up.

Not affiliated with or endorsed by HIOKI E.E. Corporation. Use at your
own risk; no warranty. "HIOKI" and "3197" are trademarks of their
respective owner, used here only to identify compatible hardware.
"""

import time
import usb.core
import usb.util

VENDOR_ID = 0x108F
PRODUCT_ID = 0x3197

_READ_CHUNK = 64  # matches the device's bulk endpoint wMaxPacketSize


class Hioki3197Error(Exception):
    pass


def find_device():
    """Return the pyusb Device for a connected HIOKI 3197, or None."""
    return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)


class Hioki3197:
    """
    A session talking to one HIOKI 3197 over USB.

    Usage:
        with Hioki3197() as dev:
            print(dev.query(":MODE?"))
            dev.capture_screen("screen.bmp")
    """

    def __init__(self, device=None):
        self.dev = device or find_device()
        if self.dev is None:
            raise Hioki3197Error(
                "No HIOKI 3197 found on USB. Is it plugged in? "
                "Have you run setup_usb_permissions.sh and replugged it?"
            )
        self.dev.set_configuration()
        cfg = self.dev.get_active_configuration()
        intf = cfg[(0, 0)]
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT,
        )
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN
            and e.bmAttributes & 0x3 == 0x2,  # bulk, not the interrupt endpoint
        )
        if self.ep_out is None or self.ep_in is None:
            raise Hioki3197Error("Unexpected USB descriptor layout; endpoints not found")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        usb.util.dispose_resources(self.dev)

    # -- low-level transport -------------------------------------------------

    def _flush(self):
        """Drain any stale bytes left over from a prior, incompletely-read response.

        The device is a simple FIFO with no request/response tagging: if a
        previous query's reply wasn't fully read, it will bleed into the next
        one. Always flush before writing a new command.
        """
        while True:
            try:
                self.ep_in.read(_READ_CHUNK, timeout=150)
            except usb.core.USBError:
                break

    def _write(self, command, timeout=5000):
        self.ep_out.write((command + "\r\n").encode("ascii"), timeout=timeout)

    def query(self, command, timeout=5000):
        """Send a text command and return the trimmed ASCII reply (e.g. ":MODE?" -> "4")."""
        self._flush()
        self._write(command, timeout=timeout)
        data = bytearray()
        try:
            data += self.ep_in.read(_READ_CHUNK, timeout=timeout)
        except usb.core.USBError:
            return ""
        # Keep reading while packets arrive exactly full-sized (more data follows);
        # a short packet signals the reply is complete.
        while len(data) and len(data) % _READ_CHUNK == 0:
            try:
                chunk = self.ep_in.read(_READ_CHUNK, timeout=800)
            except usb.core.USBError:
                break
            if not chunk:
                break
            data += chunk
        return data.decode("ascii", errors="replace").strip()

    def bquery(self, command, timeout=15000):
        """
        Send a command whose reply is a binary block and return the raw bytes.

        Binary replies are framed as an 8-ASCII-digit length, one delimiter
        byte, then that many bytes of payload (used by all `:BDATA:...?`
        and `:BCOPY?` / `:COPY:DATA?` commands).
        """
        self._flush()
        self._write(command, timeout=timeout)
        data = bytearray()
        first = bytes(self.ep_in.read(_READ_CHUNK, timeout=timeout))
        data += first
        if len(first) < 9 or not first[:8].isdigit():
            raise Hioki3197Error(f"Expected a binary block header, got: {bytes(data)!r}")
        total = int(first[:8].decode("ascii"))
        needed = total + 9
        while len(data) < needed:
            chunk = bytes(
                self.ep_in.read(min(4096, needed - len(data) + _READ_CHUNK), timeout=timeout)
            )
            if not chunk:
                break
            data += chunk
        return bytes(data[9 : 9 + total])

    # -- convenience wrappers --------------------------------------------------

    def status(self):
        """Return a dict of basic live device status."""
        return {
            "mode": self.query(":MODE?"),
            "copy_stat": self.query(":COPY:STAT?"),
            "alias": self.query(":DISP:NAME?"),
            "memory": self.query(":BDATA:MEMORY? 0"),
        }

    def capture_screen(self, path=None):
        """
        Grab a live screenshot of the instrument's display right now.

        Returns the raw BMP bytes (RLE8-compressed, 256-color, 240x320),
        and writes them to `path` if given.
        """
        bmp = self.bquery(":BCOPY?")
        if path:
            with open(path, "wb") as f:
                f.write(bmp)
        return bmp

    def event_counts(self, mem_no=0):
        """Return (event_count, voltage_event_count, inrush_event_count) for a memory bank."""
        reply = self.query(f":BDATA:EVENT:NUM? {mem_no}")
        parts = [p.strip() for p in reply.split(",")]
        if len(parts) != 3:
            raise Hioki3197Error(f"Unexpected :BDATA:EVENT:NUM? reply: {reply!r}")
        return tuple(int(p) for p in parts)

    def download_stored_data(self, dest_dir, mem_no=0, progress=None):
        """
        Download the full stored-data set for one memory bank into `dest_dir`,
        using the same filenames HIOKI's own 3197Communicator produces
        (3197set.set, 97intvl.itv, 97demand.dem, 97evmoni.mon, 97evt###.evt,
        97dv###.wdu, 97inrush.inr), so the result can be opened directly in
        HIOKI's View3197.jar.

        `progress`, if given, is called with a short status string after each file.
        """
        import os

        os.makedirs(dest_dir, exist_ok=True)
        ev_max, wdu_max, inr_max = self.event_counts(mem_no)

        def save(name, payload):
            path = os.path.join(dest_dir, name)
            with open(path, "wb") as f:
                f.write(payload)
            if progress:
                progress(f"{name}: {len(payload)} bytes")
            return path

        save("3197set.set", self.bquery(f":BDATA:SYSTEM? {mem_no}"))
        save("97intvl.itv", self.bquery(f":BDATA:TIMEPLOT? {mem_no}"))
        save("97demand.dem", self.bquery(f":BDATA:DEMAND? {mem_no}"))
        save("97evmoni.mon", self.bquery(f":BDATA:EVENT:MONI? {mem_no}"))

        for i in range(ev_max):
            save(f"97evt{i:03d}.evt", self.bquery(f":BDATA:EVENT:LIST? {mem_no},{i}"))
        for i in range(wdu_max):
            save(f"97dv{i:03d}.wdu", self.bquery(f":BDATA:EVENT:VOLT? {mem_no},{i}"))
        for i in range(inr_max):
            # The original app reuses this one filename for every inrush
            # event too (each download overwrites the last) -- kept as-is
            # for compatibility with View3197.jar's expectations.
            save("97inrush.inr", self.bquery(f":BDATA:EVENT:INRUSH? {mem_no},{i}"))

        return {"event_count": ev_max, "voltage_event_count": wdu_max, "inrush_event_count": inr_max}
