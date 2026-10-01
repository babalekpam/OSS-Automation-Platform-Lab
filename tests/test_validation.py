from fastapi.testclient import TestClient

from oss_lab.core.api import create_app
from oss_lab.core.stack import Stack
from oss_lab.validation.client import LabClient
from oss_lab.validation.contract import diff_openapi
from oss_lab.validation.runner import build_report, percentile, to_markdown


def lab(version, stack=None):
    return LabClient(TestClient(create_app(stack or Stack(version=version))))


def spec(props, required=(), params=(), path="/x", method="get"):
    return {
        "paths": {
            path: {
                method: {
                    "parameters": list(params),
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"a": {"type": "string"}},
                                    "required": list(required),
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": props}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {"schemas": {}},
    }


def kinds(old, new):
    return sorted((c["kind"], c["detail"]) for c in diff_openapi(old, new))


def test_contract_removed_property_is_breaking():
    out = kinds(
        spec({"a": {"type": "string"}, "b": {"type": "string"}}), spec({"a": {"type": "string"}})
    )
    assert out == [("breaking", "response property 'b' removed")]


def test_contract_type_change_is_breaking():
    out = kinds(spec({"a": {"type": "string"}}), spec({"a": {"type": "integer"}}))
    assert out[0][0] == "breaking" and "string -> integer" in out[0][1]


def test_contract_additions_are_additive():
    out = kinds(
        spec({"a": {"type": "string"}}), spec({"a": {"type": "string"}, "b": {"type": "string"}})
    )
    assert out == [("additive", "response property 'b' added")]


def test_contract_removed_operation_and_new_required_param():
    assert kinds(spec({}), {"paths": {}, "components": {}}) == [("breaking", "operation removed")]
    param = {"name": "q", "in": "query", "required": True}
    assert ("breaking", "new required query parameter 'q'") in kinds(
        spec({}), spec({}, params=[param])
    )


def test_contract_new_required_request_property_is_breaking():
    new = spec({})
    new["paths"]["/x"]["get"]["requestBody"]["content"]["application/json"]["schema"]["properties"][
        "z"
    ] = {"type": "string"}
    new["paths"]["/x"]["get"]["requestBody"]["content"]["application/json"]["schema"][
        "required"
    ] = ["z"]
    assert ("breaking", "new required request property 'z'") in kinds(spec({}), new)


def test_percentile():
    assert percentile([], 95) == 0.0
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile([1, 2, 3, 4], 100) == 4


def test_readiness_go_for_compatible_candidate():
    report = build_report("n", lab("n"), "n1", lab("n1"), "orchestrator")
    assert report["decision"] == "GO", report["blockers"]
    assert all(m["n"] == m["n1"] == "pass" for m in report["matrix"])
    assert {c["kind"] for c in report["contract"]} == {"additive"}
    assert "Decision: **GO**" in to_markdown(report)


def test_readiness_no_go_when_candidate_regresses():
    class LeakyStack(Stack):
        def _build(self):  # candidate leaks reservations on rollback, even after /lab/reset
            super()._build()
            self.inventory.release = lambda *a, **k: None

    broken = LeakyStack(version="n1")
    report = build_report("n", lab("n"), "n1", lab("n1", broken))
    assert report["decision"] == "NO-GO"
    assert any(
        d["scenario"] == "rollback_on_device_failure" and d["to"] == "fail"
        for d in report["behavioralDiffs"]
    )
    assert any("n1 fails" in b for b in report["blockers"])


def test_interop_matrix_all_release_combinations_pass():
    from oss_lab.validation.runner import interop_matrix

    rows = interop_matrix()
    assert len(rows) == 4 and all(r["passed"] for r in rows), rows
    used = {(r["orchestrator"], r["activation"]): r["correlationUsed"] for r in rows}
    # the new capability is used only when both sides are N+1; mixed stacks degrade gracefully
    assert used == {("n", "n"): False, ("n", "n1"): False, ("n1", "n"): False, ("n1", "n1"): True}


def test_readiness_no_go_on_interop_failure():
    bad = [
        {
            "orchestrator": "n1",
            "activation": "n",
            "passed": False,
            "failedScenarios": ["lifecycle_full"],
            "correlationUsed": False,
            "correlationExpected": False,
        }
    ]
    report = build_report("n", lab("n"), "n1", lab("n1"), interop=bad)
    assert report["decision"] == "NO-GO"
    assert any(
        b.startswith("interop failure: orchestrator=n1 activation=n") for b in report["blockers"]
    )
    assert "| n1 | n | FAIL |" in to_markdown(report)
