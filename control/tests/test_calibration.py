from __future__ import annotations

import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from control.app import create_app
from control.calibration import CalibrationStore, calibration_code_plan, propose_layout_from_tracks
from control.mock_conductor import MockConductor


FIXTURE_DIR = Path(__file__).with_name("fixtures")


def make_blob_png() -> bytes:
    image = Image.new("RGB", (40, 30), (3, 4, 5))
    pixels = image.load()
    for y in range(4, 8):
        for x in range(5, 9):
            pixels[x, y] = (255, 245, 230)
    for y in range(20, 24):
        for x in range(30, 34):
            pixels[x, y] = (240, 250, 255)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def make_sequence_png(active: set[str]) -> bytes:
    image = Image.new("RGB", (60, 40), (2, 3, 4))
    pixels = image.load()
    centers = {
        "a": (12, 10),
        "b": (45, 30),
    }
    for key in active:
        cx, cy = centers[key]
        for y in range(cy - 2, cy + 2):
            for x in range(cx - 2, cx + 2):
                pixels[x, y] = (255, 240, 220)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def make_video_like_sequence_png(active: set[str], index: int) -> bytes:
    image = Image.new("RGB", (540, 960), (8, 9, 10))
    pixels = image.load()
    centers = {
        "a": (244, 492),
        "b": (46, 638),
    }
    for key in active:
        cx, cy = centers[key]
        for y in range(cy - 18, cy + 18):
            for x in range(cx - 18, cx + 18):
                pixels[x, y] = (255, 238, 190)
    for speck in range(80):
        cx = (speck * 37 + index * 19) % 540
        cy = (speck * 83 + index * 23) % 960
        for y in range(max(0, cy - 1), min(960, cy + 2)):
            for x in range(max(0, cx - 1), min(540, cx + 2)):
                pixels[x, y] = (255, 255, 255)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def extract_fixture_frames(video: Path, out_dir: Path, count: int = 3) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required for MOV fixture extraction")
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "frame-%02d.png"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0",
            "-i",
            str(video),
            "-vf",
            "fps=1",
            "-frames:v",
            str(count),
            str(pattern),
        ],
        check=True,
    )
    return sorted(out_dir.glob("frame-*.png"))


def test_calibration_frame_upload_detects_bright_blobs(tmp_path) -> None:
    client = TestClient(
        create_app(
            MockConductor(),
            calibration_store=CalibrationStore(tmp_path),
        )
    )

    upload = client.put(
        "/api/calibration/frames?filename=anchor.png",
        content=make_blob_png(),
        headers={"content-type": "image/png"},
    )
    frame = upload.json()["frame"]
    detect = client.post(
        f"/api/calibration/frames/{frame['frame_id']}/detect",
        json={"threshold": 180, "min_area": 4},
    )
    listed = client.get("/api/calibration/frames").json()

    assert upload.status_code == 200
    assert frame["filename"] == "anchor.png"
    assert frame["width"] == 40
    assert frame["height"] == 30
    assert listed["frames"][0]["frame_id"] == frame["frame_id"]
    assert detect.status_code == 200
    body = detect.json()["detection"]
    assert body["metrics"]["count"] == 2
    assert {item["area"] for item in body["detections"]} == {16}
    xs = sorted(item["x"] for item in body["detections"])
    ys = sorted(item["y"] for item in body["detections"])
    assert xs[0] < 0.2
    assert xs[1] > 0.7
    assert ys[0] < 0.25
    assert ys[1] > 0.65


def test_calibration_rejects_non_image(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))

    response = client.put(
        "/api/calibration/frames?filename=bad.txt",
        content=b"not an image",
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "calibration image must be .jpg, .jpeg, .png, or .webp"


def test_calibration_detect_rejects_unknown_frame(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))

    response = client.post(
        "/api/calibration/frames/missing/detect",
        json={"threshold": 180, "min_area": 4},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown calibration frame"


def test_calibration_frame_image_endpoint_serves_uploaded_frame(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))
    upload = client.put(
        "/api/calibration/frames?filename=anchor.png",
        content=make_blob_png(),
        headers={"content-type": "image/png"},
    )
    frame = upload.json()["frame"]

    response = client.get(f"/api/calibration/frames/{frame['frame_id']}/image")

    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


def test_calibration_decode_tracks_presence_bits_across_frames(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))
    frame_ids = []
    for index, active in enumerate([{"a", "b"}, {"b"}, {"a"}]):
        response = client.put(
            f"/api/calibration/frames?filename=code-{index}.png",
            content=make_sequence_png(active),
            headers={"content-type": "image/png"},
        )
        frame_ids.append(response.json()["frame"]["frame_id"])

    response = client.post(
        "/api/calibration/decode",
        json={
            "frame_ids": frame_ids,
            "threshold": 180,
            "min_area": 4,
            "max_distance": 0.05,
        },
    )

    assert response.status_code == 200
    decoded = response.json()["decoded"]
    tracks = decoded["tracks"]
    assert decoded["metrics"]["track_count"] == 2
    assert {track["bits"] for track in tracks} == {"101", "110"}
    assert {track["value"] for track in tracks} == {5, 6}
    assert decoded["metrics"]["ambiguous_values"] == []


def test_calibration_proposes_layout_from_decoded_codes(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))
    frame_ids = []
    for index, active in enumerate([{"a", "b"}, {"b"}, {"a"}]):
        response = client.put(
            f"/api/calibration/frames?filename=proposal-{index}.png",
            content=make_sequence_png(active),
            headers={"content-type": "image/png"},
        )
        frame_ids.append(response.json()["frame"]["frame_id"])

    response = client.post(
        "/api/calibration/propose-layout",
        json={
            "frame_ids": frame_ids,
            "roster_macs": ["AA:00:00:00:00:05", "AA:00:00:00:00:06"],
            "first_code": 5,
            "threshold": 180,
            "min_area": 4,
            "max_distance": 0.05,
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["metrics"] == {
        "expected": 2,
        "assigned": 2,
        "missing": 0,
        "ambiguous": 0,
        "extra": proposal["metrics"]["extra"],
    }
    assignments = sorted(proposal["assignments"], key=lambda item: item["code"])
    assert [(item["mac"], item["code"], item["bits"]) for item in assignments] == [
        ("AA:00:00:00:00:05", 5, "101"),
        ("AA:00:00:00:00:06", 6, "110"),
    ]
    assert all(0.0 <= item["x"] <= 1.0 and 0.0 <= item["y"] <= 1.0 for item in assignments)


def test_video_frame_defaults_ignore_tiny_bright_specks(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))
    frame_ids = []
    for index, active in enumerate([{"b"}, {"b"}, {"a"}]):
        response = client.put(
            f"/api/calibration/frames?filename=video-calibration-{index + 1:02d}.png",
            content=make_video_like_sequence_png(active, index),
            headers={"content-type": "image/png"},
        )
        frame_ids.append(response.json()["frame"]["frame_id"])

    response = client.post(
        "/api/calibration/propose-layout",
        json={
            "frame_ids": frame_ids,
            "code_map": [
                {"mac": "AA:00:00:00:00:01", "code": 1, "bits": "001"},
                {"mac": "AA:00:00:00:00:02", "code": 6, "bits": "110"},
            ],
            "threshold": 180,
            "min_area": 4,
            "max_distance": 0.035,
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["metrics"]["expected"] == 2
    assert proposal["metrics"]["assigned"] == 2
    assert proposal["metrics"]["missing"] == 0
    assert proposal["metrics"]["ambiguous"] == 0
    assignments = sorted(proposal["assignments"], key=lambda item: item["mac"])
    assert [(item["mac"], item["bits"]) for item in assignments] == [
        ("AA:00:00:00:00:01", "001"),
        ("AA:00:00:00:00:02", "110"),
    ]
    assert proposal["decoded"]["metrics"]["track_count"] == 2


def test_video_proposal_accepts_extra_uploaded_frames_by_scanning_windows(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))
    frame_ids = []
    for index, active in enumerate([set(), {"b"}, {"b"}, {"a"}]):
        response = client.put(
            f"/api/calibration/frames?filename=video-calibration-{index + 1:02d}.png",
            content=make_video_like_sequence_png(active, index),
            headers={"content-type": "image/png"},
        )
        frame_ids.append(response.json()["frame"]["frame_id"])

    response = client.post(
        "/api/calibration/propose-layout",
        json={
            "frame_ids": frame_ids,
            "code_map": [
                {"mac": "AA:00:00:00:00:01", "code": 1, "bits": "001"},
                {"mac": "AA:00:00:00:00:02", "code": 6, "bits": "110"},
            ],
            "threshold": 180,
            "min_area": 4,
            "max_distance": 0.035,
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["frame_window_start"] == 1
    assert proposal["metrics"]["assigned"] == 2
    assert proposal["metrics"]["missing"] == 0
    assert proposal["metrics"]["ambiguous"] == 0


def test_real_two_node_video_with_extra_lights_uses_temporal_code_signal(tmp_path) -> None:
    frames = extract_fixture_frames(
        FIXTURE_DIR / "2_nodes_calibration_extra_lights.mov",
        tmp_path / "extra-light-frames",
        count=3,
    )
    store = CalibrationStore(tmp_path / "store")
    frame_ids = [
        store.add_image(f"video-calibration-{index + 1:02d}.png", frame.read_bytes())["frame_id"]
        for index, frame in enumerate(frames)
    ]

    proposal = store.propose_layout(
        frame_ids,
        [],
        threshold=180,
        min_area=4,
        max_distance=0.035,
        code_map=[
            {"mac": "8C:94:DF:8F:71:50", "code": 1, "bits": "001"},
            {"mac": "30:76:F5:93:67:3C", "code": 6, "bits": "110"},
        ],
    )

    assert proposal["metrics"]["assigned"] == 2
    assert proposal["metrics"]["missing"] == 0
    assert proposal["metrics"]["ambiguous"] == 0
    assert proposal["decoded"]["metrics"]["temporal"] is True
    assignments = {item["mac"]: item for item in proposal["assignments"]}
    upper = assignments["8C:94:DF:8F:71:50"]
    lower = assignments["30:76:F5:93:67:3C"]
    assert upper["bits"] == "001"
    assert lower["bits"] == "110"
    assert abs(upper["x"] - 0.69) < 0.04
    assert abs(upper["y"] - 0.34) < 0.04
    assert abs(lower["x"] - 0.48) < 0.04
    assert abs(lower["y"] - 0.65) < 0.04


def test_real_two_node_video_auto_aligns_and_ignores_extra_lights(tmp_path) -> None:
    frames = extract_fixture_frames(
        FIXTURE_DIR / "2_nodes_calibration.mov",
        tmp_path / "frames",
        count=3,
    )
    store = CalibrationStore(tmp_path / "store")
    frame_ids = [
        store.add_image(f"video-calibration-{index + 1:02d}.png", frame.read_bytes())["frame_id"]
        for index, frame in enumerate(frames)
    ]

    proposal = store.propose_layout(
        frame_ids,
        [],
        threshold=180,
        min_area=4,
        max_distance=0.035,
        code_map=[
            {"mac": "8C:94:DF:8F:71:50", "code": 1, "bits": "001"},
            {"mac": "30:76:F5:93:67:3C", "code": 6, "bits": "110"},
        ],
    )

    assert proposal["alignment_offset"] == 2
    assert proposal["metrics"]["assigned"] == 2
    assert proposal["metrics"]["missing"] == 0
    assert proposal["metrics"]["ambiguous"] == 0
    assert proposal["metrics"]["extra"] > 0
    assignments = sorted(proposal["assignments"], key=lambda item: item["mac"])
    assert [(item["mac"], item["bits"]) for item in assignments] == [
        ("30:76:F5:93:67:3C", "110"),
        ("8C:94:DF:8F:71:50", "001"),
    ]


def test_calibration_proposal_reports_missing_roster_codes(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))
    frame_ids = []
    for index, active in enumerate([{"a", "b"}, {"b"}, {"a"}]):
        response = client.put(
            f"/api/calibration/frames?filename=missing-{index}.png",
            content=make_sequence_png(active),
            headers={"content-type": "image/png"},
        )
        frame_ids.append(response.json()["frame"]["frame_id"])

    response = client.post(
        "/api/calibration/propose-layout",
        json={
            "frame_ids": frame_ids,
            "roster_macs": ["AA:00:00:00:00:05", "AA:00:00:00:00:06", "AA:00:00:00:00:07"],
            "first_code": 5,
            "threshold": 180,
            "min_area": 4,
            "max_distance": 0.05,
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["metrics"]["assigned"] == 2
    assert proposal["metrics"]["missing"] == 1
    assert proposal["missing"] == [
        {
            "mac": "AA:00:00:00:00:07",
            "code": 7,
            "reason": "not detected",
        }
    ]


def test_calibration_proposal_reports_duplicate_decoded_codes() -> None:
    decoded = {
        "tracks": [
            {"track_id": 0, "x": 0.2, "y": 0.3, "bits": "101", "value": 5, "frames_seen": 2},
            {"track_id": 1, "x": 0.7, "y": 0.6, "bits": "101", "value": 5, "frames_seen": 2},
            {"track_id": 2, "x": 0.4, "y": 0.5, "bits": "110", "value": 6, "frames_seen": 2},
        ],
        "metrics": {"track_count": 3, "ambiguous_values": [5]},
    }

    proposal = propose_layout_from_tracks(
        decoded,
        ["AA:00:00:00:00:05", "AA:00:00:00:00:06"],
        first_code=5,
    )

    assert proposal["assignments"] == [
        {
            "mac": "AA:00:00:00:00:06",
            "x": 0.4,
            "y": 0.5,
            "code": 6,
            "bits": "110",
            "track_id": 2,
            "frames_seen": 2,
        }
    ]
    assert proposal["ambiguous"] == [
        {
            "mac": "AA:00:00:00:00:05",
            "code": 5,
            "reason": "multiple tracks decoded to this code",
        }
    ]
    assert proposal["metrics"]["ambiguous"] == 1


def test_calibration_code_plan_sizes_bits_for_roster() -> None:
    plan = calibration_code_plan(
        ["AA:00:00:00:00:01", "AA:00:00:00:00:02", "AA:00:00:00:00:03"],
        first_code=1,
    )

    assert plan["bit_count"] == 2
    assert [(item["code"], item["bits"]) for item in plan["codes"]] == [
        (1, "01"),
        (2, "10"),
        (3, "11"),
    ]


def test_synthetic_calibration_sequence_recovers_known_layout(tmp_path) -> None:
    store = CalibrationStore(tmp_path)
    nodes = [
        {"mac": "AA:00:00:00:00:01", "x": 0.18, "y": 0.22},
        {"mac": "AA:00:00:00:00:02", "x": 0.64, "y": 0.35},
        {"mac": "AA:00:00:00:00:03", "x": 0.42, "y": 0.78},
        {"mac": "AA:00:00:00:00:04", "x": 0.82, "y": 0.69},
    ]

    simulation = store.add_synthetic_sequence(
        nodes,
        width=320,
        height=240,
        first_code=1,
        bit_count=3,
        blob_radius=4,
        min_hamming_distance=1,
        threshold=180,
        min_area=12,
        max_distance=0.03,
    )

    assert len(simulation["frames"]) == 3
    assert simulation["plan"]["codes"] == [
        {"mac": "AA:00:00:00:00:01", "code": 1, "bits": "001"},
        {"mac": "AA:00:00:00:00:02", "code": 2, "bits": "010"},
        {"mac": "AA:00:00:00:00:03", "code": 3, "bits": "011"},
        {"mac": "AA:00:00:00:00:04", "code": 4, "bits": "100"},
    ]
    proposal = simulation["proposal"]
    assert proposal["metrics"] == {
        "expected": 4,
        "assigned": 4,
        "missing": 0,
        "ambiguous": 0,
        "extra": 0,
    }
    assignments = {item["mac"]: item for item in proposal["assignments"]}
    for node in nodes:
        assigned = assignments[node["mac"]]
        assert abs(assigned["x"] - node["x"]) < 0.01
        assert abs(assigned["y"] - node["y"]) < 0.01


def test_synthetic_calibration_api_uses_positioned_alive_roster(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))

    response = client.post(
        "/api/calibration/simulate",
        json={
            "width": 360,
            "height": 260,
            "first_code": 1,
            "blob_radius": 4,
            "threshold": 180,
            "min_area": 12,
            "max_distance": 0.03,
        },
    )

    assert response.status_code == 200
    simulation = response.json()["simulation"]
    assert len(simulation["frames"]) == simulation["plan"]["bit_count"]
    assert simulation["plan"]["min_hamming_distance"] == 3
    assert simulation["proposal"]["metrics"] == {
        "expected": 8,
        "assigned": 8,
        "missing": 0,
        "ambiguous": 0,
        "extra": 0,
    }
    assert len(client.get("/api/calibration/frames").json()["frames"]) == simulation["plan"]["bit_count"]


def test_calibration_code_plan_api_uses_alive_roster(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))

    response = client.post(
        "/api/calibration/code-plan",
        json={"first_code": 1, "min_hamming_distance": 3},
    )

    assert response.status_code == 200
    plan = response.json()["plan"]
    assert len(plan["codes"]) == 9
    assert plan["min_hamming_distance"] == 3
    assert all(item["code"] > 0 for item in plan["codes"])


def test_calibration_propose_layout_accepts_explicit_code_map(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), calibration_store=CalibrationStore(tmp_path)))
    frame_ids = []
    for index, active in enumerate([{"a"}, {"b"}, {"a"}, {"b"}]):
        response = client.put(
            f"/api/calibration/frames?filename=mapped-{index}.png",
            content=make_sequence_png(active),
            headers={"content-type": "image/png"},
        )
        frame_ids.append(response.json()["frame"]["frame_id"])

    response = client.post(
        "/api/calibration/propose-layout",
        json={
            "frame_ids": frame_ids,
            "code_map": [
                {"mac": "AA:00:00:00:00:05", "code": 10, "bits": "1010"},
                {"mac": "AA:00:00:00:00:06", "code": 5, "bits": "0101"},
            ],
            "threshold": 180,
            "min_area": 4,
            "max_distance": 0.05,
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["metrics"]["assigned"] == 2
    assignments = sorted(proposal["assignments"], key=lambda item: item["mac"])
    assert [(item["mac"], item["code"], item["expected_bits"]) for item in assignments] == [
        ("AA:00:00:00:00:05", 10, "1010"),
        ("AA:00:00:00:00:06", 5, "0101"),
    ]


def test_synthetic_calibration_missing_frame_does_not_alias_to_wrong_mac(tmp_path) -> None:
    store = CalibrationStore(tmp_path)
    nodes = [
        {"mac": "AA:00:00:00:00:01", "x": 0.18, "y": 0.22},
        {"mac": "AA:00:00:00:00:02", "x": 0.64, "y": 0.35},
        {"mac": "AA:00:00:00:00:03", "x": 0.42, "y": 0.78},
        {"mac": "AA:00:00:00:00:04", "x": 0.82, "y": 0.69},
    ]

    simulation = store.add_synthetic_sequence(
        nodes,
        width=320,
        height=240,
        first_code=1,
        bit_count=5,
        blob_radius=4,
        min_hamming_distance=3,
        missing_frames=[0],
        threshold=180,
        min_area=12,
        max_distance=0.03,
    )

    proposal = simulation["proposal"]
    assigned_macs = {item["mac"] for item in proposal["assignments"]}
    missing_macs = {item["mac"] for item in proposal["missing"]}
    assert assigned_macs.isdisjoint(missing_macs)
    assert proposal["metrics"]["assigned"] < len(nodes)
    assert proposal["metrics"]["missing"] > 0
    assert proposal["metrics"]["extra"] > 0


def test_synthetic_calibration_tolerates_jitter_glare_and_perspective(tmp_path) -> None:
    store = CalibrationStore(tmp_path)
    nodes = [
        {"mac": "AA:00:00:00:00:01", "x": 0.16, "y": 0.18},
        {"mac": "AA:00:00:00:00:02", "x": 0.68, "y": 0.27},
        {"mac": "AA:00:00:00:00:03", "x": 0.35, "y": 0.74},
    ]

    simulation = store.add_synthetic_sequence(
        nodes,
        width=420,
        height=300,
        first_code=1,
        blob_radius=5,
        led_value=235,
        jitter_px=1.2,
        glare_count=12,
        glare_value=150,
        perspective=0.08,
        threshold=180,
        min_area=18,
        max_distance=0.035,
    )

    assert simulation["proposal"]["metrics"] == {
        "expected": 3,
        "assigned": 3,
        "missing": 0,
        "ambiguous": 0,
        "extra": 0,
    }
