// Pure pattern math — the time/space functions behind each pattern, with no
// dependency on the LED library. Patterns are f(x, y, t), so a pulse can travel
// across the physical field once real (x,y) coordinates arrive in Milestone 2.
// Kept separate from patterns.h so the wrap/continuity behavior is host-testable.
#pragma once

#include <math.h>
#include <stdint.h>

namespace pmath {

static constexpr float kPi = 3.14159265358979323846f;

// Map microseconds to a phase in [0,1) over `period_s` seconds. Continuous and
// monotonic within a period; wraps cleanly at the boundary (no visible hitch).
inline float phase(int64_t t_us, float period_s) {
  double secs = (double)t_us / 1e6;
  double p = fmod(secs / (double)period_s, 1.0);
  if (p < 0) p += 1.0;  // fmod keeps the sign of the dividend; force [0,1)
  return (float)p;
}

// Breathing pulse intensity in [0,1]: a smooth raised cosine. `spatial` shifts
// the phase per-node so the field can ripple; it is 0 for Milestone 1.
inline float pulseIntensity(int64_t synced_us, float period_s, float spatial) {
  float p = phase(synced_us, period_s) + spatial;
  return 0.5f * (1.0f - cosf(2.0f * kPi * p));
}

// Floored division: rounds toward negative infinity (unlike C's truncation), so
// the heartbeat parity is correct even if synced time briefly goes negative.
inline int64_t floorDiv(int64_t a, int64_t b) {
  int64_t q = a / b;
  if ((a % b != 0) && ((a < 0) != (b < 0))) q--;
  return q;
}

// Traveling-wave sweep: a brightness wave that moves across the field in +x, so
// a pulse physically travels from one lantern to the next. A node at position x
// sees the same waveform as a node at x=0, delayed by period_s * x / wavelength.
//   period_s    time for one full cycle of the wave
//   wavelength  spatial distance between successive wave peaks (same units as x)
// Returns intensity in [0,1] (raised cosine).
inline float sweepIntensity(int64_t synced_us, float x, float period_s,
                            float wavelength) {
  double secs = (double)synced_us / 1e6;
  double ph = secs / (double)period_s - (double)x / (double)wavelength;
  double p = ph - floor(ph);  // wrap to [0,1)
  return 0.5f * (1.0f - cosf(2.0f * kPi * (float)p));
}

// Rainbow hue for a color drift: cycles 0->1 over period_s (one full trip around
// the color wheel), offset by position so the field can show a *moving* rainbow.
//   period_s  time for one full hue cycle
//   spatial   hue offset in cycles per unit x (0 => every node shares one hue)
// Returns hue in [0,1), wrapping cleanly (hue 1 == hue 0 == red, so the wrap is
// seamless on the wheel).
inline float driftHue(int64_t synced_us, float x, float period_s, float spatial) {
  double secs = (double)synced_us / 1e6;
  double h = secs / (double)period_s + (double)x * (double)spatial;
  return (float)(h - floor(h));
}

// HSV -> RGB, all components in [0,1]. Standard six-sextant conversion; hue wraps
// so any real hue is valid. Kept pure (no LED type) so it is host-testable; the
// patterns layer scales the result into RGBW pixels.
inline void hsvToRgb(float h, float s, float v, float& r, float& g, float& b) {
  h -= floorf(h);  // wrap hue into [0,1)
  float hf = h * 6.0f;
  int i = (int)hf;  // sextant 0..5
  float f = hf - (float)i;
  float p = v * (1.0f - s);
  float q = v * (1.0f - f * s);
  float t = v * (1.0f - (1.0f - f) * s);
  switch (i % 6) {
    case 0:  r = v; g = t; b = p; break;  // red   -> yellow
    case 1:  r = q; g = v; b = p; break;  // yellow-> green
    case 2:  r = p; g = v; b = t; break;  // green -> cyan
    case 3:  r = p; g = q; b = v; break;  // cyan  -> blue
    case 4:  r = t; g = p; b = v; break;  // blue  -> magenta
    default: r = v; g = p; b = q; break;  // magenta-> red (case 5)
  }
}

// Square-wave heartbeat: ON for the first half_period_us of each full cycle, OFF
// for the second. Driven by synced time, so every node that agrees on the clock
// agrees on the blink — two boards blink in unison iff they are in sync.
inline bool heartbeatOn(int64_t synced_us, int64_t half_period_us) {
  return (floorDiv(synced_us, half_period_us) % 2) == 0;
}

}  // namespace pmath
