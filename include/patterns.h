// Pattern rendering: turns the pure pattern math (pattern_math.h) into SK6812
// RGBW colors on a NeoPixelBus strip. The time/space behavior lives in pmath::
// so it can be unit-tested; this layer only maps intensity -> pixels.
#pragma once

#include <Arduino.h>
#include <NeoPixelBus.h>

#include "beacon.h"
#include "pattern_ids.h"  // PatternId enum + patternIsStatic (dependency-free)
#include "pattern_math.h"

namespace patterns {

// Uniform breathing pulse in the white channel — every node in unison.
inline RgbwColor pulse(int64_t synced_us, uint8_t brightness) {
  float s = pmath::pulseIntensity(synced_us, /*period_s*/ 4.0f, /*spatial*/ 0.0f);
  return RgbwColor(0, 0, 0, (uint8_t)lroundf(s * brightness));
}

// Position-aware sweep: a wave of brightness travels across the field, so the
// pulse physically moves from lantern to lantern. params let the conductor tune
// it live: params[0] = period in ms, params[1] = wavelength in hundredths of a
// coordinate unit (both fall back to sensible defaults when 0).
inline RgbwColor sweep(int64_t synced_us, uint8_t brightness, float x, float y,
                       const uint16_t params[4]) {
  float period_s = params[0] ? params[0] / 1000.0f : 4.0f;
  float wavelength = params[1] ? params[1] / 100.0f : 3.0f;
  float s = pmath::sweepIntensity(synced_us, x, period_s, wavelength);
  return RgbwColor(0, 0, 0, (uint8_t)lroundf(s * brightness));
}

// Smooth rainbow drift: each node's color sweeps through the full color wheel
// (red -> orange -> yellow -> green -> blue -> violet -> red) over period_s. With
// a nonzero spatial term the hue is offset by position, so the rainbow travels
// across the field; the default (0) cycles every ring in unison. Colors are fully
// saturated on the RGB channels (white channel unused).
//   params[0] = full-cycle period in ms              (default 8000)
//   params[1] = spatial hue offset, hundredths of a cycle per x unit (default 0)
inline RgbwColor paletteDrift(int64_t synced_us, uint8_t brightness, float x,
                              const uint16_t params[4]) {
  float period_s = params[0] ? params[0] / 1000.0f : 8.0f;
  float spatial = params[1] / 100.0f;  // 0 => unison
  float h = pmath::driftHue(synced_us, x, period_s, spatial);
  float r, g, b;
  pmath::hsvToRgb(h, /*s*/ 1.0f, /*v*/ 1.0f, r, g, b);
  return RgbwColor((uint8_t)lroundf(r * brightness), (uint8_t)lroundf(g * brightness),
                   (uint8_t)lroundf(b * brightness), 0);
}

// Worst-case power draw: every pixel lit full white on all four RGBW channels at
// `brightness`. Not a show pattern — it's the bench rig for measuring the per-node
// LED ceiling (step brightness to trace the draw curve). Any real pattern draws
// strictly less at the same brightness, so if SOLID fits the budget, all do.
inline RgbwColor solid(uint8_t brightness) {
  return RgbwColor(brightness, brightness, brightness, brightness);
}

// Steady solid color: a constant hue with NO time dependence, so the whole field
// holds one calm color and the LED draw is flat (no pulse). Fits the warm/gentle
// aesthetic and is the realistic-conservative pattern for power measurement (a
// steady draw isolates the radio duty-cycle from LED variation). RGB channels
// only (white off). Colors come from the host-tested pmath::hsvToRgb.
//   params[0] = hue in degrees, 0-359  (e.g. 30 = orange, 50 = amber/yellow)
//   params[1] = saturation in percent, 1-100; 0 falls back to 100 (full)
inline RgbwColor glow(uint8_t brightness, const uint16_t params[4]) {
  float h = (params[0] % 360) / 360.0f;
  uint16_t sp = params[1] ? (params[1] > 100 ? 100 : params[1]) : 100;
  float r, g, b;
  pmath::hsvToRgb(h, sp / 100.0f, 1.0f, r, g, b);
  return RgbwColor((uint8_t)lroundf(r * brightness), (uint8_t)lroundf(g * brightness),
                   (uint8_t)lroundf(b * brightness), 0);
}

// Render one pattern into a NeoPixelBus strip (all pixels share one color for
// these 16-pixel rings; per-pixel spatial effects can come later).
template <typename StripT>
inline void render(StripT& strip, const BeaconMsg& b, int64_t synced_us, float x,
                   float y) {
  RgbwColor c;
  switch (b.pattern_id) {
    case SWEEP:
      c = sweep(synced_us, b.brightness, x, y, b.params);
      break;
    case PALETTE_DRIFT:
      c = paletteDrift(synced_us, b.brightness, x, b.params);
      break;
    case SOLID:
      c = solid(b.brightness);
      break;
    case GLOW:
      c = glow(b.brightness, b.params);
      break;
    case PULSE:
    default:
      c = pulse(synced_us, b.brightness);
      break;
  }
  for (uint16_t i = 0; i < strip.PixelCount(); i++) strip.SetPixelColor(i, c);
}

}  // namespace patterns
