# Build Handoff — start here

The "you are here, do this next" doc for a session picking up the build cold.
Design rationale lives in [`ARCHITECTURE.md`](ARCHITECTURE.md); this is state +
next steps only.

**Read order:** this doc → `ARCHITECTURE.md` → `README.md` →
[`FLASHING.md`](FLASHING.md) → [`do_baskets_firmware_brief.md`](do_baskets_firmware_brief.md).

**Repo:** https://github.com/underminedsk/baskets-lights · everything is committed
and pushed; `pio test -e native` (25 pass) and `pio run -e devkitc` build clean.

---

## Where the build is right now

**Done & hardware-verified (Milestones 1–2):**
- **One firmware image for every node** (`src/main.cpp`); role is a runtime NVS
  value (default performer), set over serial. Build envs: `devkitc`, `firebeetle`,
  `native`.
- **Sync:** conductor broadcasts a clock beacon; performers lock an offset and
  render against synced time; **free-run on missed beacon** (no blackout), re-lock
  on return. Verified: `LOCKED`, stable offset ~±100 µs, `gaps=0`.
- **Patterns** (`f(x,y,t)`): `PULSE` (uniform breathing), `PALETTE_DRIFT` (smooth
  rainbow hue cycle; `params[0]`=period ms, `params[1]`=spatial hue offset ×100 so
  the rainbow travels or runs in unison), and `SWEEP` (1-D traveling wave).
  Conductor broadcasts the recipe (`pattern_id`/`brightness`/`params[4]`);
  performers render it.
- **NVS identity:** `id` + `(x,y)` persist across reboot; set over serial.
- **Pattern config persists** too: `pattern_id`/`brightness`/`params` survive a
  power-cycle (keys `pat`/`bri`/`p0`..`p3` in the `"node"` namespace).
- **GPIO2 heartbeat** blinks on the synced beat (zero-wiring sync check).
- **Serial commands:** `info`, `role conductor|performer`, `id <n>`,
  `pos <x> <y>`, `pattern <n>`, `bri <n>`, `param <i> <v>`.
- **Host unit tests** for the sync core + pattern math (`test/test_logic/`).

**Code layout:** `include/` — `config.h` (pins/constants), `beacon.h` (wire
packet), `sync.h` (clock core, tested), `pattern_math.h` (pure pattern fns,
tested), `patterns.h` (LED binding), `identity.h` (NodeIdentity). `src/main.cpp`
is the on-device glue. NVS namespace is `"node"` (keys: `id`, `x`, `y`, `role`).

**Not built yet:** MAC identity, the layout table, multi-message/bidirectional
protocol (next), auto-calibration, show program / scheduling, the Pi web UI,
power management (modem-sleep is on, but no deep-sleep/LDR/ADC), OTA.

## Hardware state

- 3× DOIT ESP32 DevKit V1, all on the unified image. Rings on 2 of them; LED data
  on **GPIO13 (`D13`)**, USB 5V (no 12 V / buck yet).
- Provisioned roles/ids/positions may have drifted across sessions — **re-check
  each board with `info`** rather than trusting labels.
- **Gotcha:** factory boards ship with ESP-AT firmware and need a one-time
  `esptool.py --port <P> erase_flash` before our image runs right. Boards report
  the same USB serial, so port names shuffle on replug — flash by current port.
  Full details in `FLASHING.md`.

## Quick reference

```bash
export PATH="/opt/homebrew/bin:$PATH"
pio test -e native                                  # 25 host tests
pio run -e devkitc                                  # build
pio run -e devkitc -t upload --upload-port /dev/cu.usbserial-XXXX
pio device monitor -p /dev/cu.usbserial-XXXX        # provision + watch
```
Reading serial without resetting the board: see the pyserial snippet in
`FLASHING.md` (opening the port auto-resets; wait ~2 s before typing commands).

---

**⚠ NVS pattern-config pitfall (for future edits to `main.cpp`):** `configLoad()`
is defined *above* `g_beacon`, so it can't touch it. The pattern recipe is
loaded/saved by separate `patternConfigLoad()` / `patternConfigSave()` defined
*after* `g_beacon` (called from `setup()` and the `pattern`/`bri`/`param`
handlers). Don't move pattern loads into `configLoad()` or forward-declare
`g_beacon` — a prior attempt broke the build that way.

## Next task (big): protocol foundation

The substrate for calibration, node replacement, and the web UI. See
`ARCHITECTURE.md` §3, §5, §7 for the why. Concrete plan:

1. **Typed message header.** Rework `beacon.h` into a common header
   `{uint32 magic; uint8 version; uint8 type;}` + per-type payload. Keep the
   current beacon as `type=BEACON` (hot path). Add types: `REGISTER`, `ROSTER`,
   `ASSIGN_POS`/`TABLE`, `ACK`, `CALIB_START`.
2. **MAC as identity.** `esp_read_mac(mac, ESP_MAC_WIFI_STA)`; key everything on
   MAC (keep `id` as a human label).
3. **Bidirectional ESP-NOW.** In the recv callback, grab the sender MAC (already
   handling the 2.x/3.x cb signature split), add the conductor as a peer, and
   unicast `REGISTER {mac, fw}` so the conductor builds a roster.
4. **Routing table in conductor NVS.** Store as a blob of `{uint8 mac[6]; float x;
   float y;}` rows. Broadcast in chunks (≤~250 B → ~17 rows/packet), occasionally.
   Each node scans for its MAC and caches `(x,y)` to NVS. Conductor is
   authoritative; field runs with no laptop.
5. **Structured serial protocol (Pi↔conductor).** Beyond the human commands, add a
   machine protocol with bulk **table get/set** (60 rows won't fit a typed line),
   show-program get/set, and calibration control, with clean acks/errors.

## After that

Auto-calibration (drone + CV; `ARCHITECTURE.md` §6) → Pi web UI (§5.2) →
Milestone 3 power management (modem-sleep tuning, dusk deep-sleep, LDR + battery
ADC on GPIO34/35) → Milestone 4 battery + **ET900 draw measurement = battery
vs. wired go/no-go** → Milestone 5 OTA + enclosure.

## Project memory (loaded automatically in this dir)

- `at-firmware-erase-flash` — erase new boards first; serial-port shuffle.
- `power-budget-go-no-go` — ET900 measurement plan, ~11 Wh/night target.
- `design-discussion-style` — in design mode, recommend in prose (no question widgets).
