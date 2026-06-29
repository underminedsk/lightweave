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

// ---- Diagnostics -------------------------------------------------------------
// How often each node prints a sync status line to serial (microseconds).
static constexpr int64_t DIAG_INTERVAL_US = 1000000;  // 1s
