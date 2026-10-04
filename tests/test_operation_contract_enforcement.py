"""Source integrity and trusted role contracts apply to every operation provider."""
from copy import deepcopy

import pytest

from ciw.core.identities import evidence_id
from ciw.instruments import make_demo_run
from ciw.operations.registry import Operation, OperationRegistry
from ciw.operations.runner import check_seal, execute, seal
from ciw.operations.schemas import register_payload_validator, validate_role
from ciw.session import Session, read_json, write_json


@pytest.mark.parametrize("fault", ["stale_identity", "duplicate_time", "truncated_channel", "invalid_unit"])
def test_direct_runner_refuses_invalid_source_before_provider_invocation(fault):
    run = make_demo_run()
    if fault == "stale_identity":
        run["channels"]["q"]["values"][0] += 10
    elif fault == "duplicate_time":
        run["time_s"][1] = run["time_s"][0]
    elif fault == "truncated_channel":
        run["channels"]["q"]["values"].pop()
    else:
        run["channels"]["q"]["unit"] = ""
    if fault != "stale_identity":
        run["evidence_id"] = evidence_id(run)
    calls = []
    registry = OperationRegistry()
    registry.register(Operation("statistics.v1", "analysis",
        lambda *args: calls.append("execute"), lambda: calls.append("runtime")))
    selection = {"revision": 0, "channel": "q", "interval_s": [0.0, run["metadata"]["duration_s"]]}
    execution, result = execute(registry, run, selection, "recording.json", "statistics.v1", {})
    assert execution["status"] == "refused" and result is None and calls == []
    assert execution["runtime"] is None
    check_seal(execution)


def test_schema_less_provider_cannot_execute_before_publication_refusal():
    calls = []
    registry = OperationRegistry()
    registry.register(Operation("contract-enforcement.unregistered.v1", "verification",
        lambda *args: calls.append("execute"), lambda: calls.append("runtime")))
    run = make_demo_run()
    selection = {"revision": 0, "channel": "q", "interval_s": [0.0, run["metadata"]["duration_s"]]}
    execution, result = execute(registry, run, selection, "recording.json",
                                "contract-enforcement.unregistered.v1", {})
    assert execution["status"] == "refused" and result is None and calls == []
    assert "schema and role" in execution["refusal"]["message"]


@pytest.mark.parametrize("role", ["analysis", "state_estimator", "calibration", "verification", "decision", "backend"])
def test_extension_role_is_bound_with_its_trusted_payload_schema(role):
    operation_id = f"contract-enforcement.{role}.v1"
    register_payload_validator(operation_id, lambda *args: None, role=role)
    validate_role(operation_id, role)
    for other in {"analysis", "state_estimator", "calibration", "verification", "decision", "backend"} - {role}:
        with pytest.raises(ValueError, match="role contradicts"):
            validate_role(operation_id, other)
    with pytest.raises(ValueError, match="already registered"):
        register_payload_validator(operation_id, lambda *args: None, role="backend")


def test_existing_extension_registration_defaults_to_backend_only():
    operation_id = "contract-enforcement.default.v1"
    register_payload_validator(operation_id, lambda *args: None)
    validate_role(operation_id, "backend")
    with pytest.raises(ValueError, match="role contradicts"):
        validate_role(operation_id, "verification")


@pytest.mark.parametrize("operation_id", ["impact.spring-contact.v1", "fluid.wave.verify.v1", "polymer.assess-cycle.v1"])
def test_all_builtin_schema_roles_are_protected_from_extension_registration(operation_id):
    with pytest.raises(ValueError, match="already registered"):
        register_payload_validator(operation_id, lambda *args: None)


@pytest.mark.parametrize("role", [None, "admitted", [], True])
def test_extension_registration_rejects_invalid_role(role):
    with pytest.raises(ValueError, match="supported role"):
        register_payload_validator("contract-enforcement.invalid.v1", lambda *args: None, role=role)


@pytest.mark.parametrize("forged_role", ["verification", "decision"])
def test_historical_authoring_result_cannot_be_reclassified_on_reopen(tmp_path, forged_role):
    from ciw import historical_perspective as h, perspective_workflow as workflow
    source = workflow.run_case(h.example(), tmp_path / "initial", at_tick=4)
    saved_path = tmp_path / "initial" / "workspace.json"
    valid = Session.from_workspace(saved_path, tmp_path / "valid")
    assert valid.results == source.results
    workspace = deepcopy(read_json(saved_path))
    workspace["results"][0]["role"] = forged_role
    seal(workspace["results"][0])
    write_json(saved_path, workspace)
    with pytest.raises(ValueError, match="role contradicts"):
        Session.from_workspace(saved_path, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_dsp_schema_declares_analysis_role_without_loading_native_library():
    from ciw import dsp
    dsp.register_schemas()
    validate_role(dsp.OPERATION, "analysis")
    with pytest.raises(ValueError, match="role contradicts"):
        validate_role(dsp.OPERATION, "verification")


@pytest.mark.parametrize("fault", ["unit", "frame", "instrument"])
def test_direct_runner_checks_embedded_source_contract_before_provider(fault):
    from ciw.adapters.protocol import InstrumentManifest
    manifest = InstrumentManifest("contract-enforcement.record.v1", role="record_only",
                                  units={"q": "m"}, frames=("test/world",))
    run = {"run_schema": "run.v1", "run_id": "run-contract-enforcement",
           "instrument": manifest.instrument_id, "time_s": [0.0],
           "channels": {"q": {"unit": "m", "values": [1.0]}},
           "metadata": {"duration_s": 1.0, "sample_count": 1,
                        "coordinate_frame": "test/world", "manifest": manifest.to_dict(),
                        "provenance": {"semantics": "reference"}}}
    if fault == "unit":
        run["channels"]["q"]["unit"] = "kg"
    elif fault == "frame":
        run["metadata"]["coordinate_frame"] = "other/world"
    else:
        run["instrument"] = "contract-enforcement.other.v1"
    run["evidence_id"] = evidence_id(run)
    operation_id = "contract-enforcement.source-" + fault + ".v1"
    register_payload_validator(operation_id, lambda *args: None)
    calls = []
    registry = OperationRegistry()
    registry.register(Operation(operation_id, "backend", lambda *args: calls.append("execute"),
                                lambda: calls.append("runtime")))
    selection = {"revision": 0, "channel": "q", "interval_s": [0.0, 1.0]}
    execution, result = execute(registry, run, selection, "recording.json", operation_id, {})
    assert execution["status"] == "refused" and result is None and calls == []
    assert execution["runtime"] is None
    check_seal(execution)

@pytest.mark.parametrize("operation_id,role", [
    ("irrigation.plan.v1", "backend"),
    ("irrigation.verify.v1", "verification"),
    ("leakage.assess-balance.v1", "backend"),
    ("leakage.verify-balance.v1", "verification"),
])
def test_integrated_irrigation_and_leakage_keep_their_trusted_roles(operation_id, role):
    validate_role(operation_id, role)
    with pytest.raises(ValueError, match="role contradicts"):
        validate_role(operation_id, "analysis")
    with pytest.raises(ValueError, match="already registered"):
        register_payload_validator(operation_id, lambda *args: None, role=role)

@pytest.mark.parametrize("operation_id,role", [
    ("system.compile.v1", "backend"), ("system.simulate.v1", "backend"),
    ("system.compare.v1", "backend"), ("system.study.v1", "backend"),
    ("system.verify.v1", "verification"),
])
def test_integrated_system_operations_reserve_their_trusted_roles(operation_id, role):
    validate_role(operation_id, role)
    with pytest.raises(ValueError, match="role contradicts"):
        validate_role(operation_id, "analysis")
    with pytest.raises(ValueError, match="already registered"):
        register_payload_validator(operation_id, lambda *args: None, role=role)
