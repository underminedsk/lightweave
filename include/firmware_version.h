#pragma once

#include <stdint.h>

#ifndef FIRMWARE_BUILD_ID
#define FIRMWARE_BUILD_ID 0x00000000u
#endif

#ifndef FIRMWARE_BUILD_DIRTY
#define FIRMWARE_BUILD_DIRTY 1
#endif

struct FirmwareVersion {
  uint8_t  proto;
  uint32_t build_id;
  uint8_t  dirty;
};

inline FirmwareVersion currentFirmwareVersion(uint8_t proto) {
  FirmwareVersion v = {proto, (uint32_t)FIRMWARE_BUILD_ID, (uint8_t)FIRMWARE_BUILD_DIRTY};
  return v;
}

inline bool firmwareSame(const FirmwareVersion& a, const FirmwareVersion& b) {
  return a.proto == b.proto && a.build_id == b.build_id && a.dirty == b.dirty;
}

inline bool firmwareFleetConsistent(const FirmwareVersion& expected,
                                    const FirmwareVersion* seen,
                                    uint8_t count) {
  for (uint8_t i = 0; i < count; i++) {
    if (!firmwareSame(expected, seen[i])) return false;
  }
  return true;
}
