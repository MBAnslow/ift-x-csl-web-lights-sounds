# Spider-web LED

This project started as a workshop demo for a physical spider-web light/sound
instrument. The web could be touched and "played" like an instrument, with the
software turning those interactions into responsive light and audio.

It maps addressable LEDs (SK6805 / WS2812-compatible) into a 2D spider-web
layout and drives them with **spatial events** — ripples, moving blobs, overlap
regions, and signals that propagate along the web's strands.

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

## 2. Play the web (dream-catcher)

```bash
.venv/bin/python run.py dream
```

This is the main interactive app — the web reacts to your touch with **light and
sound**. Interactions are **edge-based**: hover the canvas to highlight the
nearest strand, then click to fire the active tool from that edge.

Some nodes of the web are **beads** with their own colour. When a ripple
reaches a bead it flares brighter, and the light flowing *past* it onto the
nodes downstream takes on a share of the bead's colour. Ripples that cross
several beads blend their colours, so the whole web mixes.

Each bead colour is also a different **lens** on the ripple travelling through
it. No bead ever stops the signal — it is always passed on somehow:

- **fast** (amber) — speeds the wave up on every strand beyond it
- **funnel** (vivid green) — sends the signal on along a **single** direction only, in a super-saturated, extra-bright version of its colour
- **bounce** (magenta) — duplicates the signal back the way it came

So a single touch can race through a fast cluster, get funnelled into one bright
beam, and throw return ripples back toward where it started — all in the same
web.

Where two ripples meet, their colours **average** (intensity-weighted) rather
than just adding — so a red and a blue wave crossing read as purple, like two
signals combining.

- `1` ripple — click an edge to send a colour-mixing ripple through the web
- `2` area — click an edge to light its neighbourhood (also mixes bead colour along the path)
- `3` bead — click a node to cycle its bead **type** (fast → funnel → bounce → off)
- `R` shuffle beads — randomise how many nodes are beads, which ones, and their types
- `C` calibrate signal — step through background → hover → touch to set the threshold (see below)
- `S` sound — toggle the responsive soundscape (see below)
- `SPACE` ripple colour, `A` ambient, `X` clear ripples (keeps beads), `ESC` quit
- **Trail slider** — afterglow, so colours linger and overlap
- **Bead colour mix slider** — how strongly beads tint the light passing through
- **Signal decay slider** — half-life of a ripple's strength over time; lower values make the signal die out quickly (only nearby lights respond), higher values let it carry across the web
- **Area reach / emission sliders** — how far the Area tool spreads (active-node hops) and how strongly it falls off per hop
- **Signal meter (noise / hover / touch handles)** — drag the two boundary handles on the live meter to set the signal levels that count as noise, hovering, and a touch (see below); `C` calibrates them automatically
- **Ambient gain / Hover gain sliders** — amplify the idle ambient and the hover response independently; both stay normalised to the bands, so they only change how the basic look reads *within* the thresholds
- **Volume slider** — master level of the responsive soundscape
- **Drone / Chime volume sliders** — trim the bead chord drone and the ring-touch chimes independently
- **Base bead chime slider** — the steady resonance the beads hold (their always-on glow) even when no ripple is washing over them; raising it keeps the chord drone humming, lowering it lets the beads fall quiet between touches
- **Chord dropdown** — beads are the tones of a chord; pick the chord *quality* here. The options depend on how many beads there are (3 beads → triads like maj/min/sus, 4 → 7ths, 5 → 9ths, 6 → 11ths, 7 → 13ths; other counts fall back to a diatonic stack). Beads are ordered centre→outer = low→high, so the centre bead is the chord root.

### Beads as a chord drone

The beads are the tones of a sustained **chord drone** in the background. Its
loudness tracks **web activation**: a faint floor while only the ambient is
running, swelling louder as ripples (vibrations/activations) energise the web —
bigger/stronger ripples make the chord drone louder. This is *separate* from the
ring chimes below. Pick the chord quality from the dropdown (its choices track
the bead count), order is centre→outer = low→high, and reshuffle beads with `R`.

### Responsive soundscape

On top of the light, the dream-catcher drives a live ambient **soundscape**
(inspired by [Polpii/sensitive-webs](https://github.com/Polpii/sensitive-webs)).
It's deliberately decoupled from the LEDs — it only listens to the same
capacitive **signal**, not the nodes:

- a continuous **bed** (low drone + airy noise + a soft chord) always plays so
  there's never silence;
- **hover intensity** (how far the signal sits up the hover band) opens the
  timbre — brighter drone, louder shimmer/air, busier chime trickle;
- a **ring touch** (a threshold crossing on a ring edge — never an axis/spoke
  edge) rings a **chime**; outer rings ring higher, and it's fully polyphonic, so
  many chimes overlap freely;
- the **bead chord drone** (see above) sits underneath, swelling with activation;
- toggle it with `S`, set the level with the **Volume** slider.

Audio uses `sounddevice` (PortAudio). If it (or an output device) isn't
available the simulator runs silently — the button shows `Sound: (no device)`.

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
  Fine-tune afterwards by dragging the **noise / touch handles** on the meter.

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
.venv/bin/python run.py dream --serial /dev/tty.usbserial-XXXX --baud 921600
```

Everything you do in the dream-catcher is sent live to the LEDs.

> A lower-level event preview, `run.py sim`, also exists for testing raw
> propagate / overlap / charge events (no beads or sound). It streams to serial
> the same way with `--serial`.

## 4. Backend server (live installation)

For the real installation the webbing senses **capacitance per ring** and the
host turns that into light + sound. A FastAPI backend ties it together:

```
ESP32-S3  --USB serial-->  per-ring capacitance
                            |
                  RingProcessor (calibrate -> intensity / touch per ring)
                            |
        Engine (ring ripples, ring-tinted hover glow, ambient)  +  Soundscape
                            |
        gamma + brightness --USB serial--> SK6805 LEDs (same chain order as the UI)
```

Flash `firmware/spiderweb_xiao_s3/spiderweb_xiao_s3.ino` to the XIAO ESP32-S3
(it both drives the SK6805 strip *and* streams per-ring touch readings). Wire one
conductive thread per ring to a touch GPIO listed in `RING_PINS` (centre ring
first, to match `Web.node_rings()`), and set `LED_PIN` / `NUM_LEDS`.

Run the backend:

```bash
.venv/bin/python run.py serve --serial /dev/tty.usbserial-XXXX   # real device
.venv/bin/python run.py serve                                    # simulated (no board)
```

The install currently has **4 sensor rings** (`--rings 4`, the default). The web's
finer concentric geometry is collapsed into that many contiguous ring zones
(centre out) without touching the LED layout; pass `--rings 0` to use the web's
own ring count, or another number to match your hardware. The dashboard and
`RingProcessor` adapt automatically.

Then open <http://127.0.0.1:8000>. The dashboard mirrors the physical LEDs
one-for-one, shows the live per-ring signal, lets you **calibrate**
(background -> hover -> touch) and **simulate touches** per ring when no hardware
is attached.

REST / WebSocket API:

| route | what |
|-------|------|
| `GET /api/state` | device status, per-ring values/intensity, config |
| `GET /api/leds` | LED positions + ring index (chain order) |
| `GET /api/frame` | latest RGB buffer (flat list, chain order) |
| `POST /api/config` | patch brightness, hover/ambient gain, ripple speed/falloff, volume, sound on/off |
| `POST /api/calibrate` | advance the background/hover/touch calibration |
| `POST /api/sim/touch` | `{ring, intensity}` — inject a touch (simulated device) |
| `POST /api/sim/rings` | `{values:[...]}` — inject raw per-ring values |
| `WS /ws` | live `{state, rgb}` stream at ~30 fps |

A ring **touch** spawns a ripple from every LED on that ring (hop-based, with
per-hop falloff) and triggers that ring's chime; **hover** intensity tints those
ring's LEDs and drives the soundscape timbre. See `PROTOCOL.md` for the
bidirectional serial framing.

## 5. GitHub Pages main interface (standalone)

This repo ships a browser version of the dream interface in `docs/` and deploys
it with `.github/workflows/pages.yml`.

Enable Pages in GitHub (`Settings -> Pages -> Build and deployment -> GitHub
Actions`), then push to `main`. The workflow publishes the `docs/` folder.

The Pages app is standalone: it does **not** connect to hardware or require the
FastAPI backend. It runs local simulation only (edge ripples/area, bead cycling,
ambient modes, and trail/mix/decay controls) so it matches the main interaction
model without device I/O.

It loads its web geometry from `docs/web.json` (a snapshot of `config/web.json`).
If you update the physical layout, copy/regenerate `docs/web.json` before deploy.

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
  cli.py          `editor | dream | sim | gen | serve | ports`
firmware/spiderweb_esp/spiderweb_esp.ino   ESP32 + SK6805 reader
config/web.json   your saved layout
PROTOCOL.md       wire format
```
