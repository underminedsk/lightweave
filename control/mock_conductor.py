from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


def _now() -> float:
    return time.time()


@dataclass
class Lantern:
    mac: str
    label: str
    status: str
    last_seen_s: float
    x: float | None
    y: float | None
    power_wh: float | None = None
    avg_w: float | None = None

    def as_dict(self, now: float) -> dict[str, Any]:
        has_position = self.x is not None and self.y is not None
        attention = "None"
        if self.status == "missing":
            attention = "Not seen"
        elif not has_position:
            attention = "Needs position"

        return {
            "mac": self.mac,
            "label": self.label,
            "status": self.status,
            "last_seen_s": round(self.last_seen_s, 1),
            "last_seen_label": self._age_label(),
            "x": self.x,
            "y": self.y,
            "position": "Set" if has_position else "Missing",
            "attention": attention,
            "power": {
                "wh": self.power_wh,
                "avg_w": self.avg_w,
                "last_report_label": self._age_label() if self.power_wh is not None else None,
            },
            "updated_at": now,
        }

    def _age_label(self) -> str:
        if self.last_seen_s < 60:
            return f"{int(self.last_seen_s)}s ago"
        minutes = int(self.last_seen_s // 60)
        return f"{minutes}m ago"


@dataclass
class MockConductor:
    started_at: float = field(default_factory=_now)
    seq: int = 184221
    wake: bool = True
    connected: bool = True
    pattern: dict[str, Any] = field(
        default_factory=lambda: {
            "pattern": "Glow",
            "brightness": 48,
            "params": {"hue": 40, "saturation": 100},
        }
    )
    events: list[dict[str, Any]] = field(default_factory=list)
    _lanterns: list[Lantern] = field(
        default_factory=lambda: [
            Lantern("8C:94:DF:8F:71:50", "#0", "alive", 4, 0.54, 0.47, 0.38, 0.71),
            Lantern("30:76:F5:93:67:3C", "#2", "alive", 8, 0.43, 0.36, None, None),
            Lantern("A0:B7:65:11:40:77", "#7", "alive", 9, 0.61, 0.35, None, None),
            Lantern("A0:B7:65:11:42:09", "#9", "alive", 13, 0.34, 0.52, None, None),
            Lantern("A0:B7:65:11:42:14", "#14", "alive", 17, 0.72, 0.55, None, None),
            Lantern("A0:B7:65:11:42:15", "#15", "alive", 20, 0.48, 0.64, None, None),
            Lantern("A0:B7:65:11:44:91", "#18", "missing", 42, 0.66, 0.69, None, None),
            Lantern("8C:94:DF:57:7F:14", "Unknown", "alive", 12, None, None, None, None),
            Lantern("A0:B7:65:11:42:21", "#21", "alive", 10, 0.76, 0.30, None, None),
            Lantern("A0:B7:65:11:42:24", "#24", "alive", 18, 0.22, 0.31, None, None),
        ]
    )

    def __post_init__(self) -> None:
        if not self.events:
            self._event("mock conductor started")
            self._event("beacon seq=184221 pattern=GLOW bri=48 wake=on")

    def snapshot(self) -> dict[str, Any]:
        now = _now()
        lanterns = self.lanterns()
        alive = sum(1 for item in lanterns if item["status"] == "alive")
        attention = sum(1 for item in lanterns if item["attention"] != "None")
        return {
            "conductor": {
                "connected": self.connected,
                "uptime_s": round(now - self.started_at, 1),
                "seq": self.seq,
                "wake": self.wake,
                "sync": "locked",
            },
            "summary": {
                "alive": alive,
                "total": 60,
                "attention": attention,
                "table_rows": sum(1 for item in lanterns if item["position"] == "Set"),
            },
            "pattern": deepcopy(self.pattern),
            "lanterns": lanterns,
            "events": list(reversed(self.events[-20:])),
        }

    def lanterns(self) -> list[dict[str, Any]]:
        now = _now()
        return [lantern.as_dict(now) for lantern in self._lanterns]

    def tick(self) -> None:
        self.seq += 20
        for lantern in self._lanterns:
            lantern.last_seen_s += 5
        self._event(f"beacon seq={self.seq} pattern={self.pattern['pattern'].upper()} bri={self.pattern['brightness']}")

    def identify(self, mac: str) -> dict[str, Any]:
        lantern = self._find(mac)
        if not lantern:
            return {"ok": False, "error": "unknown lantern"}
        self._event(f"identify {lantern.mac} {lantern.label}")
        return {"ok": True, "message": f"identify sent to {lantern.label}", "mac": lantern.mac}

    def assign(self, mac: str, x: float, y: float) -> dict[str, Any]:
        lantern = self._find(mac)
        if not lantern:
            return {"ok": False, "error": "unknown lantern"}
        lantern.x = x
        lantern.y = y
        self._event(f"assign {lantern.mac} x={x:.2f} y={y:.2f}")
        return {"ok": True, "message": f"assigned {lantern.label}", "mac": lantern.mac}

    def forget(self, mac: str) -> dict[str, Any]:
        lantern = self._find(mac)
        if not lantern:
            return {"ok": False, "error": "unknown lantern"}
        lantern.x = None
        lantern.y = None
        self._event(f"forget {lantern.mac}")
        return {"ok": True, "message": f"forgot position for {lantern.label}", "mac": lantern.mac}

    def replace(self, old_mac: str, new_mac: str) -> dict[str, Any]:
        old = self._find(old_mac)
        new = self._find(new_mac)
        if not old or not new:
            return {"ok": False, "error": "old or new lantern not found"}
        new.x = old.x
        new.y = old.y
        old.x = None
        old.y = None
        self._event(f"replace old={old.mac} new={new.mac}")
        return {"ok": True, "message": f"moved position from {old.label} to {new.label}"}

    def update_pattern(self, pattern: str, brightness: int, params: dict[str, int | float | str]) -> dict[str, Any]:
        self.pattern = {"pattern": pattern, "brightness": brightness, "params": dict(params)}
        self._event(f"pattern={pattern} bri={brightness}")
        return {"ok": True, "message": f"broadcast {pattern}", "pattern": deepcopy(self.pattern)}

    def blackout(self) -> dict[str, Any]:
        self.pattern = {"pattern": self.pattern["pattern"], "brightness": 0, "params": deepcopy(self.pattern["params"])}
        self._event("blackout bri=0")
        return {"ok": True, "message": "blackout broadcast", "pattern": deepcopy(self.pattern)}

    def _find(self, mac: str) -> Lantern | None:
        normalized = mac.upper()
        return next((lantern for lantern in self._lanterns if lantern.mac.upper() == normalized), None)

    def _event(self, message: str) -> None:
        self.events.append({"ts": _now(), "message": message})
