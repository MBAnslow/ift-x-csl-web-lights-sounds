"""Web model: nodes (some with LEDs), strands between them, and graph helpers.

The web lives in a 2D coordinate space measured in pixels. Some nodes carry an
LED; those LEDs have a `index` giving their position in the physical SK6805
chain (the order in which colour bytes are streamed to the ESP).
"""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def _norm_edge(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _segment_distance(px: float, py: float, ax: float, ay: float,
                      bx: float, by: float) -> float:
    """Shortest distance from point (px,py) to the segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


@dataclass
class Node:
    id: int
    x: float
    y: float
    led: bool = True
    index: int = -1  # position in the SK6805 chain, or -1 if this node has no LED


@dataclass
class Web:
    size: tuple[int, int] = (1000, 700)
    nodes: list[Node] = field(default_factory=list)
    strands: list[tuple[int, int]] = field(default_factory=list)
    background: Optional[str] = None

    # ---- node / strand management -------------------------------------
    def add_node(self, x: float, y: float, led: bool = True) -> Node:
        nid = max((n.id for n in self.nodes), default=-1) + 1
        node = Node(id=nid, x=float(x), y=float(y), led=led)
        self.nodes.append(node)
        self.reindex()
        return node

    def remove_node(self, nid: int) -> None:
        self.nodes = [n for n in self.nodes if n.id != nid]
        self.strands = [s for s in self.strands if nid not in s]
        self.reindex()

    def add_strand(self, a: int, b: int) -> None:
        if a == b:
            return
        key = _norm_edge(a, b)
        if key not in self.strands:
            self.strands.append(key)

    def remove_strand(self, a: int, b: int) -> None:
        key = _norm_edge(a, b)
        self.strands = [s for s in self.strands if _norm_edge(*s) != key]

    def set_led(self, nid: int, led: bool) -> Optional[Node]:
        n = self.node_by_id(nid)
        if n is not None:
            n.led = led
            self.reindex()
        return n

    def toggle_led(self, nid: int) -> Optional[Node]:
        n = self.node_by_id(nid)
        if n is not None:
            return self.set_led(nid, not n.led)
        return None

    def node_by_id(self, nid: int) -> Optional[Node]:
        for n in self.nodes:
            if n.id == nid:
                return n
        return None

    def reindex(self) -> None:
        """Assign chain indices to LED nodes in node-id (creation) order."""
        i = 0
        for n in sorted(self.nodes, key=lambda n: n.id):
            if n.led:
                n.index = i
                i += 1
            else:
                n.index = -1

    def leds(self) -> list[Node]:
        return sorted((n for n in self.nodes if n.led), key=lambda n: n.index)

    @property
    def num_leds(self) -> int:
        return sum(1 for n in self.nodes if n.led)

    def nearest_node(
        self, x: float, y: float, max_dist: Optional[float] = None, led_only: bool = False
    ) -> Optional[Node]:
        best: Optional[Node] = None
        best_d = math.inf
        for n in self.nodes:
            if led_only and not n.led:
                continue
            d = math.hypot(n.x - x, n.y - y)
            if d < best_d:
                best_d, best = d, n
        if max_dist is not None and best_d > max_dist:
            return None
        return best

    def nearest_edge(
        self, x: float, y: float, max_dist: Optional[float] = None
    ) -> Optional[tuple[int, int]]:
        """Return the strand (a, b) whose segment is closest to (x, y)."""
        pos = {n.id: (n.x, n.y) for n in self.nodes}
        best: Optional[tuple[int, int]] = None
        best_d = math.inf
        for a, b in self.strands:
            if a in pos and b in pos:
                d = _segment_distance(x, y, pos[a][0], pos[a][1], pos[b][0], pos[b][1])
                if d < best_d:
                    best_d, best = d, (a, b)
        if max_dist is not None and best_d > max_dist:
            return None
        return best

    # ---- graph helpers ------------------------------------------------
    def adjacency(self) -> dict[int, list[tuple[int, float]]]:
        adj: dict[int, list[tuple[int, float]]] = {n.id: [] for n in self.nodes}
        pos = {n.id: (n.x, n.y) for n in self.nodes}
        for a, b in self.strands:
            if a in pos and b in pos:
                w = math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])
                adj[a].append((b, w))
                adj[b].append((a, w))
        return adj

    def _straightest_next(self, adj, pos, prev: int, cur: int) -> Optional[int]:
        """From `cur` (arrived from `prev`), the outgoing strand whose direction
        best continues the incoming direction (smallest turn)."""
        indx = pos[cur][0] - pos[prev][0]
        indy = pos[cur][1] - pos[prev][1]
        inlen = math.hypot(indx, indy) or 1.0
        best, best_dot = None, -2.0
        for nb, _ in adj.get(cur, []):
            if nb == prev:
                continue
            dx, dy = pos[nb][0] - pos[cur][0], pos[nb][1] - pos[cur][1]
            dlen = math.hypot(dx, dy) or 1.0
            dot = (indx * dx + indy * dy) / (inlen * dlen)
            if dot > best_dot:
                best_dot, best = dot, nb
        return best

    def _walk_to_enabled(self, adj, pos, led_ids, src: int, first: int) -> Optional[int]:
        """Walk from `src` toward neighbour `first`, skipping disabled nodes in
        the straightest direction, and return the first enabled node reached."""
        prev, cur, steps = src, first, 0
        limit = len(self.nodes) + 1
        while steps < limit:
            if cur in led_ids:
                return cur if cur != src else None
            nxt = self._straightest_next(adj, pos, prev, cur)
            if nxt is None:
                return None
            prev, cur, steps = cur, nxt, steps + 1
        return None

    def led_topology(self) -> dict[int, set[int]]:
        """Neighbour relation among LED (enabled) nodes, ignoring disabled ones.

        From each enabled node we walk outward along every strand direction.
        When we land on a disabled (non-LED) node we keep going in the
        *straightest* continuation (the outgoing strand best aligned with the
        direction we arrived from) until we reach an enabled node -- that node
        is a distance-1 neighbour. So disabled lights are skipped over without
        the wavefront turning sideways, and the nearest enabled light along a
        strand is always one hop away, both across (rings) and down (spokes).
        """
        adj = self.adjacency()
        pos = {n.id: (n.x, n.y) for n in self.nodes}
        led_ids = {n.id for n in self.nodes if n.led}
        result: dict[int, set[int]] = {nid: set() for nid in led_ids}
        for src in led_ids:
            for first, _ in adj.get(src, []):
                reached = self._walk_to_enabled(adj, pos, led_ids, src, first)
                if reached is not None:
                    result[src].add(reached)
        return result

    def edge_seed_leds(self, a: int, b: int) -> set[int]:
        """The enabled LEDs that an interaction on strand (a, b) seeds directly.

        An enabled endpoint seeds itself; a disabled endpoint seeds the nearest
        enabled light along each strand leaving it (skipping further disabled
        nodes). These are the distance-0 lights from which an edge effect grows.
        """
        adj = self.adjacency()
        pos = {n.id: (n.x, n.y) for n in self.nodes}
        led_ids = {n.id for n in self.nodes if n.led}
        seeds: set[int] = set()
        for endpoint in (a, b):
            if endpoint not in pos:
                continue
            if endpoint in led_ids:
                seeds.add(endpoint)
            else:
                for first, _ in adj.get(endpoint, []):
                    reached = self._walk_to_enabled(adj, pos, led_ids, endpoint, first)
                    if reached is not None:
                        seeds.add(reached)
        return seeds

    def graph_distances(self, sources, hops: bool = False) -> dict[int, float]:
        """Shortest-path distance (along strands) from one or more sources.

        `sources` may be a single node id or a list of node ids; the result is
        the distance to the nearest source (so an edge can seed from both of
        its endpoints at once).

        hops=False uses physical strand lengths; hops=True counts edges (each
        strand = 1), giving uniform node-to-node steps regardless of geometry.
        """
        if isinstance(sources, int):
            sources = [sources]
        adj = self.adjacency()
        if hops:
            adj = {u: [(v, 1.0) for v, _ in nbrs] for u, nbrs in adj.items()}
        dist = {nid: math.inf for nid in adj}
        pq: list[tuple[float, int]] = []
        for s in sources:
            if s in dist:
                dist[s] = 0.0
                pq.append((0.0, s))
        heapq.heapify(pq)
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, w in adj[u]:
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    # ---- persistence --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "size": list(self.size),
            "background": self.background,
            "nodes": [asdict(n) for n in self.nodes],
            "strands": [list(s) for s in self.strands],
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Web":
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text())
        web = cls(
            size=tuple(data.get("size", (1000, 700))),
            background=data.get("background"),
        )
        web.nodes = [Node(**n) for n in data.get("nodes", [])]
        web.strands = [_norm_edge(*s) for s in data.get("strands", [])]
        web.reindex()
        return web
