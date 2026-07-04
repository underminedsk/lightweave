// Performer radio duty-cycle scheduler — Milestone 3, Lever 1, Stage A.
//
// A performer free-runs f(x,y,t) from the synced clock, so it does NOT need the
// radio on continuously: it only needs periodic beacons for clock-drift
// correction and recipe/table updates. So the radio is powered down most of the
// time and woken for a brief listen window every few seconds to resync. This is
// the main attack on the RX-dominated night draw — modem-sleep can't help
// connectionless ESP-NOW (no AP/DTIM, so RX otherwise stays on).
//
// Like sync.h, this is deliberately dependency-free (no Arduino, no ESP-NOW, no
// globals): the on/off schedule is a small state machine expressed as plain
// functions over a DutyCycle struct, so it runs and is unit-tested on the host.
// src/main.cpp owns the actual radio teardown/bring-up; here we only decide WHEN.
//
// All times are int64 microseconds (esp_timer_get_time on-device), matching the
// rest of the firmware — no 32-bit millis wrap over a multi-day run.
#pragma once

#include <stdint.h>

// Timing knobs (set from config.h on-device). The listen window must be wide
// enough to reliably catch at least one beacon at the conductor's broadcast rate,
// even with scheduling jitter and radio-wake latency.
struct DutyConfig {
  int64_t off_us;     // radio stays OFF this long between listen windows
  int64_t listen_us;  // radio stays ON this long hunting for a beacon
};

// What the caller must do this step. The scheduler never touches the radio
// itself — it returns an action and the on-device glue powers the radio up/down.
enum DutyAction : uint8_t {
  DUTY_NONE = 0,  // no transition this step
  DUTY_WAKE,      // power the radio UP now (re-add peers, re-register recv cb)
  DUTY_SLEEP,     // power the radio DOWN now
};

struct DutyCycle {
  bool     radio_on;        // should the radio be powered right now?
  bool     ever_caught;     // have we caught any beacon since boot?
  bool     caught;          // caught a beacon during the CURRENT on-window?
  int64_t  change_at_us;    // local time the next transition is due
  uint32_t windows;         // completed listen windows (after acquisition)
  uint32_t missed_windows;  // of those, how many ended catching no beacon
};

// Start in the ON state, listening, so a node locks before it ever sleeps —
// the "battery swap looks like a single blink" guarantee. (Its table position
// survives in NVS across the swap; a node that lacks one gets a targeted row
// reply to its first REGISTER, which also happens before the first sleep — see
// table_wire.h.) The radio is assumed already powered by the caller's setup.
inline void dutyInit(DutyCycle& d, const DutyConfig& c, int64_t now_us) {
  d.radio_on = true;
  d.ever_caught = false;
  d.caught = false;
  d.change_at_us = now_us + c.listen_us;
  d.windows = 0;
  d.missed_windows = 0;
}

// Note that a beacon was accepted. Call from the render loop when the sync core
// reports a fresh beacon while the radio is on. Records that this window caught
// one (so we can tell whether the window is reliably catching beacons) and that
// we have acquired at least once (which lets the scheduler begin sleeping).
inline void dutyNoteBeacon(DutyCycle& d) {
  if (!d.radio_on) return;
  d.caught = true;
  d.ever_caught = true;
}

// Advance the schedule. Returns the transition the caller must apply, if any.
//
// Cold-boot acquisition: while we have NEVER caught a beacon, the on-window is
// extended rather than slept through, so a fresh node keeps listening until it
// first locks (and hears its table row) instead of napping through a silent gap.
// Once acquired, every window is a fixed listen_us and the radio sleeps off_us
// between windows. A conductor going silent mid-show is fine: windows then catch
// nothing, the node free-runs from the synced clock, and it re-locks when the
// conductor returns.
inline DutyAction dutyStep(DutyCycle& d, const DutyConfig& c, int64_t now_us) {
  if (d.radio_on) {
    if (now_us < d.change_at_us) return DUTY_NONE;
    if (!d.ever_caught) {  // still acquiring: keep listening, don't sleep yet
      d.change_at_us = now_us + c.listen_us;
      return DUTY_NONE;
    }
    d.windows++;
    if (!d.caught) d.missed_windows++;
    d.radio_on = false;
    d.caught = false;
    d.change_at_us = now_us + c.off_us;
    return DUTY_SLEEP;
  }
  // Radio off: wake when the off-interval elapses.
  if (now_us < d.change_at_us) return DUTY_NONE;
  d.radio_on = true;
  d.caught = false;
  d.change_at_us = now_us + c.listen_us;
  return DUTY_WAKE;
}
