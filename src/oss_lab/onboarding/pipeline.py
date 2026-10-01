"""Onboarding pipeline: every package must pass the full gate before it is 'ready'.
Packages are exercised in a scratch ``Stack`` so the gate never touches shared state."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..core.activation import Activation
from ..core.catalog import ServiceSpecification
from ..core.stack import Stack


class GateFailure(Exception):
    pass


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise GateFailure(msg)


def _run(steps: list[tuple[str, Callable[[], None]]]) -> dict[str, Any]:
    results, ready = [], True
    for name, fn in steps:
        if not ready:
            results.append({"step": name, "status": "skipped", "detail": None})
            continue
        try:
            fn()
            results.append({"step": name, "status": "pass", "detail": None})
        except Exception as exc:
            ready = False
            results.append(
                {"step": name, "status": "fail", "detail": f"{type(exc).__name__}: {exc}"}
            )
    return {"ready": ready, "steps": results}


def _order(stack: Stack, action: str, service: dict[str, Any]) -> dict[str, Any]:
    order = stack.orchestrator.submit({"orderItem": [{"action": action, "service": service}]})
    _check(order["state"] == "completed", f"{action} failed: {order['orderItem'][0]['error']}")
    return order


def _svc(spec_id: str, chars: dict[str, Any]) -> dict[str, Any]:
    return {
        "serviceSpecification": {"id": spec_id},
        "serviceCharacteristic": [{"name": k, "value": v} for k, v in chars.items()],
    }


def onboard_device_type(pkg: dict[str, Any]) -> dict[str, Any]:
    stack = Stack(seed=True)
    dev, acc = f"onboard-{pkg['name']}", pkg["acceptance"]
    state: dict[str, Any] = {}

    def schema() -> None:
        for key in ("name", "os", "interfaces", "acceptance"):
            _check(key in pkg, f"missing '{key}'")
        _check(
            acc["interface"] in pkg["interfaces"], "acceptance interface not in package interfaces"
        )

    def inventory_model() -> None:
        stack.inventory.add_device(dev, pkg["interfaces"], os=pkg["os"])
        _check(f"{dev}:{acc['interface']}" in stack.inventory.resources, "interface not modelled")

    def controller_adapter() -> None:
        stack.controller.register_device(dev, pkg["interfaces"], os=pkg["os"])
        stack.controller.device(dev).commit({})  # session/commit path works
        _check(stack.controller.actual(dev) is not None, "cannot read device config")

    def activation() -> None:
        chars = {"device": dev, "interface": acc["interface"], "ipv4": acc["ipv4"]}
        state["sid"] = _order(stack, "add", _svc(acc.get("service", "l3-interface"), chars))[
            "orderItem"
        ][0]["service"]["id"]
        cfg = stack.controller.actual(dev)[f"interfaces/{acc['interface']}"]
        _check(cfg["ipv4"]["address"] == acc["ipv4"], "rendered config not applied")

    def assurance() -> None:
        _check(
            stack.assurance.health(state["sid"])["health"] == "healthy",
            "service not healthy in assurance",
        )

    def teardown_and_reconcile() -> None:
        _check(stack.reconcile()["summary"]["clean"], "reconciliation not clean after activation")
        _order(stack, "delete", {"id": state["sid"]})
        _check(stack.reconcile()["summary"]["clean"], "reconciliation not clean after teardown")

    return _run([
        ("schema_validation", schema), ("inventory_model", inventory_model),
        ("controller_adapter_test", controller_adapter), ("activation_test", activation),
        ("assurance_enrollment", assurance), ("reconciliation_gate", teardown_and_reconcile),
    ])  # fmt: skip


def onboard_service_model(pkg: dict[str, Any]) -> dict[str, Any]:
    stack = Stack(seed=True)
    acc = pkg["acceptance"]
    box: dict[str, Any] = {}

    def schema() -> None:
        try:
            box["spec"] = ServiceSpecification(**pkg["spec"])
        except ValidationError as exc:
            raise GateFailure(f"invalid service specification: {exc.errors()[0]['msg']}") from exc
        stack.catalog.add(box["spec"])

    def render() -> None:
        chars = stack.catalog.validate_characteristics(
            box["spec"],
            acc["characteristics"] | {"device": acc["device"], "interface": acc["interface"]},
        )
        key, value = Activation.render(box["spec"], chars, enabled=True)
        _check(key.startswith("interfaces/"), f"unexpected config key '{key}'")
        _check(isinstance(value, dict) and value, "template rendered empty config")

    def lifecycle() -> None:
        chars = {"device": acc["device"], "interface": acc["interface"], **acc["characteristics"]}
        sid = _order(stack, "add", _svc(box["spec"].id, chars))["orderItem"][0]["service"]["id"]
        box["sid"] = sid
        for action in ("suspend", "resume"):
            _order(stack, action, {"id": sid})
            _check(stack.reconcile()["summary"]["clean"], f"reconciliation dirty after {action}")
        if "modify" in acc:
            _order(
                stack,
                "modify",
                {
                    "id": sid,
                    "serviceCharacteristic": [
                        {"name": k, "value": v} for k, v in acc["modify"].items()
                    ],
                },
            )
            _check(stack.reconcile()["summary"]["clean"], "reconciliation dirty after modify")
        _order(stack, "delete", {"id": sid})
        _check(stack.reconcile()["summary"]["clean"], "reconciliation dirty after delete")

    def assurance_mapping() -> None:
        sid = _order(
            stack,
            "add",
            _svc(
                box["spec"].id,
                {"device": acc["device"], "interface": acc["interface"], **acc["characteristics"]},
            ),
        )["orderItem"][0]["service"]["id"]
        _check(stack.assurance.health(sid)["health"] == "healthy", "not enrolled in assurance")
        stack.assurance.raise_alarm(f"{acc['device']}:{acc['interface']}", "critical")
        _check(stack.assurance.health(sid)["health"] == "down", "alarm does not map to service")

    return _run([
        ("schema_validation", schema), ("template_render", render),
        ("lifecycle_tests", lifecycle), ("assurance_mapping", assurance_mapping),
    ])  # fmt: skip


HANDLERS = {"device-type": onboard_device_type, "service-model": onboard_service_model}


def onboard(path: Path) -> dict[str, Any]:
    pkg = yaml.safe_load(Path(path).read_text())
    kind = pkg.get("kind")
    if kind not in HANDLERS:
        return {
            "ready": False,
            "steps": [
                {
                    "step": "schema_validation",
                    "status": "fail",
                    "detail": f"unknown package kind {kind!r}",
                }
            ],
        }
    return {"package": str(path), "kind": kind, **HANDLERS[kind](pkg)}


def discover(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.yaml") if p.is_file())
