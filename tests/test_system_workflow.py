"""Scientific-system workflows preserve NET occurrence and evidence contracts.

These integration tests exercise numerical reference calculations and retained
identity boundaries. They do not establish experimental polymer validation.
"""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from ciw.core.identities import evidence_id, new_identity
from ciw.operations.runner import check_seal, seal
from ciw.session import Session, read_json, write_json
from ciw.system_spec import demo_spec
from ciw import system_workflow as workflow


def _request(session, operation, parameters):
    return session.handle({"protocol_version": 1, "request_id": "system-test",
                           "type": "operation.execute", "payload": {
                               "operation_id": operation, "parameters": parameters}})


def _completed(session, operation, parameters):
    response = _request(session, operation, parameters)
    assert response["type"] == "response", response
    payload = response["payload"]
    assert payload["status"] == "completed", payload
    return payload["result"]


def _refused(session, operation, parameters):
    response = _request(session, operation, parameters)
    assert response["type"] == "response", response
    payload = response["payload"]
    assert payload["status"] == "refused", payload
    assert payload["result"] is None
    assert payload["execution"]["refusal"]["message"].strip()
    check_seal(payload["execution"])
    return payload["execution"]


def _session(tmp_path, *specs):
    return Session(workflow.source_run(list(specs) or [demo_spec()]), tmp_path)


def _compile(session, spec):
    return _completed(session, workflow.COMPILE, {"specification": spec})


def _simulation(session, plan, **parameters):
    return _completed(session, workflow.RUN, {"plan": plan, "source_result_id": plan["result_id"], **parameters})


def _comparison(session, left, right):
    return _completed(session, workflow.COMPARE, {
        "left": left, "right": right, "source_result_id": left["result_id"],
        "source_result_ids": [left["result_id"], right["result_id"]]})


def _declare_reduction(spec, cells):
    if not any(item["target_cells"] == cells for item in spec["representations"]):
        spec["representations"].append({
            "representation_id": f"thermal-reduced-{cells}", "kind": "cell-average.v1",
            "source_node": "thermal", "target_cells": cells,
            "assumptions": ["Equal-volume averaging discards temperature gradients within cell blocks."]})
    return spec


def test_source_specifications_bind_evidence_without_experimental_authority():
    spec = demo_spec()
    run = workflow.source_run([spec])
    assert run["evidence_id"] == evidence_id(run)
    assert run["metadata"]["provenance"]["experimental_observations"] is False
    assert set(run["metadata"]["manifest"]["supported_operations"]) == set(workflow.OPERATION_IDS)
    spec["configuration_id"] = "caller-changed"
    assert run["metadata"]["system_specifications"][0]["configuration_id"] != "caller-changed"
    assert run["evidence_id"] == evidence_id(run)


def test_default_session_retains_compile_simulation_and_distinct_verification(tmp_path):
    spec = demo_spec()
    session = _session(tmp_path, spec)
    source_before = deepcopy(session.run)
    result = workflow.run_specification(session, spec)
    assert len(session.results) == len(session.executions) == 3
    occurrence_ids = []
    for name, operation, role in (("plan", workflow.COMPILE, "backend"),
                                 ("candidate", workflow.RUN, "backend"),
                                 ("verification", workflow.VERIFY, "verification")):
        record = result[name]
        check_seal(record)
        assert record["operation_id"] == operation and record["role"] == role
        assert record["evidence_id"] == session.run["evidence_id"]
        assert record["verification_status"] == "not_verified"
        assert record["verification_id"] is None
        occurrence_ids.extend((record["result_id"], record["execution_id"]))
    verification = result["verification"]["data"]
    occurrence_ids.append(verification["verification_id"])
    assert len(occurrence_ids) == len(set(occurrence_ids))
    assert verification["candidate_result_id"] == result["candidate"]["result_id"]
    assert verification["candidate_execution_id"] == result["candidate"]["execution_id"]
    assert verification["candidate_record_digest"] == result["candidate"]["record_digest"]
    assert verification["report"]["physical_validation_status"] == "not_assessed"
    assert verification["report"]["canonical_admission"] is False
    assert session.run == source_before


def test_offline_restore_never_simulates_verifies_compares_or_runs_worker(tmp_path):
    free, fixed = _declare_reduction(demo_spec(cells=16), 8), demo_spec(cells=8)
    session = _session(tmp_path / "initial", free, fixed)
    left = workflow.run_specification(session, free)
    right = workflow.run_specification(session, fixed)
    _comparison(session, left["candidate"], right["candidate"])
    path = session.save_workspace(tmp_path / "workspace.json")
    before = path.read_bytes()
    with patch("ciw.system_models.simulate", side_effect=AssertionError("offline simulation")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("offline verification")), \
         patch("ciw.system_models.compare_configurations", side_effect=AssertionError("offline comparison")), \
         patch("ciw.system_execution.execute_worker", side_effect=AssertionError("offline worker")):
        restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.results == session.results
    assert restored.executions == session.executions
    assert path.read_bytes() == before


def test_reproduction_reuses_content_with_fresh_occurrences_and_verification(tmp_path):
    spec = demo_spec()
    session = _session(tmp_path, spec)
    first, second = (workflow.run_specification(session, spec) for _ in range(2))
    assert first["plan"]["data"] == second["plan"]["data"]
    assert first["candidate"]["data"]["simulation"] == second["candidate"]["data"]["simulation"]
    for name in ("plan", "candidate", "verification"):
        for field in ("result_id", "execution_id", "record_digest"):
            assert first[name][field] != second[name][field]
    assert first["verification"]["data"]["verification_id"] != second["verification"]["data"]["verification_id"]
    assert first["verification"]["data"]["candidate_record_digest"] == first["candidate"]["record_digest"]
    assert second["verification"]["data"]["candidate_record_digest"] == second["candidate"]["record_digest"]


def test_foreign_configuration_is_a_retained_refusal(tmp_path):
    session = _session(tmp_path, demo_spec())
    execution = _refused(session, workflow.COMPILE, {"specification": demo_spec(boundary="fixed")})
    assert not session.results
    path = session.save_workspace(tmp_path / "workspace.json")
    restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.executions[execution["execution_id"]] == execution


@pytest.mark.parametrize("mutation", ["missing_occurrence", "different_retained_runtime", "wrong_source_result"])
def test_simulation_cannot_consume_forged_or_mismatched_upstream_plan(tmp_path, mutation):
    session = _session(tmp_path, demo_spec())
    plan = _compile(session, demo_spec())
    supplied = deepcopy(plan)
    source_id = supplied["result_id"]
    if mutation == "missing_occurrence":
        supplied["result_id"] = new_identity("result")
        supplied["execution_id"] = new_identity("execution")
        source_id = supplied["result_id"]
        seal(supplied)
    elif mutation == "different_retained_runtime":
        supplied["runtime"]["caller_forgery"] = True
        seal(supplied)
    else:
        source_id = new_identity("result")
    before = deepcopy(session.results)
    _refused(session, workflow.RUN, {"plan": supplied, "source_result_id": source_id})
    assert session.results == before
    path = session.save_workspace(tmp_path / "workspace.json")
    assert Session.from_workspace(path, tmp_path / "restored").executions == session.executions


def test_worker_failure_retains_attempt_and_does_not_publish_candidate(tmp_path):
    from ciw.system_execution import SystemExecutionError
    session = _session(tmp_path, demo_spec())
    plan = _compile(session, demo_spec())
    with patch("ciw.system_execution.execute_worker", side_effect=SystemExecutionError("test worker timeout")):
        execution = _refused(session, workflow.RUN, {"plan": plan, "source_result_id": plan["result_id"], "engine": "subprocess"})
    assert set(session.results) == {plan["result_id"]}
    assert "test worker timeout" in execution["refusal"]["message"]
    path = session.save_workspace(tmp_path / "workspace.json")
    assert Session.from_workspace(path, tmp_path / "restored").executions == session.executions


@pytest.mark.parametrize("mutation", ["different_report", "wrong_candidate_occurrence", "physical_authority"])
def test_resealed_verification_must_bind_exact_candidate_before_restore_writes(tmp_path, mutation):
    free, fixed = demo_spec(), demo_spec(boundary="fixed", force_n=0.0)
    session = _session(tmp_path / "initial", free, fixed)
    left = workflow.run_specification(session, free)
    right = workflow.run_specification(session, fixed)
    path = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(path)
    record = next(record for record in workspace["results"] if record["result_id"] == left["verification"]["result_id"])
    if mutation == "different_report":
        record["data"]["report"] = deepcopy(right["verification"]["data"]["report"])
    elif mutation == "wrong_candidate_occurrence":
        record["data"]["candidate_execution_id"] = right["candidate"]["execution_id"]
    else:
        record["data"]["report"]["physical_validation_status"] = "validated"
        record["data"]["report"]["canonical_admission"] = True
        seal(record["data"]["report"])
    seal(record)
    write_json(path, workspace)
    with patch("ciw.session.write_json") as writer:
        with pytest.raises(ValueError):
            Session.from_workspace(path, tmp_path / "rejected")
        writer.assert_not_called()


def test_resealed_comparison_cannot_transplant_report_for_another_reduction(tmp_path):
    free, fixed, alternative = _declare_reduction(demo_spec(cells=16), 8), demo_spec(cells=8), demo_spec(cells=4)
    session = _session(tmp_path / "initial", free, fixed, alternative)
    left = workflow.run_specification(session, free)["candidate"]
    right = workflow.run_specification(session, fixed)["candidate"]
    other = workflow.run_specification(session, alternative)["candidate"]
    original, other_result = _comparison(session, left, right), _comparison(session, left, other)
    path = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(path)
    record = next(record for record in workspace["results"] if record["result_id"] == original["result_id"])
    record["data"]["report"] = deepcopy(other_result["data"]["report"])
    seal(record)
    write_json(path, workspace)
    with patch("ciw.session.write_json") as writer:
        with pytest.raises(ValueError):
            Session.from_workspace(path, tmp_path / "rejected")
        writer.assert_not_called()


def test_embedded_upstream_record_must_match_retained_occurrence_exactly(tmp_path):
    session = _session(tmp_path / "initial", demo_spec())
    result = workflow.run_specification(session, demo_spec())
    path = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(path)
    candidate = next(record for record in workspace["results"] if record["result_id"] == result["candidate"]["result_id"])
    candidate["parameters"]["plan"]["runtime"]["forged_worker"] = "caller"
    seal(candidate["parameters"]["plan"])
    seal(candidate)
    execution = next(item for item in workspace["executions"] if item["execution_id"] == candidate["execution_id"])
    execution["parameters"] = deepcopy(candidate["parameters"])
    seal(execution)
    # Remove the downstream verification, ensuring this tests the upstream plan
    # occurrence gate rather than a later embedded candidate inconsistency.
    workspace["results"] = [item for item in workspace["results"] if item["operation_id"] != workflow.VERIFY]
    workspace["executions"] = [item for item in workspace["executions"] if item["operation_id"] != workflow.VERIFY]
    write_json(path, workspace)
    with patch("ciw.session.write_json") as writer:
        with pytest.raises(ValueError):
            Session.from_workspace(path, tmp_path / "rejected")
        writer.assert_not_called()


def test_dependency_projection_preserves_both_comparison_inputs_and_claims(tmp_path):
    free, fixed = _declare_reduction(demo_spec(cells=16), 8), demo_spec(cells=8)
    session = _session(tmp_path, free, fixed)
    left = workflow.run_specification(session, free)
    right = workflow.run_specification(session, fixed)
    comparison = _comparison(session, left["candidate"], right["candidate"])
    graph = session.dependency_status()["nodes"]
    assert left["plan"]["result_id"] in graph[left["candidate"]["result_id"]]["dependencies"]
    assert left["candidate"]["result_id"] in graph[left["verification"]["result_id"]]["dependencies"]
    assert {left["candidate"]["result_id"], right["candidate"]["result_id"]} <= set(graph[comparison["result_id"]]["dependencies"])
    response = session.handle({"protocol_version": 1, "request_id": "system-claim", "type": "claim.add", "payload": {
        "claim_type": "predicted", "predicate": "Configured rods have different axial response",
        "scope": "synthetic reference workload", "basis": "retained comparison only",
        "dependencies": [comparison["result_id"]]}})
    assert response["type"] == "response", response
    claim = response["payload"]
    assert claim["verification_id"] is None and claim["verification_status"] == "not_verified"
    assert claim["state_admission"] == "unadmitted" and claim["execution_authorized"] is False
    path = session.save_workspace(tmp_path / "workspace.json")
    restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.dependency_status() == session.dependency_status()


def test_cli_demo_inspection_and_fresh_numerical_verification(tmp_path, capsys):
    from ciw import cli, system_cli
    destination = tmp_path / "demo"
    assert cli.main(["system", "demo", "--output-dir", str(destination)]) == 0
    capsys.readouterr()
    assert (destination / "review.html").is_file()
    workspace = destination / "workspace.json"
    before = workspace.read_bytes()
    original = Session.from_workspace(workspace, tmp_path / "original-inspection")
    simulations = [item for item in original.results.values() if item["operation_id"] == workflow.RUN]
    assert len(simulations) == 5
    worker = next(item for item in simulations if item["data"]["execution_runtime"]["engine"] == "subprocess")
    local = next(item for item in simulations if item["data"]["execution_runtime"]["engine"] == "local"
                 and item["data"]["simulation"]["plan_digest"] == worker["data"]["simulation"]["plan_digest"])
    assert local["data"]["simulation"] == worker["data"]["simulation"]
    assert local["execution_id"] != worker["execution_id"]
    with patch("ciw.system_models.simulate", side_effect=AssertionError("inspection solver")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("inspection verifier")):
        assert system_cli.main(["inspect", str(workspace)]) == 0
    overview = json.loads(capsys.readouterr().out)
    assert overview["schema"] == "ciw.system-inspection.v1"
    assert overview["canonical_admission"] is False
    original_verifications = {item["data"]["verification_id"] for item in original.results.values()
                              if item["operation_id"] == workflow.VERIFY}
    with patch("ciw.system_execution.execute_worker", side_effect=AssertionError("verification must not launch deployment worker")):
        assert system_cli.main(["verify", str(workspace), "--output-dir", str(tmp_path / "verified")]) == 0
    capsys.readouterr()
    refreshed = Session.from_workspace(tmp_path / "verified" / "workspace.json", tmp_path / "refreshed-inspection")
    verification_ids = {item["data"]["verification_id"] for item in refreshed.results.values()
                        if item["operation_id"] == workflow.VERIFY}
    assert original_verifications < verification_ids
    assert len(verification_ids - original_verifications) == len(simulations)
    assert {item["result_id"] for item in refreshed.results.values() if item["operation_id"] == workflow.RUN} == {item["result_id"] for item in simulations}
    assert workspace.read_bytes() == before


def test_failed_scientific_check_is_retained_without_promoting_candidate_authority(tmp_path):
    spec = demo_spec(force_n=2000.0)
    session = _session(tmp_path, spec)
    result = workflow.run_specification(session, spec)
    assert result["verification"]["data"]["report"]["status"] == "FAIL"
    assert result["verification"]["data"]["report"]["canonical_admission"] is False
    assert result["candidate"]["verification_status"] == "not_verified"
    assert result["candidate"]["verification_id"] is None
    assert session.executions[result["verification"]["execution_id"]]["status"] == "completed"


def test_resealed_deployment_receipt_must_match_selected_execution_engine(tmp_path):
    session = _session(tmp_path / "initial", demo_spec())
    plan = _compile(session, demo_spec())
    candidate = _simulation(session, plan)
    path = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(path)
    record = next(item for item in workspace["results"] if item["result_id"] == candidate["result_id"])
    record["data"]["execution_runtime"]["engine"] = "container"
    record["data"]["execution_runtime"]["image_reference"] = "forged-image@sha256:" + "f" * 64
    seal(record)
    write_json(path, workspace)
    with patch("ciw.session.write_json") as writer:
        with pytest.raises(ValueError):
            Session.from_workspace(path, tmp_path / "rejected")
        writer.assert_not_called()


def test_resealed_numerical_report_cannot_claim_pass_when_checks_failed(tmp_path):
    spec = demo_spec(force_n=2000.0)
    session = _session(tmp_path / "initial", spec)
    result = workflow.run_specification(session, spec)
    assert result["verification"]["data"]["report"]["status"] == "FAIL"
    path = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(path)
    record = next(item for item in workspace["results"] if item["result_id"] == result["verification"]["result_id"])
    record["data"]["report"]["status"] = "PASS"
    seal(record["data"]["report"])
    seal(record)
    write_json(path, workspace)
    with patch("ciw.session.write_json") as writer:
        with pytest.raises(ValueError):
            Session.from_workspace(path, tmp_path / "rejected")
        writer.assert_not_called()
