# Control Plane

Local web control plane for Do Baskets Dream. This first slice runs against a
mock conductor so the UI/API can be developed without hardware attached.

## Run

Use Python 3.13 or 3.12. Python 3.14 is too new for the pinned FastAPI/Pydantic
dependency stack today.

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -r control/requirements.txt
.venv/bin/python -m uvicorn control.app:app --reload --host 127.0.0.1 --port 8000
```

Open:

- UI: <http://127.0.0.1:8000/>
- OpenAPI: <http://127.0.0.1:8000/docs>

## Test

```bash
.venv/bin/python -m pytest control/tests
pio test -e native
```

## Current Scope

- FastAPI app with HTTP + WebSocket state updates
- Mock conductor adapter
- API-backed Map/Node List/Patterns/Operations UI shell
- Shared lantern detail sheet
- Mock actions for identify, assign, forget, pattern broadcast, and blackout

Next step: add the real serial adapter and firmware-side structured JSON command
protocol while preserving the existing human CLI.
