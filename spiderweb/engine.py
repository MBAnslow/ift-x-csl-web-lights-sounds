"""Frame engine: composite active events into a per-LED RGB buffer."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from spiderweb.events import Event
from spiderweb.web import Web


@dataclass
class Context:
    positions: np.ndarray  # (N, 2) LED positions in chain order
    led_node_ids: list[int]  # node id per LED, aligned with positions
    web: Web
    _led_adj: list | None = None

    def zeros(self) -> np.ndarray:
        return np.zeros((len(self.positions), 3))

    def led_adjacency(self) -> list[list[int]]:
        """For each LED (by chain index), its neighbouring LED indices.

        Uses the web's LED topology, which contracts disabled nodes so the
        neighbour is the nearest *enabled* node along the strands."""
        if self._led_adj is None:
            idx_of = {nid: i for i, nid in enumerate(self.led_node_ids)}
            topo = self.web.led_topology()
            adj: list[list[int]] = [[] for _ in self.led_node_ids]
            for nid, nbrs in topo.items():
                if nid in idx_of:
                    adj[idx_of[nid]] = [idx_of[m] for m in nbrs if m in idx_of]
            self._led_adj = adj
        return self._led_adj

    def led_hop_distances(self, seed_node_ids) -> np.ndarray:
        """Hop distance (per LED chain index) over the *enabled-only* topology.

        Steps count active-node to active-node moves; deactivated nodes are
        skipped entirely, so two lights the same number of active steps away
        share a hop distance regardless of any dead nodes between them.
        """
        adj = self.led_adjacency()
        idx_of = {nid: i for i, nid in enumerate(self.led_node_ids)}
        dist = np.full(len(self.led_node_ids), np.inf)
        dq: deque[int] = deque()
        for s in seed_node_ids:
            i = idx_of.get(s)
            if i is not None and not np.isfinite(dist[i]):
                dist[i] = 0.0
                dq.append(i)
        while dq:
            u = dq.popleft()
            for v in adj[u]:
                if not np.isfinite(dist[v]):
                    dist[v] = dist[u] + 1.0
                    dq.append(v)
        return dist


class Engine:
    def __init__(self, web: Web, blend: str = "add", brightness: float = 1.0, gamma: float = 2.2):
        self.web = web
        self.events: list[Event] = []
        self.blend = blend  # "add" or "max"
        self.brightness = brightness
        self.gamma = gamma
        self.rebuild()

    def rebuild(self) -> None:
        """Recompute LED geometry from the web (call after editing the web)."""
        leds = self.web.leds()
        if leds:
            self.positions = np.asarray([[n.x, n.y] for n in leds], dtype=float)
        else:
            self.positions = np.zeros((0, 2))
        self.led_node_ids = [n.id for n in leds]
        self.ctx = Context(self.positions, self.led_node_ids, self.web)

    def add(self, event: Event) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events = [e for e in self.events if e.duration is None and e.start == 0.0]

    def clear_all(self) -> None:
        self.events = []

    def update(self, t: float) -> np.ndarray:
        """Return float RGB (N,3) in 0..1 for time t."""
        self.events = [e for e in self.events if e.alive(t)]
        acc = np.zeros((len(self.positions), 3))
        for e in self.events:
            c = e.contribution(self.ctx, t)
            if not isinstance(c, np.ndarray) or c.shape != acc.shape:
                continue
            if self.blend == "max":
                acc = np.maximum(acc, c)
            else:
                acc = acc + c
        return np.clip(acc, 0.0, 1.0)

    def frame_bytes(self, t: float) -> np.ndarray:
        """Return gamma-corrected uint8 RGB (N,3) ready for the wire."""
        v = np.clip(self.update(t) * self.brightness, 0.0, 1.0)
        v = np.power(v, self.gamma)
        return (v * 255.0 + 0.5).astype(np.uint8)
