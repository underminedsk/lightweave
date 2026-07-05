# Build Handoff — start here

The "you are here, do this next" doc for a session picking up the build cold.
Design rationale lives in [`ARCHITECTURE.md`](ARCHITECTURE.md); this is state +
next steps only.

**Read order:** this doc → `ARCHITECTURE.md` → `README.md` →
[`FLASHING.md`](FLASHING.md) → [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md).

**Repo:** https://github.com/underminedsk/baskets-lights · `pio test -e native`
(**83 pass**) and all four device envs (`devkitc` / `firebeetle` / `field-*`)
build clean. Latest on `main` (2026-07-04): **review-debt paydown** — the
host-unreachable logic extracted out of `main.cpp` into pure tested headers
(`macaddr.h`, `table_wire.h`, `bootplan.h`, `patternBootSafe`), `field-*` build
envs (`-D HEARTBEAT_LED=0`), and the table rebroadcast stretched 5 s → 60 s
steady-state with **targeted single-row replies to needy REGISTERs** (the
initial new-MAC-burst design was replaced the same day after an 8-angle
adversarial review confirmed three delivery holes in it — see "Self
code-review" below). Before that: **INA228
power-telemetry firmware (code-complete, host-tested, 8-angle-reviewed — built
ahead of the chip, see "INA228 power telemetry" below)**,
Stage-A radio duty-cycle (measured), **Stage-B CPU light-sleep
(hardware-verified on bench 2026-07-03)**, **Lever-2 daytime deep-sleep
(code-complete, default off, awaiting the pilot phototransistors)**, a
full-repo adversarial self-review with all 5 correctness findings fixed, the
production BOM, and the **pilot-batch order placed 2026-07-03** (most parts
arrive Mon Jul 6, batteries Jul 10 — see "Pilot batch: ORDERED" below).

## ▶ Next session: pick up here (updated 2026-07-04)

Priority order (the INA228 firmware and the review-debt paydown, previously
items here, both landed 2026-07-04):
1. **Monday (parts in hand):** wire a phototransistor → calibrate
   `DUSK_DAY_MV`/`DUSK_NIGHT_MV`/`DUSK_DAY_ABOVE` against the real divider →
   verify the full dusk sleep → timer-wake → re-sleep → `wake on` summon cycle;
   wire INA228 on one reference node (SDA→21, SCL→22, chip in series between
   battery+ and buck input) → run the INA228 bench checklist below → first
   real Wh integral; first flash of the `firebeetle` env on real FireBeetle
   hardware. **Plus one new 2-minute check:** with the conductor up and a
   table row assigned, `erase_flash` + reflash a performer and confirm it
   re-adopts its position within ~10 s of registering (the new single-row
   `[table]` reply; code-reviewed + host-tested but the radio path itself
   isn't hardware-verified yet).
2. **User task, anytime (needs hands + DMM):** re-measure the 12 V
   battery-side draw with naps running, **USB disconnected** (USB backfeeds the
   5 V rail and corrupts the reading) — quantifies the Stage-B win vs the old
   51 mA rest / 55 mA avg numbers. Same scene for apples-to-apples: amber GLOW
   @ bri 48.

---

## Where the build is right now

**Done & hardware-verified** (Milestones 1–2 → symmetric-role refactor → rainbow +
pattern persistence → protocol foundation Half 1 & 2 → battery go/no-go + worst-case
power measurement):
- **One firmware image for every node** (`src/main.cpp`); role is a runtime NVS
  value (default performer), set over serial. Build envs: `devkitc`, `firebeetle`,
  `native`, plus `field-devkitc` / `field-firebeetle` — identical firmware with
  `-D HEARTBEAT_LED=0` (the onboard blink is invisible inside an opaque lantern,
  burns LED current, and caps every Stage-B nap at 500 ms). Bench flashes keep
  the heartbeat; flash `field-*` for deployment.
- **Sync:** conductor broadcasts a clock beacon; performers lock an offset and
  render against synced time; **free-run on missed beacon** (no blackout), re-lock
  on return. Verified: `LOCKED`, stable offset ~±100 µs, `gaps=0`.
- **Patterns** (`f(x,y,t)`): `PULSE` (uniform breathing), `PALETTE_DRIFT` (smooth
  rainbow hue cycle; `params[0]`=period ms, `params[1]`=spatial hue offset ×100 so
  the rainbow travels or runs in unison), `SWEEP` (1-D traveling wave), and
  `SOLID` (`pattern 3`: every pixel full RGBW — the worst-case power draw, for
  bench-measuring the LED ceiling). Conductor broadcasts the pattern
  (`pattern_id`/`brightness`/`params[4]`); performers render it. Every node
  hard-clamps brightness to `MAX_BRIGHTNESS` (config.h, **192**) so no pattern can
  exceed the per-node power budget (see the worst-case measurement below).
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
  `pattern <n>`, `bri <n>`, `param <i> <v>`, `powersave on|off`,
  `dusk on|off` (performer; daytime deep-sleep, default off),
  `wake on|off` (conductor; FIELD_AWAKE beacon flag, summons dusk-sleeping
  nodes), `power` / `power reset` (INA228 nodes; print / zero the energy
  accumulators). Note diag output is gated: it prints only within **5 min of
  serial input** — hit Enter in a monitor to revive a quiet node (see
  FLASHING.md). Exception: the conductor's `[power]` telemetry log is
  deliberately ungated (it's the overnight audit trail).
- **Wire protocol is v2** (`PROTO_VERSION 2`, beacon grew a `flags` byte for
  FIELD_AWAKE). v1 and v2 nodes silently reject each other — **flash every
  board together** (all 3 bench boards are on v2 as of 2026-07-03).
- **Host unit tests** (`test/test_logic/`, 83): sync core, pattern math, roster,
  layout table, radio duty-cycle, nap scheduler (Stage B), dusk detector +
  fail-awake gates (Lever 2), pattern static-ids + boot-guard, glow warm-hue
  color, power telemetry (conversions / plausibility gate / report scheduler),
  MAC text parsing, table wire (chunking / length validation / own-row scan /
  row-reply decision + builder), and boot classification.

**Hardware-verified (2026-06-28) — Milestone 3, Lever 1, Stage A (performer radio
duty-cycle):** a performer powers the radio **down** between brief listen windows
and keeps rendering from the synced clock, attacking the RX-dominated night draw.
Logic is the dependency-free `include/powersave.h` (5 host tests): a state machine
that holds the radio ON to acquire the first beacon, then cycles `DUTY_LISTEN_US`
ON (600 ms, spans ~2 of the 4 Hz beacons) / `DUTY_OFF_US` OFF (4 s) — ~13% radio
duty. `main.cpp` does the teardown/bring-up (`radioSleep`/`radioWake`:
`esp_wifi_stop`/`start`, re-adding the broadcast peer + recv-cb and re-flagging the
conductor unicast peer on each wake), gates TX on the radio being up, and feeds
caught-beacon events back to the scheduler. Conductor is exempt (gated on
`role == performer`). Toggle live with `powersave on|off` (persisted, NVS key
`ps`; default ON). The `[duty]` diag line reports `radio=ON/off` + `windows`/
`missed`.

**Bench result (conductor + duty-cycling performer, both DevKitC on USB):** radio
cycles ~0.6 s ON / ~4 s OFF as designed; **0% missed windows in steady state**
(every window catches a beacon and re-locks); the node free-runs the render across
each OFF with no blackout; wake reliably rebuilds the peer table (rx climbs across
sleeps); and the performer still appears in the conductor's `roster` (~10 s
cadence, mildly stretched because TX only happens during a window). The only
misses seen were transient, while the conductor itself was being reset during
setup. Note `gaps` increments ~once per wake (the first beacon after a 4 s sleep
has skipped seq numbers) — that's expected with duty-cycling and benign; the
`missed` counter is the meaningful health metric now, not `gaps`.

**SOLID boot-guard (same change):** a node never *boots* into `SOLID` (pattern 3,
full-white worst case) — `patternConfigLoad` falls a persisted SOLID back to
`SWEEP`, so a power-cycle can't leave a node draining the battery on all four
channels. `pattern 3` still works live for a deliberate on-bench measurement.

**Power result — MEASURED (2026-06-28, 12 V battery-side DMM, one DevKitC performer
locked to a beaconing conductor, steady-amber `GLOW` @ bri 48):**
- Radio **off** (rest, ~87% of the cycle): **51 mA @ 12 V**; radio **on** (the
  ~600 ms listen window, ~13%): **85 mA**. powersave-on **average ≈ 55 mA (~0.74 W
  @ 13.4 V)**; powersave-off pins the radio at the **85 mA** level (~1.14 W).
- Radio RX term = 85 − 51 ≈ **34 mA**; duty-cycling pays it only ~13% of the time
  → **saves ~30 mA @ 12 V (~0.4 W), ~35% of node draw.** The always-on 85 mA
  matches the original go/no-go's ~83 mA baseline (rigs agree).
- **Battery life (138 Wh, 10 h/night): ~12 → ~19 nights (~1.5×);** 24/7 calendar
  ~5 → ~7.8 days.
- **Why ~1.5× and not more:** the saving is a *fixed* ~30 mA radio term, but the
  ~51 mA rest floor (LEDs + CPU) now dominates, so the duty-cycle % scales inversely
  with LED load — a bigger win on dim shows. **Radio is no longer the dominant
  term;** the next levers are LED brightness, Stage B (CPU light-sleep to cut the
  floor), and Lever 2 (daytime deep-sleep).
- A *dim/5 V-side* sanity run under pulsing-white earlier showed the radio blip
  clearly (off-floor 0.05 A vs on 0.09–0.17 A); consistent with the above.

Measurement gotcha confirmed: do the battery reading with **USB disconnected**
(USB backfeeds 5 V into the DevKit and corrupts the 12 V draw) — powersave persists
in NVS, so set the mode over serial, then unplug and read. And never leave a USB
power meter inline on the data path (corrupts the UART / browns out radio init —
cost us a session; see FLASHING.md).

**A new show pattern landed alongside this:** `GLOW` (`pattern 4`) — a steady solid
color at a fixed hue, no time term, so the field holds one calm color with a *flat*
(non-pulsing) draw. `params[0]` = hue degrees (30 orange / 40 amber / 50 yellow),
`params[1]` = saturation %. Used as the realistic-conservative power-test scene; also
a genuine warm/gentle show pattern. Host test covers the warm-hue color math (39
tests now).

**INA228 power telemetry — CODE-COMPLETE 2026-07-04, host-tested (65 native
tests green), both device envs build — NOT hardware-verified (chip arrives with
Monday's order).** ARCHITECTURE §4.2. Reviewed same day (8-angle /code-review,
10 verified candidates → 4 fixes applied: avg-W plausibility bound, time-gate
before the spinlock in maybePowerReport, PowerSample embedded in PowerMsg,
conductor self-log on the tested scheduler). What landed:
- `include/powermon.h` (pure, 7 host tests): J→Wh / C→mAh / avg-W conversions,
  a plausibility gate (NaN/inf/orders-of-magnitude nonsense gets logged but
  flagged `** IMPLAUSIBLE`, never trusted into the budget — including the
  reboot-inflated avg-W case: accumulator survives an ESP32 reset while the
  elapsed anchor restarts, so a whole night's Joules over seconds would read
  as kW; the gate bounds avg at 50 W), and the report scheduler (fires only
  while the radio is up + conductor peer exists, catches up with exactly ONE
  report after a long radio-off span — no bursts).
- Wire: `MSG_POWER` unicast performer→conductor on the existing REGISTER path;
  the payload IS the embedded `PowerSample` struct (one field list, sender and
  receiver can't drift; byte-identical layout — all members 4-byte aligned).
  **No PROTO_VERSION bump** — new type only; v2 receivers without the handler
  ignore it via the dispatch default. The peer-add logic is now shared
  (`conductorPeerReady`).
- Glue (`main.cpp`): I2C probe at boot (`Wire` on SDA 21 / SCL 22; a node
  without the chip fails the probe in ~ms and stays silent — one image
  everywhere). **`begin(..., skipReset=true)` is load-bearing:** the chip stays
  battery-powered across an ESP32 reset, and the lib's default begin()
  hardware-resets it — which would wipe the night's Wh the moment a serial
  monitor's DTR auto-reset hits. Zeroing is only ever explicit (`power reset`).
  Continuous conversion mode is set explicitly (triggered mode invalidates the
  hardware accumulators). Conductor drains reports from a spinlocked queue and
  logs `[power] <mac> E=… Wh avg=… W Q=… mAh V=… I=… (elapsed)` — ungated by
  the serial-activity window (it IS the overnight audit log). A conductor
  carrying the chip logs its own line on the same 60 s cadence.
- Caveat (documented in the code): `elapsed_s` restarts on reboot while the
  chip keeps accumulating, so after an unplanned mid-night reboot the energy
  total is still right but avg-W overstates until the next `power reset`.
- Overnight flow: `power reset` at dusk → run untethered → morning: reconnect
  USB and read with the **no-reset pyserial trick** (FLASHING.md) *or* just
  read the conductor's scrollback; a DTR reset no longer zeroes the chip, only
  the elapsed anchor.

**INA228 bench checklist (Monday, chip in hand):** (1) wire VCC→3V3, GND→GND,
SDA→21, SCL→22, shunt in series battery+ → buck input; (2) boot log shows
`[power] INA228 found`; `info` shows `ina228=yes`; (3) `power` prints a sane
line (V≈13.4, I≈55 mA on the GLOW scene); (4) cross-check E against the DMM/
ET900 over ~30 min; (5) confirm a `[power]` report lands in the conductor log
~every 60 s with powersave on (deferred through radio-off spans); (6) DTR-reset
the node mid-run and confirm E survives (only elapsed resets); (7) `power
reset` then verify E climbs from 0.

**Code layout:** `include/` — `config.h` (pins/constants), `beacon.h` (wire
packets), `sync.h` (clock core, tested), `pattern_math.h` (pure pattern fns,
tested), `patterns.h` (LED binding), `roster.h` + `table.h` (pure, tested),
`table_wire.h` (table chunking / validation / broadcast cadence, pure, tested),
`powersave.h` (radio duty-cycle schedule, pure, tested), `powermon.h` (INA228
telemetry logic, pure, tested), `macaddr.h` (MAC text parse/format, pure,
tested), `bootplan.h` (Lever-2 boot classification, pure, tested), `identity.h`
(NodeIdentity). `src/main.cpp`
is the on-device glue. NVS namespace is `"node"` (keys: `id`, `x`, `y`, `role`,
`pat`/`bri`/`p0`..`p3` for the pattern, `table` blob on the conductor).

**Not built yet:** structured machine Pi↔conductor serial (lands with the Pi UI),
auto-calibration, show program / scheduling, the Pi web UI, OTA. (INA228
telemetry firmware IS built now — see the section above; it awaits the physical
chip.)

## Hardware state

- 3× DOIT ESP32 DevKit V1, all on the unified image. Rings on 2 of them; LED data
  on **GPIO13 (`D13`)**, USB 5V (no 12 V / buck yet).
- **As of 2026-07-03 (all reflashed to protocol v2 together):**
  - `8C:94:DF:57:7F:14` — **CONDUCTOR**, id 0, GLOW pattern (pat 4, bri 48,
    params [40 100]). Needed a fresh `erase_flash` (clock-scramble gotcha) —
    **its NVS layout table was lost; re-`assign` positions when they matter.**
  - `30:76:F5:93:67:3C` — performer, id 2.
  - `8C:94:DF:8F:71:50` — performer, id 0 (fully erased today —
    **unprovisioned**; set `id`, and position comes from the conductor table).
- Provisioned roles/ids/positions may have drifted across sessions — **re-check
  each board with `info`** rather than trusting labels.
- **Gotcha:** factory boards ship with ESP-AT firmware and need a one-time
  `esptool.py --port <P> erase_flash` before our image runs right. Boards report
  the same USB serial, so port names shuffle on replug — flash by current port.
  Full details in `FLASHING.md`.

**Power — battery go/no-go MEASURED (2026-06-28): GO (nighttime).** Wired the real
battery → buck → node chain (12 Ah LiFePO4 at 13.43 V → UCTRONICS 9–36V→5V buck →
DevKit performer + pulsing ring) and measured the **12V input** current with a DMM
in series. **Loaded draw ~83 mA → ~1.11 W total, converter included**; buck idle
(no board) 8.7 mA ≈ 0.12 W; **converter efficiency ~77%** (load = 0.855 W full-hour
ET900 integral ÷ 1.11 W input; UCTRONICS is fine — keep it). **~11 Wh/night →
138 Wh ÷ 1.11 W ≈ ~12 nights** at 10 h/night, clears the 10-night target. The
5V-side ET900 reading was the *load only* (0.855 W); this 12V number is
authoritative. Caveats: (1) **calendar life still needs
daytime deep-sleep** — at 24/7 it's ~5 days, under a 10-night event. The photodiode
sets the duty cycle: at BRC (BM 2026 = Aug 30–Sep 7, ~40.8 °N) darkness is ~11 h
sunset→sunrise, so a dusk-tripped LDR runs **~10–10.5 h on / ~13.5 h asleep** —
the 10 h/night assumption holds; use 10.5 h for the post-M3 recompute (math in the
`power-budget-go-no-go` memory); (2) radio
likely dominates the draw — **modem-sleep is ineffective in connectionless ESP-NOW**
(`WIFI_PS_MIN_MODEM` set, CPU 160 MHz, but no AP/DTIM so RX stays on); the real
lever is **scheduled light-sleep between beacons** (synced clock enables it), the
Milestone-3 power item; (3) **FireBeetle** would draw less still. Full math in the
`power-budget-go-no-go` memory.

**Worst-case measured (2026-06-28, ET900 @ 5V):** `SOLID` (`pattern 3`) at
`bri 255` — every pixel full RGBW white — drew **0.76 A @ 5V = 3.8 W → ~4.6 W @ 12V
→ ~3 nights sustained** (~4× the colored show, since white lights all 4 channels).
**Battery GO holds** — even pathological all-white never fails in one night, and
0.76 A is well inside the buck/USB limits (so the cap is policy, not safety).
Decision: **`MAX_BRIGHTNESS = 192`** keeps worst case ~3.8 nights while barely
dimming real shows. Watts are fine; the gating issue is *hours* → daytime sleep.

## Quick reference

```bash
export PATH="/opt/homebrew/bin:$PATH"
pio test -e native                                  # 83 host tests
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

## Pilot batch: ORDERED 2026-07-03 (receipts in `receipts/`)

The **`docs/BOM.md` → "Pilot batch (5–7 units)"** order went out 2026-07-03,
across three carts (~$732 incl. tax/shipping):
- **DFRobot** — 6× FireBeetle 2 ESP32-E, $57 (invoice `366209`).
- **Adafruit** — 4× SK6812 **RGBW** rings (PID 2855, Natural White) + **2×
  INA228** total across two invoices (`3705934`, `3705946`). The first invoice
  was a mis-order (**RGB** PID 1463 rings) corrected 33 min later — ⚠ **confirm
  the RGB order was cancelled/refunded.** 2 INA228s is fine (plan is 1–2
  instrumented reference nodes).
- **Amazon** (`111-8959596-4536221`, $600.40) — 5× TalentCell LF120A1 batteries
  (arriving Jul 10), 3× buck 2-packs, CanaKit Pi 3 B+ kit, preloaded Pi OS SD
  card, 74AHCT125 10-pack, perfboard 30-pack, PT334-6C phototransistors,
  resistor kit, 1000 µF caps, toggle switches, plus beyond-BOM extras: 5× IP65
  junction boxes, grommet kit, silicone sealant, and a 150 A inline power
  analyzer. Most of it arriving Mon Jul 6.

**Follow-ups from the receipts:**
- ⚠ **The battery line is on Subscribe & Save ("every 2 weeks") — cancel the
  subscription after the first delivery** or it re-ships 5 batteries (~$220)
  every two weeks.
- Ordered **5 batteries / 4 RGBW rings**, not 6 — the 6th node is covered by
  already-owned bench hardware (2 rings are mounted on the DevKitC boards).
- **Not ordered anywhere: JST-SM connector kit and fuse holders + fuses** (BOM
  pilot rows 10–11) — add to a future cart before field wiring.

## Milestone 3 detail — power management (Stage A measured, Stage B verified, Lever 2 awaiting sensors; next-session order is at the top of this doc)

**Lever 1 Stage A (performer radio duty-cycle) is done, hardware-verified, measured,
and pushed** (`main` @ `5089d33`) — see the bench result near the top. It cut node
draw ~35% (85 → ~55 mA @ 12 V, ~12 → ~19 nights). **The radio is no longer the
dominant term.**

**Power budget now breaks into three roughly co-equal terms** (measured @ 5 V on a
DevKitC): **CPU + board ~50 mA** (incl. the DevKit's power LED + CP2102 USB chip),
**LEDs ~50 mA** (amber `GLOW` @ bri 48), **radio RX ~70 mA when on** (now paid only
~13% of the time). So the remaining levers, roughly in impact order:
1. **CPU floor** — the largest *constant* draw now. Attacked by Stage B and, more
   powerfully, by **scheduled-wake + deep-sleep for static scenes** (next section).
2. **LED brightness** — pure policy knob.
3. **Daytime deep-sleep (Lever 2)** — the calendar-life fix (24/7 is still ~5 days).

⏳ **Quick measurement still owed:** the CPU floor above was read *through USB*, which
includes CP2102 + power-LED overhead absent on battery. Re-measure `bri 0` rest on
the **12 V battery rig, USB disconnected** for the true MCU floor before sizing the
sleep work. (FireBeetle, the M4 candidate, has a lower quiescent draw and shrinks
this floor further.)

**INA228 precision power monitor: firmware DONE (2026-07-04)** (see the
"INA228 power telemetry" section above, `PROJECT_BRIEF.md` readout-path
section, and `ARCHITECTURE.md` §4.2) — an I2C breakout with hardware
energy/charge accumulation, wired in series between battery+ and the buck
input on 1–2 reference nodes. It replaces one-off ET900/DMM snapshots with a
true continuous Wh integral per night, and instrumented performers report
accumulated Wh to the conductor over ESP-NOW (`MSG_POWER`) so every overnight
sync test doubles as a fleet-wide power audit. Awaiting the physical chip
(Monday's order) — the bench checklist is in the section above.

### Lever 1 (do first): radio off between beacons — performer-only

**Why it works:** a performer free-runs `f(x,y,t)` from the synced clock, so it does
*not* need continuous RX — only periodic beacons for clock-drift correction and
pattern/table updates. So turn the radio **off** most of the time and wake it briefly
to resync. This attacks the dominant RX term (modem-sleep can't, see power note).
**Conductor is exempt** — it must beacon every 250 ms (TX), and is typically
wall-powered; gate all of this on `role == performer`.

**Staged plan:**
- **Stage A — radio duty-cycle, CPU stays on. ✅ DONE + host-tested +
  hardware-verified + measured + pushed.** `include/powersave.h` + glue in
  `main.cpp`; `powersave on|off` toggles it live (NVS `ps`, default on); `[duty]`
  diag. Implementation notes preserved in §8.1 / the commit. The one thing *not*
  worth doing: widening `DUTY_OFF_US` (4 s → 8 s/60 s) — at 13% duty we already
  removed ~30 of the ~34 mA radio term, so a 60 s wake saves only ~4 mA more while
  multiplying pattern-update latency. Diminishing returns; leave it at 4 s.
- **Stage B — cut the CPU floor (now the biggest constant term). ✅ HARDWARE-VERIFIED
  (2026-07-03 bench, conductor + 2 performers, all on protocol v2): naps run
  ~2/s on GLOW (heartbeat-capped ~0.5 s each), measured slept time tracks wall
  clock (~87–93% of each radio-off span asleep) — `esp_timer` IS compensated
  across light sleep, the known risk is retired; sync stays `LOCKED` with
  `missed=0` across hundreds of naps; UART wake + serial grace + diag gating
  all behave as designed. One glue fix found on the bench: light sleep releases
  the UART0 TX pad and sprayed a junk byte per transition at the host —
  `gpio_hold_en(GPIO_NUM_1)` across the sleep eliminates it (verified 0 junk
  bytes post-fix). Power re-measure on the 12 V rig still owed. ⚠ Watch item:
  the conductor board (8C:94:DF:57:7F:14, the one that needed today's
  `erase_flash` for the FLASHING.md clock-scramble) once flooded serial with
  diag lines at full baud with `seq` racing (time-gated timers firing every
  loop) — cleared by reset, not reproducible after retrying the same command
  sequence; if it recurs, suspect this board's hardware first.** What landed: `include/napsched.h`
  (pure `napPlan()` — how long may the CPU light-sleep right now; 9 host tests)
  + `include/pattern_ids.h` (PatternId enum extracted from Arduino-bound
  patterns.h so `patternIsStatic` is host-reachable) + glue in `main.cpp`
  (replaces the `delay(16)` loop tail). Behavior: naps happen only on a
  performer with `powersave on` and **only while the radio is already off**
  (never into a listen window); a nap ends at the earliest of the next radio
  duty transition, the next ~30 fps frame (animated patterns only — GLOW/SOLID
  skip this and sleep clear through), the next heartbeat edge (keeps the GPIO2
  sync blink square), or a 1 s safety cap. Serial safety: any serial byte holds
  naps off for 30 s, a UART wakeup (typing at a sleeping node — hit Enter once,
  then type) does the same, and boot seeds the grace so a fresh flash has a
  provisioning window. Glue details: waits for the RMT LED transfer +
  `Serial.flush()` before sleeping (sleep truncates both), knobs in config.h
  (`NAP_*`, `SERIAL_NAP_GRACE_US`).
  **Bench checklist for the next hardware session:** (1) `[nap]` diag —
  `slept` should track wall time (measured via esp_timer delta; **slept≈0 with a
  climbing nap count means esp_timer is NOT compensated across light sleep**,
  the known Stage-B risk — fix would be adding slept RTC time back); (2) sync
  must stay `LOCKED`, `missed=0`, offset stable across naps; (3) heartbeat still
  square @ 1 Hz; (4) serial: confirm Enter-then-type works on a napping node;
  (5) re-measure the 12 V USB-disconnected draw on GLOW — expect the ~50 mA
  CPU floor to drop meaningfully; compare `powersave on` vs `off`.
  Original design notes (kept for context) — two flavors, gated on whether the
  current scene is animated:
  - **Animated patterns (pulse/sweep/drift):** CPU must re-render ~20–30 Hz, so the
    play is **light-sleep between rendered frames**. SK6812 latch their last color,
    so LEDs hold during the nap. Harder part: verify whether `esp_timer`/systimer
    advances across `esp_light_sleep_start()` — if not, add the slept RTC duration
    back or synced time drifts.
  - **Static scenes (`GLOW` and any constant `f`):** the LEDs hold with the MCU
    fully asleep, so the node can **deep/light-sleep for the whole inter-wake
    interval** — attacking the radio *and* CPU floor at once, down toward LED-only
    draw. This is the **"scheduled-wake" protocol idea** (below) and is the single
    biggest remaining win for calm shows.

**Scheduled-wake + deep-sleep protocol (design thread, not yet built):** because
every node shares the synced clock, instead of each performer waking on its own
~4.6 s timer, have them **all wake at a shared wall-clock boundary** (e.g. each
synced-time minute), the conductor **bursts the current pattern during that window**,
nodes catch it and sleep again. No "subscribe" handshake is needed — ESP-NOW is
broadcast and the shared clock *is* the coordination. The real prize isn't more
radio savings (Stage A already got those) but that a known next-wake time lets a
node **deep-sleep between windows on static scenes** (LEDs latched), and it makes
Lever 2's deep-sleep/rejoin coherent. Costs to design around: pattern/show updates
land up to one wake-interval late (fine for a programmed show, painful for live
tuning), and each window gets more critical (miss → longer free-run + delayed
update; mitigate with a longer burst + the conductor repeating it).

**Drift budget (still holds):** free-running tens of seconds on the last offset is
<~5 ms relative drift (crystals ~tens of ppm) — invisible on the slow patterns, so
minute-scale wake intervals are fine for *timekeeping*; the limiter is update
latency, not sync.

### Lever 2 (then): daytime deep-sleep — calendar life

**🛠 CODE-COMPLETE (2026-07-03), host-tested (57 native tests green), both device
envs build — NOT hardware-verified (needs the pilot phototransistors, arriving);
DEFAULT OFF (`dusk on|off`, NVS `dusk`) because GPIO34 floats until the sensor
is wired.** What landed: `include/dusk.h` (pure debounced day/night detector
with hysteresis + the deep-sleep gate; 9 host tests) + glue in `main.cpp` +
config knobs (`DUSK_*`; thresholds are placeholders to bench-calibrate).

**Design principle: FAIL AWAKE** — every ambiguous case resolves to staying
awake; the only possible failure mode is battery drain, never an unreachable
field. Four independent layers guarantee daytime testability:
1. **`wake on|off` (conductor, NVS-sticky)** sets `BEACON_FLAG_FIELD_AWAKE` in
   every beacon. A dusk-sleeping node wakes every **15 min** (`DUSK_RESAMPLE_US`)
   and listens for a beacon before it may re-sleep — a flagged beacon pins it
   awake (60 s TTL, continuously refreshed). Summon latency for the whole field:
   ≤ one resample interval. **Wire format grew a `flags` byte → PROTO_VERSION 2 —
   reflash every board together; v1/v2 nodes reject each other's packets.**
2. **Any power-cycle boots awake** (cold boot starts in "night", won't dusk-sleep
   for 10 min, 60 s light debounce on top). Per-lantern physical override via the
   battery toggle switch — no firmware bug can remove it.
3. **`dusk` is default OFF** — nothing sleeps until deliberately enabled after
   the pilot proves it (INA228 watching).
4. **Fail-awake invariants:** deep sleep is only entered via `duskEnterDeepSleep()`
   which arms the RTC wake timer atomically (`esp_deep_sleep`); implausible light
   readings (outside `[20, 3100] mV` — floating/broken sensor) read as *night*;
   serial traffic holds sleep off 5 min; all host-tested.

Mechanics: 1 Hz ADC sampling; flip day↔night only after 60 s continuously past
the far hysteresis threshold (clouds/headlights/flashlights reset the stretch);
timer wakes start in "day" via an RTC-memory flag and re-sleep in ~10 s if still
bright; dusk arrival flips to night and the show starts. `[dusk]` diag line +
light/vbat in `info` (VBAT divider read landed too — reported only, no cutoff
policy yet). Daytime cost at 15-min cadence ≈ ~1% duty → ~0.15 Wh/day, noise.
**Bench checklist:** calibrate `DUSK_DAY_MV`/`DUSK_NIGHT_MV` (+ polarity
`DUSK_DAY_ABOVE`) against the real divider; verify deep-sleep current (~10 µA
class); verify a full sleep→timer-wake→re-sleep cycle and `wake on` summoning;
verify a cold boot never sleeps for 10 min.

Original design notes: fixes the 24/7 ~5-day problem by sleeping through daylight. **Light sensor on
`PIN_LDR` = GPIO34 (ADC1 — ADC2 dies with the radio, already reserved in config.h).**
User leans toward a **photodiode/phototransistor** (faster than an LDR; an LDR in a
divider also works for a coarse threshold — pick by what's on hand). Below a light
threshold for a debounce → `esp_deep_sleep` (~10 µA), waking on an RTC timer to
re-sample (e.g. every ~30 min) or sleeping a fixed span until expected dusk. Add the
**battery ADC on `PIN_VBAT` = GPIO35** (divider) to report voltage + low-batt cutoff.

### Self code-review (2026-07-03) — fixes landed + known debt

A full-repo adversarial review (8 finder angles) ran after Stage B + Lever 2.
**Fixed + tested + committed:** (1) the stale-RTC-day trap — a dusk node
timer-waking after sunset re-slept every 15 min all night; `duskShouldSleep`
now refuses while live samples disagree with the day state (`d.cand != d.day`,
host-tested); (2) re-issued `powersave on` during a radio-off span stranded the
radio off permanently (now radioWake()s first; espnowStart also tolerates
double-init); (3) serial `pattern`/`bri`/`param` raced the recv callback's
`g_beacon` overwrite (now mutate+snapshot under `g_sync_mux`, NVS-save from the
snapshot); (4) role switches left a stale duty schedule (role command now
re-inits the duty machine); (5) `missed_windows` overcounted (beacon credit now
runs before `dutyStep` in loop()); (6) static patterns no longer re-render at
60 Hz (pattern-change detection + 1 Hz safety refresh); (7) diag prints only
within 5 min of serial activity — **hit Enter on a monitor to revive a quiet
node's diag** (headless nodes no longer burn ~13 ms/s of UART drain).

**Debt PAID 2026-07-04 (the review-debt session):**
- ✅ **Field build envs** `field-devkitc` / `field-firebeetle` (`extends` the
  bench envs + `-D HEARTBEAT_LED=0`; see "Build envs" near the top and
  FLASHING.md's env table).
- ✅ **Table rebroadcast stretched** 5 s → 60 s steady-state backstop
  (`TABLE_INTERVAL_US`); targeted delivery is a **single-row MSG_TABLE reply
  (23 B, broadcast)** to any REGISTER from a node that is *new to the roster
  or unprovisioned (id 0)* — sent while that node's radio is provably up (it
  just transmitted), retried for free by its next REGISTER, zero table traffic
  in steady state. `assign` still broadcasts the full table immediately.
- ✅ **Host-unreachable logic extracted + tested** (83 total):
  `parseMac`/`macStr` → `macaddr.h` (parseMac rejects trailing garbage — a
  pasted EUI-64 must not silently truncate to its prefix MAC); `broadcastTable`
  chunk math, MSG_TABLE length validation, own-row scan, and the row-reply
  decision/builder → `table_wire.h`; the Lever-2 boot classification →
  `bootplan.h` (the seed now pre-expires `max(dusk, nap)` serial grace, so the
  old unlabeled `DUSK_SERIAL_GRACE_US > SERIAL_NAP_GRACE_US` invariant is
  *gone*, not just labeled); the SOLID boot-guard → `patternBootSafe` in
  `pattern_ids.h`.

**Same-day adversarial review of the paydown (8 finder angles + 1-vote
verify, 10 findings, 7 CONFIRMED) — all fixed the same day:** the first
cadence design (burst on new-MAC, inferred from a roster count change) had
three confirmed delivery holes — a reflashed node (known MAC, wiped NVS) never
got a burst and waited ~5-8 min for its position; a full roster (never pruned,
64 slots vs 60 nodes + swaps) silently suppressed the burst; and a burst
deferred by the 2 s rate limit could fire into the requester's radio-off gap
with no retry. The row-reply design above replaced it (simpler AND covers all
three: reply keyed on pre-upsert known-ness + id, not on count inference; no
hold-off to miss). Also fixed from the review: `parseMac` trailing-garbage
acceptance (empirically confirmed — `forget <EUI-64 paste>` would have
operated on the wrong lantern), the `role` round-trip resuming a stale table
schedule (now file-scope `g_next_table_us`, zeroed on role change), FLASHING.md
missing the field-* envs, and the field envs copy-pasting instead of
`extends`-ing the bench envs.

**Known debt (deliberate, not yet done):**
- Dead wire artifacts: `palette_id` unused, `MSG_ROSTER`/`MSG_ACK` unsent,
  `TableMsg.chunk/chunks` written but never read, roster `fw` can never differ
  from PROTO_VERSION (the version gate rejects stragglers before dispatch — a
  stale-firmware node just vanishes from the roster instead of being flagged).
  Removing the wire fields would change layout → PROTO_VERSION bump → reflash
  every board together; fold it into the next deliberate protocol rev instead
  of doing it standalone.

### After Milestone 3
Milestone 4 — battery enclosure + final go/no-go on the **FireBeetle** (lower draw
than the DevKit). Then the non-power tracks still open: auto-calibration (§6),
Pi web UI / control plane (§5.2, needs the deferred machine serial), show program
(§4.1). Milestone 5 — OTA + enclosure.

## Project memory (loaded automatically in this dir)

- `at-firmware-erase-flash` — erase new boards first; serial-port shuffle.
- `power-budget-go-no-go` — ET900 measurement plan, ~11 Wh/night target.
- `design-discussion-style` — in design mode, recommend in prose (no question widgets).
