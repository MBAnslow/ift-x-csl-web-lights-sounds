"""Responsive soundscape layer for the dream-catcher.

A port of the *sensitive-webs* idea (https://github.com/Polpii/sensitive-webs)
to a self-contained, real-time numpy synth. It deliberately knows nothing about
LEDs or nodes -- it only consumes a few abstract control values and turns them
into sound:

    * intensity (0..1)  -- how strongly a hand is sensed (hover). Opens the
      timbre up: brighter drone, louder shimmer/air, busier idle chimes.
    * pan       (0..1)  -- stereo placement (left..right) of the continuous bed.
      Individual notes carry their *own* pan from where they sit on screen.
    * a *touch* event   -- a discrete trigger that rings a chime/bell note.

Outward rings ring higher notes, just like the original. A continuous bed
(drone + air + shimmer) always runs so there is never dead silence.

Synthesis is block-vectorised numpy fed to a `sounddevice` output stream. If
`sounddevice` (or an output device) is unavailable the class degrades to a
silent no-op so the simulator still runs.
"""
from __future__ import annotations

import queue
import random

import numpy as np

# Per-ring bell roots: C3 G3 Bb3 C4 Eb4 G4 Bb4 C5 -- a low minor-pentatonic
# spread, exactly the registers the original used. Outer ring -> higher note.
RING_FREQS = [130.81, 196.00, 233.08, 261.63, 311.13, 392.00, 466.16, 523.25]

# inharmonic partials of a struck metal tube (harmonicity ~2.76) for a chime
_BELL_RATIOS = np.array([1.0, 2.0, 2.756, 5.404])
_BELL_AMPS = np.array([1.0, 0.55, 0.38, 0.20])

_DRONE_FREQS = np.array([32.70, 65.41, 98.00])
_DRONE_AMPS = np.array([0.6, 0.4, 0.22])

_MAX_VOICES = 48  # plenty of simultaneous chimes -> fully polyphonic


class _Voice:
    __slots__ = ("freq", "vel", "tau", "age", "phase", "lg", "rg")

    def __init__(self, freq, vel, tau, pan=0.5):
        self.freq = freq
        self.vel = vel
        self.tau = tau
        self.age = 0  # samples elapsed
        self.phase = np.zeros(len(_BELL_RATIOS))
        # equal-power stereo gains baked in at trigger time, from the note's
        # on-screen position (0 = far left, 1 = far right)
        pan = float(np.clip(pan, 0.0, 1.0))
        self.lg = float(np.cos(pan * (np.pi / 2.0)))
        self.rg = float(np.sin(pan * (np.pi / 2.0)))


class Soundscape:
    def __init__(self, samplerate: int = 44100, blocksize: int = 1024):
        self.sr = samplerate
        self.blocksize = blocksize
        self.ok = False
        self._stream = None

        # control parameters (written from the main thread; float writes are
        # atomic enough in CPython for our smoothing to absorb any tearing)
        self.intensity = 0.0
        self.pan = 0.5
        self.volume = 0.7
        self.enabled = True
        # bead chord drone: the set of tones, and how loud it currently is
        self.chord_level = 0.0
        self._chord = np.zeros(0)
        # independent mixer trims for the two musical layers (1.0 = unity)
        self.drone_gain = 1.0
        self.chime_gain = 1.0

        # internal smoothed state (audio thread only)
        self._inten = 0.0
        self._pan = 0.5
        self._vol = 0.0
        self._chord_lvl = 0.0
        self._drone_g = 1.0
        self._chime_g = 1.0
        self._phase = {}
        self._voices: list[_Voice] = []
        self._q: "queue.Queue" = queue.Queue(maxsize=64)

        # ~6 s of pink-ish noise for the "air" bed, streamed with wraparound
        rng = np.random.default_rng(3)
        white = rng.standard_normal(samplerate * 6)
        # cheap pinking: cumulative + detrend, then normalise
        pink = np.cumsum(white)
        pink -= np.linspace(pink[0], pink[-1], pink.size)
        pink /= (np.max(np.abs(pink)) + 1e-9)
        self._pink = pink.astype(np.float32)
        self._pink_i = 0

        try:
            import sounddevice as sd  # noqa: F401
            self._sd = sd
        except Exception:
            self._sd = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> bool:
        if self._sd is None:
            return False
        try:
            self._stream = self._sd.OutputStream(
                samplerate=self.sr, blocksize=self.blocksize, channels=2,
                dtype="float32", callback=self._callback)
            self._stream.start()
            self.ok = True
        except Exception:
            self.ok = False
            self._stream = None
        return self.ok

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self.ok = False

    # -- control -----------------------------------------------------------
    def set_intensity(self, x: float) -> None:
        self.intensity = float(np.clip(x, 0.0, 1.0))

    def set_pan(self, x: float) -> None:
        self.pan = float(np.clip(x, 0.0, 1.0))

    def set_volume(self, x: float) -> None:
        self.volume = float(np.clip(x, 0.0, 1.0))

    def set_chord(self, freqs) -> None:
        """Set the tones of the sustained bead-chord drone."""
        self._chord = np.asarray(list(freqs), dtype=float)

    def set_chord_level(self, x: float) -> None:
        """How loud the chord drone is (0..1), driven by web activation."""
        self.chord_level = float(np.clip(x, 0.0, 1.0))

    def set_drone_gain(self, x: float) -> None:
        """Mixer trim for the bead chord drone (0 = silent, 1 = unity)."""
        self.drone_gain = float(np.clip(x, 0.0, 2.0))

    def set_chime_gain(self, x: float) -> None:
        """Mixer trim for the ring-touch chimes (0 = silent, 1 = unity)."""
        self.chime_gain = float(np.clip(x, 0.0, 2.0))

    def trigger_ring(self, ring: int, velocity: float = 0.8, tau: float = 2.4,
                     pan: float = 0.5) -> None:
        """Ring a bell for the given ring index (outer = higher)."""
        if not self.ok:
            return
        ring = int(np.clip(ring, 0, len(RING_FREQS) - 1))
        self.trigger_note(RING_FREQS[ring], velocity, tau, pan)

    def trigger_note(self, freq: float, velocity: float = 0.8, tau: float = 2.6,
                     pan: float = 0.5) -> None:
        """Ring a bell at an arbitrary frequency (e.g. a chord tone).

        `pan` (0..1) places the note in the stereo field from its on-screen
        position, so each note sounds where it lives rather than where the
        mouse is."""
        if not self.ok:
            return
        try:
            self._q.put_nowait((float(freq), float(np.clip(velocity, 0.05, 1.0)),
                                float(tau), float(np.clip(pan, 0.0, 1.0))))
        except queue.Full:
            pass

    # -- audio thread ------------------------------------------------------
    def _osc(self, key, freq, frames):
        ph = self._phase.get(key, 0.0)
        incr = 2.0 * np.pi * freq / self.sr
        arr = ph + incr * np.arange(1, frames + 1)
        self._phase[key] = float(arr[-1] % (2.0 * np.pi))
        return np.sin(arr)

    def _callback(self, outdata, frames, time_info, status):
        # drain pending triggers into voices
        while True:
            try:
                freq, vel, tau, pan = self._q.get_nowait()
            except queue.Empty:
                break
            if len(self._voices) >= _MAX_VOICES:
                self._voices.pop(0)  # steal the oldest rather than block
            self._voices.append(_Voice(freq, vel, tau, pan))

        # smooth control params toward their targets across this block
        tgt_i = self.intensity if self.enabled else 0.0
        tgt_v = self.volume if self.enabled else 0.0
        tgt_c = self.chord_level if self.enabled else 0.0
        ni = self._inten + (tgt_i - self._inten) * 0.18
        np_ = self._pan + (self.pan - self._pan) * 0.20
        nv = self._vol + (tgt_v - self._vol) * 0.15
        nc = self._chord_lvl + (tgt_c - self._chord_lvl) * 0.08  # slow swell/fade
        ndg = self._drone_g + (self.drone_gain - self._drone_g) * 0.2
        ncg = self._chime_g + (self.chime_gain - self._chime_g) * 0.2
        inten = np.linspace(self._inten, ni, frames, endpoint=False)
        pan = np.linspace(self._pan, np_, frames, endpoint=False)
        vol = np.linspace(self._vol, nv, frames, endpoint=False)
        clvl = np.linspace(self._chord_lvl, nc, frames, endpoint=False)
        dgain = np.linspace(self._drone_g, ndg, frames, endpoint=False)
        cgain = np.linspace(self._chime_g, ncg, frames, endpoint=False)
        self._inten, self._pan, self._vol, self._chord_lvl = ni, np_, nv, nc
        self._drone_g, self._chime_g = ndg, ncg

        mix = np.zeros(frames, dtype=np.float64)

        # drone bed: low partials always on; upper partials brighten with hover
        bright = 0.35 + 0.65 * inten
        for i, (f, a) in enumerate(zip(_DRONE_FREQS, _DRONE_AMPS)):
            w = a * (bright if i > 0 else 1.0)
            mix += 0.16 * w * self._osc(("dr", i), f, frames)

        # bead chord drone: the chord tones, sustained, swelling with activation
        chord = self._chord
        if chord.size and nc > 1e-4:
            cs = np.zeros(frames)   # body (octave below)
            ot = np.zeros(frames)   # high overtones / shimmer
            for i, f in enumerate(chord):
                fo = f * 0.5  # background drone sits an octave below the chimes
                cs += self._osc(("chd", i), fo, frames)
                cs += 0.5 * self._osc(("chd2", i), fo * 1.005, frames)  # detune
                # upper partials add air/shimmer on top of the low body
                ot += 0.5 * self._osc(("ot2", i), fo * 2.0, frames)
                ot += 0.3 * self._osc(("ot3", i), fo * 3.01, frames)
                ot += 0.2 * self._osc(("ot4", i), fo * 4.0, frames)
            cs /= (chord.size * 1.5)
            ot /= max(chord.size, 1)
            shimmer = 0.2 + 0.5 * inten  # overtones open up as a hand nears
            mix += dgain * clvl * (0.34 * cs + 0.16 * shimmer * ot)

        # air: pink noise bed, a touch louder with hover
        seg = self._take_pink(frames)
        mix += seg * (0.05 + 0.16 * inten)

        # the continuous bed (drone + chord + air) is a wide wash placed by the
        # global pan; default centred.
        left = mix * np.cos(pan * (np.pi / 2.0))
        right = mix * np.sin(pan * (np.pi / 2.0))

        # bells / chimes: each note is panned to *its own* on-screen position
        if self._voices:
            n = np.arange(frames)
            alive = []
            for v in self._voices:
                env = np.exp(-(v.age + n) / (self.sr * v.tau))
                sig = np.zeros(frames)
                incr = 2.0 * np.pi * v.freq * _BELL_RATIOS / self.sr
                for k in range(len(_BELL_RATIOS)):
                    arr = v.phase[k] + incr[k] * np.arange(1, frames + 1)
                    v.phase[k] = float(arr[-1] % (2.0 * np.pi))
                    sig += _BELL_AMPS[k] * np.sin(arr)
                voice = 0.15 * cgain * v.vel * env * sig
                left += voice * v.lg
                right += voice * v.rg
                v.age += frames
                if env[-1] > 1e-3:
                    alive.append(v)
            self._voices = alive

        # master: soft-clip each channel, then apply master volume
        outdata[:, 0] = (np.tanh(left * 1.1) * vol).astype(np.float32)
        outdata[:, 1] = (np.tanh(right * 1.1) * vol).astype(np.float32)

    def _take_pink(self, frames):
        i = self._pink_i
        n = self._pink.size
        if i + frames <= n:
            seg = self._pink[i:i + frames]
            self._pink_i = (i + frames) % n
        else:
            first = self._pink[i:]
            seg = np.concatenate([first, self._pink[: frames - first.size]])
            self._pink_i = frames - first.size
        return seg.astype(np.float64)
