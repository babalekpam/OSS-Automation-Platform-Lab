"""Activation engine (TMF640): renders model-driven config from the catalog
templates and pushes it southbound through the controller."""

from __future__ import annotations

import itertools
import json
import time
from typing import Any

from jinja2 import Environment, StrictUndefined

from .catalog import ServiceSpecification
from .controller import Controller
from .errors import ValidationFailed

_env = Environment(undefined=StrictUndefined, autoescape=False)


class Activation:
    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.jobs: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    @staticmethod
    def render(
        spec: ServiceSpecification, chars: dict[str, Any], enabled: bool
    ) -> tuple[str, dict[str, Any]]:
        ctx = {**chars, "enabled": enabled}
        try:
            key = _env.from_string(spec.config_key).render(**ctx)
            value = json.loads(_env.from_string(spec.template).render(**ctx))
        except Exception as exc:  # template or JSON error is a design defect, not a device fault
            raise ValidationFailed(f"cannot render '{spec.id}': {exc}") from exc
        return key, value

    def activate(
        self, service_id: str, spec: ServiceSpecification, chars: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        key, value = self.render(spec, chars, enabled)
        job = {
            "id": f"job-{next(self._ids)}",
            "service_id": service_id,
            "action": "activate",
            "device": chars["device"],
            "key": key,
            "state": "running",
            "started": time.time(),
        }
        self.jobs.append(job)
        try:
            self.controller.push(chars["device"], key, value)
        except Exception:
            job["state"] = "failed"
            raise
        job["state"] = "completed"
        return job

    def deactivate(
        self, service_id: str, spec: ServiceSpecification, chars: dict[str, Any]
    ) -> dict[str, Any]:
        key, _ = self.render(spec, chars, enabled=False)
        job = {
            "id": f"job-{next(self._ids)}",
            "service_id": service_id,
            "action": "deactivate",
            "device": chars["device"],
            "key": key,
            "state": "running",
            "started": time.time(),
        }
        self.jobs.append(job)
        try:
            self.controller.remove(chars["device"], key)
        except Exception:
            job["state"] = "failed"
            raise
        job["state"] = "completed"
        return job
