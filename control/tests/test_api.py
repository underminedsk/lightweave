from fastapi.testclient import TestClient

from control.adapters import SerialProtocolError
from control.app import create_app
from control.mock_conductor import MockConductor


class DownConductor(MockConductor):
    def snapshot(self) -> dict:
        raise SerialProtocolError("timeout waiting for state ack")


class RejectingPatternConductor(MockConductor):
    def update_pattern(self, pattern: str, brightness: int, params: dict) -> dict:
        return {"ok": False, "error": "bad pattern"}


def test_state_endpoint_returns_mock_state() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get("/api/state")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["alive"] == 8
    assert body["summary"]["total"] == 9
    assert body["conductor"]["sync"] == "locked"
    assert body["conductor"]["firmware"]["version"] == "0.2.0"
    assert body["conductor"]["firmware"]["proto"] == 5
    assert body["summary"]["firmware"]["consistent"] is True
    assert body["power"]["light_sleep_check_s"] == 4


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
        },
    )
    state = client.get("/api/state").json()

    assert response.status_code == 200
    assert state["power"]["light_sleep_check_s"] == 30
    assert state["power"]["deep_sleep_check_min"] == 60
    assert state["power"]["schedule_enabled"] is True
    assert state["power"]["force_awake"] is False
    assert state["power"]["leds_on"] is False


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
