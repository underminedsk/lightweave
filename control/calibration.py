from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, UnidentifiedImageError


MAX_CALIBRATION_IMAGE_BYTES = 25 * 1024 * 1024
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
TRACK_MATCH_DISTANCE = 0.035
DEFAULT_MIN_AREA = 4
VIDEO_FRAME_MIN_AREA_RATIO = 0.00045
TEMPORAL_MAX_DIMENSION = 720
TEMPORAL_DIFF_THRESHOLD = 50
TEMPORAL_WINDOW_RATIO = 0.075
TEMPORAL_PEAK_DISTANCE_RATIO = 0.14
TEMPORAL_CANDIDATE_COUNT = 4


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class CalibrationFrame:
    frame_id: str
    filename: str
    size: int
    sha256: str
    width: int
    height: int
    uploaded_at: float
    path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "uploaded_at": self.uploaded_at,
        }


class CalibrationStore:
    def __init__(self, root: Path | str = ".control_calibration") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.root / "frames.json"
        self._frames = self._load_frames()

    def list_frames(self) -> list[dict[str, Any]]:
        return [frame.as_dict() for frame in self._frames.values()]

    def frame(self, frame_id: str) -> CalibrationFrame | None:
        return self._frames.get(frame_id)

    def add_image(self, filename: str, data: bytes) -> dict[str, Any]:
        clean_name = Path(filename or "calibration.png").name
        suffix = Path(clean_name).suffix.lower()
        if suffix not in SUPPORTED_IMAGE_SUFFIXES:
            raise CalibrationError("calibration image must be .jpg, .jpeg, .png, or .webp")
        if not data:
            raise CalibrationError("calibration image is empty")
        if len(data) > MAX_CALIBRATION_IMAGE_BYTES:
            raise CalibrationError(f"calibration image exceeds {MAX_CALIBRATION_IMAGE_BYTES} bytes")

        sha256 = hashlib.sha256(data).hexdigest()
        frame_id = sha256[:16]
        path = self.root / f"{frame_id}{suffix}"
        path.write_bytes(data)
        try:
            with Image.open(path) as image:
                width, height = image.size
                image.verify()
        except (OSError, UnidentifiedImageError) as error:
            path.unlink(missing_ok=True)
            raise CalibrationError("calibration image could not be decoded") from error

        frame = CalibrationFrame(
            frame_id=frame_id,
            filename=clean_name,
            size=len(data),
            sha256=sha256,
            width=int(width),
            height=int(height),
            uploaded_at=time.time(),
            path=path,
        )
        self._frames[frame_id] = frame
        self._save_frames()
        return frame.as_dict()

    def detect(self, frame_id: str, threshold: int = 180, min_area: int = DEFAULT_MIN_AREA) -> dict[str, Any]:
        frame = self.frame(frame_id)
        if frame is None:
            raise CalibrationError("unknown calibration frame")
        return detect_bright_blobs(
            frame.path,
            threshold=threshold,
            min_area=effective_min_area(frame, min_area),
        )

    def decode_sequence(
        self,
        frame_ids: list[str],
        threshold: int = 180,
        min_area: int = DEFAULT_MIN_AREA,
        max_distance: float = TRACK_MATCH_DISTANCE,
    ) -> dict[str, Any]:
        if not frame_ids:
            raise CalibrationError("calibration sequence needs at least one frame")
        if max_distance <= 0 or max_distance > 1:
            raise CalibrationError("max_distance must be between 0 and 1")
        frames = []
        for frame_id in frame_ids:
            frame = self.frame(frame_id)
            if frame is None:
                raise CalibrationError(f"unknown calibration frame: {frame_id}")
            detection = self.detect(frame_id, threshold=threshold, min_area=min_area)
            frames.append({"frame": frame.as_dict(), "detection": detection})
        return decode_presence_sequence(frames, max_distance=max_distance)

    def propose_layout(
        self,
        frame_ids: list[str],
        roster_macs: list[str],
        threshold: int = 180,
        min_area: int = DEFAULT_MIN_AREA,
        max_distance: float = TRACK_MATCH_DISTANCE,
        first_code: int = 1,
        code_map: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not roster_macs and not code_map:
            raise CalibrationError("calibration proposal needs at least one roster MAC")
        if len(set(roster_macs)) != len(roster_macs):
            raise CalibrationError("calibration proposal roster MACs must be unique")
        if first_code < 0:
            raise CalibrationError("first_code must be non-negative")
        frames = []
        for frame_id in frame_ids:
            frame = self.frame(frame_id)
            if frame is None:
                raise CalibrationError(f"unknown calibration frame: {frame_id}")
            frames.append(frame)
        if code_map is not None:
            if _should_use_temporal_code_detection(frames):
                temporal = propose_layout_from_temporal_code_frames(frames, code_map)
                metrics = temporal.get("metrics") or {}
                if int(metrics.get("assigned") or 0) == int(metrics.get("expected") or 0) and int(metrics.get("ambiguous") or 0) == 0:
                    return temporal
            decoded = self.decode_sequence(
                frame_ids,
                threshold=threshold,
                min_area=min_area,
                max_distance=max_distance,
            )
            return propose_layout_from_code_map(decoded, code_map)
        decoded = self.decode_sequence(
            frame_ids,
            threshold=threshold,
            min_area=min_area,
            max_distance=max_distance,
        )
        return propose_layout_from_tracks(decoded, roster_macs, first_code=first_code)

    def add_synthetic_sequence(
        self,
        nodes: list[dict[str, Any]],
        width: int = 960,
        height: int = 720,
        first_code: int = 1,
        bit_count: int | None = None,
        blob_radius: int = 5,
        led_value: int = 255,
        jitter_px: float = 0.0,
        glare_count: int = 0,
        glare_value: int = 230,
        missing_frames: list[int] | None = None,
        perspective: float = 0.0,
        min_hamming_distance: int = 3,
        threshold: int = 180,
        min_area: int = DEFAULT_MIN_AREA,
        max_distance: float = TRACK_MATCH_DISTANCE,
    ) -> dict[str, Any]:
        sequence = render_synthetic_sequence(
            nodes,
            width=width,
            height=height,
            first_code=first_code,
            bit_count=bit_count,
            blob_radius=blob_radius,
            led_value=led_value,
            jitter_px=jitter_px,
            glare_count=glare_count,
            glare_value=glare_value,
            missing_frames=missing_frames,
            perspective=perspective,
            min_hamming_distance=min_hamming_distance,
        )
        frames = []
        for item in sequence["frames"]:
            frames.append(self.add_image(item["filename"], item["data"]))
        proposal = self.propose_layout(
            [frame["frame_id"] for frame in frames],
            [],
            threshold=threshold,
            min_area=min_area,
            max_distance=max_distance,
            first_code=first_code,
            code_map=sequence["plan"]["codes"],
        )
        return {
            "frames": frames,
            "plan": sequence["plan"],
            "proposal": proposal,
            "settings": {
                "width": width,
                "height": height,
                "first_code": first_code,
                "bit_count": sequence["plan"]["bit_count"],
                "blob_radius": blob_radius,
                "led_value": led_value,
                "jitter_px": jitter_px,
                "glare_count": glare_count,
                "glare_value": glare_value,
                "missing_frames": missing_frames or [],
                "perspective": perspective,
                "min_hamming_distance": min_hamming_distance,
                "threshold": threshold,
                "min_area": min_area,
                "max_distance": max_distance,
            },
        }

    def _load_frames(self) -> dict[str, CalibrationFrame]:
        if not self._manifest_path.exists():
            return {}
        try:
            raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            frames = {}
            for item in raw:
                path = self.root / str(item["path"])
                if not path.exists():
                    continue
                frame = CalibrationFrame(
                    frame_id=str(item["frame_id"]),
                    filename=str(item["filename"]),
                    size=int(item["size"]),
                    sha256=str(item["sha256"]),
                    width=int(item["width"]),
                    height=int(item["height"]),
                    uploaded_at=float(item["uploaded_at"]),
                    path=path,
                )
                frames[frame.frame_id] = frame
            return frames
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            return {}

    def _save_frames(self) -> None:
        payload = []
        for frame in self._frames.values():
            item = frame.as_dict()
            item["path"] = frame.path.name
            payload.append(item)
        self._manifest_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def effective_min_area(frame: CalibrationFrame, requested_min_area: int) -> int:
    if requested_min_area != DEFAULT_MIN_AREA:
        return requested_min_area
    if not frame.filename.startswith("video-calibration-"):
        return requested_min_area
    scaled = int(round(frame.width * frame.height * VIDEO_FRAME_MIN_AREA_RATIO))
    return max(DEFAULT_MIN_AREA, scaled)


def _should_use_temporal_code_detection(frames: list[CalibrationFrame]) -> bool:
    return any(frame.filename.startswith("video-calibration-") for frame in frames)


def detect_bright_blobs(path: Path, threshold: int = 180, min_area: int = DEFAULT_MIN_AREA) -> dict[str, Any]:
    if threshold < 0 or threshold > 255:
        raise CalibrationError("threshold must be between 0 and 255")
    if min_area < 1:
        raise CalibrationError("min_area must be at least 1")

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = rgb.load()
        active = bytearray(width * height)
        for y in range(height):
            row = y * width
            for x in range(width):
                r, g, b = pixels[x, y]
                if max(r, g, b) >= threshold:
                    active[row + x] = 1

    seen = bytearray(width * height)
    detections = []
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            if not active[idx] or seen[idx]:
                continue
            blob = _flood_blob(active, seen, width, height, x, y)
            if blob["area"] < min_area:
                continue
            detections.append(_blob_result(blob, width, height))

    detections.sort(key=lambda item: (-item["area"], item["y"], item["x"]))
    return {
        "width": width,
        "height": height,
        "threshold": threshold,
        "min_area": min_area,
        "detections": detections,
        "metrics": {
            "count": len(detections),
            "lit_pixels": sum(item["area"] for item in detections),
        },
    }


def decode_presence_sequence(frames: list[dict[str, Any]], max_distance: float = TRACK_MATCH_DISTANCE) -> dict[str, Any]:
    tracks: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(frames):
        detections = frame["detection"].get("detections") or []
        matched_tracks: set[int] = set()
        for detection in detections:
            track_index = _nearest_track(tracks, matched_tracks, detection, max_distance)
            if track_index is None:
                track = {
                    "observations": [None] * len(frames),
                    "sum_x": 0.0,
                    "sum_y": 0.0,
                    "count": 0,
                }
                tracks.append(track)
                track_index = len(tracks) - 1
            track = tracks[track_index]
            track["observations"][frame_index] = detection
            track["sum_x"] += float(detection["x"])
            track["sum_y"] += float(detection["y"])
            track["count"] += 1
            matched_tracks.add(track_index)

    decoded = []
    for index, track in enumerate(tracks):
        count = int(track["count"])
        if count <= 0:
            continue
        bits = "".join("1" if item is not None else "0" for item in track["observations"])
        decoded.append({
            "track_id": index,
            "x": round(float(track["sum_x"]) / count, 6),
            "y": round(float(track["sum_y"]) / count, 6),
            "bits": bits,
            "value": int(bits, 2) if bits else 0,
            "frames_seen": count,
            "observations": [
                None if item is None else {
                    "x": item["x"],
                    "y": item["y"],
                    "area": item["area"],
                }
                for item in track["observations"]
            ],
        })
    decoded.sort(key=lambda item: (item["value"], item["y"], item["x"]))
    return {
        "frame_count": len(frames),
        "max_distance": max_distance,
        "tracks": decoded,
        "metrics": {
            "track_count": len(decoded),
            "ambiguous_values": _duplicate_values(decoded),
        },
    }


def propose_layout_from_tracks(
    decoded: dict[str, Any],
    roster_macs: list[str],
    first_code: int = 1,
) -> dict[str, Any]:
    code_to_mac = {
        first_code + index: mac
        for index, mac in enumerate(roster_macs)
    }
    assignments = []
    selected, ambiguous_codes, extra_tracks = _select_tracks_by_code(decoded, set(code_to_mac))
    for value, track in selected.items():
        mac = code_to_mac[value]
        assignments.append({
            "mac": mac,
            "x": track["x"],
            "y": track["y"],
            "code": value,
            "bits": track["bits"],
            "track_id": track["track_id"],
            "frames_seen": track["frames_seen"],
        })

    assigned_codes = {item["code"] for item in assignments}
    missing = [
        {
            "mac": mac,
            "code": code,
            "reason": "not detected",
        }
        for code, mac in code_to_mac.items()
        if code not in assigned_codes and code not in ambiguous_codes
    ]
    ambiguous = [
        {
            "mac": code_to_mac[code],
            "code": code,
            "reason": "multiple tracks decoded to this code",
        }
        for code in sorted(ambiguous_codes)
    ]
    return {
        "assignments": assignments,
        "missing": missing,
        "ambiguous": ambiguous,
        "extra_tracks": extra_tracks,
        "metrics": {
            "expected": len(roster_macs),
            "assigned": len(assignments),
            "missing": len(missing),
            "ambiguous": len(ambiguous),
            "extra": len(extra_tracks),
        },
        "decoded": decoded,
    }


def propose_layout_from_code_map(
    decoded: dict[str, Any],
    code_map: list[dict[str, Any]],
) -> dict[str, Any]:
    code_to_item = _code_items_by_value(code_map)
    return _best_code_map_proposal(decoded, code_to_item)


def _code_items_by_value(code_map: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if not code_map:
        raise CalibrationError("calibration proposal code map is empty")
    code_to_item: dict[int, dict[str, Any]] = {}
    for item in code_map:
        value = int(item["code"])
        if value in code_to_item:
            raise CalibrationError("calibration proposal code map values must be unique")
        code_to_item[value] = {
            "mac": str(item["mac"]),
            "code": value,
            "bits": str(item["bits"]),
        }
    return code_to_item


def propose_layout_from_temporal_code_frames(
    frames: list[CalibrationFrame],
    code_map: list[dict[str, Any]],
) -> dict[str, Any]:
    if not frames:
        raise CalibrationError("calibration sequence needs at least one frame")
    if not code_map:
        raise CalibrationError("calibration proposal code map is empty")
    code_to_item = _code_items_by_value(code_map)
    bit_counts = {len(str(item["bits"])) for item in code_to_item.values()}
    if len(bit_counts) != 1:
        raise CalibrationError("calibration code map entries must use the same bit count")
    bit_count = bit_counts.pop()
    if len(frames) < bit_count:
        raise CalibrationError("calibration video has fewer extracted frames than the code plan needs")

    width, height, brightness_frames = _load_temporal_brightness(frames)
    proposals = []
    for frame_window_start in range(0, len(brightness_frames) - bit_count + 1):
        window = brightness_frames[frame_window_start:frame_window_start + bit_count]
        rotations = range(bit_count) if bit_count > 1 else range(1)
        for offset in rotations:
            proposal = _temporal_code_proposal_for_offset(
                code_to_item,
                width,
                height,
                window,
                offset,
            )
            proposal["frame_window_start"] = frame_window_start
            proposal["frame_window_count"] = bit_count
            proposals.append(proposal)
    return max(proposals, key=_temporal_proposal_score)


def _load_temporal_brightness(frames: list[CalibrationFrame]) -> tuple[int, int, list[list[int]]]:
    first_width = frames[0].width
    first_height = frames[0].height
    scale = min(1.0, TEMPORAL_MAX_DIMENSION / max(first_width, first_height))
    width = max(1, int(round(first_width * scale)))
    height = max(1, int(round(first_height * scale)))
    brightness_frames = []
    for frame in frames:
        with Image.open(frame.path) as image:
            rgb = image.convert("RGB")
            if rgb.size != (first_width, first_height):
                raise CalibrationError("calibration sequence frames must have matching dimensions")
            if rgb.size != (width, height):
                rgb = rgb.resize((width, height), Image.Resampling.BILINEAR)
            pixels = rgb.load()
            values = [0] * (width * height)
            for y in range(height):
                row = y * width
                for x in range(width):
                    r, g, b = pixels[x, y]
                    values[row + x] = max(r, g, b)
            brightness_frames.append(values)
    return width, height, brightness_frames


def _temporal_code_proposal_for_offset(
    code_to_item: dict[int, dict[str, Any]],
    width: int,
    height: int,
    brightness_frames: list[list[int]],
    alignment_offset: int,
) -> dict[str, Any]:
    frame_count = len(brightness_frames)
    tracks = []
    assignments = []
    missing = []
    ambiguous = []
    extra_tracks = []
    total_score = 0.0
    for item in code_to_item.values():
        observed_bits = _unrotate_bits(str(item["bits"]), alignment_offset)
        candidates = _temporal_candidates_for_bits(
            brightness_frames,
            width,
            height,
            observed_bits,
        )
        if not candidates:
            missing.append({
                "mac": item["mac"],
                "code": int(item["code"]),
                "bits": item["bits"],
                "reason": "not detected",
            })
            continue
        best = candidates[0]
        if len(candidates) > 1 and float(candidates[1]["score"]) >= float(best["score"]) * 0.82:
            ambiguous.append({
                "mac": item["mac"],
                "code": int(item["code"]),
                "bits": item["bits"],
                "reason": "multiple temporal candidates matched this code",
            })
            extra_tracks.extend(_temporal_extra_tracks(candidates, item, alignment_offset, frame_count))
            continue
        track_id = len(tracks)
        track = _temporal_track(best, item, track_id, alignment_offset, frame_count)
        tracks.append(track)
        assignments.append({
            "mac": item["mac"],
            "x": track["x"],
            "y": track["y"],
            "code": int(item["code"]),
            "bits": item["bits"],
            "expected_bits": item["bits"],
            "observed_bits": observed_bits,
            "track_id": track_id,
            "frames_seen": track["frames_seen"],
            "score": track["score"],
        })
        total_score += float(best["score"])
        extra_tracks.extend(_temporal_extra_tracks(candidates[1:], item, alignment_offset, frame_count))

    decoded = {
        "frame_count": frame_count,
        "tracks": sorted(tracks, key=lambda track: (track["value"], track["y"], track["x"])),
        "metrics": {
            "track_count": len(tracks),
            "ambiguous_values": _duplicate_values(tracks),
            "temporal": True,
            "temporal_score": round(total_score, 3),
        },
    }
    return {
        "assignments": assignments,
        "missing": missing,
        "ambiguous": ambiguous,
        "extra_tracks": extra_tracks,
        "metrics": {
            "expected": len(code_to_item),
            "assigned": len(assignments),
            "missing": len(missing),
            "ambiguous": len(ambiguous),
            "extra": len(extra_tracks),
        },
        "alignment_offset": alignment_offset,
        "temporal_score": round(total_score, 3),
        "decoded": decoded,
    }


def _temporal_candidates_for_bits(
    brightness_frames: list[list[int]],
    width: int,
    height: int,
    observed_bits: str,
) -> list[dict[str, Any]]:
    on_frames = [index for index, bit in enumerate(observed_bits) if bit == "1"]
    off_frames = [index for index, bit in enumerate(observed_bits) if bit == "0"]
    if not on_frames or not off_frames:
        return []
    window_radius = max(8, int(round(min(width, height) * TEMPORAL_WINDOW_RATIO)))
    peak_distance = max(window_radius + 1, int(round(min(width, height) * TEMPORAL_PEAK_DISTANCE_RATIO)))
    step = max(2, window_radius // 8)
    integral = _temporal_score_integral(
        brightness_frames,
        width,
        height,
        on_frames,
        off_frames,
        TEMPORAL_DIFF_THRESHOLD,
    )
    raw_peaks = []
    for y in range(window_radius, height - window_radius, step):
        y0 = y - window_radius
        y1 = y + window_radius
        for x in range(window_radius, width - window_radius, step):
            x0 = x - window_radius
            x1 = x + window_radius
            score = _integral_sum(integral, width, x0, y0, x1, y1)
            if score > 0:
                raw_peaks.append((float(score), x, y))
    raw_peaks.sort(reverse=True)

    candidates = []
    for score, x, y in raw_peaks:
        if any((x - item["pixel_x"]) ** 2 + (y - item["pixel_y"]) ** 2 < peak_distance ** 2 for item in candidates):
            continue
        candidates.append({
            "x": round(x / max(1, width - 1), 6),
            "y": round(y / max(1, height - 1), 6),
            "pixel_x": x,
            "pixel_y": y,
            "score": round(score / 1_000_000.0, 3),
            "window_radius": window_radius,
        })
        if len(candidates) >= TEMPORAL_CANDIDATE_COUNT:
            break
    return candidates


def _temporal_score_integral(
    brightness_frames: list[list[int]],
    width: int,
    height: int,
    on_frames: list[int],
    off_frames: list[int],
    threshold: int,
) -> list[int]:
    integral = [0] * ((width + 1) * (height + 1))
    on_count = len(on_frames)
    off_count = len(off_frames)
    for y in range(height):
        row_sum = 0
        current_row = (y + 1) * (width + 1)
        previous_row = y * (width + 1)
        pixel_row = y * width
        for x in range(width):
            index = pixel_row + x
            on_value = sum(brightness_frames[frame_index][index] for frame_index in on_frames) / on_count
            off_value = sum(brightness_frames[frame_index][index] for frame_index in off_frames) / off_count
            diff = on_value - off_value
            if diff > threshold:
                excess = int(diff - threshold)
                row_sum += excess * excess
            integral[current_row + x + 1] = integral[previous_row + x + 1] + row_sum
    return integral


def _integral_sum(integral: list[int], width: int, x0: int, y0: int, x1: int, y1: int) -> int:
    stride = width + 1
    return (
        integral[(y1 + 1) * stride + x1 + 1]
        - integral[y0 * stride + x1 + 1]
        - integral[(y1 + 1) * stride + x0]
        + integral[y0 * stride + x0]
    )


def _temporal_track(
    candidate: dict[str, Any],
    item: dict[str, Any],
    track_id: int,
    alignment_offset: int,
    frame_count: int,
) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "x": candidate["x"],
        "y": candidate["y"],
        "bits": item["bits"],
        "observed_bits": _unrotate_bits(str(item["bits"]), alignment_offset),
        "value": int(item["code"]),
        "frames_seen": str(item["bits"]).count("1"),
        "score": candidate["score"],
        "observations": _temporal_observations(candidate, item, alignment_offset, frame_count),
    }


def _temporal_extra_tracks(
    candidates: list[dict[str, Any]],
    item: dict[str, Any],
    alignment_offset: int,
    frame_count: int,
) -> list[dict[str, Any]]:
    return [
        {
            **_temporal_track(candidate, item, -1, alignment_offset, frame_count),
            "rejected_reason": "weaker temporal candidate for expected code",
        }
        for candidate in candidates
    ]


def _temporal_observations(
    candidate: dict[str, Any],
    item: dict[str, Any],
    alignment_offset: int,
    frame_count: int,
) -> list[dict[str, Any] | None]:
    observed_bits = _unrotate_bits(str(item["bits"]), alignment_offset)
    observations = []
    for index in range(frame_count):
        if observed_bits[index] != "1":
            observations.append(None)
            continue
        observations.append({
            "x": candidate["x"],
            "y": candidate["y"],
            "area": int(candidate["window_radius"]) ** 2,
        })
    return observations


def _unrotate_bits(bits: str, offset: int) -> str:
    if not bits:
        return bits
    shift = offset % len(bits)
    if shift == 0:
        return bits
    return bits[-shift:] + bits[:-shift]


def _temporal_proposal_score(proposal: dict[str, Any]) -> tuple[int, int, int, float, int, int, int]:
    metrics = proposal.get("metrics") or {}
    return (
        int(metrics.get("assigned") or 0),
        -int(metrics.get("ambiguous") or 0),
        -int(metrics.get("missing") or 0),
        float(proposal.get("temporal_score") or 0.0),
        -int(metrics.get("extra") or 0),
        -int(proposal.get("frame_window_start") or 0),
        -int(proposal.get("alignment_offset") or 0),
    )


def _best_code_map_proposal(decoded: dict[str, Any], code_to_item: dict[int, dict[str, Any]]) -> dict[str, Any]:
    frame_count = int(decoded.get("frame_count") or 0)
    rotations = range(frame_count) if frame_count > 1 else range(1)
    proposals = [
        _propose_layout_from_code_items(_rotate_decoded_bits(decoded, rotation), code_to_item, rotation)
        for rotation in rotations
    ]
    return max(proposals, key=_proposal_score)


def _propose_layout_from_code_items(
    decoded: dict[str, Any],
    code_to_item: dict[int, dict[str, Any]],
    alignment_offset: int = 0,
) -> dict[str, Any]:
    selected, ambiguous_codes, extra_tracks = _select_tracks_by_code(decoded, set(code_to_item))

    assignments = []
    for value, track in selected.items():
        item = code_to_item.get(value)
        assignments.append({
            "mac": item["mac"],
            "x": track["x"],
            "y": track["y"],
            "code": value,
            "bits": track["bits"],
            "expected_bits": item["bits"],
            "track_id": track["track_id"],
            "frames_seen": track["frames_seen"],
        })

    assigned_codes = {item["code"] for item in assignments}
    missing = [
        {
            "mac": item["mac"],
            "code": int(item["code"]),
            "bits": item["bits"],
            "reason": "not detected",
        }
        for item in code_to_item.values()
        if int(item["code"]) not in assigned_codes and int(item["code"]) not in ambiguous_codes
    ]
    ambiguous = [
        {
            "mac": code_to_item[code]["mac"],
            "code": code,
            "bits": code_to_item[code]["bits"],
            "reason": "multiple tracks decoded to this code",
        }
        for code in sorted(ambiguous_codes)
    ]
    return {
        "assignments": assignments,
        "missing": missing,
        "ambiguous": ambiguous,
        "extra_tracks": extra_tracks,
        "metrics": {
            "expected": len(code_to_item),
            "assigned": len(assignments),
            "missing": len(missing),
            "ambiguous": len(ambiguous),
            "extra": len(extra_tracks),
        },
        "alignment_offset": alignment_offset,
        "decoded": decoded,
    }


def _select_tracks_by_code(
    decoded: dict[str, Any],
    expected_codes: set[int],
) -> tuple[dict[int, dict[str, Any]], set[int], list[dict[str, Any]]]:
    by_code: dict[int, list[dict[str, Any]]] = {}
    extra_tracks = []
    for track in decoded.get("tracks") or []:
        value = int(track["value"])
        if value not in expected_codes:
            extra_tracks.append(track)
            continue
        by_code.setdefault(value, []).append(track)

    selected: dict[int, dict[str, Any]] = {}
    ambiguous: set[int] = set()
    for value, tracks in by_code.items():
        if len(tracks) == 1:
            selected[value] = tracks[0]
            continue
        ranked = sorted(tracks, key=_track_signal_score, reverse=True)
        best_score = _track_signal_score(ranked[0])
        second_score = _track_signal_score(ranked[1])
        if best_score >= max(second_score * 3.0, second_score + 1000.0):
            selected[value] = ranked[0]
            for rejected in ranked[1:]:
                extra = dict(rejected)
                extra["rejected_reason"] = "weaker duplicate of expected code"
                extra_tracks.append(extra)
        else:
            ambiguous.add(value)
    return selected, ambiguous, extra_tracks


def _track_signal_score(track: dict[str, Any]) -> float:
    areas = [
        float(item.get("area") or 0)
        for item in track.get("observations") or []
        if item is not None
    ]
    if not areas:
        return float(track.get("frames_seen") or 0)
    return sum(areas)


def _rotate_decoded_bits(decoded: dict[str, Any], offset: int) -> dict[str, Any]:
    if offset == 0:
        return decoded
    rotated_tracks = []
    for track in decoded.get("tracks") or []:
        bits = str(track.get("bits") or "")
        if not bits:
            rotated_tracks.append(track)
            continue
        shift = offset % len(bits)
        rotated_bits = bits[shift:] + bits[:shift]
        item = dict(track)
        item["observed_bits"] = bits
        item["bits"] = rotated_bits
        item["value"] = int(rotated_bits, 2)
        rotated_tracks.append(item)
    ambiguous = _duplicate_values(rotated_tracks)
    return {
        **decoded,
        "tracks": sorted(rotated_tracks, key=lambda item: (item["value"], item["y"], item["x"])),
        "metrics": {
            **(decoded.get("metrics") or {}),
            "track_count": len(rotated_tracks),
            "ambiguous_values": ambiguous,
            "alignment_offset": offset,
        },
    }


def _proposal_score(proposal: dict[str, Any]) -> tuple[int, int, int, int, int]:
    metrics = proposal.get("metrics") or {}
    return (
        int(metrics.get("assigned") or 0),
        -int(metrics.get("ambiguous") or 0),
        -int(metrics.get("missing") or 0),
        -int(metrics.get("extra") or 0),
        -int(proposal.get("alignment_offset") or 0),
    )


def calibration_code_plan(
    roster_macs: list[str],
    first_code: int = 1,
    bit_count: int | None = None,
    min_hamming_distance: int = 1,
) -> dict[str, Any]:
    if not roster_macs:
        raise CalibrationError("calibration code plan needs at least one roster MAC")
    if len(set(roster_macs)) != len(roster_macs):
        raise CalibrationError("calibration code plan roster MACs must be unique")
    if first_code < 1:
        raise CalibrationError("first_code must be at least 1")
    if min_hamming_distance < 1:
        raise CalibrationError("min_hamming_distance must be at least 1")
    needed_bits = max(1, (first_code + len(roster_macs) - 1).bit_length())
    if bit_count is None:
        bit_count = needed_bits
        while len(_hamming_code_values(len(roster_macs), first_code, bit_count, min_hamming_distance)) < len(roster_macs):
            bit_count += 1
    values = _hamming_code_values(len(roster_macs), first_code, bit_count, min_hamming_distance)
    if len(values) < len(roster_macs):
        raise CalibrationError("bit_count is too small for the roster and hamming distance")
    return {
        "first_code": first_code,
        "bit_count": bit_count,
        "frame_count": bit_count,
        "min_hamming_distance": min_hamming_distance,
        "codes": [
            {
                "mac": mac,
                "code": values[index],
                "bits": format(values[index], f"0{bit_count}b"),
            }
            for index, mac in enumerate(roster_macs)
        ],
    }


def render_synthetic_sequence(
    nodes: list[dict[str, Any]],
    width: int = 960,
    height: int = 720,
    first_code: int = 1,
    bit_count: int | None = None,
    blob_radius: int = 5,
    led_value: int = 255,
    jitter_px: float = 0.0,
    glare_count: int = 0,
    glare_value: int = 230,
    missing_frames: list[int] | None = None,
    perspective: float = 0.0,
    min_hamming_distance: int = 3,
) -> dict[str, Any]:
    if width < 40 or height < 40:
        raise CalibrationError("synthetic frame size must be at least 40x40")
    if blob_radius < 1 or blob_radius > min(width, height) // 4:
        raise CalibrationError("synthetic blob_radius is out of range")
    if led_value < 0 or led_value > 255 or glare_value < 0 or glare_value > 255:
        raise CalibrationError("synthetic brightness values must be between 0 and 255")
    if jitter_px < 0 or jitter_px > max(width, height):
        raise CalibrationError("synthetic jitter_px is out of range")
    if glare_count < 0 or glare_count > 500:
        raise CalibrationError("synthetic glare_count is out of range")
    if perspective < 0.0 or perspective > 0.45:
        raise CalibrationError("synthetic perspective must be between 0 and 0.45")
    missing_frame_set = set(missing_frames or [])
    clean_nodes = []
    for node in nodes:
        mac = str(node.get("mac") or "")
        if not mac:
            raise CalibrationError("synthetic nodes need MAC addresses")
        x = float(node.get("x"))
        y = float(node.get("y"))
        if x < 0.0 or x > 1.0 or y < 0.0 or y > 1.0:
            raise CalibrationError("synthetic node coordinates must be normalized")
        clean_nodes.append({"mac": mac, "x": x, "y": y})
    plan = calibration_code_plan(
        [node["mac"] for node in clean_nodes],
        first_code=first_code,
        bit_count=bit_count,
        min_hamming_distance=min_hamming_distance,
    )
    code_by_mac = {item["mac"]: item for item in plan["codes"]}
    frames = []
    for frame_index in range(plan["bit_count"]):
        image = Image.new("RGB", (width, height), (7, 9, 10))
        draw = ImageDraw.Draw(image)
        _draw_synthetic_background(draw, width, height)
        _draw_synthetic_glare(draw, width, height, frame_index, glare_count, glare_value)
        active = []
        if frame_index not in missing_frame_set:
            for node_index, node in enumerate(clean_nodes):
                code = code_by_mac[node["mac"]]
                if code["bits"][frame_index] != "1":
                    continue
                px, py = _synthetic_pixel(
                    node["x"],
                    node["y"],
                    width,
                    height,
                    perspective,
                    _deterministic_jitter(node_index, frame_index, jitter_px),
                )
                draw.ellipse(
                    (px - blob_radius, py - blob_radius, px + blob_radius, py + blob_radius),
                    fill=(led_value, max(0, led_value - 13), max(0, led_value - 41)),
                )
                active.append({"mac": node["mac"], "code": code["code"], "x": node["x"], "y": node["y"]})
        out = BytesIO()
        image.save(out, format="PNG")
        frames.append({
            "filename": f"synthetic-calibration-{frame_index + 1:02d}.png",
            "data": out.getvalue(),
            "active": active,
        })
    return {"plan": plan, "frames": frames}


def _hamming_code_values(count: int, first_code: int, bit_count: int, min_distance: int) -> list[int]:
    values: list[int] = []
    limit = 1 << bit_count
    for value in range(first_code, limit):
        if all(_hamming_distance(value, existing) >= min_distance for existing in values):
            values.append(value)
            if len(values) == count:
                break
    return values


def _hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _synthetic_pixel(
    x: float,
    y: float,
    width: int,
    height: int,
    perspective: float,
    jitter: tuple[float, float],
) -> tuple[int, int]:
    px = x
    py = y
    if perspective:
        center_x = px - 0.5
        center_y = py - 0.5
        scale = 1.0 - perspective * center_y
        px = 0.5 + center_x * scale
        py = 0.5 + center_y * (1.0 + perspective * 0.35)
    ix = int(round(px * (width - 1) + jitter[0]))
    iy = int(round(py * (height - 1) + jitter[1]))
    return max(0, min(width - 1, ix)), max(0, min(height - 1, iy))


def _deterministic_jitter(node_index: int, frame_index: int, jitter_px: float) -> tuple[float, float]:
    if jitter_px <= 0:
        return (0.0, 0.0)
    a = (node_index + 1) * 12.9898 + (frame_index + 1) * 78.233
    b = (node_index + 1) * 39.3467 + (frame_index + 1) * 11.135
    return ((math_sin_unit(a) * 2.0 - 1.0) * jitter_px, (math_sin_unit(b) * 2.0 - 1.0) * jitter_px)


def math_sin_unit(value: float) -> float:
    # Deterministic pseudo-random value in [0,1) without importing random state.
    raw = math.sin(value) * 43758.5453
    return raw - math.floor(raw)


def _draw_synthetic_glare(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    frame_index: int,
    glare_count: int,
    glare_value: int,
) -> None:
    for index in range(glare_count):
        x = int((math_sin_unit((index + 1) * 91.17 + frame_index * 3.11)) * (width - 1))
        y = int((math_sin_unit((index + 1) * 17.31 + frame_index * 5.23)) * (height - 1))
        r = 1 + (index % 3)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(glare_value, glare_value, glare_value))


def _draw_synthetic_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    step = max(32, min(width, height) // 10)
    for x in range(0, width, step):
        draw.line((x, 0, x, height), fill=(18, 22, 24))
    for y in range(0, height, step):
        draw.line((0, y, width, y), fill=(18, 22, 24))
    for index in range(7):
        cx = int((index + 1) * width / 8)
        cy = int((3 + (index * 5) % 11) * height / 16)
        r = max(8, min(width, height) // 42)
        shade = 28 + index * 3
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(shade, shade + 2, shade), width=2)


def _nearest_track(
    tracks: list[dict[str, Any]],
    matched_tracks: set[int],
    detection: dict[str, Any],
    max_distance: float,
) -> int | None:
    best_index = None
    best_distance = max_distance
    dx = float(detection["x"])
    dy = float(detection["y"])
    for index, track in enumerate(tracks):
        if index in matched_tracks or int(track["count"]) <= 0:
            continue
        tx = float(track["sum_x"]) / int(track["count"])
        ty = float(track["sum_y"]) / int(track["count"])
        distance = ((dx - tx) ** 2 + (dy - ty) ** 2) ** 0.5
        if distance <= best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _duplicate_values(tracks: list[dict[str, Any]]) -> list[int]:
    counts: dict[int, int] = {}
    for track in tracks:
        value = int(track["value"])
        counts[value] = counts.get(value, 0) + 1
    return sorted(value for value, count in counts.items() if count > 1)


def _flood_blob(active: bytearray, seen: bytearray, width: int, height: int, x0: int, y0: int) -> dict[str, Any]:
    stack = [(x0, y0)]
    area = 0
    sum_x = 0
    sum_y = 0
    min_x = max_x = x0
    min_y = max_y = y0
    while stack:
        x, y = stack.pop()
        if x < 0 or x >= width or y < 0 or y >= height:
            continue
        idx = y * width + x
        if seen[idx] or not active[idx]:
            continue
        seen[idx] = 1
        area += 1
        sum_x += x
        sum_y += y
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)
        stack.append((x + 1, y))
        stack.append((x - 1, y))
        stack.append((x, y + 1))
        stack.append((x, y - 1))
    return {
        "area": area,
        "sum_x": sum_x,
        "sum_y": sum_y,
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
    }


def _blob_result(blob: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    cx = blob["sum_x"] / blob["area"]
    cy = blob["sum_y"] / blob["area"]
    return {
        "x": round(cx / max(1, width - 1), 6),
        "y": round(cy / max(1, height - 1), 6),
        "pixel_x": round(cx, 3),
        "pixel_y": round(cy, 3),
        "area": blob["area"],
        "bbox": {
            "x0": blob["min_x"],
            "y0": blob["min_y"],
            "x1": blob["max_x"],
            "y1": blob["max_y"],
        },
    }
