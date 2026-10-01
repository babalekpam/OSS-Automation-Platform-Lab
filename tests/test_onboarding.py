from pathlib import Path

import pytest
import yaml

from oss_lab.onboarding.cli import main
from oss_lab.onboarding.pipeline import discover, onboard

ROOT = Path(__file__).resolve().parent.parent / "onboarding"
PACKAGES = discover(ROOT)


def test_expected_packages_exist():
    assert {p.name for p in PACKAGES} == {
        "package.yaml",
        "mgmt-interface.yaml",
        "uplink-interface.yaml",
    }


@pytest.mark.parametrize("pkg", PACKAGES, ids=lambda p: p.name)
def test_every_package_passes_the_gate(pkg):
    result = onboard(pkg)
    assert result["ready"], result["steps"]
    assert all(s["status"] == "pass" for s in result["steps"])


def test_gate_fails_fast_and_skips_remaining(tmp_path):
    bad = yaml.safe_load((ROOT / "service-models/mgmt-interface.yaml").read_text())
    bad["spec"]["template"] = '{"mtu": {{ undefined_var }}}'
    f = tmp_path / "bad.yaml"
    f.write_text(yaml.safe_dump(bad))
    result = onboard(f)
    statuses = {s["step"]: s["status"] for s in result["steps"]}
    assert not result["ready"]
    assert statuses["schema_validation"] == "pass" and statuses["template_render"] == "fail"
    assert statuses["lifecycle_tests"] == "skipped"


def test_device_type_with_bad_acceptance_interface_fails(tmp_path):
    bad = yaml.safe_load((ROOT / "device-types/srlinux/package.yaml").read_text())
    bad["acceptance"]["interface"] = "ethernet-9/9"
    f = tmp_path / "dev.yaml"
    f.write_text(yaml.safe_dump(bad))
    assert onboard(f)["steps"][0]["status"] == "fail"


def test_unknown_kind_and_cli(tmp_path, capsys):
    f = tmp_path / "x.yaml"
    f.write_text("kind: nonsense\n")
    assert not onboard(f)["ready"]
    assert main([str(ROOT)]) == 0
    assert "READY" in capsys.readouterr().out
    assert main([str(f)]) == 1
