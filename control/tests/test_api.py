from fastapi.testclient import TestClient

from control.app import create_app
from control.mock_conductor import MockConductor


def test_state_endpoint_returns_mock_state() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.get("/api/state")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["alive"] == 9
    assert body["conductor"]["sync"] == "locked"


def test_identify_unknown_lantern_is_404() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.post("/api/lanterns/00:00:00:00:00:00/identify")

    assert response.status_code == 404


def test_recipe_update_round_trips_to_state() -> None:
    client = TestClient(create_app(MockConductor()))

    response = client.post(
        "/api/show/recipe",
        json={"pattern": "Sweep", "brightness": 64, "params": {"period": 8000}},
    )
    state = client.get("/api/state").json()

    assert response.status_code == 200
    assert state["recipe"]["pattern"] == "Sweep"
    assert state["recipe"]["brightness"] == 64
    assert state["recipe"]["params"] == {"period": 8000}


def test_assign_endpoint_updates_lantern_position() -> None:
    client = TestClient(create_app(MockConductor()))
    mac = "8C:94:DF:57:7F:14"

    response = client.post(f"/api/lanterns/{mac}/assign", json={"x": 0.2, "y": 0.3})
    lanterns = client.get("/api/lanterns").json()
    lantern = next(item for item in lanterns if item["mac"] == mac)

    assert response.status_code == 200
    assert lantern["position"] == "Set"
    assert lantern["attention"] == "None"
