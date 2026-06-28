// Pattern rendering. Every pattern is a pure function of synced time and the
// node's (x,y) position, so a single pulse can physically travel across the
// field once Milestone 2 wires in real coordinates. For Milestone 1 the node
// position is (0,0) and patterns render identically everywhere.
#pragma once

#include <Arduino.h>
#include <NeoPixelBus.h>
#include <math.h>

#include "beacon.h"

namespace patterns {

enum PatternId : uint16_t {
  PULSE = 0,        // slow global brightness pulse
  PALETTE_DRIFT = 1 // hue drift across a palette
};

// Convert microseconds to a 0..1 phase over `period_s` seconds.
inline float phase(int64_t t_us, float period_s) {
  double secs = (double)t_us / 1e6;
  double p = fmod(secs / period_s, 1.0);
  if (p < 0) p += 1.0;
  return (float)p;
}

// Slow breathing pulse: a smooth sine in white, gentle by design.
inline RgbwColor pulse(int64_t synced_us, uint8_t brightness, float x, float y) {
  // 4s breathing period. (x,y) shifts the phase so the field can ripple later.
  const float period_s = 4.0f;
  const float spatial = (x + y) * 0.05f;  // 0 for Milestone 1
  float p = phase(synced_us, period_s) + spatial;
  float s = 0.5f * (1.0f - cosf(2.0f * (float)M_PI * p));  // 0..1, smooth
  uint8_t w = (uint8_t)roundf(s * brightness);
  return RgbwColor(0, 0, 0, w);  // white channel only — calm, efficient
}

// Render one pattern into a NeoPixelBus strip.
template <typename StripT>
inline void render(StripT& strip, const Beacon& b, int64_t synced_us,
                   float x, float y) {
  switch (b.pattern_id) {
    case PULSE:
    default: {
      RgbwColor c = pulse(synced_us, b.brightness, x, y);
      for (uint16_t i = 0; i < strip.PixelCount(); i++) strip.SetPixelColor(i, c);
      break;
    }
  }
}

}  // namespace patterns
