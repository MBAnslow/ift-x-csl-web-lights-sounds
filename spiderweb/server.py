"""FastAPI backend: capacitance in -> lights + sound out.

Pipeline (runs on the machine the XIAO ESP32-S3 is plugged into):

    ESP32  --USB serial-->  per-ring capacitance
                             |
                   RingProcessor  (calibrate, threshold -> intensity / touch)
                             |
        +--------------------+--------------------+
        |                                         |
   Engine (ripples from the touched ring,    Soundscape (hover intensity ->
   ring-tinted hover glow, ambient)          timbre, touch -> chime per ring)
        |
   gamma + brightness  --USB serial-->  SK6805 LEDs (same chain order as the UI)

With no board attached it falls back to a simulated sensor source so the whole
thing runs for development. The LED order is exactly `config/web.json`, so the
physical strip mirrors the interface one-for-one.
"""
from __future__ import annotations

import asyncio
import colorsys
import threading
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from spiderweb.device import SerialDevice, SimDevice, available_ports
from spiderweb.engine import Engine
from spiderweb.events import Ambient, Propagate
from spiderweb.rings import RingProcessor
from spiderweb.web import Web

try:
    from spiderweb.sound import Soundscape
except Exception:  # pragma: no cover - sound is optional
    Soundscape = None


def ring_color(r: int, num_rings: int) -> np.ndarray:
    """A stable hue per ring (centre warm -> outer cool)."""
    h = (0.08 + 0.62 * (r / max(num_rings - 1, 1))) % 1.0
    return np.array(colorsys.hsv_to_rgb(h, 0.85, 1.0))


class Config(BaseModel):
    brightness: float = 1.0
    gamma: float = 2.2
    fps: int = 60
    hover_gain: float = 0.6
    ambient_on: bool = True
    ambient_mode: str = "shimmer"
    ambient_gain: float = 0.5
    ripple_speed: float = 3.5      # hops/sec
    ripple_falloff: float = 0.78   # per-hop brightness falloff
    sound_on: bool = True
    volume: float = 0.7


class SimTouch(BaseModel):
    ring: int
    intensity: float = 1.1


class SimRings(BaseModel):
    values: list[float]


class CalStep(BaseModel):
    pass


class Runtime:
    """Owns the web, hardware link, processing, render loop and shared state."""

    def __init__(self, web_path: str, port: str | None, baud: int, num_rings: int = 0):
        self.web = Web.load(web_path)
        if self.web is None:
            raise RuntimeError(f"could not load web from {web_path}")
        self.cfg = Config()

        # ring geometry ---------------------------------------------------
        # The web's concentric geometry gives `geo_n` rings; the physical
        # installation may sense fewer. When `num_rings` is set we collapse the
        # geometric rings into that many contiguous sensor zones (centre out),
        # keeping the LED layout untouched.
        geo_ring_of, geo_n = self.web.node_rings()
        if num_rings and 0 < num_rings < geo_n:
            self.num_rings = num_rings
            ring_of = {nid: min(g * num_rings // geo_n, num_rings - 1)
                       for nid, g in geo_ring_of.items()}
        else:
            self.num_rings = geo_n
            ring_of = geo_ring_of
        leds = self.web.leds()
        self.led_ring = np.asarray([ring_of.get(n.id, 0) for n in leds], dtype=int)
        self.led_colors = np.stack([ring_color(r, self.num_rings) for r in self.led_ring]) \
            if len(leds) else np.zeros((0, 3))
        self.ring_nodes: dict[int, list[int]] = {r: [] for r in range(self.num_rings)}
        for n in leds:
            self.ring_nodes[ring_of.get(n.id, 0)].append(n.id)
        self.positions = np.asarray([[n.x, n.y] for n in leds], dtype=float) \
            if len(leds) else np.zeros((0, 2))

        # engine + processing + sound ------------------------------------
        self.engine = Engine(self.web, blend="add",
                             brightness=self.cfg.brightness, gamma=self.cfg.gamma)
        self.ambient = Ambient(mode=self.cfg.ambient_mode)
        self.engine.add(self.ambient)
        self.proc = RingProcessor(self.num_rings)

        self.sound = None
        if Soundscape is not None and self.cfg.sound_on:
            self.sound = Soundscape()
            self.sound.start()

        # hardware --------------------------------------------------------
        self.sim_rings = np.zeros(self.num_rings)  # used only by SimDevice
        self.sim_hold = np.zeros(self.num_rings)   # ticks a sim touch is held
        self.device = self._make_device(port, baud)

        # shared latest state (guarded by lock) --------------------------
        self.lock = threading.Lock()
        self.frame = np.zeros((len(leds), 3), dtype=np.uint8)
        self.t0 = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _make_device(self, port: str | None, baud: int):
        if port:
            try:
                return SerialDevice(port, baud, num_rings=self.num_rings)
            except Exception as e:  # noqa: BLE001
                print(f"[serve] serial open failed ({e!r}); using simulated device")
        return SimDevice(self.num_rings)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.device.close()
        except Exception:
            pass
        if self.sound is not None:
            self.sound.close()

    # -- the render/sense loop -------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            t = time.time() - self.t0
            cfg = self.cfg

            # 1. read sensors (real device or decaying sim values) --------
            if isinstance(self.device, SimDevice):
                self.sim_hold = np.maximum(self.sim_hold - 1, 0)
                # held rings stay pressed; everything else relaxes to baseline
                self.sim_rings = np.where(self.sim_hold > 0, self.sim_rings,
                                          self.sim_rings * 0.85)
                self.device.inject(self.sim_rings)
            raw = self.device.latest_rings()
            rising = self.proc.update(raw)

            # 2. ring touches -> ripples + chimes -------------------------
            for r in rising:
                nodes = self.ring_nodes.get(int(r), [])
                if nodes:
                    self.engine.add(Propagate(
                        nodes, color=tuple(ring_color(int(r), self.num_rings)),
                        metric="hops", speed=cfg.ripple_speed, multi=True,
                        dist_falloff=cfg.ripple_falloff, duration=4.0, gain=1.2,
                        start=t,
                    ))
                if self.sound is not None:
                    self.sound.trigger_ring(int(r), velocity=0.6 + 0.4 * self.proc.global_intensity)

            # 3. render: ambient + ripples + ring-tinted hover glow -------
            self.ambient.amplitude = 0.7 * cfg.ambient_gain if cfg.ambient_on else 0.0
            base = self.engine.update(t)
            if len(self.led_ring):
                glow = self.proc.intensity[self.led_ring][:, None] * self.led_colors
                base = base + glow * cfg.hover_gain
            v = np.clip(base, 0.0, 1.0) * cfg.brightness
            v = np.power(np.clip(v, 0.0, 1.0), cfg.gamma)
            frame = (v * 255.0 + 0.5).astype(np.uint8)

            # 4. push to LEDs + drive sound -------------------------------
            self.device.send_frame(frame)
            if self.sound is not None:
                self.sound.set_intensity(self.proc.global_intensity)
                self.sound.set_pan(0.5)  # no angular resolution from the rings
                self.sound.set_volume(cfg.volume if cfg.sound_on else 0.0)

            with self.lock:
                self.frame = frame

            time.sleep(max(1.0 / max(cfg.fps, 1), 0.001))

    # -- snapshots for the API -------------------------------------------
    def state(self) -> dict:
        with self.lock:
            frame = self.frame.copy()
        return {
            "connected": self.device.connected,
            "device": type(self.device).__name__,
            "num_leds": int(frame.shape[0]),
            "num_rings": self.num_rings,
            "rings": self.proc.state(),
            "config": self.cfg.model_dump(),
            "sound": bool(self.sound is not None and getattr(self.sound, "ok", False)),
        }

    def frame_list(self) -> list[int]:
        with self.lock:
            return self.frame.reshape(-1).tolist()

    def leds_meta(self) -> dict:
        return {
            "positions": self.positions.tolist(),
            "ring": self.led_ring.tolist(),
            "size": list(self.web.size),
            "num_rings": self.num_rings,
        }


rt: Runtime | None = None
_INIT = {"web": "config/web.json", "port": None, "baud": 921600, "rings": 4}


def configure(web: str, port: str | None, baud: int, rings: int = 4) -> None:
    _INIT.update(web=web, port=port, baud=baud, rings=rings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rt
    rt = Runtime(_INIT["web"], _INIT["port"], _INIT["baud"], _INIT["rings"])
    rt.start()
    yield
    rt.stop()


app = FastAPI(title="Spider-web backend", lifespan=lifespan)


@app.get("/api/state")
def get_state():
    return rt.state()


@app.get("/api/leds")
def get_leds():
    return rt.leds_meta()


@app.get("/api/frame")
def get_frame():
    return {"rgb": rt.frame_list()}


@app.get("/api/ports")
def get_ports():
    return {"ports": available_ports()}


@app.post("/api/config")
def set_config(patch: dict):
    cur = rt.cfg.model_dump()
    cur.update({k: v for k, v in patch.items() if k in cur})
    rt.cfg = Config(**cur)
    rt.engine.brightness = rt.cfg.brightness
    rt.engine.gamma = rt.cfg.gamma
    rt.ambient.mode = rt.cfg.ambient_mode
    if rt.sound is not None:
        rt.sound.set_volume(rt.cfg.volume if rt.cfg.sound_on else 0.0)
    return rt.cfg.model_dump()


@app.post("/api/calibrate")
def calibrate(_: CalStep | None = None):
    rt.proc.calibrate_step()
    return {"cal_step": rt.proc.cal_step}


@app.post("/api/sim/touch")
def sim_touch(t: SimTouch):
    if 0 <= t.ring < rt.num_rings and isinstance(rt.device, SimDevice):
        rt.sim_rings[t.ring] = float(t.intensity)
        rt.sim_hold[t.ring] = 18  # hold the press so it crosses the threshold
        return {"ok": True}
    return {"ok": False, "reason": "out of range or real device attached"}


@app.post("/api/sim/rings")
def sim_rings(s: SimRings):
    if isinstance(rt.device, SimDevice):
        v = np.asarray(s.values, dtype=float)
        n = min(v.size, rt.num_rings)
        rt.sim_rings[:n] = v[:n]
        return {"ok": True}
    return {"ok": False, "reason": "real device attached"}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        while True:
            await sock.send_json({"state": rt.state(), "rgb": rt.frame_list()})
            await asyncio.sleep(1 / 30)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/", response_class=HTMLResponse)
def index():
    return _DASHBOARD


_DASHBOARD = """<!doctype html><html><head><meta charset=utf-8>
<title>Spider-web</title>
<style>
 body{margin:0;background:#0a0c12;color:#cdd6e6;font:13px system-ui;display:flex}
 #side{width:260px;padding:14px;box-sizing:border-box;border-right:1px solid #1d2433}
 canvas{flex:1;display:block}
 h1{font-size:15px;margin:0 0 10px} .row{margin:6px 0}
 button{background:#1d2740;color:#cdd6e6;border:1px solid #2c3a5c;border-radius:6px;
   padding:6px 9px;cursor:pointer;margin:2px} button:hover{background:#27345a}
 label{display:block;margin:8px 0 2px;color:#8da0c0} input[type=range]{width:100%}
 #st{white-space:pre;font:11px ui-monospace;color:#7fa7d8;margin-top:10px}
</style></head><body>
<div id=side>
 <h1>Spider-web backend</h1>
 <div class=row><b>Calibrate</b><br><button onclick=cal()>next step</button> <span id=cs></span></div>
 <div class=row><b>Simulate touch</b><div id=rings></div></div>
 <label>Brightness <span id=bv></span></label><input id=br type=range min=0 max=1 step=0.01 value=1>
 <label>Hover gain <span id=hv></span></label><input id=hg type=range min=0 max=2 step=0.01 value=0.6>
 <label>Ambient gain <span id=av></span></label><input id=ag type=range min=0 max=1 step=0.01 value=0.5>
 <label>Volume <span id=vv></span></label><input id=vo type=range min=0 max=1 step=0.01 value=0.7>
 <div id=st></div>
</div>
<canvas id=c></canvas>
<script>
let meta=null;
async function cal(){const r=await fetch('/api/calibrate',{method:'POST'});const j=await r.json();document.getElementById('cs').textContent='step '+j.cal_step;}
function cfg(k,v){fetch('/api/config',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({[k]:v})});}
function touch(r){fetch('/api/sim/touch',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({ring:r,intensity:1.1})});}
br.oninput=e=>{cfg('brightness',+e.target.value);bv.textContent=(+e.target.value).toFixed(2)};
hg.oninput=e=>{cfg('hover_gain',+e.target.value);hv.textContent=(+e.target.value).toFixed(2)};
ag.oninput=e=>{cfg('ambient_gain',+e.target.value);av.textContent=(+e.target.value).toFixed(2)};
vo.oninput=e=>{cfg('volume',+e.target.value);vv.textContent=(+e.target.value).toFixed(2)};
const c=document.getElementById('c'),x=c.getContext('2d');
function resize(){c.width=c.clientWidth;c.height=c.clientHeight;}window.onresize=resize;resize();
fetch('/api/leds').then(r=>r.json()).then(m=>{meta=m;
 m.cx=m.size[0]/2;m.cy=m.size[1]/2;m.ringR=new Array(m.num_rings).fill(0);
 m.positions.forEach((p,i)=>{const k=m.ring[i];const d=Math.hypot(p[0]-m.cx,p[1]-m.cy);if(d>m.ringR[k])m.ringR[k]=d;});
 const d=document.getElementById('rings');for(let i=0;i<m.num_rings;i++){const b=document.createElement('button');b.textContent=i;b.onclick=()=>touch(i);d.appendChild(b);}});
function draw(rgb,state){
 if(!meta)return;const W=meta.size[0],H=meta.size[1];
 const s=Math.min(c.width/W,c.height/H);const ox=(c.width-W*s)/2,oy=(c.height-H*s)/2;
 x.fillStyle='#0a0c12';x.fillRect(0,0,c.width,c.height);
 const CX=ox+meta.cx*s,CY=oy+meta.cy*s,inten=state.rings.intensity||[],act=state.rings.active_ring;
 for(let k=0;k<meta.num_rings;k++){const rr=meta.ringR[k]*s;if(rr<=0)continue;
  const a=Math.min(0.12+0.7*(inten[k]||0)+(k===act?0.18:0),0.95);
  x.strokeStyle=`rgba(120,170,255,${a})`;x.lineWidth=(k===act?3.5:1.5);
  x.beginPath();x.arc(CX,CY,rr,0,7);x.stroke();}
 const R=Math.max(18,Math.min(c.width,c.height)/16),DR=R*0.32;
 meta.positions.forEach((p,i)=>{const r=rgb[i*3],g=rgb[i*3+1],b=rgb[i*3+2];
  const X=ox+p[0]*s,Y=oy+p[1]*s;const br=(r+g+b)/3;
  const grd=x.createRadialGradient(X,Y,0,X,Y,R);
  grd.addColorStop(0,`rgba(${r},${g},${b},${0.25+br/255*0.75})`);grd.addColorStop(1,'rgba(0,0,0,0)');
  x.fillStyle=grd;x.beginPath();x.arc(X,Y,R,0,7);x.fill();
  x.fillStyle=`rgb(${r},${g},${b})`;x.beginPath();x.arc(X,Y,DR,0,7);x.fill();});
 document.getElementById('st').textContent=
  'device: '+state.device+(state.connected?' (live)':'')+'\\nsound: '+state.sound+
  '\\nactive ring: '+state.rings.active_ring+'  global: '+state.rings.global+
  '\\nintensity: ['+state.rings.intensity.map(v=>v.toFixed(2)).join(', ')+']';
}
const proto=location.protocol==='https:'?'wss':'ws';
const sock=new WebSocket(proto+'://'+location.host+'/ws');
sock.onmessage=ev=>{const m=JSON.parse(ev.data);draw(m.rgb,m.state);};
</script></body></html>"""
