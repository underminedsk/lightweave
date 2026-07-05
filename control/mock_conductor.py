from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


def _now() -> float:
    return time.time()


FIELD_FIRMWARE = {
    "version": "0.2.0",
    "proto": 5,
    "build_id": 0x44D028FD,
    "build_label": "44d028fd",
    "dirty": False,
}

DEFAULT_POWER_POLICY = {
    "light_sleep_check_s": 4,
    "deep_sleep_check_min": 15,
    "led_on_start_min": 20 * 60,
    "led_on_end_min": 6 * 60,
    "current_min": 12 * 60,
    "schedule_enabled": False,
    "force_awake": True,
    "leds_on": True,
}


def _firmware_matches(expected: dict[str, Any], actual: dict[str, Any] | None) -> bool:
    if not actual:
        return False
    return (
        actual.get("proto") == expected.get("proto")
        and actual.get("build_id") == expected.get("build_id")
        and bool(actual.get("dirty")) == bool(expected.get("dirty"))
        and actual.get("version") == expected.get("version")
    )


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
    firmware: dict[str, Any] = field(default_factory=lambda: deepcopy(FIELD_FIRMWARE))

    def as_dict(self, now: float) -> dict[str, Any]:
        has_position = self.x is not None and self.y is not None
        attention = "None"
        if self.status == "missing":
            attention = "Not seen"
        elif self.status == "retired":
            attention = "Retired"
        elif not has_position:
            attention = "Needs position"
        elif not _firmware_matches(FIELD_FIRMWARE, self.firmware):
            attention = "Firmware mismatch"

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
            "firmware": deepcopy(self.firmware),
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
    power: dict[str, Any] = field(default_factory=lambda: deepcopy(DEFAULT_POWER_POLICY))
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
        table_rows = sum(1 for item in lanterns if item["position"] == "Set")
        alive = sum(1 for item in lanterns if item["status"] == "alive" and item["position"] == "Set")
        attention = sum(1 for item in lanterns if item["attention"] != "None")
        return {
            "conductor": {
                "connected": self.connected,
                "uptime_s": round(now - self.started_at, 1),
                "seq": self.seq,
                "wake": self.wake,
                "sync": "locked",
                "firmware": deepcopy(FIELD_FIRMWARE),
            },
            "summary": {
                "alive": alive,
                "total": table_rows,
                "attention": attention,
                "table_rows": table_rows,
                "firmware": self._firmware_summary(lanterns, table_rows),
            },
            "pattern": deepcopy(self.pattern),
            "power": deepcopy(self.power),
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
        if old.x is None or old.y is None:
            return {"ok": False, "error": "old lantern has no position to replace"}
        if new.x is not None or new.y is not None:
            return {"ok": False, "error": "replacement lantern already has a position"}
        if new.status != "alive":
            return {"ok": False, "error": "replacement lantern is not awake"}
        old_label = old.label
        new.x = old.x
        new.y = old.y
        new.label = old_label
        old.x = None
        old.y = None
        old.label = f"{old_label} retired"
        old.status = "retired"
        self._event(f"replace old={old.mac} new={new.mac} label={old_label}")
        return {
            "ok": True,
            "message": f"moved {old_label} to replacement lantern",
            "old_mac": old.mac,
            "new_mac": new.mac,
        }

    def update_pattern(self, pattern: str, brightness: int, params: dict[str, int | float | str]) -> dict[str, Any]:
        self.pattern = {"pattern": pattern, "brightness": brightness, "params": dict(params)}
        self._event(f"pattern={pattern} bri={brightness}")
        return {"ok": True, "message": f"pattern changed to {pattern}", "pattern": deepcopy(self.pattern)}

    def blackout(self) -> dict[str, Any]:
        self.pattern = {"pattern": self.pattern["pattern"], "brightness": 0, "params": deepcopy(self.pattern["params"])}
        self._event("blackout bri=0")
        return {"ok": True, "message": "blackout broadcast", "pattern": deepcopy(self.pattern)}

    def update_power_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(self.power)
        updated.update(policy)
        updated["leds_on"] = bool(updated["force_awake"]) or not bool(updated["schedule_enabled"]) or self._minute_in_window(
            int(updated["current_min"]), int(updated["led_on_start_min"]), int(updated["led_on_end_min"])
        )
        self.power = updated
        self.wake = bool(updated["force_awake"])
        self._event(
            f"power policy light={updated['light_sleep_check_s']}s deep={updated['deep_sleep_check_min']}m"
        )
        return {"ok": True, "message": "power policy changed", "power": deepcopy(self.power)}

    def _find(self, mac: str) -> Lantern | None:
        normalized = mac.upper()
        return next((lantern for lantern in self._lanterns if lantern.mac.upper() == normalized), None)

    def _event(self, message: str) -> None:
        self.events.append({"ts": _now(), "message": message})

    @staticmethod
    def _minute_in_window(minute: int, start: int, end: int) -> bool:
        minute %= 1440
        start %= 1440
        end %= 1440
        if start == end:
            return True
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end

    def _firmware_summary(self, lanterns: list[dict[str, Any]], table_rows: int) -> dict[str, Any]:
        positioned = [item for item in lanterns if item["position"] == "Set" and item["status"] == "alive"]
        matching = sum(1 for item in positioned if _firmware_matches(FIELD_FIRMWARE, item.get("firmware")))
        consistent = all(_firmware_matches(FIELD_FIRMWARE, item.get("firmware")) for item in positioned)
        return {
            "consistent": consistent,
            "matching": matching,
            "seen": len(positioned),
            "expected": table_rows,
            "version": FIELD_FIRMWARE["version"],
            "build_label": FIELD_FIRMWARE["build_label"],
            "dirty": FIELD_FIRMWARE["dirty"],
        }
