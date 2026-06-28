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

// Square-wave heartbeat: ON for the first half_period_us of each full cycle, OFF
// for the second. Driven by synced time, so every node that agrees on the clock
// agrees on the blink — two boards blink in unison iff they are in sync.
inline bool heartbeatOn(int64_t synced_us, int64_t half_period_us) {
  return (floorDiv(synced_us, half_period_us) % 2) == 0;
}

}  // namespace pmath
