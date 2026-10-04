"""Scientific system attachment to existing NET Session and identity contracts.

Source configurations are evidence; compilation, simulation, comparison and
verification are distinct retained operations. Saved inspection never solves.
"""
from copy import deepcopy
from pathlib import Path
import uuid

from .core.identities import canonical_json, content_identity, evidence_id, validate_identity
from .core.records import finite_tree
from .operations.registry import Operation
from .operations.runner import check_seal, seal
from .system_runtime import runtime_identity

COMPILE = "system.compile.v1"
RUN = "system.simulate.v1"
VERIFY = "system.verify.v1"
COMPARE = "system.compare.v1"
STUDY = "system.study.v1"
OPERATION_IDS = (COMPILE, RUN, VERIFY, COMPARE, STUDY)


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError("System operation fields differ from the declared contract")


def source_run(specifications):
    from .system_spec import validate_spec
    from .adapters.protocol import InstrumentManifest
    specs = [validate_spec(spec) for spec in specifications]
    if not specs or len(specs) > 16 or len({s["frame"] for s in specs}) != 1:
        raise ValueError("Require 1 to 16 specifications in a shared declared frame")
    if len({content_identity(s) for s in specs}) != len(specs):
        raise ValueError("Repeated source specification")
    run = {"run_schema": "run.v1", "run_id": "run-system-" + uuid.uuid4().hex,
           "instrument": "scientific-system.specification.v1",
           "metadata": {"duration_s": 1.0, "sample_count": 1,
                        "coordinate_frame": specs[0]["frame"], "system_specifications": specs,
                        "provenance": {"source": "declared scientific configurations",
                                       "experimental_observations": False}},
           "time_s": [0.0], "channels": {"configuration_count": {"unit": "1", "values": [len(specs)]}},
           "render": {}}
    run["metadata"]["manifest"] = InstrumentManifest(
        instrument_id=run["instrument"], role="scientific_specification",
        units={"configuration_count": "1"}, frames=(specs[0]["frame"],),
        supported_operations=OPERATION_IDS, sampling={"kind": "specification_snapshot"}).to_dict()
    run["evidence_id"] = evidence_id(run)
    return run


def _source_spec(run, spec):
    from .system_spec import validate_spec
    spec = validate_spec(spec)
    if spec["frame"] != run["metadata"]["coordinate_frame"] or not any(
            content_identity(item) == content_identity(spec)
            for item in run["metadata"].get("system_specifications", [])):
        raise ValueError("Specification does not bind this Session source evidence")
    return spec


def _compile_specification(run, parameters):
    _keys(parameters, {"specification"}, {"source_id", "source"})
    present = {key for key in ("source_id", "source") if key in parameters}
    if not present:
        return _source_spec(run, parameters["specification"])
    if present != {"source_id", "source"}:
        raise ValueError("Retained compilation requires source_id and full source together")
    from .system_source import validate_source
    spec = validate_source(parameters["source"], parameters["specification"])
    if parameters["source_id"] != parameters["source"]["source_id"]:
        raise ValueError("Compilation source_id differs from the retained descriptor")
    if spec["frame"] != run["metadata"]["coordinate_frame"]:
        raise ValueError("Retained specification frame differs from Session evidence")
    return spec


def _record(record, run, operation_id):
    check_seal(record)
    validate_identity(record.get("result_id"), "result")
    validate_identity(record.get("execution_id"), "execution")
    if (record.get("schema") != "ciw.operation-result.v1" or record.get("operation_id") != operation_id
            or record.get("role") != "backend" or record.get("run_id") != run["run_id"]
            or record.get("evidence_id") != run["evidence_id"]
            or record.get("verification_id") is not None or record.get("verification_status") != "not_verified"):
        raise ValueError("Upstream system result identity or evidence binding differs")
    return record


def _plan_record(run, record):
    from .system_spec import compile_spec
    record = _record(record, run, COMPILE)
    expected = compile_spec(_compile_specification(run, record["parameters"]))
    if record["data"] != expected:
        raise ValueError("Upstream compiled plan differs from its source specification")
    return expected


def _run_parameters(run, parameters):
    _keys(parameters, {"plan", "source_result_id"}, {"engine", "image", "timeout_s"})
    if parameters["source_result_id"] != parameters["plan"]["result_id"]:
        raise ValueError("Simulation source_result_id differs from its compiled result")
    plan = _plan_record(run, parameters["plan"])
    if parameters.get("engine", "local") not in {"local", "subprocess", "oci"}:
        raise ValueError("Unsupported deployment engine")
    if parameters.get("engine", "local") != "oci" and "image" in parameters:
        raise ValueError("Image applies only to an explicit OCI execution")
    if parameters.get("engine", "local") == "local" and "timeout_s" in parameters:
        raise ValueError("Local reference execution has no external worker timeout")
    return plan


def _run_candidate(run, record):
    record = _record(record, run, RUN)
    plan = _run_parameters(run, record["parameters"])
    _validate_simulation_payload(record["data"], plan, record["parameters"])
    if (record["parameters"].get("engine", "local") == "local"
            and record["data"]["execution_runtime"]["runtime"] != record["runtime"]):
        raise ValueError("Local deployment receipt differs from retained execution runtime")
    return plan, record["data"]["simulation"]


def _validate_simulation_payload(data, plan, parameters):
    _keys(data, {"schema", "plan_digest", "simulation", "execution_runtime"})
    if data["schema"] != "ciw.system-run-payload.v1" or data["plan_digest"] != plan["plan_digest"]:
        raise ValueError("System run payload plan binding differs")
    candidate = data["simulation"]
    check_seal(candidate)
    from .system_models import validate_candidate
    validate_candidate(plan, candidate)
    if (candidate.get("schema") != "ciw.system-simulation.v1" or candidate.get("spec_digest") != plan["spec_digest"]
            or candidate.get("configuration_digest") != plan["configuration_digest"]
            or candidate.get("plan_digest") != plan["plan_digest"]
            or candidate.get("canonical_admission") is not False
            or candidate.get("physical_validation_status") != "not_assessed"):
        raise ValueError("Simulation source, configuration or authority binding differs")
    if not isinstance(data["execution_runtime"], dict) or not data["execution_runtime"]:
        raise ValueError("Missing execution deployment receipt")
    receipt = data["execution_runtime"]
    expected_engine = {"local": "local", "subprocess": "subprocess", "oci": "container"}[parameters.get("engine", "local")]
    if receipt.get("engine") != expected_engine:
        raise ValueError("Deployment receipt differs from requested engine")
    expected_resources = plan["specification"]["execution"]["resources"]
    if (receipt.get("resources") != expected_resources
            or receipt.get("resource_limits_enforced") is not (expected_engine == "container")):
        raise ValueError("Deployment receipt resource declaration differs")
    if expected_engine == "local" and receipt != {
            "engine": "local", "runtime": _local_runtime(receipt), "resources": expected_resources,
            "resource_limits_enforced": False, "timeout_s": None}:
        raise ValueError("Local deployment receipt fields differ")
    if expected_engine == "container" and receipt.get("image_reference") != parameters.get("image"):
        raise ValueError("Deployment receipt image differs from requested image")
    finite_tree(data, "system simulation")


def _local_runtime(receipt):
    # Historical source hashes remain historical when code is upgraded.
    runtime = receipt.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("provider") != "ciw.scientific-system":
        raise ValueError("Local deployment receipt needs the reference runtime identity")
    return runtime


def _report_coherent(report):
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Numerical report requires nonempty check records")
    allowed = {"PASS", "FAIL", "NOT_ASSESSED"}
    for check in checks:
        if not isinstance(check, dict) or check.get("status") not in allowed:
            raise ValueError("Numerical check has an unsupported disposition")
        for key in ("value", "limit"):
            if check.get(key) is not None and type(check[key]) not in (int, float):
                raise ValueError("Numerical check value and limit require real numbers")
        if check.get("value") is not None and check.get("limit") is not None:
            if check.get("status") != ("PASS" if check["value"] <= check["limit"] else "FAIL"):
                raise ValueError("Numerical check value contradicts its disposition")
    expected = "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    if report.get("status") != expected:
        raise ValueError("Numerical report status contradicts retained checks")


def _compile(run, parameters):
    from .system_spec import compile_spec
    return compile_spec(_compile_specification(run, parameters))


def _simulate(run, parameters):
    from .system_models import simulate
    plan = _run_parameters(run, parameters)
    engine = parameters.get("engine", "local")
    if engine == "local":
        candidate, receipt = simulate(plan), {"engine": "local", "runtime": runtime_identity(),
            "resources": deepcopy(plan["specification"]["execution"]["resources"]),
            "resource_limits_enforced": False, "timeout_s": None}
    else:
        from .system_execution import execute_worker
        worker = execute_worker(plan["specification"], engine=engine,
                                image=parameters.get("image"), timeout_s=parameters.get("timeout_s", 60))
        candidate, receipt = worker["candidate"], worker["execution_runtime"]
    return {"schema": "ciw.system-run-payload.v1", "plan_digest": plan["plan_digest"],
            "simulation": candidate, "execution_runtime": receipt}


def _verification_inputs(run, parameters):
    _keys(parameters, {"candidate", "source_result_id"})
    if parameters["source_result_id"] != parameters["candidate"]["result_id"]:
        raise ValueError("Verification source result differs from candidate")
    return _run_candidate(run, parameters["candidate"])


def _verify(run, parameters):
    from .system_models import verify_simulation
    plan, candidate = _verification_inputs(run, parameters)
    record = parameters["candidate"]
    return {"schema": "ciw.system-verification-payload.v1", "verification_id": "verification-" + uuid.uuid4().hex,
            "source_evidence_id": run["evidence_id"], "candidate_result_id": record["result_id"],
            "candidate_execution_id": record["execution_id"], "candidate_record_digest": record["record_digest"],
            "report": verify_simulation(plan, candidate)}


def _comparison_inputs(run, parameters):
    _keys(parameters, {"left", "right", "source_result_id", "source_result_ids"})
    ids = [parameters["left"]["result_id"], parameters["right"]["result_id"]]
    if parameters["source_result_id"] != ids[0] or parameters["source_result_ids"] != ids or ids[0] == ids[1]:
        raise ValueError("Comparison source result bindings differ")
    return _run_candidate(run, parameters["left"]), _run_candidate(run, parameters["right"])


def _compare(run, parameters):
    from .system_models import compare_configurations
    (left_plan, left), (right_plan, right) = _comparison_inputs(run, parameters)
    return {"schema": "ciw.system-comparison-payload.v1",
            "left_result_id": parameters["left"]["result_id"], "right_result_id": parameters["right"]["result_id"],
            "left_record_digest": parameters["left"]["record_digest"],
            "right_record_digest": parameters["right"]["record_digest"],
            "report": compare_configurations(left_plan, left, right_plan, right)}


def _study_inputs(run, parameters):
    _keys(parameters, {"plan", "source_result_id"}, {"step_sizes_s"})
    if parameters["source_result_id"] != parameters["plan"]["result_id"]:
        raise ValueError("Temporal study source_result_id differs from its compiled result")
    return _plan_record(run, parameters["plan"])


def _study(run, parameters):
    from .system_study import run_temporal_study
    plan = _study_inputs(run, parameters)
    return {"schema": "ciw.system-study-payload.v1",
            "plan_result_id": parameters["plan"]["result_id"],
            "plan_record_digest": parameters["plan"]["record_digest"],
            "report": run_temporal_study(plan["specification"], step_sizes_s=parameters.get("step_sizes_s"))}


def operations():
    return [Operation(COMPILE, "backend", _compile, runtime_identity),
            Operation(RUN, "backend", _simulate, runtime_identity),
            Operation(VERIFY, "verification", _verify, runtime_identity),
            Operation(COMPARE, "backend", _compare, runtime_identity),
            Operation(STUDY, "backend", _study, runtime_identity)]


def validate_payload(operation_id, data, run, parameters, selection):
    """Offline binding inspection; no scientific solver or fresh verifier runs."""
    if operation_id == COMPILE:
        if data != _compile(run, parameters):
            raise ValueError("Retained system plan differs from deterministic compilation")
    elif operation_id == RUN:
        _validate_simulation_payload(data, _run_parameters(run, parameters), parameters)
    elif operation_id == VERIFY:
        plan, candidate = _verification_inputs(run, parameters)
        record = parameters["candidate"]
        _keys(data, {"schema", "verification_id", "source_evidence_id", "candidate_result_id",
                     "candidate_execution_id", "candidate_record_digest", "report"})
        validate_identity(data["verification_id"], "verification")
        if (data["schema"] != "ciw.system-verification-payload.v1"
                or data["source_evidence_id"] != run["evidence_id"]
                or data["candidate_result_id"] != record["result_id"]
                or data["candidate_execution_id"] != record["execution_id"]
                or data["candidate_record_digest"] != record["record_digest"]):
            raise ValueError("Retained system verification binds another occurrence")
        check_seal(data["report"])
        report = data["report"]
        _report_coherent(report)
        if (report.get("schema") != "ciw.system-numerical-verification.v1"
                or report.get("candidate_digest") != candidate["record_digest"]
                or any(report.get(key) != plan[key] for key in ("spec_digest", "configuration_digest", "plan_digest"))
                or report.get("status") not in {"PASS", "FAIL", "NOT_ASSESSED"}
                or report.get("physical_validation_status") != "not_assessed"
                or data["report"].get("canonical_admission") is not False):
            raise ValueError("Numerical report binding, scope or authority differs")
    elif operation_id == COMPARE:
        (left_plan, left), (right_plan, right) = _comparison_inputs(run, parameters)
        _keys(data, {"schema", "left_result_id", "right_result_id", "left_record_digest", "right_record_digest", "report"})
        for side in ("left", "right"):
            if (data[side + "_result_id"] != parameters[side]["result_id"]
                    or data[side + "_record_digest"] != parameters[side]["record_digest"]):
                raise ValueError("Retained comparison binds another occurrence")
        if data["schema"] != "ciw.system-comparison-payload.v1":
            raise ValueError("Unsupported comparison payload schema")
        check_seal(data["report"])
        report = data["report"]
        _report_coherent(report)
        ordered = sorted(((left_plan, left), (right_plan, right)),
                         key=lambda pair: pair[0]["specification"]["discretization"]["cells"], reverse=True)
        if report.get("schema") != "ciw.system-reduction-comparison.v1":
            raise ValueError("Unsupported reduction report schema")
        for prefix, (plan, candidate) in zip(("fine", "coarse"), ordered):
            if (report.get(prefix + "_plan_digest") != plan["plan_digest"]
                    or report.get(prefix + "_candidate_digest") != candidate["record_digest"]
                    or report.get(prefix + "_configuration_digest") != plan["configuration_digest"]):
                raise ValueError("Reduction report binds another configuration or candidate")
        fine_plan, coarse_plan = ordered[0][0], ordered[1][0]
        declared = [mapping for mapping in fine_plan["representations"]
                    if mapping["representation_id"] == report.get("representation_id")
                    and mapping["target_cells"] == coarse_plan["mesh"]["cells"]]
        if len(declared) != 1 or report.get("mapping_digest") != content_identity({
                "representation": declared[0], "fine_configuration_digest": fine_plan["configuration_digest"],
                "coarse_configuration_digest": coarse_plan["configuration_digest"]}):
            raise ValueError("Reduction report differs from its declared representation contract")
        if report.get("canonical_admission") is not False or report.get("physical_validation_status") != "not_assessed":
            raise ValueError("Reduction report cannot grant physical or canonical authority")
    elif operation_id == STUDY:
        from .system_study import normalized_step_sizes, validate_temporal_study
        plan = _study_inputs(run, parameters)
        _keys(data, {"schema", "plan_result_id", "plan_record_digest", "report"})
        if (data["schema"] != "ciw.system-study-payload.v1"
                or data["plan_result_id"] != parameters["plan"]["result_id"]
                or data["plan_record_digest"] != parameters["plan"]["record_digest"]):
            raise ValueError("Retained temporal study binds another plan occurrence")
        validate_temporal_study(data["report"], plan["specification"])
        if data["report"]["step_sizes_s"] != normalized_step_sizes(plan["specification"], parameters.get("step_sizes_s")):
            raise ValueError("Temporal study report differs from the requested step sizes")
    else:
        raise ValueError("Unsupported system operation")


def validate_saved_dependencies(results, sources=None):
    """Every embedded source must exactly match the retained occurrence."""
    for result in results.values():
        operation_id = result.get("operation_id")
        fields = {RUN: ("plan",), VERIFY: ("candidate",), COMPARE: ("left", "right"), STUDY: ("plan",)}.get(operation_id, ())
        for field in fields:
            source = result["parameters"][field]
            if results.get(source["result_id"]) != source:
                raise ValueError("System retained dependency is absent or differs from embedded source")
        if operation_id == COMPILE:
            _retained_source_dependency(result["parameters"], sources or {})
        if (operation_id == RUN and result["parameters"].get("engine", "local") == "local"
                and result["data"]["execution_runtime"]["runtime"] != result["runtime"]):
            raise ValueError("Local deployment receipt differs from retained execution runtime")
        if operation_id == STUDY and result["data"]["report"]["runtime"] != result["runtime"]:
            raise ValueError("Temporal study runtime differs from retained execution runtime")


def _retained_source_dependency(parameters, sources):
    if "source_id" not in parameters and "source" not in parameters:
        return
    source = parameters.get("source")
    source_id = parameters.get("source_id")
    if (type(source) is not dict or type(source_id) is not str or source_id != source.get("source_id")
            or source_id not in sources or canonical_json(sources[source_id]) != canonical_json(source)):
        raise ValueError("System specification source is not exactly retained in this Session")


def validate_live_dependencies(operation_id, parameters, results, sources=None):
    if operation_id == COMPILE:
        sources = sources or {}
        _retained_source_dependency(parameters, sources)
        if "source_id" not in parameters and "source" not in parameters:
            from .system_source import _source as parse_source
            from .system_spec import validate_spec
            import base64
            specification_digest = content_identity(validate_spec(parameters.get("specification")))
            if any(source["kind"] == "system-specification"
                   and content_identity(parse_source(base64.b64decode(source["bytes_b64"], validate=True))) == specification_digest
                   for source in sources.values()):
                raise ValueError("A matching retained specification requires explicit source_id and source binding")
    fields = {RUN: ("plan",), VERIFY: ("candidate",), COMPARE: ("left", "right"), STUDY: ("plan",)}.get(operation_id, ())
    for field in fields:
        source = parameters.get(field)
        if not isinstance(source, dict) or results.get(source.get("result_id")) != source:
            raise ValueError("System upstream occurrence is not exactly retained in this Session")


def execute(session, operation_id, parameters):
    from .session import write_json
    reply = session.handle({"protocol_version": 1, "request_id": uuid.uuid4().hex,
                            "type": "operation.execute", "payload": {
                                "operation_id": operation_id, "parameters": parameters}})
    session.save_workspace(session.output_dir / "workspace.json")
    if reply["type"] != "response":
        raise ValueError(reply["payload"]["message"])
    if reply["payload"]["status"] != "completed":
        raise ValueError(reply["payload"]["execution"]["refusal"]["message"])
    return deepcopy(reply["payload"]["result"])


def run_specification(session, specification, *, engine="local", image=None, source_id=None, source=None):
    compile_parameters = {"specification": specification}
    if source_id is not None or source is not None:
        if source_id is None:
            source_id = source.get("source_id") if type(source) is dict else None
        if source is None:
            source = session.workbench.get_source(source_id)
        compile_parameters.update(source_id=source_id, source=source)
    plan = execute(session, COMPILE, compile_parameters)
    parameters = {"plan": plan, "source_result_id": plan["result_id"], "engine": engine}
    if image is not None:
        parameters["image"] = image
    candidate = execute(session, RUN, parameters)
    verification = execute(session, VERIFY, {"candidate": candidate, "source_result_id": candidate["result_id"]})
    return {"plan": plan, "candidate": candidate, "verification": verification}
