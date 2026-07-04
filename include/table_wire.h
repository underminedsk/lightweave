// The layout table on the wire: chunking, validation, and broadcast cadence.
//
// table.h is the pure MAC->(x,y) data structure; beacon.h is the packet layout.
// This header is everything in between — the logic that used to live inline in
// main.cpp's broadcastTable()/onRecv() where it was host-unreachable:
//   - sender side: how many MSG_TABLE chunks a table needs, and filling one
//     chunk (header, indices, rows, exact wire length);
//   - receiver side: validating a chunk's length against its declared row
//     count BEFORE trusting it, and scanning it for this node's own row;
//   - cadence: positions are static, so steady-state rebroadcast is slow
//     (TABLE_INTERVAL_US); a burst request (new node registered, `assign`)
//     pulls the next broadcast forward, rate-limited by a minimum spacing so
//     a mass rejoin after a conductor reboot can't turn into a packet storm.
// Dependency-free beyond sibling pure headers; main.cpp owns the radio calls.
#pragma once

#include <stddef.h>  // offsetof
#include <stdint.h>
#include <string.h>

#include "beacon.h"
#include "config.h"  // BEACON_MAGIC
#include "table.h"

// ---- Sender side ---------------------------------------------------------

// Exact wire length of a MSG_TABLE packet carrying n rows.
inline size_t tableMsgWireLen(uint8_t n) {
  return offsetof(TableMsg, rows) + (size_t)n * sizeof(TableRow);
}

// Number of MSG_TABLE chunks needed to carry `count` rows (0 for an empty
// table — nothing to advertise).
inline uint8_t tableChunkCount(uint8_t count) {
  return (uint8_t)((count + TABLE_ROWS_PER_MSG - 1) / TABLE_ROWS_PER_MSG);
}

// Fill chunk `c` of the table into `m` (full header + chunk indices + rows).
// Returns the wire length to send, or 0 when `c` is out of range (which
// includes every c for an empty table).
inline size_t tableChunkBuild(const LayoutTable& t, uint8_t c, TableMsg& m) {
  uint8_t chunks = tableChunkCount(t.count);
  if (c >= chunks) return 0;
  m.hdr.magic = BEACON_MAGIC;
  m.hdr.version = PROTO_VERSION;
  m.hdr.type = MSG_TABLE;
  m.chunk = c;
  m.chunks = chunks;
  uint8_t start = (uint8_t)(c * TABLE_ROWS_PER_MSG);
  uint8_t n = (uint8_t)(t.count - start);
  if (n > TABLE_ROWS_PER_MSG) n = TABLE_ROWS_PER_MSG;
  m.n = n;
  for (uint8_t i = 0; i < n; i++) {
    memcpy(m.rows[i].mac, t.entries[start + i].mac, 6);
    m.rows[i].x = t.entries[start + i].x;
    m.rows[i].y = t.entries[start + i].y;
  }
  return tableMsgWireLen(n);
}

// ---- Receiver side ---------------------------------------------------------

// Validation step 1, BEFORE copying the packet into a TableMsg: is the length
// inside the struct's bounds at all (header + counts present, no overrun)?
inline bool tableMsgLenPlausible(int len) {
  return len >= (int)offsetof(TableMsg, rows) && len <= (int)sizeof(TableMsg);
}

// Validation step 2, after the copy: the declared row count must be in range
// and match the wire length exactly — rejects truncation, padding, and an `n`
// that would overrun rows[].
inline bool tableMsgLenValid(int len, uint8_t n) {
  if (n > TABLE_ROWS_PER_MSG) return false;
  return len == (int)tableMsgWireLen(n);
}

// Scan a validated chunk for this node's own row. Writes (x, y) and returns
// true when found. (Reads packed members by value — never by reference.)
inline bool tableMsgFindRow(const TableMsg& m, const uint8_t mac[6], float& x,
                            float& y) {
  uint8_t n = m.n > TABLE_ROWS_PER_MSG ? TABLE_ROWS_PER_MSG : m.n;
  for (uint8_t i = 0; i < n; i++) {
    if (memcmp(m.rows[i].mac, mac, 6) == 0) {
      x = m.rows[i].x;
      y = m.rows[i].y;
      return true;
    }
  }
  return false;
}

// ---- Broadcast cadence ------------------------------------------------------

// Steady-state rebroadcast every interval; a burst request (new node in the
// roster) pulls the next broadcast forward but never violates the minimum
// spacing. Zero-init ({0, 0}) makes the first check due immediately, so a
// freshly booted conductor advertises the table right away.
struct TableSched {
  int64_t next_us;      // next steady-state broadcast
  int64_t earliest_us;  // hold-off: no broadcast (even a burst) before this
};

// May a pending burst request be consumed right now? While this returns false
// the caller must LEAVE the request flagged, so the burst fires when the
// hold-off expires instead of being silently dropped.
inline bool tableSchedBurstReady(const TableSched& s, int64_t t) {
  return t >= s.earliest_us;
}

// Should the table be broadcast now? Advances the schedule when it says yes.
inline bool tableSchedDue(TableSched& s, int64_t t, bool burst,
                          int64_t interval_us, int64_t spacing_us) {
  if (t < s.earliest_us) return false;
  if (!burst && t < s.next_us) return false;
  s.next_us = t + interval_us;
  s.earliest_us = t + spacing_us;
  return true;
}
