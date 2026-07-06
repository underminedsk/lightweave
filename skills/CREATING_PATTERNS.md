# Creating Patterns

Use this guide when an agent is asked to author, compare, review, save, or
broadcast show patterns for Do Baskets Dream.

This is a control-plane workflow for the current compiled pattern vocabulary:
`Pulse`, `Glow`, `Sweep`, and `Palette Drift`. It does not create arbitrary new
firmware pattern functions. New C++ pattern functions still belong in
`include/pattern_math.h` with host tests.

## Preconditions

Start from the repository root.

Read the project onboarding docs first if this is a new session:

1. `docs/PROJECT_BRIEF.md`
2. `docs/ARCHITECTURE.md`
3. `docs/HANDOFF.md`

Run or verify the control-plane server:

```bash
PYTHONPATH=. uvicorn control.app:create_app --factory --host 127.0.0.1 --port 8000
```

Use `http://127.0.0.1:8000` as the base URL unless the user gives a different
control-plane host.

## Rules

- Prefer API-only iteration before browser/UI work.
- Never broadcast a candidate with `rating: reject`.
- Do not use `SOLID` for show authoring. It is a bench power-test pattern only.
- Treat high brightness as power/glare risk. Ask or explain before broadcasting
  values above 128.
- Keep saved pattern names human-readable and specific.
- Use the real positioned lantern layout from the control plane; do not invent a
  fake layout unless explicitly working offline.
- Broadcasting is a live field mutation. Only broadcast when the user asks to run
  a pattern live or clearly approves the candidate.

## Pattern Parameters

`Glow`

```json
{"pattern":"Glow","brightness":48,"params":{"hue":40,"saturation":100}}
```

`Pulse`

```json
{"pattern":"Pulse","brightness":48,"params":{"hue":40,"saturation":100}}
```

`Sweep`

```json
{"pattern":"Sweep","brightness":64,"params":{"period":8000,"spatial":300}}
```

For `Sweep`, `spatial` is the wavelength in hundredths of a coordinate unit
because it maps to firmware `params[1]`.

`Palette Drift`

```json
{"pattern":"Palette Drift","brightness":48,"params":{"period":8000,"spatial":100}}
```

For `Palette Drift`, `spatial` is hue offset in hundredths of a cycle per x unit.

## Draft Review Loop

1. Choose a candidate pattern, brightness, and params.
2. Get automated review:

```bash
curl -sS 'http://127.0.0.1:8000/review?pattern=Sweep&brightness=64&period=8000&spatial=300&duration_ms=8000&fps=4'
```

3. Inspect frame metrics if the pattern is temporal:

```bash
curl -sS 'http://127.0.0.1:8000/preview/frames.json?pattern=Sweep&brightness=64&period=8000&spatial=300&duration_ms=8000&fps=4'
```

4. Generate a still PNG if visual feedback is useful:

```bash
curl -sS -o preview.png 'http://127.0.0.1:8000/preview?pattern=Sweep&brightness=64&period=8000&spatial=300&t=1200'
```

5. Iterate until the review is acceptable and the metrics fit the user's intent.

Useful review fields:

- `ok`: false means do not broadcast.
- `rating`: `strong`, `usable`, `needs_review`, or `reject`.
- `score`: 0-100.
- `issues`: specific blockers or warnings.
- `recommendations`: concrete next adjustments.
- `metrics.avg_luma_mean`: overall brightness across the sampled window.
- `metrics.max_contrast`: spatial variation.
- `metrics.temporal_luma_range`: motion/change over time.

## Save Candidate

Save only candidates worth reusing or broadcasting:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/patterns \
  -H 'content-type: application/json' \
  -d '{"name":"Slow Sweep","pattern":"Sweep","brightness":64,"params":{"period":8000,"spatial":300}}'
```

List saved patterns:

```bash
curl -sS http://127.0.0.1:8000/api/patterns
```

## Review Saved Pattern

Use the saved pattern id from create/list responses.

```bash
curl -sS 'http://127.0.0.1:8000/api/patterns/slow-sweep/review?duration_ms=8000&fps=4'
```

Other saved-pattern preview endpoints:

```bash
curl -sS -o preview.png 'http://127.0.0.1:8000/api/patterns/slow-sweep/preview?t=1200'
curl -sS 'http://127.0.0.1:8000/api/patterns/slow-sweep/preview.json?t=1200'
curl -sS 'http://127.0.0.1:8000/api/patterns/slow-sweep/preview/frames.json?duration_ms=8000&fps=4'
```

## Broadcast Saved Pattern

Broadcast only after review passes and the user approves live execution:

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/patterns/slow-sweep/broadcast'
```

Confirm live state after broadcasting:

```bash
curl -sS http://127.0.0.1:8000/api/state
```

## UI Workflow

Open `http://127.0.0.1:8000`, then use the Patterns tab.

- Tune the draft controls.
- `Save draft` stores the current draft in the pattern library.
- Saved pattern actions:
  - `Preview`: PNG still frame.
  - `Frames`: JSON frame sequence.
  - `Review`: automated score and recommendations.
  - `Broadcast`: live field mutation.
  - `Delete`: remove stale candidates.

## Verification

After changing this workflow or related code, run:

```bash
PYTHONPATH=. pytest control/tests
pio test -e native
python -m compileall control
node --check control/static/app.js
```

If a hardware conductor is attached, also smoke test one saved pattern end to end:

1. Save candidate.
2. Review saved candidate.
3. Broadcast saved candidate.
4. Confirm `/api/state.pattern`.

