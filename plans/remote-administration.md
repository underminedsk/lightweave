# Remote administration

> **Status:** Draft
> **Tracking issue:** underminedsk/lightweave#3 · **Created:** 2026-07-22 · **Last amended:** - (see Amendments)
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
- `POST /api/network/wifi` and `POST /api/network/hotspot` are available whenever
  NetworkManager is present. On the single-radio Pi Zero 2 W deployment, either
  operation can disconnect the Starlink client and strand remote administration.
- The FastAPI app has no cross-origin mutation check, and `/ws` accepts a browser
  connection without validating its `Origin`. Cloudflare Access authenticates
  the public hostname, but the application should still reject requests initiated
  by an unrelated site in an already-authenticated browser.

The ESP32 side does not need internet support. The conductor already persists
field configuration in NVS and performers continue rendering through loss of the
Pi or upstream connectivity (`docs/ARCHITECTURE.md` sections 5.2 and 8).

## Solution

Run the Pi Zero 2 W as a normal client of Starlink Wi-Fi. Install a named
`cloudflared` tunnel that maps one public hostname to
`http://127.0.0.1:8000`, with a Cloudflare Access policy covering the entire
hostname. Uvicorn remains loopback-only, so the public route has no Pi IP,
dynamic DNS, router port-forward, or direct Starlink-LAN origin to bypass.

Within the existing control plane:

- Convert field OTA to one server-owned `asyncio.Task`. The POST starts one job
  and returns `202`; GET remains the authoritative status/result surface. Preserve
  the existing serial lock, chunk retry, alignment, readiness, and full-field
  verification logic.
- Add deployment settings for allowed browser origins and whether network
  mutations are enabled. The field service disables network mutation; bench
  development can retain the existing behavior.
- Validate present `Origin` headers on mutating HTTP requests and the WebSocket
  handshake without enabling CORS or breaking authenticated non-browser clients
  that send no `Origin`.
- Add a systemd unit, non-secret environment example, and Pi runbook. Cloudflare
  owns the tunnel service and credentials separately from the FastAPI service.

Alternatives considered:

- A tailnet was rejected for this operator path because it requires every browser
  device to install and join a private network; it remains viable for SSH or a
  future private-only deployment.
- A DNS A record, dynamic DNS, and router port forwarding were rejected because
  the tunnel removes the public-IP and Starlink-router dependency entirely.
- A second Wi-Fi adapter or Ethernet uplink was rejected because the accepted
  operating model allows a physical visit when upstream connectivity fails; an
  always-on Basketnet AP is not a first-release requirement.
- Origin-side Cloudflare JWT validation is deferred while loopback is the only
  listener and the named tunnel is the only external ingress. It becomes required
  if a second ingress path is introduced.

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
- `control/README.md` - documents the current loopback development launch and
  real-serial environment.
- `docs/REMOTE_ADMIN.md` - current design note; it will become the durable
  deployment/operator runbook summary rather than remain a second execution plan.
- `docs/CONTROLPLANE.md` - records the API contract and OTA correctness model.
- `docs/ARCHITECTURE.md` - records the conductor-authoritative, Pi-optional
  runtime boundary.
- `docs/HANDOFF.md` - records verified transfer durations, test counts, hardware
  state, and outstanding Pi packaging.

### New

- `plans/remote-administration.md` - the single living execution artifact for
  this initiative.
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

## Phases

### Phase 1 - Lock the public request boundary

- [ ] Add a small environment parser for `CONTROL_ALLOWED_ORIGINS` and
  `CONTROL_ALLOW_NETWORK_CHANGES` in `control/app.py`; preserve current bench
  behavior unless field configuration explicitly disables network changes.
- [ ] Reject a present unapproved `Origin` on `POST`, `PUT`, `PATCH`, and
  `DELETE`; continue allowing clients that send no `Origin` and do not enable
  permissive CORS.
- [ ] Validate the WebSocket `Origin` before `accept()` and close an unapproved
  browser origin with code `1008`.
- [ ] Include `allow_changes` in `GET /api/network/wifi`, return `403` from both
  network mutation endpoints when disabled, and hide or disable their UI actions.
- [ ] Add API tests for allowed, disallowed, absent, and unconfigured origins;
  WebSocket origins; and enabled/disabled network mutation.

**Validation gate** - do not exit this phase until every line passes; if a
command fails, fix the cause and re-run.

- [ ] `.venv/bin/python -m pytest control/tests/test_api.py -k 'origin or websocket or wifi or hotspot'`
- [ ] `.venv/bin/python -m pytest control/tests`
- [ ] With a mock Uvicorn server, a configured same-origin browser can load state
  and WebSocket updates, while a foreign Origin cannot mutate or read `/ws`.

### Phase 2 - Detach OTA from the browser request

- [ ] Extract the existing transfer and verification body into an internal
  coroutine that owns all terminal success/failure state updates and does not
  leak an unobserved task exception.
- [ ] Add a dedicated OTA job lifecycle guard so two near-simultaneous POSTs
  cannot both pass preflight or invoke `ota_begin`; retain
  `app.state.conductor_lock` for serial exclusivity.
- [ ] Make `POST /api/operations/ota-install` return `202` with the accepted job
  snapshot and `409` while a job is already active; keep GET authoritative.
- [ ] On application shutdown, cancel and await an active OTA task, record it as
  interrupted, and leave recovery based on staged artifact plus live firmware
  consistency rather than reporting success.
- [ ] Change the UI to start once and poll GET until a terminal state independent
  of the POST connection; surface the recorded terminal message/error.
- [ ] Adapt every existing OTA test to the job contract without weakening its
  retry, alignment, missing-node, node-failure, finalize-timeout, or post-reboot
  assertions.
- [ ] Add regressions for immediate `202`, duplicate `409`, initiator disconnect,
  task exception capture, and shutdown interruption.

**Validation gate**

- [ ] `.venv/bin/python -m pytest control/tests/test_api.py -k ota`
- [ ] `.venv/bin/python -m pytest control/tests`
- [ ] Start an OTA against the mock server, terminate the initiating browser
  request, reconnect, and observe the same job reach one terminal result through
  GET without a second `ota_begin`.

### Phase 3 - Package the Pi and tunnel deployment

- [ ] Add `deploy/pi/lightweave-control.service` with a fixed working directory,
  loopback Uvicorn bind, unprivileged service user, serial-group access,
  `Restart=on-failure`, and bounded restart delay.
- [ ] Add `deploy/pi/lightweave.env.example` with
  `CONTROL_CONDUCTOR=serial`, a `/dev/serial/by-path` conductor path,
  `CONTROL_SERIAL_RESET_ON_OPEN=0`, field origin, and disabled network mutation;
  do not commit tunnel credentials, operator emails, or tokens.
- [ ] Add `deploy/pi/README.md` with install/upgrade/rollback commands, Starlink
  client setup, stable serial discovery, Cloudflare named-tunnel and Access
  policy setup, service/log inspection, and physical recovery.
- [ ] Rewrite `docs/REMOTE_ADMIN.md` as stable architecture and operator guidance
  that links this plan for execution status; remove duplicate phase/status text.
- [ ] Update `control/README.md`, `docs/CONTROLPLANE.md`, and
  `docs/ARCHITECTURE.md` to point to the deployed shape without claiming hardware
  verification before it occurs.

**Validation gate**

- [ ] `git diff --check`
- [ ] `.venv/bin/python -m pytest control/tests`
- [ ] `pio test -e native`
- [ ] On Raspberry Pi OS, `systemd-analyze verify deploy/pi/lightweave-control.service`
  passes and a reboot brings both FastAPI and `cloudflared` back without login.
- [ ] From another Starlink Wi-Fi client, port 8000 is not reachable directly;
  the public hostname is reachable only after the configured Access login.

### Phase 4 - Prove interruption and recovery on hardware

- [ ] Remotely change a harmless pattern and verify the serial acknowledgement
  plus performer update.
- [ ] Disconnect Starlink and verify the field continues its stored pattern and
  schedule without Pi/internet participation.
- [ ] Restore Starlink and verify `cloudflared`, the page, and `/ws` recover
  without restarting the conductor.
- [ ] On the 3-board bench, stage firmware, enter maintenance, start OTA, close
  the browser after transfer begins, reconnect, and verify the same job completes
  with every expected performer firmware-consistent.
- [ ] Confirm the field deployment cannot remotely join Wi-Fi or start hotspot
  mode, and record the deployed hostname and service verification in
  `docs/HANDOFF.md` without storing credentials.

**Validation gate**

- [ ] `curl -fsS https://control.example.com/api/state` is denied before Access
  authentication and succeeds in an authenticated browser session.
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
hostname to verify Access, WebSocket reconnection, hidden network actions, and
detached OTA polling, then repeat the interruption path on the 3-board bench.
The Pi service gate is executed on Raspberry Pi OS because macOS cannot validate
the installed systemd runtime.

## Lane map

| Lane | Dispatch issue | Phases | One-line scope | Marker mode | Status |
|---|---|---|---|---|---|
| Pending approval | Not created | 1-4 | One coherent control-plane and Pi-deployment delivery | solo | blocked on approval |

**Lanes:** Split runs only after plan approval. The expected shape is one lane:
the OTA API/UI contract, deployment safety settings, systemd contract, and
end-to-end interruption proof are tightly coupled and small enough that splitting
them would duplicate integration verification rather than shorten wall-clock
time.

## Amendments

- **2026-07-22** (Codex, Moda Create): Promoted the remote-admin design note into
  the required living plan artifact and verified its code, test, and deployment
  claims against the working tree.

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
