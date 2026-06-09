// Spider-web LED driver for ESP32 + SK6805 (WS2812-compatible).
//
// Reads an Adalight-style framed protocol from USB serial and pushes each
// frame to the LED chain. The host (Python `run.py sim --serial ...`) owns all
// the animation logic; this sketch is just a fast, dumb pixel pusher.
//
// Wire on the host:
//   'A' 'd' 'a'  count_hi count_lo  checksum   <R G B per LED ...>
//   count = NUM_LEDS - 1,  checksum = count_hi ^ count_lo ^ 0x55
//
// Library: Adafruit NeoPixel (install via Library Manager).

#include <Adafruit_NeoPixel.h>

// ---- configuration -------------------------------------------------------
#define LED_PIN   5          // data pin to the SK6805 DIN
#define NUM_LEDS  30         // must match your web's LED count
#define BAUD      921600     // must match the host --baud

// SK6805 uses GRB ordering at 800 KHz, same as WS2812.
Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);

static uint8_t frame[NUM_LEDS * 3];

enum State { WAIT_A, WAIT_D, WAIT_A2, READ_HI, READ_LO, READ_CHK, READ_DATA };

void setup() {
  strip.begin();
  strip.setBrightness(255);
  strip.clear();
  strip.show();
  Serial.begin(BAUD);
}

void loop() {
  static State st = WAIT_A;
  static uint16_t count = 0;
  static uint32_t idx = 0;
  static uint8_t hi = 0, lo = 0;

  while (Serial.available() > 0) {
    uint8_t b = (uint8_t)Serial.read();
    switch (st) {
      case WAIT_A:    st = (b == 'A') ? WAIT_D  : WAIT_A; break;
      case WAIT_D:    st = (b == 'd') ? WAIT_A2 : WAIT_A; break;
      case WAIT_A2:   st = (b == 'a') ? READ_HI : WAIT_A; break;
      case READ_HI:   hi = b; st = READ_LO;  break;
      case READ_LO:   lo = b; st = READ_CHK; break;
      case READ_CHK:
        if (b == (uint8_t)(hi ^ lo ^ 0x55)) {
          count = ((uint16_t)hi << 8 | lo) + 1;
          if (count > NUM_LEDS) count = NUM_LEDS;
          idx = 0;
          st = (count > 0) ? READ_DATA : WAIT_A;
        } else {
          st = WAIT_A;  // bad header, resync
        }
        break;
      case READ_DATA:
        frame[idx++] = b;
        if (idx >= (uint32_t)count * 3) {
          for (uint16_t i = 0; i < count; i++) {
            strip.setPixelColor(i, frame[i * 3], frame[i * 3 + 1], frame[i * 3 + 2]);
          }
          strip.show();
          st = WAIT_A;
        }
        break;
    }
  }
}
