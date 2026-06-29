# Do Baskets Dream — System Architecture & Design

Companion to [`do_baskets_firmware_brief.md`](do_baskets_firmware_brief.md). The
brief is the original spec; this doc records the architectural decisions made
while building it. Status tags: **[done]** shipped, **[wip]** in progress,
**[planned]** designed, not yet built.

---

## 1. Design philosophy: parametric field, not addressed pixels

The conductor broadcasts a compact **recipe** (which pattern + a few knobs + the
clock); every node computes its own color locally from **`f(x, y, t)`** using its
stored position. We deliberately do **not** push per-node frames.

Why this is the right core:
- **Resilience.** A node that misses a beacon keeps evaluating `f(x,y,t)` against
  the synced clock — it free-runs, never blanks. This is the brief's hard
  requirement, and it only works because content is a function, not pushed data.
- **Scale.** One small broadcast drives 5 nodes or 500. Bandwidth is constant.
- **Power.** Minimal radio traffic fits the modem-sleep / battery budget.

Trade-off: patterns must be expressible as smooth functions of position, time, and
a handful of parameters. That covers pulses, waves, ripples, and drifts — the
installation's whole vocabulary. Arbitrary imagery/text is explicitly out of
scope (it would break resilience and the power budget).

## 2. Roles **[done]**

Every board runs **one identical firmware image**. Role is a runtime value in NVS
(default **performer**), set once over serial (`role conductor|performer`). One
conductor per field; it is typically a **ring-less, headless timekeeper** so all
visible rings are on performers.

- Conductor: broadcasts the clock beacon + pattern recipe; renders against its own
  clock (if it has a ring).
- Performer: locks an offset to the conductor's clock; renders against synced time;
  free-runs on missed beacons.

Rationale for runtime role: flashing different binaries per role was the single
biggest source of operational error during bring-up (wrong role on wrong board,
two conductors at once). One image + NVS role removes that entirely.

## 3. Identity vs. position — keep them separate

- **Identity = the ESP32 MAC address.** **[done]** Read at boot
  (`esp_read_mac`/`ESP_MAC_WIFI_STA`), shown in `info`, and reported in REGISTER so
  the conductor's roster is MAC-keyed. Globally unique, burned in, zero
  provisioning, no collisions. The friendly `id` persists as a human label, but the
  MAC is authoritative. (Layout table keying on MAC is Half 2.)
- **Position = `(x, y)`.** **[done, manual]** Per-deployment, changes whenever the
  field is re-laid. **Relative geometry only — no GPS / metric coords needed**;
  patterns (waves, ripples) care about relative positions, so normalized image
  coordinates from calibration are sufficient. Optional reference markers can set
  orientation/scale if a pattern must align to a real-world feature.

## 4. Pattern model **[done for 1-D; 2-D planned]**

Conductor broadcasts the recipe in the beacon: `pattern_id`, `brightness`,
`palette_id`, `params[4]`, plus the clock (`epoch_us`, `seq`).

- `params` are pattern-specific knobs for live tuning (e.g. sweep period in ms,
  wavelength ×100). Persisted in NVS on the conductor so a power-cycle keeps the
  tuned look. **[done]** (every node persists/restores the recipe; keys
  `pat`/`bri`/`p0`..`p3` in the `"node"` namespace.)
- Patterns are `f(x, y, t)`:
  - `PULSE` — uniform breathing, all nodes in unison. **[done]**
  - `PALETTE_DRIFT` — smooth rainbow hue cycle; `params[0]` = cycle period in ms,
    `params[1]` = spatial hue offset (×100 cycles per x unit) so the rainbow can
    travel across the field or run in unison (0). **[done]**
  - `SWEEP` — traveling wave across `x`. **[done]** (1-D today.)
  - **[planned]** true 2-D: plane wave at an arbitrary **angle**, **radial ripple**
    from a center point. `params` encode direction/center.

Pure pattern math lives in `include/pattern_math.h` (host-unit-tested); the
LED-library binding is in `include/patterns.h`.

### 4.1 Show program (pattern scheduling) **[planned]**

Beyond a single live recipe, the conductor holds a **show program** in NVS — a
schedule of *what plays when* (e.g. pattern A for a while, then B; calmer/dimmer
late; brightness ramps). The conductor walks the program against its clock and
**broadcasts the current recipe each beacon**; nodes render whatever arrives and
stay dumb. So scheduling lives entirely on the conductor and needs no per-node
logic. Open considerations: smooth **transitions/crossfades** between patterns
(would need a blend factor in the recipe), and the schedule's **time base**
(uptime vs. dusk-relative once the LDR lands in Milestone 3, vs. a set wall-clock).

## 5. Layout table — conductor-authoritative `MAC → (x,y)` **[done]**

The conductor holds the authoritative field map and broadcasts it; each node finds
its own MAC, adopts its `(x,y)`, and **caches it in NVS**. Edit one table to
re-arrange the whole field.

- **The conductor is authoritative and stores the table in its own NVS.** The
  field runs with **no laptop present** — the conductor is the coordination point
  for the table just as it is for the clock and the pattern recipe.
- Resilient: a node needs to hear the table only once, then survives on the cache.
- Cheap: 14 B/node on the wire (`TableRow`) → ~840 B for 60 nodes → ~4 `MSG_TABLE`
  packets (17 rows each), sent every `TABLE_INTERVAL_US` (positions are static).

Implementation: the table logic is the dependency-free, host-tested
`include/table.h` (`tableSet`/`tableLookup`/`tableRemove`); `main.cpp` owns the NVS
blob, the chunked broadcast, and the node-side adoption. The conductor edits it
over serial — `assign <mac> <x> <y>`, `table`, `forget <mac>` — and pushes the
change immediately. A node stashes its row in the recv callback and applies +
`identitySave()`s it from `loop()` (no flash write in the callback). Verified on
hardware: a node adopts a position set only on the conductor, with no serial to
that node, and keeps it across a reboot. Manual `pos x y` over serial remains as a
fallback/override for tests and stragglers.

### 5.1 Node replacement **[done via assign + forget]**

A dead lamp's spare has a new MAC, so replacement is a **table edit**, not a
re-calibration — because positions are MAC-keyed and the table is
conductor-authoritative. Physically drop the spare into the dead one's spot, then
transfer the position from the old MAC to the new one:

1. Spare boots as a performer (default), registers, and shows in the `roster`.
2. Operator runs `assign <newMAC> <x> <y>` then `forget <oldMAC>` on the conductor
   (a single `replace <oldMAC> <newMAC>` convenience command is a possible later
   nicety, but the two existing commands already cover it).
3. Conductor rebroadcasts; the spare caches its `(x,y)` to NVS and joins the field.

No drone, no re-fly — a single swap is one command. Getting the new MAC: read it
from the spare's serial `info`, or label spares with their MAC, or let the
conductor surface "new unknown MAC seen." Only worth a full re-calibration if many
nodes move at once.

### 5.2 Control plane — operator/admin interface **[planned]**

The conductor stores the authoritative **routing table** (§5) *and* **show
program** (§4.1) in NVS and runs the field with **no laptop present**. The laptop
is a **transient admin tool**, plugged into the conductor over USB serial only
when you want to *change something*:

- **Routing table edits:** reposition nodes (new `(x,y)`), or replace a node
  (§5.1) — i.e. change *where* the light is.
- **Show program edits:** pattern selection, appearance (brightness, sweep
  speed/wavelength, palette), and schedule (what runs when) — i.e. change *what*
  the light does.
- It is also the host that runs the **calibration CV** (§6).

The conductor persists every pushed change to NVS, then executes/broadcasts it —
so the laptop can walk away and the field keeps running the new config. Likely
form: a **local web UI** (laptop server + browser) over a structured serial
command/response protocol (§7) — chosen because the table/map editor, live pattern
controls, and calibration wizard are far better visually than a CLI. The protocol
must support **bulk table transfer** (60 rows won't fit a typed line) and clean
acks/errors so a program can drive the conductor reliably.

**Not a runtime dependency:** unplug the admin host and the conductor + field
continue on their stored table and program.

**Deployment (no router/internet):** the admin host is a **Raspberry Pi** cabled
to the conductor over USB. The Pi runs as its **own Wi-Fi access point** (hotspot
via NetworkManager / RaspAP), so a phone joins the Pi's SSID directly and browses
the UI (`http://baskets.local` or the AP IP) — no router, no internet, works on the
playa. Set a WPA2 password. The Pi can also run the calibration CV (§6) on-site, so
no separate laptop is needed. The Pi is a permanent convenience but **stays
non-essential to runtime** — the authoritative table + show program live in the
conductor's NVS, so the field survives the Pi being removed or failing.

```
phone --WiFi--> Pi (AP + web UI + CV + serial bridge) --USB--> conductor --ESP-NOW--> field
```

## 6. Auto-calibration — drone + computer vision **[planned]**

Goal: build the `MAC → (x,y)` table by **survey**, not by hand (manual surveying of
60 lamps in a field is slow and inaccurate). Technique: temporal LED mapping — the
lamps blink in a known schedule, a drone films from above, and CV recovers each
lamp's position and associates it with a MAC.

The synced clock makes the capture **open-loop** — no live RF during the fly-over.

### Procedure
1. **Register (one-time, live).** Conductor broadcasts "enter calibration"; each
   node phones home once with its MAC. Conductor builds the roster (and thus the
   expected count).
2. **Freeze + distribute roster (live, must be reliable).** Conductor sorts MACs
   ascending, broadcasts the finalized roster + a calibration **start epoch**;
   nodes ACK. Each node computes its **rank** in the sorted list → its blink slot.
3. **Capture (open-loop, no live RF).** At the start epoch all lamps **flash
   together once** (a timeline anchor for the video), then each rank blinks in its
   **absolute synced-time slot** (`rank × slot_width`). Drone records a stable
   top-down clip. Sequential, one lamp lit at a time, with off-gaps between slots.
4. **Process (laptop).** CV detects each blink, converts its timestamp → slot →
   rank → MAC (via the roster), and records the blob's normalized `(x,y)`. Reports
   any **empty slots** = MACs that failed to map.
5. **Distribute (live).** Load `MAC → (x,y)` into the conductor; broadcast; nodes
   cache to NVS (§5).

### Why absolute time slots (not just activation order)
Pure ordering shifts the entire tail of the table if one lamp is dead/occluded.
Binding each rank to an absolute synced-time window + the start-flash anchor means
a missing lamp leaves an **empty slot** instead of mis-mapping everyone. The
register step gives the expected roster, so empty slots pinpoint exactly which MACs
need a manual `pos` fallback. (Optional periodic all-flash re-anchors long runs.)

### Notes & decisions
- **Blink encoding:** start **sequential** (bulletproof CV: find the one lit blob).
  Temporal binary coding (all blink, ~6 frames) is a future speed optimization that
  trades CV robustness for time.
- **Coordinates:** CV output is normalized pixel `(x,y)` — sufficient for relative
  patterns. Add reference markers only if real-world orientation matters.
- **Sync during capture:** leaving the (light-less) conductor beacon running keeps
  clocks tight; fully silent free-run is also fine (drift over ~60 s is sub-ms).
- **Off-board:** the laptop runs CV and feeds the table to the conductor over USB;
  the ESP32 never runs CV. Prototype the CV on a phone photo of 3 lamps before
  investing in the drone workflow.
- **Idempotent:** re-fly and re-calibrate at will.

## 7. Wire protocol **[partly done]**

- **[done]** Common header `MsgHeader {uint32 magic; uint8 version; uint8 type;}`
  on every packet; receiver validates magic+version then dispatches on `type`.
  Types: `MSG_BEACON` (hot path), `MSG_REGISTER`, `MSG_TABLE` (live);
  `MSG_ROSTER`/`MSG_ACK` reserved. `PROTO_VERSION` is rejected on mismatch.
- **[done]** `MSG_BEACON` (clock + recipe) broadcast on a fixed channel to
  `FF:FF:FF:FF:FF:FF`, `WIFI_STA`. The hot path (sync.h) reads `epoch_us`+`seq`.
- **[done]** Bidirectional ESP-NOW: a performer learns the conductor's MAC from the
  recv-info, adds it as a peer, and unicasts `MSG_REGISTER {mac, id, fw}` every 10 s;
  the conductor builds a MAC-keyed roster (`roster` serial command).
- **[done]** `MSG_TABLE`: the conductor broadcasts the layout table in chunks
  (`TableRow` ×17/packet); nodes adopt their own row. `chunk`/`chunks` fields let a
  receiver tell how much it has seen.
- **[planned]** `MSG_ACK` + richer machine Pi↔conductor serial (lands with the Pi
  UI).
- Time base: 64-bit `esp_timer` microseconds throughout (no 32-bit `millis` wrap).

## 8. Resilience model

- Missed beacon → free-run on last offset; re-lock on next. **[done]**
- Cold boot → read role/identity/position from NVS + MAC from efuse, lock within
  ~1–2 s, resume. **[done — role/pos/MAC; table-assigned position is cached to the
  same NVS pos keys, so it survives a reboot without re-hearing the table]**
- Calibration capture is open-loop (no live RF dependency during the fly-over).
- NVS caches (role, position, pattern config) survive power cycles / battery swaps.

## 9. Milestone mapping

| Milestone | Status |
|---|---|
| 1 — sync proof (conductor + performers) | ✅ done, hardware-verified |
| 2 — NVS identity + position-aware sweep | ✅ done, hardware-verified |
| Refactor — symmetric runtime role + NVS pattern persistence + rainbow drift pattern | ✅ done, hardware-verified |
| Protocol foundation, Half 1 — typed header, MAC identity, bidirectional ESP-NOW, registration + roster | ✅ done, hardware-verified |
| Protocol foundation, Half 2 — MAC→(x,y) layout table broadcast + NVS cache (`assign`/`table`/`forget`) | ✅ done, hardware-verified |
| Control plane — structured machine Pi↔conductor serial (bulk table/show-program) | 📐 planned (with the Pi UI) |
| Auto-calibration — register / roster / blink + laptop CV | 📐 planned |
| 3 — power management (modem-sleep, dusk deep-sleep, LDR/battery ADC) | 📐 planned |
| 4 — battery power + ET900 draw measurement (go/no-go) | 📐 planned |
| 5 — OTA + enclosure | 📐 planned |

## 10. Resolved & open decisions

Resolved:
- **Master table & show program: conductor-authoritative, stored in conductor
  NVS.** Field runs laptop-free; the laptop is a transient editor only (§5, §5.2).

Open:
- 2-D pattern parameter encoding (how to pack angle/center into `params[4]`).
- Pattern transitions/crossfades, and the show-program time base (uptime vs.
  dusk-relative vs. wall-clock) (§4.1).
- Admin UI form: local web UI (current lean) vs. CLI-first.
- Temporal-coded calibration as a later speed upgrade (§6).
