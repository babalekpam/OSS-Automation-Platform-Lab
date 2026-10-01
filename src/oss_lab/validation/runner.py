"""N vs N+1 qualification: runs the same suite against two targets and produces a
readiness report with a go/no-go decision."""

from __future__ import annotations

import math
import time
from typing import Any

from fastapi.testclient import TestClient

from ..core.api import create_app
from ..core.stack import Stack
from .client import LabClient
from .contract import diff_openapi
from .scenarios import SCENARIOS


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p / 100 * len(ordered)) - 1))]


def run_suite(client: LabClient) -> list[dict[str, Any]]:
    results = []
    for name, fn in SCENARIOS.items():
        start = time.perf_counter()
        try:
            fn(client)
            status, error = "pass", None
        except Exception as exc:  # any failure, including transport errors, fails the scenario
            status, error = "fail", f"{type(exc).__name__}: {exc}"
        results.append(
            {"name": name, "status": status, "error": error, "seconds": time.perf_counter() - start}
        )
    return results


def perf_benchmark(client: LabClient) -> dict[str, float]:
    """Add / modify / delete on every free interface, measuring API latency and throughput."""
    client.reset()
    targets = [(d, f"ethernet-1/{i}") for d in ("r1", "r2", "r3") for i in range(1, 5)]
    before = len(client.latencies)
    start = time.perf_counter()
    orders = 0
    ids = []
    for n, (dev, iface) in enumerate(targets, start=1):
        o = client.order(client.add_l3(dev, iface, f"10.9.{n}.1"))
        ids.append(o["orderItem"][0]["service"]["id"])
        orders += 1
    for sid in ids:
        client.order(client.action("modify", sid, mtu=1500))
        orders += 1
    for sid in ids:
        client.order(client.action("delete", sid))
        orders += 1
    elapsed = time.perf_counter() - start
    lat = client.latencies[before:]
    return {
        "orders": orders,
        "ordersPerSecond": round(orders / elapsed, 2),
        "p50Ms": round(percentile(lat, 50) * 1000, 2),
        "p95Ms": round(percentile(lat, 95) * 1000, 2),
        "p99Ms": round(percentile(lat, 99) * 1000, 2),
    }


def migration_check(n: LabClient, n1: LabClient) -> dict[str, Any]:
    """Inventory, controller and workflow state created on N must survive the move to N+1."""
    n.reset()
    n1.reset()
    sids = [
        n.order(n.add_l3("r1", f"ethernet-1/{i}", f"10.7.{i}.1"))["orderItem"][0]["service"]["id"]
        for i in (1, 2, 3)
    ]
    n.order(n.action("suspend", sids[1]))
    checks: list[tuple[str, bool]] = []
    n1.json("POST", "/lab/import", json=n.json("GET", "/lab/export"))

    def core(c: LabClient) -> list[dict[str, Any]]:
        keys = ("id", "state", "serviceSpecification", "serviceCharacteristic", "resourceIds")
        return [
            {k: s[k] for k in keys} for s in c.json("GET", "/tmf-api/serviceInventory/v4/service")
        ]

    checks.append(("services identical after import", core(n) == core(n1)))
    checks.append(("reconciliation clean on N+1", n1.reconcile()["summary"]["clean"]))
    mod = n1.order(n1.action("modify", sids[0], mtu=1400))
    checks.append(("modify imported service on N+1", mod["state"] == "completed"))
    res = n1.order(n1.action("resume", sids[1]))
    checks.append(("resume imported suspended service", res["state"] == "completed"))
    new = n1.order(n1.add_l3("r2", "ethernet-1/1", "10.7.9.1"))
    checks.append(
        ("new order after import gets unique id", new["orderItem"][0]["service"]["id"] not in sids)
    )
    checks.append(
        ("reconciliation clean after follow-up changes", n1.reconcile()["summary"]["clean"])
    )
    return {
        "passed": all(ok for _, ok in checks),
        "checks": [{"name": name, "ok": ok} for name, ok in checks],
    }


def interop_matrix(n_label: str = "n", n1_label: str = "n1") -> list[dict[str, Any]]:
    """Mixed-version stacks: every orchestrator x activation release combination must pass the
    full suite, and the N+1 correlation capability must be used only when both sides support it."""
    rows = []
    for orch in (n_label, n1_label):
        for act in (n_label, n1_label):
            stack = Stack(version=n_label, components={"orchestrator": orch, "activation": act})
            client = LabClient(TestClient(create_app(stack)))
            results = run_suite(client)
            client.reset()
            client.order(client.add_l3("r1", "ethernet-1/1", "10.0.0.1"))
            jobs = stack.activation.jobs
            correlated = bool(jobs) and all(j.get("correlation_id") for j in jobs)
            expect_correlated = orch == n1_label and act == n1_label
            failed = [r["name"] for r in results if r["status"] == "fail"]
            ok = not failed and correlated == expect_correlated
            rows.append(
                {
                    "orchestrator": orch,
                    "activation": act,
                    "passed": ok,
                    "failedScenarios": failed,
                    "correlationUsed": correlated,
                    "correlationExpected": expect_correlated,
                }
            )
    return rows


def _perf_regressions(n: dict[str, float], n1: dict[str, float]) -> list[str]:
    out = []
    for metric in ("p95Ms", "p99Ms"):
        if n1[metric] > max(2 * n[metric], n[metric] + 25):
            out.append(f"{metric} regressed {n[metric]} -> {n1[metric]}")
    if n1["ordersPerSecond"] < n["ordersPerSecond"] / 2:
        out.append(
            f"throughput regressed {n['ordersPerSecond']} -> {n1['ordersPerSecond']} orders/s"
        )
    return out


def build_report(
    n_label: str,
    n: LabClient,
    n1_label: str,
    n1: LabClient,
    component: str = "all",
    interop: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    n_results, n1_results = run_suite(n), run_suite(n1)
    contract = diff_openapi(n.json("GET", "/openapi.json"), n1.json("GET", "/openapi.json"))
    perf = {n_label: perf_benchmark(n), n1_label: perf_benchmark(n1)}
    migration = migration_check(n, n1)
    n_by = {r["name"]: r for r in n_results}
    diffs = [
        {
            "scenario": r["name"],
            "from": n_by[r["name"]]["status"],
            "to": r["status"],
            "error": r["error"],
        }
        for r in n1_results
        if r["status"] != n_by[r["name"]]["status"]
    ]
    perf_issues = _perf_regressions(perf[n_label], perf[n1_label])
    blockers = (
        [f"baseline '{n_label}' fails: {r['name']}" for r in n_results if r["status"] == "fail"]
        + [f"{n1_label} fails: {r['name']}" for r in n1_results if r["status"] == "fail"]
        + [
            f"breaking API change: {c['where']} - {c['detail']}"
            for c in contract
            if c["kind"] == "breaking"
        ]
        + ([] if migration["passed"] else ["state migration failed"])
        + [
            f"interop failure: orchestrator={r['orchestrator']} activation={r['activation']} "
            f"(failed: {r['failedScenarios']}, correlation used={r['correlationUsed']}, "
            f"expected={r['correlationExpected']})"
            for r in (interop or [])
            if not r["passed"]
        ]
        + perf_issues
    )
    return {
        "component": component,
        "from": n_label,
        "to": n1_label,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "matrix": [
            {"scenario": r["name"], n_label: n_by[r["name"]]["status"], n1_label: r["status"]}
            for r in n1_results
        ],
        "failures": [r for r in n_results + n1_results if r["status"] == "fail"],
        "behavioralDiffs": diffs,
        "contract": contract,
        "performance": perf,
        "migration": migration,
        "interop": interop,
        "blockers": blockers,
        "decision": "GO" if not blockers else "NO-GO",
    }


def to_markdown(report: dict[str, Any]) -> str:
    a, b = report["from"], report["to"]
    lines = [
        f"# Readiness report: {a} -> {b} ({report['component']})",
        f"Generated {report['generatedAt']}",
        "",
        f"## Decision: **{report['decision']}**",
        *(
            [""] + [f"- {x}" for x in report["blockers"]]
            if report["blockers"]
            else ["", "No blockers."]
        ),
        "",
        "## Pass/fail matrix",
        f"| Scenario | {a} | {b} |",
        "|---|---|---|",
        *[f"| {m['scenario']} | {m[a]} | {m[b]} |" for m in report["matrix"]],
        "",
        "## API contract",
    ]
    contract = report["contract"]
    lines += [f"- **{c['kind']}** `{c['where']}`: {c['detail']}" for c in contract] or [
        "No changes."
    ]
    lines += [
        "",
        "## Performance",
        "| Release | orders | orders/s | p50 ms | p95 ms | p99 ms |",
        "|---|---|---|---|---|---|",
    ]
    for label, p in report["performance"].items():
        cells = (label, p["orders"], p["ordersPerSecond"], p["p50Ms"], p["p95Ms"], p["p99Ms"])
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines += ["", "## Mixed-version interoperability"]
    if report.get("interop") is None:
        lines.append("Not run (requires in-process stacks; skipped for remote targets).")
    else:
        lines += [
            "| orchestrator | activation | result | correlation id used |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {r['orchestrator']} | {r['activation']} | {'pass' if r['passed'] else 'FAIL'} "
            f"| {r['correlationUsed']} |"
            for r in report["interop"]
        ]
    lines += ["", "## State migration"]
    lines += [f"- [{'x' if c['ok'] else ' '}] {c['name']}" for c in report["migration"]["checks"]]
    if report["behavioralDiffs"]:
        lines += ["", "## Behavioral diffs"]
        lines += [
            f"- `{d['scenario']}`: {d['from']} -> {d['to']} {d['error'] or ''}"
            for d in report["behavioralDiffs"]
        ]
    return "\n".join(lines) + "\n"
