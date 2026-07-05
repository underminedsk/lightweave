from control.mock_conductor import MockConductor


def test_snapshot_counts_attention_and_alive_nodes() -> None:
    conductor = MockConductor()

    snapshot = conductor.snapshot()

    assert snapshot["summary"]["alive"] == 9
    assert snapshot["summary"]["total"] == 60
    assert snapshot["summary"]["attention"] == 2
    assert snapshot["pattern"]["pattern"] == "Glow"


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

    conductor.update_pattern("Sweep", 72, {"period": 8000})
    ack = conductor.blackout()

    assert ack["ok"] is True
    assert conductor.snapshot()["pattern"] == {
        "pattern": "Sweep",
        "brightness": 0,
        "params": {"period": 8000},
    }


def test_replace_moves_label_and_position_to_unpositioned_spare() -> None:
    conductor = MockConductor()
    old_mac = "A0:B7:65:11:44:91"
    new_mac = "8C:94:DF:57:7F:14"

    ack = conductor.replace(old_mac, new_mac)
    lanterns = conductor.lanterns()
    old = next(item for item in lanterns if item["mac"] == old_mac)
    new = next(item for item in lanterns if item["mac"] == new_mac)

    assert ack["ok"] is True
    assert old["position"] == "Missing"
    assert old["label"] == "#18 retired"
    assert new["position"] == "Set"
    assert new["label"] == "#18"
    assert new["x"] == 0.66
    assert new["y"] == 0.69


def test_replace_rejects_positioned_replacement() -> None:
    conductor = MockConductor()

    ack = conductor.replace("A0:B7:65:11:44:91", "30:76:F5:93:67:3C")

    assert ack == {"ok": False, "error": "replacement lantern already has a position"}


def test_replace_rejects_unpositioned_old_lantern() -> None:
    conductor = MockConductor()

    ack = conductor.replace("8C:94:DF:57:7F:14", "A0:B7:65:11:44:91")

    assert ack == {"ok": False, "error": "old lantern has no position to replace"}
