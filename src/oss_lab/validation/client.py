"""Thin HTTP client used by every validation scenario. Works against an in-process
ASGI app or a remote deployment, and records per-request latency."""

from __future__ import annotations

import time
from typing import Any

import httpx

ORD = "/tmf-api/serviceOrdering/v4/serviceOrder"
SVC = "/tmf-api/serviceInventory/v4/service"
RES = "/tmf-api/resourceInventoryManagement/v4/resource"
ALM = "/tmf-api/alarmManagement/v4/alarm"


def characteristics(**values: Any) -> list[dict[str, Any]]:
    return [{"name": k, "value": v} for k, v in values.items()]


class LabClient:
    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self.latencies: list[float] = []

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        start = time.perf_counter()
        resp = self.http.request(method, url, **kw)
        self.latencies.append(time.perf_counter() - start)
        return resp

    def json(self, method: str, url: str, expect: int = 200, **kw: Any) -> Any:
        resp = self.request(method, url, **kw)
        if resp.status_code != expect:
            raise AssertionError(
                f"{method} {url} -> {resp.status_code} (expected {expect}): {resp.text}"
            )
        return resp.json()

    def reset(self) -> None:
        self.json("POST", "/lab/reset")

    def order(self, *items: dict[str, Any], order_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"orderItem": list(items)}
        if order_id:
            body["id"] = order_id
        return self.json("POST", ORD, expect=201, json=body)

    def add_l3(self, device: str, iface: str, ip: str, **extra: Any) -> dict[str, Any]:
        return {
            "action": "add",
            "service": {
                "serviceSpecification": {"id": "l3-interface"},
                "serviceCharacteristic": characteristics(
                    device=device, interface=iface, ipv4=ip, **extra
                ),
            },
        }

    def action(self, action: str, service_id: str, **chars: Any) -> dict[str, Any]:
        return {
            "action": action,
            "service": {"id": service_id, "serviceCharacteristic": characteristics(**chars)},
        }

    def service(self, service_id: str) -> dict[str, Any]:
        return self.json("GET", f"{SVC}/{service_id}")

    def reconcile(self) -> dict[str, Any]:
        return self.json("GET", "/reconciliation/report")
