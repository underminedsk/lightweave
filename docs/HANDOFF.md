# Build Handoff — start here

The "you are here, do this next" doc for a session picking up the build cold.
Design rationale lives in [`ARCHITECTURE.md`](ARCHITECTURE.md); this is state +
next steps only.

**Read order:** this doc → `ARCHITECTURE.md` → `README.md` →
[`FLASHING.md`](FLASHING.md) → [`do_baskets_firmware_brief.md`](do_baskets_firmware_brief.md).

**Repo:** https://github.com/underminedsk/baskets-lights · everything is committed
and pushed; `pio test -e native` (33 pass) and `pio run -e devkitc` build clean.

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
- **Protocol foundation Half 1** (hardware-verified): typed message header
  `{magic, version, type}` with type dispatch; **MAC identity** read at boot and
  shown in `info`; **bidirectional ESP-NOW** — performers unicast `REGISTER`
  every 10 s and the conductor builds a **MAC-keyed roster** (`roster` command).
  Sync hot path unchanged (still `LOCKED gaps` flat after the rework).
- **Protocol foundation Half 2** (hardware-verified): **conductor-authoritative
  `MAC→(x,y)` layout table** in NVS, broadcast as chunked `MSG_TABLE`; a node finds
  its row, adopts its `(x,y)`, and caches it (survives reboot, no laptop needed).
  Conductor edits it with `assign <mac> <x> <y>` / `table` / `forget <mac>`.
  Verified: a node took a position set only on the conductor, no serial to it.
- **GPIO2 heartbeat** blinks on the synced beat (zero-wiring sync check).
- **Serial commands:** `info`, `roster` / `table` / `assign` / `forget`
  (conductor), `role conductor|performer`, `id <n>`, `pos <x> <y>`,
  `pattern <n>`, `bri <n>`, `param <i> <v>`.
- **Host unit tests** (`test/test_logic/`, 33): sync core, pattern math, roster,
  layout table.

**Code layout:** `include/` — `config.h` (pins/constants), `beacon.h` (wire
packets), `sync.h` (clock core, tested), `pattern_math.h` (pure pattern fns,
tested), `patterns.h` (LED binding), `roster.h` + `table.h` (pure, tested),
`identity.h` (NodeIdentity). `src/main.cpp`
is the on-device glue. NVS namespace is `"node"` (keys: `id`, `x`, `y`, `role`,
`pat`/`bri`/`p0`..`p3` for the recipe, `table` blob on the conductor).

**Not built yet:** structured machine Pi↔conductor serial (lands with the Pi UI),
auto-calibration, show program / scheduling, the Pi web UI, power management
(modem-sleep is on, but no deep-sleep/LDR/ADC), OTA.

## Hardware state

- 3× DOIT ESP32 DevKit V1, all on the unified image. Rings on 2 of them; LED data
  on **GPIO13 (`D13`)**, USB 5V (no 12 V / buck yet).
- Provisioned roles/ids/positions may have drifted across sessions — **re-check
  each board with `info`** rather than trusting labels.
- **Gotcha:** factory boards ship with ESP-AT firmware and need a one-time
  `esptool.py --port <P> erase_flash` before our image runs right. Boards report
  the same USB serial, so port names shuffle on replug — flash by current port.
  Full details in `FLASHING.md`.

**Power — first bench measurement (2026-06-28, ET900 on USB 5V):** a *performer*
on a DevKit V1, **LED ring connected, gentle pulsing color-wheel pattern**, draws
**0.17 A = 0.85 W** — the **full per-node draw** (board + radio + LEDs).
**Budget passes:** 0.85 W × 10 h = 8.5 Wh/night (under the 11 Wh target); 138 Wh ÷
0.85 W ≈ **~16 nights** at 10 h/night, even on the inefficient DevKit. Caveats:
(1) confirm 0.17 A as a true average via the ET900 mAh accumulator (LEDs pulse →
current swings); (2) radio likely dominates — **modem-sleep is ineffective in
connectionless ESP-NOW** (`WIFI_PS_MIN_MODEM` set, CPU 160 MHz, but no AP/DTIM so
RX stays on); the real lever is **scheduled light-sleep between beacons** (synced
clock enables it), a Milestone-3 margin item; (3) **FireBeetle** would draw less;
(4) **calendar life still needs daytime deep-sleep** — at 24/7 it's ~6.8 days,
under a 10-night event. Full math in the `power-budget-go-no-go` memory.

## Quick reference

```bash
export PATH="/opt/homebrew/bin:$PATH"
pio test -e native                                  # 33 host tests
pio run -e devkitc                                  # build
pio run -e devkitc -t upload --upload-port /dev/cu.usbserial-XXXX
pio device monitor -p /dev/cu.usbserial-XXXX        # provision + watch
```
Reading serial without resetting the board: see the pyserial snippet in
`FLASHING.md` (opening the port auto-resets; wait ~2 s before typing commands).

---

**⚠ `main.cpp` global-ordering pitfall (for future edits):** NVS helpers must be
defined *below* the global they touch. `patternConfigLoad/Save` sit *after*
`g_beacon`; `tableLoad/Save` need `g_table`, which is declared up top with the
other config globals for exactly this reason. Don't move these loads into
`configLoad()` or forward-declare the globals — a prior attempt broke the build.

## Where to go next — pick one

The protocol foundation (both halves) is **done and hardware-verified**. Candidate
next milestones, see `ARCHITECTURE.md` for each:

- **Auto-calibration** (§6) — build the `MAC→(x,y)` table by survey (drone + CV)
  instead of by hand. The table plumbing it feeds into now exists. Biggest payoff
  for a real 60-node deploy; biggest effort (off-board CV).
- **Pi web UI / control plane** (§5.2) — the laptop-free operator surface. Needs
  the **structured machine Pi↔conductor serial** (bulk `table` get/set, show
  program), which was deliberately deferred to land here.
- **Show program / scheduling** (§4.1) — conductor walks a schedule and broadcasts
  the current recipe; nodes stay dumb. Pure-on-conductor, modest scope.
- **Milestone 3 — power management** — modem-sleep does nothing for connectionless
  ESP-NOW (see the power note above); the real lever is **scheduled light-sleep
  between beacons**. Gates the battery-vs-wired go/no-go (Milestone 4).

## After that

Auto-calibration (drone + CV; `ARCHITECTURE.md` §6) → Pi web UI (§5.2) →
Milestone 3 power management (modem-sleep tuning, dusk deep-sleep, LDR + battery
ADC on GPIO34/35) → Milestone 4 battery + **ET900 draw measurement = battery
vs. wired go/no-go** → Milestone 5 OTA + enclosure.

## Project memory (loaded automatically in this dir)

- `at-firmware-erase-flash` — erase new boards first; serial-port shuffle.
- `power-budget-go-no-go` — ET900 measurement plan, ~11 Wh/night target.
- `design-discussion-style` — in design mode, recommend in prose (no question widgets).
