"""Execution envelopes preserve refusal separately from immutable result records."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone

from ..adapters.protocol import AdapterRefusal
from ..core.identities import validate_evidence_identity
from ..core.records import finite_tree
from ..instruments import validate_run
from .registry import OperationRegistry, valid_operation_id
from .schemas import validate_payload, validate_role, validate_request_dependencies

LOG = logging.getLogger(__name__)


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def seal(record: dict) -> dict:
    record["record_digest"] = digest({key: value for key, value in record.items() if key != "record_digest"})
    return record


def check_seal(record: dict) -> None:
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    ):
        raise ValueError("Operation record integrity mismatch")


def execute(registry: OperationRegistry, run: dict, selection: dict, recording_file: str,
            operation_id: str, parameters: dict, *, retained_results: dict | None = None,
            retained_sources: dict | None = None) -> tuple[dict, dict | None]:
    run = copy.deepcopy(run)
    selection = copy.deepcopy(selection)
    parameters = copy.deepcopy(parameters)
    finite_tree(parameters, "operation parameters")
    execution = {
        "schema": "ciw.execution.v1", "execution_id": "execution-" + uuid.uuid4().hex,
        "operation_id": operation_id, "evidence_id": run["evidence_id"],
        "run_id": run["run_id"], "selection_revision": selection["revision"],
        "channel": selection["channel"], "interval_s": copy.deepcopy(selection["interval_s"]),
        "parameters": copy.deepcopy(parameters), "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime": None, "status": "refused", "result_id": None,
    }
    try:
        # Direct runner callers share the Session source-integrity boundary.
        # Check before reading a provider runtime or invoking numerical code.
        validate_run(run)
        validate_evidence_identity(run)
        operation = registry.get(operation_id)
        validate_role(operation_id, operation.role)
        validate_request_dependencies(operation_id, parameters, {} if retained_results is None else retained_results)
        if operation_id in {"system.compile.v1", "system.simulate.v1", "system.verify.v1",
                            "system.compare.v1", "system.study.v1"}:
            from ..system_workflow import validate_live_dependencies
            validate_live_dependencies(operation_id, parameters,
                                       {} if retained_results is None else retained_results,
                                       {} if retained_sources is None else retained_sources)
        if operation.role == "analysis":
            parameters.setdefault("channel", selection["channel"])
            parameters.setdefault("interval_s", copy.deepcopy(selection["interval_s"]))
        for key in ("channel", "interval_s"):
            if key in parameters and parameters[key] != selection[key]:
                raise AdapterRefusal("selection_mismatch", "Operation parameters contradict the captured selection")
        execution["parameters"] = copy.deepcopy(parameters)
        runtime = copy.deepcopy(operation.runtime_identity())
        if not isinstance(runtime, dict) or not runtime:
            raise AdapterRefusal("invalid_runtime_identity", "An operation must identify its numerical runtime")
        finite_tree(runtime, "runtime identity")
        execution["runtime"] = runtime
        # A provider receives detached scientific evidence, never live mutable state.
        # Cached provider objects remain provider-owned. Detach the returned
        # payload before validating or retaining it as immutable evidence.
        data = copy.deepcopy(operation.execute(copy.deepcopy(run), copy.deepcopy(parameters)))
        if not isinstance(data, dict):
            raise AdapterRefusal("invalid_adapter_output", "Operation data must be an object")
        validate_payload(operation_id, data, run, parameters, selection)
        if (operation_id == "system.simulate.v1" and parameters.get("engine", "local") == "local"
                and data["execution_runtime"]["runtime"] != runtime):
            raise ValueError("Local deployment receipt differs from retained execution runtime")
        if operation_id == "system.study.v1" and data["report"]["runtime"] != runtime:
            raise ValueError("Temporal study runtime differs from retained execution runtime")
        json.dumps(data, allow_nan=False)
    except AdapterRefusal as exc:
        execution["refusal"] = exc.to_dict()
        if not execution["refusal"]["message"].strip():
            execution["refusal"]["message"] = type(exc).__name__ + " (no diagnostic message)"
        return seal(execution), None
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        message = str(exc)
        execution["refusal"] = {
            "code": "invalid_operation",
            "message": message if message.strip() else type(exc).__name__ + " (no diagnostic message)",
        }
        return seal(execution), None
    except Exception as exc:
        # Once accepted, an ordinary provider/validator failure is an execution
        # outcome, not an absent attempt or a storage error. Keep raw traceback
        # details in host logs rather than leaking them through a saved record.
        # BaseException (including process cancellation/termination) propagates.
        LOG.exception("Operation %s failed (execution %s)", operation_id, execution["execution_id"])
        execution["refusal"] = {
            "code": "operation_failed",
            "message": "Unexpected operation failure (" + type(exc).__name__ + ")",
        }
        return seal(execution), None
    result = {
        "schema": "ciw.operation-result.v1", "result_id": "result-" + uuid.uuid4().hex,
        "execution_id": execution["execution_id"], "operation_id": operation_id,
        "evidence_id": run["evidence_id"], "run_id": run["run_id"],
        "selection_revision": selection["revision"], "channel": selection["channel"],
        "interval_s": copy.deepcopy(selection["interval_s"]), "created_at": execution["created_at"],
        "recording_file": recording_file, "verification_id": None,
        "verification_status": "not_verified", "role": operation.role,
        "runtime": execution["runtime"], "parameters": copy.deepcopy(parameters), "data": data,
    }
    execution.update(status="completed", result_id=result["result_id"])
    return seal(execution), seal(result)


def validate_execution(execution: dict, run: dict, revision: int, results: dict) -> None:
    check_seal(execution)
    if execution.get("schema") != "ciw.execution.v1":
        raise ValueError("Unsupported execution schema")
    if not re.fullmatch(r"execution-[0-9a-f]{32}", str(execution.get("execution_id"))):
        raise ValueError("Invalid execution identity")
    if execution.get("run_id") != run["run_id"] or execution.get("evidence_id") != run["evidence_id"]:
        raise ValueError("Execution source binding mismatch")
    if (type(execution.get("selection_revision")) is not int
            or not 0 <= execution["selection_revision"] <= revision):
        raise ValueError("Invalid execution selection revision")
    if not isinstance(execution.get("parameters"), dict):
        raise ValueError("Invalid execution parameters")
    finite_tree(execution, "execution")
    operation_id = execution.get("operation_id")
    if not valid_operation_id(operation_id):
        raise ValueError("Invalid execution operation identity")
    channel, interval = execution.get("channel"), execution.get("interval_s")
    if not isinstance(channel, str) or channel not in run["channels"]:
        raise ValueError("Invalid execution channel")
    if (not isinstance(interval, list) or len(interval) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in interval)
            or not 0 <= interval[0] < interval[1] <= run["metadata"]["duration_s"]
            or not any(interval[0] <= value < interval[1] for value in run["time_s"])):
        raise ValueError("Invalid execution interval")
    for key in ("channel", "interval_s"):
        if key in execution["parameters"] and execution["parameters"][key] != execution[key]:
            raise ValueError("Execution parameter/selection mismatch")
    runtime = execution.get("runtime")
    if runtime is not None and (not isinstance(runtime, dict) or not runtime):
        raise ValueError("Invalid execution runtime identity")
    if operation_id in {"fluid.reservoir.simulate.v1", "fluid.reservoir.verify.v1",
                        "fluid.wave.simulate.v1", "fluid.wave.verify.v1"}:
        from ..fluid_workflow import validate_runtime
        validate_runtime(operation_id, runtime, allow_absent=execution.get("status") == "refused")
    if operation_id in {"irrigation.plan.v1", "irrigation.verify.v1"}:
        from ..irrigation_workflow import validate_runtime
        validate_runtime(operation_id, runtime, allow_absent=execution.get("status") == "refused")
    if operation_id in {"leakage.assess-balance.v1", "leakage.verify-balance.v1"}:
        from ..leakage_workflow import validate_runtime
        validate_runtime(operation_id, runtime, allow_absent=execution.get("status") == "refused")
    try:
        timestamp = datetime.fromisoformat(execution["created_at"])
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid execution timestamp") from exc
    if execution.get("status") == "completed":
        if runtime is None:
            raise ValueError("Completed execution has no runtime identity")
        result = results.get(execution.get("result_id"))
        if result is None or result.get("schema") != "ciw.operation-result.v1":
            raise ValueError("Completed execution has no matching operation result")
        for key in ("execution_id", "operation_id", "runtime", "parameters", "created_at", "selection_revision", "channel", "interval_s"):
            if result.get(key) != execution.get(key):
                raise ValueError(f"Execution/result {key} binding mismatch")
        if "refusal" in execution:
            raise ValueError("Completed execution cannot carry refusal")
    elif execution.get("status") == "refused":
        refusal = execution.get("refusal")
        if (execution.get("result_id") is not None or not isinstance(refusal, dict)
                or not {"code", "message"} <= refusal.keys() <= {"code", "message", "reason_code"}
                or not all(isinstance(value, str) and value.strip() for value in refusal.values())):
            raise ValueError("Refused execution must have a reason and no result")
    else:
        raise ValueError("Unsupported execution status")
