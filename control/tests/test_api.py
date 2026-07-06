from fastapi.testclient import TestClient

from control.adapters import SerialProtocolError
from control.app import create_app
from control.mock_conductor import Lantern, MockConductor
from control.ota_store import OtaArtifactStore
from control.pattern_store import PatternStore


class DownConductor(MockConductor):
    def snapshot(self) -> dict:
        raise SerialProtocolError("timeout waiting for state ack")


class RejectingPatternConductor(MockConductor):
    def update_pattern(self, pattern: str, brightness: int, params: dict) -> dict:
        return {"ok": False, "error": "bad pattern"}


class DroppingOtaChunkConductor(MockConductor):
    def __init__(self) -> None:
        super().__init__()
        self.dropped = False

    def ota_chunk(self, offset: int, data: bytes) -> dict:
        if offset > 0 and not self.dropped:
            self.dropped = True
            raise SerialProtocolError("timeout waiting for ota_chunk ack")
        return super().ota_chunk(offset, data)


class NackingOtaChunkConductor(MockConductor):
    def __init__(self) -> None:
        super().__init__()
        self.nacked = False

    def ota_chunk(self, offset: int, data: bytes) -> dict:
        if offset > 0 and not self.nacked:
            self.nacked = True
            return {"ok": False, "error": "ota chunk offset mismatch"}
        return super().ota_chunk(offset, data)


class LengthMismatchOtaChunkConductor(MockConductor):
    def __init__(self) -> None:
        super().__init__()
        self.nacked = False

    def ota_chunk(self, offset: int, data: bytes) -> dict:
        if offset > 0 and not self.nacked:
            self.nacked = True
            return {"ok": False, "error": "ota chunk length mismatch"}
        return super().ota_chunk(offset, data)


class AdvancedThenNackingOtaChunkConductor(MockConductor):
    def __init__(self) -> None:
        super().__init__()
        self.nacked = False

    def ota_chunk(self, offset: int, data: bytes) -> dict:
        if offset > 0 and not self.nacked:
            self.nacked = True
            ack = super().ota_chunk(offset, data)
            assert ack["ok"] is True
            return {"ok": False, "error": "ota chunk offset mismatch"}
        return super().ota_chunk(offset, data)


class PartialAdvancedNackingOtaChunkConductor(MockConductor):
    def __init__(self) -> None:
        super().__init__()
        self.nacked = False
        self.partial_written = 0

    def ota_chunk(self, offset: int, data: bytes) -> dict:
        if offset > 0 and not self.nacked:
            self.nacked = True
            self._ota_write.extend(data[:17])
            self.partial_written = len(self._ota_write)
            return {"ok": False, "error": "ota chunk offset mismatch"}
        return super().ota_chunk(offset, data)


class ProgressFailedOtaConductor(MockConductor):
    def __init__(self) -> None:
        super().__init__()
        self.ended = False

    def ota_progress(self) -> dict:
        progress = super().ota_progress()
        if progress.get("written", 0) >= 64 * 128 and progress.get("nodes"):
            node = progress["nodes"][0]
            node.update({"phase": "failed", "error": "flash write failed"})
            self._ota_nodes[node["mac"]] = node
        return progress

    def ota_end(self) -> dict:
        self.ended = True
        return super().ota_end()


class PartialOtaStatusConductor(MockConductor):
    def ota_progress(self) -> dict:
        progress = super().ota_progress()
        progress["nodes"] = progress.get("nodes", [])[:10]
        return progress

    def ota_end(self) -> dict:
        ack = super().ota_end()
        if ack.get("ok"):
            ack = dict(ack)
            ack["nodes"] = ack.get("nodes", [])[:10]
        return ack


class NoOtaStatusConductor(MockConductor):
    def ota_progress(self) -> dict:
        progress = super().ota_progress()
        progress["nodes"] = []
        return progress

    def ota_end(self) -> dict:
        ack = super().ota_end()
        if ack.get("ok"):
            ack = dict(ack)
            ack["nodes"] = []
            self._ota_nodes = {}
        return ack


class OneNodeOnlyOtaStatusConductor(MockConductor):
    def ota_end(self) -> dict:
        ack = super().ota_end()
        if ack.get("ok"):
            first = self._lanterns[0]
            first.firmware = {
                "version": "0.3.0-mismatch",
                "proto": 6,
                "build_id": 0xED2E397F,
                "build_label": "ed2e397f",
                "dirty": True,
            }
            first.attention = "Firmware mismatch"
            ack = dict(ack)
            ack["nodes"] = [ack["nodes"][1]]
        return ack


class LegacySnapshotConductor(MockConductor):
    def snapshot(self) -> dict:
        state = super().snapshot()
        state.pop("recovery", None)
        return state


class LegacyMixedFirmwareOtaConductor(MockConductor):
    def snapshot(self) -> dict:
        state = super().snapshot()
        if state["summary"]["firmware"]["consistent"] is False and state["ota"]["enabled"]:
            state["ota"]["ready"] = False
            state["ota"]["ready_count"] = state["summary"]["firmware"]["matching"]
            state["ota"]["blocked"] = ["firmware mismatch"]
        return state


def make_placed_conductor(count: int) -> MockConductor:
    conductor = MockConductor()
    conductor._lanterns = [
        Lantern(
            mac=f"02:00:00:00:{index // 256:02X}:{index % 256:02X}",
            label=f"#{index + 1}",
            status="alive",
            last_seen_s=index % 17,
            x=(index % 10) / 9,
            y=(index // 10) / 5,
        )
        for index in range(count)
    ]
    return conductor


def test_state_endpoint_returns_mock_state() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get("/api/state")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["alive"] == 8
    assert body["summary"]["total"] == 9
    assert body["conductor"]["sync"] == "locked"
    assert body["conductor"]["firmware"]["version"] == "0.3.0"
    assert body["conductor"]["firmware"]["proto"] == 6
    assert body["summary"]["firmware"]["consistent"] is True
    assert body["power"]["light_sleep_check_s"] == 4
    assert body["recovery"]["status"] == "missing_nodes"


def test_state_endpoint_enriches_legacy_snapshot_with_recovery() -> None:
    conductor = LegacySnapshotConductor()
    conductor._lanterns[0].firmware = {
        "version": "0.2.0",
        "proto": 6,
        "build_id": 0xDEADBEEF,
        "build_label": "deadbeef",
        "dirty": False,
    }
    client = TestClient(create_app(conductor))

    recovery = client.get("/api/state").json()["recovery"]

    assert recovery["status"] == "mixed_firmware"
    assert recovery["mismatched"][0]["mac"] == "8C:94:DF:8F:71:50"


def test_identify_unknown_lantern_is_404() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.post("/api/lanterns/00:00:00:00:00:00/identify")

    assert response.status_code == 404


def test_state_endpoint_reports_serial_timeout_as_503() -> None:
    client = TestClient(create_app(DownConductor()))

    response = client.get("/api/state")

    assert response.status_code == 503
    assert response.json()["detail"] == "timeout waiting for state ack"


def test_websocket_reports_serial_timeout_as_error_event() -> None:
    client = TestClient(create_app(DownConductor()))

    with client.websocket_connect("/ws") as ws:
        event = ws.receive_json()

    assert event["type"] == "error"
    assert event["message"] == "timeout waiting for state ack"


def test_pattern_update_round_trips_to_state() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.post(
        "/api/show/pattern",
        json={"pattern": "Sweep", "brightness": 64, "params": {"period": 8000}},
    )
    state = client.get("/api/state").json()

    assert response.status_code == 200
    assert state["pattern"]["pattern"] == "Sweep"
    assert state["pattern"]["brightness"] == 64
    assert state["pattern"]["params"] == {"period": 8000}


def test_pattern_update_rejected_by_conductor_is_400() -> None:
    client = TestClient(create_app(RejectingPatternConductor()))

    response = client.post(
        "/api/show/pattern",
        json={"pattern": "Bad", "brightness": 64, "params": {}},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "bad pattern"


def test_preview_endpoint_returns_png_for_positioned_lanterns() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get(
        "/preview",
        params={
            "pattern": "Sweep",
            "brightness": 64,
            "period": 8000,
            "wavelength": 300,
            "t": 1200,
            "width": 180,
            "height": 120,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(response.content) > 100


def test_preview_endpoint_accepts_json_params() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get(
        "/preview",
        params={
            "pattern": "Glow",
            "brightness": 48,
            "params": '{"hue": 40, "saturation": 100}',
            "width": 120,
            "height": 120,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_preview_json_endpoint_returns_lantern_samples_and_metrics() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get(
        "/preview.json",
        params={"pattern": "Sweep", "brightness": 64, "period": 8000, "spatial": 300, "t": 1200},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["pattern"] == "Sweep"
    assert body["brightness"] == 64
    assert body["t"] == 1200
    assert body["metrics"]["count"] == 9
    assert 0 <= body["metrics"]["lit_count"] <= body["metrics"]["count"]
    assert 0 <= body["metrics"]["contrast"] <= 1
    first = body["lanterns"][0]
    assert first["mac"] == "8C:94:DF:8F:71:50"
    assert len(first["rgbw"]) == 4
    assert len(first["rgb"]) == 3
    assert isinstance(first["luma"], float)


def test_preview_frames_endpoint_returns_sequence_metrics() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get(
        "/preview/frames.json",
        params={
            "pattern": "Sweep",
            "brightness": 64,
            "period": 8000,
            "spatial": 300,
            "duration_ms": 2000,
            "fps": 2,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["frame_count"] == 4
    assert [frame["t"] for frame in body["frames"]] == [0, 500, 1000, 1500]
    assert body["metrics"]["max_lit_count"] <= 9
    assert body["metrics"]["avg_luma_mean"] >= 0
    assert body["metrics"]["max_contrast"] >= body["metrics"]["min_contrast"]


def test_review_endpoint_scores_candidate_pattern() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get(
        "/review",
        params={"pattern": "Glow", "brightness": 48, "hue": 40, "duration_ms": 2000, "fps": 2},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["rating"] in {"strong", "usable"}
    assert body["score"] >= 70
    assert body["metrics"]["avg_luma_mean"] > 0
    assert body["issues"] == []


def test_review_endpoint_rejects_blackout_candidate() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get(
        "/review",
        params={"pattern": "Glow", "brightness": 0, "duration_ms": 2000, "fps": 2},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is False
    assert body["rating"] == "reject"
    assert "blackout" in {issue["code"] for issue in body["issues"]}


def test_preview_endpoint_rejects_unknown_pattern() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get("/preview", params={"pattern": "Bad"})

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown pattern: Bad"


def test_preview_endpoint_reports_serial_timeout_as_503() -> None:
    client = TestClient(create_app(DownConductor()))

    response = client.get("/preview", params={"pattern": "Glow"})

    assert response.status_code == 503
    assert response.json()["detail"] == "timeout waiting for state ack"


def test_pattern_library_crud_round_trip(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))

    created = client.post(
        "/api/patterns",
        json={
            "name": "Amber Glow",
            "pattern": "Glow",
            "brightness": 48,
            "params": {"hue": 40, "saturation": 100},
        },
    )
    pattern_id = created.json()["pattern"]["id"]
    updated = client.put(
        f"/api/patterns/{pattern_id}",
        json={
            "name": "Slow Sweep",
            "pattern": "Sweep",
            "brightness": 64,
            "params": {"period": 8000, "wavelength": 300},
        },
    )
    fetched = client.get(f"/api/patterns/{pattern_id}")
    listed = client.get("/api/patterns")
    deleted = client.delete(f"/api/patterns/{pattern_id}")
    missing = client.get(f"/api/patterns/{pattern_id}")

    assert created.status_code == 200
    assert pattern_id == "amber-glow"
    assert updated.status_code == 200
    assert updated.json()["pattern"]["pattern"] == "Sweep"
    assert fetched.json()["pattern"]["name"] == "Slow Sweep"
    assert [item["id"] for item in listed.json()["patterns"]] == [pattern_id]
    assert deleted.status_code == 200
    assert missing.status_code == 404


def test_pattern_library_corrupt_brightness_returns_500(tmp_path) -> None:
    (tmp_path / "patterns.json").write_text(
        '{"bad":{"id":"bad","name":"Bad","pattern":"Glow","params":{}}}',
        encoding="utf-8",
    )
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))

    response = client.get("/api/patterns")

    assert response.status_code == 500
    assert response.json()["detail"] == "brightness must be between 0 and 192"


def test_pattern_library_preview_returns_png(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))
    created = client.post(
        "/api/patterns",
        json={
            "name": "Preview Me",
            "pattern": "Palette Drift",
            "brightness": 64,
            "params": {"period": 8000, "spatial": 300},
        },
    )
    pattern_id = created.json()["pattern"]["id"]

    response = client.get(f"/api/patterns/{pattern_id}/preview", params={"width": 120, "height": 120})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_pattern_library_preview_json_returns_saved_pattern_data(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))
    created = client.post(
        "/api/patterns",
        json={
            "name": "Preview Json",
            "pattern": "Glow",
            "brightness": 48,
            "params": {"hue": 40, "saturation": 100},
        },
    )
    pattern_id = created.json()["pattern"]["id"]

    response = client.get(f"/api/patterns/{pattern_id}/preview.json", params={"t": 1000})
    body = response.json()

    assert response.status_code == 200
    assert body["pattern"] == "Glow"
    assert body["params"] == {"hue": 40, "saturation": 100}
    assert body["metrics"]["count"] == 9
    assert body["metrics"]["lit_count"] == 9


def test_pattern_library_preview_frames_json_returns_saved_sequence(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))
    created = client.post(
        "/api/patterns",
        json={
            "name": "Frame Json",
            "pattern": "Sweep",
            "brightness": 64,
            "params": {"period": 8000, "spatial": 300},
        },
    )
    pattern_id = created.json()["pattern"]["id"]

    response = client.get(
        f"/api/patterns/{pattern_id}/preview/frames.json",
        params={"duration_ms": 2000, "fps": 2},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["pattern"] == "Sweep"
    assert body["frame_count"] == 4
    assert len(body["frames"]) == 4


def test_pattern_library_review_returns_saved_pattern_score(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))
    created = client.post(
        "/api/patterns",
        json={
            "name": "Review Me",
            "pattern": "Glow",
            "brightness": 48,
            "params": {"hue": 40, "saturation": 100},
        },
    )
    pattern_id = created.json()["pattern"]["id"]

    response = client.get(f"/api/patterns/{pattern_id}/review", params={"duration_ms": 2000, "fps": 2})
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["score"] >= 70
    assert body["recommendations"]


def test_pattern_library_broadcast_updates_live_pattern(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))
    created = client.post(
        "/api/patterns",
        json={
            "name": "Broadcast Sweep",
            "pattern": "Sweep",
            "brightness": 64,
            "params": {"period": 8000, "spatial": 300},
        },
    )
    pattern_id = created.json()["pattern"]["id"]

    response = client.post(f"/api/patterns/{pattern_id}/broadcast")
    state = client.get("/api/state").json()

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["pattern"]["id"] == pattern_id
    assert state["pattern"]["pattern"] == "Sweep"
    assert state["pattern"]["brightness"] == 64
    assert state["pattern"]["params"] == {"period": 8000, "spatial": 300}


def test_pattern_library_broadcast_unknown_is_404(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))

    response = client.post("/api/patterns/nope/broadcast")

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown pattern"


def test_pattern_library_broadcast_rejected_by_conductor_is_400(tmp_path) -> None:
    client = TestClient(create_app(RejectingPatternConductor(), pattern_store=PatternStore(tmp_path)))
    created = client.post(
        "/api/patterns",
        json={"name": "Bad Live Pattern", "pattern": "Bad", "brightness": 64, "params": {}},
    )
    pattern_id = created.json()["pattern"]["id"]

    response = client.post(f"/api/patterns/{pattern_id}/broadcast")

    assert response.status_code == 400
    assert response.json()["detail"] == "bad pattern"


def test_pattern_library_update_unknown_is_404(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), pattern_store=PatternStore(tmp_path)))

    response = client.put(
        "/api/patterns/nope",
        json={"name": "Nope", "pattern": "Glow", "brightness": 48, "params": {}},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown pattern"


def test_power_policy_update_round_trips_to_state() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.post(
        "/api/operations/power-policy",
        json={
            "light_sleep_check_s": 30,
            "deep_sleep_check_min": 60,
            "led_on_start_min": 19 * 60,
            "led_on_end_min": 5 * 60,
            "schedule_enabled": True,
            "force_awake": False,
            "current_min": 12 * 60,
            "current_epoch_s": 1_720_123_456,
        },
    )
    state = client.get("/api/state").json()

    assert response.status_code == 200
    assert state["power"]["light_sleep_check_s"] == 30
    assert state["power"]["deep_sleep_check_min"] == 60
    assert state["power"]["schedule_enabled"] is True
    assert state["power"]["force_awake"] is False
    assert state["power"]["current_epoch_s"] == 1_720_123_456
    assert state["power"]["leds_on"] is False


def test_ota_mode_update_round_trips_to_state() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.post("/api/operations/ota-mode", json={"enabled": True})
    state = client.get("/api/state").json()

    assert response.status_code == 200
    assert state["ota"]["mode"] == "maintenance"
    assert state["ota"]["enabled"] is True
    assert state["ota"]["expected"] == 9
    assert state["ota"]["missing"] == 1
    assert state["ota"]["ready"] is False
    assert "missing placed lanterns" in state["ota"]["blocked"]


def test_ota_artifact_upload_stages_firmware_metadata(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), ota_store=OtaArtifactStore(tmp_path)))

    response = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=b"\xe9" + b"\x00" * 4095,
        headers={"content-type": "application/octet-stream"},
    )
    current = client.get("/api/operations/ota-artifact").json()

    assert response.status_code == 200
    artifact = response.json()["artifact"]
    assert artifact["filename"] == "firmware.bin"
    assert artifact["size"] == 4096
    assert artifact["chunks"] == 32
    assert len(artifact["sha256"]) == 64
    assert current["artifact"]["sha256"] == artifact["sha256"]


def test_ota_artifact_store_reload_preserves_current_artifact(tmp_path) -> None:
    firmware = b"\xe9" + b"\x00" * 4095
    staged = OtaArtifactStore(tmp_path).stage("firmware.bin", firmware)

    reloaded = OtaArtifactStore(tmp_path)

    assert reloaded.current() == staged
    assert reloaded.artifact() is not None
    assert reloaded.artifact().path.read_bytes() == firmware


def test_ota_artifact_upload_rejects_non_bin(tmp_path) -> None:
    client = TestClient(create_app(MockConductor(), ota_store=OtaArtifactStore(tmp_path)))

    response = client.put(
        "/api/operations/ota-artifact?filename=firmware.txt",
        content=b"not firmware",
        headers={"content-type": "application/octet-stream"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "firmware artifact must be a .bin file"


def test_ota_install_requires_staged_artifact(tmp_path) -> None:
    conductor = MockConductor()
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 400
    assert response.json()["detail"] == "no firmware staged"


def test_ota_install_blocks_when_placed_lantern_is_missing(tmp_path) -> None:
    conductor = MockConductor()
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=b"\xe9" + bytes(range(255)),
        headers={"content-type": "application/octet-stream"},
    )

    state = client.get("/api/state").json()
    response = client.post("/api/operations/ota-install")

    assert state["recovery"]["status"] == "missing_nodes"
    assert state["recovery"]["missing"] == [
        {"mac": "A0:B7:65:11:44:91", "label": "#18", "reason": "not seen"}
    ]
    assert state["ota"]["enabled"] is True
    assert state["ota"]["ready"] is False
    assert "missing placed lanterns" in state["ota"]["blocked"]
    assert response.status_code == 400
    assert response.json()["detail"] == "OTA not ready: missing placed lanterns"


def test_ota_install_streams_staged_artifact_when_ready(tmp_path) -> None:
    conductor = MockConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 20
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    assert response.json()["message"] == "ota install complete; rebooting"
    assert conductor.ota_installed_crc32 == stage["artifact"]["crc32"]
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["running"] is False
    assert install["complete"] is True
    assert install["chunks_sent"] == stage["artifact"]["chunks"]
    assert install["bytes_sent"] == stage["artifact"]["size"]
    assert install["elapsed_s"] >= 0
    assert install["bytes_per_s"] >= 0
    assert install["eta_s"] == 0
    assert {node["phase"] for node in install["nodes"]} == {"complete"}
    ota = client.get("/api/state").json()["ota"]
    assert {node["phase"] for node in ota["nodes"]} == {"complete"}
    assert all(node["offset"] == stage["artifact"]["size"] for node in ota["nodes"])


def test_ota_install_scales_to_60_expected_nodes(tmp_path) -> None:
    conductor = make_placed_conductor(60)
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 40
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    state = client.get("/api/state").json()
    response = client.post("/api/operations/ota-install")

    assert state["summary"]["total"] == 60
    assert state["ota"]["ready"] is True
    assert state["ota"]["ready_count"] == 60
    assert response.status_code == 200
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is True
    assert install["chunks_sent"] == stage["artifact"]["chunks"]
    assert len(install["nodes"]) == 60
    assert {node["phase"] for node in install["nodes"]} == {"complete"}
    assert {node["offset"] for node in install["nodes"]} == {stage["artifact"]["size"]}


def test_ota_install_60_nodes_falls_back_to_post_reboot_verification_when_status_is_partial(tmp_path) -> None:
    conductor = PartialOtaStatusConductor()
    conductor._lanterns = make_placed_conductor(60)._lanterns
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 40
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is True
    assert len(install["nodes"]) == 60
    assert {node["source"] for node in install["nodes"]} == {"post_reboot_state"}
    assert {node["offset"] for node in install["nodes"]} == {stage["artifact"]["size"]}


def test_ota_install_retries_transient_chunk_timeout(tmp_path) -> None:
    conductor = DroppingOtaChunkConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    assert conductor.dropped is True
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is True
    assert install["chunks_sent"] == stage["artifact"]["chunks"]
    assert install["bytes_sent"] == stage["artifact"]["size"]
    assert install["last_retry"]["error"] == "timeout waiting for ota_chunk ack"


def test_ota_install_stops_on_polled_node_failure(tmp_path) -> None:
    conductor = ProgressFailedOtaConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 40
    client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    )

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 400
    assert response.json()["detail"] == "ota node failure"
    assert conductor.ended is False
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is False
    assert install["error"] == "ota node failure"
    assert any(node["phase"] == "failed" for node in install["nodes"])


def test_ota_install_retries_retryable_chunk_nack(tmp_path) -> None:
    conductor = NackingOtaChunkConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    assert conductor.nacked is True
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is True
    assert install["chunks_sent"] == stage["artifact"]["chunks"]
    assert install["last_retry"]["error"] == "ota chunk offset mismatch"


def test_ota_install_retries_chunk_length_mismatch_without_advancing(tmp_path) -> None:
    conductor = LengthMismatchOtaChunkConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    assert conductor.nacked is True
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is True
    assert install["chunks_sent"] == stage["artifact"]["chunks"]
    assert install["bytes_sent"] == stage["artifact"]["size"]
    assert install["last_retry"]["error"] == "ota chunk length mismatch"


def test_ota_install_resyncs_after_already_advanced_chunk_nack(tmp_path) -> None:
    conductor = AdvancedThenNackingOtaChunkConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    assert conductor.nacked is True
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is True
    assert install["chunks_sent"] == stage["artifact"]["chunks"]
    assert install["bytes_sent"] == stage["artifact"]["size"]
    assert install["last_retry"]["error"] == "resynced after ota chunk offset mismatch"


def test_ota_install_rejects_partial_offset_resync(tmp_path) -> None:
    conductor = PartialAdvancedNackingOtaChunkConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    )

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 503
    assert response.json()["detail"] == f"ota write offset is not chunk-aligned: {conductor.partial_written}"
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is False
    assert install["error"] == response.json()["detail"]


def test_ota_install_infers_nodes_from_post_reboot_state_when_status_missing(tmp_path) -> None:
    conductor = NoOtaStatusConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    stage = client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    ).json()

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is True
    assert {node["source"] for node in install["nodes"]} == {"post_reboot_state"}
    assert all(node["offset"] == stage["artifact"]["size"] for node in install["nodes"])


def test_ota_install_fails_when_not_all_expected_nodes_verify(tmp_path) -> None:
    conductor = OneNodeOnlyOtaStatusConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    )

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 503
    assert response.json()["detail"] == "ota post-reboot verification failed"
    install = client.get("/api/operations/ota-install").json()["install"]
    assert install["complete"] is False
    assert install["error"] == "ota post-reboot verification failed"
    failed = [node for node in install["nodes"] if node["phase"] == "failed"]
    assert {node["mac"] for node in failed} == {
        "8C:94:DF:8F:71:50",
        "A0:B7:65:11:40:77",
        "A0:B7:65:11:42:09",
        "A0:B7:65:11:42:14",
        "A0:B7:65:11:42:15",
        "A0:B7:65:11:44:91",
        "A0:B7:65:11:42:21",
        "A0:B7:65:11:42:24",
    }
    assert {node["error"] for node in failed} == {"post-reboot verification missing"}
    assert {node["source"] for node in failed} == {"post_reboot_verification"}


def test_ota_install_allows_mixed_firmware_recovery_with_legacy_ready_blocker(tmp_path) -> None:
    conductor = LegacyMixedFirmwareOtaConductor()
    missing = next(item for item in conductor._lanterns if item.mac == "A0:B7:65:11:44:91")
    missing.status = "alive"
    conductor._lanterns[0].firmware = {
        "version": "0.3.0-mismatch",
        "proto": 6,
        "build_id": 0x44D028FD,
        "build_label": "44d028fd",
        "dirty": False,
    }
    conductor.set_ota_mode(True)
    client = TestClient(create_app(conductor, ota_store=OtaArtifactStore(tmp_path)))
    firmware = b"\xe9" + bytes(range(255)) * 3
    client.put(
        "/api/operations/ota-artifact?filename=firmware.bin",
        content=firmware,
        headers={"content-type": "application/octet-stream"},
    )

    state = client.get("/api/state").json()
    assert state["recovery"]["status"] == "mixed_firmware"
    assert state["ota"]["ready"] is False
    assert state["ota"]["blocked"] == ["firmware mismatch"]

    response = client.post("/api/operations/ota-install")

    assert response.status_code == 200
    assert response.json()["message"] == "ota install complete; rebooting"


def test_assign_endpoint_updates_lantern_position() -> None:
    client = TestClient(create_app(MockConductor()))
    mac = "8C:94:DF:57:7F:14"

    response = client.post(f"/api/lanterns/{mac}/assign", json={"x": 0.2, "y": 0.3})
    lanterns = client.get("/api/lanterns").json()
    lantern = next(item for item in lanterns if item["mac"] == mac)

    assert response.status_code == 200
    assert lantern["position"] == "Set"
    assert lantern["attention"] == "None"


def test_replace_endpoint_moves_position_to_spare() -> None:
    client = TestClient(create_app(MockConductor()))
    old_mac = "A0:B7:65:11:44:91"
    new_mac = "8C:94:DF:57:7F:14"

    response = client.post("/api/lanterns/replace", json={"old_mac": old_mac, "new_mac": new_mac})
    lanterns = client.get("/api/lanterns").json()
    old = next(item for item in lanterns if item["mac"] == old_mac)
    new = next(item for item in lanterns if item["mac"] == new_mac)

    assert response.status_code == 200
    assert response.json()["new_mac"] == new_mac
    assert old["position"] == "Missing"
    assert new["position"] == "Set"
    assert new["label"] == "#18"
