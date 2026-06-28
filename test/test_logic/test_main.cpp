// Host-side unit tests for the sync core and pattern math — the subtle, silently-
// failing logic the brief flags as "the hard part and the real risk". These run
// on your machine via `pio test -e native`; they need no ESP32 hardware.
//
// What is intentionally NOT tested here: radio range, real-world packet loss,
// the ADC2-dies-with-radio trap, and on-chip timing jitter. Those only surface
// on hardware — these tests complement field testing, they don't replace it.

#include <unity.h>

#include "sync.h"
#include "pattern_math.h"

// ---- Sync: locking & offset --------------------------------------------------

void test_starts_unlocked() {
  SyncState s;
  syncInit(s);
  TEST_ASSERT_FALSE(s.locked);
  TEST_ASSERT_EQUAL_INT64(0, s.offset_us);
  // Before any lock, synced time is just local time.
  TEST_ASSERT_EQUAL_INT64(12345, syncedTime(s, 12345));
}

void test_offset_reproduces_conductor_clock() {
  SyncState s;
  syncInit(s);
  // Conductor's clock is 1_000_000us ahead of ours when the beacon arrives.
  syncOnBeacon(s, /*epoch*/ 5'000'000, /*seq*/ 0, /*local*/ 4'000'000);
  TEST_ASSERT_TRUE(s.locked);
  TEST_ASSERT_EQUAL_INT64(1'000'000, s.offset_us);
  // syncedTime now maps a later local clock onto the conductor's timeline.
  TEST_ASSERT_EQUAL_INT64(6'000'000, syncedTime(s, 5'000'000));
}

void test_relock_updates_offset() {
  SyncState s;
  syncInit(s);
  syncOnBeacon(s, 5'000'000, 0, 4'000'000);      // offset +1_000_000
  syncOnBeacon(s, 9'500'000, 1, 9'000'000);      // offset +500_000
  TEST_ASSERT_EQUAL_INT64(500'000, s.offset_us);
}

// ---- Sync: free-run on missed beacons (must never blank) ---------------------

void test_free_run_keeps_advancing_without_beacons() {
  SyncState s;
  syncInit(s);
  syncOnBeacon(s, 5'000'000, 0, 4'000'000);  // offset +1_000_000
  // No further beacons. Synced time must keep advancing with the local clock,
  // not freeze or reset — this is the no-blackout guarantee.
  TEST_ASSERT_EQUAL_INT64(11'000'000, syncedTime(s, 10'000'000));
  TEST_ASSERT_EQUAL_INT64(31'000'000, syncedTime(s, 30'000'000));
}

void test_staleness_boundary() {
  SyncState s;
  syncInit(s);
  TEST_ASSERT_TRUE(syncIsStale(s, 0, 2'000'000));  // never locked => stale

  syncOnBeacon(s, 0, 0, 1'000'000);  // locked at local t=1_000_000
  // Just inside the window: not stale.
  TEST_ASSERT_FALSE(syncIsStale(s, 1'000'000 + 2'000'000, 2'000'000));
  // One microsecond past the window: stale (but still free-running).
  TEST_ASSERT_TRUE(syncIsStale(s, 1'000'000 + 2'000'001, 2'000'000));
}

void test_beacon_age() {
  SyncState s;
  syncInit(s);
  TEST_ASSERT_EQUAL_INT64(-1, beaconAge(s, 999));  // sentinel before lock
  syncOnBeacon(s, 0, 0, 1'000'000);
  TEST_ASSERT_EQUAL_INT64(2'500'000, beaconAge(s, 3'500'000));
}

// ---- Sync: drop / out-of-order detection ------------------------------------

void test_in_sequence_has_no_gaps() {
  SyncState s;
  syncInit(s);
  for (uint32_t i = 0; i < 100; i++) syncOnBeacon(s, i * 1000, i, i * 1000);
  TEST_ASSERT_EQUAL_UINT32(0, s.seq_gaps);
  TEST_ASSERT_EQUAL_UINT32(100, s.beacons_rx);
}

void test_dropped_beacon_counts_one_gap() {
  SyncState s;
  syncInit(s);
  syncOnBeacon(s, 0, 0, 0);
  syncOnBeacon(s, 0, 1, 0);
  BeaconOutcome o = syncOnBeacon(s, 0, 3, 0);  // skipped seq 2
  TEST_ASSERT_TRUE(o.gap);
  TEST_ASSERT_EQUAL_UINT32(1, s.seq_gaps);
}

void test_first_beacon_is_never_a_gap() {
  SyncState s;
  syncInit(s);
  // A node may join mid-stream; its first-ever beacon (any seq) is not a gap.
  BeaconOutcome o = syncOnBeacon(s, 0, 9999, 0);
  TEST_ASSERT_FALSE(o.gap);
  TEST_ASSERT_EQUAL_UINT32(0, s.seq_gaps);
}

void test_seq_gap_handles_uint32_wrap() {
  SyncState s;
  syncInit(s);
  syncOnBeacon(s, 0, 0xFFFFFFFEu, 0);          // lock at next-to-max
  BeaconOutcome a = syncOnBeacon(s, 0, 0xFFFFFFFFu, 0);  // +1: fine
  BeaconOutcome b = syncOnBeacon(s, 0, 0x00000000u, 0);  // wraps to 0: fine
  TEST_ASSERT_FALSE(a.gap);
  TEST_ASSERT_FALSE(b.gap);
  TEST_ASSERT_EQUAL_UINT32(0, s.seq_gaps);
}

// ---- Pattern math: phase wrap & pulse continuity ----------------------------

void test_phase_range_and_wrap() {
  // Phase stays in [0,1) and resets at the period boundary.
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 0.0f, pmath::phase(0, 4.0f));
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 0.5f, pmath::phase(2'000'000, 4.0f));
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 0.0f, pmath::phase(4'000'000, 4.0f));   // wrapped
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 0.25f, pmath::phase(5'000'000, 4.0f));  // 2nd period
}

void test_phase_handles_large_time_no_overflow() {
  // ~5 days of microseconds — must still be a clean phase, not garbage.
  int64_t t = (int64_t)5 * 24 * 3600 * 1'000'000;
  float p = pmath::phase(t, 4.0f);
  TEST_ASSERT_TRUE(p >= 0.0f && p < 1.0f);
}

void test_pulse_intensity_bounds_and_endpoints() {
  // Raised cosine: 0 at the start of a period, peak 1 at the half period.
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0.0f, pmath::pulseIntensity(0, 4.0f, 0.0f));
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 1.0f, pmath::pulseIntensity(2'000'000, 4.0f, 0.0f));
  // Stays within [0,1] sampled across a full period.
  for (int64_t us = 0; us < 4'000'000; us += 50'000) {
    float v = pmath::pulseIntensity(us, 4.0f, 0.0f);
    TEST_ASSERT_TRUE(v >= -1e-4f && v <= 1.0f + 1e-4f);
  }
}

void test_pulse_continuous_across_wrap() {
  // No visible "hitch": the value just before the boundary ~= just after.
  float before = pmath::pulseIntensity(3'999'000, 4.0f, 0.0f);
  float after = pmath::pulseIntensity(4'001'000, 4.0f, 0.0f);
  TEST_ASSERT_FLOAT_WITHIN(0.01f, before, after);
}

// ---- Heartbeat: synced square wave ------------------------------------------

void test_heartbeat_square_wave() {
  const int64_t half = 500'000;
  TEST_ASSERT_TRUE(pmath::heartbeatOn(0, half));            // on at cycle start
  TEST_ASSERT_TRUE(pmath::heartbeatOn(499'999, half));      // still on
  TEST_ASSERT_FALSE(pmath::heartbeatOn(500'000, half));     // off in 2nd half
  TEST_ASSERT_FALSE(pmath::heartbeatOn(999'999, half));     // still off
  TEST_ASSERT_TRUE(pmath::heartbeatOn(1'000'000, half));    // on again next cycle
}

void test_heartbeat_agrees_across_boards_in_sync() {
  // Two boards with different boot times but the SAME synced time must blink
  // identically — that is the whole point of the visual proof.
  const int64_t half = 500'000;
  int64_t synced = 7'250'000;  // arbitrary shared synced instant
  bool boardA = pmath::heartbeatOn(synced, half);
  bool boardB = pmath::heartbeatOn(synced, half);
  TEST_ASSERT_EQUAL(boardA, boardB);
}

void test_heartbeat_handles_negative_synced_time() {
  // Floored division keeps the square wave continuous through 0 if synced time
  // briefly goes negative (no glitch at the boundary). Bins are [k*half,(k+1)*half),
  // ON when k is even.
  const int64_t half = 500'000;
  TEST_ASSERT_FALSE(pmath::heartbeatOn(-1, half));          // k=-1 (odd): off
  TEST_ASSERT_FALSE(pmath::heartbeatOn(-500'000, half));    // k=-1 (odd): off
  TEST_ASSERT_TRUE(pmath::heartbeatOn(-750'000, half));     // k=-2 (even): on
  TEST_ASSERT_TRUE(pmath::heartbeatOn(-1'000'000, half));   // k=-2 (even): on
}

// ---- Runner ------------------------------------------------------------------

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_starts_unlocked);
  RUN_TEST(test_offset_reproduces_conductor_clock);
  RUN_TEST(test_relock_updates_offset);
  RUN_TEST(test_free_run_keeps_advancing_without_beacons);
  RUN_TEST(test_staleness_boundary);
  RUN_TEST(test_beacon_age);
  RUN_TEST(test_in_sequence_has_no_gaps);
  RUN_TEST(test_dropped_beacon_counts_one_gap);
  RUN_TEST(test_first_beacon_is_never_a_gap);
  RUN_TEST(test_seq_gap_handles_uint32_wrap);
  RUN_TEST(test_phase_range_and_wrap);
  RUN_TEST(test_phase_handles_large_time_no_overflow);
  RUN_TEST(test_pulse_intensity_bounds_and_endpoints);
  RUN_TEST(test_pulse_continuous_across_wrap);
  RUN_TEST(test_heartbeat_square_wave);
  RUN_TEST(test_heartbeat_agrees_across_boards_in_sync);
  RUN_TEST(test_heartbeat_handles_negative_synced_time);
  return UNITY_END();
}
