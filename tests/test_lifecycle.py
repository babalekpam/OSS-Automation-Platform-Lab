from conftest import SVC, l3


def add(svc=None):
    return {"action": "add", "service": svc or l3()}


def test_add_activates_and_is_consistent(client, order, stack):
    o = order(add())
    assert o["state"] == "completed"
    sid = o["orderItem"][0]["service"]["id"]
    assert client.get(f"{SVC}/{sid}").json()["state"] == "active"
    cfg = stack.controller.actual("r1")["interfaces/ethernet-1/1"]
    assert cfg["enabled"] is True and cfg["ipv4"]["address"] == "10.0.0.1"
    assert client.get("/reconciliation/report").json()["summary"]["clean"]
    assert client.get(f"/assurance/service/{sid}/health").json()["health"] == "healthy"


def test_full_lifecycle(client, order, stack):
    sid = order(add())["orderItem"][0]["service"]["id"]
    mod = order({"action": "modify", "service": {"id": sid, "serviceCharacteristic": [
        {"name": "ipv4", "value": "10.0.0.3"}, {"name": "mtu", "value": 1500}]}})
    assert mod["state"] == "completed"
    assert stack.controller.actual("r1")["interfaces/ethernet-1/1"]["mtu"] == 1500

    order({"action": "suspend", "service": {"id": sid}})
    assert client.get(f"{SVC}/{sid}").json()["state"] == "inactive"
    assert stack.controller.actual("r1")["interfaces/ethernet-1/1"]["enabled"] is False
    assert client.get("/reconciliation/report").json()["summary"]["clean"]

    order({"action": "resume", "service": {"id": sid}})
    assert stack.controller.actual("r1")["interfaces/ethernet-1/1"]["enabled"] is True

    order({"action": "delete", "service": {"id": sid}})
    assert client.get(f"{SVC}/{sid}").json()["state"] == "terminated"
    assert "interfaces/ethernet-1/1" not in stack.controller.actual("r1")
    assert stack.inventory.get_resource("r1:ethernet-1/1")["assigned_to"] is None
    assert client.get("/reconciliation/report").json()["summary"]["clean"]


def test_order_is_idempotent(order, stack):
    first = order(add(), order_id="ord-x")
    again = order(add(), order_id="ord-x")
    assert first == again and len(stack.inventory.services) == 1


def test_resource_conflict_fails_without_side_effects(order, stack):
    order(add())
    o = order(add(l3(ip="10.0.0.9")))
    assert o["state"] == "failed" and o["orderItem"][0]["error"]["code"] == "conflict"
    assert len(stack.inventory.services) == 1


def test_permanent_device_failure_compensates(client, order, stack):
    client.post("/lab/faults", json={"device": "r1", "mode": "permanent"})
    o = order(add())
    assert o["state"] == "failed" and o["orderItem"][0]["error"]["code"] == "device_error"
    assert stack.inventory.services == {}
    assert stack.inventory.get_resource("r1:ethernet-1/1")["assigned_to"] is None
    assert client.get("/reconciliation/report").json()["summary"]["clean"]
    statuses = {e.payload["status"] for e in stack.bus.events("workflow.step")}
    assert "failed" in statuses and "compensated" in statuses


def test_transient_failure_is_retried(client, order, stack):
    client.post("/lab/faults", json={"device": "r1", "mode": "transient", "count": 2})
    o = order(add())
    assert o["state"] == "completed"
    assert stack.orchestrator.metrics["step_retries"] == 2


def test_transient_failure_exhausts_retries(client, order):
    client.post("/lab/faults", json={"device": "r1", "mode": "transient", "count": 5})
    assert order(add())["state"] == "failed"


def test_partial_order(order):
    o = order(add(l3(iface="ethernet-1/1")), add(l3(iface="nope", ip="10.0.0.5")))
    assert o["state"] == "partial"
    assert [i["state"] for i in o["orderItem"]] == ["completed", "failed"]


def test_validation_errors(order):
    bad_ip = order(add(l3(ip="999.1.1.1")))
    assert bad_ip["orderItem"][0]["error"]["code"] == "validation_failed"
    missing = order({"action": "add", "service": {"serviceSpecification": {"id": "l3-interface"}}})
    assert missing["state"] == "failed"
    unknown_spec = order({"action": "add", "service": {"serviceSpecification": {"id": "x"}}})
    assert unknown_spec["orderItem"][0]["error"]["code"] == "not_found"


def test_immutable_characteristic_rejected(order):
    sid = order(add())["orderItem"][0]["service"]["id"]
    o = order({"action": "modify", "service": {"id": sid, "serviceCharacteristic": [
        {"name": "interface", "value": "ethernet-1/2"}]}})
    assert o["orderItem"][0]["error"]["code"] == "validation_failed"


def test_terminated_service_cannot_change(order):
    sid = order(add())["orderItem"][0]["service"]["id"]
    order({"action": "delete", "service": {"id": sid}})
    o = order({"action": "suspend", "service": {"id": sid}})
    assert o["orderItem"][0]["error"]["code"] == "conflict"


def test_events_trace_the_order(client, order):
    o = order(add())
    steps = [e["payload"]["step"] for e in client.get("/events", params={
        "topic": "workflow.step", "key": o["id"]}).json() if e["payload"]["status"] == "completed"]
    assert steps == ["create_service", "reserve_resources", "activate", "enroll_assurance", "set_state"]


def test_alarms_drive_service_health(client, order):
    sid = order(add())["orderItem"][0]["service"]["id"]
    alarm = client.post("/tmf-api/alarmManagement/v4/alarm", json={
        "source": "r1:ethernet-1/1", "severity": "critical", "text": "LOS"}).json()
    assert client.get(f"/assurance/service/{sid}/health").json()["health"] == "down"
    client.patch(f"/tmf-api/alarmManagement/v4/alarm/{alarm['id']}/clear")
    assert client.get(f"/assurance/service/{sid}/health").json()["health"] == "healthy"
    client.post("/tmf-api/alarmManagement/v4/alarm", json={"source": "r1", "severity": "major"})
    assert client.get(f"/assurance/service/{sid}/health").json()["health"] == "degraded"
