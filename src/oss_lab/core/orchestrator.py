"""Service orchestration: TMF641 orders decomposed into idempotent, retryable
steps with first-class compensation (the in-process analogue of a Temporal workflow)."""

from __future__ import annotations

import itertools
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .activation import Activation
from .assurance import Assurance
from .catalog import Catalog
from .errors import Conflict, LabError, TransientError, ValidationFailed
from .events import EventBus
from .inventory import LIVE_STATES, Inventory


@dataclass
class Step:
    name: str
    do: Callable[[], None]
    undo: Callable[[], None] | None = None


class Orchestrator:
    ACTIONS = ("add", "modify", "delete", "suspend", "resume")

    def __init__(
        self,
        catalog: Catalog,
        inventory: Inventory,
        activation: Activation,
        assurance: Assurance,
        bus: EventBus,
        max_attempts: int = 3,
        release: str = "n",
    ) -> None:
        self.release = release
        self.catalog, self.inventory = catalog, inventory
        self.activation, self.assurance, self.bus = activation, assurance, bus
        self.max_attempts = max_attempts
        self.orders: dict[str, dict[str, Any]] = {}
        self.metrics: Counter[str] = Counter()
        self._order_ids = itertools.count(1)

    # -- public API ---------------------------------------------------------
    def submit(self, order_in: dict[str, Any]) -> dict[str, Any]:
        order_id = order_in.get("id") or f"ord-{next(self._order_ids)}"
        if order_id in self.orders:  # idempotent resubmission
            return self.orders[order_id]
        order = {
            "id": order_id,
            "state": "inProgress",
            "orderDate": time.time(),
            "orderItem": [],
        }
        self.orders[order_id] = order
        self._emit_order(order)
        for index, item_in in enumerate(order_in.get("orderItem", []), start=1):
            order["orderItem"].append(
                self._run_item(order_id, str(item_in.get("id") or index), item_in)
            )
        states = {i["state"] for i in order["orderItem"]}
        order["state"] = (
            "completed"
            if states <= {"completed"}
            else "failed"
            if states == {"failed"}
            else "partial"
        )
        order["completionDate"] = time.time()
        self.metrics[f"orders_{order['state']}"] += 1
        self._emit_order(order)
        return order

    # -- item execution -----------------------------------------------------
    def _run_item(self, order_id: str, item_id: str, item_in: dict[str, Any]) -> dict[str, Any]:
        action = item_in.get("action")
        svc_in = item_in.get("service", {})
        item = {
            "id": item_id,
            "action": action,
            "state": "inProgress",
            "service": {},
            "error": None,
        }
        ctx = {"order_id": order_id, "item_id": item_id}
        try:
            if action not in self.ACTIONS:
                raise ValidationFailed(f"unsupported action '{action}'")
            steps, service_id = self._plan(action, order_id, svc_in)
            ctx["service_id"] = service_id
            self._run_steps(steps, ctx)
            item["state"] = "completed"
        except LabError as exc:
            item["state"] = "failed"
            item["error"] = {"code": exc.code, "message": exc.message}
            self.metrics["items_failed"] += 1
        else:
            self.metrics["items_completed"] += 1
        item["service"] = {"id": ctx.get("service_id")}
        return item

    def _run_steps(self, steps: list[Step], ctx: dict[str, Any]) -> None:
        done: list[Step] = []
        try:
            for step in steps:
                self._attempt(step, ctx)
                done.append(step)
        except LabError:
            for step in reversed(done):
                if step.undo is None:
                    continue
                try:
                    step.undo()
                    self._emit_step(ctx, step.name, "compensated")
                except Exception as undo_exc:  # compensation must never mask the root cause
                    self._emit_step(ctx, step.name, "compensation_failed", error=str(undo_exc))
            raise

    def _attempt(self, step: Step, ctx: dict[str, Any]) -> None:
        for attempt in range(1, self.max_attempts + 1):
            self._emit_step(ctx, step.name, "started", attempt=attempt)
            try:
                step.do()
            except TransientError as exc:
                self._emit_step(ctx, step.name, "retry", attempt=attempt, error=exc.message)
                self.metrics["step_retries"] += 1
                if attempt == self.max_attempts:
                    self._emit_step(ctx, step.name, "failed", error=exc.message)
                    raise
            except LabError as exc:
                self._emit_step(ctx, step.name, "failed", error=exc.message)
                raise
            else:
                self._emit_step(ctx, step.name, "completed")
                return

    # -- planning -----------------------------------------------------------
    def _plan(self, action: str, order_id: str, svc_in: dict[str, Any]) -> tuple[list[Step], str]:
        chars_in = {c["name"]: c["value"] for c in svc_in.get("serviceCharacteristic", [])}
        if action == "add":
            return self._plan_add(order_id, svc_in, chars_in)
        service_id = svc_in.get("id")
        if not service_id:
            raise ValidationFailed(f"action '{action}' requires service.id")
        svc = self.inventory.get_service(service_id)
        spec = self.catalog.get(svc["spec_id"])
        if svc["state"] not in LIVE_STATES:
            raise Conflict(f"service '{service_id}' is {svc['state']}; no further changes allowed")
        old_chars, old_state = dict(svc["characteristics"]), svc["state"]

        def set_state(state: str) -> Callable[[], None]:
            return lambda: self._set_state(service_id, state)

        def push(chars: dict[str, Any], enabled: bool) -> Callable[[], None]:
            return lambda: self._activate(order_id, service_id, spec, chars, enabled)

        if action == "modify":
            immutable = {c.name for c in spec.characteristics if c.immutable}
            changed = sorted(
                n for n in immutable if n in chars_in and chars_in[n] != old_chars.get(n)
            )
            if changed:
                raise ValidationFailed(f"characteristics are immutable: {changed}")
            new_chars = self.catalog.validate_characteristics(spec, {**old_chars, **chars_in})
            enabled = old_state == "active"
            return [
                Step("activate", push(new_chars, enabled), push(old_chars, enabled)),
                Step(
                    "update_inventory",
                    lambda: self.inventory.update_service(service_id, characteristics=new_chars),
                    lambda: self.inventory.update_service(service_id, characteristics=old_chars),
                ),
            ], service_id
        if action == "suspend":
            if old_state != "active":
                raise Conflict(f"service '{service_id}' is not active")
            return [
                Step("activate", push(old_chars, False), push(old_chars, True)),
                Step("set_state", set_state("inactive"), set_state(old_state)),
            ], service_id
        if action == "resume":
            if old_state != "inactive":
                raise Conflict(f"service '{service_id}' is not inactive")
            return [
                Step("activate", push(old_chars, True), push(old_chars, False)),
                Step("set_state", set_state("active"), set_state(old_state)),
            ], service_id
        # delete
        resource_ids = list(svc["resource_ids"])
        return [
            Step(
                "deactivate",
                lambda: self.activation.deactivate(service_id, spec, old_chars),
                push(old_chars, old_state == "active"),
            ),
            Step(
                "unenroll_assurance",
                lambda: self.assurance.unenroll(service_id),
                lambda: self.assurance.enroll(service_id, resource_ids),
            ),
            Step(
                "release_resources",
                lambda: [self.inventory.release(r, service_id) for r in resource_ids],
                lambda: [self.inventory.reserve(r, service_id) for r in resource_ids],
            ),
            Step("set_state", set_state("terminated"), set_state(old_state)),
        ], service_id

    def _plan_add(
        self, order_id: str, svc_in: dict[str, Any], chars_in: dict[str, Any]
    ) -> tuple[list[Step], str]:
        spec_id = (svc_in.get("serviceSpecification") or {}).get("id")
        if not spec_id:
            raise ValidationFailed("add requires service.serviceSpecification.id")
        spec = self.catalog.get(spec_id)
        chars = self.catalog.validate_characteristics(spec, chars_in)
        service_id = svc_in.get("id") or self.inventory.new_service_id()
        resource_id = f"{chars['device']}:{chars['interface']}"
        self.inventory.get_resource(resource_id)  # fail fast before any state changes

        def create() -> None:
            self.inventory.create_service(service_id, spec_id, order_id, chars)

        def reserve() -> None:
            self.inventory.reserve(resource_id, service_id)
            self.inventory.update_service(service_id, state="reserved", resource_ids=[resource_id])

        def activate() -> None:
            self._activate(order_id, service_id, spec, chars, True)

        return [
            Step(
                "create_service", create, lambda: self.inventory.delete_service_record(service_id)
            ),
            Step(
                "reserve_resources",
                reserve,
                lambda: self.inventory.release(resource_id, service_id),
            ),
            Step(
                "activate",
                activate,
                lambda: self.activation.deactivate(service_id, spec, chars),
            ),
            Step(
                "enroll_assurance",
                lambda: self.assurance.enroll(service_id, [resource_id]),
                lambda: self.assurance.unenroll(service_id),
            ),
            Step("set_state", lambda: self._set_state(service_id, "active")),
        ], service_id

    # -- helpers ------------------------------------------------------------
    def _activate(
        self, order_id: str, service_id: str, spec: Any, chars: dict, enabled: bool
    ) -> None:
        """Release N+1 passes the order id as a correlation id, but only to an activation
        component that advertises the capability, so it still works against release N."""
        if self.release != "n" and "correlation_id" in self.activation.capabilities:
            self.activation.activate(service_id, spec, chars, enabled, correlation_id=order_id)
        else:
            self.activation.activate(service_id, spec, chars, enabled)

    def _set_state(self, service_id: str, state: str) -> None:
        self.inventory.update_service(service_id, state=state)
        self.bus.publish("service.state", {"service_id": service_id, "state": state}, service_id)

    def _emit_order(self, order: dict[str, Any]) -> None:
        self.bus.publish(
            "order.state", {"order_id": order["id"], "state": order["state"]}, order["id"]
        )

    def _emit_step(self, ctx: dict[str, Any], step: str, status: str, **extra: Any) -> None:
        self.bus.publish(
            "workflow.step",
            {**ctx, "step": step, "status": status, **extra},
            key=ctx["order_id"],
        )
