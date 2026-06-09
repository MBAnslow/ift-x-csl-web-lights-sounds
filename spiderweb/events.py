"""Spatial events.

An event computes a per-LED additive colour contribution given a `Context`
(LED positions in 2D space, plus the web graph) and the current time `t` in
seconds. The engine sums/maxes these contributions every frame.

Contributions are float RGB in roughly the 0..1 range; the engine clips and
gamma-corrects before output.
"""
from __future__ import annotations

import numpy as np


class Event:
    """Base class. Subclasses implement `contribution`."""

    def __init__(self, color=(1.0, 1.0, 1.0), start: float = 0.0, duration: float | None = None):
        self.color = np.asarray(color, dtype=float)
        self.start = float(start)
        self.duration = duration  # None => lives until removed

    def alive(self, t: float) -> bool:
        if self.duration is None:
            return True
        return (t - self.start) <= self.duration

    def contribution(self, ctx, t: float) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError


def _gaussian(x: np.ndarray, sigma: float) -> np.ndarray:
    sigma = max(sigma, 1e-6)
    return np.exp(-0.5 * (x / sigma) ** 2)


def _seed_neighbourhood(ctx, seed_ids, hops: int, falloff: float) -> np.ndarray:
    """Per-LED intensity for the k-hop neighbourhood grown from seed LEDs.

    Hops are counted on the *enabled-only* topology, where disabled lights are
    ignored: the nearest enabled light along a strand is one hop away no matter
    how many disabled nodes lie between them. Multiple seeds (e.g. the two ends
    of an edge) all start at hop 0; each LED takes its smallest hop distance.
    """
    adj = ctx.led_adjacency()  # index-based, enabled-only neighbours
    idx_of = {nid: i for i, nid in enumerate(ctx.led_node_ids)}
    out = np.zeros(len(ctx.positions))
    starts = [idx_of[s] for s in seed_ids if s in idx_of]
    if not starts:
        return out
    dist = {s: 0 for s in starts}
    frontier = list(dict.fromkeys(starts))
    while frontier:
        nxt = []
        for u in frontier:
            for v in adj[u]:
                if v not in dist:
                    dist[v] = dist[u] + 1
                    if dist[v] < hops:
                        nxt.append(v)
        frontier = nxt
    for i, h in dist.items():
        if h <= hops:
            out[i] = falloff ** h
    return out


class Ripple(Event):
    """An expanding ring of light from a point in space."""

    def __init__(self, origin, color=(0.2, 0.6, 1.0), speed=180.0, width=40.0,
                 start=0.0, duration=3.0, fade=True):
        super().__init__(color, start, duration)
        self.origin = np.asarray(origin, dtype=float)
        self.speed = speed
        self.width = width
        self.fade = fade

    def contribution(self, ctx, t):
        dt = t - self.start
        if dt < 0:
            return ctx.zeros()
        radius = self.speed * dt
        dist = np.linalg.norm(ctx.positions - self.origin, axis=1)
        intensity = _gaussian(dist - radius, self.width * 0.5)
        if self.fade and self.duration:
            intensity *= max(0.0, 1.0 - dt / self.duration)
        return intensity[:, None] * self.color[None, :]


class Charge(Event):
    """An edge that brightens the longer it is held, then is released.

    While held it glows (with a pulse) along the strand's endpoints and their
    immediate neighbours, ramping up to full over `charge_time`. The simulator
    reads `level(t)` on release to spawn a brighter Propagate from the edge.
    """

    def __init__(self, edge, color=(1.0, 0.8, 0.2), start=0.0,
                 charge_time=2.0, spread_hops=1, spread_falloff=0.5):
        super().__init__(color, start, None)
        self.edge = tuple(edge)  # (a, b) node ids of the strand
        self.charge_time = charge_time
        self.spread_hops = spread_hops
        self.spread_falloff = spread_falloff
        self._nb = None

    def level(self, t):
        return min(1.0, max(0.0, (t - self.start) / self.charge_time))

    def contribution(self, ctx, t):
        if self._nb is None:
            seeds = ctx.web.edge_seed_leds(*self.edge)
            self._nb = _seed_neighbourhood(ctx, seeds, self.spread_hops, self.spread_falloff)
        lvl = self.level(t)
        pulse = 0.65 + 0.35 * np.sin((t - self.start) * 9.0)
        return (lvl * pulse * self._nb)[:, None] * self.color[None, :]


def _point_in_polygon(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Vectorised ray-casting test. points (N,2), poly (M,2) -> bool (N,)."""
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        )
        inside ^= cond
        j = i
    return inside


class Overlap(Event):
    """A static (or timed) region that lights whatever LEDs it covers.

    shape is a dict:
      {"type": "edge", "a": id, "b": id}  -- light the strand's ends + neighbours
      {"type": "node", "source_id": id}   -- light an LED + its neighbours
      {"type": "circle", "center": (x, y), "radius": r}
      {"type": "polygon", "points": [(x, y), ...]}
    """

    def __init__(self, shape, color=(0.4, 1.0, 0.3), start=0.0, duration=None, edge=20.0,
                 spread_hops=1, spread_falloff=0.7):
        super().__init__(color, start, duration)
        self.shape = shape
        self.edge = max(edge, 1e-6)
        self.spread_hops = spread_hops
        self.spread_falloff = spread_falloff
        self._nb = None

    def contribution(self, ctx, t):
        pos = ctx.positions
        if self.shape["type"] in ("edge", "node"):
            if self._nb is None:
                if self.shape["type"] == "edge":
                    seeds = ctx.web.edge_seed_leds(self.shape["a"], self.shape["b"])
                else:
                    seeds = {self.shape["source_id"]}
                self._nb = _seed_neighbourhood(
                    ctx, seeds, self.spread_hops, self.spread_falloff)
            return self._nb[:, None] * self.color[None, :]
        elif self.shape["type"] == "circle":
            c = np.asarray(self.shape["center"], dtype=float)
            r = float(self.shape["radius"])
            dist = np.linalg.norm(pos - c, axis=1)
            intensity = np.clip((r - dist) / self.edge, 0.0, 1.0)
        elif self.shape["type"] == "polygon":
            poly = np.asarray(self.shape["points"], dtype=float)
            intensity = _point_in_polygon(pos, poly).astype(float)
        else:
            return ctx.zeros()
        return intensity[:, None] * self.color[None, :]


class Propagate(Event):
    """A wavefront that travels along the web strands from a source edge/node.

    `sources` is a node id or a list of node ids (e.g. the two ends of an
    edge); the wave grows outward from the nearest source. Distances are
    measured along the graph (strand lengths), so the signal follows the web
    rather than cutting straight across empty space.
    """

    def __init__(self, sources, color=(1.0, 0.8, 0.2), speed=None, width=None,
                 start=0.0, duration=4.0, fade=True, metric="distance", gain=1.0,
                 dist_falloff=1.0):
        super().__init__(color, start, duration)
        self.sources = [sources] if isinstance(sources, int) else list(sources)
        self.gain = gain  # intensity multiplier (>1 blooms toward white)
        # per-hop attenuation: distant nodes peak dimmer (1.0 = no attenuation)
        self.dist_falloff = dist_falloff
        self.metric = metric  # "distance" (strand length) or "hops" (edge count)
        if metric == "hops":
            self.speed = 3.5 if speed is None else speed   # hops per second
            self.width = 0.85 if width is None else width   # hops
        else:
            self.speed = 160.0 if speed is None else speed  # px per second
            self.width = 50.0 if width is None else width    # px
        self.fade = fade
        self._dist: np.ndarray | None = None
        self._atten: np.ndarray | None = None

    def _ensure(self, ctx):
        if self._dist is None:
            if len(self.sources) >= 2:
                seeds = ctx.web.edge_seed_leds(self.sources[0], self.sources[1])
            else:
                seeds = set(self.sources)
            # enabled-only hop distance: deactivated nodes never add a step
            hops = ctx.led_hop_distances(seeds)
            if self.metric == "hops":
                self._dist = hops
            else:
                gd = ctx.web.graph_distances(self.sources, hops=False)
                self._dist = np.asarray(
                    [gd.get(nid, np.inf) for nid in ctx.led_node_ids], dtype=float
                )
            # attenuation by active-node hop count from the source(s)
            if self.dist_falloff >= 0.999:
                self._atten = np.ones(len(self._dist))
            else:
                self._atten = np.where(
                    np.isfinite(hops), self.dist_falloff ** np.where(np.isfinite(hops), hops, 0.0), 0.0
                )

    def contribution(self, ctx, t):
        dt = t - self.start
        if dt < 0:
            return ctx.zeros()
        self._ensure(ctx)
        radius = self.speed * dt
        gdist = self._dist
        intensity = _gaussian(gdist - radius, self.width * 0.5)
        intensity = np.where(np.isfinite(gdist), intensity, 0.0)
        intensity = intensity * self._atten
        if self.fade and self.duration:
            intensity *= max(0.0, 1.0 - dt / self.duration)
        return (intensity * self.gain)[:, None] * self.color[None, :]


def _hsv_to_rgb(h, s, v):
    """Vectorised HSV->RGB. h,s,v are (N,) arrays in 0..1 -> (N,3)."""
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=1)


class Ambient(Event):
    """A persistent idle animation so the web is never fully dark.

    modes: shimmer (spatial sine), breathe (uniform pulse), twinkle (per-LED
    sparkle), wander (a soft hotspot drifting around), rainbow (hue drift).
    """

    MODES = ("shimmer", "breathe", "twinkle", "wander", "rainbow")

    def __init__(self, mode="shimmer", color=(0.22, 0.34, 0.52), amplitude=0.7,
                 speed=0.25, start=0.0, seed=1234):
        super().__init__(color, start, None)
        self.mode = mode
        self.amplitude = amplitude
        self.speed = speed
        self._rng = np.random.default_rng(seed)
        self._phase = None
        self._freq = None

    def _init_random(self, n):
        if self._phase is None or len(self._phase) != n:
            self._phase = self._rng.uniform(0, 2 * np.pi, n)
            self._freq = self._rng.uniform(0.5, 1.8, n)

    def contribution(self, ctx, t):
        pos = ctx.positions
        n = len(pos)
        if n == 0:
            return ctx.zeros()
        base = self.color

        if self.mode == "shimmer":
            phase = pos[:, 0] * 0.012 * 1.3 + pos[:, 1] * 0.012 * 0.7
            wave = 0.5 + 0.5 * np.sin(2 * np.pi * (t * self.speed) + phase * 6.283)
            level = (1.0 - self.amplitude) + self.amplitude * wave
            return level[:, None] * base[None, :]

        if self.mode == "breathe":
            wave = 0.5 + 0.5 * np.sin(2 * np.pi * t * self.speed)
            level = (1.0 - self.amplitude) + self.amplitude * wave
            return np.full((n, 1), level) * base[None, :]

        if self.mode == "twinkle":
            self._init_random(n)
            wave = 0.5 + 0.5 * np.sin(t * self.speed * 6.283 * self._freq * 4 + self._phase)
            wave = wave ** 3  # sparse, star-like peaks
            spark = np.array([0.55, 0.6, 0.8])
            return 0.18 * base[None, :] + wave[:, None] * spark[None, :] * 0.85

        if self.mode == "wander":
            w, h = ctx.web.size
            cx = w * (0.5 + 0.42 * np.sin(t * self.speed * 1.7))
            cy = h * (0.5 + 0.42 * np.cos(t * self.speed * 1.3))
            d = np.linalg.norm(pos - np.array([cx, cy]), axis=1)
            glow = np.exp(-0.5 * (d / (min(w, h) * 0.18)) ** 2)
            tint = np.array([0.30, 0.55, 1.0])
            return 0.12 * base[None, :] + glow[:, None] * tint[None, :] * 0.8

        if self.mode == "rainbow":
            w, h = ctx.web.size
            hue = (pos[:, 0] / w * 0.5 + pos[:, 1] / h * 0.5 + t * self.speed * 0.6) % 1.0
            rgb = _hsv_to_rgb(hue, np.full(n, 0.85), np.full(n, 0.5))
            return rgb * (0.5 + 0.5 * self.amplitude)

        return ctx.zeros()
