# Serial protocol (host -> ESP)

Adalight-style framing. One frame per rendered LED frame.

```
byte  0 : 'A'  (0x41)
byte  1 : 'd'  (0x64)
byte  2 : 'a'  (0x61)
byte  3 : count_hi      high byte of (NUM_LEDS - 1)
byte  4 : count_lo      low  byte of (NUM_LEDS - 1)
byte  5 : checksum      count_hi XOR count_lo XOR 0x55
byte  6.. : payload      NUM_LEDS * 3 bytes, R,G,B per LED, in chain order
```

- LED order is the SK6805 chain order, i.e. the `index` field in `config/web.json`.
- Colour values are 0..255. The host applies brightness + gamma before sending,
  so the ESP writes bytes straight to the strip.
- The 6-byte header lets the ESP resynchronise: it scans for `A d a` and
  validates the checksum before reading a fixed-length payload, so RGB bytes can
  never be mistaken for a header.

Default baud is `921600`. Keep `--baud` (host) and `BAUD` (firmware) in sync.

This framing is Adalight-compatible, so existing Adalight tooling also works if
you ever want it.

# Serial protocol (ESP -> host) — per-ring capacitance

The webbing senses capacitance **per ring**. The ESP streams one sensor frame
periodically (e.g. every 20–30 ms) on the same serial link, in the opposite
direction to the LED frames. It is independently framed so the two streams
never collide.

```
byte  0 : 'S'  (0x53)
byte  1 : 'n'  (0x6E)
byte  2 : count        number of rings R (0 = centre, increasing outward)
byte  3 : checksum     count XOR 0x55
byte  4.. : payload     R * 2 bytes, one uint16 per ring, LITTLE-ENDIAN
```

- Each uint16 is the raw/averaged capacitance reading for that ring. Scale is
  arbitrary; the host calibrates per ring (noise floor / hover / touch) and maps
  the value into a 0..1 intensity, then decides touches by threshold.
- Ring order matches `Web.node_rings()` on the host: ring 0 is the centre, ring
  `R-1` the outermost.
- The host scans for `S n`, validates the checksum, then reads `2 * count`
  payload bytes.

The host (`run.py serve`) writes LED frames and reads sensor frames on the same
port. With no device attached it falls back to a simulated sensor source so the
pipeline (lights + sound) still runs for development.
