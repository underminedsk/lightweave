// Pattern identifiers + per-pattern facts, dependency-free (no Arduino, no LED
// library) so host-side logic can reason about patterns. patterns.h (the LED
// binding) includes this; the enum values are wire format (BeaconMsg.pattern_id)
// and must never be renumbered.
#pragma once

#include <stdint.h>

namespace patterns {

enum PatternId : uint16_t {
  PULSE = 0,         // uniform slow breathing pulse (all nodes in unison)
  PALETTE_DRIFT = 1, // smooth rainbow hue cycle (optionally traveling by position)
  SWEEP = 2,         // brightness wave that travels across the field by position
  SOLID = 3,         // every pixel full RGBW at `brightness` — the worst-case
                     // power draw, for bench-measuring the per-node ceiling
  GLOW = 4           // steady solid color at a fixed hue (no time term): the
                     // field holds one calm warm color, flat (non-pulsing) draw
};

// True when f(x,y,t) has no time term: the rendered color never changes until
// the recipe does, so the LEDs (which latch their last color) don't need to be
// re-rendered every frame. Stage B uses this to let the CPU sleep through whole
// radio-off spans on calm scenes instead of waking ~30x/second to redraw the
// same pixels. An unknown/future pattern id must return false (assume animated —
// the safe direction: it only costs power, never a frozen show).
inline bool patternIsStatic(uint16_t pattern_id) {
  return pattern_id == SOLID || pattern_id == GLOW;
}

}  // namespace patterns
