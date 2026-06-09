"""Shared on-screen UI: a clickable control panel that lists keys, highlights
the active mode/tool, supports hover, and a small status bar.

Panel rows are clickable buttons. `panel_layout` returns the panel rect and a
rect per row so callers can hit-test mouse clicks; `draw_panel` renders using
the same geometry.
"""
from __future__ import annotations

import pygame

PANEL_BG = (16, 18, 26, 225)
BORDER = (62, 70, 88)
TITLE = (255, 210, 90)
KEY_BG = (44, 50, 64)
KEY_BG_ON = (255, 210, 90)
KEY_FG = (222, 228, 238)
KEY_FG_ON = (18, 20, 26)
LABEL = (205, 212, 224)
LABEL_ON = (20, 22, 28)
ROW_ON = (255, 210, 90)
ROW_HOVER = (40, 46, 60)
LINE_H = 30
PAD = 12
TITLE_H = 32
INNER = 6


def make_fonts():
    pygame.font.init()
    return (
        pygame.font.SysFont("menlo,consolas,monospace", 15),
        pygame.font.SysFont("menlo,consolas,monospace", 16, bold=True),
    )


def panel_layout(rows, origin=(12, 12), width=300):
    height = PAD * 2 + TITLE_H + len(rows) * LINE_H
    panel_rect = pygame.Rect(origin[0], origin[1], width, height)
    row_rects = []
    y = origin[1] + PAD + TITLE_H
    for _ in rows:
        row_rects.append(pygame.Rect(origin[0] + INNER, y, width - 2 * INNER, LINE_H - 2))
        y += LINE_H
    return panel_rect, row_rects


def draw_panel(screen, font, title_font, title, rows, mouse_pos=None, origin=(12, 12), width=300):
    """rows: list of (key, label, active_bool). Returns (panel_rect, row_rects)."""
    panel_rect, row_rects = panel_layout(rows, origin, width)

    panel = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
    panel.fill(PANEL_BG)
    pygame.draw.rect(panel, BORDER, panel.get_rect(), 1, border_radius=8)
    screen.blit(panel, origin)
    screen.blit(title_font.render(title, True, TITLE), (origin[0] + PAD, origin[1] + PAD))

    chip_w = 36
    for (key, label, active), rrect in zip(rows, row_rects):
        hover = mouse_pos is not None and rrect.collidepoint(mouse_pos)
        if active:
            pygame.draw.rect(screen, ROW_ON, rrect, border_radius=5)
        elif hover:
            pygame.draw.rect(screen, ROW_HOVER, rrect, border_radius=5)

        chip = pygame.Rect(rrect.x + 4, rrect.y + 4, chip_w, rrect.height - 8)
        pygame.draw.rect(screen, KEY_BG_ON if active else KEY_BG, chip, border_radius=4)
        ktxt = font.render(key, True, KEY_FG_ON if active else KEY_FG)
        screen.blit(ktxt, (chip.x + (chip_w - ktxt.get_width()) // 2,
                           chip.y + (chip.height - ktxt.get_height()) // 2))
        ltxt = font.render(label, True, LABEL_ON if active else LABEL)
        screen.blit(ltxt, (chip.right + 10, rrect.y + (rrect.height - ltxt.get_height()) // 2))

    return panel_rect, row_rects


SLIDER_TRACK = (44, 50, 64)
SLIDER_FILL = (255, 210, 90)
SLIDER_KNOB = (245, 246, 250)


def slider_layout(origin, width=300, height=46):
    panel = pygame.Rect(origin[0], origin[1], width, height)
    track = pygame.Rect(origin[0] + PAD, origin[1] + height - 18, width - 2 * PAD, 6)
    return panel, track


def value_from_x(track, x):
    return min(1.0, max(0.0, (x - track.x) / max(track.width, 1)))


def draw_slider(screen, font, title, value, origin, width=300, height=46):
    panel, track = slider_layout(origin, width, height)
    bg = pygame.Surface(panel.size, pygame.SRCALPHA)
    bg.fill(PANEL_BG)
    pygame.draw.rect(bg, BORDER, bg.get_rect(), 1, border_radius=8)
    screen.blit(bg, origin)
    screen.blit(font.render(title, True, LABEL), (origin[0] + PAD, origin[1] + 8))
    pygame.draw.rect(screen, SLIDER_TRACK, track, border_radius=3)
    fill = pygame.Rect(track.x, track.y, int(track.width * value), track.height)
    pygame.draw.rect(screen, SLIDER_FILL, fill, border_radius=3)
    kx = track.x + int(track.width * value)
    pygame.draw.circle(screen, SLIDER_KNOB, (kx, track.centery), 8)
    return panel, track


def draw_status(screen, font, lines, origin, width=300):
    height = PAD * 2 + len(lines) * 20
    panel = pygame.Surface((width, height), pygame.SRCALPHA)
    panel.fill(PANEL_BG)
    pygame.draw.rect(panel, BORDER, panel.get_rect(), 1, border_radius=8)
    screen.blit(panel, origin)
    for i, line in enumerate(lines):
        screen.blit(font.render(line, True, LABEL), (origin[0] + PAD, origin[1] + PAD + i * 20))
