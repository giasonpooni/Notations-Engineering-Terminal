"""Temporal studies consume retained plans and do not grant physical authority."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from ciw.core.identities import new_identity
from ciw.operations.runner import seal
from ciw.session import Session, read_json, write_json
from ciw.system_spec import demo_spec
from ciw import system_workflow as workflow


def _call(session, operation, parameters):
    reply = session.handle({"protocol_version": 1, "request_id": "study-test",
        "type": "operation.execute", "payload": {"operation_id": operation, "parameters": parameters}})
    assert reply["type"] == "response", reply
    return reply["payload"]


def _fixture(tmp_path):
    spec = demo_spec(cells=16)
    spec["clock"]["duration"] = 20.0
    session = Session(workflow.source_run([spec]), tmp_path)
    plan = workflow.execute(session, workflow.COMPILE, {"specification": spec})
    return spec, session, plan


def _study(session, plan, **extra):
    return workflow.execute(session, workflow.STUDY, {
        "plan": plan, "source_result_id": plan["result_id"], **extra})


def test_study_is_retained_compute_with_exact_plan_and_dependency(tmp_path):
    spec, session, plan = _fixture(tmp_path)
    record = _study(session, plan, step_sizes_s=[5.0, 2.5])
    data = record["data"]
    assert record["role"] == "backend"
    assert record["verification_id"] is None
    assert record["verification_status"] == "not_verified"
    assert data["plan_result_id"] == plan["result_id"]
    assert data["plan_record_digest"] == plan["record_digest"]
    assert data["report"]["step_sizes_s"] == [5.0, 2.5]
    assert data["report"]["plan_digest"] == plan["data"]["plan_digest"]
    assert data["report"]["canonical_admission"] is False
    assert data["report"]["physical_validation_status"] == "not_assessed"
    from ciw.dependency_graph import artifact_graph
    graph = artifact_graph(session.run, session.results, session.executions, session.workbench)
    assert plan["result_id"] in graph[record["result_id"]]["dependencies"]
    assert plan["result_id"] in graph[record["execution_id"]]["dependencies"]


def test_study_inspection_and_restore_do_not_run_science(tmp_path):
    _, session, plan = _fixture(tmp_path / "initial")
    _study(session, plan)
    path = session.save_workspace(tmp_path / "initial" / "workspace.json")
    before = path.read_bytes()
    with patch("ciw.system_study.run_temporal_study", side_effect=AssertionError("offline study")), \
         patch("ciw.system_models.simulate", side_effect=AssertionError("offline solver")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("offline verification")):
        restored = Session.from_workspace(path, tmp_path / "restored")
        from ciw.system_cli import summary
        overview = summary(restored)
    assert restored.results == session.results
    assert path.read_bytes() == before
    assert overview["physical_validation_status"] == "not_assessed"


def test_forged_study_plan_is_a_retained_refusal(tmp_path):
    _, session, plan = _fixture(tmp_path)
    foreign = deepcopy(plan)
    foreign["result_id"] = new_identity("result")
    foreign["execution_id"] = new_identity("execution")
    seal(foreign)
    reply = _call(session, workflow.STUDY, {"plan": foreign, "source_result_id": foreign["result_id"]})
    assert reply["status"] == "refused"
    assert reply["result"] is None
    assert set(session.results) == {plan["result_id"]}
    restored = Session.from_workspace(session.save_workspace(tmp_path / "workspace.json"), tmp_path / "restored")
    assert restored.executions == session.executions


def test_repeated_studies_retain_fresh_occurrences_with_reproducible_content(tmp_path):
    _, session, plan = _fixture(tmp_path)
    first = _study(session, plan)
    second = _study(session, plan)
    assert first["result_id"] != second["result_id"]
    assert first["execution_id"] != second["execution_id"]
    assert first["data"] == second["data"]


@pytest.mark.parametrize("mutation", ["authority", "plan_binding", "clock_request"])
def test_resealed_study_forgery_refuses_before_restore_writes(tmp_path, mutation):
    spec, session, plan = _fixture(tmp_path / "initial")
    record = _study(session, plan, step_sizes_s=[5.0, 2.5])
    path = session.save_workspace(tmp_path / "initial" / "workspace.json")
    saved = read_json(path)
    result = next(x for x in saved["results"] if x["result_id"] == record["result_id"])
    if mutation == "authority":
        result["data"]["report"]["canonical_admission"] = True
        seal(result["data"]["report"])
    elif mutation == "plan_binding":
        result["data"]["plan_result_id"] = new_identity("result")
    else:
        from ciw.system_study import run_temporal_study
        result["data"]["report"] = run_temporal_study(spec, [5.0, 1.25])
    seal(result)
    write_json(path, saved)
    destination = tmp_path / "restore-rejected"
    with pytest.raises(ValueError):
        Session.from_workspace(path, destination)
    assert not destination.exists()
