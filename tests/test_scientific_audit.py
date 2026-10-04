"""Review-coverage drift must fail without mistaking references for qualification."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from ciw import operator_catalog, scientific_audit
from ciw.workbench import Workbench

ROOT = Path(__file__).resolve().parents[1]


def test_current_coverage_and_references_are_complete_without_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Audit may not bind, execute or launch a provider")
    monkeypatch.setattr(Workbench, "bind_workflow", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    report = scientific_audit.audit(repository=ROOT)
    assert report["issues"] == []
    assert report["counts"] == {"commands": 51, "workflows": 28, "ladder_levels": 10}
    assert report["source_references"] == "files_exist_only"
    assert report["tests_executed"] is False
    assert report["authorizes_execution"] is report["authorizes_state_admission"] is False
    assert report["physical_validation"] == "not_established"
    assert all(row["qualification"] == "not_performed_by_audit" for row in report["ladder"])
    assert report["open_obligations"]


@pytest.mark.parametrize("collection,key,name", [
    ("commands", "command", "net unchecked"),
    ("workflows", "workflow", "unchecked"),
])
def test_new_surface_refuses_until_explicitly_reviewed(collection, key, name):
    inventory = operator_catalog.catalog()
    inventory[collection].append({key: name})
    report = scientific_audit.audit(inventory=inventory)
    assert report["status"] == "failed"
    assert any(issue["code"] == "unreviewed_surface" for issue in report["issues"])


@pytest.mark.parametrize("collection", ["commands", "workflows"])
def test_removed_or_duplicate_surface_refuses(collection):
    inventory = operator_catalog.catalog()
    inventory[collection].pop()
    assert any(issue["code"] == "stale_review" for issue in scientific_audit.audit(inventory=inventory)["issues"])
    inventory[collection].append(deepcopy(inventory[collection][0]))
    assert any(issue["code"] == "duplicate_surface" for issue in scientific_audit.audit(inventory=inventory)["issues"])


def test_repository_missing_references_fail_and_installed_scope_is_honest(tmp_path):
    report = scientific_audit.audit(repository=tmp_path)
    assert report["status"] == "failed" and report["source_references"] == "failed"
    assert all(issue["code"] == "missing_or_unsafe_reference" for issue in report["issues"])
    installed = scientific_audit.audit()
    assert not installed["issues"]
    assert installed["source_references"] == "not_checked_without_repository"


def test_new_operation_version_requires_review_under_existing_workflow():
    inventory = operator_catalog.catalog()
    inventory["workflows"][0]["operation_id"] = "replacement.v2"
    report = scientific_audit.audit(inventory=inventory)
    assert report["status"] == "failed"
    assert any(issue["code"] == "changed_operation_identity" for issue in report["issues"])


def test_cli_reports_failure_and_success_without_relabeling_qualification(capsys, tmp_path):
    assert operator_catalog.main(["--audit", "--json", "--repository", str(ROOT)]) == 0
    assert json.loads(capsys.readouterr().out)["tests_executed"] is False
    assert operator_catalog.main(["--audit", "--json", "--repository", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    with pytest.raises(SystemExit) as caught:
        operator_catalog.main(["--repository", str(ROOT)])
    assert caught.value.code == 2

def test_integrated_irrigation_and_leakage_have_explicit_review_references():
    report = scientific_audit.audit(repository=ROOT)
    rows = {row["command"]: row for row in report["commands"]}
    assert rows["net irrigation"]["source"] == "src/ciw/irrigation_cli.py"
    assert rows["net irrigation"]["test"] == "tests/test_irrigation_workflow.py"
    assert rows["net polymer leakage"]["source"] == "src/ciw/leakage_cli.py"
    assert rows["net polymer leakage"]["test"] == "tests/test_leakage_workflow.py"

def test_integrated_system_command_has_explicit_review_references():
    report = scientific_audit.audit(repository=ROOT)
    row = next(row for row in report["commands"] if row["command"] == "net system")
    assert row["source"] == "src/ciw/system_cli.py"
    assert row["test"] == "tests/test_system_workflow.py"
