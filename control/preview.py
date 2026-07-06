from __future__ import annotations

import json
import math
import struct
import zlib
from dataclasses import dataclass
from typing import Any


MAX_BRIGHTNESS = 192
MAX_PREVIEW_FRAMES = 240


@dataclass(frozen=True)
class Rgbw:
    r: int
    g: int
    b: int
    w: int = 0


def parse_params(raw: str | None, aliases: dict[str, int | float | str | None]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if raw:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("params must be valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError("params must be a JSON object")
        params.update(decoded)
    for key, value in aliases.items():
        if value is not None:
            params[key] = value
    return params


def render_preview_png(
    state: dict[str, Any],
    pattern: str,
    brightness: int,
    params: dict[str, Any],
    t_ms: int,
    width: int,
    height: int,
) -> bytes:
    if brightness < 0 or brightness > MAX_BRIGHTNESS:
        raise ValueError(f"brightness must be between 0 and {MAX_BRIGHTNESS}")
    if width < 80 or width > 2000 or height < 80 or height > 2000:
        raise ValueError("width and height must be between 80 and 2000")

    lanterns = [
        item
        for item in state.get("lanterns", [])
        if isinstance(item.get("x"), (int, float)) and isinstance(item.get("y"), (int, float))
    ]
    if not lanterns:
        raise ValueError("no positioned lanterns to preview")

    bg = (12, 14, 18)
    pixels = bytearray(bg * width * height)
    margin = max(18, min(width, height) // 18)
    radius = max(4, min(width, height) // 42)
    synced_us = int(t_ms) * 1000
    normalized = _normalize_pattern(pattern)

    for lantern in lanterns:
        x = float(lantern["x"])
        y = float(lantern["y"])
        color = _pattern_color(normalized, brightness, params, synced_us, x, y)
        rgb = _rgbw_to_preview_rgb(color)
        cx = round(margin + x * (width - 2 * margin))
        cy = round(margin + y * (height - 2 * margin))
        _draw_disc(pixels, width, height, cx, cy, radius + 2, (34, 38, 46))
        _draw_disc(pixels, width, height, cx, cy, radius, rgb)

    return _encode_png(width, height, pixels)


def render_preview_data(
    state: dict[str, Any],
    pattern: str,
    brightness: int,
    params: dict[str, Any],
    t_ms: int,
) -> dict[str, Any]:
    if brightness < 0 or brightness > MAX_BRIGHTNESS:
        raise ValueError(f"brightness must be between 0 and {MAX_BRIGHTNESS}")
    lanterns = [
        item
        for item in state.get("lanterns", [])
        if isinstance(item.get("x"), (int, float)) and isinstance(item.get("y"), (int, float))
    ]
    if not lanterns:
        raise ValueError("no positioned lanterns to preview")

    normalized = _normalize_pattern(pattern)
    synced_us = int(t_ms) * 1000
    rendered = []
    lumas = []
    lit_count = 0
    for lantern in lanterns:
        x = float(lantern["x"])
        y = float(lantern["y"])
        color = _pattern_color(normalized, brightness, params, synced_us, x, y)
        rgb = _rgbw_to_preview_rgb(color)
        luma = _luma(rgb)
        lumas.append(luma)
        if max(color.r, color.g, color.b, color.w) > 0:
            lit_count += 1
        rendered.append({
            "mac": lantern.get("mac"),
            "label": lantern.get("label"),
            "x": x,
            "y": y,
            "status": lantern.get("status"),
            "rgbw": [color.r, color.g, color.b, color.w],
            "rgb": list(rgb),
            "luma": round(luma, 3),
        })

    avg_luma = sum(lumas) / len(lumas)
    min_luma = min(lumas)
    max_luma = max(lumas)
    return {
        "pattern": pattern,
        "brightness": brightness,
        "params": dict(params),
        "t": t_ms,
        "lanterns": rendered,
        "metrics": {
            "count": len(rendered),
            "lit_count": lit_count,
            "avg_luma": round(avg_luma, 3),
            "min_luma": round(min_luma, 3),
            "max_luma": round(max_luma, 3),
            "contrast": round((max_luma - min_luma) / 255.0, 4),
        },
    }


def render_preview_frames(
    state: dict[str, Any],
    pattern: str,
    brightness: int,
    params: dict[str, Any],
    duration_ms: int,
    fps: int,
) -> dict[str, Any]:
    frame_times = _frame_times(duration_ms, fps)
    frames = [
        render_preview_data(state, pattern, brightness, params, t_ms)
        for t_ms in frame_times
    ]
    return {
        "pattern": pattern,
        "brightness": brightness,
        "params": dict(params),
        "duration_ms": duration_ms,
        "fps": fps,
        "frame_count": len(frames),
        "frames": frames,
        "metrics": _sequence_metrics(frames),
    }


def review_preview(
    state: dict[str, Any],
    pattern: str,
    brightness: int,
    params: dict[str, Any],
    duration_ms: int,
    fps: int,
) -> dict[str, Any]:
    sequence = render_preview_frames(state, pattern, brightness, params, duration_ms, fps)
    metrics = sequence["metrics"]
    normalized = _normalize_pattern(pattern)
    issues: list[dict[str, str]] = []

    if brightness == 0:
        issues.append(_issue("error", "blackout", "Brightness is zero; the field will be dark."))
    elif brightness < 8:
        issues.append(_issue("warn", "very_dim", "Brightness is very low; confirm this is intentional."))
    elif brightness > 128:
        issues.append(_issue("warn", "high_brightness", "Brightness is high for battery-powered field use."))

    if metrics["max_lit_count"] == 0:
        issues.append(_issue("error", "no_lit_lanterns", "No positioned lantern is lit in the sampled window."))
    if metrics["avg_luma_mean"] < 2 and brightness > 0:
        issues.append(_issue("warn", "mostly_dark", "Average luma is near black across the sampled window."))
    if normalized in {"sweep", "palette_drift", "pulse"} and metrics["temporal_luma_range"] < 1:
        issues.append(_issue("warn", "no_temporal_change", "The sampled window has almost no visible temporal change."))
    if normalized in {"sweep", "palette_drift"} and metrics["max_contrast"] < 0.02:
        issues.append(_issue("warn", "low_spatial_contrast", "The field has little spatial variation at the sampled times."))
    if normalized == "solid":
        issues.append(_issue("warn", "bench_pattern", "SOLID is a bench power pattern, not a show pattern."))

    score = 100
    for issue in issues:
        score -= 35 if issue["severity"] == "error" else 12
    if metrics["avg_luma_mean"] > 90:
        score -= 8
    if metrics["max_contrast"] > 0.6:
        score -= 4
    score = max(0, min(100, score))

    recommendations = _recommendations(normalized, issues, metrics)
    return {
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "score": score,
        "rating": _rating(score, issues),
        "pattern": pattern,
        "brightness": brightness,
        "params": dict(params),
        "duration_ms": duration_ms,
        "fps": fps,
        "metrics": metrics,
        "issues": issues,
        "recommendations": recommendations,
    }


def _normalize_pattern(pattern: str) -> str:
    key = pattern.strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "pulse": "pulse",
        "palette drift": "palette_drift",
        "drift": "palette_drift",
        "sweep": "sweep",
        "solid": "solid",
        "glow": "glow",
    }
    try:
        return aliases[key]
    except KeyError as error:
        raise ValueError(f"unknown pattern: {pattern}") from error


def _pattern_color(pattern: str, brightness: int, params: dict[str, Any], synced_us: int, x: float, y: float) -> Rgbw:
    if pattern == "pulse":
        hue = _number(params, "hue", "p0", default=0) % 360
        saturation = _saturation(params)
        intensity = _pulse_intensity(synced_us, 4.0, 0.0)
        return _hsv_color(brightness, intensity, hue, saturation)
    if pattern == "palette_drift":
        period_s = _number(params, "period", "p0", default=8000) / 1000.0
        spatial = _number(params, "spatial", "p1", default=0) / 100.0
        hue = _drift_hue(synced_us, x, period_s, spatial)
        r, g, b = _hsv_to_rgb(hue, 1.0, 1.0)
        return Rgbw(round(r * brightness), round(g * brightness), round(b * brightness), 0)
    if pattern == "sweep":
        period_s = _number(params, "period", "p0", default=4000) / 1000.0
        wavelength = _number(params, "wavelength", "spatial", "p1", default=300) / 100.0
        intensity = _sweep_intensity(synced_us, x, period_s, wavelength)
        return Rgbw(0, 0, 0, round(intensity * brightness))
    if pattern == "solid":
        return Rgbw(brightness, brightness, brightness, brightness)
    if pattern == "glow":
        hue = _number(params, "hue", "p0", default=40) % 360
        saturation = _saturation(params)
        return _hsv_color(brightness, 1.0, hue, saturation)
    raise ValueError(f"unknown pattern: {pattern}")


def _frame_times(duration_ms: int, fps: int) -> list[int]:
    if duration_ms < 500 or duration_ms > 60_000:
        raise ValueError("duration_ms must be between 500 and 60000")
    if fps < 1 or fps > 24:
        raise ValueError("fps must be between 1 and 24")
    step_ms = max(1, round(1000 / fps))
    frame_count = max(1, math.ceil(duration_ms / step_ms))
    if frame_count > MAX_PREVIEW_FRAMES:
        raise ValueError(f"preview is limited to {MAX_PREVIEW_FRAMES} frames")
    return [i * step_ms for i in range(frame_count)]


def _sequence_metrics(frames: list[dict[str, Any]]) -> dict[str, Any]:
    frame_metrics = [frame["metrics"] for frame in frames]
    lit_counts = [metric["lit_count"] for metric in frame_metrics]
    avg_lumas = [metric["avg_luma"] for metric in frame_metrics]
    contrasts = [metric["contrast"] for metric in frame_metrics]
    return {
        "min_lit_count": min(lit_counts),
        "max_lit_count": max(lit_counts),
        "avg_lit_count": round(sum(lit_counts) / len(lit_counts), 3),
        "avg_luma_min": round(min(avg_lumas), 3),
        "avg_luma_max": round(max(avg_lumas), 3),
        "avg_luma_mean": round(sum(avg_lumas) / len(avg_lumas), 3),
        "temporal_luma_range": round(max(avg_lumas) - min(avg_lumas), 3),
        "min_contrast": round(min(contrasts), 4),
        "max_contrast": round(max(contrasts), 4),
        "avg_contrast": round(sum(contrasts) / len(contrasts), 4),
    }


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _rating(score: int, issues: list[dict[str, str]]) -> str:
    if any(issue["severity"] == "error" for issue in issues):
        return "reject"
    if score >= 85:
        return "strong"
    if score >= 70:
        return "usable"
    return "needs_review"


def _recommendations(normalized: str, issues: list[dict[str, str]], metrics: dict[str, Any]) -> list[str]:
    codes = {issue["code"] for issue in issues}
    recommendations = []
    if "blackout" in codes or "mostly_dark" in codes or "very_dim" in codes:
        recommendations.append("Raise brightness or sample a brighter phase before broadcasting.")
    if "high_brightness" in codes:
        recommendations.append("Lower brightness unless this is a short bench or special-event cue.")
    if "no_temporal_change" in codes and normalized == "sweep":
        recommendations.append("Shorten period or sample a longer duration to verify the sweep motion.")
    if "low_spatial_contrast" in codes and normalized == "palette_drift":
        recommendations.append("Increase spatial spread if the field should show a color gradient.")
    if "low_spatial_contrast" in codes and normalized == "sweep":
        recommendations.append("Reduce wavelength if the field should show a visible moving wave.")
    if metrics["avg_luma_mean"] > 90:
        recommendations.append("Average luma is high; check battery and glare before running this for long periods.")
    if not recommendations:
        recommendations.append("No blocking issues found in the sampled preview window.")
    return recommendations


def _number(params: dict[str, Any], *keys: str, default: float) -> float:
    for key in keys:
        value = params.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{key} must be numeric") from error
    return default


def _saturation(params: dict[str, Any]) -> float:
    saturation = _number(params, "saturation", "p1", default=100)
    if saturation <= 0:
        saturation = 100
    return min(saturation, 100) / 100.0


def _phase(t_us: int, period_s: float) -> float:
    return (t_us / 1_000_000.0 / period_s) % 1.0


def _pulse_intensity(synced_us: int, period_s: float, spatial: float) -> float:
    p = _phase(synced_us, period_s) + spatial
    return 0.5 * (1.0 - math.cos(2.0 * math.pi * p))


def _sweep_intensity(synced_us: int, x: float, period_s: float, wavelength: float) -> float:
    if period_s <= 0 or wavelength <= 0:
        raise ValueError("period and wavelength must be positive")
    ph = synced_us / 1_000_000.0 / period_s - x / wavelength
    p = ph - math.floor(ph)
    return 0.5 * (1.0 - math.cos(2.0 * math.pi * p))


def _drift_hue(synced_us: int, x: float, period_s: float, spatial: float) -> float:
    if period_s <= 0:
        raise ValueError("period must be positive")
    h = synced_us / 1_000_000.0 / period_s + x * spatial
    return h - math.floor(h)


def _hsv_color(brightness: int, intensity: float, hue_degrees: float, saturation: float) -> Rgbw:
    r, g, b = _hsv_to_rgb(hue_degrees / 360.0, saturation, intensity)
    return Rgbw(round(r * brightness), round(g * brightness), round(b * brightness), 0)


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    h = h - math.floor(h)
    hf = h * 6.0
    i = int(hf)
    f = hf - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    match i % 6:
        case 0:
            return v, t, p
        case 1:
            return q, v, p
        case 2:
            return p, v, t
        case 3:
            return p, q, v
        case 4:
            return t, p, v
        case _:
            return v, p, q


def _rgbw_to_preview_rgb(color: Rgbw) -> tuple[int, int, int]:
    return (
        _clamp_byte(color.r + color.w),
        _clamp_byte(color.g + color.w),
        _clamp_byte(color.b + color.w),
    )


def _clamp_byte(value: int) -> int:
    return max(0, min(255, int(value)))


def _luma(rgb: tuple[int, int, int]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _draw_disc(
    pixels: bytearray,
    width: int,
    height: int,
    cx: int,
    cy: int,
    radius: int,
    color: tuple[int, int, int],
) -> None:
    r2 = radius * radius
    for y in range(max(0, cy - radius), min(height, cy + radius + 1)):
        for x in range(max(0, cx - radius), min(width, cx + radius + 1)):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) > r2:
                continue
            idx = (y * width + x) * 3
            pixels[idx:idx + 3] = bytes(color)


def _encode_png(width: int, height: int, rgb_pixels: bytes | bytearray) -> bytes:
    scanlines = bytearray()
    stride = width * 3
    for y in range(height):
        scanlines.append(0)
        start = y * stride
        scanlines.extend(rgb_pixels[start:start + stride])
    return b"".join([
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
        _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6)),
        _png_chunk(b"IEND", b""),
    ])


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )
