"""Dream-catcher runtime: like the spider-web simulator, but some nodes are
*beads* with their own colour.

When a ripple reaches a bead the bead flares brighter, and the light flowing
*past* the bead onto the ordinary nodes downstream takes on a share of the
bead's colour. Because that tint is carried along every onward strand, ripples
that cross several beads blend their colours together and the whole web mixes.

Each bead colour is also a different *lens* on the ripple travelling through it.
No bead ever stops the signal -- it is always passed on somehow:

* fast   (amber)        -- speeds the wave up on every strand beyond it
* funnel (vivid green)  -- sends the signal on along a single direction only, in
                           a super-saturated, extra-bright version of its colour
* bounce (magenta)      -- duplicates the signal back the way it came

Interactions are edge-based (click a strand to send a ripple); the Bead tool
clicks a node to cycle its bead type/colour.
"""
from __future__ import annotations

import heapq
import random
import time

import numpy as np
import pygame

from spiderweb import ui
from spiderweb.engine import Engine
from spiderweb.events import Ambient, Event, _gaussian
from spiderweb.rings import RingProcessor
from spiderweb.simulator import _draw_bulb, _make_glow_sprite
from spiderweb.sound import Soundscape
from spiderweb.web import Web, _segment_distance

BG = (8, 9, 14)
SIDEBAR_BG = (6, 7, 11)
DIVIDER = (34, 38, 50)
SIDEBAR_W = 320      # left "lights" sidebar (also the web's x offset)
RIGHT_W = 300        # right "sound" sidebar
STRAND = (32, 36, 46)
EDGE_HL = (255, 210, 90)
NODE_HL = (255, 210, 90)
EDGE_PICK_DIST = 26.0
NODE_PICK_DIST = 28.0
TEXT = (210, 215, 225)

# ripple colours (the "uncoloured" light fired into the web)
PALETTE = [
    (0.85, 0.88, 1.00),  # cool white
    (1.00, 0.95, 0.80),  # warm white
    (0.30, 0.70, 1.00),  # sky
    (1.00, 0.45, 0.20),  # ember
]

# bead types, cycled by the Bead tool. No bead ever stops the signal -- it is
# always passed on somehow. Each bead has its own effect on the passing wave:
#   speed   -- multiplies the wave's speed on every strand *downstream* of the
#              bead (>1 faster, <1 slower).
#   funnel  -- the bead sends the signal on along just *one* outgoing direction
#              (chosen per ripple) and intensifies it: the onward light is a
#              super-saturated, brighter version of the bead's colour.
#   bounce  -- the bead duplicates the signal back in the opposite direction (a
#              return ripple toward the source), on top of the forward wave.
# The colour is both the bead's hue and the tint mixed into the passing light.
BEAD_TYPES = [
    {"name": "fast",   "color": (1.00, 0.85, 0.15), "speed": 2.2, "funnel": False, "bounce": False},  # amber
    {"name": "funnel", "color": (0.10, 1.00, 0.50), "speed": 1.0, "funnel": True,  "bounce": False},  # vivid green
    {"name": "bounce", "color": (1.00, 0.20, 0.60), "speed": 1.0, "funnel": False, "bounce": True},   # magenta
]
# colours indexed the same as BEAD_TYPES, for the many places that only need hue
BEAD_COLORS = [t["color"] for t in BEAD_TYPES]

# grouped control menus. Each is one panel of clickable rows; the keys still
# work globally via KEYMAP regardless of which menu a control lives in.
# LEFT column: impulse (touch/ripple interaction) then ambient.
IMPULSE_BUTTONS = [
    ("1", "Ripple", "tool:ripple_hops"),
    ("2", "Area", "tool:overlap"),
    ("spc", "Ripple colour", "act:color"),
    ("X", "Clear ripples", "act:clear"),
    ("esc", "Quit", "act:quit"),
]
# the signal meter + its calibration live together in their own menu
SIGNAL_BUTTONS = [
    ("C", "Calibrate signal", "act:cal"),
]
AMBIENT_BUTTONS = [
    ("A", "Ambient", "act:ambient"),
]
# RIGHT column: sound then beads.
SOUND_BUTTONS = [
    ("S", "Sound", "act:sound"),
]
BEADS_BUTTONS = [
    ("3", "Bead (cycle type)", "tool:bead"),
    ("R", "Shuffle beads", "act:shuffle"),
]
KEYMAP = {
    pygame.K_1: "tool:ripple_hops",
    pygame.K_2: "tool:overlap",
    pygame.K_3: "tool:bead",
    pygame.K_r: "act:shuffle",
    pygame.K_c: "act:cal",
    pygame.K_s: "act:sound",
    pygame.K_SPACE: "act:color",
    pygame.K_a: "act:ambient",
    pygame.K_x: "act:clear",
    pygame.K_ESCAPE: "act:quit",
}

AMBIENT_MODES = ("off", "shimmer", "breathe", "twinkle", "wander", "rainbow")
# the dream ambient sits back behind the signal response, so it is dimmed
AMBIENT_DIM = 0.4
TRAIL_MAX = 5.0
MIX_MIN, MIX_MAX = 0.1, 0.95
# signal decay slider: half-life (seconds) of a ripple's strength over time
DECAY_MIN, DECAY_MAX = 0.15, 3.0
# overlap tool: how far the neighbourhood reaches (active-node hops) and how
# strongly the light falls off per hop of distance
OV_MAX_HOPS = 5
OVF_MIN, OVF_MAX = 0.1, 0.95

# --- capacitive signal model ---------------------------------------------
# A touch is detected when an edge's signal crosses the threshold. Defaults are
# chosen so a touch (~CLICK_AMP) clears the threshold while background+hover
# stays under it; the Calibrate routine measures the real ranges and sets it.
CLICK_AMP = 1.5            # spike injected on the touched edge
THRESH_MIN, THRESH_MAX = 0.05, 2.0
SIGNAL_SCALE = 2.0         # top of the signal meter
HOVER_GLOW = 0.7           # default hover pre-light strength (slider-controlled)
HOVER_COLOR = (0.30, 0.44, 0.70)  # cool tone for the hover pre-light
# gain sliders: amplify the idle ambient and the hover response independently
AMB_GAIN_MIN, AMB_GAIN_MAX = 0.0, 1.5
HOV_GAIN_MIN, HOV_GAIN_MAX = 0.0, 3.0   # hover has a strong effect on the lights
DRONE_GAIN_MAX = 1.6       # drone volume trim (1.0 = unity)
CHIME_GAIN_MAX = 1.6       # ring-touch chime volume trim (1.0 = unity)
# constant always-on glow on the beads (slider-controlled). Keeps the beads lit
# and, via the drone tie, keeps them resonating at a steady level.
BEAD_GLOW_MAX = 0.8
BEAD_GLOW_DEFAULT = 0.08   # "Base bead chime" slider starts at 10% of max
# click-and-hold "charge": while a strand is held, its nodes brighten over this
# many seconds (to full), and fade back over HOLD_DECAY once released. The charge
# also whitens the held strand's colour toward the pure "signal" white.
HOLD_RAMP = 2.0
HOLD_DECAY = 0.6
# repeated taps on the same edge build a per-edge "press energy" that whitens the
# ripple colour toward white; each tap adds PRESS_STEP and it decays by half every
# PRESS_HALFLIFE seconds.
PRESS_STEP = 0.34
PRESS_HALFLIFE = 0.8
CAL_PROMPTS = ("", "background: keep clear, press C",
               "hover: hold hand near, press C", "touch: hold a touch, press C")

# --- chord / bead-note model ---------------------------------------------
# Every bead is one tone of a chord; when a ripple illuminates the bead it
# sounds that tone. Beads are ordered centre -> outer = low -> high. The chord
# *quality* is chosen from a dropdown whose options match the number of beads.
NOTE_ROOT = 220.0  # A3; chord tones stack upward from here
_MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
CHORDS = {
    2: [("5 (power)", [0, 7]), ("octave", [0, 12]), ("tritone", [0, 6])],
    3: [("maj", [0, 4, 7]), ("min", [0, 3, 7]), ("sus2", [0, 2, 7]),
        ("sus4", [0, 5, 7]), ("dim", [0, 3, 6]), ("aug", [0, 4, 8])],
    4: [("maj7", [0, 4, 7, 11]), ("7", [0, 4, 7, 10]), ("min7", [0, 3, 7, 10]),
        ("6", [0, 4, 7, 9]), ("m7b5", [0, 3, 6, 10]), ("dim7", [0, 3, 6, 9])],
    5: [("maj9", [0, 4, 7, 11, 14]), ("9", [0, 4, 7, 10, 14]),
        ("min9", [0, 3, 7, 10, 14]), ("6/9", [0, 4, 7, 9, 14]),
        ("m9", [0, 3, 7, 10, 14])],
    6: [("maj11", [0, 4, 7, 11, 14, 17]), ("11", [0, 4, 7, 10, 14, 17]),
        ("min11", [0, 3, 7, 10, 14, 17])],
    7: [("maj13", [0, 4, 7, 11, 14, 17, 21]), ("13", [0, 4, 7, 10, 14, 17, 21]),
        ("min13", [0, 3, 7, 10, 14, 17, 21])],
}


def _chord_options(n: int):
    """Chord choices for `n` beads: a curated list when we have one, otherwise a
    diatonic stack so any bead count still maps to sensible tones."""
    if n in CHORDS:
        return CHORDS[n]
    if n >= 1:
        return [("stack", [_MAJOR_SCALE[i % 7] + 12 * (i // 7) for i in range(n)])]
    return [("(add beads)", [0])]


def _saturate(c, k=2.2):
    """Push an RGB colour toward a pure, vivid hue (more saturated) while
    keeping its peak channel, by squashing the dimmer channels. `k`>1 deepens
    the saturation. Used by funnel beads so the light they pass on is intense."""
    c = np.asarray(c, dtype=float)
    mx = float(c.max())
    if mx <= 1e-6:
        return c
    return np.clip((c / mx) ** k * mx, 0.0, 1.0)


def _bead_accumulate(ctx, sources, beads, base_color, mix, bead_gain, multi=False,
                     whiten=0.0, bead_fx=None):
    """Wavefront traversal over the enabled-only topology from the seed lights.

    Returns (dist, colors, boost, reflections) per LED chain index.

    `dist` is the wave's *travel distance* to each node, measured in ordinary
    hops but warped by any speed-changing beads on the path: a fast bead
    shortens every hop downstream of it (so the wave arrives sooner = faster),
    a slow bead lengthens them (slower). With no speed beads this is exactly
    the active-node hop count, matching the old behaviour. The wave always
    travels through every bead -- nothing blocks it.

    The colour reaching each node is the *bead* colour carried along the path:
    the first bead on a path emits its own pure colour (it is not diluted by the
    white signal), and further beads blend into the carried colour by `mix`.
    `boost` marks beads so they flare brighter.

    A `funnel` bead passes the wave on along just one outgoing strand (chosen at
    random for this ripple) and the colour it sends on is super-saturated and
    boosted, so a single bright beam shoots off in one direction.

    `reflections` is a list of (node_index, arrival_dist) for every `bounce`
    bead the wave reached, so the caller can launch a return ripple from it.

    `whiten` (0..1) then lerps every colour toward white -- this is the "white
    signal" intensifying as a strand is pressed repeatedly or held down.

    `multi=True` treats every source as its own seed (e.g. a whole ring), rather
    than the two ends of one edge.
    """
    n = len(ctx.positions)
    idx_of = {nid: i for i, nid in enumerate(ctx.led_node_ids)}
    adj = ctx.led_adjacency()
    if len(sources) >= 2 and not multi:
        seeds = ctx.web.edge_seed_leds(sources[0], sources[1])
    else:
        seeds = set(sources)
    starts = [idx_of[s] for s in seeds if s in idx_of]

    colors = np.tile(np.asarray(base_color, dtype=float), (n, 1))
    tinted = np.zeros(n, dtype=bool)   # has a bead colour reached this node yet?
    boost = np.ones(n)
    bead_idx = {idx_of[b]: np.asarray(c, dtype=float)
                for b, c in beads.items() if b in idx_of}
    fx = {idx_of[b]: f for b, f in (bead_fx or {}).items() if b in idx_of}

    dist = np.full(n, np.inf)
    carry = np.ones(n)            # wave-speed multiplier inherited along the path
    parent: dict[int, int] = {}
    order: list[int] = []
    settled = np.zeros(n, dtype=bool)
    reflections: list[tuple[int, float]] = []

    # Dijkstra over fractional hop cost (1 / current speed multiplier), so a
    # path through a speed-changing bead warps how long every onward hop takes.
    heap: list[tuple[float, int]] = []
    for s in starts:
        dist[s] = 0.0
        heapq.heappush(heap, (0.0, s))
    while heap:
        d, u = heapq.heappop(heap)
        if settled[u]:
            continue
        settled[u] = True
        order.append(u)
        f = fx.get(u)
        if f and f.get("bounce"):
            # the wave keeps going forward AND a copy heads back the way it came
            reflections.append((u, d))
        out_spd = carry[u] * (f["speed"] if f else 1.0)
        step = 1.0 / max(out_spd, 1e-3)
        targets = adj[u]
        if f and f.get("funnel"):
            # send the signal on along a single direction only (prefer onward,
            # i.e. not straight back the way it came)
            cand = [v for v in adj[u] if v != parent.get(u)] or list(adj[u])
            targets = [random.choice(cand)] if cand else []
        for v in targets:
            nd = d + step
            if nd < dist[v]:
                dist[v] = nd
                carry[v] = out_spd
                parent[v] = u
                heapq.heappush(heap, (nd, v))

    for i in order:
        p = parent.get(i)
        if p is not None:
            colors[i] = colors[p]
            tinted[i] = tinted[p]
        if i in bead_idx:
            f = fx.get(i)
            if f and f.get("funnel"):
                # funnel: pass on an intense, super-saturated version of its
                # colour and flare brighter than an ordinary bead
                colors[i] = _saturate(bead_idx[i])
                boost[i] = bead_gain * 1.7
            else:
                # first bead on the path emits its pure colour; later beads blend
                colors[i] = ((1.0 - mix) * colors[i] + mix * bead_idx[i]
                             if tinted[i] else bead_idx[i])
                boost[i] = bead_gain
            tinted[i] = True
    if whiten > 0.0:
        colors = (1.0 - whiten) * colors + whiten * np.ones(3)
    return dist, colors, boost, reflections


class _DimAmbient(Ambient):
    """An Ambient whose whole output is scaled down, so the idle background
    stays understated next to the hover/proximity response."""

    def __init__(self, *args, dim=1.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.dim = dim

    def contribution(self, ctx, t):
        return super().contribution(ctx, t) * self.dim


class DreamSignal(Event):
    """Base for dream events that carry bead-mixed colour. Subclasses expose
    `weight(ctx, t)` (per-LED scalar strength) and `colors(ctx)` (per-LED RGB)
    so the dream compositor can average their colours where they overlap."""

    def weight(self, ctx, t):  # pragma: no cover - abstract
        raise NotImplementedError

    def colors(self, ctx):
        self._ensure(ctx)
        return self._colors

    def contribution(self, ctx, t):
        return self.weight(ctx, t)[:, None] * self.colors(ctx)


class DreamRipple(DreamSignal):
    """A wave that travels the web and accumulates bead colour along the way.

    Timing follows the graph (distance or active-node hops). The colour reaching
    each node is the ripple's base colour blended with every bead encountered on
    the path from the source, so downstream nodes carry a mix of upstream bead
    colours. Beads themselves are boosted brighter as the wave passes them.
    """

    def __init__(self, sources, beads, color=(0.85, 0.88, 1.0), speed=None,
                 width=None, start=0.0, half_life=1.0, metric="hops",
                 mix=0.6, bead_gain=2.4, multi=False, whiten=0.0, bead_fx=None):
        # the signal loses half its strength every `half_life` seconds; it is
        # considered done (and removed) after ~6 half-lives.
        self.half_life = max(float(half_life), 0.05)
        duration = self.half_life * 6.0 + 2.0
        super().__init__(color, start, duration)
        self.sources = [sources] if isinstance(sources, int) else list(sources)
        self.beads = {int(k): np.asarray(v, dtype=float) for k, v in beads.items()}
        self.bead_fx = {int(k): v for k, v in (bead_fx or {}).items()}
        self.mix = float(mix)
        self.bead_gain = float(bead_gain)
        self.metric = metric
        self.multi = multi
        self.whiten = float(whiten)
        if metric == "hops":
            self.speed = 0.7 if speed is None else speed
            self.width = 0.9 if width is None else width
        else:
            self.speed = 40.0 if speed is None else speed
            self.width = 55.0 if width is None else width
        self._dist = None
        self._colors = None
        self._boost = None
        self._refl = []   # (arrival_dist, dist-from-bead) for each bounce bead

    def _ensure(self, ctx):
        if self._dist is not None:
            return
        hop, colors, boost, reflections = _bead_accumulate(
            ctx, self.sources, self.beads, self.color, self.mix, self.bead_gain,
            multi=self.multi, whiten=self.whiten, bead_fx=self.bead_fx)
        self._colors = colors
        self._boost = boost
        if self.metric == "hops":
            self._dist = hop
            # each bounce bead duplicates the signal back the way it came: a
            # return wave from the bead, restricted to the region *behind* it
            # (nodes the forward wave reached no later than the bead), so it
            # travels back toward the source rather than re-lighting downstream.
            for node_i, arrival in reflections:
                nid = ctx.led_node_ids[node_i]
                rdist = ctx.led_hop_distances([nid])
                behind = self._dist <= float(arrival) + 1e-6
                rdist = np.where(behind, rdist, np.inf)
                self._refl.append((float(arrival), rdist))
        else:
            gd = ctx.web.graph_distances(self.sources, hops=False)
            self._dist = np.asarray([gd.get(nid, np.inf) for nid in ctx.led_node_ids], dtype=float)

    def weight(self, ctx, t):
        dt = t - self.start
        if dt < 0:
            return np.zeros(len(ctx.positions))
        self._ensure(ctx)
        radius = self.speed * dt
        env = _gaussian(self._dist - radius, self.width * 0.5)
        env = np.where(np.isfinite(self._dist), env, 0.0)
        for arrival, rdist in self._refl:
            # the return wave only begins once the forward wave reached the
            # bounce bead (radius == arrival); from then it sweeps back outward.
            rrad = radius - arrival
            if rrad <= 0.0:
                continue
            renv = _gaussian(rdist - rrad, self.width * 0.5)
            env = env + np.where(np.isfinite(rdist), renv, 0.0)
        amp = 0.5 ** (dt / self.half_life)  # diminishes over time
        return env * self._boost * amp


class DreamOverlap(DreamSignal):
    """A static neighbourhood lit from a source edge, with the same bead colour
    mixing as a ripple: beads in range tint the light, and that tint carries
    onto the nodes downstream along the path. `reach` sets how many active-node
    hops it covers; `falloff` sets how strongly emission drops per hop."""

    def __init__(self, sources, beads, color=(0.85, 0.88, 1.0), start=0.0,
                 duration=2.5, reach=2, falloff=0.5, mix=0.6, bead_gain=2.4,
                 whiten=0.0):
        super().__init__(color, start, duration)
        self.sources = [sources] if isinstance(sources, int) else list(sources)
        self.beads = {int(k): np.asarray(v, dtype=float) for k, v in beads.items()}
        self.reach = int(reach)
        self.falloff = float(falloff)
        self.mix = float(mix)
        self.bead_gain = float(bead_gain)
        self.whiten = float(whiten)
        self._hop = None
        self._colors = None
        self._boost = None

    def _ensure(self, ctx):
        if self._hop is not None:
            return
        # Area is a *static* neighbourhood, so bead speed effects don't apply:
        # we measure plain active-node hops (no warping) and never pass bead_fx,
        # so a slow bead can't push nodes outside `reach` and look like a block.
        # Bead *colour* still mixes along the path (that comes from `beads`).
        self._hop, self._colors, self._boost, _ = _bead_accumulate(
            ctx, self.sources, self.beads, self.color, self.mix, self.bead_gain,
            whiten=self.whiten)

    def weight(self, ctx, t):
        dt = t - self.start
        if dt < 0:
            return np.zeros(len(ctx.positions))
        self._ensure(ctx)
        inten = np.where(self._hop <= self.reach, self.falloff ** self._hop, 0.0)
        inten = np.where(np.isfinite(self._hop), inten, 0.0)
        if self.duration:
            inten = inten * max(0.0, 1.0 - dt / self.duration)
        return inten * self._boost


class BeadGlow(Event):
    """A persistent gentle glow at each bead so the beads are always visible."""

    def __init__(self, beads, level=0.30, pulse=0.10, speed=0.5, start=0.0):
        super().__init__((1.0, 1.0, 1.0), start, None)
        self.beads = {int(k): np.asarray(v, dtype=float) for k, v in beads.items()}
        self.level = level
        self.pulse = pulse
        self.speed = speed

    def contribution(self, ctx, t):
        n = len(ctx.positions)
        out = np.zeros((n, 3))
        idx_of = {nid: i for i, nid in enumerate(ctx.led_node_ids)}
        p = self.level + self.pulse * np.sin(t * self.speed * 6.283)
        for b, c in self.beads.items():
            if b in idx_of:
                out[idx_of[b]] = c * p
        return out


class EdgeSignals:
    """Per-edge capacitance signal model, mirroring the real installation.

    Each strand carries its own value that = ever-present background noise +
    a broad rise while a hand hovers near + a spike while it is touched. We do
    not know a precise hand position in the real rig; we only read these
    changing per-edge values and decide a "click" by thresholding them.
    """

    def __init__(self, web, noise_amp=0.10, hover_amp=0.55, hover_sigma=170.0,
                 hover_global=0.10, press_decay=0.80, seed=11):
        self.edges = list(web.strands)
        pos = {n.id: (n.x, n.y) for n in web.nodes}
        if self.edges:
            self.mid = np.array([[(pos[a][0] + pos[b][0]) / 2.0,
                                   (pos[a][1] + pos[b][1]) / 2.0] for a, b in self.edges])
        else:
            self.mid = np.zeros((0, 2))
        n = len(self.edges)
        rng = np.random.default_rng(seed)
        self.phase = rng.uniform(0, 2 * np.pi, n)
        self.freq = rng.uniform(0.4, 1.5, n)
        self.bias = rng.uniform(0.55, 1.0, n)  # per-edge noise scale
        self.noise_amp = noise_amp
        self.hover_amp = hover_amp
        self.hover_sigma = hover_sigma
        self.hover_global = hover_global
        self.press_decay = press_decay
        self.press = np.zeros(n)
        self.hover = np.zeros(n)
        self.signal = np.zeros(n)
        self.prev_above = np.zeros(n, dtype=bool)

    def update(self, t, hand):
        n = len(self.edges)
        if n == 0:
            return self.signal
        noise = self.noise_amp * self.bias * (
            0.5 + 0.5 * np.sin(t * 2 * np.pi * 0.3 * self.freq + self.phase))
        self.hover = np.zeros(n)
        if hand is not None:
            d = np.linalg.norm(self.mid - np.asarray(hand, dtype=float), axis=1)
            self.hover = self.hover_amp * (
                np.exp(-0.5 * (d / self.hover_sigma) ** 2) + self.hover_global)
        self.press *= self.press_decay
        self.signal = noise + self.hover + self.press
        return self.signal

    def inject(self, edge_index, amp):
        if 0 <= edge_index < len(self.press):
            self.press[edge_index] = amp

    def crossings(self, threshold, hysteresis=0.8):
        """Rising threshold crossings since the last call (Schmitt trigger)."""
        hi, lo = threshold, threshold * hysteresis
        above = self.prev_above.copy()
        above[self.signal >= hi] = True
        above[self.signal < lo] = False
        rising = np.where(above & ~self.prev_above)[0]
        self.prev_above = above
        return rising

    def nearest(self, x, y, max_dist=None):
        if len(self.edges) == 0:
            return None
        d = np.linalg.norm(self.mid - np.array([x, y], dtype=float), axis=1)
        i = int(np.argmin(d))
        if max_dist is not None and d[i] > max_dist:
            return None
        return i


def run(config_path: str, serial_port: str | None = None, baud: int = 921600,
        brightness: float = 1.0, fps: int = 60, rings: int = 4) -> None:
    web = Web.load(config_path)
    if not web.nodes:
        print(f"No web found at {config_path}. Run the editor or generate a sample first.")
        return

    engine = Engine(web, blend="add", brightness=brightness)

    # hardware link: bidirectional (LED frames out, per-ring capacitance in).
    # With a board attached, the real rings drive the ripples/chimes/drone; the
    # mouse still works for tuning. Without one, it's purely mouse-driven.
    device = None
    proc = None
    if serial_port:
        from spiderweb.device import SerialDevice
        try:
            device = SerialDevice(serial_port, baud, num_rings=rings)
            proc = RingProcessor(rings)
            print(f"Streaming to {serial_port} @ {baud}; reading {rings} rings")
        except Exception as e:  # noqa: BLE001
            print(f"serial open failed ({e!r}); running mouse-only")
            device = None

    # the web sits between the two sidebars; the right sidebar starts past it
    RIGHT_X = SIDEBAR_W + web.size[0]
    SLW = SIDEBAR_W - 24       # left control width
    RSW = RIGHT_W - 24         # right control width
    SLH, GAP = 44, 6          # full-height sliders so the labels stay readable

    def _slider(prev_bottom, x=12, w=SLW):
        o = (x, prev_bottom + GAP)
        panel, track = ui.slider_layout(o, width=w, height=SLH)
        return o, panel, track

    # ---- LEFT column: IMPULSE menu --------------------------------------
    impulse_panel, impulse_rows = ui.panel_layout(IMPULSE_BUTTONS, width=SLW)
    trail_origin, trail_panel, trail_track = _slider(impulse_panel.bottom)
    decay_origin, decay_panel, decay_track = _slider(trail_panel.bottom)
    reach_origin, reach_panel, reach_track = _slider(decay_panel.bottom)
    ovf_origin, ovf_panel, ovf_track = _slider(reach_panel.bottom)
    hov_origin, hov_panel, hov_track = _slider(ovf_panel.bottom)

    # ---- LEFT column: SIGNAL menu (calibrate + live meter) --------------
    # the touch/noise thresholds are set by dragging the meter handles below,
    # so there is no separate threshold slider.
    signal_panel, signal_rows = ui.panel_layout(
        SIGNAL_BUTTONS, origin=(12, hov_panel.bottom + GAP), width=SLW)

    meter_origin = (12, signal_panel.bottom + GAP)
    meter_h = 70
    meter_bottom = meter_origin[1] + meter_h
    METER_X = 12
    METER_W = SLW
    meter_bar_y = meter_origin[1] + 24
    meter_bar_h = 14
    meter_hit = pygame.Rect(METER_X, meter_bar_y - 8, METER_W, meter_bar_h + 18)

    def meter_value(x: int) -> float:
        return float(np.clip((x - METER_X) / METER_W, 0.0, 1.0)) * SIGNAL_SCALE

    def meter_x(v: float) -> int:
        return METER_X + int(min(v / SIGNAL_SCALE, 1.0) * METER_W)

    # ---- LEFT column: AMBIENT menu (below the signal meter) -------------
    ambient_panel, ambient_rows = ui.panel_layout(
        AMBIENT_BUTTONS, origin=(12, meter_origin[1] + meter_h + GAP), width=SLW)
    amb_origin, amb_panel, amb_track = _slider(ambient_panel.bottom)

    status_origin = (12, amb_panel.bottom + GAP)
    status_h = ui.PAD * 2 + 3 * 20

    # ---- RIGHT column: SOUND menu ---------------------------------------
    RX = RIGHT_X + 12
    sound_panel, sound_rows = ui.panel_layout(SOUND_BUTTONS, origin=(RX, 12), width=RSW)
    DD_H, DD_ROW = 32, 24
    dd_rect = pygame.Rect(RX, sound_panel.bottom + GAP, RSW, DD_H)

    def option_rect(oi: int) -> pygame.Rect:
        return pygame.Rect(dd_rect.x, dd_rect.bottom + oi * DD_ROW, dd_rect.w, DD_ROW)

    vol_origin, vol_panel, vol_track = _slider(dd_rect.bottom, x=RX, w=RSW)
    drone_origin, drone_panel, drone_track = _slider(vol_panel.bottom, x=RX, w=RSW)
    chime_origin, chime_panel, chime_track = _slider(drone_panel.bottom, x=RX, w=RSW)

    # ---- RIGHT column: BEADS menu (below the sound volumes) -------------
    beads_panel, beads_rows = ui.panel_layout(
        BEADS_BUTTONS, origin=(RX, chime_panel.bottom + GAP), width=RSW)
    mix_origin, mix_panel, mix_track = _slider(beads_panel.bottom, x=RX, w=RSW)
    bp_origin, bp_panel, bp_track = _slider(mix_panel.bottom, x=RX, w=RSW)
    sound_status_origin = (RX, bp_panel.bottom + GAP)

    # bead-type legend, listing what each bead colour does to a passing ripple
    BEAD_FX_DESC = {
        "fast": "speeds wave up",
        "funnel": "one way, intense colour",
        "bounce": "bounces back",
    }
    LEGEND_ROW = 18
    legend_origin = (RX, sound_status_origin[1] + status_h + GAP)
    legend_h = LEGEND_ROW * (len(BEAD_TYPES) + 1) + 6

    win_w = RIGHT_X + RIGHT_W
    win_h = max(web.size[1],
                status_origin[1] + status_h + 12,
                legend_origin[1] + legend_h + 12)

    pygame.init()
    screen = pygame.display.set_mode((win_w, win_h))
    pygame.display.set_caption("Dream-catcher simulator")
    font, title_font = ui.make_fonts()
    clock = pygame.time.Clock()

    leds = web.leds()
    led_pos = [(int(n.x) + SIDEBAR_W, int(n.y)) for n in leds]
    glow_sprite = _make_glow_sprite()
    persist = np.zeros((len(leds), 3))

    led_ids = [n.id for n in leds]

    # bead state: node_id -> palette index
    beads: dict[int, int] = {}

    def shuffle_beads() -> None:
        """Randomly scatter 4-8 beads of mixed types (repeats allowed, so the
        web can hold several boosters / mirrors / walls at once)."""
        beads.clear()
        if not led_ids:
            return
        lo = min(4, len(led_ids))
        hi = min(8, len(led_ids))
        count = random.randint(lo, max(lo, hi))
        nodes = random.sample(led_ids, count)
        for nid in nodes:
            beads[nid] = random.randrange(len(BEAD_TYPES))

    shuffle_beads()  # start with a random arrangement

    # responsive soundscape (independent of LEDs -- consumes signal + touches)
    sound = Soundscape()
    sound.start()

    state = {"tool": "ripple_hops", "color_idx": 0, "ambient_idx": 0,
             "trail": 3.0, "mix": 0.6, "decay": 2.5, "overlap_hops": 1,
             "overlap_falloff": 0.5, "threshold": 0.85, "noise_max": 0.30,
             "ambient_gain": AMBIENT_DIM, "hover_gain": HOVER_GLOW,
             "bead_level": BEAD_GLOW_DEFAULT,
             "sound_on": sound.ok, "volume": 0.7, "_last_trickle": 0.0,
             "drone_gain": 1.0, "chime_gain": 0.35,
             "drag_target": None, "touch_edge": None, "ambient": None,
             "hold_charge": 0.0, "hold_edge": None, "press_energy": {},
             "bead_glow": None, "cal_step": 0, "chord_idx": 0, "chord_open": False,
             "cal": {"bg": 0.0, "hover": 0.0, "click": 0.0}, "run": True}

    # web centre, so beads (and ring chimes) can be ordered by radius
    _xy = np.array([[n.x, n.y] for n in web.nodes], dtype=float)
    _center = _xy.mean(axis=0) if len(_xy) else np.zeros(2)

    # distance of each node from the web centre, so beads can be ordered
    # centre -> outer (low -> high chord tone)
    node_r = {n.id: float(np.linalg.norm(np.array([n.x, n.y]) - _center))
              for n in web.nodes}

    def bead_notes(intervals) -> dict[int, float]:
        """Map each bead to a chord-tone frequency, centre bead = lowest."""
        order = sorted(beads.keys(), key=lambda nid: node_r.get(nid, 0.0))
        L = max(len(intervals), 1)
        notes: dict[int, float] = {}
        for i, nid in enumerate(order):
            semi = intervals[i % L] + 12 * (i // L)
            notes[nid] = NOTE_ROOT * (2.0 ** (semi / 12.0))
        return notes

    # per-edge capacitance signal model
    signals = EdgeSignals(web)
    # which edges touch each LED node, so a rising signal pre-lights its nodes
    led_edges: list[list[int]] = [[] for _ in led_ids]
    led_row = {nid: i for i, nid in enumerate(led_ids)}
    for i, (a, b) in enumerate(signals.edges):
        for nid in (a, b):
            if nid in led_row:
                led_edges[led_row[nid]].append(i)

    # ring vs axis edges: only the *ring* (circumferential) strands are
    # interactive; the radial axis strands do nothing. An edge is a ring edge
    # when both ends sit on the same concentric ring.
    _ring_of, _ = web.node_rings()
    npos = {n.id: (n.x, n.y) for n in web.nodes}
    ring_edge_set = {i for i, (a, b) in enumerate(signals.edges)
                     if _ring_of.get(a) == _ring_of.get(b)}

    def pick_edge(mx: float, my: float, max_dist: float = EDGE_PICK_DIST):
        """Nearest *ring* edge index to (mx, my); axis edges are ignored."""
        best, bestd = None, float("inf")
        for i in ring_edge_set:
            a, b = signals.edges[i]
            d = _segment_distance(mx, my, npos[a][0], npos[a][1],
                                  npos[b][0], npos[b][1])
            if d < bestd:
                best, bestd = i, d
        return best if bestd <= max_dist else None

    # ring edges, innermost -> outermost, map to chord degrees 0,1,2,... so a
    # ring chime plays the 1st / 3rd / 5th of the current chord (and matches the
    # bead drone, which uses the same intervals).
    _ring_levels = sorted({_ring_of.get(signals.edges[i][0]) for i in ring_edge_set})
    ring_level_deg = {lvl: i for i, lvl in enumerate(_ring_levels)}

    def degree_freq(deg: int, intervals) -> float:
        L = max(len(intervals), 1)
        semi = intervals[deg % L] + 12 * (deg // L)
        return NOTE_ROOT * (2.0 ** (semi / 12.0))

    def edge_chord_freq(i: int, intervals) -> float:
        lvl = _ring_of.get(signals.edges[i][0], 0)
        return degree_freq(ring_level_deg.get(lvl, 0), intervals)

    def edge_pan(i: int) -> float:
        """Stereo pan (0..1) of an edge from its midpoint's screen x."""
        a, b = signals.edges[i]
        x = 0.5 * (npos[a][0] + npos[b][0])
        return min(max(x / max(web.size[0], 1), 0.0), 1.0)

    # map each LED node to a physical *sensor ring* index (0 = centre/inner),
    # matching the device's ring order; and the LED nodes that belong to each.
    def _sensor_of(nid: int) -> int:
        lvl = _ring_of.get(nid, 0) or 0
        return max(0, min(lvl - 1, rings - 1)) if lvl else 0

    led_sensor = np.array([_sensor_of(nid) for nid in led_ids], dtype=int) \
        if led_ids else np.zeros(0, dtype=int)
    sensor_nodes: dict[int, list[int]] = {r: [] for r in range(rings)}
    for nid in led_ids:
        sensor_nodes[_sensor_of(nid)].append(nid)

    def ring_pan(r: int) -> float:
        """Stereo pan (0..1) of a sensor ring from the mean screen x of its nodes."""
        nodes = sensor_nodes.get(r, [])
        if not nodes:
            return 0.5
        x = sum(npos[n][0] for n in nodes) / len(nodes)
        return min(max(x / max(web.size[0], 1), 0.0), 1.0)

    def fire_ring(r: int, t: float, color) -> None:
        """A physical ring touch: ripple outward from every LED on that ring."""
        nodes = sensor_nodes.get(r, [])
        if not nodes:
            return
        metric = "hops"
        engine.add(DreamRipple(nodes, bead_rgb(), color=color, start=t,
                               metric=metric, mix=state["mix"],
                               half_life=state["decay"], multi=True,
                               bead_fx=bead_fx()))

    def bead_rgb() -> dict[int, tuple]:
        return {nid: BEAD_COLORS[i] for nid, i in beads.items()}

    def bead_fx() -> dict[int, dict]:
        """Per-bead physical effect on the ripple (speed / block / reflect)."""
        return {nid: BEAD_TYPES[i] for nid, i in beads.items()}

    def refresh_beads() -> None:
        if state["bead_glow"] is not None and state["bead_glow"] in engine.events:
            engine.events.remove(state["bead_glow"])
        state["bead_glow"] = BeadGlow(bead_rgb())
        engine.add(state["bead_glow"])

    def set_ambient() -> None:
        if state["ambient"] is not None and state["ambient"] in engine.events:
            engine.events.remove(state["ambient"])
        state["ambient"] = None
        mode = AMBIENT_MODES[state["ambient_idx"]]
        if mode != "off":
            # subtle + calm: dimmed, lower amplitude, slower drift. The dim is
            # the live "Ambient gain" slider, refreshed each frame.
            state["ambient"] = _DimAmbient(mode=mode, amplitude=0.45, speed=0.16,
                                           dim=state["ambient_gain"])
            engine.add(state["ambient"])

    refresh_beads()
    set_ambient()

    def fire_edge(i: int, t: float, color) -> None:
        """Spawn the current touch-tool's effect from edge index `i`.

        Each tap charges this edge's "press energy"; the ripple is dyed toward
        white in proportion to that energy, so a single tap shows pure bead
        colour and repeated taps intensify the signal toward white."""
        if not (0 <= i < len(signals.edges)):
            return
        whiten = min(1.0, state["press_energy"].get(i, 0.0))
        state["press_energy"][i] = state["press_energy"].get(i, 0.0) + PRESS_STEP
        edge = list(signals.edges[i])
        tool = state["tool"]
        if tool == "overlap":
            engine.add(DreamOverlap(edge, bead_rgb(), color=color, start=t,
                                    reach=state["overlap_hops"],
                                    falloff=state["overlap_falloff"],
                                    mix=state["mix"], whiten=whiten))
        else:
            engine.add(DreamRipple(edge, bead_rgb(), color=color, start=t,
                                   metric="hops", mix=state["mix"],
                                   half_life=state["decay"], whiten=whiten,
                                   bead_fx=bead_fx()))

    def activate(action: str) -> None:
        if action.startswith("tool:"):
            state["tool"] = action.split(":", 1)[1]
        elif action == "act:color":
            state["color_idx"] = (state["color_idx"] + 1) % len(PALETTE)
        elif action == "act:cal":
            # the same C presses calibrate the live device rings in lockstep
            if proc is not None:
                proc.calibrate_step()
            # step through: arm -> capture background -> hover -> touch -> set thr
            step = state["cal_step"]
            cur = float(signals.signal.max()) if len(signals.signal) else 0.0
            if step == 0:
                state["cal_step"] = 1
            elif step == 1:
                state["cal"]["bg"] = cur
                state["cal_step"] = 2
            elif step == 2:
                state["cal"]["hover"] = cur
                state["cal_step"] = 3
            elif step == 3:
                state["cal"]["click"] = cur
                bg, hv, cl = state["cal"]["bg"], state["cal"]["hover"], state["cal"]["click"]
                # noise ceiling sits between background and hover; touch
                # threshold between hover and touch
                nz = (bg + hv) / 2.0 if hv > bg else bg * 1.5
                thr = (hv + cl) / 2.0 if cl > hv else max(hv, cur) * 1.1
                nz = float(np.clip(nz, THRESH_MIN, THRESH_MAX))
                thr = float(np.clip(thr, THRESH_MIN, THRESH_MAX))
                state["threshold"] = max(thr, nz + 0.03)
                state["noise_max"] = min(nz, state["threshold"] - 0.03)
                state["cal_step"] = 0
        elif action == "act:sound":
            state["sound_on"] = (not state["sound_on"]) and sound.ok
        elif action == "act:clear":
            engine.clear_all()
            refresh_beads()
            set_ambient()
            persist[:] = 0
        elif action == "act:ambient":
            state["ambient_idx"] = (state["ambient_idx"] + 1) % len(AMBIENT_MODES)
            set_ambient()
        elif action == "act:shuffle":
            shuffle_beads()
            refresh_beads()
        elif action == "act:quit":
            state["run"] = False

    t0 = time.time()
    while state["run"]:
        t = time.time() - t0
        color = PALETTE[state["color_idx"]]

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                state["run"] = False
            elif ev.type == pygame.KEYDOWN and ev.key in KEYMAP:
                activate(KEYMAP[ev.key])
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if ev.pos[0] < SIDEBAR_W:
                    # ---- LEFT column: IMPULSE + AMBIENT menus ----
                    hit = next((i for i, r in enumerate(impulse_rows)
                                if r.collidepoint(ev.pos)), None)
                    srow = next((i for i, r in enumerate(signal_rows)
                                 if r.collidepoint(ev.pos)), None)
                    arow = next((i for i, r in enumerate(ambient_rows)
                                 if r.collidepoint(ev.pos)), None)
                    if hit is not None:
                        activate(IMPULSE_BUTTONS[hit][2])
                    elif srow is not None:
                        activate(SIGNAL_BUTTONS[srow][2])
                    elif arow is not None:
                        activate(AMBIENT_BUTTONS[arow][2])
                    elif trail_panel.collidepoint(ev.pos):
                        state["drag_target"] = "trail"
                        state["trail"] = ui.value_from_x(trail_track, ev.pos[0]) * TRAIL_MAX
                    elif decay_panel.collidepoint(ev.pos):
                        state["drag_target"] = "decay"
                        state["decay"] = DECAY_MIN + ui.value_from_x(
                            decay_track, ev.pos[0]) * (DECAY_MAX - DECAY_MIN)
                    elif reach_panel.collidepoint(ev.pos):
                        state["drag_target"] = "reach"
                        state["overlap_hops"] = 1 + round(
                            ui.value_from_x(reach_track, ev.pos[0]) * (OV_MAX_HOPS - 1))
                    elif ovf_panel.collidepoint(ev.pos):
                        state["drag_target"] = "ovf"
                        state["overlap_falloff"] = OVF_MIN + ui.value_from_x(
                            ovf_track, ev.pos[0]) * (OVF_MAX - OVF_MIN)
                    elif hov_panel.collidepoint(ev.pos):
                        state["drag_target"] = "hov"
                        state["hover_gain"] = HOV_GAIN_MIN + ui.value_from_x(
                            hov_track, ev.pos[0]) * (HOV_GAIN_MAX - HOV_GAIN_MIN)
                    elif amb_panel.collidepoint(ev.pos):
                        state["drag_target"] = "amb"
                        state["ambient_gain"] = AMB_GAIN_MIN + ui.value_from_x(
                            amb_track, ev.pos[0]) * (AMB_GAIN_MAX - AMB_GAIN_MIN)
                    elif meter_hit.collidepoint(ev.pos):
                        # grab whichever band boundary (noise / touch) is nearer
                        dn = abs(ev.pos[0] - meter_x(state["noise_max"]))
                        dt = abs(ev.pos[0] - meter_x(state["threshold"]))
                        target = "m_noise" if dn <= dt else "m_thr"
                        state["drag_target"] = target
                        v = meter_value(ev.pos[0])
                        if target == "m_noise":
                            state["noise_max"] = min(v, state["threshold"] - 0.03)
                        else:
                            state["threshold"] = max(v, state["noise_max"] + 0.03)
                elif ev.pos[0] >= RIGHT_X:
                    # ---- RIGHT column: SOUND + BEADS menus ----
                    if dd_rect.collidepoint(ev.pos):
                        state["chord_open"] = not state["chord_open"]
                    elif state["chord_open"]:
                        _opts = _chord_options(len(beads))
                        sel = next((oi for oi in range(len(_opts))
                                    if option_rect(oi).collidepoint(ev.pos)), None)
                        if sel is not None:
                            state["chord_idx"] = sel
                        state["chord_open"] = False
                    else:
                        shit = next((i for i, r in enumerate(sound_rows)
                                     if r.collidepoint(ev.pos)), None)
                        bhit = next((i for i, r in enumerate(beads_rows)
                                     if r.collidepoint(ev.pos)), None)
                        if shit is not None:
                            activate(SOUND_BUTTONS[shit][2])
                        elif bhit is not None:
                            activate(BEADS_BUTTONS[bhit][2])
                        elif vol_panel.collidepoint(ev.pos):
                            state["drag_target"] = "vol"
                            state["volume"] = ui.value_from_x(vol_track, ev.pos[0])
                        elif drone_panel.collidepoint(ev.pos):
                            state["drag_target"] = "drone"
                            state["drone_gain"] = ui.value_from_x(
                                drone_track, ev.pos[0]) * DRONE_GAIN_MAX
                        elif chime_panel.collidepoint(ev.pos):
                            state["drag_target"] = "chime"
                            state["chime_gain"] = ui.value_from_x(
                                chime_track, ev.pos[0]) * CHIME_GAIN_MAX
                        elif mix_panel.collidepoint(ev.pos):
                            state["drag_target"] = "mix"
                            state["mix"] = MIX_MIN + ui.value_from_x(
                                mix_track, ev.pos[0]) * (MIX_MAX - MIX_MIN)
                        elif bp_panel.collidepoint(ev.pos):
                            state["drag_target"] = "bp"
                            state["bead_level"] = ui.value_from_x(
                                bp_track, ev.pos[0]) * BEAD_GLOW_MAX
                else:
                    mx, my = ev.pos[0] - SIDEBAR_W, ev.pos[1]
                    tool = state["tool"]
                    if tool in ("ripple_hops", "overlap"):
                        # a press doesn't fire directly -- it injects a touch
                        # spike on the nearest ring edge; the threshold decides.
                        idx = pick_edge(mx, my)
                        if idx is not None:
                            state["touch_edge"] = idx
                    elif tool == "bead":
                        n = web.nearest_node(mx, my, max_dist=NODE_PICK_DIST, led_only=True)
                        if n is not None:
                            cur = beads.get(n.id, -1)
                            nxt = cur + 1
                            if nxt >= len(BEAD_TYPES):
                                beads.pop(n.id, None)
                            else:
                                beads[n.id] = nxt
                            refresh_beads()
            elif ev.type == pygame.MOUSEMOTION and state["drag_target"]:
                if state["drag_target"] == "trail":
                    state["trail"] = ui.value_from_x(trail_track, ev.pos[0]) * TRAIL_MAX
                elif state["drag_target"] == "mix":
                    state["mix"] = MIX_MIN + ui.value_from_x(
                        mix_track, ev.pos[0]) * (MIX_MAX - MIX_MIN)
                elif state["drag_target"] == "decay":
                    state["decay"] = DECAY_MIN + ui.value_from_x(
                        decay_track, ev.pos[0]) * (DECAY_MAX - DECAY_MIN)
                elif state["drag_target"] == "reach":
                    state["overlap_hops"] = 1 + round(
                        ui.value_from_x(reach_track, ev.pos[0]) * (OV_MAX_HOPS - 1))
                elif state["drag_target"] == "ovf":
                    state["overlap_falloff"] = OVF_MIN + ui.value_from_x(
                        ovf_track, ev.pos[0]) * (OVF_MAX - OVF_MIN)
                elif state["drag_target"] == "amb":
                    state["ambient_gain"] = AMB_GAIN_MIN + ui.value_from_x(
                        amb_track, ev.pos[0]) * (AMB_GAIN_MAX - AMB_GAIN_MIN)
                elif state["drag_target"] == "hov":
                    state["hover_gain"] = HOV_GAIN_MIN + ui.value_from_x(
                        hov_track, ev.pos[0]) * (HOV_GAIN_MAX - HOV_GAIN_MIN)
                elif state["drag_target"] == "bp":
                    state["bead_level"] = ui.value_from_x(
                        bp_track, ev.pos[0]) * BEAD_GLOW_MAX
                elif state["drag_target"] == "vol":
                    state["volume"] = ui.value_from_x(vol_track, ev.pos[0])
                elif state["drag_target"] == "drone":
                    state["drone_gain"] = ui.value_from_x(
                        drone_track, ev.pos[0]) * DRONE_GAIN_MAX
                elif state["drag_target"] == "chime":
                    state["chime_gain"] = ui.value_from_x(
                        chime_track, ev.pos[0]) * CHIME_GAIN_MAX
                elif state["drag_target"] == "m_noise":
                    state["noise_max"] = min(meter_value(ev.pos[0]),
                                             state["threshold"] - 0.03)
                elif state["drag_target"] == "m_thr":
                    state["threshold"] = max(meter_value(ev.pos[0]),
                                             state["noise_max"] + 0.03)
            elif ev.type == pygame.MOUSEMOTION and state["touch_edge"] is not None:
                # drag the touch across the web (hand sliding over the strands)
                if SIDEBAR_W <= ev.pos[0] < RIGHT_X:
                    idx = pick_edge(ev.pos[0] - SIDEBAR_W, ev.pos[1])
                    if idx is not None:
                        state["touch_edge"] = idx
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                state["drag_target"] = None
                state["touch_edge"] = None

        # resolve the active chord and each bead's note for this frame
        opts = _chord_options(len(beads))
        state["chord_idx"] %= len(opts)
        chord_name, intervals = opts[state["chord_idx"]]
        notes_map = bead_notes(intervals)
        if sound.ok:
            sound.set_chord(list(notes_map.values()))

        # keep the ambient brightness in sync with its gain slider
        if state["ambient"] is not None:
            state["ambient"].dim = state["ambient_gain"]

        # constant always-on bead glow: the slider sets a steady level (no
        # pulsing), keeping the beads lit and the drone resonating evenly.
        if state["bead_glow"] is not None:
            state["bead_glow"].level = state["bead_level"]
            state["bead_glow"].pulse = 0.0

        # ---- click-and-hold charge: held strand's nodes brighten over time --
        hold_dt = clock.get_time() / 1000.0
        # decay each edge's repeated-press energy toward zero
        if state["press_energy"] and hold_dt > 0.0:
            fade = 0.5 ** (hold_dt / PRESS_HALFLIFE)
            state["press_energy"] = {k: v * fade for k, v in state["press_energy"].items()
                                     if v * fade > 0.01}
        held = state["touch_edge"]
        if held is not None and int(held) in ring_edge_set:
            state["hold_edge"] = int(held)
            state["hold_charge"] = min(1.0, state["hold_charge"] + hold_dt / HOLD_RAMP)
        else:
            state["hold_charge"] = max(0.0, state["hold_charge"] - hold_dt / HOLD_DECAY)
            if state["hold_charge"] <= 0.0:
                state["hold_edge"] = None

        # ---- update the per-edge capacitance signal & threshold trigger ----
        if state["touch_edge"] is not None:
            signals.inject(state["touch_edge"], CLICK_AMP)
        mxh, myh = pygame.mouse.get_pos()
        hand = (mxh - SIDEBAR_W, myh) if SIDEBAR_W <= mxh < RIGHT_X else None
        signals.update(t, hand)
        rising = signals.crossings(state["threshold"])
        if state["tool"] in ("ripple_hops", "overlap"):
            for i in rising:
                if int(i) in ring_edge_set:
                    fire_edge(int(i), t, color)

        # ---- drive the soundscape from the same signal (no LED knowledge) ----
        if sound.ok:
            maxsig = float(signals.signal.max()) if len(signals.signal) else 0.0
            span = max(state["threshold"] - state["noise_max"], 1e-3)
            inten = min(max((maxsig - state["noise_max"]) / span, 0.0), 1.0)
            sound.set_intensity(inten)
            sound.set_volume(state["volume"] if state["sound_on"] else 0.0)
            sound.set_drone_gain(state["drone_gain"])
            sound.set_chime_gain(state["chime_gain"])
            if state["sound_on"]:
                # touching a ring edge rings its chime (outer ring = higher).
                # This is separate from the bead chord drone below. The chime is
                # panned to the edge's own position on screen.
                for i in rising:
                    ii = int(i)
                    if ii in ring_edge_set:
                        vel = 0.5 + 0.5 * min(
                            float(signals.signal[ii]) / max(state["threshold"], 1e-3), 1.0)
                        sound.trigger_note(edge_chord_freq(ii, intervals), velocity=vel,
                                           pan=edge_pan(ii))

        # ---- live device: the real per-ring capacitance drives the rings ----
        dev_inten = None
        if device is not None and proc is not None:
            ring_rising = proc.update(device.latest_rings())
            for r in ring_rising:
                fire_ring(int(r), t, color)
                if sound.ok and state["sound_on"]:
                    sound.trigger_note(degree_freq(int(r), intervals),
                                       velocity=0.55 + 0.45 * proc.global_intensity,
                                       pan=ring_pan(int(r)))
            dev_inten = proc.intensity
            if sound.ok:
                sound.set_intensity(max(inten, proc.global_intensity))

        # ---- composite: ripples average their colours where they meet
        # (signals combining), then the bead glow / ambient are added under. ----
        engine.events = [e for e in engine.events if e.alive(t)]
        n = len(engine.positions)
        num = np.zeros((n, 3))   # intensity-weighted colour sum
        den = np.zeros(n)        # total ripple weight
        base = np.zeros((n, 3))  # bead glow + ambient (additive)
        for e in engine.events:
            if isinstance(e, DreamSignal):
                w = e.weight(engine.ctx, t)
                num += w[:, None] * e.colors(engine.ctx)
                den += w
            else:
                c = e.contribution(engine.ctx, t)
                if isinstance(c, np.ndarray) and c.shape == base.shape:
                    base += c
        # hover pre-light: signal *in the hover band* (above the noise ceiling,
        # below the touch threshold) warms a node before any touch fires.
        if len(signals.signal):
            led_sig = np.array([max((signals.signal[e] for e in eidxs), default=0.0)
                                for eidxs in led_edges])
            span = max(state["threshold"] - state["noise_max"], 1e-3)
            hover_frac = np.clip((led_sig - state["noise_max"]) / span, 0.0, 1.0)
            base += (hover_frac * state["hover_gain"])[:, None] * np.array(HOVER_COLOR)[None, :]
        # device hover: a hand approaching a real ring warms that ring's LEDs.
        if dev_inten is not None and len(led_sensor):
            dev_frac = np.clip(dev_inten[led_sensor], 0.0, 1.0)
            base += (dev_frac * state["hover_gain"])[:, None] * np.array(HOVER_COLOR)[None, :]
        # click-and-hold charge: the held strand's seed nodes glow brighter the
        # longer the button is held, then fade back when released. The glow starts
        # in each node's actual colour (its bead colour, else the signal colour)
        # and intensifies toward white as the charge builds.
        if state["hold_charge"] > 0.0 and state["hold_edge"] is not None:
            a, b = signals.edges[state["hold_edge"]]
            hc = state["hold_charge"]
            sig = np.array(color, dtype=float)
            for s in web.edge_seed_leds(a, b):
                if s not in led_row:
                    continue
                actual = np.array(BEAD_COLORS[beads[s]], dtype=float) if s in beads else sig
                base[led_row[s]] += hc * ((1.0 - hc) * actual + hc * np.ones(3))

        avg = num / np.maximum(den, 1e-6)[:, None]   # averaged hue
        strength = np.clip(den, 0.0, 1.0)            # combined ripple strength
        rgb = np.clip(base + avg * strength[:, None], 0.0, 1.0)

        # the bead chord drone resonates directly with the *light on the beads*:
        # the constant bead glow holds a steady resonance, and any ripple
        # washing over a bead swells the drone with it.
        if sound.ok:
            bidx = [led_row[nid] for nid in beads if nid in led_row]
            if bidx:
                bead_light = float(np.clip(rgb[bidx].max(axis=1).mean() * 1.3, 0.0, 1.0))
            else:
                bead_light = float(np.clip(strength.mean() * 3.0, 0.0, 1.0))
            sound.set_chord_level(bead_light if state["sound_on"] else 0.0)

        trail = state["trail"]
        if trail > 0.05:
            half_life = trail / 3.32
            dt = clock.get_time() / 1000.0
            decay = 0.5 ** (dt / half_life) if dt > 0 else 1.0
            persist[:] = np.maximum(rgb, persist * decay)
            out = persist
        else:
            out = rgb

        # ---- draw ----
        screen.fill(BG)
        pos = {n.id: (int(n.x) + SIDEBAR_W, int(n.y)) for n in web.nodes}
        # strands are physical threads -- always static; only the LED nodes light
        for a, b in signals.edges:
            if a in pos and b in pos:
                pygame.draw.line(screen, STRAND, pos[a], pos[b], 1)

        tool = state["tool"]
        mxy = pygame.mouse.get_pos()
        if SIDEBAR_W <= mxy[0] < RIGHT_X:
            if tool in ("ripple_hops", "overlap"):
                idx = pick_edge(mxy[0] - SIDEBAR_W, mxy[1])
                if idx is not None:
                    a, b = signals.edges[idx]
                    if a in pos and b in pos:
                        pygame.draw.line(screen, EDGE_HL, pos[a], pos[b], 3)
            elif tool == "bead":
                n = web.nearest_node(mxy[0] - SIDEBAR_W, mxy[1], max_dist=NODE_PICK_DIST, led_only=True)
                if n is not None:
                    pygame.draw.circle(screen, NODE_HL, pos[n.id], 11, 2)

        for i, p in enumerate(led_pos):
            c = out[i]
            disp = tuple(int(min(255, v * 255)) for v in c)
            bright = float(np.max(c))
            _draw_bulb(screen, glow_sprite, p, disp, bright)

        # bead rings so beads read as physical beads on the web
        for nid, ci in beads.items():
            if nid in pos:
                ring = tuple(int(v * 255) for v in BEAD_COLORS[ci])
                pygame.draw.circle(screen, ring, pos[nid], 7, 2)

        # two sidebars flanking the web: lights on the left, sound on the right
        pygame.draw.rect(screen, SIDEBAR_BG, (0, 0, SIDEBAR_W, win_h))
        pygame.draw.rect(screen, SIDEBAR_BG, (RIGHT_X, 0, RIGHT_W, win_h))
        pygame.draw.line(screen, DIVIDER, (SIDEBAR_W, 0), (SIDEBAR_W, win_h), 1)
        pygame.draw.line(screen, DIVIDER, (RIGHT_X, 0), (RIGHT_X, win_h), 1)
        mouse_pos = pygame.mouse.get_pos()

        # ---- LEFT column: IMPULSE menu (one card behind the whole section) ----
        ui.draw_card(screen, pygame.Rect(impulse_panel.x, impulse_panel.y, SLW,
                                         hov_panel.bottom - impulse_panel.y))
        irows = [(key, label, action == f"tool:{state['tool']}")
                 for key, label, action in IMPULSE_BUTTONS]
        click_hint = "click a node" if state["tool"] == "bead" else "click an edge"
        ui.draw_panel(screen, font, title_font, f"IMPULSE \u2014 {click_hint}", irows,
                      mouse_pos=mouse_pos, width=SLW, fill=False)
        sw = pygame.Surface((28, 16))
        sw.fill(tuple(int(v * 255) for v in color))
        screen.blit(sw, (SLW - 44, 22))

        trail_label = "Trail: off" if state["trail"] <= 0.05 else f"Trail: {state['trail']:.1f}s"
        ui.draw_slider(screen, font, trail_label, state["trail"] / TRAIL_MAX, trail_origin,
                       width=SLW, height=SLH, fill=False)
        ui.draw_slider(screen, font, f"Signal decay: {state['decay']:.1f}s half-life",
                       (state["decay"] - DECAY_MIN) / (DECAY_MAX - DECAY_MIN), decay_origin,
                       width=SLW, height=SLH, fill=False)
        oh = state["overlap_hops"]
        ui.draw_slider(screen, font, f"Area reach: {oh} hop" + ("s" if oh != 1 else ""),
                       (oh - 1) / (OV_MAX_HOPS - 1), reach_origin, width=SLW, height=SLH, fill=False)
        ui.draw_slider(screen, font, f"Area emission: {int(state['overlap_falloff'] * 100)}%/hop",
                       (state["overlap_falloff"] - OVF_MIN) / (OVF_MAX - OVF_MIN), ovf_origin,
                       width=SLW, height=SLH, fill=False)
        ui.draw_slider(screen, font, f"Hover gain: {int(state['hover_gain'] * 100)}%",
                       (state["hover_gain"] - HOV_GAIN_MIN) / (HOV_GAIN_MAX - HOV_GAIN_MIN),
                       hov_origin, width=SLW, height=SLH, fill=False)

        # ---- LEFT column: SIGNAL menu (calibrate + live meter, one card) ----
        ui.draw_card(screen, pygame.Rect(signal_panel.x, signal_panel.y, SLW,
                                         meter_bottom - signal_panel.y))
        cal_label = (f"Calibrate {state['cal_step']}/3" if state["cal_step"]
                     else "Calibrate signal")
        ui.draw_panel(screen, font, title_font, "SIGNAL",
                      [("C", cal_label, state["cal_step"] != 0)],
                      mouse_pos=mouse_pos, origin=signal_panel.topleft, width=SLW, fill=False)

        # ---- live signal meter: three draggable bands (noise / hover / touch) ----
        cur = float(signals.signal.max()) if len(signals.signal) else 0.0
        hot = int((signals.signal >= state["threshold"]).sum()) if len(signals.signal) else 0
        nz_x = meter_x(state["noise_max"])
        thr_x = meter_x(state["threshold"])
        screen.blit(font.render(f"Signal max {cur:.2f}   hot edges {hot}", True, TEXT),
                    (METER_X, meter_origin[1]))
        # band backgrounds: noise (grey) | hover (blue) | touch (red)
        pygame.draw.rect(screen, (40, 44, 54), (METER_X, meter_bar_y, nz_x - METER_X, meter_bar_h))
        pygame.draw.rect(screen, (28, 52, 84), (nz_x, meter_bar_y, thr_x - nz_x, meter_bar_h))
        pygame.draw.rect(screen, (74, 30, 34),
                         (thr_x, meter_bar_y, METER_X + METER_W - thr_x, meter_bar_h))
        # live level fill, coloured by which band it's in
        fill = meter_x(cur)
        if cur >= state["threshold"]:
            lvl_col = (235, 95, 85)
        elif cur >= state["noise_max"]:
            lvl_col = (95, 170, 235)
        else:
            lvl_col = (120, 130, 150)
        pygame.draw.rect(screen, lvl_col, (METER_X, meter_bar_y + 3, fill - METER_X, meter_bar_h - 6))
        # boundary handles (drag these to define the bands)
        for hx, hcol in ((nz_x, (150, 200, 255)), (thr_x, (255, 235, 120))):
            pygame.draw.line(screen, hcol, (hx, meter_bar_y - 4),
                             (hx, meter_bar_y + meter_bar_h + 4), 3)
            pygame.draw.circle(screen, hcol, (hx, meter_bar_y + meter_bar_h + 6), 4)
        # band labels
        lbl_y = meter_bar_y + meter_bar_h + 9
        screen.blit(font.render("noise", True, (140, 148, 162)), (METER_X, lbl_y))
        screen.blit(font.render("hover", True, (130, 180, 240)), ((nz_x + thr_x) // 2 - 16, lbl_y))
        screen.blit(font.render("touch", True, (240, 130, 120)),
                    (min(thr_x + 4, METER_X + METER_W - 32), lbl_y))

        # ---- LEFT column: AMBIENT menu (one card) ----
        ui.draw_card(screen, pygame.Rect(ambient_panel.x, ambient_panel.y, SLW,
                                         amb_panel.bottom - ambient_panel.y))
        ambient_mode = AMBIENT_MODES[state["ambient_idx"]]
        ui.draw_panel(screen, font, title_font, "AMBIENT",
                      [("A", f"Ambient: {ambient_mode}", ambient_mode != "off")],
                      mouse_pos=mouse_pos, origin=ambient_panel.topleft, width=SLW, fill=False)
        ui.draw_slider(screen, font, f"Ambient gain: {int(state['ambient_gain'] * 100)}%",
                       (state["ambient_gain"] - AMB_GAIN_MIN) / (AMB_GAIN_MAX - AMB_GAIN_MIN),
                       amb_origin, width=SLW, height=SLH, fill=False)

        cal_line = (f"calibrate: {CAL_PROMPTS[state['cal_step']]}"
                    if state["cal_step"] else
                    f"noise<{state['noise_max']:.2f}  hover  touch>{state['threshold']:.2f}")
        if device is not None and proc is not None:
            vals = " ".join(f"{v:.0f}" for v in proc.value)
            serial_line = (f"device {serial_port}  rings[{vals}]"
                           f"  ring{proc.active_ring} {proc.global_intensity:.2f}")
        else:
            serial_line = "device: off (mouse only)"
        ui.draw_status(
            screen, font,
            [
                cal_line,
                f"signals {sum(1 for e in engine.events if isinstance(e, DreamSignal))}"
                f"   beads {len(beads)}   LEDs {len(leds)}",
                serial_line,
            ],
            origin=status_origin, width=SLW,
        )

        # ---- RIGHT column: SOUND menu (one card behind the whole section) ----
        ui.draw_card(screen, pygame.Rect(sound_panel.x, sound_panel.y, RSW,
                                         chime_panel.bottom - sound_panel.y))
        sound_lbl = (f"Sound: {'on' if state['sound_on'] else 'off'}"
                     if sound.ok else "Sound: (no device)")
        ui.draw_panel(screen, font, title_font, "SOUND",
                      [("S", sound_lbl, state["sound_on"])],
                      mouse_pos=mouse_pos, origin=(RX, 12), width=RSW, fill=False)

        # chord dropdown (closed box; the open list overlays last)
        pygame.draw.rect(screen, (22, 28, 44), dd_rect, border_radius=5)
        pygame.draw.rect(screen, (64, 78, 110), dd_rect, 1, border_radius=5)
        screen.blit(font.render(f"Chord: {chord_name}  ({len(beads)} beads)", True, TEXT),
                    (dd_rect.x + 8, dd_rect.y + 8))
        pygame.draw.polygon(screen, (150, 165, 195), [
            (dd_rect.right - 18, dd_rect.y + 13), (dd_rect.right - 8, dd_rect.y + 13),
            (dd_rect.right - 13, dd_rect.y + 19)])

        vol_lbl = (f"Master volume: {int(state['volume'] * 100)}%"
                   if state["sound_on"] else "Master volume: (muted)")
        ui.draw_slider(screen, font, vol_lbl, state["volume"], vol_origin,
                       width=RSW, height=SLH, fill=False)
        ui.draw_slider(screen, font, f"Drone volume: {int(state['drone_gain'] * 100)}%",
                       state["drone_gain"] / DRONE_GAIN_MAX, drone_origin,
                       width=RSW, height=SLH, fill=False)
        ui.draw_slider(screen, font, f"Chime volume: {int(state['chime_gain'] * 100)}%",
                       state["chime_gain"] / CHIME_GAIN_MAX, chime_origin,
                       width=RSW, height=SLH, fill=False)

        # ---- RIGHT column: BEADS menu (one card) ----
        ui.draw_card(screen, pygame.Rect(beads_panel.x, beads_panel.y, RSW,
                                         bp_panel.bottom - beads_panel.y))
        brows = [(key, label, action == f"tool:{state['tool']}")
                 for key, label, action in BEADS_BUTTONS]
        ui.draw_panel(screen, font, title_font, "BEADS", brows,
                      mouse_pos=mouse_pos, origin=beads_panel.topleft, width=RSW, fill=False)
        ui.draw_slider(screen, font, f"Bead colour mix: {int(state['mix'] * 100)}%",
                       (state["mix"] - MIX_MIN) / (MIX_MAX - MIX_MIN), mix_origin,
                       width=RSW, height=SLH, fill=False)
        ui.draw_slider(screen, font, f"Base bead chime: {int(state['bead_level'] / BEAD_GLOW_MAX * 100)}%",
                       state["bead_level"] / BEAD_GLOW_MAX, bp_origin, width=RSW, height=SLH, fill=False)
        voices = len(sound._voices) if sound.ok else 0
        ui.draw_status(
            screen, font,
            [
                f"chord: {chord_name}",
                f"beads {len(beads)}   voices {voices}",
                f"sound: {'on' if state['sound_on'] else 'off'}",
            ],
            origin=sound_status_origin, width=RSW,
        )

        # ---- RIGHT column: BEAD LEGEND (what each bead colour does) ----
        lx, ly = legend_origin
        screen.blit(title_font.render("BEAD EFFECTS", True, TEXT), (lx, ly))
        for ti, bt in enumerate(BEAD_TYPES):
            ry = ly + LEGEND_ROW * (ti + 1)
            swatch = tuple(int(min(255, v * 255)) for v in bt["color"])
            pygame.draw.circle(screen, swatch, (lx + 6, ry + 7), 6)
            pygame.draw.circle(screen, (220, 224, 234), (lx + 6, ry + 7), 6, 1)
            label = f"{bt['name']} \u2014 {BEAD_FX_DESC.get(bt['name'], '')}"
            screen.blit(font.render(label, True, TEXT), (lx + 18, ry))

        # chord dropdown options, drawn last so they overlay the controls below
        if state["chord_open"]:
            mp = pygame.mouse.get_pos()
            for oi, (nm, _iv) in enumerate(opts):
                r = option_rect(oi)
                hovd = r.collidepoint(mp)
                pygame.draw.rect(screen, (44, 56, 84) if hovd else (28, 36, 56), r)
                pygame.draw.rect(screen, (64, 78, 110), r, 1)
                col = (255, 230, 140) if oi == state["chord_idx"] else TEXT
                screen.blit(font.render(nm, True, col), (r.x + 8, r.y + 3))

        pygame.display.flip()

        if device is not None:
            v = np.clip(out * brightness, 0, 1)
            v = np.power(v, engine.gamma)
            device.send_frame((v * 255 + 0.5).astype(np.uint8))

        clock.tick(fps)

    sound.close()
    if device is not None:
        device.send_frame(np.zeros((len(leds), 3), dtype=np.uint8))
        device.close()
    pygame.quit()
