#pragma once

#include <stdint.h>
#include <string.h>

#include "powermon.h"
#include "roster.h"

struct PowerEntry {
  uint8_t mac[6];
  PowerSample sample;
  int64_t last_us;
};

struct PowerTable {
  PowerEntry entries[ROSTER_MAX];
  uint8_t count;
};

inline void powerTableInit(PowerTable& t) { t.count = 0; }

inline int powerTableFind(const PowerTable& t, const uint8_t mac[6]) {
  for (int i = 0; i < t.count; i++)
    if (memcmp(t.entries[i].mac, mac, 6) == 0) return i;
  return -1;
}

inline bool powerTableUpsert(PowerTable& t, const uint8_t mac[6],
                             const PowerSample& sample, int64_t now_us) {
  int i = powerTableFind(t, mac);
  if (i < 0) {
    if (t.count >= ROSTER_MAX) return false;
    i = t.count++;
  }
  memcpy(t.entries[i].mac, mac, 6);
  t.entries[i].sample = sample;
  t.entries[i].last_us = now_us;
  return true;
}
