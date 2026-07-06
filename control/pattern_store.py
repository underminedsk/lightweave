from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PatternStoreError(ValueError):
    pass


@dataclass
class PatternStore:
    root: Path = Path(".control_patterns")

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.path = self.root / "patterns.json"

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._load().values(), key=lambda item: item["name"].lower())

    def get(self, pattern_id: str) -> dict[str, Any] | None:
        item = self._load().get(pattern_id)
        return dict(item) if item else None

    def create(self, name: str, pattern: str, brightness: int, params: dict[str, Any]) -> dict[str, Any]:
        items = self._load()
        pattern_id = _unique_id(_slug(name), items)
        now = _now()
        item = _normalize_item({
            "id": pattern_id,
            "name": name,
            "pattern": pattern,
            "brightness": brightness,
            "params": params,
            "created_at": now,
            "updated_at": now,
        })
        items[pattern_id] = item
        self._save(items)
        return dict(item)

    def update(self, pattern_id: str, name: str, pattern: str, brightness: int, params: dict[str, Any]) -> dict[str, Any] | None:
        items = self._load()
        existing = items.get(pattern_id)
        if not existing:
            return None
        item = _normalize_item({
            "id": pattern_id,
            "name": name,
            "pattern": pattern,
            "brightness": brightness,
            "params": params,
            "created_at": existing.get("created_at") or _now(),
            "updated_at": _now(),
        })
        items[pattern_id] = item
        self._save(items)
        return dict(item)

    def delete(self, pattern_id: str) -> bool:
        items = self._load()
        if pattern_id not in items:
            return False
        del items[pattern_id]
        self._save(items)
        return True

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise PatternStoreError("pattern store is corrupt")
        return {str(key): _normalize_item(value) for key, value in raw.items()}

    def _save(self, items: dict[str, dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(items, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.replace(self.path)


def _normalize_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise PatternStoreError("pattern item must be an object")
    pattern_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    pattern = str(item.get("pattern") or "").strip()
    try:
        brightness = int(item.get("brightness"))
    except (TypeError, ValueError) as error:
        raise PatternStoreError("brightness must be between 0 and 192") from error
    params = item.get("params") or {}
    if not pattern_id:
        raise PatternStoreError("pattern id is required")
    if not name:
        raise PatternStoreError("pattern name is required")
    if not pattern:
        raise PatternStoreError("pattern type is required")
    if brightness < 0 or brightness > 192:
        raise PatternStoreError("brightness must be between 0 and 192")
    if not isinstance(params, dict):
        raise PatternStoreError("params must be an object")
    return {
        "id": pattern_id,
        "name": name,
        "pattern": pattern,
        "brightness": brightness,
        "params": dict(params),
        "created_at": float(item.get("created_at") or _now()),
        "updated_at": float(item.get("updated_at") or _now()),
    }


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "pattern"


def _unique_id(base: str, items: dict[str, Any]) -> str:
    candidate = base
    i = 2
    while candidate in items:
        candidate = f"{base}-{i}"
        i += 1
    return candidate


def _now() -> float:
    return time.time()
