// MAC address text parsing/formatting.
//
// Dependency-free (stdio only) and host-tested like sync.h / table.h. parseMac
// is the gatekeeper for the conductor's `assign`/`forget` commands — a silent
// misparse would move the wrong lantern — so it gets tests. main.cpp just calls
// these; there is no device-side logic here.
#pragma once

#include <stdint.h>
#include <stdio.h>

// Parse "AA:BB:CC:DD:EE:FF" (any case) into 6 bytes. Returns false on anything
// malformed: too few groups, a non-hex group, a group value above 0xFF, or
// trailing characters after the sixth group (a pasted EUI-64 or concatenated
// string must be rejected whole, never silently truncated to its prefix MAC —
// that would move the wrong lantern). The %n + terminator check enforces the
// full-token match; sscanf alone stops at the sixth conversion and ignores the
// rest.
inline bool parseMac(const char* s, uint8_t out[6]) {
  unsigned v[6];
  int used = 0;
  if (sscanf(s, "%x:%x:%x:%x:%x:%x%n", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5],
             &used) != 6)
    return false;
  if (s[used] != '\0') return false;
  for (int i = 0; i < 6; i++) {
    if (v[i] > 255) return false;
    out[i] = (uint8_t)v[i];
  }
  return true;
}

// Format a MAC into a caller-supplied 18-byte buffer ("AA:BB:CC:DD:EE:FF").
// Returns the buffer so it can be used inline in a printf argument list.
inline const char* macStr(const uint8_t mac[6], char out[18]) {
  snprintf(out, 18, "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2],
           mac[3], mac[4], mac[5]);
  return out;
}
