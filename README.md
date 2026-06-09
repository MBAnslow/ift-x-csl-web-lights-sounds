# Spider-web LED

Map addressable LEDs (SK6805 / WS2812-compatible) into a 2D spider-web layout
and drive them with **spatial events** — ripples, moving blobs, overlap regions,
and signals that propagate along the web's strands.

The "brain" runs in Python on your computer. It renders frames and streams them
over USB serial to an ESP32, which is a thin pixel-pusher for the LED chain.

```
 you  ->  Python engine (2D space, events)  -->  USB serial  -->  ESP32  -->  SK6805 chain
```

## Install

Use a virtual environment (the on-screen controls need a pygame build with the
`font` module, which the system Python 3.14 lacks — Python 3.12/3.13 works):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then run everything through the venv. Either prefix each command:

```bash
.venv/bin/python run.py editor
```

…or activate the environment once per shell and use plain `python`:

```bash
source .venv/bin/activate     # deactivate to leave
python run.py editor
```

The rest of this README uses `.venv/bin/python` so the commands work without
activating.

## 1. Lay out the web

Open the editor and place LEDs where they sit on your physical web, then draw
the strands between them.

```bash
.venv/bin/python run.py editor
```

- `L` place an LED node, `N` place a plain (no-LED) node
- `T` toggle: click any node to enable/disable its LED
- `E` edge mode: click two nodes to connect a strand
- `M` move nodes, `D` delete nodes/strands
- `G` drop in a sample radial orb-web to start from
- `S` save to `config/web.json`

The controls are shown on-screen and the active mode is highlighted.

LED chain index is assigned in the order you place LED nodes, so **place them in
the order they're wired** on the strip (or re-place to match). The number drawn
next to each LED is its chain index.

Prefer a head start? Generate a sample web:

```bash
.venv/bin/python run.py gen --spokes 8 --rings 7
```

## 2. Preview the events

```bash
.venv/bin/python run.py sim
```

Interactions are **edge-based**: hover the canvas to highlight the nearest
strand, then click to fire the active tool from that edge. Which LEDs light
depends on the edge — its endpoint lights and their neighbours.

- `1` propagate — distance — wavefront grows from the clicked edge's endpoints, timed by physical strand length
- `2` propagate — hops — same, but timed by edge count, so every node-to-node step is uniform regardless of geometry
- `3` overlap — lights the edge's endpoint lights and their k-hop neighbours along the strands; disabled LEDs are skipped, so neighbours route to the nearest *enabled* node. The **Overlap reach** slider sets k (1–4 hops).
- `4` charge & release (press and hold an edge) — it brightens the longer you hold, then releases a brighter propagation wave on mouse-up
- `SPACE` cycle colour
- `A` cycle ambient mode — `off / shimmer / breathe / twinkle / wander / rainbow`
- **Trail slider** — how long a light stays on after it's activated (0 = snap off, up to 5 s).
- **Distant glow slider** — how brightly *distant* lights respond to a single click. The edge's own lights always light fully; farther lights scale by `falloff^hops`, so at a low setting they barely glow and **repeated clicks build them up** (contributions add). Applies to overlap, charge and propagate. (The charge *release* always discharges at full brightness.)
- `X` clear, `ESC` quit

## 2b. Dream-catcher mode

```bash
.venv/bin/python run.py dream
```

Same web, but some nodes are **beads** with their own colour. When a ripple
reaches a bead it flares brighter, and the light flowing *past* it onto the
nodes downstream takes on a share of the bead's colour. Ripples that cross
several beads blend their colours, so the whole web mixes.

Where two ripples meet, their colours **average** (intensity-weighted) rather
than just adding — so a red and a blue wave crossing read as purple, like two
signals combining.

- `1` / `2` ripple — distance / hops — click an edge to send a colour-mixing ripple
- `3` overlap — click an edge to light its neighbourhood (also mixes bead colour along the path)
- `4` bead — click a node to cycle its bead colour (cycles through the palette, then off)
- `R` shuffle beads — randomise how many nodes are beads, which ones, and their colours
- `C` calibrate signal — step through background → hover → touch to set the threshold (see below)
- `SPACE` ripple colour, `A` ambient, `X` clear ripples (keeps beads), `ESC` quit
- **Trail slider** — afterglow, so colours linger and overlap
- **Bead colour mix slider** — how strongly beads tint the light passing through
- **Signal decay slider** — half-life of a ripple's strength over time; lower values make the signal die out quickly (only nearby lights respond), higher values let it carry across the web
- **Overlap reach / emission sliders** — how far an overlap spreads (active-node hops) and how strongly it falls off per hop
- **Click threshold slider** — the signal level at which an edge registers a touch
- **Ambient gain / Hover gain sliders** — amplify the idle ambient and the hover response independently; both stay normalised to the bands, so they only change how the basic look reads *within* the thresholds

Beads start in a random arrangement (and are drawn as coloured rings); hit `R`
to reshuffle.

**Capacitive signal + threshold.** The real installation never gives a precise
hand position — only a **changing value per edge**. Each strand carries its own
signal that = ever-present background noise + a broad rise while a hand hovers
near + a spike while it's touched. The firmware decides a *click* by
**thresholding** that value (with hysteresis so noise near the line doesn't
chatter). The sim mirrors this exactly:

- The strands tint with their live signal (brighter = closer to triggering), and
  the **signal meter** in the sidebar shows the live level split into three
  bands — **noise / hover / touch**. Drag the two boundary handles right on the
  meter to set, live against whatever the data is doing, what counts as noise,
  what counts as hovering, and where a touch (click) fires.
- The cursor is the "hand": moving over the web raises the broad hover signal
  (warming nearby nodes *before* a touch); pressing injects a touch spike on the
  nearest edge. A press only fires an effect **if the signal crosses the
  threshold** — so a too-high threshold ignores touches and a too-low one
  false-triggers on hover. Drag a touch across strands to fire several.
- **Calibrate** with `C`: press once to arm, then capture three levels —
  *background* (hands clear), *hover* (hold a hand near), *touch* (hold a touch).
  The threshold is set automatically midway between the hover and touch ranges.
  Fine-tune afterwards with the **Click threshold** slider.

Where ripples meet, their colours **average** (intensity-weighted) like
combining signals.

Propagation timing counts **active-node hops only** — deactivated lights are
skipped entirely, so two lights the same number of live steps from the source
light at the same time regardless of any dead nodes between them.

## 3. Drive the hardware

Flash `firmware/spiderweb_esp/spiderweb_esp.ino` to the ESP32 (Arduino IDE +
Adafruit NeoPixel library). Set `LED_PIN`, `NUM_LEDS`, and `BAUD` to match.

Find the port, then stream:

```bash
.venv/bin/python run.py ports
.venv/bin/python run.py sim --serial /dev/tty.usbserial-XXXX --baud 921600
```

Everything you do in the simulator is sent live to the LEDs.

## Scripting your own events

The engine is plain Python — drive it headlessly without the pygame window:

```python
from spiderweb.web import Web
from spiderweb.engine import Engine
from spiderweb.events import Ripple, Propagate
from spiderweb.serial_link import SerialLink
import time

web = Web.load("config/web.json")
engine = Engine(web, brightness=0.8)

with SerialLink("/dev/tty.usbserial-XXXX") as link:
    t0 = time.time()
    engine.add(Ripple((450, 350), color=(0.2, 0.6, 1.0), start=0))
    engine.add(Propagate([0, 1], color=(1, 0.8, 0.2), start=0.5))  # from an edge
    while True:
        t = time.time() - t0
        link.send(engine.frame_bytes(t))
        time.sleep(1 / 60)
```

Each event maps a point/shape/wavefront in the same 2D space as the LEDs onto a
per-LED colour, so "things overlapping the space" naturally ripple across
whatever LEDs they touch. See `spiderweb/events.py` to add your own.

## Layout

```
spiderweb/
  web.py          web model: nodes, LEDs, strands, JSON, edges, graph distances
  events.py       Ripple, Charge, Overlap, Propagate, Ambient
  engine.py       composites active events into a per-LED RGB buffer
  serial_link.py  Adalight-style framed serial output
  editor.py       interactive layout editor
  simulator.py    live preview + serial streaming (edge-based tools)
  dream.py        dream-catcher mode: beads tint & mix the light
  webgen.py       sample radial orb-web
  cli.py          `editor | sim | dream | gen | ports`
firmware/spiderweb_esp/spiderweb_esp.ino   ESP32 + SK6805 reader
config/web.json   your saved layout
PROTOCOL.md       wire format
```
