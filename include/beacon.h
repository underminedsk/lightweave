// Wire protocol for the ESP-NOW messages nodes exchange.
//
// Every packet starts with a common MsgHeader {magic, version, type}. The
// receiver validates magic + version, then dispatches on `type` to the matching
// payload struct. The clock beacon (MSG_BEACON) is the hot path — broadcast a few
// times a second and followed by every performer — so it stays small. Everything
// else (REGISTER, and later ROSTER/TABLE) is occasional control traffic.
//
// All structs are packed so the wire layout is identical on every node
// regardless of compiler padding, and every message stays well under the
// 250-byte ESP-NOW payload limit. The magic constant lives in config.h
// (BEACON_MAGIC); the version is bumped here whenever the layout changes.
#pragma once

#include <stdint.h>

// Bumped on any incompatible wire-layout change. Receivers reject a mismatch
// rather than misparse a packet from a node on different firmware. Also reported
// in REGISTER so the conductor can spot a straggler running stale firmware.
static constexpr uint8_t PROTO_VERSION = 1;

enum MsgType : uint8_t {
  MSG_BEACON   = 0,  // conductor -> all: clock + pattern recipe (hot path)
  MSG_REGISTER = 1,  // performer -> conductor: announce my MAC + firmware
  MSG_ROSTER   = 2,  // conductor -> all: finalized roster        (Half 2)
  MSG_TABLE    = 3,  // conductor -> all: MAC->(x,y) layout chunk  (Half 2)
  MSG_ACK      = 4,  // generic acknowledgement                   (Half 2)
};

typedef struct __attribute__((packed)) {
  uint32_t magic;    // BEACON_MAGIC — reject anything else
  uint8_t  version;  // PROTO_VERSION — reject a mismatch
  uint8_t  type;     // MsgType
} MsgHeader;

// type = MSG_BEACON. The conductor's clock plus the pattern recipe every node
// renders. Fields after the header are unchanged from the original beacon, so
// the sync hot path (sync.h consumes epoch_us + seq) is untouched.
typedef struct __attribute__((packed)) {
  MsgHeader hdr;
  int64_t   epoch_us;    // conductor's esp_timer clock at send time
  uint16_t  pattern_id;  // which pattern to render
  uint8_t   brightness;  // global brightness cap (0-255)
  uint8_t   palette_id;  // palette selector
  uint16_t  params[4];   // pattern-specific knobs for live tweaking
  uint32_t  seq;         // monotonic; for drop detection / logging
} BeaconMsg;

// type = MSG_REGISTER. A performer unicasts this to the conductor when it hears a
// beacon, so the conductor can build a roster keyed on the node's MAC (its stable
// identity). Sent periodically so a restarted conductor rebuilds the roster.
typedef struct __attribute__((packed)) {
  MsgHeader hdr;
  uint8_t   mac[6];  // sender's WiFi STA MAC — the node's stable identity
  uint16_t  id;      // human label (0 if unprovisioned)
  uint8_t   fw;      // sender's PROTO_VERSION (firmware/protocol marker)
} RegisterMsg;

// One row of the layout table on the wire: a node's MAC and its (x,y) position.
typedef struct __attribute__((packed)) {
  uint8_t mac[6];
  float   x;
  float   y;
} TableRow;  // 14 bytes

// Rows per MSG_TABLE packet. ESP-NOW caps the payload at 250 B; the header + chunk
// fields are 9 B, so (250 - 9) / 14 = 17 rows fit (a 247 B packet at full).
static constexpr uint8_t TABLE_ROWS_PER_MSG = 17;

// type = MSG_TABLE. The conductor's authoritative MAC->(x,y) map, broadcast in
// chunks (a 60-node table won't fit one packet). A node scans every chunk for its
// own MAC and adopts + caches its (x,y). `chunk`/`chunks` let a receiver tell how
// much of the table it has seen; positions are static, so it is sent occasionally.
typedef struct __attribute__((packed)) {
  MsgHeader hdr;
  uint8_t   chunk;   // this chunk's index, 0..chunks-1
  uint8_t   chunks;  // total chunks in the table this round
  uint8_t   n;       // rows present in this packet (<= TABLE_ROWS_PER_MSG)
  TableRow  rows[TABLE_ROWS_PER_MSG];
} TableMsg;
