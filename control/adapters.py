from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


class ConductorAdapter(Protocol):
    def snapshot(self) -> dict[str, Any]: ...
    def lanterns(self) -> list[dict[str, Any]]: ...
    def tick(self) -> None: ...
    def identify(self, mac: str) -> dict[str, Any]: ...
    def assign(self, mac: str, x: float, y: float) -> dict[str, Any]: ...
    def forget(self, mac: str) -> dict[str, Any]: ...
    def replace(self, old_mac: str, new_mac: str) -> dict[str, Any]: ...
    def update_pattern(self, pattern: str, brightness: int, params: dict[str, int | float | str]) -> dict[str, Any]: ...
    def blackout(self) -> dict[str, Any]: ...
    def update_power_policy(self, policy: dict[str, Any]) -> dict[str, Any]: ...


class JsonLineTransport(Protocol):
    def write_line(self, line: str) -> None: ...
    def read_line(self, timeout_s: float) -> str | None: ...


@dataclass
class JsonLineSerialConductor:
    transport: JsonLineTransport
    timeout_s: float = 1.0
    _next_id: int = field(default=1, init=False)
    _last_state: dict[str, Any] | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def snapshot(self) -> dict[str, Any]:
        response = self._request("state")
        state = response.get("state")
        if not isinstance(state, dict):
            raise SerialProtocolError("state response missing state object")
        self._last_state = state
        return state

    def lanterns(self) -> list[dict[str, Any]]:
        state = self.snapshot()
        lanterns = state.get("lanterns")
        if not isinstance(lanterns, list):
            raise SerialProtocolError("state response missing lanterns list")
        return lanterns

    def tick(self) -> None:
        return None

    def identify(self, mac: str) -> dict[str, Any]:
        return self._request("identify", mac=mac)

    def assign(self, mac: str, x: float, y: float) -> dict[str, Any]:
        return self._request("assign", mac=mac, x=x, y=y)

    def forget(self, mac: str) -> dict[str, Any]:
        return self._request("forget", mac=mac)

    def replace(self, old_mac: str, new_mac: str) -> dict[str, Any]:
        return self._request("replace", old_mac=old_mac, new_mac=new_mac)

    def update_pattern(self, pattern: str, brightness: int, params: dict[str, int | float | str]) -> dict[str, Any]:
        return self._request("pattern", pattern=pattern, brightness=brightness, params=params)

    def blackout(self) -> dict[str, Any]:
        return self._request("blackout")

    def update_power_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        return self._request("power_policy", **policy)

    def _request(self, command: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            request = {"id": request_id, "cmd": command, **payload}
            self.transport.write_line(json.dumps(request, separators=(",", ":")))

            deadline = time.monotonic() + self.timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SerialProtocolError(f"timeout waiting for {command} ack")
                line = self.transport.read_line(remaining)
                if line is None:
                    raise SerialProtocolError(f"timeout waiting for {command} ack")
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") != request_id:
                    continue
                if response.get("ok") is not True:
                    return {"ok": False, "error": str(response.get("error") or "serial command failed")}
                return {"ok": True, **{key: value for key, value in response.items() if key not in {"id", "ok"}}}


class SerialProtocolError(RuntimeError):
    pass
