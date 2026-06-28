// The sync beacon broadcast by the conductor over ESP-NOW.
//
// Packed so the wire layout is identical on every node regardless of compiler
// padding. Kept small (well under the 250-byte ESP-NOW payload limit).
#pragma once

#include <stdint.h>

typedef struct __attribute__((packed)) {
  uint32_t magic;       // BEACON_MAGIC — reject anything else
  int64_t  epoch_us;    // conductor's esp_timer clock at send time
  uint16_t pattern_id;  // which pattern to render
  uint8_t  brightness;  // global brightness cap (0-255)
  uint8_t  palette_id;  // palette selector
  uint16_t params[4];   // pattern-specific knobs for live tweaking
  uint32_t seq;         // monotonic; for drop detection / logging
} Beacon;
