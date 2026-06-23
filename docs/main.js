(() => {
  "use strict";

  const BG = "#08090e";
  const STRAND = "#222834";
  const EDGE_HL = "#ffd25a";
  const NODE_HL = "#ffd25a";
  const EDGE_PICK_DIST = 26;
  const NODE_PICK_DIST = 28;

  const PALETTE = [
    [0.85, 0.88, 1.0],  // cool white
    [1.0, 0.95, 0.8],   // warm white
    [0.3, 0.7, 1.0],    // sky
    [1.0, 0.45, 0.2],   // ember
  ];

  const AMBIENT_MODES = ["off", "shimmer", "breathe", "twinkle", "wander", "rainbow"];

  const BEAD_TYPES = [
    { name: "fast", color: [1.0, 0.85, 0.15], speed: 2.2, funnel: false, bounce: false },
    { name: "funnel", color: [0.1, 1.0, 0.5], speed: 1.0, funnel: true, bounce: false },
    { name: "bounce", color: [1.0, 0.2, 0.6], speed: 1.0, funnel: false, bounce: true },
  ];

  const ui = {
    canvas: document.getElementById("c"),
    status: document.getElementById("status"),
    colorSwatch: document.getElementById("colorSwatch"),
    trail: document.getElementById("trail"),
    mix: document.getElementById("mix"),
    decay: document.getElementById("decay"),
    reach: document.getElementById("reach"),
    falloff: document.getElementById("falloff"),
    trailV: document.getElementById("trailV"),
    mixV: document.getElementById("mixV"),
    decayV: document.getElementById("decayV"),
    reachV: document.getElementById("reachV"),
    falloffV: document.getElementById("falloffV"),
    toolRipple: document.getElementById("toolRipple"),
    toolArea: document.getElementById("toolArea"),
    toolBead: document.getElementById("toolBead"),
    shuffle: document.getElementById("shuffle"),
    clear: document.getElementById("clear"),
    ambient: document.getElementById("ambient"),
    cycleColor: document.getElementById("cycleColor"),
  };
  const x = ui.canvas.getContext("2d");

  const state = {
    tool: "ripple",
    colorIdx: 0,
    ambientIdx: 1,
    trail: 3.0,
    mix: 0.6,
    decay: 2.5,
    areaReach: 1,
    areaFalloff: 0.5,
  };

  const rt = {
    web: null,
    nodesById: new Map(),
    leds: [],
    ledNodeIds: [],
    ledPos: [],
    ledRow: new Map(),
    strands: [],
    ringEdges: [],
    adjLedByIdx: [],
    hopDist: [],
    beads: new Map(), // nodeId -> beadTypeIndex
    events: [],
    persist: [],
    hoverEdge: null,
    hoverNode: null,
    transform: { s: 1, ox: 0, oy: 0 },
    t0: performance.now() / 1000,
    lastFrame: performance.now() / 1000,
  };

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function colorLerp(a, b, t) {
    return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
  }

  function gaussian(d, sigma) {
    if (!Number.isFinite(d) || sigma <= 1e-6) return 0;
    const z = d / sigma;
    return Math.exp(-0.5 * z * z);
  }

  function segmentDistance(px, py, ax, ay, bx, by) {
    const dx = bx - ax;
    const dy = by - ay;
    const seg2 = dx * dx + dy * dy;
    if (seg2 === 0) return Math.hypot(px - ax, py - ay);
    const t = clamp(((px - ax) * dx + (py - ay) * dy) / seg2, 0, 1);
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }

  function saturate(c, k = 2.2) {
    const mx = Math.max(c[0], c[1], c[2]);
    if (mx <= 1e-6) return [c[0], c[1], c[2]];
    return [
      clamp(Math.pow(c[0] / mx, k) * mx, 0, 1),
      clamp(Math.pow(c[1] / mx, k) * mx, 0, 1),
      clamp(Math.pow(c[2] / mx, k) * mx, 0, 1),
    ];
  }

  function hsvToRgb(h, s, v) {
    const i = Math.floor(h * 6);
    const f = h * 6 - i;
    const p = v * (1 - s);
    const q = v * (1 - f * s);
    const t = v * (1 - (1 - f) * s);
    const k = i % 6;
    if (k === 0) return [v, t, p];
    if (k === 1) return [q, v, p];
    if (k === 2) return [p, v, t];
    if (k === 3) return [p, q, v];
    if (k === 4) return [t, p, v];
    return [v, p, q];
  }

  function edgeKey(a, b) {
    return a < b ? `${a},${b}` : `${b},${a}`;
  }

  function nodeRings(nodes) {
    if (!nodes.length) return { ringOf: new Map(), numRings: 0 };
    let cx = 0;
    let cy = 0;
    for (const n of nodes) {
      cx += n.x;
      cy += n.y;
    }
    cx /= nodes.length;
    cy /= nodes.length;
    const pairs = nodes
      .map((n) => ({ d: Math.hypot(n.x - cx, n.y - cy), id: n.id }))
      .sort((a, b) => a.d - b.d);
    const distinct = [];
    for (const p of pairs) {
      if (!distinct.length || p.d - distinct[distinct.length - 1] > 1.0) distinct.push(p.d);
    }
    const gaps = [];
    for (let i = 0; i + 1 < distinct.length; i += 1) gaps.push(distinct[i + 1] - distinct[i]);
    const spacing = gaps.length ? gaps.slice().sort((a, b) => a - b)[Math.floor(gaps.length / 2)] : 1.0;
    const tol = Math.max(spacing * 0.5, 8.0);

    const levels = [];
    const ringOf = new Map();
    for (const p of pairs) {
      if (!levels.length || p.d - levels[levels.length - 1] > tol) levels.push(p.d);
      ringOf.set(p.id, levels.length - 1);
    }
    return { ringOf, numRings: levels.length };
  }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = ui.canvas.clientWidth;
    const h = ui.canvas.clientHeight;
    ui.canvas.width = Math.max(1, Math.floor(w * dpr));
    ui.canvas.height = Math.max(1, Math.floor(h * dpr));
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function updateUI() {
    ui.trailV.textContent = `${state.trail.toFixed(2)}s`;
    ui.mixV.textContent = `${Math.round(state.mix * 100)}%`;
    ui.decayV.textContent = `${state.decay.toFixed(2)}s`;
    ui.reachV.textContent = `${state.areaReach} hop${state.areaReach === 1 ? "" : "s"}`;
    ui.falloffV.textContent = `${Math.round(state.areaFalloff * 100)}%`;
    const c = PALETTE[state.colorIdx];
    ui.colorSwatch.style.background = `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;
    ui.toolRipple.classList.toggle("active", state.tool === "ripple");
    ui.toolArea.classList.toggle("active", state.tool === "area");
    ui.toolBead.classList.toggle("active", state.tool === "bead");
    ui.ambient.textContent = `A Ambient: ${AMBIENT_MODES[state.ambientIdx]}`;
  }

  function toWeb(mx, my) {
    const s = rt.transform.s || 1;
    return {
      x: (mx - rt.transform.ox) / s,
      y: (my - rt.transform.oy) / s,
    };
  }

  function nearestEdge(wx, wy) {
    const edges = rt.ringEdges.length ? rt.ringEdges : rt.strands;
    let best = null;
    let bestD = Infinity;
    for (const [a, b] of edges) {
      const na = rt.nodesById.get(a);
      const nb = rt.nodesById.get(b);
      if (!na || !nb) continue;
      const d = segmentDistance(wx, wy, na.x, na.y, nb.x, nb.y);
      if (d < bestD) {
        bestD = d;
        best = [a, b];
      }
    }
    if (bestD > EDGE_PICK_DIST) return null;
    return best;
  }

  function nearestLedNode(wx, wy) {
    let best = null;
    let bestD = Infinity;
    for (const n of rt.leds) {
      const d = Math.hypot(n.x - wx, n.y - wy);
      if (d < bestD) {
        bestD = d;
        best = n;
      }
    }
    if (bestD > NODE_PICK_DIST) return null;
    return best;
  }

  function ambientContribution(t) {
    const n = rt.ledPos.length;
    const out = new Array(n);
    for (let i = 0; i < n; i += 1) out[i] = [0, 0, 0];
    const mode = AMBIENT_MODES[state.ambientIdx];
    if (mode === "off") return out;

    let cx = 0;
    let cy = 0;
    for (const p of rt.ledPos) {
      cx += p[0];
      cy += p[1];
    }
    cx /= Math.max(rt.ledPos.length, 1);
    cy /= Math.max(rt.ledPos.length, 1);

    for (let i = 0; i < n; i += 1) {
      const p = rt.ledPos[i];
      let a = 0.08;
      let c = [0.35, 0.45, 0.7];
      if (mode === "shimmer") {
        a = 0.08 * (0.65 + 0.35 * Math.sin(t * 0.9 + i * 0.37));
        c = [0.25, 0.4, 0.75];
      } else if (mode === "breathe") {
        a = 0.09 * (0.45 + 0.55 * (0.5 + 0.5 * Math.sin(t * 0.45)));
        c = [0.35, 0.5, 0.8];
      } else if (mode === "twinkle") {
        const tw = 0.5 + 0.5 * Math.sin(t * 2.1 + i * 2.73);
        a = 0.1 * Math.pow(tw, 3.0);
        c = [0.5, 0.62, 0.9];
      } else if (mode === "wander") {
        const dx = p[0] - cx;
        const dy = p[1] - cy;
        const ang = Math.atan2(dy, dx);
        a = 0.08 * (0.4 + 0.6 * (0.5 + 0.5 * Math.sin(t * 0.9 + ang * 2.5)));
        c = [0.3, 0.55, 0.85];
      } else if (mode === "rainbow") {
        const h = (0.15 * t + i * 0.03) % 1.0;
        c = hsvToRgb(h, 0.75, 1.0);
        a = 0.08;
      }
      out[i][0] = c[0] * a;
      out[i][1] = c[1] * a;
      out[i][2] = c[2] * a;
    }
    return out;
  }

  function buildTopology() {
    rt.nodesById.clear();
    for (const n of rt.web.nodes) rt.nodesById.set(n.id, n);
    rt.strands = rt.web.strands.map((s) => [s[0], s[1]]);

    rt.leds = rt.web.nodes
      .filter((n) => n.led)
      .slice()
      .sort((a, b) => a.index - b.index);
    rt.ledNodeIds = rt.leds.map((n) => n.id);
    rt.ledPos = rt.leds.map((n) => [n.x, n.y]);
    rt.ledRow.clear();
    rt.ledNodeIds.forEach((id, i) => rt.ledRow.set(id, i));

    const { ringOf } = nodeRings(rt.web.nodes);
    const ringEdges = [];
    for (const [a, b] of rt.strands) {
      if (ringOf.get(a) === ringOf.get(b)) ringEdges.push([a, b]);
    }
    rt.ringEdges = ringEdges;

    const n = rt.ledNodeIds.length;
    rt.adjLedByIdx = new Array(n);
    for (let i = 0; i < n; i += 1) rt.adjLedByIdx[i] = [];
    for (const [a, b] of rt.strands) {
      const ia = rt.ledRow.get(a);
      const ib = rt.ledRow.get(b);
      if (ia === undefined || ib === undefined) continue;
      rt.adjLedByIdx[ia].push(ib);
      rt.adjLedByIdx[ib].push(ia);
    }

    rt.hopDist = new Array(n);
    for (let s = 0; s < n; s += 1) {
      const dist = new Array(n).fill(Infinity);
      dist[s] = 0;
      const q = [s];
      for (let qh = 0; qh < q.length; qh += 1) {
        const u = q[qh];
        const nd = dist[u] + 1;
        for (const v of rt.adjLedByIdx[u]) {
          if (nd < dist[v]) {
            dist[v] = nd;
            q.push(v);
          }
        }
      }
      rt.hopDist[s] = dist;
    }

    rt.persist = new Array(n);
    for (let i = 0; i < n; i += 1) rt.persist[i] = [0, 0, 0];
  }

  function beadAccumulate(sources, baseColor, mix, beadGain, useFx) {
    const n = rt.ledNodeIds.length;
    const dist = new Array(n).fill(Infinity);
    const colors = new Array(n);
    const tinted = new Array(n).fill(false);
    const boost = new Array(n).fill(1.0);
    const carry = new Array(n).fill(1.0);
    const parent = new Array(n).fill(-1);
    const settled = new Array(n).fill(false);
    const order = [];
    const reflections = [];

    for (let i = 0; i < n; i += 1) colors[i] = [baseColor[0], baseColor[1], baseColor[2]];

    const starts = [];
    for (const nid of sources) {
      const idx = rt.ledRow.get(nid);
      if (idx !== undefined) starts.push(idx);
    }
    if (!starts.length) return { dist, colors, boost, reflections };

    const pq = [];
    for (const s of starts) {
      dist[s] = 0;
      pq.push([0, s]);
    }
    pq.sort((a, b) => a[0] - b[0]);

    while (pq.length) {
      const cur = pq.shift();
      const d = cur[0];
      const u = cur[1];
      if (settled[u]) continue;
      settled[u] = true;
      order.push(u);

      const nid = rt.ledNodeIds[u];
      const beadTypeIdx = rt.beads.get(nid);
      const fx = beadTypeIdx !== undefined ? BEAD_TYPES[beadTypeIdx] : null;
      if (useFx && fx && fx.bounce) reflections.push({ idx: u, arrival: d });

      const outSpd = carry[u] * (useFx && fx ? fx.speed : 1.0);
      const step = 1.0 / Math.max(outSpd, 1e-3);

      let targets = rt.adjLedByIdx[u];
      if (useFx && fx && fx.funnel && targets.length > 0) {
        const p = parent[u];
        const cand = targets.filter((v) => v !== p);
        const use = cand.length ? cand : targets;
        const pick = use[Math.floor(Math.random() * use.length)];
        targets = [pick];
      }

      for (const v of targets) {
        const nd = d + step;
        if (nd < dist[v]) {
          dist[v] = nd;
          carry[v] = outSpd;
          parent[v] = u;
          pq.push([nd, v]);
        }
      }
      pq.sort((a, b) => a[0] - b[0]);
    }

    for (const i of order) {
      const p = parent[i];
      if (p !== -1) {
        colors[i] = [colors[p][0], colors[p][1], colors[p][2]];
        tinted[i] = tinted[p];
      }
      const nid = rt.ledNodeIds[i];
      const beadTypeIdx = rt.beads.get(nid);
      if (beadTypeIdx === undefined) continue;
      const fx = BEAD_TYPES[beadTypeIdx];
      const bead = fx.color;
      if (useFx && fx.funnel) {
        colors[i] = saturate(bead);
        boost[i] = beadGain * 1.7;
      } else {
        colors[i] = tinted[i] ? colorLerp(colors[i], bead, mix) : [bead[0], bead[1], bead[2]];
        boost[i] = beadGain;
      }
      tinted[i] = true;
    }

    return { dist, colors, boost, reflections };
  }

  function spawnRipple(edge, t) {
    const src = [edge[0], edge[1]];
    const baseColor = PALETTE[state.colorIdx];
    const acc = beadAccumulate(src, baseColor, state.mix, 2.4, true);
    const refl = [];
    for (const r of acc.reflections) {
      refl.push({ arrival: r.arrival, dist: rt.hopDist[r.idx] });
    }
    rt.events.push({
      type: "ripple",
      start: t,
      duration: state.decay * 6 + 2,
      halfLife: state.decay,
      speed: 0.7,
      width: 0.9,
      dist: acc.dist,
      colors: acc.colors,
      boost: acc.boost,
      reflections: refl,
    });
  }

  function spawnArea(edge, t) {
    const src = [edge[0], edge[1]];
    const baseColor = PALETTE[state.colorIdx];
    const acc = beadAccumulate(src, baseColor, state.mix, 2.4, false);
    rt.events.push({
      type: "area",
      start: t,
      duration: 2.5,
      reach: state.areaReach,
      falloff: state.areaFalloff,
      dist: acc.dist,
      colors: acc.colors,
      boost: acc.boost,
    });
  }

  function shuffleBeads() {
    rt.beads.clear();
    const ids = rt.ledNodeIds.slice();
    if (!ids.length) return;
    const lo = Math.min(4, ids.length);
    const hi = Math.min(8, ids.length);
    const count = lo + Math.floor(Math.random() * (Math.max(lo, hi) - lo + 1));
    for (let i = ids.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      const tmp = ids[i];
      ids[i] = ids[j];
      ids[j] = tmp;
    }
    for (let i = 0; i < count; i += 1) {
      rt.beads.set(ids[i], Math.floor(Math.random() * BEAD_TYPES.length));
    }
  }

  function drawLed(px, py, c) {
    const r = Math.round(clamp(c[0], 0, 1) * 255);
    const g = Math.round(clamp(c[1], 0, 1) * 255);
    const b = Math.round(clamp(c[2], 0, 1) * 255);
    const bright = Math.max(c[0], c[1], c[2]);
    if (bright > 0.02) {
      const rad = 7 + 44 * bright;
      const grd = x.createRadialGradient(px, py, 0, px, py, rad);
      grd.addColorStop(0, `rgba(${r},${g},${b},${0.25 + 0.75 * bright})`);
      grd.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = grd;
      x.beginPath();
      x.arc(px, py, rad, 0, Math.PI * 2);
      x.fill();
    }
    x.fillStyle = `rgb(${Math.max(r, 28)},${Math.max(g, 28)},${Math.max(b, 32)})`;
    x.beginPath();
    x.arc(px, py, 3.2, 0, Math.PI * 2);
    x.fill();
  }

  function frame(nowMs) {
    const now = nowMs / 1000;
    const t = now - rt.t0;
    const dt = Math.max(0.0001, now - rt.lastFrame);
    rt.lastFrame = now;

    const W = rt.web.size[0];
    const H = rt.web.size[1];
    const vw = ui.canvas.clientWidth;
    const vh = ui.canvas.clientHeight;
    const s = Math.min(vw / W, vh / H);
    const ox = (vw - W * s) / 2;
    const oy = (vh - H * s) / 2;
    rt.transform = { s, ox, oy };

    x.fillStyle = BG;
    x.fillRect(0, 0, vw, vh);

    const base = ambientContribution(t);
    const n = rt.ledNodeIds.length;
    const num = new Array(n);
    const den = new Array(n).fill(0);
    for (let i = 0; i < n; i += 1) num[i] = [base[i][0], base[i][1], base[i][2]];

    const alive = [];
    for (const ev of rt.events) {
      const age = t - ev.start;
      if (age < 0) continue;
      if (ev.type === "ripple") {
        if (age > ev.duration) continue;
        const radius = ev.speed * age;
        const amp = Math.pow(0.5, age / ev.halfLife);
        for (let i = 0; i < n; i += 1) {
          let w = gaussian(ev.dist[i] - radius, ev.width * 0.5);
          for (const r of ev.reflections) {
            const rr = radius - r.arrival;
            if (rr <= 0) continue;
            w += gaussian(r.dist[i] - rr, ev.width * 0.5);
          }
          w *= ev.boost[i] * amp;
          if (w <= 1e-6) continue;
          num[i][0] += ev.colors[i][0] * w;
          num[i][1] += ev.colors[i][1] * w;
          num[i][2] += ev.colors[i][2] * w;
          den[i] += w;
        }
        alive.push(ev);
      } else if (ev.type === "area") {
        if (age > ev.duration) continue;
        const fade = Math.max(0, 1 - age / ev.duration);
        for (let i = 0; i < n; i += 1) {
          const d = ev.dist[i];
          if (!Number.isFinite(d) || d > ev.reach) continue;
          const w = Math.pow(ev.falloff, d) * fade * ev.boost[i];
          if (w <= 1e-6) continue;
          num[i][0] += ev.colors[i][0] * w;
          num[i][1] += ev.colors[i][1] * w;
          num[i][2] += ev.colors[i][2] * w;
          den[i] += w;
        }
        alive.push(ev);
      }
    }
    rt.events = alive;

    const out = new Array(n);
    for (let i = 0; i < n; i += 1) {
      const d = den[i];
      const c = d > 1e-6 ? [num[i][0] / (1 + d), num[i][1] / (1 + d), num[i][2] / (1 + d)] : num[i];
      out[i] = [clamp(c[0], 0, 1), clamp(c[1], 0, 1), clamp(c[2], 0, 1)];
    }

    if (state.trail > 0.05) {
      const halfLife = state.trail / 3.32;
      const decay = Math.pow(0.5, dt / Math.max(halfLife, 1e-3));
      for (let i = 0; i < n; i += 1) {
        const p = rt.persist[i];
        p[0] = Math.max(out[i][0], p[0] * decay);
        p[1] = Math.max(out[i][1], p[1] * decay);
        p[2] = Math.max(out[i][2], p[2] * decay);
      }
    } else {
      for (let i = 0; i < n; i += 1) {
        rt.persist[i][0] = out[i][0];
        rt.persist[i][1] = out[i][1];
        rt.persist[i][2] = out[i][2];
      }
    }

    for (const [a, b] of rt.strands) {
      const na = rt.nodesById.get(a);
      const nb = rt.nodesById.get(b);
      if (!na || !nb) continue;
      x.strokeStyle = STRAND;
      x.lineWidth = 1;
      x.beginPath();
      x.moveTo(ox + na.x * s, oy + na.y * s);
      x.lineTo(ox + nb.x * s, oy + nb.y * s);
      x.stroke();
    }

    if (rt.hoverEdge && state.tool !== "bead") {
      const na = rt.nodesById.get(rt.hoverEdge[0]);
      const nb = rt.nodesById.get(rt.hoverEdge[1]);
      if (na && nb) {
        x.strokeStyle = EDGE_HL;
        x.lineWidth = 3;
        x.beginPath();
        x.moveTo(ox + na.x * s, oy + na.y * s);
        x.lineTo(ox + nb.x * s, oy + nb.y * s);
        x.stroke();
      }
    }
    if (rt.hoverNode && state.tool === "bead") {
      x.strokeStyle = NODE_HL;
      x.lineWidth = 2;
      x.beginPath();
      x.arc(ox + rt.hoverNode.x * s, oy + rt.hoverNode.y * s, 10 * s * 0.25 + 7, 0, Math.PI * 2);
      x.stroke();
    }

    for (let i = 0; i < n; i += 1) {
      const p = rt.ledPos[i];
      drawLed(ox + p[0] * s, oy + p[1] * s, rt.persist[i]);
    }

    for (const [nid, kind] of rt.beads.entries()) {
      const n0 = rt.nodesById.get(nid);
      if (!n0) continue;
      const c = BEAD_TYPES[kind].color;
      x.strokeStyle = `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;
      x.lineWidth = 2;
      x.beginPath();
      x.arc(ox + n0.x * s, oy + n0.y * s, 7, 0, Math.PI * 2);
      x.stroke();
    }

    const color = PALETTE[state.colorIdx];
    ui.status.textContent =
      `tool: ${state.tool}\n` +
      `ambient: ${AMBIENT_MODES[state.ambientIdx]}\n` +
      `events: ${rt.events.length}   beads: ${rt.beads.size}   leds: ${n}\n` +
      `color: rgb(${Math.round(color[0] * 255)}, ${Math.round(color[1] * 255)}, ${Math.round(color[2] * 255)})`;

    requestAnimationFrame(frame);
  }

  function onCanvasMove(ev) {
    const rect = ui.canvas.getBoundingClientRect();
    const wx = ev.clientX - rect.left;
    const wy = ev.clientY - rect.top;
    const p = toWeb(wx, wy);
    if (state.tool === "bead") {
      rt.hoverNode = nearestLedNode(p.x, p.y);
      rt.hoverEdge = null;
    } else {
      rt.hoverEdge = nearestEdge(p.x, p.y);
      rt.hoverNode = null;
    }
  }

  function onCanvasDown(ev) {
    if (ev.button !== 0) return;
    const rect = ui.canvas.getBoundingClientRect();
    const wx = ev.clientX - rect.left;
    const wy = ev.clientY - rect.top;
    const p = toWeb(wx, wy);
    const t = performance.now() / 1000 - rt.t0;

    if (state.tool === "bead") {
      const n = nearestLedNode(p.x, p.y);
      if (!n) return;
      const cur = rt.beads.has(n.id) ? rt.beads.get(n.id) : -1;
      const nxt = cur + 1;
      if (nxt >= BEAD_TYPES.length) rt.beads.delete(n.id);
      else rt.beads.set(n.id, nxt);
      return;
    }

    const edge = nearestEdge(p.x, p.y);
    if (!edge) return;
    if (state.tool === "ripple") spawnRipple(edge, t);
    if (state.tool === "area") spawnArea(edge, t);
  }

  function wireEvents() {
    ui.canvas.addEventListener("mousemove", onCanvasMove);
    ui.canvas.addEventListener("mousedown", onCanvasDown);
    ui.canvas.addEventListener("mouseleave", () => {
      rt.hoverEdge = null;
      rt.hoverNode = null;
    });

    ui.toolRipple.addEventListener("click", () => { state.tool = "ripple"; updateUI(); });
    ui.toolArea.addEventListener("click", () => { state.tool = "area"; updateUI(); });
    ui.toolBead.addEventListener("click", () => { state.tool = "bead"; updateUI(); });
    ui.shuffle.addEventListener("click", () => { shuffleBeads(); updateUI(); });
    ui.clear.addEventListener("click", () => { rt.events = []; updateUI(); });
    ui.ambient.addEventListener("click", () => {
      state.ambientIdx = (state.ambientIdx + 1) % AMBIENT_MODES.length;
      updateUI();
    });
    ui.cycleColor.addEventListener("click", () => {
      state.colorIdx = (state.colorIdx + 1) % PALETTE.length;
      updateUI();
    });

    ui.trail.addEventListener("input", () => { state.trail = Number(ui.trail.value); updateUI(); });
    ui.mix.addEventListener("input", () => { state.mix = Number(ui.mix.value); updateUI(); });
    ui.decay.addEventListener("input", () => { state.decay = Number(ui.decay.value); updateUI(); });
    ui.reach.addEventListener("input", () => { state.areaReach = Number(ui.reach.value); updateUI(); });
    ui.falloff.addEventListener("input", () => { state.areaFalloff = Number(ui.falloff.value); updateUI(); });

    window.addEventListener("resize", resize);
    window.addEventListener("keydown", (ev) => {
      if (ev.key === "1") state.tool = "ripple";
      else if (ev.key === "2") state.tool = "area";
      else if (ev.key === "3") state.tool = "bead";
      else if (ev.key === "r" || ev.key === "R") shuffleBeads();
      else if (ev.key === "x" || ev.key === "X") rt.events = [];
      else if (ev.key === "a" || ev.key === "A") state.ambientIdx = (state.ambientIdx + 1) % AMBIENT_MODES.length;
      else if (ev.code === "Space") state.colorIdx = (state.colorIdx + 1) % PALETTE.length;
      else return;
      ev.preventDefault();
      updateUI();
    });
  }

  async function init() {
    try {
      const res = await fetch("./web.json", { cache: "no-store" });
      if (!res.ok) throw new Error(`Failed to load web.json (${res.status})`);
      rt.web = await res.json();
      buildTopology();
      shuffleBeads();
      wireEvents();
      resize();
      updateUI();
      requestAnimationFrame(frame);
    } catch (err) {
      ui.status.textContent = `Failed to initialize web dream UI:\n${err && err.message ? err.message : String(err)}`;
    }
  }

  init();
})();
