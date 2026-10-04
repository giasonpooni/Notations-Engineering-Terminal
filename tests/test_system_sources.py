"""Exact retained specification bindings and transitive correction projections."""
import base64
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from ciw.core.identities import canonical_json, content_identity
from ciw.operations.runner import seal
from ciw.session import Session, read_json, write_json
from ciw.system_source import MAX_BYTES, _source
from ciw.system_spec import demo_spec
from ciw import system_workflow as workflow


def _call(session, kind, payload):
    reply = session.handle({"protocol_version": 1, "request_id": "system-source-test",
                            "type": kind, "payload": payload})
    assert reply["type"] == "response", reply
    return reply["payload"]


def _add(session, spec, label="Declared polymer configuration", raw=None):
    raw = json.dumps(spec, indent=2).encode() if raw is None else raw
    descriptor = _call(session, "source.add", {"kind": "system-specification", "label": label,
                                              "bytes_b64": base64.b64encode(raw).decode("ascii")})
    return session.workbench.get_source(descriptor["source_id"])


def _parameters(spec, source):
    return {"specification": spec, "source_id": source["source_id"], "source": source}


def _attempt(session, params):
    return _call(session, "operation.execute", {"operation_id": workflow.COMPILE, "parameters": params})


def _session(tmp_path, spec=None):
    return Session(workflow.source_run([demo_spec() if spec is None else spec]), tmp_path)


def test_import_is_data_only_and_retains_raw_and_normalized_identities(tmp_path):
    spec = demo_spec()
    session = _session(tmp_path)
    raw = json.dumps(spec, indent=2).encode()
    with patch("ciw.system_models.simulate", side_effect=AssertionError("import solver")):
        source = _add(session, spec, raw=raw)
    assert source["source_schema"] == "ciw.system-spec.v1"
    assert source["evidence_id"] != content_identity(spec)
    assert base64.b64decode(source["bytes_b64"]) == raw
    assert not session.results and not session.executions
    canonical = _add(session, spec, label="Same declaration in canonical JSON", raw=canonical_json(spec).encode())
    assert canonical["evidence_id"] == content_identity(spec)
    assert source["source_id"] != canonical["source_id"]
    assert _source(raw) == spec
    path = session.save_workspace(tmp_path / "workspace.json")
    assert Session.from_workspace(path, tmp_path / "restored").workbench.retained_sources() == session.workbench.retained_sources()


@pytest.mark.parametrize("raw", [
    b"{}", b'{"schema":"ciw.system-spec.v1","schema":"ciw.system-spec.v1"}',
    b'{"value":NaN}', b" " * (MAX_BYTES + 1),
], ids=["missing_schema", "duplicate_key", "nonfinite", "oversized"])
def test_source_import_refuses_malformed_nonfinite_duplicate_or_unbounded_json(raw):
    with pytest.raises(ValueError):
        _source(raw)


def test_retained_configuration_can_extend_the_initial_session_snapshot(tmp_path):
    session = _session(tmp_path)
    additional = demo_spec(boundary="fixed")
    source = _add(session, additional)
    executed = workflow.run_specification(session, additional, source_id=source["source_id"])
    assert executed["plan"]["parameters"]["source"] == source
    assert executed["plan"]["data"]["spec_digest"] == content_identity(additional)
    report = executed["verification"]["data"]["report"]
    assert report["physical_validation_status"] == "not_assessed"
    assert report["canonical_admission"] is False
    assert session.run["metadata"]["system_specifications"] == [demo_spec()]


@pytest.mark.parametrize("mutation", ["not_retained", "wrong_kind", "wrong_schema", "wrong_evidence", "wrong_bytes", "wrong_spec", "numeric_alias", "missing_id", "missing_source", "malformed_id"])
def test_compile_rejects_forged_or_incomplete_retained_binding(tmp_path, mutation):
    spec = demo_spec()
    session = _session(tmp_path)
    source = _add(session, spec)
    parameters = _parameters(spec, deepcopy(source))
    if mutation == "not_retained":
        parameters["source"]["label"] = "Not retained here"
        from ciw.workbench import _source as prepare
        forged = prepare({key: parameters["source"][key] for key in ("kind", "label", "bytes_b64")})
        parameters.update(source=forged, source_id=forged["source_id"])
    elif mutation == "wrong_kind":
        parameters["source"]["kind"] = "reference-evidence"
    elif mutation == "wrong_schema":
        parameters["source"]["source_schema"] = "ciw.reference-evidence.v1"
    elif mutation == "wrong_evidence":
        parameters["source"]["evidence_id"] = "sha256:" + "a" * 64
    elif mutation == "wrong_bytes":
        parameters["source"]["bytes_b64"] = "e30="
    elif mutation == "wrong_spec":
        parameters["specification"] = demo_spec(force_n=6)
    elif mutation == "numeric_alias":
        parameters["source"]["byte_count"] = float(parameters["source"]["byte_count"])
    elif mutation == "missing_id":
        parameters.pop("source_id")
    elif mutation == "missing_source":
        parameters.pop("source")
    elif mutation == "malformed_id":
        parameters["source_id"] = {"source": "invalid"}
    before = deepcopy(session.results)
    refused = _attempt(session, parameters)
    assert refused["status"] == "refused" and refused["result"] is None
    assert session.results == before
    path = session.save_workspace(tmp_path / "workspace.json")
    assert Session.from_workspace(path, tmp_path / "restored").executions == session.executions


def test_restore_requires_exact_embedded_source_before_any_writes(tmp_path):
    spec = demo_spec()
    session = _session(tmp_path / "original")
    source = _add(session, spec)
    compiled = _attempt(session, _parameters(spec, source))["result"]
    path = session.save_workspace(tmp_path / "workspace.json")
    saved = read_json(path)
    saved["workbench"]["sources"] = []
    saved["workbench"]["revision"] = 0
    corrupt = write_json(tmp_path / "missing-source.json", saved)
    destination = tmp_path / "must-remain-empty"
    with pytest.raises(ValueError, match="not exactly retained"):
        Session.from_workspace(corrupt, destination)
    assert not destination.exists()
    saved = read_json(path)
    result = next(record for record in saved["results"] if record["result_id"] == compiled["result_id"])
    result["parameters"]["source"]["byte_count"] = float(source["byte_count"])
    seal(result)
    execution = saved["executions"][0]
    execution["parameters"] = deepcopy(result["parameters"])
    seal(execution)
    corrupt = write_json(tmp_path / "aliased-source.json", saved)
    with pytest.raises(ValueError, match="descriptor differs"):
        Session.from_workspace(corrupt, destination)
    assert not destination.exists()


def test_source_correction_reaches_computation_verification_and_comparison_without_rewriting(tmp_path):
    fine, coarse = demo_spec(16), demo_spec(4)
    session = _session(tmp_path / "original", fine)
    old = _add(session, fine, label="Original configuration")
    replacement = demo_spec(16, force_n=6)
    new = _add(session, replacement, label="Corrected configured load")
    coarse_source = _add(session, coarse, label="Reduced configuration")
    left = workflow.run_specification(session, fine, source_id=old["source_id"])
    right = workflow.run_specification(session, coarse, source_id=coarse_source["source_id"])
    comparison = workflow.execute(session, workflow.COMPARE, {
        "left": left["candidate"], "right": right["candidate"],
        "source_result_id": left["candidate"]["result_id"],
        "source_result_ids": [left["candidate"]["result_id"], right["candidate"]["result_id"]]})
    study = workflow.execute(session, workflow.STUDY, {"plan": left["plan"],
                                                     "source_result_id": left["plan"]["result_id"],
                                                     "step_sizes_s": [5.0, 2.5]})
    before = deepcopy(session.results)
    proposed = _call(session, "correction.propose", {"old_source_id": old["source_id"], "new_source_id": new["source_id"],
                                                     "kind": "configuration", "reason": "Revise the illustrative configured axial load."})
    _call(session, "correction.review", {"correction_id": proposed["correction_id"], "decision": "accept",
                                         "expected_revision": session.correction_journal.revision,
                                         "reviewer": "Declared test operator", "reason": "Review the replacement input."})
    statuses = session.dependency_status()["artifact_status"]
    for identity in (old["source_id"], *(record[field] for record in left.values() for field in ("result_id", "execution_id")),
                     comparison["result_id"], comparison["execution_id"], study["result_id"], study["execution_id"]):
        assert statuses[identity]["status"] == "stale"
    for identity in (new["source_id"], coarse_source["source_id"], *(record["result_id"] for record in right.values()),
                     old["evidence_id"], session.run["evidence_id"]):
        assert statuses[identity]["status"] == "current"
    assert session.results == before
    path = session.save_workspace(tmp_path / "workspace.json")
    with patch("ciw.system_models.simulate", side_effect=AssertionError("offline solver")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("offline verifier")), \
         patch("ciw.system_study.run_temporal_study", side_effect=AssertionError("offline study")):
        restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.dependency_status() == session.dependency_status()
    assert restored.results == before


def test_new_bare_compile_cannot_bypass_withdrawn_matching_source_but_saved_legacy_is_preserved(tmp_path):
    spec = demo_spec()
    session = _session(tmp_path / "original")
    legacy = workflow.execute(session, workflow.COMPILE, {"specification": spec})
    old = _add(session, spec, label="Retained original declaration")
    new = _add(session, demo_spec(force_n=6), label="Retained correction")
    proposal = _call(session, "correction.propose", {"old_source_id": old["source_id"], "new_source_id": new["source_id"],
                                                    "kind": "configuration", "reason": "Revise the configured load."})
    _call(session, "correction.review", {"correction_id": proposal["correction_id"], "decision": "accept",
                                         "expected_revision": session.correction_journal.revision,
                                         "reviewer": "Declared test operator", "reason": "Use the revised declaration."})
    refused = _attempt(session, {"specification": spec})
    assert refused["status"] == "refused"
    assert "explicit source_id and source" in refused["execution"]["refusal"]["message"]
    assert session.dependency_status()["artifact_status"][legacy["result_id"]]["status"] == "current"
    path = session.save_workspace(tmp_path / "workspace.json")
    restored = Session.from_workspace(path, tmp_path / "restored")
    assert restored.results[legacy["result_id"]] == legacy
    assert restored.dependency_status() == session.dependency_status()
