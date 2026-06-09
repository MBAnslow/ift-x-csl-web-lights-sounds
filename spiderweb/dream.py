"""Dream-catcher runtime: like the spider-web simulator, but some nodes are
*beads* with their own colour.

When a ripple reaches a bead the bead flares brighter, and the light flowing
*past* the bead onto the ordinary nodes downstream takes on a share of the
bead's colour. Because that tint is carried along every onward strand, ripples
that cross several beads blend their colours together and the whole web mixes.

Interactions are edge-based (click a strand to send a ripple); the Bead tool
clicks a node to cycle its bead colour.
"""
from __future__ import annotations

import random
import time
from collections import deque

import numpy as np
import pygame

from spiderweb import ui
from spiderweb.engine import Engine
from spiderweb.events import Ambient, Event, _gaussian
from spiderweb.simulator import _draw_bulb, _make_glow_sprite
from spiderweb.web import Web

BG = (8, 9, 14)
SIDEBAR_BG = (6, 7, 11)
DIVIDER = (34, 38, 50)
SIDEBAR_W = 320
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

# bead colours, cycled by the Bead tool (vivid, well-separated hues)
BEAD_COLORS = [
    (1.00, 0.18, 0.30),  # red
    (1.00, 0.55, 0.10),  # orange
    (1.00, 0.85, 0.15),  # amber
    (0.25, 0.95, 0.45),  # green
    (0.20, 0.65, 1.00),  # blue
    (0.70, 0.30, 1.00),  # violet
    (1.00, 0.35, 0.80),  # pink
]

BUTTONS = [
    ("1", "Ripple \u2014 distance", "tool:ripple"),
    ("2", "Ripple \u2014 hops", "tool:ripple_hops"),
    ("3", "Overlap (edge)", "tool:overlap"),
    ("4", "Bead (cycle colour)", "tool:bead"),
    ("R", "Shuffle beads", "act:shuffle"),
    ("C", "Calibrate signal", "act:cal"),
    ("spc", "Ripple colour", "act:color"),
    ("A", "Ambient", "act:ambient"),
    ("X", "Clear ripples", "act:clear"),
    ("esc", "Quit", "act:quit"),
]
KEYMAP = {
    pygame.K_1: "tool:ripple",
    pygame.K_2: "tool:ripple_hops",
    pygame.K_3: "tool:overlap",
    pygame.K_4: "tool:bead",
    pygame.K_r: "act:shuffle",
    pygame.K_c: "act:cal",
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
DECAY_MIN, DECAY_MAX = 0.15, 4.0
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
HOVER_GLOW = 0.22          # default hover pre-light strength (slider-controlled)
HOVER_COLOR = (0.30, 0.44, 0.70)  # cool tone for the hover pre-light
# gain sliders: amplify the idle ambient and the hover response independently
AMB_GAIN_MIN, AMB_GAIN_MAX = 0.0, 1.5
HOV_GAIN_MIN, HOV_GAIN_MAX = 0.0, 1.0
CAL_PROMPTS = ("", "background: keep clear, press C",
               "hover: hold hand near, press C", "touch: hold a touch, press C")


def _bead_accumulate(ctx, sources, beads, base_color, mix, bead_gain):
    """BFS over the enabled-only topology from the edge's seed lights.

    Returns (hop, colors, boost) per LED chain index. Hops count active-node
    steps (deactivated nodes are skipped). `colors` is the base colour blended
    with every bead on the path from the source, so the bead tints carry onto
    everything downstream and several beads mix together. `boost` marks beads
    so they flare brighter.
    """
    n = len(ctx.positions)
    idx_of = {nid: i for i, nid in enumerate(ctx.led_node_ids)}
    adj = ctx.led_adjacency()
    if len(sources) >= 2:
        seeds = ctx.web.edge_seed_leds(sources[0], sources[1])
    else:
        seeds = set(sources)
    starts = [idx_of[s] for s in seeds if s in idx_of]

    colors = np.tile(np.asarray(base_color, dtype=float), (n, 1))
    boost = np.ones(n)
    bead_idx = {idx_of[b]: np.asarray(c, dtype=float)
                for b, c in beads.items() if b in idx_of}

    hop = np.full(n, np.inf)
    parent: dict[int, int] = {}
    order: list[int] = list(starts)
    seen = set(starts)
    dq = deque(starts)
    for s in starts:
        hop[s] = 0.0
    while dq:
        u = dq.popleft()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                parent[v] = u
                hop[v] = hop[u] + 1.0
                order.append(v)
                dq.append(v)
    for i in order:
        p = parent.get(i)
        if p is not None:
            colors[i] = colors[p]
        if i in bead_idx:
            colors[i] = (1.0 - mix) * colors[i] + mix * bead_idx[i]
            boost[i] = bead_gain
    return hop, colors, boost


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
                 mix=0.6, bead_gain=2.4):
        # the signal loses half its strength every `half_life` seconds; it is
        # considered done (and removed) after ~6 half-lives.
        self.half_life = max(float(half_life), 0.05)
        duration = self.half_life * 6.0 + 2.0
        super().__init__(color, start, duration)
        self.sources = [sources] if isinstance(sources, int) else list(sources)
        self.beads = {int(k): np.asarray(v, dtype=float) for k, v in beads.items()}
        self.mix = float(mix)
        self.bead_gain = float(bead_gain)
        self.metric = metric
        if metric == "hops":
            self.speed = 3.0 if speed is None else speed
            self.width = 0.9 if width is None else width
        else:
            self.speed = 150.0 if speed is None else speed
            self.width = 55.0 if width is None else width
        self._dist = None
        self._colors = None
        self._boost = None

    def _ensure(self, ctx):
        if self._dist is not None:
            return
        hop, colors, boost = _bead_accumulate(
            ctx, self.sources, self.beads, self.color, self.mix, self.bead_gain)
        self._colors = colors
        self._boost = boost
        if self.metric == "hops":
            self._dist = hop
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
        amp = 0.5 ** (dt / self.half_life)  # diminishes over time
        return env * self._boost * amp


class DreamOverlap(DreamSignal):
    """A static neighbourhood lit from a source edge, with the same bead colour
    mixing as a ripple: beads in range tint the light, and that tint carries
    onto the nodes downstream along the path. `reach` sets how many active-node
    hops it covers; `falloff` sets how strongly emission drops per hop."""

    def __init__(self, sources, beads, color=(0.85, 0.88, 1.0), start=0.0,
                 duration=2.5, reach=2, falloff=0.5, mix=0.6, bead_gain=2.4):
        super().__init__(color, start, duration)
        self.sources = [sources] if isinstance(sources, int) else list(sources)
        self.beads = {int(k): np.asarray(v, dtype=float) for k, v in beads.items()}
        self.reach = int(reach)
        self.falloff = float(falloff)
        self.mix = float(mix)
        self.bead_gain = float(bead_gain)
        self._hop = None
        self._colors = None
        self._boost = None

    def _ensure(self, ctx):
        if self._hop is not None:
            return
        self._hop, self._colors, self._boost = _bead_accumulate(
            ctx, self.sources, self.beads, self.color, self.mix, self.bead_gain)

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
        brightness: float = 1.0, fps: int = 60) -> None:
    web = Web.load(config_path)
    if not web.nodes:
        print(f"No web found at {config_path}. Run the editor or generate a sample first.")
        return

    engine = Engine(web, blend="add", brightness=brightness)

    link = None
    if serial_port:
        from spiderweb.serial_link import SerialLink
        link = SerialLink(serial_port, baud)
        print(f"Streaming to {serial_port} @ {baud}")

    panel_rect, row_rects = ui.panel_layout(BUTTONS)
    trail_origin = (12, panel_rect.bottom + 8)
    trail_panel, trail_track = ui.slider_layout(trail_origin)
    mix_origin = (12, trail_panel.bottom + 8)
    mix_panel, mix_track = ui.slider_layout(mix_origin)
    decay_origin = (12, mix_panel.bottom + 8)
    decay_panel, decay_track = ui.slider_layout(decay_origin)
    reach_origin = (12, decay_panel.bottom + 8)
    reach_panel, reach_track = ui.slider_layout(reach_origin)
    ovf_origin = (12, reach_panel.bottom + 8)
    ovf_panel, ovf_track = ui.slider_layout(ovf_origin)
    thr_origin = (12, ovf_panel.bottom + 8)
    thr_panel, thr_track = ui.slider_layout(thr_origin)
    amb_origin = (12, thr_panel.bottom + 8)
    amb_panel, amb_track = ui.slider_layout(amb_origin)
    hov_origin = (12, amb_panel.bottom + 8)
    hov_panel, hov_track = ui.slider_layout(hov_origin)
    meter_origin = (12, hov_panel.bottom + 8)
    meter_h = 64
    # interactive signal meter: a draggable bar where the user marks the bands
    METER_X = 12
    METER_W = SIDEBAR_W - 24
    meter_bar_y = meter_origin[1] + 24
    meter_bar_h = 14
    # generous hit-zone so the boundary handles are easy to grab
    meter_hit = pygame.Rect(METER_X, meter_bar_y - 8, METER_W, meter_bar_h + 18)

    def meter_value(x: int) -> float:
        return float(np.clip((x - METER_X) / METER_W, 0.0, 1.0)) * SIGNAL_SCALE

    def meter_x(v: float) -> int:
        return METER_X + int(min(v / SIGNAL_SCALE, 1.0) * METER_W)

    status_origin = (12, meter_origin[1] + meter_h + 8)
    status_h = ui.PAD * 2 + 3 * 20
    win_w = SIDEBAR_W + web.size[0]
    win_h = max(web.size[1], status_origin[1] + status_h + 12)

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
        """Randomly choose how many nodes are beads and which, with random colours."""
        beads.clear()
        if not led_ids:
            return
        count = random.randint(max(2, len(led_ids) // 8), max(3, len(led_ids) // 2))
        for nid in random.sample(led_ids, min(count, len(led_ids))):
            beads[nid] = random.randrange(len(BEAD_COLORS))

    shuffle_beads()  # start with a random arrangement

    state = {"tool": "ripple_hops", "color_idx": 0, "ambient_idx": 1,
             "trail": 1.5, "mix": 0.6, "decay": 1.2, "overlap_hops": 2,
             "overlap_falloff": 0.5, "threshold": 0.85, "noise_max": 0.30,
             "ambient_gain": AMBIENT_DIM, "hover_gain": HOVER_GLOW,
             "drag_target": None, "touch_edge": None, "ambient": None,
             "bead_glow": None, "cal_step": 0,
             "cal": {"bg": 0.0, "hover": 0.0, "click": 0.0}, "run": True}

    # per-edge capacitance signal model + index lookup (strand tuple -> index)
    signals = EdgeSignals(web)
    edge_index = {e: i for i, e in enumerate(signals.edges)}
    # which edges touch each LED node, so a rising signal pre-lights its nodes
    led_edges: list[list[int]] = [[] for _ in led_ids]
    led_row = {nid: i for i, nid in enumerate(led_ids)}
    for i, (a, b) in enumerate(signals.edges):
        for nid in (a, b):
            if nid in led_row:
                led_edges[led_row[nid]].append(i)

    def bead_rgb() -> dict[int, tuple]:
        return {nid: BEAD_COLORS[i] for nid, i in beads.items()}

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
        """Spawn the current touch-tool's effect from edge index `i`."""
        if not (0 <= i < len(signals.edges)):
            return
        edge = list(signals.edges[i])
        tool = state["tool"]
        if tool == "overlap":
            engine.add(DreamOverlap(edge, bead_rgb(), color=color, start=t,
                                    reach=state["overlap_hops"],
                                    falloff=state["overlap_falloff"], mix=state["mix"]))
        else:
            metric = "distance" if tool == "ripple" else "hops"
            engine.add(DreamRipple(edge, bead_rgb(), color=color, start=t,
                                   metric=metric, mix=state["mix"],
                                   half_life=state["decay"]))

    def activate(action: str) -> None:
        if action.startswith("tool:"):
            state["tool"] = action.split(":", 1)[1]
        elif action == "act:color":
            state["color_idx"] = (state["color_idx"] + 1) % len(PALETTE)
        elif action == "act:cal":
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
                    hit = next((i for i, r in enumerate(row_rects) if r.collidepoint(ev.pos)), None)
                    if hit is not None:
                        activate(BUTTONS[hit][2])
                    elif trail_panel.collidepoint(ev.pos):
                        state["drag_target"] = "trail"
                        state["trail"] = ui.value_from_x(trail_track, ev.pos[0]) * TRAIL_MAX
                    elif mix_panel.collidepoint(ev.pos):
                        state["drag_target"] = "mix"
                        state["mix"] = MIX_MIN + ui.value_from_x(
                            mix_track, ev.pos[0]) * (MIX_MAX - MIX_MIN)
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
                    elif thr_panel.collidepoint(ev.pos):
                        state["drag_target"] = "thr"
                        state["threshold"] = THRESH_MIN + ui.value_from_x(
                            thr_track, ev.pos[0]) * (THRESH_MAX - THRESH_MIN)
                    elif amb_panel.collidepoint(ev.pos):
                        state["drag_target"] = "amb"
                        state["ambient_gain"] = AMB_GAIN_MIN + ui.value_from_x(
                            amb_track, ev.pos[0]) * (AMB_GAIN_MAX - AMB_GAIN_MIN)
                    elif hov_panel.collidepoint(ev.pos):
                        state["drag_target"] = "hov"
                        state["hover_gain"] = HOV_GAIN_MIN + ui.value_from_x(
                            hov_track, ev.pos[0]) * (HOV_GAIN_MAX - HOV_GAIN_MIN)
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
                else:
                    mx, my = ev.pos[0] - SIDEBAR_W, ev.pos[1]
                    tool = state["tool"]
                    if tool in ("ripple", "ripple_hops", "overlap"):
                        # a press doesn't fire directly -- it injects a touch
                        # spike on the nearest edge; the threshold decides.
                        edge = web.nearest_edge(mx, my, max_dist=EDGE_PICK_DIST)
                        if edge is not None and edge in edge_index:
                            state["touch_edge"] = edge_index[edge]
                    elif tool == "bead":
                        n = web.nearest_node(mx, my, max_dist=NODE_PICK_DIST, led_only=True)
                        if n is not None:
                            cur = beads.get(n.id, -1)
                            nxt = cur + 1
                            if nxt >= len(BEAD_COLORS):
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
                elif state["drag_target"] == "thr":
                    state["threshold"] = THRESH_MIN + ui.value_from_x(
                        thr_track, ev.pos[0]) * (THRESH_MAX - THRESH_MIN)
                elif state["drag_target"] == "amb":
                    state["ambient_gain"] = AMB_GAIN_MIN + ui.value_from_x(
                        amb_track, ev.pos[0]) * (AMB_GAIN_MAX - AMB_GAIN_MIN)
                elif state["drag_target"] == "hov":
                    state["hover_gain"] = HOV_GAIN_MIN + ui.value_from_x(
                        hov_track, ev.pos[0]) * (HOV_GAIN_MAX - HOV_GAIN_MIN)
                elif state["drag_target"] == "m_noise":
                    state["noise_max"] = min(meter_value(ev.pos[0]),
                                             state["threshold"] - 0.03)
                elif state["drag_target"] == "m_thr":
                    state["threshold"] = max(meter_value(ev.pos[0]),
                                             state["noise_max"] + 0.03)
            elif ev.type == pygame.MOUSEMOTION and state["touch_edge"] is not None:
                # drag the touch across the web (hand sliding over the strands)
                if ev.pos[0] >= SIDEBAR_W:
                    edge = web.nearest_edge(ev.pos[0] - SIDEBAR_W, ev.pos[1],
                                            max_dist=EDGE_PICK_DIST)
                    if edge is not None and edge in edge_index:
                        state["touch_edge"] = edge_index[edge]
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                state["drag_target"] = None
                state["touch_edge"] = None

        # keep the ambient brightness in sync with its gain slider
        if state["ambient"] is not None:
            state["ambient"].dim = state["ambient_gain"]

        # ---- update the per-edge capacitance signal & threshold trigger ----
        if state["touch_edge"] is not None:
            signals.inject(state["touch_edge"], CLICK_AMP)
        mxh, myh = pygame.mouse.get_pos()
        hand = (mxh - SIDEBAR_W, myh) if mxh >= SIDEBAR_W else None
        signals.update(t, hand)
        rising = signals.crossings(state["threshold"])
        if state["tool"] in ("ripple", "ripple_hops", "overlap"):
            for i in rising:
                fire_edge(int(i), t, color)

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

        avg = num / np.maximum(den, 1e-6)[:, None]   # averaged hue
        strength = np.clip(den, 0.0, 1.0)            # combined ripple strength
        rgb = np.clip(base + avg * strength[:, None], 0.0, 1.0)

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
        if mxy[0] >= SIDEBAR_W:
            if tool in ("ripple", "ripple_hops", "overlap"):
                e = web.nearest_edge(mxy[0] - SIDEBAR_W, mxy[1], max_dist=EDGE_PICK_DIST)
                if e is not None and e[0] in pos and e[1] in pos:
                    pygame.draw.line(screen, EDGE_HL, pos[e[0]], pos[e[1]], 3)
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

        pygame.draw.rect(screen, SIDEBAR_BG, (0, 0, SIDEBAR_W, win_h))
        pygame.draw.line(screen, DIVIDER, (SIDEBAR_W, 0), (SIDEBAR_W, win_h), 1)
        ambient_mode = AMBIENT_MODES[state["ambient_idx"]]
        rows = []
        for key, label, action in BUTTONS:
            active = False
            if action == f"tool:{state['tool']}":
                active = True
            elif action == "act:ambient":
                label = f"Ambient: {ambient_mode}"
                active = ambient_mode != "off"
            elif action == "act:cal":
                if state["cal_step"]:
                    label = f"Calibrate {state['cal_step']}/3"
                active = state["cal_step"] != 0
            rows.append((key, label, active))
        click_hint = "click a node" if state["tool"] == "bead" else "click an edge"
        ui.draw_panel(screen, font, title_font, f"DREAM-CATCHER \u2014 {click_hint}", rows,
                      mouse_pos=pygame.mouse.get_pos())
        sw = pygame.Surface((28, 16))
        sw.fill(tuple(int(v * 255) for v in color))
        screen.blit(sw, (252, 22))
        trail_label = "Trail: off" if state["trail"] <= 0.05 else f"Trail: {state['trail']:.1f}s"
        ui.draw_slider(screen, font, trail_label, state["trail"] / TRAIL_MAX, trail_origin)
        ui.draw_slider(screen, font, f"Bead colour mix: {int(state['mix'] * 100)}%",
                       (state["mix"] - MIX_MIN) / (MIX_MAX - MIX_MIN), mix_origin)
        ui.draw_slider(screen, font, f"Signal decay: {state['decay']:.1f}s half-life",
                       (state["decay"] - DECAY_MIN) / (DECAY_MAX - DECAY_MIN), decay_origin)
        oh = state["overlap_hops"]
        ui.draw_slider(screen, font, f"Overlap reach: {oh} hop" + ("s" if oh != 1 else ""),
                       (oh - 1) / (OV_MAX_HOPS - 1), reach_origin)
        ui.draw_slider(screen, font, f"Overlap emission: {int(state['overlap_falloff'] * 100)}%/hop",
                       (state["overlap_falloff"] - OVF_MIN) / (OVF_MAX - OVF_MIN), ovf_origin)
        ui.draw_slider(screen, font, f"Click threshold: {state['threshold']:.2f}",
                       (state["threshold"] - THRESH_MIN) / (THRESH_MAX - THRESH_MIN), thr_origin)
        ui.draw_slider(screen, font, f"Ambient gain: {int(state['ambient_gain'] * 100)}%",
                       (state["ambient_gain"] - AMB_GAIN_MIN) / (AMB_GAIN_MAX - AMB_GAIN_MIN),
                       amb_origin)
        ui.draw_slider(screen, font, f"Hover gain: {int(state['hover_gain'] * 100)}%",
                       (state["hover_gain"] - HOV_GAIN_MIN) / (HOV_GAIN_MAX - HOV_GAIN_MIN),
                       hov_origin)

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

        cal_line = (f"calibrate: {CAL_PROMPTS[state['cal_step']]}"
                    if state["cal_step"] else
                    f"noise<{state['noise_max']:.2f}  hover  touch>{state['threshold']:.2f}")
        ui.draw_status(
            screen, font,
            [
                cal_line,
                f"signals {sum(1 for e in engine.events if isinstance(e, DreamSignal))}"
                f"   beads {len(beads)}   LEDs {len(leds)}",
                f"serial: {serial_port}" if link else "serial: off",
            ],
            origin=status_origin,
        )

        pygame.display.flip()

        if link is not None:
            v = np.clip(out * brightness, 0, 1)
            v = np.power(v, engine.gamma)
            link.send((v * 255 + 0.5).astype(np.uint8))

        clock.tick(fps)

    if link is not None:
        link.send(np.zeros((len(leds), 3), dtype=np.uint8))
        link.close()
    pygame.quit()
