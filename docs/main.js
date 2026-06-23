(() => {
  "use strict";

  const BG = "#08090e";
  const STRAND = "#222834";
  const EDGE_HL = "#ffd25a";
  const NODE_HL = "#ffd25a";
  const EDGE_PICK_DIST = 26;
  const NODE_PICK_DIST = 28;
  const SIGNAL_SCALE = 2.0;
  const CLICK_AMP = 1.5;
  const HOVER_COLOR = [0.30, 0.44, 0.70];
  const NOTE_ROOT = 220.0;
  const BEAD_GLOW_MAX = 0.8;

  const CAL_PROMPTS = [
    "",
    "background: keep clear, press C",
    "hover: hold hand near, press C",
    "touch: hold a touch, press C",
  ];

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

  const CHORDS = {
    2: [["5 (power)", [0, 7]], ["octave", [0, 12]], ["tritone", [0, 6]]],
    3: [["maj", [0, 4, 7]], ["min", [0, 3, 7]], ["sus2", [0, 2, 7]], ["sus4", [0, 5, 7]], ["dim", [0, 3, 6]], ["aug", [0, 4, 8]]],
    4: [["maj7", [0, 4, 7, 11]], ["7", [0, 4, 7, 10]], ["min7", [0, 3, 7, 10]], ["6", [0, 4, 7, 9]], ["m7b5", [0, 3, 6, 10]], ["dim7", [0, 3, 6, 9]]],
    5: [["maj9", [0, 4, 7, 11, 14]], ["9", [0, 4, 7, 10, 14]], ["min9", [0, 3, 7, 10, 14]], ["6/9", [0, 4, 7, 9, 14]], ["m9", [0, 3, 7, 10, 14]]],
    6: [["maj11", [0, 4, 7, 11, 14, 17]], ["11", [0, 4, 7, 10, 14, 17]], ["min11", [0, 3, 7, 10, 14, 17]]],
    7: [["maj13", [0, 4, 7, 11, 14, 17, 21]], ["13", [0, 4, 7, 10, 14, 17, 21]], ["min13", [0, 3, 7, 10, 14, 17, 21]]],
  };
  const MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11];

  class SoundscapeWeb {
    constructor() {
      this.AudioCtx = window.AudioContext || window.webkitAudioContext || null;
      this.supported = !!this.AudioCtx;
      this.ctx = null;
      this.master = null;
      this.droneBus = null;
      this.chimeBus = null;
      this.enabled = false;
      this.volume = 0.7;
      this.droneGain = 1.0;
      this.chimeGain = 0.35;
      this.chordLevel = 0.0;
      this.droneVoices = [];
      this.chordKey = "";
    }

    _ensure() {
      if (!this.supported || this.ctx) return;
      this.ctx = new this.AudioCtx();
      this.master = this.ctx.createGain();
      this.droneBus = this.ctx.createGain();
      this.chimeBus = this.ctx.createGain();
      this.master.gain.value = 0.0;
      this.droneBus.gain.value = this.droneGain;
      this.chimeBus.gain.value = this.chimeGain;
      this.droneBus.connect(this.master);
      this.chimeBus.connect(this.master);
      this.master.connect(this.ctx.destination);
    }

    _setGain(g, value, tc = 0.06) {
      if (!this.ctx || !g) return;
      const now = this.ctx.currentTime;
      g.gain.setTargetAtTime(value, now, tc);
    }

    touch() {
      if (!this.supported) return;
      this._ensure();
      if (this.ctx && this.ctx.state === "suspended") {
        this.ctx.resume().catch(() => {});
      }
    }

    setEnabled(on) {
      this.enabled = !!on;
      if (this.enabled) this.touch();
      this._setGain(this.master, this.enabled ? this.volume : 0.0, 0.08);
    }

    setVolume(v) {
      this.volume = clamp(v, 0, 1);
      this._setGain(this.master, this.enabled ? this.volume : 0.0, 0.08);
    }

    setDroneGain(v) {
      this.droneGain = clamp(v, 0, 1.6);
      this._setGain(this.droneBus, this.droneGain, 0.08);
      this._updateDroneVoices();
    }

    setChimeGain(v) {
      this.chimeGain = clamp(v, 0, 1.6);
      this._setGain(this.chimeBus, this.chimeGain, 0.08);
    }

    setChordLevel(v) {
      this.chordLevel = clamp(v, 0, 1);
      this._updateDroneVoices();
    }

    _updateDroneVoices() {
      if (!this.ctx) return;
      const scale = this.chordLevel;
      for (const v of this.droneVoices) {
        this._setGain(v.gain, v.base * scale, 0.12);
      }
    }

    _makePanner(pan01) {
      const p = clamp(pan01, 0, 1) * 2 - 1;
      if (this.ctx.createStereoPanner) {
        const pn = this.ctx.createStereoPanner();
        pn.pan.value = p;
        return pn;
      }
      return this.ctx.createGain();
    }

    setChord(freqs) {
      if (!this.supported) return;
      this._ensure();
      const key = freqs.map((f) => f.toFixed(3)).join(",");
      if (key === this.chordKey) return;
      this.chordKey = key;

      for (const v of this.droneVoices) {
        try { v.osc1.stop(); } catch (_) {}
        try { v.osc2.stop(); } catch (_) {}
      }
      this.droneVoices = [];
      if (!freqs.length) return;

      const spread = Math.max(freqs.length - 1, 1);
      const base = 0.12 / Math.sqrt(freqs.length);
      for (let i = 0; i < freqs.length; i += 1) {
        const f = freqs[i];
        const pan = i / spread;
        const osc1 = this.ctx.createOscillator();
        const osc2 = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        const mix = this.ctx.createGain();
        const panNode = this._makePanner(pan);

        osc1.type = "sine";
        osc2.type = "triangle";
        osc1.frequency.value = f;
        osc2.frequency.value = f * 0.5;
        osc2.detune.value = 3;
        mix.gain.value = 0.5;
        gain.gain.value = 0.0;

        osc1.connect(mix);
        osc2.connect(mix);
        mix.connect(gain);
        gain.connect(panNode);
        panNode.connect(this.droneBus);

        osc1.start();
        osc2.start();
        this.droneVoices.push({ osc1, osc2, gain, base });
      }
      this._updateDroneVoices();
    }

    triggerNote(freq, velocity = 1.0, pan01 = 0.5) {
      if (!this.supported || !this.enabled) return;
      this.touch();
      if (!this.ctx) return;
      const now = this.ctx.currentTime;
      const hit = clamp(velocity, 0, 1);
      const panNode = this._makePanner(pan01);
      const hp = this.ctx.createBiquadFilter();
      hp.type = "highpass";
      hp.frequency.value = 180;
      hp.Q.value = 0.6;
      panNode.connect(hp);
      hp.connect(this.chimeBus);

      // Inharmonic partials + staggered decays for a bell-like edge chime.
      const partials = [
        { mul: 1.00, amp: 0.26, decay: 2.3 },
        { mul: 2.71, amp: 0.18, decay: 1.7 },
        { mul: 4.07, amp: 0.10, decay: 1.15 },
      ];

      for (const p of partials) {
        const osc = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq * p.mul;
        osc.detune.value = (Math.random() * 2 - 1) * 4;
        const peak = p.amp * (0.45 + 0.55 * hit);
        g.gain.setValueAtTime(0.0001, now);
        g.gain.exponentialRampToValueAtTime(Math.max(peak, 0.0002), now + 0.006);
        g.gain.exponentialRampToValueAtTime(0.0001, now + p.decay);
        osc.connect(g);
        g.connect(panNode);
        osc.start(now);
        osc.stop(now + p.decay + 0.05);
      }
    }
  }

  class EdgeSignals {
    constructor(edges, nodesById) {
      this.edges = edges;
      this.mid = edges.map(([a, b]) => {
        const na = nodesById.get(a);
        const nb = nodesById.get(b);
        return [0.5 * (na.x + nb.x), 0.5 * (na.y + nb.y)];
      });
      const n = edges.length;
      const rng = seededRng(11);
      this.phase = new Array(n);
      this.freq = new Array(n);
      this.bias = new Array(n);
      this.press = new Array(n).fill(0);
      this.hover = new Array(n).fill(0);
      this.signal = new Array(n).fill(0);
      this.prevAbove = new Array(n).fill(false);
      for (let i = 0; i < n; i += 1) {
        this.phase[i] = rng() * Math.PI * 2;
        this.freq[i] = 0.4 + rng() * 1.1;
        this.bias[i] = 0.55 + rng() * 0.45;
      }
      this.noiseAmp = 0.10;
      this.hoverAmp = 0.55;
      this.hoverSigma = 170.0;
      this.hoverGlobal = 0.10;
      this.pressDecayPerFrame = 0.80;
    }

    update(t, hand, dt) {
      const decay = Math.pow(this.pressDecayPerFrame, dt * 60);
      for (let i = 0; i < this.edges.length; i += 1) {
        const noise = this.noiseAmp * this.bias[i] *
          (0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 0.3 * this.freq[i] + this.phase[i]));
        let hover = 0.0;
        if (hand) {
          const dx = this.mid[i][0] - hand[0];
          const dy = this.mid[i][1] - hand[1];
          const d = Math.hypot(dx, dy);
          hover = this.hoverAmp * (Math.exp(-0.5 * (d / this.hoverSigma) ** 2) + this.hoverGlobal);
        }
        this.hover[i] = hover;
        this.press[i] *= decay;
        this.signal[i] = noise + hover + this.press[i];
      }
    }

    inject(edgeIdx, amp) {
      if (edgeIdx < 0 || edgeIdx >= this.press.length) return;
      this.press[edgeIdx] = Math.max(this.press[edgeIdx], amp);
    }

    crossings(threshold, hysteresis = 0.8) {
      const hi = threshold;
      const lo = threshold * hysteresis;
      const rising = [];
      for (let i = 0; i < this.signal.length; i += 1) {
        let above = this.prevAbove[i];
        if (this.signal[i] >= hi) above = true;
        else if (this.signal[i] < lo) above = false;
        if (above && !this.prevAbove[i]) rising.push(i);
        this.prevAbove[i] = above;
      }
      return rising;
    }

    maxSignal() {
      if (!this.signal.length) return 0;
      let m = this.signal[0];
      for (let i = 1; i < this.signal.length; i += 1) {
        if (this.signal[i] > m) m = this.signal[i];
      }
      return m;
    }
  }

  const ui = {
    canvas: document.getElementById("c"),
    toolRipple: document.getElementById("toolRipple"),
    toolArea: document.getElementById("toolArea"),
    toolBead: document.getElementById("toolBead"),
    shuffle: document.getElementById("shuffle"),
    clear: document.getElementById("clear"),
    ambient: document.getElementById("ambient"),
    cycleColor: document.getElementById("cycleColor"),
    soundToggle: document.getElementById("soundToggle"),
    calibrate: document.getElementById("calibrate"),
    chordSelect: document.getElementById("chordSelect"),

    trail: document.getElementById("trail"),
    decay: document.getElementById("decay"),
    reach: document.getElementById("reach"),
    falloff: document.getElementById("falloff"),
    hoverGain: document.getElementById("hoverGain"),
    ambientGain: document.getElementById("ambientGain"),
    mix: document.getElementById("mix"),
    volume: document.getElementById("volume"),
    drone: document.getElementById("drone"),
    chime: document.getElementById("chime"),
    beadLevel: document.getElementById("beadLevel"),

    colorSwatch: document.getElementById("colorSwatch"),
    trailV: document.getElementById("trailV"),
    decayV: document.getElementById("decayV"),
    reachV: document.getElementById("reachV"),
    falloffV: document.getElementById("falloffV"),
    hoverV: document.getElementById("hoverV"),
    ambientV: document.getElementById("ambientV"),
    mixV: document.getElementById("mixV"),
    volV: document.getElementById("volV"),
    droneV: document.getElementById("droneV"),
    chimeV: document.getElementById("chimeV"),
    beadV: document.getElementById("beadV"),

    meterTrack: document.getElementById("meterTrack"),
    meterFill: document.getElementById("meterFill"),
    noiseHandle: document.getElementById("noiseHandle"),
    thresholdHandle: document.getElementById("thresholdHandle"),
    signalInfo: document.getElementById("signalInfo"),
    leftStatus: document.getElementById("leftStatus"),
    rightStatus: document.getElementById("rightStatus"),
  };
  const x = ui.canvas.getContext("2d");

  const state = {
    tool: "ripple_hops",
    colorIdx: 0,
    ambientIdx: 1,
    trail: 3.0,
    mix: 0.6,
    decay: 2.5,
    overlapHops: 1,
    overlapFalloff: 0.5,
    hoverGain: 0.7,
    ambientGain: 0.4,
    threshold: 0.85,
    noiseMax: 0.30,
    calStep: 0,
    cal: { bg: 0, hover: 0, click: 0 },
    dragTarget: null,
    soundOn: false,
    volume: 0.7,
    droneGain: 1.0,
    chimeGain: 0.35,
    beadLevel: 0.08,
    chordIdx: 0,
  };

  const rt = {
    web: null,
    nodesById: new Map(),
    strands: [],
    leds: [],
    ledNodeIds: [],
    ledPos: [],
    ledRow: new Map(),
    nodeRadius: new Map(),
    ringOf: new Map(),
    ringEdges: [],
    interactiveEdges: [],
    ringLevelDeg: new Map(),
    ledEdgeIdxs: [],
    adjLedByIdx: [],
    hopDist: [],
    beads: new Map(),
    events: [],
    persist: [],
    hoverEdgeIdx: null,
    hoverNode: null,
    transform: { s: 1, ox: 0, oy: 0 },
    t0: performance.now() / 1000,
    lastFrame: performance.now() / 1000,
    pointerDown: false,
    touchEdgeIdx: null,
    mouseInside: false,
    mouseWeb: [0, 0],
    signals: null,
    sound: new SoundscapeWeb(),
    chordName: "(add beads)",
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

  function seededRng(seed) {
    let s = seed >>> 0;
    return () => {
      s = (s * 1664525 + 1013904223) >>> 0;
      return (s & 0xffffffff) / 0x100000000;
    };
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

  function chordOptions(n) {
    if (CHORDS[n]) return CHORDS[n];
    if (n >= 1) {
      const stack = [];
      for (let i = 0; i < n; i += 1) {
        stack.push(MAJOR_SCALE[i % MAJOR_SCALE.length] + 12 * Math.floor(i / MAJOR_SCALE.length));
      }
      return [["stack", stack]];
    }
    return [["(add beads)", [0]]];
  }

  function selectedChord() {
    const opts = chordOptions(rt.beads.size);
    state.chordIdx = clamp(state.chordIdx, 0, opts.length - 1);
    return { name: opts[state.chordIdx][0], intervals: opts[state.chordIdx][1], options: opts };
  }

  function degreeFreq(deg, intervals) {
    const L = Math.max(intervals.length, 1);
    const semi = intervals[deg % L] + 12 * Math.floor(deg / L);
    return NOTE_ROOT * Math.pow(2, semi / 12);
  }

  function edgePan(edge) {
    const a = rt.nodesById.get(edge[0]);
    const b = rt.nodesById.get(edge[1]);
    if (!a || !b) return 0.5;
    const xmid = 0.5 * (a.x + b.x);
    return clamp(xmid / Math.max(rt.web.size[0], 1), 0, 1);
  }

  function beadNotes(intervals) {
    const order = Array.from(rt.beads.keys()).sort((a, b) => (rt.nodeRadius.get(a) || 0) - (rt.nodeRadius.get(b) || 0));
    const out = [];
    const L = Math.max(intervals.length, 1);
    for (let i = 0; i < order.length; i += 1) {
      const semi = intervals[i % L] + 12 * Math.floor(i / L);
      out.push(NOTE_ROOT * Math.pow(2, semi / 12));
    }
    return out;
  }

  function refreshChordSelect() {
    const opts = chordOptions(rt.beads.size);
    state.chordIdx = clamp(state.chordIdx, 0, opts.length - 1);
    ui.chordSelect.innerHTML = "";
    for (let i = 0; i < opts.length; i += 1) {
      const op = document.createElement("option");
      op.value = String(i);
      op.textContent = opts[i][0];
      ui.chordSelect.appendChild(op);
    }
    ui.chordSelect.value = String(state.chordIdx);
  }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = ui.canvas.clientWidth;
    const h = ui.canvas.clientHeight;
    ui.canvas.width = Math.max(1, Math.floor(w * dpr));
    ui.canvas.height = Math.max(1, Math.floor(h * dpr));
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function toWeb(mx, my) {
    const s = rt.transform.s || 1;
    return [(mx - rt.transform.ox) / s, (my - rt.transform.oy) / s];
  }

  function nearestEdgeIndex(wx, wy) {
    let best = null;
    let bestD = Infinity;
    for (let i = 0; i < rt.interactiveEdges.length; i += 1) {
      const [a, b] = rt.interactiveEdges[i];
      const na = rt.nodesById.get(a);
      const nb = rt.nodesById.get(b);
      if (!na || !nb) continue;
      const d = segmentDistance(wx, wy, na.x, na.y, nb.x, nb.y);
      if (d < bestD) {
        bestD = d;
        best = i;
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
      out[i][0] = c[0] * a * state.ambientGain;
      out[i][1] = c[1] * a * state.ambientGain;
      out[i][2] = c[2] * a * state.ambientGain;
    }
    return out;
  }

  function buildTopology() {
    rt.nodesById.clear();
    for (const n of rt.web.nodes) rt.nodesById.set(n.id, n);
    rt.strands = rt.web.strands.map((s) => [s[0], s[1]]);

    rt.leds = rt.web.nodes.filter((n) => n.led).slice().sort((a, b) => a.index - b.index);
    rt.ledNodeIds = rt.leds.map((n) => n.id);
    rt.ledPos = rt.leds.map((n) => [n.x, n.y]);
    rt.ledRow.clear();
    rt.ledNodeIds.forEach((id, i) => rt.ledRow.set(id, i));

    const { ringOf } = nodeRings(rt.web.nodes);
    rt.ringOf = ringOf;
    rt.ringEdges = rt.strands.filter(([a, b]) => ringOf.get(a) === ringOf.get(b));
    rt.interactiveEdges = rt.ringEdges.length ? rt.ringEdges.slice() : rt.strands.slice();

    const ringLevels = Array.from(new Set(rt.interactiveEdges.map(([a]) => ringOf.get(a)))).sort((a, b) => a - b);
    rt.ringLevelDeg.clear();
    ringLevels.forEach((lvl, i) => rt.ringLevelDeg.set(lvl, i));

    let cx = 0;
    let cy = 0;
    for (const n of rt.web.nodes) {
      cx += n.x;
      cy += n.y;
    }
    cx /= Math.max(rt.web.nodes.length, 1);
    cy /= Math.max(rt.web.nodes.length, 1);
    rt.nodeRadius.clear();
    for (const n of rt.web.nodes) {
      rt.nodeRadius.set(n.id, Math.hypot(n.x - cx, n.y - cy));
    }

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

    rt.ledEdgeIdxs = new Array(n);
    for (let i = 0; i < n; i += 1) rt.ledEdgeIdxs[i] = [];
    for (let ei = 0; ei < rt.interactiveEdges.length; ei += 1) {
      const [a, b] = rt.interactiveEdges[ei];
      const ia = rt.ledRow.get(a);
      const ib = rt.ledRow.get(b);
      if (ia !== undefined) rt.ledEdgeIdxs[ia].push(ei);
      if (ib !== undefined && ib !== ia) rt.ledEdgeIdxs[ib].push(ei);
    }

    rt.persist = new Array(n);
    for (let i = 0; i < n; i += 1) rt.persist[i] = [0, 0, 0];

    rt.signals = new EdgeSignals(rt.interactiveEdges, rt.nodesById);
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
      const [d, u] = pq.shift();
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
    const baseColor = PALETTE[state.colorIdx];
    const acc = beadAccumulate(edge, baseColor, state.mix, 2.4, true);
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
    const baseColor = PALETTE[state.colorIdx];
    const acc = beadAccumulate(edge, baseColor, state.mix, 2.4, false);
    rt.events.push({
      type: "area",
      start: t,
      duration: 2.5,
      reach: state.overlapHops,
      falloff: state.overlapFalloff,
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
    refreshChordSelect();
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

  function updateSignalMeter(cur, hotCount) {
    const nFrac = clamp(state.noiseMax / SIGNAL_SCALE, 0, 1);
    const tFrac = clamp(state.threshold / SIGNAL_SCALE, 0, 1);
    const cFrac = clamp(cur / SIGNAL_SCALE, 0, 1);

    ui.meterTrack.style.background = `linear-gradient(90deg,
      rgba(40,44,54,1) 0%,
      rgba(40,44,54,1) ${nFrac * 100}%,
      rgba(28,52,84,1) ${nFrac * 100}%,
      rgba(28,52,84,1) ${tFrac * 100}%,
      rgba(74,30,34,1) ${tFrac * 100}%,
      rgba(74,30,34,1) 100%)`;
    ui.meterFill.style.width = `${cFrac * 100}%`;
    ui.noiseHandle.style.left = `${nFrac * 100}%`;
    ui.thresholdHandle.style.left = `${tFrac * 100}%`;
    ui.signalInfo.textContent = `signal max ${cur.toFixed(2)}   hot edges ${hotCount}\nnoise < ${state.noiseMax.toFixed(2)}   touch > ${state.threshold.toFixed(2)}`;
    ui.calibrate.textContent = state.calStep ? `C Calibrate ${state.calStep}/3` : "C Calibrate signal";
  }

  function updateUI() {
    ui.trailV.textContent = `${state.trail.toFixed(2)}s`;
    ui.decayV.textContent = `${state.decay.toFixed(2)}s`;
    ui.reachV.textContent = `${state.overlapHops} hop${state.overlapHops === 1 ? "" : "s"}`;
    ui.falloffV.textContent = `${Math.round(state.overlapFalloff * 100)}%`;
    ui.hoverV.textContent = `${Math.round(state.hoverGain * 100)}%`;
    ui.ambientV.textContent = `${Math.round(state.ambientGain * 100)}%`;
    ui.mixV.textContent = `${Math.round(state.mix * 100)}%`;
    ui.volV.textContent = `${Math.round(state.volume * 100)}%`;
    ui.droneV.textContent = `${Math.round(state.droneGain * 100)}%`;
    ui.chimeV.textContent = `${Math.round(state.chimeGain * 100)}%`;
    ui.beadV.textContent = `${Math.round((state.beadLevel / BEAD_GLOW_MAX) * 100)}%`;
    ui.ambient.textContent = `A Ambient: ${AMBIENT_MODES[state.ambientIdx]}`;

    const c = PALETTE[state.colorIdx];
    ui.colorSwatch.style.background = `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;
    ui.toolRipple.classList.toggle("active", state.tool === "ripple_hops");
    ui.toolArea.classList.toggle("active", state.tool === "overlap");
    ui.toolBead.classList.toggle("active", state.tool === "bead");

    if (!rt.sound.supported) {
      ui.soundToggle.textContent = "S Sound: unsupported";
      ui.soundToggle.disabled = true;
      ui.soundToggle.classList.remove("active");
    } else {
      ui.soundToggle.textContent = `S Sound: ${state.soundOn ? "on" : "off"}`;
      ui.soundToggle.classList.toggle("active", state.soundOn);
    }
  }

  function calibrateStep() {
    const cur = rt.signals ? rt.signals.maxSignal() : 0;
    if (state.calStep === 0) {
      state.calStep = 1;
    } else if (state.calStep === 1) {
      state.cal.bg = cur;
      state.calStep = 2;
    } else if (state.calStep === 2) {
      state.cal.hover = cur;
      state.calStep = 3;
    } else {
      state.cal.click = cur;
      const bg = state.cal.bg;
      const hv = state.cal.hover;
      const cl = state.cal.click;
      const nz = hv > bg ? (bg + hv) * 0.5 : bg * 1.5;
      const thr = cl > hv ? (hv + cl) * 0.5 : Math.max(hv, cur) * 1.1;
      state.noiseMax = clamp(nz, 0.05, 2.0);
      state.threshold = clamp(Math.max(thr, state.noiseMax + 0.03), 0.05, 2.0);
      state.calStep = 0;
    }
    updateUI();
  }

  function applyMeterClientX(clientX) {
    const rect = ui.meterTrack.getBoundingClientRect();
    const frac = clamp((clientX - rect.left) / Math.max(rect.width, 1), 0, 1);
    const v = frac * SIGNAL_SCALE;
    if (state.dragTarget === "noise") {
      state.noiseMax = Math.min(v, state.threshold - 0.03);
    } else if (state.dragTarget === "threshold") {
      state.threshold = Math.max(v, state.noiseMax + 0.03);
    }
  }

  function handleToolAction(action) {
    if (action === "ripple") state.tool = "ripple_hops";
    else if (action === "area") state.tool = "overlap";
    else if (action === "bead") state.tool = "bead";
    else if (action === "shuffle") shuffleBeads();
    else if (action === "clear") rt.events = [];
    else if (action === "ambient") state.ambientIdx = (state.ambientIdx + 1) % AMBIENT_MODES.length;
    else if (action === "color") state.colorIdx = (state.colorIdx + 1) % PALETTE.length;
    else if (action === "sound") {
      if (rt.sound.supported) {
        state.soundOn = !state.soundOn;
        if (state.soundOn) rt.sound.touch();
      }
    } else if (action === "calibrate") calibrateStep();
    updateUI();
  }

  function wireEvents() {
    ui.toolRipple.addEventListener("click", () => handleToolAction("ripple"));
    ui.toolArea.addEventListener("click", () => handleToolAction("area"));
    ui.toolBead.addEventListener("click", () => handleToolAction("bead"));
    ui.shuffle.addEventListener("click", () => handleToolAction("shuffle"));
    ui.clear.addEventListener("click", () => handleToolAction("clear"));
    ui.ambient.addEventListener("click", () => handleToolAction("ambient"));
    ui.cycleColor.addEventListener("click", () => handleToolAction("color"));
    ui.soundToggle.addEventListener("click", () => handleToolAction("sound"));
    ui.calibrate.addEventListener("click", () => handleToolAction("calibrate"));

    ui.trail.addEventListener("input", () => { state.trail = Number(ui.trail.value); updateUI(); });
    ui.decay.addEventListener("input", () => { state.decay = Number(ui.decay.value); updateUI(); });
    ui.reach.addEventListener("input", () => { state.overlapHops = Number(ui.reach.value); updateUI(); });
    ui.falloff.addEventListener("input", () => { state.overlapFalloff = Number(ui.falloff.value); updateUI(); });
    ui.hoverGain.addEventListener("input", () => { state.hoverGain = Number(ui.hoverGain.value); updateUI(); });
    ui.ambientGain.addEventListener("input", () => { state.ambientGain = Number(ui.ambientGain.value); updateUI(); });
    ui.mix.addEventListener("input", () => { state.mix = Number(ui.mix.value); updateUI(); });
    ui.volume.addEventListener("input", () => { state.volume = Number(ui.volume.value); updateUI(); });
    ui.drone.addEventListener("input", () => { state.droneGain = Number(ui.drone.value); updateUI(); });
    ui.chime.addEventListener("input", () => { state.chimeGain = Number(ui.chime.value); updateUI(); });
    ui.beadLevel.addEventListener("input", () => { state.beadLevel = Number(ui.beadLevel.value); updateUI(); });
    ui.chordSelect.addEventListener("change", () => {
      state.chordIdx = Number(ui.chordSelect.value) || 0;
      updateUI();
    });

    ui.meterTrack.addEventListener("mousedown", (ev) => {
      const r = ui.meterTrack.getBoundingClientRect();
      const nX = (state.noiseMax / SIGNAL_SCALE) * r.width;
      const tX = (state.threshold / SIGNAL_SCALE) * r.width;
      const x0 = ev.clientX - r.left;
      state.dragTarget = Math.abs(x0 - nX) <= Math.abs(x0 - tX) ? "noise" : "threshold";
      applyMeterClientX(ev.clientX);
    });
    ui.noiseHandle.addEventListener("mousedown", (ev) => {
      ev.stopPropagation();
      state.dragTarget = "noise";
    });
    ui.thresholdHandle.addEventListener("mousedown", (ev) => {
      ev.stopPropagation();
      state.dragTarget = "threshold";
    });
    window.addEventListener("mousemove", (ev) => {
      if (state.dragTarget) applyMeterClientX(ev.clientX);
    });
    window.addEventListener("mouseup", () => {
      state.dragTarget = null;
    });

    window.addEventListener("resize", resize);

    ui.canvas.addEventListener("mousemove", (ev) => {
      const rect = ui.canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      const [wx, wy] = toWeb(mx, my);
      rt.mouseInside = true;
      rt.mouseWeb = [wx, wy];
      if (state.tool === "bead") {
        rt.hoverNode = nearestLedNode(wx, wy);
        rt.hoverEdgeIdx = null;
      } else {
        rt.hoverEdgeIdx = nearestEdgeIndex(wx, wy);
        rt.hoverNode = null;
      }
      if (rt.pointerDown && state.tool !== "bead") {
        rt.touchEdgeIdx = nearestEdgeIndex(wx, wy);
      }
    });

    ui.canvas.addEventListener("mouseleave", () => {
      rt.mouseInside = false;
      rt.hoverEdgeIdx = null;
      rt.hoverNode = null;
      if (!rt.pointerDown) rt.touchEdgeIdx = null;
    });

    ui.canvas.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      if (state.soundOn) rt.sound.touch();
      rt.pointerDown = true;
      const rect = ui.canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      const [wx, wy] = toWeb(mx, my);
      rt.mouseInside = true;
      rt.mouseWeb = [wx, wy];

      if (state.tool === "bead") {
        const n = nearestLedNode(wx, wy);
        if (!n) return;
        const cur = rt.beads.has(n.id) ? rt.beads.get(n.id) : -1;
        const nxt = cur + 1;
        if (nxt >= BEAD_TYPES.length) rt.beads.delete(n.id);
        else rt.beads.set(n.id, nxt);
        refreshChordSelect();
        return;
      }

      rt.touchEdgeIdx = nearestEdgeIndex(wx, wy);
      if (rt.touchEdgeIdx !== null) {
        rt.signals.inject(rt.touchEdgeIdx, CLICK_AMP);
      }
    });

    window.addEventListener("mouseup", () => {
      rt.pointerDown = false;
      rt.touchEdgeIdx = null;
    });

    window.addEventListener("keydown", (ev) => {
      if (ev.key === "1") handleToolAction("ripple");
      else if (ev.key === "2") handleToolAction("area");
      else if (ev.key === "3") handleToolAction("bead");
      else if (ev.key === "r" || ev.key === "R") handleToolAction("shuffle");
      else if (ev.key === "x" || ev.key === "X") handleToolAction("clear");
      else if (ev.key === "a" || ev.key === "A") handleToolAction("ambient");
      else if (ev.key === "s" || ev.key === "S") handleToolAction("sound");
      else if (ev.key === "c" || ev.key === "C") handleToolAction("calibrate");
      else if (ev.code === "Space") handleToolAction("color");
      else return;
      ev.preventDefault();
    });
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

    const hand = rt.mouseInside ? rt.mouseWeb : null;
    rt.signals.update(t, hand, dt);
    if (rt.touchEdgeIdx !== null) {
      rt.signals.inject(rt.touchEdgeIdx, CLICK_AMP);
    }

    const rising = rt.signals.crossings(state.threshold);
    const chord = selectedChord();
    rt.chordName = chord.name;
    for (const ei of rising) {
      const edge = rt.interactiveEdges[ei];
      if (!edge) continue;
      if (state.tool === "ripple_hops") spawnRipple(edge, t);
      else if (state.tool === "overlap") spawnArea(edge, t);

      const lvl = rt.ringOf.get(edge[0]) || 0;
      const deg = rt.ringLevelDeg.get(lvl) || 0;
      const freq = degreeFreq(deg, chord.intervals);
      const pan = edgePan(edge);
      const vel = 0.5 + 0.5 * clamp(rt.signals.signal[ei] / Math.max(state.threshold, 1e-3), 0, 1);
      if (state.soundOn) rt.sound.triggerNote(freq, vel, pan);
    }

    x.fillStyle = BG;
    x.fillRect(0, 0, vw, vh);

    const n = rt.ledNodeIds.length;
    const base = ambientContribution(t);

    const span = Math.max(state.threshold - state.noiseMax, 1e-3);
    for (let i = 0; i < n; i += 1) {
      let ledSig = 0.0;
      for (const ei of rt.ledEdgeIdxs[i]) {
        ledSig = Math.max(ledSig, rt.signals.signal[ei]);
      }
      const frac = clamp((ledSig - state.noiseMax) / span, 0, 1);
      base[i][0] += frac * state.hoverGain * HOVER_COLOR[0];
      base[i][1] += frac * state.hoverGain * HOVER_COLOR[1];
      base[i][2] += frac * state.hoverGain * HOVER_COLOR[2];
    }

    for (const [nid, kind] of rt.beads.entries()) {
      const idx = rt.ledRow.get(nid);
      if (idx === undefined) continue;
      const bc = BEAD_TYPES[kind].color;
      base[idx][0] += bc[0] * state.beadLevel;
      base[idx][1] += bc[1] * state.beadLevel;
      base[idx][2] += bc[2] * state.beadLevel;
    }

    const num = new Array(n);
    const den = new Array(n).fill(0);
    for (let i = 0; i < n; i += 1) {
      num[i] = [base[i][0], base[i][1], base[i][2]];
    }

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
      const c = d > 1e-6
        ? [num[i][0] / (1 + d), num[i][1] / (1 + d), num[i][2] / (1 + d)]
        : num[i];
      out[i] = [clamp(c[0], 0, 1), clamp(c[1], 0, 1), clamp(c[2], 0, 1)];
    }

    if (state.trail > 0.05) {
      const halfLife = state.trail / 3.32;
      const decay = Math.pow(0.5, dt / Math.max(halfLife, 1e-3));
      for (let i = 0; i < n; i += 1) {
        rt.persist[i][0] = Math.max(out[i][0], rt.persist[i][0] * decay);
        rt.persist[i][1] = Math.max(out[i][1], rt.persist[i][1] * decay);
        rt.persist[i][2] = Math.max(out[i][2], rt.persist[i][2] * decay);
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

    if (rt.hoverEdgeIdx !== null && state.tool !== "bead") {
      const edge = rt.interactiveEdges[rt.hoverEdgeIdx];
      if (edge) {
        const na = rt.nodesById.get(edge[0]);
        const nb = rt.nodesById.get(edge[1]);
        if (na && nb) {
          x.strokeStyle = EDGE_HL;
          x.lineWidth = 3;
          x.beginPath();
          x.moveTo(ox + na.x * s, oy + na.y * s);
          x.lineTo(ox + nb.x * s, oy + nb.y * s);
          x.stroke();
        }
      }
    }
    if (rt.hoverNode && state.tool === "bead") {
      x.strokeStyle = NODE_HL;
      x.lineWidth = 2;
      x.beginPath();
      x.arc(ox + rt.hoverNode.x * s, oy + rt.hoverNode.y * s, 8, 0, Math.PI * 2);
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

    const notes = beadNotes(chord.intervals);
    rt.sound.setEnabled(state.soundOn);
    rt.sound.setVolume(state.volume);
    rt.sound.setDroneGain(state.droneGain);
    rt.sound.setChimeGain(state.chimeGain);
    rt.sound.setChord(notes);
    let beadLight = 0;
    if (rt.beads.size > 0) {
      let sum = 0;
      let cnt = 0;
      for (const nid of rt.beads.keys()) {
        const idx = rt.ledRow.get(nid);
        if (idx === undefined) continue;
        const c = rt.persist[idx];
        sum += Math.max(c[0], c[1], c[2]);
        cnt += 1;
      }
      beadLight = cnt ? clamp((sum / cnt) * 1.3, 0, 1) : 0;
    }
    const chordLevel = state.soundOn
      ? clamp(state.beadLevel + (1.0 - state.beadLevel) * beadLight, 0, 1)
      : 0.0;
    rt.sound.setChordLevel(chordLevel);

    const cur = rt.signals.maxSignal();
    const hot = rt.signals.signal.filter((v) => v >= state.threshold).length;
    updateSignalMeter(cur, hot);

    const calLine = state.calStep
      ? `calibrate: ${CAL_PROMPTS[state.calStep]}`
      : `noise < ${state.noiseMax.toFixed(2)}  hover  touch > ${state.threshold.toFixed(2)}`;
    ui.leftStatus.textContent =
      `${calLine}\n` +
      `signals ${rt.events.length}   beads ${rt.beads.size}   LEDs ${n}\n` +
      `device: off (web standalone)`;
    ui.rightStatus.textContent =
      `chord: ${rt.chordName}\n` +
      `beads ${rt.beads.size}\n` +
      `sound: ${state.soundOn ? "on" : "off"}`;

    requestAnimationFrame(frame);
  }

  async function init() {
    try {
      const res = await fetch("./web.json", { cache: "no-store" });
      if (!res.ok) throw new Error(`Failed to load web.json (${res.status})`);
      rt.web = await res.json();
      buildTopology();
      shuffleBeads();
      wireEvents();
      refreshChordSelect();
      resize();
      updateUI();
      requestAnimationFrame(frame);
    } catch (err) {
      ui.leftStatus.textContent = `Failed to initialize:\n${err && err.message ? err.message : String(err)}`;
    }
  }

  init();
})();
