"""Stream RGB frames to the ESP using an Adalight-style framed protocol.

Frame on the wire:
    'A' 'd' 'a'  count_hi  count_lo  checksum   <R G B per LED ...>
where count = num_leds - 1 and checksum = count_hi ^ count_lo ^ 0x55.

The fixed-length header lets the ESP resynchronise without the RGB payload
ever being mistaken for a header.
"""
from __future__ import annotations

import numpy as np

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None


def available_ports() -> list[str]:
    if list_ports is None:
        return []
    return [p.device for p in list_ports.comports()]


def build_frame(rgb: np.ndarray) -> bytes:
    rgb = np.asarray(rgb, dtype=np.uint8)
    count = max(len(rgb) - 1, 0)
    hi, lo = (count >> 8) & 0xFF, count & 0xFF
    header = bytes([ord("A"), ord("d"), ord("a"), hi, lo, hi ^ lo ^ 0x55])
    return header + rgb.tobytes()


class SerialLink:
    def __init__(self, port: str, baud: int = 921600):
        if serial is None:
            raise RuntimeError("pyserial is not installed. Run: pip install pyserial")
        self.ser = serial.Serial(port, baud, timeout=1)

    def send(self, rgb: np.ndarray) -> None:
        self.ser.write(build_frame(rgb))

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
