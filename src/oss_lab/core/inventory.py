"""Resource and service inventory (TMF639 / TMF638). The in-memory implementation
stands in for NetBox behind the same interface."""

from __future__ import annotations

import copy
import itertools
import time
from typing import Any

from .errors import Conflict, NotFound

LIVE_STATES = ("active", "inactive")


class Inventory:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self._svc_ids = itertools.count(1)

    # -- resources (TMF639) -------------------------------------------------
    def add_device(self, device_id: str, interfaces: list[str], os: str = "srlinux") -> None:
        self.resources[device_id] = {
            "id": device_id,
            "type": "device",
            "name": device_id,
            "attributes": {"os": os},
            "assigned_to": None,
        }
        for iface in interfaces:
            rid = f"{device_id}:{iface}"
            self.resources[rid] = {
                "id": rid,
                "type": "interface",
                "name": iface,
                "parent": device_id,
                "attributes": {},
                "assigned_to": None,
            }

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        try:
            return self.resources[resource_id]
        except KeyError:
            raise NotFound(f"resource '{resource_id}' not found") from None

    def reserve(self, resource_id: str, service_id: str) -> None:
        res = self.get_resource(resource_id)
        if res["assigned_to"] not in (None, service_id):
            raise Conflict(f"resource '{resource_id}' already assigned to '{res['assigned_to']}'")
        res["assigned_to"] = service_id

    def release(self, resource_id: str, service_id: str) -> None:
        res = self.resources.get(resource_id)
        if res and res["assigned_to"] == service_id:
            res["assigned_to"] = None

    # -- services (TMF638) --------------------------------------------------
    def new_service_id(self) -> str:
        return f"svc-{next(self._svc_ids)}"

    def create_service(
        self, service_id: str, spec_id: str, order_id: str, characteristics: dict[str, Any]
    ) -> dict[str, Any]:
        if service_id in self.services:
            raise Conflict(f"service '{service_id}' already exists")
        now = time.time()
        svc = {
            "id": service_id,
            "spec_id": spec_id,
            "order_id": order_id,
            "state": "designed",
            "characteristics": dict(characteristics),
            "resource_ids": [],
            "created": now,
            "updated": now,
        }
        self.services[service_id] = svc
        return svc

    def get_service(self, service_id: str) -> dict[str, Any]:
        try:
            return self.services[service_id]
        except KeyError:
            raise NotFound(f"service '{service_id}' not found") from None

    def update_service(self, service_id: str, **fields: Any) -> dict[str, Any]:
        svc = self.get_service(service_id)
        svc.update(fields)
        svc["updated"] = time.time()
        return svc

    def delete_service_record(self, service_id: str) -> None:
        self.services.pop(service_id, None)

    def live_services(self) -> list[dict[str, Any]]:
        return [s for s in self.services.values() if s["state"] in LIVE_STATES]

    # -- migration support --------------------------------------------------
    def export_state(self) -> dict[str, Any]:
        return copy.deepcopy({"resources": self.resources, "services": self.services})

    def import_state(self, state: dict[str, Any]) -> None:
        self.resources = copy.deepcopy(state["resources"])
        self.services = copy.deepcopy(state["services"])
        numbers = [int(s.split("-")[1]) for s in self.services if s.startswith("svc-")]
        self._svc_ids = itertools.count(max(numbers, default=0) + 1)
