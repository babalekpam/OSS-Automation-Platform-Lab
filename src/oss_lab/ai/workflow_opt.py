"""Workflow analytics over the platform event stream: slow/retry-heavy steps and
event correlation to find the originating component of a failed order."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..core.events import Event

STEP_COMPONENT = {
    "create_service": "inventory",
    "reserve_resources": "inventory",
    "update_inventory": "inventory",
    "release_resources": "inventory",
    "activate": "activation/controller",
    "deactivate": "activation/controller",
    "enroll_assurance": "assurance",
    "unenroll_assurance": "assurance",
    "set_state": "orchestrator",
}


def step_stats(events: list[Event]) -> list[dict[str, Any]]:
    """Per-step run count, mean/max duration (ms), retry and failure rate, worst first."""
    started: dict[tuple, float] = {}
    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "retries": 0, "failures": 0, "durations": []}
    )
    for e in (e for e in events if e.topic == "workflow.step"):
        p = e.payload
        k = (p["order_id"], p["item_id"], p["step"])
        s = acc[p["step"]]
        if p["status"] == "started":
            started[k] = e.ts
        elif p["status"] == "completed" and k in started:
            s["runs"] += 1
            s["durations"].append((e.ts - started[k]) * 1000)
        elif p["status"] == "retry":
            s["retries"] += 1
        elif p["status"] == "failed":
            s["runs"] += 1
            s["failures"] += 1
    out = []
    for step, s in acc.items():
        d = s["durations"] or [0.0]
        out.append({
            "step": step, "runs": s["runs"], "meanMs": round(sum(d) / len(d), 3),
            "maxMs": round(max(d), 3), "retries": s["retries"],
            "failureRate": round(s["failures"] / s["runs"], 3) if s["runs"] else 0.0,
        })  # fmt: skip
    return sorted(out, key=lambda r: (-r["failureRate"], -r["retries"], -r["meanMs"]))


def root_cause(events: list[Event], order_id: str) -> dict[str, Any] | None:
    """Link a failed order to its first failed step, the component that owns it, and
    which earlier steps were compensated."""
    steps = [e.payload for e in events if e.topic == "workflow.step" and e.key == order_id]
    failed = next((p for p in steps if p["status"] == "failed"), None)
    if failed is None:
        return None
    return {
        "order_id": order_id,
        "service_id": failed.get("service_id"),
        "failed_step": failed["step"],
        "component": STEP_COMPONENT.get(failed["step"], "unknown"),
        "error": failed.get("error"),
        "compensated": [p["step"] for p in steps if p["status"] == "compensated"],
    }
