# Control plane — features & phasing

Status: **prototype in progress (2026-07-05).** Companion to
[`ARCHITECTURE.md`](ARCHITECTURE.md) §5.2 (control plane) and §7 (wire
protocol). This doc enumerates *what* the control plane does, what the
current HTTP/API contract is, and how the serial adapter talks to the
conductor without changing the UI/API surface.

## Deployment shape

```
phone / laptop / agent --HTTP+WS--> API server (FastAPI) --USB serial--> conductor --ESP-NOW--> field
                                    (laptop in dev,
                                     Pi + its own AP in the field)
```

The Pi is a **deployment target, not a dependency** — dev runs the identical
server on a laptop with the bench conductor on USB. Pi-specific work is pure
config: AP hotspot, mDNS (`baskets.local`), systemd unit, serial device
naming.

## Architecture principles

1. **API-first.** The web UI is a pure client of the same HTTP + WebSocket
   API, with the OpenAPI schema served at `/docs` (FastAPI gives this for
   free). **Anything the UI can do, an agent can do over HTTP** — no
   UI-only paths, no server-rendered actions. This makes the field
   scriptable/agent-drivable from day one.
2. **The conductor stays authoritative.** Table, pattern, and (later) show
   program live in conductor NVS. The server holds only derived/history
   data (power logs, event log). Unplug the server and the field keeps
   running — same rule as today.
3. **Structured machine serial, human CLI preserved.** Newline-delimited
   JSON with request ids and explicit ok/error acks (`{"id":1,"cmd":...}` →
   `{"id":1,"ok":...}`). Machine lines are distinguishable by the leading
   `{`; the existing human CLI and diag lines coexist on the same port.
   Protocol logic lives in a dependency-free, host-tested header per
   project convention.
4. **Honest write semantics.** Every mutation surfaces its serial-protocol
   ack or failure. Conductor unplugged → the UI/API says so loudly, never
   pretends.
5. **Fully offline.** All assets local; nothing assumes internet. The real
   client is a phone on the Pi's AP on the playa.

## Current HTTP API contract

The implemented prototype is FastAPI + a pure static client. The UI uses only
these endpoints, so agents can drive the same workflows without a browser.

### State

- `GET /api/state` → full control-plane snapshot.
- `GET /api/lanterns` → `state.lanterns` only.
- `WS /ws` → pushes `{"type":"state","state":...}` snapshots and `error`
  events.

Snapshot shape:

```json
{
  "conductor": {
    "connected": true,
    "uptime_s": 12.3,
    "seq": 184221,
    "wake": true,
    "sync": "locked",
    "firmware": {
      "version": "0.2.0",
      "proto": 5,
      "build_id": 3225866068,
      "build_label": "c046bf54",
      "dirty": false
    }
  },
  "summary": {
    "alive": 8,
    "total": 9,
    "attention": 2,
    "table_rows": 9,
    "firmware": {
      "consistent": true,
      "matching": 8,
      "seen": 8,
      "expected": 9,
      "version": "0.2.0",
      "build_label": "c046bf54",
      "dirty": false
    }
  },
  "pattern": {
    "pattern": "Glow",
    "brightness": 48,
    "params": {"hue": 40, "saturation": 100}
  },
  "power": {
    "light_sleep_check_s": 4,
    "deep_sleep_check_min": 15,
    "led_on_start_min": 1080,
    "led_on_end_min": 360,
    "current_min": 720,
    "schedule_enabled": false,
    "force_awake": true,
    "leds_on": true
  },
  "lanterns": [
    {
      "mac": "8C:94:DF:8F:71:50",
      "label": "#0",
      "status": "alive",
      "last_seen_s": 4,
      "last_seen_label": "4s ago",
      "x": 0.54,
      "y": 0.47,
      "position": "Set",
      "attention": "None",
      "firmware": {"version": "0.2.0", "proto": 5, "build_id": 3225866068, "build_label": "c046bf54", "dirty": false},
      "power": {"wh": 0.38, "avg_w": 0.71, "last_report_label": "4s ago"},
      "updated_at": 1720123456.0
    }
  ],
  "events": [{"ts": 1720123456.0, "message": "mock conductor started"}]
}
```

Lantern status values currently used by the prototype:

- `alive` — awake and usable.
- `missing` — expected/positioned but stale or not seen.
- `retired` — old MAC after a replacement; keep for audit, never offer as
  a replacement spare.

Lantern attention can also be `Firmware mismatch` when a registered node has
the same wire protocol but a different release version, build id, or dirty flag
than the conductor.
`summary.firmware` is the OTA safety invariant: manual field-wide OTA should not
start unless every reachable node is on the expected build, or the UI explicitly
keeps the operator in a recovery flow.

`summary.alive / summary.total` means healthy placed lanterns over placed
lanterns in the field table. Unpositioned live lanterns are available substrate
or spares: they have `x: null`, `y: null`, and `position: "Missing"`, appear in
the tray and Node List, and do not count in the field denominator until placed.
The map renders only positioned lanterns.

### Mutations

- `POST /api/lanterns/{mac}/identify`
- `POST /api/lanterns/{mac}/assign` with `{"x":0.25,"y":0.75}`
- `POST /api/lanterns/{mac}/forget`
- `POST /api/lanterns/replace` with `{"old_mac":"...","new_mac":"..."}`
- `POST /api/show/pattern` with
  `{"pattern":"Sweep","brightness":64,"params":{"period":8000,"spatial":300}}`
- `POST /api/show/blackout`

Every mutation returns an ack:

```json
{"ok": true, "message": "assigned #2"}
```

Adapter errors such as serial timeout surface as HTTP `503`. Command-level
errors such as unknown MAC or invalid replacement return `404` with the
adapter's `error` text. Rejected pattern changes return `400`; the UI should
only treat a pattern change as saved after a successful ack.

## Adapter contract

FastAPI depends on `control.adapters.ConductorAdapter`, not on the mock
implementation directly. The app defaults to `MockConductor`; set
`CONTROL_CONDUCTOR=serial` and `CONTROL_SERIAL_PORT=/dev/cu.usbserial-XXXX`
to use the pyserial-backed conductor. The required methods are:

```python
snapshot() -> dict
lanterns() -> list[dict]
tick() -> None
identify(mac) -> ack
assign(mac, x, y) -> ack
forget(mac) -> ack
replace(old_mac, new_mac) -> ack
update_pattern(pattern, brightness, params) -> ack
blackout() -> ack
```

`MockConductor` implements this contract for UI development.
`JsonLineSerialConductor` implements the same contract over newline-delimited
JSON and is tested with a fake transport. The serial transport deasserts
DTR/RTS by default after opening so a running conductor is not intentionally
reset just because the web server connected. FastAPI serial calls run through
a serialized `asyncio.to_thread` helper so a blocking serial read does not pin
the event loop.

### Machine serial protocol

Requests are one compact JSON object per line:

```json
{"id":1,"cmd":"state"}
{"id":2,"cmd":"assign","mac":"8C:94:DF:57:7F:14","x":0.25,"y":0.75}
{"id":3,"cmd":"forget","mac":"8C:94:DF:57:7F:14"}
{"id":4,"cmd":"replace","old_mac":"A0:B7:65:11:44:91","new_mac":"8C:94:DF:57:7F:14"}
{"id":5,"cmd":"pattern","pattern":"Sweep","brightness":64,"params":{"period":8000,"spatial":300}}
{"id":6,"cmd":"blackout"}
```

Responses echo the request id:

```json
{"id":1,"ok":true,"state":{...}}
{"id":2,"ok":true,"message":"assigned #2"}
{"id":3,"ok":false,"error":"unknown lantern"}
```

The adapter ignores human CLI/diagnostic lines and malformed JSON until it
sees a valid JSON response with the matching `id`. If no matching response
arrives before the timeout, the adapter raises `SerialProtocolError`, which
the API exposes as HTTP `503`.

Firmware support lives beside the human CLI: lines beginning with `{` enter
the machine parser in `include/serial_json.h`, while `info`, `table`, `assign`,
and the other text commands keep working. The firmware `state` response derives
lanterns from the conductor's persistent layout table plus the live roster:
positioned table rows show on the map, roster-only MACs show as "Needs
position", and table rows not currently registered show as "Not seen".

## Features

### 1. Field dashboard (read-only)

- Roster view: MAC, friendly id, last-seen age; stale (silent > ~30 s) and
  expected-but-never-seen nodes flagged.
- Count check at a glance: "58 of 60 healthy".
- Conductor status: beacon seq, current pattern, table row count, `wake`
  flag, uptime.
- Cross-checks: in roster but not table (unpositioned); in table but not
  roster (dead lantern?).

### 2. Layout map (the reason a web UI exists)

- 2-D field map of table `(x,y)` positions with roster liveness overlaid.
- Drag to reposition; click to add/edit; `forget` to remove.
- Replace-node flow (§5.1): pick dead node + spare → one action does
  `assign` + `forget`.
- **Identify:** click a dot → that physical lantern blinks so it can be
  found in a dark field of 60. ⚠ Needs a small new ESP-NOW unicast message
  (no PROTO_VERSION bump); the one Phase-1 firmware addition beyond the
  machine serial protocol.
- Table import/export as JSON — backup before edits; the socket the
  calibration CV output (§6) plugs into later.

### 3. Live show control

- Pattern picker (PULSE / PALETTE_DRIFT / SWEEP / GLOW; SOLID behind a
  bench-only flag).
- Brightness slider + per-pattern param controls with human labels:
  Pulse/Glow expose hue, Sweep exposes period + wavelength, and Palette Drift
  exposes period + spatial spread. The Change Pattern button is disabled until
  the visible draft differs from the live conductor state.
- Pattern preview: the browser renders `f(x,y,t)` live on the map *before*
  broadcasting (JS port of the pure `pattern_math.h`). Cheap because the
  math is pure and host-tested; turns knob-tuning into instant feedback.
  Also the backbone of the agent-assisted pattern-authoring workflow (see
  the "Pattern authoring" section), including its headless
  `GET /preview` render endpoint.
- Blackout button (bri 0).

### 4. Power & energy

- Live power panel: the conductor's `[power]` lines parsed into structured
  V / I / avg-W / Wh-tonight per instrumented node.
- Operations power schedule: set radio/light-sleep check interval, deep-sleep
  check interval, LED-on window, and field-wide force-awake override. These are
  runtime-broadcast in beacons, so changing them does not require another
  firmware update.
- Nightly history persisted server-side (sqlite or CSV) — trend across the
  event, not just tonight.
- `power reset` button for the dusk ritual.
- Later (protocol v3 bundle): VBAT in REGISTER → fleet-wide battery health
  on the dashboard.

### 5. Show program (firmware doesn't exist yet — see §4.1)

- Schedule editor: what plays when; brightness ramps ("calmer/dimmer after
  1 am").
- Forces the open §4.1 decisions: time base (dusk-relative vs wall-clock)
  and crossfades.
- Simulate the schedule against the map preview.

### 6. Calibration wizard (§6)

- Enter-calibration mode, capture orchestration, CV upload/review,
  empty-slot report, push table. Integrates via the map's import/export
  socket; CV itself runs on the Pi/laptop.

### 7. Ops & escape hatches

- Field firmware panel: conductor release version, GitHub-linked commit hash,
  dirty flag, and consistency count (`N / total on this build`). Mixed firmware
  is red and also appears in the Node List as `Firmware mismatch`.
- Power schedule panel: authoritative place for LED on/off hours and
  force-awake debugging. Schedule-driven sleep is the primary field behavior;
  photodiode dusk sensing is optional/fallback, not required for the main path.
- Raw serial console passthrough to the conductor CLI (the playa will
  produce a situation the UI didn't anticipate).
- Event log: registrations, table edits, errors — timestamped, server-side.
- Conductor config backup/restore (table + pattern + program as one JSON
  blob).
- Server self-health: serial connect/reconnect state, auto-reconnect on
  USB flap.

### 8. OTA (Milestone 5)

- Manual maintenance-mode OTA only. No autonomous/opportunistic updates.
- Field-wide firmware updates only. No selected-node OTA; mixed firmware is a
  recovery state, not a supported operating mode.
- Built foundation: REGISTER reports release version + protocol + build id +
  dirty flag, the control-plane state exposes per-node and conductor firmware
  versions, and Operations shows version consistency with linked commits.
- Not built yet: firmware upload/transfer, OTA window command, readiness states,
  timeout/recovery UI.

### Cross-cutting

- **Phone-first, dark theme** — night use with dark-adapted eyes; big
  touch targets; possibly a red-shifted night mode. (Visual design session
  pending — see Open items.)
- Fully offline (principle 5).
- Honest write semantics (principle 4).

## Phasing

| Phase | Scope | Firmware work |
|---|---|---|
| **1** | Machine serial protocol; API server + OpenAPI; dashboard, layout map, live show control, power panel, ops console | Serial protocol header + glue; **identify-node** message |
| **2** | Show program: conductor schedule in NVS + editor UI | Show-program feature (§4.1 decisions land here) |
| **3** | Calibration wizard | Calibration mode messages (§6) |
| **M5** | OTA UI | OTA via on-demand AP |

Everything in Phase 1 is testable end-to-end on the 3 bench DevKitC boards —
no Pi, no new parts.

## Wire-protocol implications (enumerated up front, none urgent)

- **Phase 1:** `MSG_IDENTIFY` unicast (new type only — no PROTO_VERSION
  bump, same precedent as `MSG_POWER`).
- **Protocol v3 bundle (later, one deliberate bump):** VBAT + sync-health
  fields in REGISTER, riding the same bump that clears the known dead-field
  debt (`palette_id`, `MSG_ROSTER`/`MSG_ACK`, `TableMsg.chunk/chunks`).
  Nothing else in this doc touches the radio.

## Pattern authoring — an agent-assisted workflow, not a UI feature

Pattern authoring should optimize for this operator goal:

> Design patterns in the Pi/brain, distribute them over the control plane, and keep
> performer nodes as simple and low-power as possible.

The performer should not be the creative computer. The Pi/server can spend CPU on
pattern design, layout-aware compilation, previews, and agent-assisted iteration.
The performer should spend as little CPU as practical: maintain synced time, replay
compact local instructions, interpolate colors, and drive the LEDs.

There are three pattern-delivery tiers:

1. **Built-in patterns (current firmware):** conductor broadcasts `pattern_id` +
   params; performers evaluate known C++ patterns locally. Tiny, live-tunable,
   resilient, and still the right path for simple primitives like GLOW/SWEEP.
2. **Precomputed per-node clips (target creative layer):** the Pi/brain knows
   `MAC -> (x,y)`, evaluates a desired `f(x,y,t)` offline, then sends each node a
   compact replayable artifact: start epoch, loop duration, keyframes/curve samples,
   interpolation mode, brightness cap, and hash/version. A clip may target the
   lantern as one virtual light **or** multiple emitters inside the lantern.
   Performers do not need to understand the source pattern math; they just replay
   their own clip against the synced clock. This preserves no-live-streaming
   resilience while minimizing node "thinking" and CPU time.

   Do not bake "16-LED ring" into the control-plane model. The current hardware is a
   16-pixel ring, but future nodes may splay those pixels into a different 2-D or
   3-D shape. Model each physical node as a container with an optional **emitter
   layout**: emitter count + local coordinates/orientation. Phase 1 can treat every
   node as one virtual emitter for simplicity, as long as the clip/protocol can later
   expand from `node -> 1 color` to `node -> N emitter colors` without redesigning
   the authoring model.
3. **Firmware OTA:** used only when the replay engine, protocol, or built-in
   primitives need to change. A new artistic look should not require reflashing if
   it can compile down to a replayable clip.

Important boundary: avoid live frame streaming. Once a clip is loaded and acked,
the field should keep running if the Pi disappears, and performers should free-run
through dropped beacons just like they do today.

Open engineering questions for the precomputed-clip layer:

- clip representation: color keyframes, palette-index keyframes, curves, or a mix
- spatial granularity: one color per node, per-emitter frames, or compressed local
  primitives that preserve future `1 -> N emitters` expansion without bloating clip
  size
- emitter layout schema: current default is one virtual emitter per node; later,
  support local emitter coordinates for the 16-pixel ring or a future 2-D/3-D LED
  arrangement
- sample rate and loop length that fit NVS/flash and radio transfer time
- chunking/acks/resume for distributing 60 unique clips
- whether clips live on performers, conductor, or both
- preview conformance: the browser preview and performer clip player need shared
  golden vectors

Until that layer exists, new patterns are still compiled firmware
(`pattern_math.h` stays the single home of `f(x,y,t)`), but the control plane is
what makes authoring fast and lets an agent do most of it:

1. **Author:** write the new `f(x,y,t)` in `pattern_math.h` (pure,
   dependency-free — an agent can iterate here freely) + host tests.
2. **Visualize before any flash:** the browser preview (§3) renders the
   proposed pattern on the *real* field layout. An agent drives this via
   browser control, or — cheaper — via a **headless preview endpoint**
   (`GET /preview?pattern=…&params=…&t=…` → PNG frame or GIF loop of the
   simulated field), so an agent can "see" a proposed pattern with a plain
   HTTP call. Worth building the endpoint; it makes the whole loop
   API-only.
3. **Bench:** flash the 3 bench boards, tune params live through the show
   controls.
4. **Fleet:** pre-event reflash, or OTA once Milestone 5 lands.

**Keeping C++ and the JS preview in sync:** the preview is a second
implementation of the pattern math, which will drift. Plan: a shared
**golden-vector conformance file** — the host tests emit expected RGBW for
a grid of `(pattern, params, x, y, t)` samples; the JS preview's test suite
consumes the same file. (If drift still bites, `pattern_math.h` is
dependency-free and could compile to WASM for a single-source preview —
deferred until needed.)

**Reality check:** until OTA, a new pattern means USB-reflashing every
deployed board — so the pattern *vocabulary* wants to be locked pre-event,
while the **pattern space (pattern × brightness × params) is the live
authoring surface** on the playa. That's the division of labor: agents +
preview expand the vocabulary before the event; the UI tunes patterns during
it.

## Deliberately out of scope

- Auth beyond the Pi AP's WPA2 password.
- Multi-conductor support.
- On-device pattern interpretation (a bytecode/expression VM for patterns
  on the ESP32) — patterns stay compiled C++; the authoring workflow above
  covers the need without the complexity.

## Open items

- Visual design session (aesthetic, layout, dark/night theming) — next.
- Serial protocol schema — designed against this feature list, after the
  design session.
- Stack confirmation: Python + FastAPI + pyserial (recommended; pyserial
  already proven in this repo), UI framework TBD in the design session.
