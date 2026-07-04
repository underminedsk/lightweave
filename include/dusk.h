// Daytime deep-sleep decision logic — Milestone 3, Lever 2.
//
// The calendar-life fix: a node that runs 24/7 dies in ~5 days, but the show
// only exists at night, so a performer deep-sleeps (~10 µA) through daylight,
// waking on an RTC timer every DUSK_RESAMPLE_US to re-check the light AND
// listen for a beacon (the rendezvous — a conductor beacon carrying
// BEACON_FLAG_FIELD_AWAKE pins the node awake for a daytime test). This header
// is the pure decision logic: a debounced day/night detector with hysteresis
// over light-sensor millivolt samples, plus the "may we actually deep-sleep
// right now?" gate. src/main.cpp owns the ADC read, the RTC-memory day flag
// that survives deep sleep, and esp_deep_sleep() itself.
//
// Like sync.h/powersave.h/napsched.h: dependency-free, unit-tested on the host.
//
// DESIGN PRINCIPLE — FAIL AWAKE. Every ambiguous case resolves to "stay awake":
// an implausible sensor reading (floating pin, broken wire) reads as night; a
// fresh boot gets a long hold-off; recent serial traffic or a recent flagged
// beacon blocks sleep outright. The failure mode of this logic must only ever
// be battery drain (visible, recoverable) — never an unreachable field. A
// power-cycle therefore ALWAYS yields an awake node (cold boots start in
// night), which is the per-lantern physical override no bug can remove.
//
// Polarity (day_above) depends on how the phototransistor is wired: PT from
// 3.3 V to the ADC pin with a pull-down reads HIGH in daylight (true); PT to
// GND with a pull-up reads LOW in daylight (false). Decided at wiring time in
// config.h. Pick the divider so both real extremes (noon sun / dark night)
// stay INSIDE [floor_mv, ceil_mv] — readings outside that band are treated as
// a broken sensor, i.e. night.
//
// Hysteresis + debounce make the detector boring on purpose: the state flips
// only when the reading sits past the FAR threshold continuously for
// debounce_us. Clouds, headlights, a flashlight sweep, or ADC noise reset the
// stretch and change nothing. Sunrise and sunset are the only things that
// steady.
#pragma once

#include <stdint.h>

struct DuskConfig {
  bool     day_above;        // true: daylight drives the ADC reading UP
  uint16_t day_mv;           // past this (in the day direction) = raw day
  uint16_t night_mv;         // past this (in the night direction) = raw night;
                             // readings between the two keep the current state
  uint16_t floor_mv;         // readings below this are implausible => night
  uint16_t ceil_mv;          // readings above this are implausible => night
  int64_t  debounce_us;      // raw state must hold this long to flip
  int64_t  serial_grace_us;  // never sleep this soon after serial traffic
  int64_t  wake_ttl_us;      // a FIELD_AWAKE beacon blocks sleep this long
};

struct Dusk {
  bool    day;            // debounced state; false (night) keeps the show on
  bool    cand;           // raw state of the current candidate stretch
  int64_t cand_since_us;  // when the candidate stretch started
};

// start_day comes from the RTC-memory flag: a timer wake from daytime sleep
// starts in day (so a still-bright resample re-sleeps after the short
// min-awake instead of burning a whole debounce awake); any other boot starts
// in night — the safe default, the show just runs.
inline void duskInit(Dusk& d, bool start_day, int64_t now_us) {
  d.day = start_day;
  d.cand = start_day;
  d.cand_since_us = now_us;
}

// Raw day/night call for one sample. Implausible readings (outside
// [floor_mv, ceil_mv] — a floating or broken sensor) are night: fail awake.
// Otherwise hysteresis against the CURRENT debounced state: flipping requires
// crossing the far threshold; the dead band between night_mv and day_mv always
// reads as "no change".
inline bool duskRawIsDay(const Dusk& d, const DuskConfig& c, uint16_t mv) {
  if (mv < c.floor_mv || mv > c.ceil_mv) return false;  // broken sensor => night
  if (c.day_above) {
    if (d.day) return mv > c.night_mv;  // day until it falls below night_mv
    return mv >= c.day_mv;              // night until it rises past day_mv
  }
  if (d.day) return mv < c.night_mv;    // inverted wiring: day = low reading
  return mv <= c.day_mv;
}

// Feed one light sample. Returns the debounced day state after the sample.
inline bool duskOnSample(Dusk& d, const DuskConfig& c, uint16_t mv,
                         int64_t now_us) {
  bool raw = duskRawIsDay(d, c, mv);
  if (raw == d.day) {
    // Reading agrees with the current state: any opposite stretch dies here.
    d.cand = d.day;
    d.cand_since_us = now_us;
  } else if (raw != d.cand) {
    // A new opposite stretch starts now.
    d.cand = raw;
    d.cand_since_us = now_us;
  } else if (now_us - d.cand_since_us >= c.debounce_us) {
    // Opposite stretch held for the full debounce: flip.
    d.day = raw;
    d.cand_since_us = now_us;
  }
  return d.day;
}

// May the node actually deep-sleep right now? Debounced day is necessary but
// not sufficient — all of these must ALSO hold (each is a fail-awake gate):
//   - earliest_sleep_us has passed: a cold boot gets a long provisioning
//     hold-off, a timer wake a short one (glue computes it at boot);
//   - no serial traffic within serial_grace_us: a human is at the bench;
//   - no FIELD_AWAKE-flagged beacon within wake_ttl_us: the conductor has
//     summoned the field (daytime test) — checked at every rendezvous.
// The caller gates further on role (conductor never dusk-sleeps) and the
// `dusk on|off` runtime switch (default OFF until the sensor exists).
inline bool duskShouldSleep(const Dusk& d, const DuskConfig& c, int64_t now_us,
                            int64_t earliest_sleep_us, int64_t last_serial_us,
                            int64_t last_wake_flag_us) {
  if (!d.day) return false;
  if (now_us < earliest_sleep_us) return false;
  if (now_us - last_serial_us < c.serial_grace_us) return false;
  if (now_us - last_wake_flag_us < c.wake_ttl_us) return false;
  return true;
}
