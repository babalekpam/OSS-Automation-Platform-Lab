import subprocess
import sys

import numpy as np
from fastapi.testclient import TestClient

from conftest import l3
from oss_lab.ai.anomaly import MultiKpiDetector, SeasonalBaseline, precision_recall, synthetic_kpis
from oss_lab.ai.log_analysis import TemplateMiner, compare_windows, mask, summarize
from oss_lab.ai.predictive import FailurePredictor, evaluate, synthetic_history
from oss_lab.ai.test_generation import generate_pytest
from oss_lab.ai.workflow_opt import root_cause, step_stats
from oss_lab.core.api import create_app


def test_seasonal_baseline_finds_spikes_and_ignores_seasonality():
    X, truth = synthetic_kpis(seed=1)
    det = SeasonalBaseline().fit(X[:, 0])
    p, r = precision_recall(truth, det.detect(X[:, 0]))
    assert p >= 0.9 and r >= 0.9


def test_multi_kpi_detector_precision_recall():
    X, truth = synthetic_kpis(seed=2)
    p, r = precision_recall(truth, MultiKpiDetector(contamination=0.03).fit(X).detect(X))
    assert p >= 0.6 and r >= 0.9


def test_log_masking_and_template_mining():
    assert mask("order ord-12 on 10.0.0.1 took 35ms") == "order <ID> on <IP> took 35ms"
    m = TemplateMiner()
    t = {m.add(f"push to device r{i} failed after {i} retries") for i in (1, 2, 3)}
    assert "push to device <*> failed after <NUM> retries" in {
        m.add("push to device r9 failed after 4 retries")
    }
    assert len(m.templates()) == 1 and len(t) >= 1


def test_compare_windows_flags_new_and_spiking():
    before = ["activation ok for svc-1"] * 5 + ["heartbeat ok"] * 10
    after = (
        ["activation ok for svc-2"] * 5
        + ["heartbeat ok"] * 10
        + ["controller timeout talking to r1"] * 8
        + ["heartbeat missed for r2"] * 4
    )
    out = compare_windows(before, after)
    assert any("timeout" in n["template"] for n in out["new"])
    assert "controller timeout" in summarize(out)
    assert summarize({"new": [], "spiking": []}).startswith("No new")
    assert summarize(out, llm=lambda t: "LLM:" + t).startswith("LLM:")


def test_failure_predictor_quality():
    metrics = evaluate()
    assert metrics["recall"] >= 0.7 and metrics["precision"] >= 0.4
    X, y = synthetic_history()
    model = FailurePredictor().fit(X, y)
    risky = np.array([[0, 5, 1, 1, 8]])
    safe = np.array([[0, 0, 0, 0, 1]])
    assert model.risk(risky)[0] > model.risk(safe)[0] and bool(model.flag(risky)[0])
    assert model.explain()["interface_busy"] > 0


def test_workflow_analytics_and_root_cause(stack, client, order):
    client.post("/lab/faults", json={"device": "r1", "mode": "transient", "count": 1})
    order({"action": "add", "service": l3()})
    client.post("/lab/faults", json={"device": "r2", "mode": "permanent"})
    failed = order({"action": "add", "service": l3(device="r2")})
    events = stack.bus.events()
    stats = {s["step"]: s for s in step_stats(events)}
    assert stats["activate"]["retries"] == 1 and stats["activate"]["failureRate"] > 0
    rc = root_cause(events, failed["id"])
    assert rc["failed_step"] == "activate" and rc["component"] == "activation/controller"
    assert rc["compensated"] == ["reserve_resources", "create_service"]
    assert root_cause(events, "nope") is None


def test_generated_tests_run_green(tmp_path):
    app = create_app()
    draft = generate_pytest(TestClient(app).get("/openapi.json").json())
    assert "REVIEW BEFORE MERGE" in draft and "/lab/" not in draft
    (tmp_path / "test_generated.py").write_text(draft)
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom fastapi.testclient import TestClient\n"
        "from oss_lab.core.api import create_app\n"
        "@pytest.fixture\ndef client():\n    return TestClient(create_app())\n"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
