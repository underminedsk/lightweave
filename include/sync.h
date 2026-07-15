// Clock synchronization core — the resilience heart of the installation.
//
// Deliberately dependency-free (no Arduino, no ESP-NOW, no globals): the
// conductor's clock, the performer's offset, free-run behavior, and drop
// detection are all expressed as plain functions over a SyncState struct. That
// makes the hard part of the project — the part the brief calls "the real risk"
// — readable in isolation and testable on a host without hardware.
//
// All times are int64 microseconds (esp_timer_get_time on-device), never 32-bit
// millis, so there is no wraparound over a multi-day run.
#pragma once

#include <stdint.h>

struct SyncState {
  int64_t  offset_us;       // conductor_clock - local_clock (the APPLIED, slewed offset)
  int64_t  last_beacon_us;  // local time the last beacon arrived
  bool     locked;          // have we ever locked to a beacon?
  uint32_t last_seq;        // sequence number of the last beacon seen
  uint32_t beacons_rx;      // total beacons received
  uint32_t seq_gaps;        // beacons that arrived missing/out-of-order
  uint32_t offset_rejects;  // beacons whose timestamp was gated out as an outlier
  uint32_t reject_streak;   // consecutive gated beacons (drives the re-lock escape)
};

// Tunables for how much a single beacon is trusted. The oscillator is a superb
// short-term clock; a packet is a terrible messenger (delay is one-sided and, in a
// congested mesh, occasionally whole seconds). So we let packets *discipline* the
// coasting offset rather than *set* it:
//
//   gate_us      Reject a beacon that implies the offset jumped more than this vs.
//                the coasting clock. Real drift between beacons is tens of µs to a
//                few ms even after a long free-run; a delayed/bogus beacon shows up
//                as hundreds of ms. Anything past the gate is an outlier, not a fact.
//   slew_max_us  Cap how far the applied offset may move per accepted beacon, so the
//                clock GLIDES through a correction instead of stepping. Normal
//                (sub-cap) errors apply in full — they are already invisible; only
//                near-gate corrections are spread over several beacons.
//   relock_after Escape hatch: after this many consecutive gated beacons, assume the
//                conductor genuinely jumped (reboot / master change) and snap to it.
//                Every node crosses this threshold together, so the field re-locks in
//                lockstep rather than one node at a time.
struct SyncConfig {
  int64_t  gate_us;
  int64_t  slew_max_us;
  uint32_t relock_after;
};

// Defaults sized for a 4 Hz (250 ms) beacon and ±150 ppm crystals: legitimate
// between-beacon error stays < ~10 ms, congestion delays start at hundreds of ms,
// so a 100 ms gate cleanly separates the two. 2 ms/beacon slew ≈ 8 ms/s glide at
// full rate — below the visual threshold of the patterns. 8 rejects ≈ 2 s to adopt
// a real conductor jump.
constexpr SyncConfig SYNC_DEFAULT = {
  /*gate_us*/ 100000, /*slew_max_us*/ 2000, /*relock_after*/ 8};

// Returned from syncOnBeacon so callers can log per-beacon outcomes.
struct BeaconOutcome {
  bool gap;       // this beacon was not the expected next-in-sequence
  bool rejected;  // timestamp gated out as an outlier (offset left coasting)
  bool relocked;  // gate escape fired: snapped to a genuinely-jumped conductor
};

inline void syncInit(SyncState& s) {
  s.offset_us = 0;
  s.last_beacon_us = 0;
  s.locked = false;
  s.last_seq = 0;
  s.beacons_rx = 0;
  s.seq_gaps = 0;
  s.offset_rejects = 0;
  s.reject_streak = 0;
}

// Move `applied` toward `target` by at most `max_step` (both int64 µs).
inline int64_t syncSlew(int64_t applied, int64_t target, int64_t max_step) {
  int64_t err = target - applied;
  if (err >  max_step) err =  max_step;
  if (err < -max_step) err = -max_step;
  return applied + err;
}

// Apply a received beacon.
//   epoch_us       conductor's clock stamped into the beacon at send time
//   seq            the beacon's sequence number
//   local_recv_us  this node's local clock when the beacon arrived
//   cfg            trust tunables (see SyncConfig); SYNC_DEFAULT is fine on-device
//
// The offset makes (local + offset) reproduce the conductor's clock. Transmission
// delay is sub-millisecond in the common case and intentionally ignored; the gate
// and slew exist for the *uncommon* case — a beacon delayed by hundreds of ms — so
// one late packet can never yank the whole installation off the shared timeline.
//
// Seq/gap accounting and last_beacon_us track packet *delivery* and run on every
// arrival. Only the timestamp's effect on the offset is conditional on trust.
inline BeaconOutcome syncOnBeacon(SyncState& s, int64_t epoch_us, uint32_t seq,
                                  int64_t local_recv_us,
                                  const SyncConfig& cfg = SYNC_DEFAULT) {
  BeaconOutcome out{false, false, false};

  // Gap detection before we adopt the new seq. uint32 arithmetic wraps cleanly,
  // so last_seq + 1 stays correct across the 2^32 rollover.
  if (s.locked && seq != (uint32_t)(s.last_seq + 1)) {
    s.seq_gaps++;
    out.gap = true;
  }

  const int64_t target = epoch_us - local_recv_us;

  if (!s.locked) {
    // First fix: no coasting clock to protect, so adopt the conductor exactly.
    s.offset_us = target;
    s.locked = true;
    s.reject_streak = 0;
  } else if ((target - s.offset_us) > cfg.gate_us ||
             (target - s.offset_us) < -cfg.gate_us) {
    // Outlier: the implied jump is larger than any real drift. Distrust it and
    // keep coasting — UNLESS enough of them pile up, in which case the conductor
    // itself has jumped (reboot / master change) and we must re-lock to it.
    s.offset_rejects++;
    s.reject_streak++;
    if (s.reject_streak >= cfg.relock_after) {
      s.offset_us = target;      // snap: this is the new reality, not a stray delay
      s.reject_streak = 0;
      out.relocked = true;
    } else {
      out.rejected = true;
    }
  } else {
    // Trusted correction: glide toward it so animations never see a step.
    s.offset_us = syncSlew(s.offset_us, target, cfg.slew_max_us);
    s.reject_streak = 0;
  }

  s.last_beacon_us = local_recv_us;
  s.last_seq = seq;
  s.beacons_rx++;
  return out;
}

// Synced ("conductor") time as seen by this node. Before the first lock the
// offset is 0, so this simply reads the local clock. After a lock it keeps
// returning sensible values even with no further beacons — that *is* free-run:
// the node coasts on its last known offset rather than blanking.
inline int64_t syncedTime(const SyncState& s, int64_t local_now_us) {
  return local_now_us + s.offset_us;
}

// Microseconds since the last accepted beacon. Negative sentinel if never locked.
inline int64_t beaconAge(const SyncState& s, int64_t local_now_us) {
  return s.locked ? (local_now_us - s.last_beacon_us) : -1;
}

// "Stale" means free-running on an old offset (or never locked). It does NOT mean
// stop rendering — the node keeps coasting. This is purely a diagnostics signal.
inline bool syncIsStale(const SyncState& s, int64_t local_now_us,
                         int64_t stale_us) {
  return !s.locked || (local_now_us - s.last_beacon_us) > stale_us;
}
