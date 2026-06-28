// Compile-time configuration shared by all nodes.
//
// Hardware pins and radio settings live here so the rest of the firmware never
// hard-codes a GPIO number. See docs/do_baskets_firmware_brief.md for the wiring
// table and the don't-break list.
#pragma once

#include <stdint.h>

// ---- Role --------------------------------------------------------------------
// Provided as a build flag (see platformio.ini). 0 = performer, 1 = conductor.
#ifndef NODE_ROLE
#define NODE_ROLE 0
#endif
#define ROLE_PERFORMER 0
#define ROLE_CONDUCTOR 1
#define IS_CONDUCTOR (NODE_ROLE == ROLE_CONDUCTOR)

// ---- LEDs --------------------------------------------------------------------
// 16x SK6812 RGBW ring on GPIO18 (through a 470R series resistor).
static constexpr uint16_t LED_COUNT = 16;
static constexpr uint8_t  LED_PIN   = 18;

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

// ---- Diagnostics -------------------------------------------------------------
// How often each node prints a sync status line to serial (microseconds).
static constexpr int64_t DIAG_INTERVAL_US = 1000000;  // 1s
