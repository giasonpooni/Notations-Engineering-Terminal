"""Public entry points preserve the retained system workflow on current NET."""
import json
from unittest.mock import patch

from ciw import cli, net
from ciw.operator_commands import COMMANDS
from ciw.session import read_json
from ciw.system_spec import demo_spec
from ciw import system_workflow as workflow


def _files(directory):
    return {path.relative_to(directory): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def test_ciw_and_net_compile_the_same_typed_configuration(tmp_path, capsys):
    specification = tmp_path / "specification.json"
    specification.write_text(json.dumps(demo_spec()), encoding="utf-8")
    before = specification.read_bytes()
    assert [module for name, module, _ in COMMANDS if name == "system"] == ["system_cli"]
    plans = []
    for entry in (cli.main, net.main):
        assert entry(["system", "compile", str(specification)]) == 0
        plans.append(json.loads(capsys.readouterr().out))
    assert plans[0] == plans[1]
    assert plans[0]["schema"] == "ciw.system-plan.v1"
    assert specification.read_bytes() == before


def test_public_run_inspection_and_fresh_verification_preserve_occurrences(tmp_path, capsys):
    specification = tmp_path / "specification.json"
    specification.write_text(json.dumps(demo_spec()), encoding="utf-8")
    destination, fresh = tmp_path / "run", tmp_path / "fresh"
    assert net.main(["system", "run", str(specification),
                     "--output-dir", str(destination)]) == 0
    capsys.readouterr()
    workspace = destination / "workspace.json"
    retained = read_json(workspace)
    assert retained["workspace_version"] == 3
    assert {source["kind"] for source in retained["workbench"]["sources"]} == {"system-specification"}
    assert {row["operation_id"] for row in retained["results"]} == {
        workflow.COMPILE, workflow.RUN, workflow.VERIFY}
    before = _files(destination)
    with patch("ciw.system_models.simulate", side_effect=AssertionError("Inspection simulated")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("Inspection verified")):
        assert net.main(["system", "inspect", str(workspace)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["canonical_admission"] is False
    assert inspected["physical_validation_status"] == "not_assessed"
    assert len(inspected["configurations"]) == len(inspected["simulations"]) == len(inspected["numerical_reports"]) == 1
    assert _files(destination) == before
    assert net.main(["system", "verify", str(workspace),
                     "--output-dir", str(fresh)]) == 0
    capsys.readouterr()
    reopened = read_json(fresh / "workspace.json")
    old_results = {row["result_id"]: row for row in retained["results"]}
    assert all(row == old_results[row["result_id"]]
               for row in reopened["results"] if row["result_id"] in old_results)
    reports = [row for row in reopened["results"] if row["operation_id"] == workflow.VERIFY]
    assert len(reports) == 2
    assert len({row["data"]["verification_id"] for row in reports}) == 2
    assert len({row["execution_id"] for row in reports}) == 2
    assert len({row["data"]["candidate_result_id"] for row in reports}) == 1
    assert _files(destination) == before
