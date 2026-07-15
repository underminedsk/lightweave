// USB power-bank keepalive pulse logic.
//
// Some consumer USB power banks shut off when the load is too low. This config
// lets the conductor broadcast a brief LED pulse during scheduled-off periods so
// the bank sees a periodic load without burning a constant dummy resistor.
#pragma once

#include <stdint.h>

static constexpr uint8_t KEEPALIVE_FLAG_ENABLED = 0x01;
static constexpr uint16_t KEEPALIVE_INTERVAL_MIN_MS = 1000;
static constexpr uint16_t KEEPALIVE_INTERVAL_MAX_MS = 60000;
static constexpr uint16_t KEEPALIVE_PULSE_MIN_MS = 10;
static constexpr uint16_t KEEPALIVE_PULSE_MAX_MS = 5000;
static constexpr uint8_t KEEPALIVE_BRIGHTNESS_MAX = 192;

typedef struct __attribute__((packed)) {
  uint8_t flags;
  uint16_t interval_ms;
  uint16_t pulse_ms;
  uint8_t brightness;
} KeepAliveConfig;

inline KeepAliveConfig keepAliveDefault() {
  return {0, 10000, 100, 64};
}

inline bool keepAliveEnabled(const KeepAliveConfig& c) {
  return (c.flags & KEEPALIVE_FLAG_ENABLED) != 0;
}

inline void keepAliveSanitize(KeepAliveConfig& c) {
  if (c.interval_ms < KEEPALIVE_INTERVAL_MIN_MS) c.interval_ms = KEEPALIVE_INTERVAL_MIN_MS;
  if (c.interval_ms > KEEPALIVE_INTERVAL_MAX_MS) c.interval_ms = KEEPALIVE_INTERVAL_MAX_MS;
  if (c.pulse_ms < KEEPALIVE_PULSE_MIN_MS) c.pulse_ms = KEEPALIVE_PULSE_MIN_MS;
  if (c.pulse_ms > KEEPALIVE_PULSE_MAX_MS) c.pulse_ms = KEEPALIVE_PULSE_MAX_MS;
  if (c.pulse_ms > c.interval_ms) c.pulse_ms = c.interval_ms;
  if (c.brightness > KEEPALIVE_BRIGHTNESS_MAX) c.brightness = KEEPALIVE_BRIGHTNESS_MAX;
}

inline bool keepAlivePulseOn(const KeepAliveConfig& c, int64_t synced_us) {
  if (!keepAliveEnabled(c)) return false;
  KeepAliveConfig clean = c;
  keepAliveSanitize(clean);
  int64_t interval_us = (int64_t)clean.interval_ms * 1000LL;
  int64_t pulse_us = (int64_t)clean.pulse_ms * 1000LL;
  int64_t phase = synced_us % interval_us;
  if (phase < 0) phase += interval_us;
  return phase < pulse_us;
}
