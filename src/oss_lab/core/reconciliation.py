"""Three-way reconciliation: intended (inventory + catalog), controller view, and
actual device state. Produces a data-integrity report."""

from __future__ import annotations

import time
from typing import Any

from .activation import Activation
from .assurance import Assurance
from .catalog import Catalog
from .controller import MANAGED_PREFIX, Controller
from .inventory import Inventory


def _finding(kind: str, severity: str, detail: str, **ref: Any) -> dict[str, Any]:
    return {"type": kind, "severity": severity, "detail": detail, **ref}


def reconcile(
    catalog: Catalog,
    inventory: Inventory,
    controller: Controller,
    assurance: Assurance | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    claimed: set[tuple[str, str]] = set()
    live = inventory.live_services()

    for svc in live:
        sid, chars = svc["id"], svc["characteristics"]
        device = chars.get("device")
        try:
            spec = catalog.get(svc["spec_id"])
            key, intended = Activation.render(spec, chars, enabled=svc["state"] == "active")
            actual_cfg, view_cfg = controller.actual(device), controller.view(device)
        except Exception as exc:
            findings.append(_finding("unreconcilable", "high", str(exc), service=sid))
            continue
        claimed.add((device, key))
        actual, view = actual_cfg.get(key), view_cfg.get(key)
        if actual is None:
            findings.append(
                _finding("missing_object", "high", f"{key} absent on {device}", service=sid, device=device, key=key)
            )
        elif actual != intended:
            drift = sorted(k for k in set(actual) | set(intended) if actual.get(k) != intended.get(k))
            findings.append(
                _finding("attribute_drift", "medium", f"{key} differs from intent in {drift}", service=sid, device=device, key=key)
            )
        if view != intended:
            findings.append(
                _finding("controller_drift", "medium", f"controller view of {key} differs from intent", service=sid, device=device, key=key)
            )
        for rid in svc["resource_ids"]:
            res = inventory.resources.get(rid)
            if res is None:
                findings.append(_finding("stale_reference", "high", f"{rid} not in inventory", service=sid))
            elif res["assigned_to"] != sid:
                findings.append(
                    _finding("reservation_mismatch", "medium", f"{rid} assigned to {res['assigned_to']}", service=sid)
                )
        if assurance is not None and sid not in assurance.enrolled:
            findings.append(_finding("assurance_gap", "low", "live service not enrolled in assurance", service=sid))

    for dev_id in controller.devices:
        for key in controller.actual(dev_id):
            if key.startswith(MANAGED_PREFIX) and (dev_id, key) not in claimed:
                findings.append(
                    _finding("orphaned_config", "high", f"{key} on {dev_id} has no live service", device=dev_id, key=key)
                )
    for res in inventory.resources.values():
        owner = res["assigned_to"]
        if owner and (owner not in inventory.services or inventory.services[owner]["state"] == "terminated"):
            findings.append(
                _finding("orphaned_reservation", "medium", f"{res['id']} reserved by missing/terminated {owner}")
            )

    return {
        "generatedAt": time.time(),
        "summary": {"servicesChecked": len(live), "findings": len(findings), "clean": not findings},
        "findings": findings,
    }
