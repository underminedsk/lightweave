// Performer CPU nap scheduler — Milestone 3, Lever 1, Stage B.
//
// Stage A (powersave.h) turned the radio off between listen windows; after it the
// ~50 mA CPU/board floor is the biggest constant draw. This stage attacks that
// floor: while the radio is OFF anyway, the CPU light-sleeps between the moments
// it actually has work, instead of spinning delay(16) at ~60 Hz. The SK6812s
// latch their last color, so pixels hold through a nap; on static scenes (GLOW —
// no time term in f) there is no re-render to wake for at all, so naps stretch to
// the next radio window and the node idles near LED-only draw.
//
// Like powersave.h, this is deliberately dependency-free: napPlan() is a pure
// function that answers "how long may the CPU sleep right now?", and
// src/main.cpp owns the actual esp_light_sleep_start() call (plus the UART-wake
// and don't-sleep-mid-LED-transfer details). A nap must end at the EARLIEST of:
//   - the next radio duty transition (never sleep into a listen window),
//   - the next animation frame, for patterns with a time term (~30 fps),
//   - the next heartbeat LED edge (when the heartbeat is enabled), so the
//     zero-wiring sync proof stays honest,
//   - a hard safety cap, so no math bug can sleep a node for long.
// And never naps at all while the radio is on (ESP-NOW RX must stay hot through
// a listen window) or within a grace window of serial traffic (light sleep drops
// UART chars — a human provisioning over USB must win over power).
//
// All times are int64 microseconds (esp_timer_get_time on-device), matching the
// rest of the firmware. Heartbeat edges live on the SYNCED clock (that's the
// whole point of the heartbeat), hence the separate synced_us input.
#pragma once

#include <stdint.h>

#include "pattern_math.h"  // pmath::floorDiv — heartbeat edge on negative time

struct NapConfig {
  int64_t frame_us;         // render cadence for animated patterns (nap ceiling)
  int64_t min_nap_us;       // shorter than this isn't worth the sleep transition
  int64_t max_nap_us;       // hard safety cap on any single nap
  int64_t serial_grace_us;  // no naps this soon after serial traffic
};

struct NapInputs {
  int64_t now_us;             // local clock (esp_timer_get_time)
  int64_t synced_us;          // synced show clock (heartbeat edges live here)
  bool    radio_on;           // duty-cycle radio state — never nap while ON
  int64_t radio_change_at_us; // local time of the next duty transition
  bool    pattern_static;     // patterns::patternIsStatic(current pattern)
  int64_t last_serial_us;     // local time of the last serial byte seen
  int64_t heartbeat_half_us;  // heartbeat half-period; 0 = heartbeat disabled
};

// How long the CPU may light-sleep right now, in microseconds. 0 = don't nap.
inline int64_t napPlan(const NapConfig& c, const NapInputs& in) {
  if (in.radio_on) return 0;  // listen window: RX must stay hot
  if (in.now_us - in.last_serial_us < c.serial_grace_us) return 0;

  int64_t nap = c.max_nap_us;

  // Never sleep past the next radio wake — that window must catch a beacon.
  int64_t radio = in.radio_change_at_us - in.now_us;
  if (radio < nap) nap = radio;

  // Animated f(x,y,t) needs re-rendering at frame cadence; static f doesn't.
  if (!in.pattern_static && c.frame_us < nap) nap = c.frame_us;

  // Wake for the next heartbeat edge so the blink stays square. The edge is
  // computed on synced time (floored division so a briefly-negative synced
  // clock still yields an edge in (0, half]); the DELTA applies to the local
  // clock unchanged — both clocks tick at the same rate.
  if (in.heartbeat_half_us > 0) {
    int64_t mod = in.synced_us -
                  pmath::floorDiv(in.synced_us, in.heartbeat_half_us) *
                      in.heartbeat_half_us;
    int64_t edge = in.heartbeat_half_us - mod;  // in (0, half]
    if (edge < nap) nap = edge;
  }

  return (nap < c.min_nap_us) ? 0 : nap;
}
