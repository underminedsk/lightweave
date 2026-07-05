from control.mock_conductor import MockConductor


def test_snapshot_counts_attention_and_alive_nodes() -> None:
    conductor = MockConductor()

    snapshot = conductor.snapshot()

    assert snapshot["summary"]["alive"] == 9
    assert snapshot["summary"]["total"] == 60
    assert snapshot["summary"]["attention"] == 2
    assert snapshot["recipe"]["pattern"] == "Glow"


def test_assign_sets_position_and_clears_attention() -> None:
    conductor = MockConductor()
    mac = "8C:94:DF:57:7F:14"

    ack = conductor.assign(mac, 0.25, 0.75)
    lantern = next(item for item in conductor.lanterns() if item["mac"] == mac)

    assert ack["ok"] is True
    assert lantern["position"] == "Set"
    assert lantern["attention"] == "None"
    assert lantern["x"] == 0.25
    assert lantern["y"] == 0.75


def test_blackout_preserves_pattern_and_sets_brightness_zero() -> None:
    conductor = MockConductor()

    conductor.update_recipe("Sweep", 72, {"period": 8000})
    ack = conductor.blackout()

    assert ack["ok"] is True
    assert conductor.snapshot()["recipe"] == {
        "pattern": "Sweep",
        "brightness": 0,
        "params": {"period": 8000},
    }
