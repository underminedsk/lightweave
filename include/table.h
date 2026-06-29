// The conductor's authoritative MAC -> (x,y) layout table.
//
// Where the roster (roster.h) is the ephemeral "who is alive right now", the
// table is the persistent "where each node lives" map: conductor-authoritative,
// stored in the conductor's NVS, and broadcast so every node finds its own MAC,
// adopts its (x,y), and caches it. Re-arrange the whole field by editing one
// table — no per-node serial, no laptop in the field.
//
// Dependency-free and host-tested, like roster.h / sync.h. main.cpp owns the NVS
// blob I/O, the radio broadcast, and the spinlock; the data logic lives here.
#pragma once

#include <stdint.h>
#include <string.h>

// Max nodes in the field map. 60-node field + headroom; ~14 bytes/entry in NVS.
static constexpr uint8_t TABLE_MAX = 64;

struct TableEntry {
  uint8_t mac[6];
  float   x;
  float   y;
};

struct LayoutTable {
  TableEntry entries[TABLE_MAX];
  uint8_t    count;
};

inline void tableInit(LayoutTable& t) { t.count = 0; }

// Index of the entry for this MAC, or -1 if absent.
inline int tableFind(const LayoutTable& t, const uint8_t mac[6]) {
  for (int i = 0; i < t.count; i++)
    if (memcmp(t.entries[i].mac, mac, 6) == 0) return i;
  return -1;
}

// Set (insert or update) a node's position, matched by MAC. Idempotent per MAC.
// Returns false only when the table is full AND the MAC is new.
inline bool tableSet(LayoutTable& t, const uint8_t mac[6], float x, float y) {
  int i = tableFind(t, mac);
  if (i < 0) {
    if (t.count >= TABLE_MAX) return false;
    i = t.count++;
  }
  memcpy(t.entries[i].mac, mac, 6);
  t.entries[i].x = x;
  t.entries[i].y = y;
  return true;
}

// Look up a node's position. Writes x,y and returns true when present.
inline bool tableLookup(const LayoutTable& t, const uint8_t mac[6], float& x,
                        float& y) {
  int i = tableFind(t, mac);
  if (i < 0) return false;
  x = t.entries[i].x;
  y = t.entries[i].y;
  return true;
}

// Remove a node (e.g. node replacement). Returns true if it was present. Order is
// irrelevant, so the hole is filled by the last entry (cheap, no shift).
inline bool tableRemove(LayoutTable& t, const uint8_t mac[6]) {
  int i = tableFind(t, mac);
  if (i < 0) return false;
  t.entries[i] = t.entries[--t.count];
  return true;
}
