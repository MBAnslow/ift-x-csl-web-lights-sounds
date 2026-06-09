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

    p_gen = sub.add_parser("gen", help="generate a sample radial web and save it")
    p_gen.add_argument("--spokes", type=int, default=8)
    p_gen.add_argument("--rings", type=int, default=7)

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
        dream.run(args.config, args.serial, args.baud, args.brightness, args.fps)
    elif args.cmd == "gen":
        from spiderweb import webgen
        web = webgen.radial_web(spokes=args.spokes, rings=args.rings)
        web.save(args.config)
        print(f"Wrote {web.num_leds} LEDs / {len(web.strands)} strands -> {args.config}")
    elif args.cmd == "ports":
        from spiderweb.serial_link import available_ports
        ports = available_ports()
        print("\n".join(ports) if ports else "no serial ports found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
