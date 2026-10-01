"""Lifecycle, integrity, assurance and observability scenarios. Each scenario takes a
``LabClient``, resets the target itself, and raises ``AssertionError`` on failure."""

from __future__ import annotations

from collections.abc import Callable

from .client import ALM, LabClient

Scenario = Callable[[LabClient], None]
SCENARIOS: dict[str, Scenario] = {}


def scenario(fn: Scenario) -> Scenario:
    SCENARIOS[fn.__name__] = fn
    return fn


def _sid(order: dict) -> str:
    return order["orderItem"][0]["service"]["id"]


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _clean(c: LabClient) -> None:
    rep = c.reconcile()
    _expect(rep["summary"]["clean"], f"reconciliation not clean: {rep['findings']}")


@scenario
def lifecycle_full(c: LabClient) -> None:
    c.reset()
    order = c.order(c.add_l3("r1", "ethernet-1/1", "10.0.0.1"))
    _expect(order["state"] == "completed", f"add failed: {order}")
    sid = _sid(order)
    _expect(c.service(sid)["state"] == "active", "service not active after add")
    _clean(c)
    for action, expected in (("suspend", "inactive"), ("resume", "active")):
        o = c.order(c.action(action, sid))
        _expect(o["state"] == "completed", f"{action} failed: {o}")
        _expect(c.service(sid)["state"] == expected, f"state after {action} != {expected}")
        _clean(c)
    o = c.order(c.action("modify", sid, ipv4="10.0.0.3", mtu=1500))
    _expect(o["state"] == "completed", f"modify failed: {o}")
    chars = {x["name"]: x["value"] for x in c.service(sid)["serviceCharacteristic"]}
    _expect(
        chars["ipv4"] == "10.0.0.3" and chars["mtu"] == 1500, "modify not reflected in inventory"
    )
    _clean(c)
    o = c.order(c.action("delete", sid))
    _expect(o["state"] == "completed", f"delete failed: {o}")
    _expect(c.service(sid)["state"] == "terminated", "service not terminated")
    _clean(c)


@scenario
def order_idempotency(c: LabClient) -> None:
    c.reset()
    item = c.add_l3("r2", "ethernet-1/1", "10.1.0.1")
    first = c.order(item, order_id="ord-idem")
    second = c.order(item, order_id="ord-idem")
    _expect(first == second, "resubmitted order returned a different result")
    _expect(
        len(c.json("GET", "/tmf-api/serviceInventory/v4/service")) == 1, "duplicate service created"
    )


@scenario
def reservation_conflict(c: LabClient) -> None:
    c.reset()
    c.order(c.add_l3("r1", "ethernet-1/2", "10.0.1.1"))
    o = c.order(c.add_l3("r1", "ethernet-1/2", "10.0.1.9"))
    _expect(o["state"] == "failed", "second order on a reserved interface must fail")
    _expect(o["orderItem"][0]["error"]["code"] == "conflict", "expected conflict error")
    _clean(c)


@scenario
def rollback_on_device_failure(c: LabClient) -> None:
    c.reset()
    c.json("POST", "/lab/faults", json={"device": "r3", "mode": "permanent", "count": 1})
    o = c.order(c.add_l3("r3", "ethernet-1/1", "10.3.0.1"))
    _expect(o["state"] == "failed", "order should fail on injected commit fault")
    _expect(c.json("GET", "/tmf-api/serviceInventory/v4/service") == [], "service record leaked")
    res = c.json("GET", "/tmf-api/resourceInventoryManagement/v4/resource/r3:ethernet-1/1")
    _expect(res["assignedTo"] is None, "reservation not released by compensation")
    _clean(c)


@scenario
def transient_failure_retry(c: LabClient) -> None:
    c.reset()
    c.json("POST", "/lab/faults", json={"device": "r1", "mode": "transient", "count": 2})
    o = c.order(c.add_l3("r1", "ethernet-1/3", "10.0.3.1"))
    _expect(o["state"] == "completed", f"transient faults should be retried: {o}")
    _clean(c)


@scenario
def drift_detection(c: LabClient) -> None:
    c.reset()
    c.order(c.add_l3("r1", "ethernet-1/4", "10.0.4.1"))
    c.json(
        "POST", "/lab/drift", json={"device": "r1", "key": "interfaces/ethernet-1/4", "value": None}
    )
    c.json(
        "POST",
        "/lab/drift",
        json={
            "device": "r2",
            "key": "interfaces/ethernet-1/4",
            "value": {
                "enabled": True,
                "mtu": 1500,
                "ipv4": {"address": "9.9.9.9", "prefix_length": 32},
            },
        },
    )
    kinds = {f["type"] for f in c.reconcile()["findings"]}
    _expect({"missing_object", "orphaned_config"} <= kinds, f"drift not detected: {kinds}")


@scenario
def assurance_service_health(c: LabClient) -> None:
    c.reset()
    sid = _sid(c.order(c.add_l3("r2", "ethernet-1/3", "10.2.3.1")))
    health = lambda: c.json("GET", f"/assurance/service/{sid}/health")["health"]  # noqa: E731
    _expect(health() == "healthy", "new service should be healthy")
    alarm = c.json(
        "POST", ALM, expect=201, json={"source": "r2:ethernet-1/3", "severity": "critical"}
    )
    _expect(health() == "down", "critical alarm should take service down")
    c.json("PATCH", f"{ALM}/{alarm['id']}/clear")
    _expect(health() == "healthy", "clearing the alarm should restore health")


@scenario
def observability_signals(c: LabClient) -> None:
    c.reset()
    o = c.order(c.add_l3("r1", "ethernet-1/1", "10.0.0.1"))
    events = c.json("GET", "/events", params={"topic": "workflow.step", "key": o["id"]})
    done = [e["payload"]["step"] for e in events if e["payload"]["status"] == "completed"]
    _expect("activate" in done and "set_state" in done, f"step events missing: {done}")
    text = c.request("GET", "/metrics").text
    metrics = {}
    for line in text.strip().splitlines():
        name, _, value = line.rpartition(" ")
        metrics[name] = float(value)  # raises if unparsable
    _expect(metrics.get("oss_lab_orders_completed") == 1.0, f"order metric wrong: {metrics}")
    _expect(metrics.get('oss_lab_services{state="active"}') == 1.0, "service gauge missing")
