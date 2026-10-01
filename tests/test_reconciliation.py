from conftest import l3


def types(client):
    return sorted({f["type"] for f in client.get("/reconciliation/report").json()["findings"]})


def setup_service(order):
    return order({"action": "add", "service": l3()})["orderItem"][0]["service"]["id"]


def test_detects_attribute_drift(client, order, stack):
    setup_service(order)
    cfg = dict(stack.controller.actual("r1")["interfaces/ethernet-1/1"], mtu=1500)
    client.post("/lab/drift", json={"device": "r1", "key": "interfaces/ethernet-1/1", "value": cfg})
    assert types(client) == ["attribute_drift"]


def test_detects_missing_object(client, order):
    setup_service(order)
    client.post(
        "/lab/drift", json={"device": "r1", "key": "interfaces/ethernet-1/1", "value": None}
    )
    assert "missing_object" in types(client)


def test_detects_orphaned_config(client):
    client.post(
        "/lab/drift",
        json={
            "device": "r2",
            "key": "interfaces/ethernet-1/2",
            "value": {
                "enabled": True,
                "mtu": 1500,
                "ipv4": {"address": "1.1.1.1", "prefix_length": 32},
            },
        },
    )
    assert types(client) == ["orphaned_config"]


def test_detects_stale_reference_and_orphaned_reservation(client, order, stack):
    sid = setup_service(order)
    stack.inventory.resources["r1:ethernet-1/1"]["assigned_to"] = "svc-ghost"
    assert set(types(client)) == {"reservation_mismatch", "orphaned_reservation"}
    stack.inventory.resources["r1:ethernet-1/1"]["assigned_to"] = sid
    del stack.inventory.resources["r1:ethernet-1/1"]
    assert "stale_reference" in types(client)


def test_detects_controller_drift_and_assurance_gap(client, order, stack):
    sid = setup_service(order)
    stack.controller._view["r1"].clear()
    stack.assurance.unenroll(sid)
    assert set(types(client)) == {"controller_drift", "assurance_gap"}
