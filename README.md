# Do Baskets Dream — Firmware

Node firmware for a Burning Man art installation: a field of 50–60 battery-powered
LED lanterns that play coordinated patterns (slow pulses, palette drifts) sweeping
across the physical field. Each lantern is an independent node; coordination is
wireless via **ESP-NOW** — there is no wiring between nodes.

Built with **PlatformIO** (Arduino framework) for the ESP32.

## Status

**Milestone 1 — sync proof: ✅ verified on hardware.** 1 conductor + 2 performers
over ESP-NOW. A performer locks to the conductor's clock within ~1 s (FREE-RUN →
LOCKED), holds a stable offset to within ~±100 µs of jitter, with `gaps=0`. The
GPIO2 heartbeat blinks at 1 Hz in unison across all nodes.

Verified board: DOIT ESP32 DevKit V1 (CP2102, GPIO2 user LED present). LED data
on GPIO13 (`D13`), powered from USB 5V.

See [`docs/do_baskets_firmware_brief.md`](docs/do_baskets_firmware_brief.md) for the
full project brief.

## Hardware (per node)

| Function | Part | Connection |
|---|---|---|
| MCU | ESP32-WROOM-32 (DevKitC or FireBeetle 2 ESP32-E) | — |
| LEDs | 16× SK6812 RGBW ring | GPIO13 (`D13`), 470Ω series resistor |
| Dusk sensor | LDR + 10kΩ divider | GPIO34 (ADC1) |
| Battery sense | 47kΩ/10kΩ divider off 12V | GPIO35 (ADC1) |
| Power | 12V LiFePO4 → buck → 5V | 5V to ESP32 VIN + ring; common ground |

> **Hard constraint:** sensing must use ADC1 pins (GPIO 32–39). ADC2 stops working
> when the radio is active.

## Build & test

```bash
pio run -e devkitc-conductor          # build conductor firmware
pio run -e devkitc-performer          # build performer firmware
pio run -e devkitc-conductor -t upload --upload-port /dev/cu.usbserial-XXXX
pio device monitor                    # watch sync diagnostics
pio test -e native                    # host unit tests — no hardware needed
```

### Visual sync proof with no LED wiring

The built-in board LED (GPIO2) blinks at 1 Hz on the synced beat, so two bare
boards blink in unison the moment they're in sync — a zero-wiring bring-up check.
Disable later with `-D HEARTBEAT_LED=0`; if your board's LED is inverted, set
`HEARTBEAT_ACTIVE_LOW` in `include/config.h`.

### Unit tests

See [`docs/FLASHING.md`](docs/FLASHING.md) for the flashing/bring-up runbook —
including the one-time `erase_flash` every factory-fresh board needs, the
duplicate-serial-port gotcha, and how to read the sync diagnostics.

The sync core (`include/sync.h`) and pattern math (`include/pattern_math.h`) are
dependency-free and unit-tested on the host. Those tests cover the subtle,
silently-failing logic — clock offset, free-run-on-missed-beacon, seq-gap
detection across `uint32` wrap, phase continuity. They do **not** cover radio
range, real packet loss, or on-chip timing; those require field testing.

## Roadmap

1. **Sync proof** — conductor + 2 performers, synchronized pulse, serial diagnostics.
2. (x,y) NVS identity + a pattern that sweeps across nodes by position.
3. Power management (modem-sleep, 160MHz, dusk deep-sleep) + LDR/battery ADC.
4. Battery power; validate runtime against the energy budget.
5. OTA via on-demand AP; enclosure.
