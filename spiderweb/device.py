"""Bidirectional link to the XIAO ESP32-S3 over USB serial.

The same serial port carries two independent, oppositely-framed streams:

* host -> device : Adalight LED frames (see `serial_link.build_frame`)
* device -> host : per-ring capacitance frames (see PROTOCOL.md)

`SerialDevice` runs a background reader thread that parses incoming sensor
frames into a ring-intensity vector. `SimDevice` is a no-hardware stand-in that
just holds whatever ring values you inject, so the whole server pipeline (lights
+ sound) runs for development without a board attached.
"""
from __future__ import annotations

import threading

import numpy as np

from spiderweb.serial_link import build_frame

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None

SENSOR_MAGIC = (0x53, 0x6E)  # 'S' 'n'


def available_ports() -> list[str]:
    if list_ports is None:
        return []
    return [p.device for p in list_ports.comports()]


class BaseDevice:
    """Common interface: push an LED frame, read the latest ring vector."""

    num_rings = 0

    def send_frame(self, rgb: np.ndarray) -> None:
        raise NotImplementedError

    def latest_rings(self) -> np.ndarray | None:
        """Most recent per-ring capacitance vector (raw units), or None."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def connected(self) -> bool:
        return False


class SimDevice(BaseDevice):
    """No hardware: holds an injected ring vector (driven by the UI / API)."""

    def __init__(self, num_rings: int):
        self.num_rings = num_rings
        self._rings = np.zeros(num_rings, dtype=float)

    def send_frame(self, rgb: np.ndarray) -> None:  # nothing to send to
        pass

    def inject(self, rings: np.ndarray) -> None:
        r = np.asarray(rings, dtype=float)
        if r.size == self.num_rings:
            self._rings = r

    def latest_rings(self) -> np.ndarray | None:
        return self._rings.copy()

    @property
    def connected(self) -> bool:
        return False


class SerialDevice(BaseDevice):
    """Real ESP32-S3 over USB serial, full-duplex."""

    def __init__(self, port: str, baud: int = 921600, num_rings: int = 0):
        if serial is None:
            raise RuntimeError("pyserial is not installed. Run: pip install pyserial")
        self.num_rings = num_rings
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self._rings = np.zeros(num_rings, dtype=float) if num_rings else None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_rx = 0.0
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def send_frame(self, rgb: np.ndarray) -> None:
        try:
            self.ser.write(build_frame(rgb))
        except Exception:
            pass

    def latest_rings(self) -> np.ndarray | None:
        with self._lock:
            return None if self._rings is None else self._rings.copy()

    @property
    def connected(self) -> bool:
        import time
        return (time.time() - self._last_rx) < 1.0

    def close(self) -> None:
        self._stop.set()
        try:
            self.ser.close()
        except Exception:
            pass

    # -- reader thread: parse 'S' 'n' framed sensor packets ---------------
    def _read_loop(self) -> None:
        import time
        st = 0          # 0 wait S, 1 wait n, 2 read count, 3 read chk, 4 read data
        count = 0
        need = 0
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception:
                break
            if not chunk:
                continue
            for b in chunk:
                if st == 0:
                    st = 1 if b == SENSOR_MAGIC[0] else 0
                elif st == 1:
                    st = 2 if b == SENSOR_MAGIC[1] else (1 if b == SENSOR_MAGIC[0] else 0)
                elif st == 2:
                    count = b
                    st = 3
                elif st == 3:
                    if b == (count ^ 0x55) & 0xFF:
                        need = count * 2
                        buf = bytearray()
                        st = 4 if need > 0 else 0
                    else:
                        st = 0
                elif st == 4:
                    buf.append(b)
                    if len(buf) >= need:
                        vals = np.frombuffer(bytes(buf), dtype="<u2").astype(float)
                        with self._lock:
                            self._rings = vals
                            if self.num_rings == 0:
                                self.num_rings = vals.size
                        self._last_rx = time.time()
                        st = 0
