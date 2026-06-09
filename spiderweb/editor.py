"""Interactive editor: place nodes, toggle which ones are LEDs, draw strands.

Click the buttons in the left panel (or press the matching key) to choose a
mode. The active mode is highlighted. Then click on the canvas to act.
"""
from __future__ import annotations

import math

import pygame

from spiderweb import ui, webgen
from spiderweb.web import Web

BG = (12, 14, 20)
SIDEBAR_BG = (9, 10, 15)
DIVIDER = (40, 44, 56)
SIDEBAR_W = 320
STRAND = (78, 86, 104)
LED_COL = (90, 200, 255)
LED_RING = (40, 110, 150)
NODE_COL = (110, 110, 122)
SEL_COL = (255, 210, 90)
TEXT = (210, 215, 225)
PICK_RADIUS = 16.0

# (key label, caption, action). action "mode:x" selects a mode; "act:x" runs once.
BUTTONS = [
    ("L", "Add LED node", "mode:led"),
    ("N", "Add plain node", "mode:node"),
    ("T", "Toggle LED on/off", "mode:toggle"),
    ("E", "Connect strand", "mode:edge"),
    ("M", "Move node (drag)", "mode:move"),
    ("D", "Delete node/strand", "mode:delete"),
    ("S", "Save layout", "act:save"),
    ("G", "Load sample web", "act:sample"),
    ("C", "Clear selection", "act:clear"),
    ("esc", "Quit", "act:quit"),
]
KEYMAP = {
    pygame.K_l: "mode:led",
    pygame.K_n: "mode:node",
    pygame.K_t: "mode:toggle",
    pygame.K_e: "mode:edge",
    pygame.K_m: "mode:move",
    pygame.K_d: "mode:delete",
    pygame.K_s: "act:save",
    pygame.K_g: "act:sample",
    pygame.K_c: "act:clear",
    pygame.K_ESCAPE: "act:quit",
}


def _nearest_strand(web: Web, x: float, y: float, max_dist: float):
    pos = {n.id: (n.x, n.y) for n in web.nodes}
    best, best_d = None, max_dist
    for a, b in web.strands:
        if a not in pos or b not in pos:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        if seg == 0:
            continue
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg))
        px, py = ax + t * dx, ay + t * dy
        d = math.hypot(x - px, y - py)
        if d < best_d:
            best_d, best = d, (a, b)
    return best


def run(config_path: str) -> None:
    pygame.init()
    web = Web.load(config_path)
    if not web.nodes:
        web.size = (1000, 760)
    panel_rect, row_rects = ui.panel_layout(BUTTONS)
    status_h = ui.PAD * 2 + 2 * 20
    win_w = SIDEBAR_W + web.size[0]
    win_h = max(web.size[1], panel_rect.bottom + 8 + status_h + 12)
    screen = pygame.display.set_mode((win_w, win_h))
    pygame.display.set_caption("Spider-web editor")
    font, title_font = ui.make_fonts()
    clock = pygame.time.Clock()

    state = {"mode": "led", "edge_first": None, "drag_id": None, "status": "ready", "run": True}

    def activate(action: str) -> None:
        nonlocal web
        if action.startswith("mode:"):
            state["mode"] = action.split(":", 1)[1]
            state["edge_first"] = None
            state["status"] = f"mode: {state['mode']}"
        elif action == "act:save":
            web.save(config_path)
            state["status"] = f"saved -> {config_path}"
        elif action == "act:sample":
            web = webgen.radial_web(size=web.size)
            state["status"] = "loaded sample web (Save to keep)"
        elif action == "act:clear":
            state["edge_first"] = None
            state["status"] = "selection cleared"
        elif action == "act:quit":
            state["run"] = False

    def canvas_click(mx: int, my: int) -> None:
        mode = state["mode"]
        if mode in ("led", "node"):
            n = web.add_node(mx, my, led=(mode == "led"))
            state["status"] = f"added {mode} #{n.id}"
        elif mode == "toggle":
            n = web.nearest_node(mx, my, PICK_RADIUS)
            if n is not None:
                web.toggle_led(n.id)
                state["status"] = f"node #{n.id} LED {'ON' if n.led else 'off'}"
            else:
                state["status"] = "toggle: click on a node"
        elif mode == "edge":
            n = web.nearest_node(mx, my, PICK_RADIUS)
            if n is None:
                state["status"] = "edge: click on a node"
            elif state["edge_first"] is None:
                state["edge_first"] = n.id
                state["status"] = f"edge: from #{n.id} ... click target"
            else:
                web.add_strand(state["edge_first"], n.id)
                state["status"] = f"strand #{state['edge_first']}-#{n.id}"
                state["edge_first"] = None
        elif mode == "move":
            n = web.nearest_node(mx, my, PICK_RADIUS)
            state["drag_id"] = n.id if n else None
        elif mode == "delete":
            n = web.nearest_node(mx, my, PICK_RADIUS)
            if n is not None:
                web.remove_node(n.id)
                state["status"] = f"deleted node #{n.id}"
            else:
                s = _nearest_strand(web, mx, my, PICK_RADIUS)
                if s is not None:
                    web.remove_strand(*s)
                    state["status"] = f"deleted strand {s[0]}-{s[1]}"

    while state["run"]:
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
                else:
                    canvas_click(ev.pos[0] - SIDEBAR_W, ev.pos[1])
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                state["drag_id"] = None
            elif ev.type == pygame.MOUSEMOTION and state["drag_id"] is not None:
                n = web.node_by_id(state["drag_id"])
                if n is not None:
                    n.x = max(0.0, float(ev.pos[0] - SIDEBAR_W))
                    n.y = float(ev.pos[1])

        # ---- draw web (canvas is offset right of the sidebar) ----
        screen.fill(BG)
        pos = {n.id: (int(n.x) + SIDEBAR_W, int(n.y)) for n in web.nodes}
        for a, b in web.strands:
            if a in pos and b in pos:
                pygame.draw.line(screen, STRAND, pos[a], pos[b], 2)
        for n in web.nodes:
            p = pos[n.id]
            if n.led:
                pygame.draw.circle(screen, LED_RING, p, 9)
                pygame.draw.circle(screen, LED_COL, p, 6)
                screen.blit(font.render(str(n.index), True, TEXT), (p[0] + 10, p[1] - 8))
            else:
                pygame.draw.circle(screen, NODE_COL, p, 4, 1)
            if n.id == state["edge_first"] or n.id == state["drag_id"]:
                pygame.draw.circle(screen, SEL_COL, p, 13, 2)

        # ---- controls sidebar ----
        pygame.draw.rect(screen, SIDEBAR_BG, (0, 0, SIDEBAR_W, win_h))
        pygame.draw.line(screen, DIVIDER, (SIDEBAR_W, 0), (SIDEBAR_W, win_h), 1)
        rows = [(k, label, action == f"mode:{state['mode']}") for k, label, action in BUTTONS]
        ui.draw_panel(screen, font, title_font, "EDITOR \u2014 click a mode", rows,
                      mouse_pos=pygame.mouse.get_pos())
        ui.draw_status(
            screen, font,
            [
                f"LEDs {web.num_leds}   nodes {len(web.nodes)}   strands {len(web.strands)}",
                state["status"],
            ],
            origin=(12, panel_rect.bottom + 8),
        )

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
