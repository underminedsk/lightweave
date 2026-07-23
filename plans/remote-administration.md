# Remote administration

> **Status:** In review
> **Tracking issue:** underminedsk/lightweave#3 · **Created:** 2026-07-22 · **Last amended:** 2026-07-22 (see Amendments)
>
> Markers: `[ ]` idle · `[wip]` in progress · `[x]` done · `[f]` failed/blocked (always with a note)

## Purpose

Give authorized operators a stable, browser-accessible control-plane URL over
Starlink without making the Raspberry Pi, internet connection, or cloud service
part of the show runtime. The simplest whole solution is a named Cloudflare
Tunnel plus Cloudflare Access in front of the existing FastAPI service; building
custom public ingress, identity, or firmware networking would cost more and
weaken the installation's existing offline resilience.

## Problem

The control plane already exposes the complete operator surface over HTTP and
WebSocket, and the real adapter already bridges those requests to the conductor
over structured USB serial (`control/app.py:create_app`,
`control/adapters.py:JsonLineSerialConductor`). However, production Pi packaging
is still absent: there is no `deploy/pi/` service definition or runbook, and the
documented launch command binds Uvicorn only for local development
(`control/README.md`).

Three current behaviors need explicit handling before publishing the service:

- `control/app.py:install_ota_artifact` holds one HTTP request open for the
  complete field transfer, while `control/static/app.js:pollOtaInstallWhile`
  ties browser polling to that promise. Bench transfers recorded in
  `docs/HANDOFF.md` take roughly 6 minutes, longer than Cloudflare's normal
  120-second proxy read timeout.
- The OTA handler holds `app.state.conductor_lock` for the transfer. Every normal
  serial-backed route enters that same lock through `conductor_call`, so a command
  submitted during OTA can wait past the proxy timeout and execute later after
  the operator believes it failed.
- `POST /api/network/wifi` and `POST /api/network/hotspot` are available whenever
  NetworkManager is present. On the single-radio Pi Zero 2 W deployment, either
  operation can disconnect the Starlink client and strand remote administration.
- The FastAPI app has no cross-origin mutation check, and `/ws` accepts a browser
  connection without validating its `Origin`. Cloudflare Access authenticates
  the public hostname, but the application should still reject requests initiated
  by an unrelated site in an already-authenticated browser.
- `create_app` constructs the OTA, pattern, and calibration stores under relative
  `.control_*` paths. A field service therefore needs an explicit writable state
  directory separate from the read-only application checkout.

The ESP32 side does not need internet support. The conductor already persists
field configuration in NVS and performers continue rendering through loss of the
Pi or upstream connectivity (`docs/ARCHITECTURE.md` sections 5.2 and 8).

## Solution

Run the Pi Zero 2 W as a normal client of Starlink Wi-Fi. Install a remotely
managed named `cloudflared` tunnel that maps one public hostname to
`http://127.0.0.1:8000`. Create and verify the Cloudflare Access application
before publishing the route. When Access is selected in Q1, enable
cloudflared's `Protect with Access` validation (`access.required`, team name,
and application audience) so a missing or invalid Access token is rejected at
the connector as well as at Cloudflare's edge. Uvicorn remains loopback-only,
so the public route has no Pi IP, dynamic DNS, router port-forward, or direct
Starlink-LAN origin to bypass.

Within the existing control plane:

- Convert field OTA to one server-owned `asyncio.Task`. The POST performs bounded
  preflight, reserves one job, and returns `202`; GET remains the authoritative
  status/result surface. During that reservation, fail other serial-backed work
  promptly instead of letting it queue behind the transfer. Preserve the existing
  serial lock, chunk retry, alignment, readiness, and full-field verification
  logic.
- Add deployment settings for allowed browser origins and whether network
  mutations are enabled. The field service disables network mutation; bench
  development can retain the existing behavior.
- Validate present `Origin` headers on mutating HTTP requests and the WebSocket
  handshake without enabling CORS or breaking authenticated non-browser clients
  that send no `Origin`.
- Prevent the UI from being framed by another site so an authenticated operator
  cannot be clickjacked into a destructive same-origin action.
- Store mutable control-plane data under `/var/lib/lightweave`, separate from the
  read-only checkout under `/opt/lightweave`.
- Add a hardened systemd unit, non-secret environment example, and Pi runbook.
  Cloudflare owns the tunnel service and credentials separately from the FastAPI
  service.

Alternatives considered:

- A tailnet was rejected for this operator path because it requires every browser
  device to install and join a private network; it remains viable for SSH or a
  future private-only deployment.
- A DNS A record, dynamic DNS, and router port forwarding were rejected because
  the tunnel removes the public-IP and Starlink-router dependency entirely.
- A second Wi-Fi adapter or Ethernet uplink was rejected because the accepted
  operating model allows a physical visit when upstream connectivity fails; an
  always-on Basketnet AP is not a first-release requirement.
- A custom Python JWT verifier is rejected for the first release because
  cloudflared's connector-side Protect with Access check provides the required
  second validation without adding application crypto/key-rotation code.

## Relevant files

### Existing (verified 2026-07-22)

- `control/app.py` - owns all HTTP/WS routes, lifecycle tasks, network mutations,
  conductor serialization, OTA orchestration, and in-memory OTA job state.
- `control/static/app.js` - starts OTA, polls OTA state, renders network controls,
  and reconnects the same-origin WebSocket.
- `control/static/index.html` - contains the Operations controls for Wi-Fi,
  hotspot, and OTA actions.
- `control/tests/test_api.py` - covers HTTP/WS behavior, NetworkManager command
  dispatch, and the OTA success/retry/recovery matrix.
- `control/serial_transport.py` - opens the conductor without intentional
  DTR/RTS reset and reconnects after serial failure.
- `control/ota_store.py` - validates and persists the staged artifact before an
  install begins.
- `control/pattern_store.py` - persists saved patterns under a configurable
  directory.
- `control/calibration.py` - persists calibration state under a configurable
  directory.
- `control/requirements.txt` - pins the Python dependencies that must install on
  the Pi Zero 2 W's Raspberry Pi OS Python version.
- `control/README.md` - documents the current loopback development launch and
  real-serial environment.
- `docs/CONTROLPLANE.md` - records the API contract and OTA correctness model.
- `docs/ARCHITECTURE.md` - records the conductor-authoritative, Pi-optional
  runtime boundary.
- `docs/HANDOFF.md` - records verified transfer durations, test counts, hardware
  state, and outstanding Pi packaging.

### New

- `plans/remote-administration.md` - the single living execution artifact for
  this initiative.
- `docs/REMOTE_ADMIN.md` - stable architecture and operator guidance derived
  from this plan; it contains no phase markers or duplicate execution status.
- `deploy/pi/lightweave-control.service` - boots the loopback FastAPI process as
  an unprivileged service and restarts it after failure.
- `deploy/pi/lightweave.env.example` - defines the non-secret deployment contract
  for serial, origin, and network-mutation settings.
- `deploy/pi/README.md` - installs and verifies Raspberry Pi OS dependencies,
  Starlink Wi-Fi, stable serial naming, systemd, Cloudflare Tunnel/Access, logs,
  upgrades, and physical recovery.

## Questionables

- **Q:** Which browser front-door authentication should the first release use?
  Options: (a) Cloudflare Access with an explicit operator email allowlist and
  one-time PIN or existing identity provider / (b) a single shared Basic Auth
  password at a loopback reverse proxy behind the tunnel. Recommendation: (a)
  because it adds no application password store, supports individual revocation,
  and records operator login identity while remaining usable from any browser.

- **Q:** What authorization boundary should remote firmware installation use?
  Options: (a) treat every allowlisted control-plane operator as a fully trusted
  unsigned-firmware publisher / (b) put OTA paths behind a narrower Access policy
  with stronger identity/MFA and a short session / (c) add release-signature
  verification on the Pi / (d) disable remote OTA and keep installation local-only.
  Recommendation: (b) for the first release because it is small, separates routine
  show control from arbitrary firmware execution, and keeps signature verification
  available if unsigned-firmware residual risk is unacceptable.

- **Q:** Does the first release require per-action operator audit, beyond
  Cloudflare Access login history? Options: (a) accept login-level attribution
  only / (b) append a local mutation audit containing validated operator identity,
  action, result, and timestamp. Recommendation: (b) because blackout, force-sleep,
  layout deletion, and OTA have materially different consequences and a small
  append-only audit makes remote incidents reconstructable.

- **Q:** Must OTA job state survive a Pi process restart? Options: (a) keep the
  task process-local and treat a mid-install restart as an explicit recovery state
  using the persisted staged artifact plus live firmware consistency / (b) persist
  a small accepted/terminal job journal and reconcile a prior nonterminal job at
  startup. Recommendation: (a) for the minimal first release because the existing
  updater already treats mixed firmware as recovery and does not support safely
  resuming an arbitrary in-flight serial call; the plan must not claim restart
  survival if this option is chosen.

## Phases

### Phase 1 - Lock the public request boundary

- [ ] Add strict environment parsing for `CONTROL_ALLOWED_ORIGINS` and
  `CONTROL_ALLOW_NETWORK_CHANGES` in `control/app.py`. Parse the comma-separated
  origin list into exact `scheme://host[:port]` values, reject malformed values,
  and accept only explicit true/false spellings for booleans.
- [ ] Make field/serial startup fail closed: require at least one allowed origin,
  default network mutation off, and reject a missing or malformed required field
  setting. Preserve current mock/bench behavior only through explicit development
  defaults or opt-in environment values.
- [ ] Reject malformed, `null`, and unapproved `Origin` values on `POST`, `PUT`,
  `PATCH`, and `DELETE`; continue allowing non-browser clients that omit `Origin`
  and do not enable permissive CORS.
- [ ] Validate the WebSocket `Origin` before `accept()` and deny an unapproved
  browser origin without exposing an accepted socket.
- [ ] Emit `Content-Security-Policy: frame-ancestors 'none'` and
  `X-Frame-Options: DENY` on UI/API responses.
- [ ] Include `allow_changes` in `GET /api/network/wifi`, return `403` from both
  network mutation endpoints when disabled, and hide or disable their UI actions.
- [ ] Add API tests for allowed, disallowed, absent, malformed, null, and missing
  field-origin configuration; WebSocket origins; strict boolean parsing;
  clickjacking headers; and enabled/disabled network mutation.

**Validation gate** - do not exit this phase until every line passes; if a
command fails, fix the cause and re-run.

- [ ] `.venv/bin/python -m pytest control/tests/test_api.py -k 'origin or websocket or wifi or hotspot'`
- [ ] `.venv/bin/python -m pytest control/tests`
- [ ] With a mock Uvicorn server, a configured same-origin browser can load state
  and WebSocket updates, while a foreign Origin cannot mutate or read `/ws`.

### Phase 2 - Detach OTA from the browser request

- [ ] Split `POST /api/operations/ota-install` into bounded synchronous preflight
  and a worker coroutine. Missing artifact, invalid job snapshot, and immediate
  readiness failures remain synchronous `400`/`503`; artifact reads, maintenance
  settle waits, transfer, reboot, and verification run only in the worker.
- [ ] Under one OTA-start lock, atomically re-check that no job is active, publish
  the accepted in-memory state, retain a strong reference to the worker task, and
  then return `202 {"install": <accepted snapshot>}`. A concurrent start returns
  `409` and never invokes the adapter.
- [ ] Make the worker own every terminal transition and consume its exception.
  Success records `running=false`, `complete=true`, message, and `completed_at`;
  any post-acceptance failure records `running=false`, `complete=false`, error,
  and `completed_at`. `GET /api/operations/ota-install` remains `200` and is the
  authoritative terminal-result surface.
- [ ] Reserve conductor access for the OTA worker without allowing ordinary
  calls to queue. While reserved, return `423 Locked` immediately from every
  other serial-backed route, skip ticker serial polls, and reject artifact staging
  or OTA-mode changes. The OTA worker's own readiness and verification calls must
  use an explicit internal path that cannot deadlock on its reservation.
- [ ] Disable serial-backed actions and artifact/OTA mode controls in the UI while
  the job is active; continue polling state and render the explicit busy response
  if another browser has already reserved the conductor.
- [ ] Implement the process-restart behavior selected in Q4. Under recommended
  option (a), cancel and await the task on graceful shutdown, mark the in-memory
  job interrupted, and document that an abrupt process restart returns to the
  existing persisted-artifact/live-firmware recovery flow rather than restoring
  or resuming the job.
- [ ] Change the UI to start once and poll GET until a terminal state independent
  of the POST connection; surface the recorded terminal message/error.
- [ ] Run every OTA test inside a lifespan-managed `with TestClient(app)` block
  and use one bounded `wait_for_ota_terminal()` helper so the detached task is not
  canceled when the per-request portal closes. Preserve all retry, alignment,
  missing-node, node-failure, finalize-timeout, and post-reboot assertions.
- [ ] Add regressions for immediate `202` before maintenance settling, duplicate
  `409`, initiator disconnect, task exception capture, shutdown interruption, and
  all terminal GET fields. For each OTA-busy route, assert `423` and prove its
  adapter method was never called.

**Validation gate**

- [ ] `.venv/bin/python -m pytest control/tests/test_api.py -k ota`
- [ ] `.venv/bin/python -m pytest control/tests`
- [ ] Start an OTA against the mock server, terminate the initiating browser
  request, reconnect, and observe the same job reach one terminal result through
  GET without a second `ota_begin`.

### Phase 3 - Package the Pi and tunnel deployment

- [ ] Add `CONTROL_DATA_DIR` and construct `OtaArtifactStore`, `PatternStore`, and
  `CalibrationStore` below it. Test explicit paths and persistence across app
  restart so production never requires a writable checkout.
- [ ] Verify the pinned `control/requirements.txt` installs and the control test
  suite passes with Raspberry Pi OS's supported Python version before selecting
  that image for the runbook.
- [ ] Add `deploy/pi/lightweave-control.service` for code and virtualenv under
  `/opt/lightweave`, state under `/var/lib/lightweave` via
  `StateDirectory=lightweave`, and a required root-owned mode-0600
  `EnvironmentFile=/etc/lightweave/control.env`.
- [ ] Run exactly one loopback-bound Uvicorn worker as unprivileged user
  `lightweave` with `dialout` group access, `Restart=on-failure`, `RestartSec=5`,
  `TimeoutStopSec=180`, `NoNewPrivileges=true`, `UMask=0077`,
  `ProtectSystem=strict`, and no sudo grant. Limit writes to the state directory.
- [ ] Add `deploy/pi/lightweave.env.example` with
  `CONTROL_CONDUCTOR=serial`, a `/dev/serial/by-path` conductor path,
  `CONTROL_SERIAL_RESET_ON_OPEN=0`, `CONTROL_DATA_DIR=/var/lib/lightweave`, exact
  field origin, and disabled network mutation; do not commit tunnel credentials,
  operator emails, or tokens.
- [ ] Add `deploy/pi/README.md` with install/upgrade/rollback commands, Starlink
  client setup, stable serial discovery, Cloudflare named-tunnel and Access
  policy setup, service/log inspection, and recovery through a local console or
  SSH from the Starlink LAN during a physical visit.
- [ ] In the Cloudflare sequence, create an Access self-hosted application and
  exact operator policy before publishing the tunnel route; prohibit Everyone and
  Bypass policies. Enable connector-side Protect with Access using the team name
  and application audience selected by the chosen auth/OTA policy.
- [ ] Install the remotely managed tunnel token through cloudflared's
  `--token-file` using a root/service-only file, not argv, shell history, or the
  application environment. Document routine and compromise rotation, connector
  deletion, and verification that only the expected connector is active.
- [ ] Create `docs/REMOTE_ADMIN.md` as stable architecture and operator guidance
  that links this plan for execution status and contains no phase/status copy.
- [ ] Update `control/README.md`, `docs/CONTROLPLANE.md`, and
  `docs/ARCHITECTURE.md` to point to the deployed shape without claiming hardware
  verification before it occurs.

**Validation gate**

- [ ] `git diff --check`
- [ ] `.venv/bin/python -m pytest control/tests`
- [ ] `pio test -e native`
- [ ] A controlled app restart preserves staged OTA artifact, saved patterns, and
  calibration beneath `/var/lib/lightweave`; `/opt/lightweave` remains read-only.
- [ ] On Raspberry Pi OS, `systemd-analyze verify deploy/pi/lightweave-control.service`
  passes and a reboot brings both FastAPI and `cloudflared` back without login.
- [ ] From another Starlink Wi-Fi client, port 8000 is not reachable directly;
  the public hostname is reachable only after the configured Access login.

### Phase 4 - Human-owned field rollout and recovery proof

This phase is a rollout gate owned by an operator with the Cloudflare account,
Starlink installation, Pi, and 3-board bench. It begins only after the code lane
lands; the builder supplies the runbook and records evidence but does not invent
account credentials or claim physical verification.

- [ ] Remotely change a harmless pattern and verify the serial acknowledgement
  plus performer update.
- [ ] Disconnect Starlink and verify the field continues its stored pattern and
  power policy without Pi/internet participation.
- [ ] Restore Starlink and verify `cloudflared`, the page, and `/ws` recover
  without restarting the conductor.
- [ ] On the 3-board bench, stage firmware, enter maintenance, start OTA, close
  the browser after transfer begins, reconnect, and verify the same job completes
  with every expected performer firmware-consistent.
- [ ] Exercise the Q2 firmware authorization choice and the Q3 audit choice with
  distinct authorized/unauthorized operators; retain only redacted evidence.
- [ ] If Q4 selects persisted job state, restart the Pi process during an OTA and
  verify startup reconciliation. If it selects process-local state, verify the
  documented recovery flow after interruption without claiming job resumption.
- [ ] Confirm the field deployment cannot remotely join Wi-Fi or start hotspot
  mode, and record the deployed hostname and service verification in
  `docs/HANDOFF.md` without storing credentials.

**Validation gate**

- [ ] An unauthenticated request to the public hostname does not receive a `200`;
  an authenticated browser receives the UI, `/api/state`, and `/ws` updates.
- [ ] From the Starlink LAN,
  `! curl --connect-timeout 3 http://$PI_STARLINK_IP:8000/api/state` confirms the
  loopback-only origin is not directly reachable.
- [ ] `systemctl is-active lightweave-control cloudflared` reports both active
  after a Pi reboot and after Starlink reconnection.
- [ ] The browser-disconnect OTA drill records one install, all expected nodes at
  the staged image size/CRC, and post-reboot firmware consistency.
- [ ] `.venv/bin/python -m pytest control/tests && pio test -e native`

## Proof of work

The implementation PR must keep the complete control test suite and native
firmware logic suite green. New API regressions prove HTTP/WS origin enforcement,
network gating, and every transition in the OTA task lifecycle. Existing OTA
tests retain their behavioral assertions after the endpoint changes; converting
them to polling is not permission to reduce coverage.

No new browser-test framework is required for this narrow vanilla-JS change.
Browser proof is still required: run the mock control plane through the public
hostname to verify Access, WebSocket reconnection, disabled serial actions during
OTA, hidden network actions, and detached OTA polling, then repeat the interruption
path on the 3-board bench. The Pi service gate is executed on Raspberry Pi OS
because macOS cannot validate the installed systemd runtime.

## Lane map

| Lane | Dispatch issue | Phases | One-line scope | Marker mode | Status |
|---|---|---|---|---|---|
| Pending approval | Not created | 1-3 | Control-plane contract, tests, and Pi deployment artifacts | solo | blocked on approval |

**Lanes:** Split runs only after plan approval. The expected shape is one lane:
the OTA API/UI contract, deployment safety settings, systemd contract, and
documentation are tightly coupled and small enough that splitting them would
duplicate integration verification rather than shorten wall-clock time. Phase 4
is a subsequent human-owned rollout gate, not a parallel builder lane.

## Amendments

- **2026-07-22** (Codex, Moda Create): Created the required living plan artifact
  and verified its code, test, and deployment context against the clean base.
- **2026-07-22** (Codex, Moda Review): Folded verified security, OTA concurrency,
  test-lifecycle, persistent-state, systemd, tunnel-token, and rollout findings
  into the plan; retained four policy choices for operator resolution and moved
  status to In review.

## Notes

- Cloudflare Tunnel setup:
  https://developers.cloudflare.com/tunnel/setup/
- Cloudflare Access application model:
  https://developers.cloudflare.com/cloudflare-one/access-controls/applications/choose-application-type/
- Cloudflare proxy read timeout:
  https://developers.cloudflare.com/fundamentals/reference/connection-limits/
- Cloudflare Tunnel supports WebSockets:
  https://developers.cloudflare.com/cloudflare-one/faq/cloudflare-tunnels-faq/
- The domain and final hostname are deployment parameters, not source-controlled
  decisions. Use `control.example.com` only as a redacted example in repository
  files.
- Tunnel creation, DNS, Access policy configuration, Starlink credentials, and
  the final hardware acceptance drill require human-owned accounts or physical
  access; builders document and verify them but never commit their secrets.
