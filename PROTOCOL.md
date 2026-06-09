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
