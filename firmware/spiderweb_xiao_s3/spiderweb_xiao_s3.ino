// Spider-web driver for the Seeed XIAO ESP32-S3.
//
// Full-duplex over USB serial:
//   host -> board : Adalight LED frames   ('A''d''a' count_hi count_lo chk RGB...)
//   board -> host : per-ring capacitance  ('S''n'  count  chk  uint16[count] LE)
//
// The board is a dumb pixel-pusher AND a ring capacitance sensor. All the
// animation / sound logic lives in the FastAPI host (`run.py serve --serial ...`).
//
// Wiring (XIAO ESP32-S3 silkscreen label -> GPIO):
//   * SK6805 DIN -> LED_PIN. Use a pin NOT used for touch. Here D10 = GPIO9.
//     (Level-shift the 3.3V data to 5V for long runs / many LEDs.)
//   * One conductive thread per ring -> a touch-capable GPIO (T1..T14 = GPIO1..14).
//     Here: D0=GPIO1 (inner), D1=GPIO2, D2=GPIO3, D3=GPIO4 (outer).
//     RING_PINS[0] is the innermost ring, increasing outward -- the order MUST
//     match the host's rings (centre/inner first).
//   * LED 5V + GND from a supply sized for the strip; tie its GND to the XIAO GND.
//
// Library: Adafruit NeoPixel (install via Library Manager).

#include <Adafruit_NeoPixel.h>

// ---- configuration -------------------------------------------------------
#define LED_PIN   9          // SK6805 DIN (XIAO D10 = GPIO9; keep off the touch pins)
#define NUM_LEDS  25         // 1 centre + 4 rings x 6 spokes (match config/web.json)
#define BAUD      921600     // must match the host --baud

// Touch GPIOs, innermost ring first. One entry per physical sensor ring.
// (4 rings for the current build; add/remove to match the install.)
const uint8_t RING_PINS[] = {1, 2, 3, 4};   // XIAO D0, D1, D2, D3
const uint8_t NUM_RINGS   = sizeof(RING_PINS) / sizeof(RING_PINS[0]);

#define SENSOR_INTERVAL_MS 25      // how often to stream a sensor frame
#define TOUCH_EMA          0.30f   // smoothing on each ring reading

Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);

static uint8_t  frame[NUM_LEDS * 3];
static float    ring_val[16];
static uint32_t last_sensor = 0;

enum State { WAIT_A, WAIT_D, WAIT_A2, READ_HI, READ_LO, READ_CHK, READ_DATA };

void setup() {
  strip.begin();
  strip.setBrightness(255);
  strip.clear();
  strip.show();
  Serial.begin(BAUD);
  for (uint8_t i = 0; i < NUM_RINGS; i++) ring_val[i] = 0.0f;
}

// Read + smooth each ring's capacitance, then stream one 'S''n' frame.
void sendSensors() {
  uint8_t buf[3 + 16 * 2];
  uint8_t n = NUM_RINGS;
  buf[0] = 'S';
  buf[1] = 'n';
  buf[2] = n;
  buf[3] = n ^ 0x55;
  for (uint8_t i = 0; i < n; i++) {
    uint32_t raw = touchRead(RING_PINS[i]);          // larger when approached/touched
    ring_val[i] += TOUCH_EMA * ((float)raw - ring_val[i]);
    uint16_t v = (ring_val[i] > 65535.0f) ? 65535 : (uint16_t)ring_val[i];
    buf[4 + i * 2]     = v & 0xFF;                    // little-endian
    buf[4 + i * 2 + 1] = (v >> 8) & 0xFF;
  }
  Serial.write(buf, 4 + n * 2);
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

  uint32_t now = millis();
  if (now - last_sensor >= SENSOR_INTERVAL_MS) {
    last_sensor = now;
    sendSensors();
  }
}
