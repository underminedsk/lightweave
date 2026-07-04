// Compile-time configuration shared by all nodes.
//
// Hardware pins and radio settings live here so the rest of the firmware never
// hard-codes a GPIO number. See docs/do_baskets_firmware_brief.md for the wiring
// table and the don't-break list.
#pragma once

#include <stdint.h>

// ---- Role --------------------------------------------------------------------
// Every node runs identical firmware; role is a runtime value stored in NVS and
// set once over serial (`role conductor|performer`). Default is performer so a
// fresh board never accidentally becomes a second conductor. See src/main.cpp.
static constexpr uint8_t ROLE_PERFORMER = 0;
static constexpr uint8_t ROLE_CONDUCTOR = 1;
static constexpr uint8_t DEFAULT_ROLE   = ROLE_PERFORMER;

// ---- LEDs --------------------------------------------------------------------
// 16x SK6812 RGBW ring data line (through a 470R series resistor).
// GPIO13 ("D13") sits next to VIN/GND on the DOIT DevKit V1 top header, so on a
// single breadboard the power, ground, and data pins are all reachable from one
// free row. (Any non-strapping output GPIO works; the brief's original GPIO18 is
// on the bottom header, which is awkward to reach when the board covers a row.)
static constexpr uint16_t LED_COUNT = 16;
static constexpr uint8_t  LED_PIN   = 13;

// Power guardrail. Every node clamps the rendered brightness to this, so no
// pattern or broadcast recipe can exceed the per-node power budget no matter what
// gets authored. Set to 192 (75%) after the worst-case bench measurement: solid
// white at 255 measured 0.76 A @ 5 V (~3 nights sustained), which is safe for the
// hardware but a battery risk if a pathological all-white show ran all night; 192
// keeps the worst case ~3.8 nights while barely dimming real colored shows. Raise
// toward 255 only if a brighter ceiling is wanted and battery life allows.
static constexpr uint8_t MAX_BRIGHTNESS = 192;

// ---- Onboard heartbeat LED (bring-up aid) ------------------------------------
// Blinks the board's built-in LED on the synced beat so two bare boards can be
// seen blinking in unison — a visual sync proof with no ring wiring. Drop it
// later by building with -D HEARTBEAT_LED=0.
#ifndef HEARTBEAT_LED
#define HEARTBEAT_LED 1
#endif
static constexpr uint8_t HEARTBEAT_LED_PIN = 2;       // built-in LED on most DevKitCs
static constexpr bool    HEARTBEAT_ACTIVE_LOW = false;  // set true if your LED is inverted
static constexpr int64_t HEARTBEAT_HALF_US = 500000;  // 500ms on / 500ms off => 1 Hz

// ---- Sensors (Milestone 3 — declared now so pins are never reused) -----------
// ADC1 ONLY. ADC2 silently dies whenever the radio is active.
static constexpr uint8_t PIN_LDR      = 34;  // dusk sensor, ADC1
static constexpr uint8_t PIN_VBAT     = 35;  // battery sense divider, ADC1

// ---- Radio -------------------------------------------------------------------
// Every node sets this channel explicitly (esp_wifi_set_channel) so they agree
// without scanning. All nodes MUST use the same channel.
static constexpr uint8_t WIFI_CHANNEL = 1;

// CPU at 160MHz is plenty for 16 pixels and saves ~15-30mA (brief: power discipline).
static constexpr uint32_t CPU_FREQ_MHZ = 160;

// ---- Sync protocol -----------------------------------------------------------
// Identifies our packets so stray ESP-NOW traffic is ignored.
static constexpr uint32_t BEACON_MAGIC = 0xD0BA5757;  // "DOBA" + baskets

// Conductor broadcasts at this interval (microseconds). 250ms => 4 Hz.
static constexpr int64_t BEACON_INTERVAL_US = 250000;

// A performer that hasn't heard a beacon for this long is considered "stale" for
// diagnostics — it keeps free-running regardless (no blanking on missed beacons).
static constexpr int64_t BEACON_STALE_US = 2000000;  // 2s

// ---- Registration / roster ---------------------------------------------------
// A performer re-announces itself (REGISTER) to the conductor this often, so the
// conductor's roster self-heals after a conductor restart. Cheap: one tiny packet
// per node every interval, far below the beacon rate. (The roster itself and its
// capacity ROSTER_MAX live in the dependency-free, host-tested include/roster.h.)
static constexpr int64_t REGISTER_INTERVAL_US = 10000000;  // 10s

// The conductor re-broadcasts the layout table this often. Positions are static,
// so this is occasional, not per-frame; a node needs to hear it only once, then
// survives on its NVS cache.
static constexpr int64_t TABLE_INTERVAL_US = 5000000;  // 5s

// ---- Performer radio duty-cycle (Milestone 3, Lever 1, Stage A) --------------
// A performer free-runs f(x,y,t) from the synced clock, so it does not need the
// radio on continuously — only periodic beacons for drift correction and recipe/
// table updates. So power the radio down between brief listen windows: this is the
// main attack on the RX-dominated night draw (modem-sleep can't help
// connectionless ESP-NOW — no AP/DTIM, so RX otherwise stays on). The conductor is
// exempt (it must beacon at 4 Hz and is typically wall-powered); the duty-cycle is
// gated on role == performer in src/main.cpp. The schedule logic is the
// dependency-free, host-tested include/powersave.h. Toggle live (and per-board,
// persisted to NVS key "ps") with the `powersave on|off` serial command.
#ifndef POWERSAVE_DEFAULT
#define POWERSAVE_DEFAULT 1  // performers duty-cycle by default; 0 = radio always on
#endif
// Radio OFF this long between windows, then ON for a window. At BEACON_INTERVAL_US
// = 250 ms (4 Hz), a 600 ms window spans ~2 beacons, so one is caught even with
// wake latency + jitter. ~600 ms ON / 4 s OFF ≈ 13% radio duty. Drift over a 4 s
// free-run on a ~tens-of-ppm crystal is sub-millisecond — invisible in the slow
// patterns. A recipe/position change now lands up to one OFF interval late, which
// is acceptable for the installation (noted in HANDOFF).
static constexpr int64_t DUTY_OFF_US    = 4000000;  // 4s radio off between listens
static constexpr int64_t DUTY_LISTEN_US = 600000;   // 600ms listen window

// ---- Performer CPU light-sleep (Milestone 3, Lever 1, Stage B) ---------------
// While the radio is off (Stage A above), the CPU light-sleeps between the
// moments it has real work instead of spinning delay(16): until the next
// animation frame on animated patterns, or clear to the next radio window /
// heartbeat edge on static ones (GLOW — SK6812s latch, so nothing needs the
// CPU). Decision logic is the dependency-free, host-tested include/napsched.h;
// main.cpp owns esp_light_sleep_start(). Gated on the same `powersave` toggle
// as Stage A (naps only ever happen inside radio-off spans, which only exist
// when powersave is on). Serial traffic (or a UART wakeup) holds naps off for a
// grace window so USB provisioning always wins over power.
static constexpr int64_t NAP_FRAME_US        = 33333;     // ~30 fps animated render
static constexpr int64_t NAP_MIN_US          = 5000;      // shorter isn't worth it
static constexpr int64_t NAP_MAX_US          = 1000000;   // safety cap per nap
static constexpr int64_t SERIAL_NAP_GRACE_US = 30000000;  // 30 s after serial traffic

// ---- Diagnostics -------------------------------------------------------------
// How often each node prints a sync status line to serial (microseconds).
static constexpr int64_t DIAG_INTERVAL_US = 1000000;  // 1s
