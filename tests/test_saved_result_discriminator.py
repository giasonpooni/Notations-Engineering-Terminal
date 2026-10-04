"""Unsupported result envelopes cannot downgrade to the legacy analysis reader."""
import pytest

from ciw.instruments import make_demo_run
from ciw.operations.runner import seal
from ciw.session import Session, read_json, write_json


def request(session, kind, payload):
    response = session.handle({"protocol_version": 1, "request_id": "format-audit",
                               "type": kind, "payload": payload})
    assert response["type"] == "response"
    return response["payload"]


@pytest.mark.parametrize("schema", ["ciw.operation-result.v999", "", None, False, "removed"])
def test_modern_result_cannot_bypass_role_and_execution_binding(tmp_path, schema):
    session = Session(make_demo_run(), tmp_path / "original")
    request(session, "operation.execute", {"operation_id": "statistics.v1"})
    saved = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(saved)
    result = workspace["results"][0]
    if schema == "removed":
        del result["schema"]
    else:
        result["schema"] = schema
    result["role"] = "verification"
    seal(result)
    workspace["executions"] = []
    write_json(saved, workspace)
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="schema|legacy analysis result format"):
        Session.from_workspace(saved, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["analysis.stats", "analysis.spectrum"])
def test_genuine_legacy_results_remain_readable(tmp_path, kind):
    session = Session(make_demo_run(), tmp_path / "original")
    request(session, kind, {})
    saved = session.save_workspace(tmp_path / "workspace.json")
    restored = Session.from_workspace(saved, tmp_path / "restored")
    assert restored.results == session.results
    assert not restored.executions


@pytest.mark.parametrize("field,value", [("role", "verification"),
                                          ("state_admission", "admitted"),
                                          ("runtime", {"provider": "forged"})])
@pytest.mark.parametrize("legacy", [False, True])
def test_result_readers_reject_undeclared_authority_fields(tmp_path, field, value, legacy):
    session = Session(make_demo_run(), tmp_path / "original")
    if legacy:
        request(session, "analysis.stats", {})
    else:
        request(session, "operation.execute", {"operation_id": "statistics.v1"})
    saved = session.save_workspace(tmp_path / "workspace.json")
    workspace = read_json(saved)
    result = workspace["results"][0]
    result[field] = value
    if not legacy:
        seal(result)
    write_json(saved, workspace)
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError):
        Session.from_workspace(saved, destination)
    assert not destination.exists()
