"""Assurance (TMF642): alarm ingestion and per-service health."""

from __future__ import annotations

import itertools
import time
from typing import Any

from .errors import NotFound, ValidationFailed

SEVERITIES = ("warning", "minor", "major", "critical")


class Assurance:
    def __init__(self) -> None:
        self.alarms: dict[str, dict[str, Any]] = {}
        self.enrolled: dict[str, list[str]] = {}  # service_id -> resource ids
        self._ids = itertools.count(1)

    def enroll(self, service_id: str, resource_ids: list[str]) -> None:
        self.enrolled[service_id] = list(resource_ids)

    def unenroll(self, service_id: str) -> None:
        self.enrolled.pop(service_id, None)

    def raise_alarm(self, source: str, severity: str, text: str = "") -> dict[str, Any]:
        if severity not in SEVERITIES:
            raise ValidationFailed(f"severity must be one of {SEVERITIES}")
        alarm = {
            "id": f"alm-{next(self._ids)}",
            "source": source,
            "severity": severity,
            "text": text,
            "state": "raised",
            "raised_at": time.time(),
        }
        self.alarms[alarm["id"]] = alarm
        return alarm

    def clear_alarm(self, alarm_id: str) -> dict[str, Any]:
        try:
            alarm = self.alarms[alarm_id]
        except KeyError:
            raise NotFound(f"alarm '{alarm_id}' not found") from None
        alarm["state"] = "cleared"
        return alarm

    def active_alarms(self) -> list[dict[str, Any]]:
        return [a for a in self.alarms.values() if a["state"] == "raised"]

    def health(self, service_id: str) -> dict[str, Any]:
        """Service health is derived from alarms on the service's resources or their
        parent device: critical -> down, major/minor -> degraded."""
        if service_id not in self.enrolled:
            return {"service_id": service_id, "health": "unmonitored", "alarms": []}
        scope = set(self.enrolled[service_id]) | {
            r.split(":")[0] for r in self.enrolled[service_id]
        }
        hits = [a for a in self.active_alarms() if a["source"] in scope]
        if any(a["severity"] == "critical" for a in hits):
            state = "down"
        elif any(a["severity"] in ("major", "minor") for a in hits):
            state = "degraded"
        else:
            state = "healthy"
        return {"service_id": service_id, "health": state, "alarms": [a["id"] for a in hits]}
