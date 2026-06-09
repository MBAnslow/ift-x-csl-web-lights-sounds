"""Simulator / runtime: preview spatial events and optionally stream to the ESP.

Interactions are edge-based: hover the canvas to highlight the nearest strand,
then click to fire the selected tool from that edge. Which LEDs light depends
on the edge you touch (its endpoints and their neighbours).
  1 propagate (distance)   2 propagate (hops)   3 overlap (neighbours)
  4 charge & release (press-hold an edge, release to fire a brighter wave)
  SPACE colour   A ambient mode   X clear   ESC quit
The Trail slider sets how long a light lingers after it is activated.

Pass --serial PORT to also stream frames to a connected ESP.
"""
from __future__ import annotations

import time

import numpy as np
import pygame

from spiderweb import ui
from spiderweb.engine import Engine
from spiderweb.events import Ambient, Charge, Overlap, Propagate
from spiderweb.web import Web

BG = (8, 9, 14)
SIDEBAR_BG = (6, 7, 11)
DIVIDER = (34, 38, 50)
SIDEBAR_W = 320
STRAND = (32, 36, 46)
EDGE_HL = (255, 210, 90)
EDGE_PICK_DIST = 26.0  # max px from cursor to grab a strand
TEXT = (210, 215, 225)

PALETTE = [
    (0.20, 0.65, 1.00),  # cyan-blue
    (1.00, 0.35, 0.15),  # ember
    (0.45, 1.00, 0.40),  # green
    (1.00, 0.80, 0.20),  # gold
    (0.85, 0.30, 1.00),  # violet
    (1.00, 1.00, 1.00),  # white
]

BUTTONS = [
    ("1", "Propagate edge \u2014 distance", "tool:propagate"),
    ("2", "Propagate edge \u2014 hops", "tool:prop_hops"),
    ("3", "Overlap edge (neighbours)", "tool:overlap"),
    ("4", "Charge edge & release", "tool:charge"),
    ("spc", "Cycle colour", "act:color"),
    ("A", "Ambient", "act:ambient"),
    ("X", "Clear events", "act:clear"),
    ("esc", "Quit", "act:quit"),
]
KEYMAP = {
    pygame.K_1: "tool:propagate",
    pygame.K_2: "tool:prop_hops",
    pygame.K_3: "tool:overlap",
    pygame.K_4: "tool:charge",
    pygame.K_SPACE: "act:color",
    pygame.K_a: "act:ambient",
    pygame.K_x: "act:clear",
    pygame.K_ESCAPE: "act:quit",
}

# ambient modes cycled with A; "off" plus the Ambient.MODES
AMBIENT_MODES = ("off", "shimmer", "breathe", "twinkle", "wander", "rainbow")
# Trail slider: 0 .. TRAIL_MAX seconds of "stay on" time after activation.
TRAIL_MAX = 5.0
# Overlap reach slider: how many strand-hops of enabled neighbours it covers.
MAX_HOPS = 4
# Distant-glow slider: per-hop falloff. Low = distant lights barely lit on a
# single click (so repeated clicks build them up); high = they light almost
# fully right away. Applies to overlap, charge and propagate.
FALLOFF_MIN = 0.1
FALLOFF_MAX = 0.95


def _make_glow_sprite(size=128, falloff=2.4):
    """A white radial gradient (bright centre -> black edge), alpha 255.

    Drawn additively after tinting, so RGB encodes intensity and overlapping
    bulbs blend like real light."""
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    c = size // 2
    for i in range(c, 0, -1):
        f = i / c  # 1 at edge -> 0 at centre
        v = int(255 * (1.0 - f) ** falloff)
        pygame.draw.circle(surf, (v, v, v, 255), (c, c), i)
    return surf


def _draw_bulb(surf, sprite, pos, color, bright):
    """Render one LED as a glowing bulb: wide colour bloom + hot core + body."""
    x, y = pos
    r, g, b = color
    if bright > 0.02:
        # soft colour bloom
        rad = int(7 + 46 * bright)
        bloom = pygame.transform.smoothscale(sprite, (rad * 2, rad * 2))
        bloom.fill((r, g, b, 255), special_flags=pygame.BLEND_RGB_MULT)
        surf.blit(bloom, (x - rad, y - rad), special_flags=pygame.BLEND_RGB_ADD)
        # hot near-white core for bright bulbs
        crad = int(3 + 7 * bright)
        core = pygame.transform.smoothscale(sprite, (crad * 2, crad * 2))
        k = 0.45 + 0.55 * bright
        cr = int(r + (255 - r) * k)
        cg = int(g + (255 - g) * k)
        cb = int(b + (255 - b) * k)
        core.fill((cr, cg, cb, 255), special_flags=pygame.BLEND_RGB_MULT)
        surf.blit(core, (x - crad, y - crad), special_flags=pygame.BLEND_RGB_ADD)
    # physical bulb body, always visible (dark glass when off)
    body = (max(r, 28), max(g, 28), max(b, 32))
    pygame.draw.circle(surf, body, (x, y), 3)


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
    reach_origin = (12, trail_panel.bottom + 8)
    reach_panel, reach_track = ui.slider_layout(reach_origin)
    glow_origin = (12, reach_panel.bottom + 8)
    glow_panel, glow_track = ui.slider_layout(glow_origin)
    status_origin = (12, glow_panel.bottom + 8)
    status_h = ui.PAD * 2 + 2 * 20
    win_w = SIDEBAR_W + web.size[0]
    win_h = max(web.size[1], status_origin[1] + status_h + 12)

    pygame.init()
    screen = pygame.display.set_mode((win_w, win_h))
    pygame.display.set_caption("Spider-web simulator")
    font, title_font = ui.make_fonts()
    clock = pygame.time.Clock()

    state = {"tool": "propagate", "color_idx": 0, "ambient_idx": 1,
             "trail": 1.0, "overlap_hops": 2, "falloff": 0.3, "drag_target": None,
             "ambient": None, "run": True}
    charge: dict | None = None  # active charge: {"event": Charge, "id": node_id}
    t0 = time.time()

    leds = web.leds()
    led_pos = [(int(n.x) + SIDEBAR_W, int(n.y)) for n in leds]
    glow_sprite = _make_glow_sprite()
    persist = np.zeros((len(leds), 3))

    def set_ambient() -> None:
        if state["ambient"] is not None and state["ambient"] in engine.events:
            engine.events.remove(state["ambient"])
        state["ambient"] = None
        mode = AMBIENT_MODES[state["ambient_idx"]]
        if mode != "off":
            state["ambient"] = Ambient(mode=mode)
            engine.add(state["ambient"])

    set_ambient()

    def activate(action: str) -> None:
        if action.startswith("tool:"):
            state["tool"] = action.split(":", 1)[1]
        elif action == "act:color":
            state["color_idx"] = (state["color_idx"] + 1) % len(PALETTE)
        elif action == "act:clear":
            engine.clear_all()
            set_ambient()
            persist[:] = 0
        elif action == "act:ambient":
            state["ambient_idx"] = (state["ambient_idx"] + 1) % len(AMBIENT_MODES)
            set_ambient()
        elif action == "act:quit":
            state["run"] = False

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
                    elif reach_panel.collidepoint(ev.pos):
                        state["drag_target"] = "reach"
                        state["overlap_hops"] = 1 + round(
                            ui.value_from_x(reach_track, ev.pos[0]) * (MAX_HOPS - 1))
                    elif glow_panel.collidepoint(ev.pos):
                        state["drag_target"] = "glow"
                        state["falloff"] = FALLOFF_MIN + ui.value_from_x(
                            glow_track, ev.pos[0]) * (FALLOFF_MAX - FALLOFF_MIN)
                else:
                    mx, my = ev.pos[0] - SIDEBAR_W, ev.pos[1]
                    tool = state["tool"]
                    edge = web.nearest_edge(mx, my, max_dist=EDGE_PICK_DIST)
                    if edge is None:
                        pass
                    elif tool in ("propagate", "prop_hops"):
                        metric = "hops" if tool == "prop_hops" else "distance"
                        engine.add(Propagate(list(edge), color=color, start=t, metric=metric,
                                             dist_falloff=state["falloff"]))
                    elif tool == "overlap":
                        engine.add(Overlap({"type": "edge", "a": edge[0], "b": edge[1]},
                                           color=color, start=t, duration=2.5,
                                           spread_hops=state["overlap_hops"],
                                           spread_falloff=state["falloff"]))
                    elif tool == "charge":
                        ce = Charge(edge, color=color, start=t,
                                    spread_falloff=state["falloff"])
                        engine.add(ce)
                        charge = {"event": ce, "edge": edge}
            elif ev.type == pygame.MOUSEMOTION and state["drag_target"]:
                if state["drag_target"] == "trail":
                    state["trail"] = ui.value_from_x(trail_track, ev.pos[0]) * TRAIL_MAX
                elif state["drag_target"] == "reach":
                    state["overlap_hops"] = 1 + round(
                        ui.value_from_x(reach_track, ev.pos[0]) * (MAX_HOPS - 1))
                elif state["drag_target"] == "glow":
                    state["falloff"] = FALLOFF_MIN + ui.value_from_x(
                        glow_track, ev.pos[0]) * (FALLOFF_MAX - FALLOFF_MIN)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                state["drag_target"] = None
                if charge is not None:
                    lvl = charge["event"].level(t)
                    if charge["event"] in engine.events:
                        engine.events.remove(charge["event"])
                    gain = 0.8 + 2.6 * lvl  # held longer -> brighter wave
                    # the release is a deliberate discharge: ripple out at full
                    # brightness (not subject to the distant-glow dimming).
                    engine.add(Propagate(list(charge["edge"]), color=color, start=t,
                                         gain=gain, width=60.0, duration=4.0))
                    charge = None

        rgb = engine.update(t)  # (N,3) float 0..1

        # ---- trail / afterglow (temporal persistence) ----
        # "trail" is roughly how long a light stays on; convert to a decay
        # half-life so the glow fades to ~1/10 over that time.
        trail = state["trail"]
        if trail > 0.05:
            half_life = trail / 3.32
            dt = clock.get_time() / 1000.0
            decay = 0.5 ** (dt / half_life) if dt > 0 else 1.0
            persist[:] = np.maximum(rgb, persist * decay)
            out = persist
        else:
            out = rgb

        # ---- draw (canvas is offset right of the sidebar) ----
        screen.fill(BG)
        pos = {n.id: (int(n.x) + SIDEBAR_W, int(n.y)) for n in web.nodes}
        for a, b in web.strands:
            if a in pos and b in pos:
                pygame.draw.line(screen, STRAND, pos[a], pos[b], 1)

        # highlight the edge under the cursor (or the one being charged)
        hover_edge = None
        if charge is not None:
            hover_edge = charge["edge"]
        else:
            mx, my = pygame.mouse.get_pos()
            if mx >= SIDEBAR_W:
                hover_edge = web.nearest_edge(mx - SIDEBAR_W, my, max_dist=EDGE_PICK_DIST)
        if hover_edge is not None and hover_edge[0] in pos and hover_edge[1] in pos:
            pygame.draw.line(screen, EDGE_HL, pos[hover_edge[0]], pos[hover_edge[1]], 3)

        for i, p in enumerate(led_pos):
            c = out[i]
            disp = tuple(int(min(255, v * 255)) for v in c)
            bright = float(np.max(c))
            _draw_bulb(screen, glow_sprite, p, disp, bright)

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
            rows.append((key, label, active))
        ui.draw_panel(screen, font, title_font, "SIMULATOR \u2014 click an edge", rows,
                      mouse_pos=pygame.mouse.get_pos())
        sw = pygame.Surface((28, 16))
        sw.fill(tuple(int(v * 255) for v in color))
        screen.blit(sw, (252, 22))
        trail_label = "Trail: off" if state["trail"] <= 0.05 else f"Trail: {state['trail']:.1f}s"
        ui.draw_slider(screen, font, trail_label, state["trail"] / TRAIL_MAX, trail_origin)
        hops = state["overlap_hops"]
        reach_label = f"Overlap reach: {hops} hop" + ("s" if hops != 1 else "")
        ui.draw_slider(screen, font, reach_label, (hops - 1) / (MAX_HOPS - 1), reach_origin)
        glow_val = (state["falloff"] - FALLOFF_MIN) / (FALLOFF_MAX - FALLOFF_MIN)
        ui.draw_slider(screen, font, f"Distant glow: {int(state['falloff'] * 100)}%",
                       glow_val, glow_origin)
        ui.draw_status(
            screen, font,
            [
                f"events {len(engine.events)}   LEDs {len(leds)}",
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
        link.send(np.zeros((len(leds), 3), dtype=np.uint8))  # blank on exit
        link.close()
    pygame.quit()
