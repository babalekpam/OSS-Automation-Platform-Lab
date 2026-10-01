"""HTTP edge: TM Forum style Open API endpoints over a ``Stack``.

``/lab/*`` endpoints are test-only controls (fault injection, drift injection,
reset, state export/import) and exist so the validation suite can drive any target."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .catalog import ServiceSpecification
from .errors import LabError
from .stack import Stack

TMF633 = "/tmf-api/serviceCatalogManagement/v4"
TMF638 = "/tmf-api/serviceInventory/v4"
TMF639 = "/tmf-api/resourceInventoryManagement/v4"
TMF640 = "/tmf-api/serviceActivationAndConfiguration/v4"
TMF641 = "/tmf-api/serviceOrdering/v4"
TMF642 = "/tmf-api/alarmManagement/v4"


class CharacteristicValue(BaseModel):
    name: str
    value: Any


class SpecRef(BaseModel):
    id: str


class ServiceIn(BaseModel):
    id: str | None = None
    serviceSpecification: SpecRef | None = None
    serviceCharacteristic: list[CharacteristicValue] = Field(default_factory=list)


class OrderItemIn(BaseModel):
    id: str | None = None
    action: str
    service: ServiceIn = Field(default_factory=ServiceIn)


class ServiceOrderIn(BaseModel):
    id: str | None = None
    orderItem: list[OrderItemIn]


class OrderItemOut(BaseModel):
    id: str
    action: str | None
    state: str
    service: dict[str, Any]
    error: dict[str, Any] | None = None


class ServiceOrderOut(BaseModel):
    id: str
    state: str
    orderItem: list[OrderItemOut]


class ServiceOut(BaseModel):
    id: str
    state: str
    serviceSpecification: SpecRef
    serviceCharacteristic: list[CharacteristicValue]
    resourceIds: list[str]
    orderId: str | None = None


class ServiceOutN1(ServiceOut):
    """N+1 adds an optional, non-breaking field."""

    lastUpdate: str | None = None


class ResourceOut(BaseModel):
    id: str
    type: str
    name: str
    parent: str | None = None
    assignedTo: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AlarmIn(BaseModel):
    source: str
    severity: str
    text: str = ""


class FaultIn(BaseModel):
    device: str
    mode: str = "permanent"
    count: int = 1


class DriftIn(BaseModel):
    device: str
    key: str
    value: dict[str, Any] | None = None


def _service_out(svc: dict[str, Any], version: str) -> dict[str, Any]:
    out = {
        "id": svc["id"],
        "state": svc["state"],
        "serviceSpecification": {"id": svc["spec_id"]},
        "serviceCharacteristic": [
            {"name": k, "value": v} for k, v in svc["characteristics"].items()
        ],
        "resourceIds": svc["resource_ids"],
        "orderId": svc["order_id"],
    }
    if version == "n1":
        out["lastUpdate"] = dt.datetime.fromtimestamp(svc["updated"], dt.UTC).isoformat()
    return out


def create_app(stack: Stack | None = None) -> FastAPI:
    stack = stack or Stack()
    app = FastAPI(title="OSS Automation Lab", version=f"{stack.version}")
    app.state.stack = stack
    svc_model = ServiceOutN1 if stack.version == "n1" else ServiceOut

    @app.exception_handler(LabError)
    async def _lab_error(_: Request, exc: LabError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "reason": exc.message})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "release": stack.version}

    # -- TMF633 catalog ------------------------------------------------------
    @app.get(f"{TMF633}/serviceSpecification")
    def list_specs() -> list[dict[str, Any]]:
        return [s.model_dump() for s in stack.catalog.list()]

    @app.get(f"{TMF633}/serviceSpecification/{{spec_id}}")
    def get_spec(spec_id: str) -> dict[str, Any]:
        return stack.catalog.get(spec_id).model_dump()

    @app.post(f"{TMF633}/serviceSpecification", status_code=201)
    def create_spec(spec: ServiceSpecification) -> dict[str, Any]:
        return stack.catalog.add(spec).model_dump()

    # -- TMF641 ordering -----------------------------------------------------
    @app.post(f"{TMF641}/serviceOrder", status_code=201, response_model=ServiceOrderOut)
    def create_order(order: ServiceOrderIn) -> dict[str, Any]:
        return stack.orchestrator.submit(order.model_dump())

    @app.get(f"{TMF641}/serviceOrder", response_model=list[ServiceOrderOut])
    def list_orders() -> list[dict[str, Any]]:
        return list(stack.orchestrator.orders.values())

    @app.get(f"{TMF641}/serviceOrder/{{order_id}}", response_model=ServiceOrderOut)
    def get_order(order_id: str) -> dict[str, Any]:
        from .errors import NotFound

        try:
            return stack.orchestrator.orders[order_id]
        except KeyError:
            raise NotFound(f"order '{order_id}' not found") from None

    # -- TMF638 service inventory ---------------------------------------------
    @app.get(f"{TMF638}/service", response_model=list[svc_model])
    def list_services(state: str | None = None) -> list[dict[str, Any]]:
        return [
            _service_out(s, stack.version)
            for s in stack.inventory.services.values()
            if state is None or s["state"] == state
        ]

    @app.get(f"{TMF638}/service/{{service_id}}", response_model=svc_model)
    def get_service(service_id: str) -> dict[str, Any]:
        return _service_out(stack.inventory.get_service(service_id), stack.version)

    # -- TMF639 resource inventory ----------------------------------------------
    def _res_out(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": r["id"], "type": r["type"], "name": r["name"], "parent": r.get("parent"),
            "assignedTo": r["assigned_to"], "attributes": r["attributes"],
        }  # fmt: skip

    @app.get(f"{TMF639}/resource", response_model=list[ResourceOut])
    def list_resources(type: str | None = None) -> list[dict[str, Any]]:
        return [_res_out(r) for r in stack.inventory.resources.values() if type in (None, r["type"])]

    @app.get(f"{TMF639}/resource/{{resource_id:path}}", response_model=ResourceOut)
    def get_resource(resource_id: str) -> dict[str, Any]:
        return _res_out(stack.inventory.get_resource(resource_id))

    # -- TMF640 activation -----------------------------------------------------
    @app.get(f"{TMF640}/activationJob")
    def list_jobs(service_id: str | None = None) -> list[dict[str, Any]]:
        return [j for j in stack.activation.jobs if service_id in (None, j["service_id"])]

    # -- TMF642 alarms + assurance ------------------------------------------------
    @app.post(f"{TMF642}/alarm", status_code=201)
    def raise_alarm(alarm: AlarmIn) -> dict[str, Any]:
        return stack.assurance.raise_alarm(alarm.source, alarm.severity, alarm.text)

    @app.get(f"{TMF642}/alarm")
    def list_alarms(state: str | None = None) -> list[dict[str, Any]]:
        return [a for a in stack.assurance.alarms.values() if state in (None, a["state"])]

    @app.patch(f"{TMF642}/alarm/{{alarm_id}}/clear")
    def clear_alarm(alarm_id: str) -> dict[str, Any]:
        return stack.assurance.clear_alarm(alarm_id)

    @app.get("/assurance/service/{service_id}/health")
    def service_health(service_id: str) -> dict[str, Any]:
        return stack.assurance.health(service_id)

    # -- reconciliation, events, metrics -----------------------------------------
    @app.get("/reconciliation/report")
    def reconciliation_report() -> dict[str, Any]:
        return stack.reconcile()

    @app.get("/events")
    def events(topic: str | None = None, key: str | None = None) -> list[dict[str, Any]]:
        return [e.as_dict() for e in stack.bus.events(topic, key)]

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        lines = [f"oss_lab_{name} {value}" for name, value in sorted(stack.orchestrator.metrics.items())]
        by_state: dict[str, int] = {}
        for s in stack.inventory.services.values():
            by_state[s["state"]] = by_state.get(s["state"], 0) + 1
        lines += [f'oss_lab_services{{state="{k}"}} {v}' for k, v in sorted(by_state.items())]
        lines.append(f"oss_lab_events_total {len(stack.bus.events())}")
        lines.append(f"oss_lab_active_alarms {len(stack.assurance.active_alarms())}")
        return "\n".join(lines) + "\n"

    # -- lab controls --------------------------------------------------------------
    @app.post("/lab/reset")
    def lab_reset() -> dict[str, str]:
        stack.reset()
        return {"status": "reset"}

    @app.post("/lab/faults")
    def lab_fault(fault: FaultIn) -> dict[str, Any]:
        stack.controller.device(fault.device).inject_fault(fault.mode, fault.count)
        return fault.model_dump()

    @app.post("/lab/drift")
    def lab_drift(drift: DriftIn) -> dict[str, Any]:
        dev = stack.controller.device(drift.device)
        if drift.value is None:
            dev.config.pop(drift.key, None)
        else:
            dev.config[drift.key] = drift.value
        return drift.model_dump()

    @app.get("/lab/export")
    def lab_export() -> dict[str, Any]:
        return stack.export_state()

    @app.post("/lab/import")
    def lab_import(state: dict[str, Any]) -> dict[str, str]:
        stack.import_state(state)
        return {"status": "imported"}

    return app
