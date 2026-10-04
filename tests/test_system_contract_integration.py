"""Live publication and offline restore share the system runtime contract."""
from copy import deepcopy

from ciw.operations.registry import Operation, OperationRegistry
from ciw.operations.runner import check_seal, seal
from ciw.session import Session, read_json, write_json
from ciw.system_spec import demo_spec
from ciw import system_workflow as workflow


def _execute(session, operation_id, parameters):
    reply = session.handle({"protocol_version": 1, "request_id": "system-contract",
                            "type": "operation.execute", "payload": {
                                "operation_id": operation_id, "parameters": parameters}})
    assert reply["type"] == "response", reply
    return reply["payload"]


def _compiled_session(tmp_path, *, operations=None):
    spec = demo_spec()
    session = Session(workflow.source_run([spec]), tmp_path, operations=operations)
    compiled = _execute(session, workflow.COMPILE, {"specification": spec})
    assert compiled["status"] == "completed", compiled
    return session, compiled["result"]


def test_local_runtime_mismatch_is_refused_before_publication_and_remains_reopenable(tmp_path):
    registry = OperationRegistry()
    for operation in workflow.operations():
        if operation.operation_id == workflow.RUN:
            def mismatched_runtime():
                runtime = workflow.runtime_identity()
                runtime["version"] = "different-deployment"
                return runtime
            operation = Operation(operation.operation_id, operation.role,
                                  operation.execute, mismatched_runtime)
        registry.register(operation)
    session, plan = _compiled_session(tmp_path / "source", operations=registry)
    response = _execute(session, workflow.RUN, {
        "plan": plan, "source_result_id": plan["result_id"]})
    assert response["status"] == "refused", response
    assert response["result"] is None
    assert "Local deployment receipt differs" in response["execution"]["refusal"]["message"]
    check_seal(response["execution"])
    assert set(session.results) == {plan["result_id"]}
    assert len(session.executions) == 2
    path = session.save_workspace(tmp_path / "workspace.json")
    restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.results == session.results
    assert restored.executions == session.executions


def test_matching_historical_local_runtime_does_not_require_current_source_hashes(tmp_path):
    session, plan = _compiled_session(tmp_path / "source")
    response = _execute(session, workflow.RUN, {
        "plan": plan, "source_result_id": plan["result_id"]})
    assert response["status"] == "completed", response
    path = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(path)
    result = next(row for row in workspace["results"] if row["operation_id"] == workflow.RUN)
    result["runtime"]["source_files"] = {
        name: "sha256:" + "0" * 64 for name in result["runtime"]["source_files"]}
    result["data"]["execution_runtime"]["runtime"] = deepcopy(result["runtime"])
    seal(result)
    execution = next(row for row in workspace["executions"]
                     if row["execution_id"] == result["execution_id"])
    execution["runtime"] = deepcopy(result["runtime"])
    seal(execution)
    write_json(path, workspace)
    restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.results[result["result_id"]] == result
    assert restored.executions[execution["execution_id"]] == execution
