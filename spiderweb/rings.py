"""Turn raw per-ring capacitance into usable control values.

The webbing only resolves *which ring* a hand is near and *how strongly* -- no
angular position. This processor takes the raw per-ring readings streamed by the
ESP and produces, per tick:

* `intensity[r]`  -- 0..1 within each ring's calibrated hover band
* `global`        -- strongest ring intensity (overall hover strength)
* `active_ring`   -- the ring with the strongest signal
* rising *touches* -- rings whose value just crossed their touch threshold
                      (Schmitt trigger, so noise near the line doesn't chatter)

Per-ring calibration (background / hover / touch) copes with each ring reading
on its own scale, exactly the "calibrate the rough ranges" idea from the sim.
"""
from __future__ import annotations

import numpy as np

CAL_PROMPTS = ("", "background: hands clear",
               "hover: hold a hand near", "touch: hold a touch")


class RingProcessor:
    def __init__(self, num_rings: int, smoothing: float = 0.25, hysteresis: float = 0.8):
        self.n = max(int(num_rings), 0)
        self.alpha = float(smoothing)
        self.hyst = float(hysteresis)
        self.value = np.zeros(self.n)                 # smoothed reading
        self.noise = np.zeros(self.n)                 # per-ring noise floor
        self.touch = np.full(self.n, 1.0)            # per-ring touch threshold
        self._above = np.zeros(self.n, dtype=bool)
        self.intensity = np.zeros(self.n)
        self.active_ring = 0
        self.global_intensity = 0.0
        self.cal_step = 0
        self._cal = {"bg": None, "hover": None, "touch": None}

    # -- configuration -----------------------------------------------------
    def set_noise(self, v) -> None:
        self.noise = np.full(self.n, float(v)) if np.isscalar(v) else np.asarray(v, float)

    def set_touch(self, v) -> None:
        self.touch = np.full(self.n, float(v)) if np.isscalar(v) else np.asarray(v, float)

    def calibrate_step(self) -> None:
        """Advance the background -> hover -> touch capture, then set bands."""
        step = self.cal_step
        cur = self.value.copy()
        if step == 0:
            self.cal_step = 1
        elif step == 1:
            self._cal["bg"] = cur
            self.cal_step = 2
        elif step == 2:
            self._cal["hover"] = cur
            self.cal_step = 3
        elif step == 3:
            self._cal["touch"] = cur
            bg = self._cal["bg"]
            hv = self._cal["hover"]
            tc = self._cal["touch"]
            if bg is not None and hv is not None:
                self.noise = (bg + hv) / 2.0
            if hv is not None and tc is not None:
                self.touch = np.maximum((hv + tc) / 2.0, self.noise + 1e-3)
            self.cal_step = 0

    # -- per-tick update ---------------------------------------------------
    def update(self, raw) -> np.ndarray:
        if self.n == 0:
            return np.empty(0, dtype=int)
        if raw is None:
            raw = self.value
        raw = np.asarray(raw, dtype=float)
        if raw.size != self.n:
            r = np.zeros(self.n)
            r[: min(raw.size, self.n)] = raw[: min(raw.size, self.n)]
            raw = r

        self.value += self.alpha * (raw - self.value)

        span = np.maximum(self.touch - self.noise, 1e-6)
        self.intensity = np.clip((self.value - self.noise) / span, 0.0, 1.0)

        hi = self.touch
        lo = self.noise + self.hyst * (self.touch - self.noise)
        above = self._above.copy()
        above[self.value >= hi] = True
        above[self.value < lo] = False
        rising = np.where(above & ~self._above)[0]
        self._above = above

        self.global_intensity = float(self.intensity.max())
        self.active_ring = int(np.argmax(self.intensity))
        return rising

    def state(self) -> dict:
        return {
            "value": self.value.round(2).tolist(),
            "intensity": self.intensity.round(3).tolist(),
            "noise": self.noise.round(2).tolist(),
            "touch": self.touch.round(2).tolist(),
            "active_ring": self.active_ring,
            "global": round(self.global_intensity, 3),
            "cal_step": self.cal_step,
        }
