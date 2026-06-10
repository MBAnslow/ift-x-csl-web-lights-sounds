"""Command-line entry point."""
from __future__ import annotations

import argparse

DEFAULT_CONFIG = "config/web.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="spiderweb", description="Spider-web LED control")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="path to web JSON")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("editor", help="place LEDs and draw web strands")

    p_sim = sub.add_parser("sim", help="preview events / stream to ESP")
    p_sim.add_argument("--serial", default=None, help="serial port, e.g. /dev/tty.usbserial-XXXX")
    p_sim.add_argument("--baud", type=int, default=921600)
    p_sim.add_argument("--brightness", type=float, default=1.0)
    p_sim.add_argument("--fps", type=int, default=60)

    p_dream = sub.add_parser("dream", help="dream-catcher mode: beads tint & mix the light")
    p_dream.add_argument("--serial", default=None, help="serial port, e.g. /dev/tty.usbserial-XXXX")
    p_dream.add_argument("--baud", type=int, default=921600)
    p_dream.add_argument("--brightness", type=float, default=1.0)
    p_dream.add_argument("--fps", type=int, default=60)
    p_dream.add_argument("--rings", type=int, default=4,
                         help="physical capacitance rings to read from the device")

    p_gen = sub.add_parser("gen", help="generate a sample radial web and save it")
    p_gen.add_argument("--spokes", type=int, default=8)
    p_gen.add_argument("--rings", type=int, default=7)
    p_gen.add_argument("--dr", type=float, default=110.0,
                       help="radial spacing between rings (bigger = larger web)")

    p_serve = sub.add_parser(
        "serve", help="FastAPI backend: ring capacitance -> lights + sound")
    p_serve.add_argument("--serial", default=None,
                         help="serial port of the XIAO ESP32-S3 (omit for simulated)")
    p_serve.add_argument("--baud", type=int, default=921600)
    p_serve.add_argument("--rings", type=int, default=4,
                         help="number of physical sensor rings (0 = auto from geometry)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("ports", help="list available serial ports")

    args = parser.parse_args(argv)

    if args.cmd == "editor":
        from spiderweb import editor
        editor.run(args.config)
    elif args.cmd == "sim":
        from spiderweb import simulator
        simulator.run(args.config, args.serial, args.baud, args.brightness, args.fps)
    elif args.cmd == "dream":
        from spiderweb import dream
        dream.run(args.config, args.serial, args.baud, args.brightness, args.fps,
                  args.rings)
    elif args.cmd == "gen":
        from spiderweb import webgen
        web = webgen.radial_web(spokes=args.spokes, rings=args.rings, dr=args.dr)
        web.save(args.config)
        print(f"Wrote {web.num_leds} LEDs / {len(web.strands)} strands -> {args.config}")
    elif args.cmd == "serve":
        import uvicorn
        from spiderweb import server
        server.configure(args.config, args.serial, args.baud, args.rings)
        print(f"serving on http://{args.host}:{args.port}  "
              f"(serial={args.serial or 'simulated'}, rings={args.rings or 'auto'})")
        uvicorn.run(server.app, host=args.host, port=args.port, log_level="info")
    elif args.cmd == "ports":
        from spiderweb.serial_link import available_ports
        ports = available_ports()
        print("\n".join(ports) if ports else "no serial ports found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
