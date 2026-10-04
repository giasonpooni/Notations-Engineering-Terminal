"""Trusted, read-only payload schemas, independent of executable runtime bindings.

Persisted files cannot register or import validators. New providers register a
schema in trusted process setup, alongside their executable operation binding.
"""
from __future__ import annotations

from collections.abc import Callable

from ..core.records import finite_tree
from .registry import valid_operation_id

_VALIDATORS: dict[str, Callable] = {}
_VALIDATOR_ROLES: dict[str, str] = {}
_ROLES = frozenset({"analysis", "state_estimator", "calibration", "verification", "decision", "backend"})


def dependency_result_ids(operation_id: str, parameters: dict) -> list[str]:
    """Fixed trusted dependency contracts; saved parameters never load code."""
    system_fields = {
        "system.simulate.v1": ("plan",), "system.study.v1": ("plan",),
        "system.verify.v1": ("candidate",), "system.compare.v1": ("left", "right"),
    }.get(operation_id)
    if system_fields is not None:
        return [parameters[field]["result_id"] for field in system_fields
                if isinstance(parameters.get(field), dict)
                and isinstance(parameters[field].get("result_id"), str)]
    if operation_id == "irrigation.verify.v1":
        candidate = parameters.get("candidate")
        identity = candidate.get("result_id") if type(candidate) is dict else None
        return [identity] if type(identity) is str else []
    if operation_id in {"polymer.copilot-context.v1", "polymer.verify-cycle.v1", "leakage.verify-balance.v1"}:
        candidate = parameters.get("assessment")
        identity = candidate.get("result_id") if type(candidate) is dict else None
        return [identity] if type(identity) is str else []
    return []


def validate_request_dependencies(operation_id: str, parameters: dict, retained: dict) -> None:
    if operation_id == "irrigation.verify.v1":
        from ..irrigation_workflow import validate_live_dependency
        validate_live_dependency(parameters, retained)
    if operation_id == "leakage.verify-balance.v1":
        from ..leakage_workflow import validate_live_dependency
        validate_live_dependency(parameters, retained)
    if operation_id in {"polymer.copilot-context.v1", "polymer.verify-cycle.v1"}:
        from ..polymer_workflow import validate_live_dependency
        validate_live_dependency(parameters, retained)


_BUILTIN_ROLES = {"statistics.v1": "analysis", "spectrum.periodogram.v1": "analysis",
                "fsrt.tank-reconstruct.v1": "state_estimator",
                "fsrt.tank-reconstruct.v2": "state_estimator",
                "ciw.simulated-fsrt.v1": "state_estimator",
                "jspt.covariance-propagate.v1": "backend",
                "gte.project-circle.v1": "backend",
                "gsc.local-frame.v1": "backend",
                "oscillator.rhs-native.v1": "backend",
                "legibility.compile.v1": "backend",
                "legibility.fixture-summary.v1": "backend",
                "impact.spring-contact.v1": "backend",
                "impact.spring-contact-verify.v1": "verification",
                "impact.crush-contact.v1": "backend",
                "impact.crush-contact-verify.v1": "verification",
                "impact.plate-contact.v1": "backend",
                "impact.plate-contact-verify.v1": "verification",
                "atmosphere.compile.v1": "backend",
                "atmosphere.verify.v1": "verification",
                "fluid.reservoir.simulate.v1": "backend",
                "fluid.reservoir.verify.v1": "verification",
                "fluid.wave.simulate.v1": "backend",
                "fluid.wave.verify.v1": "verification",
                "polymer.assess-cycle.v1": "backend",
                "polymer.copilot-context.v1": "backend",
                "polymer.control-simulate.v1": "backend",
                "polymer.verify-cycle.v1": "verification",
                "irrigation.plan.v1": "backend",
                "irrigation.verify.v1": "verification",
                "leakage.assess-balance.v1": "backend",
                "leakage.verify-balance.v1": "verification",
                "system.compile.v1": "backend",
                "system.simulate.v1": "backend",
                "system.compare.v1": "backend",
                "system.study.v1": "backend",
                "system.verify.v1": "verification"}


def validate_role(operation_id: str, role: str) -> None:
    expected = _BUILTIN_ROLES.get(operation_id, _VALIDATOR_ROLES.get(operation_id))
    if expected is None:
        raise ValueError(f"No trusted saved-payload schema and role for {operation_id}")
    if role != expected:
        raise ValueError("Operation role contradicts the declared payload contract")


def register_payload_validator(operation_id: str, validator: Callable, *, role: str = "backend") -> None:
    """Bind an offline schema and its role together in trusted process setup.

    Existing extension providers default to their historical backend role.
    Analysis, estimation, calibration, verification, and decision extensions must
    declare their role explicitly; retained files cannot choose or change it.
    """
    if not valid_operation_id(operation_id) or not callable(validator):
        raise ValueError("A payload schema requires a versioned operation and callable validator")
    if not isinstance(role, str) or role not in _ROLES:
        raise ValueError("A payload schema requires an explicit supported role")
    if operation_id in _VALIDATORS or operation_id in _BUILTIN_ROLES:
        raise ValueError("Payload schema already registered")
    _VALIDATORS[operation_id] = validator
    _VALIDATOR_ROLES[operation_id] = role


def validate_payload(operation_id: str, data: dict, run: dict, parameters: dict, selection: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("Operation data must be an object")
    finite_tree(data, "operation data")
    if operation_id in {"statistics.v1", "spectrum.periodogram.v1"}:
        from ..adapters.oscillator_records import validate_payload as validator
    elif operation_id == "fsrt.tank-reconstruct.v1":
        from ..adapters.fsrt_records import validate_payload as validator
    elif operation_id == "fsrt.tank-reconstruct.v2":
        from ..adapters.covariance_records import validate_fsrt_payload as validator
    elif operation_id == "ciw.simulated-fsrt.v1":
        from ..simulated_fsrt import validate_payload as validator
    elif operation_id == "jspt.covariance-propagate.v1":
        from ..adapters.covariance_records import validate_jspt_payload as validator
    elif operation_id == "gte.project-circle.v1":
        from ..adapters.gte_records import validate_payload as validator
    elif operation_id == "legibility.compile.v1":
        from ..legibility_workflow import validate_payload as validator
    elif operation_id == "legibility.fixture-summary.v1":
        from ..legibility_workflow import validate_fixture_payload as validator
    elif operation_id in {"impact.spring-contact.v1", "impact.spring-contact-verify.v1",
                          "impact.crush-contact.v1", "impact.crush-contact-verify.v1",
                          "impact.plate-contact.v1", "impact.plate-contact-verify.v1"}:
        from ..impact_workflow import validate_payload as validator
    elif operation_id == "gsc.local-frame.v1":
        from ..spatial_records import validate_payload as validator
    elif operation_id == "oscillator.rhs-native.v1":
        from ..adapters.oscillator_kernel import validate_payload as validator
    elif operation_id in {"fluid.reservoir.simulate.v1", "fluid.reservoir.verify.v1",
                          "fluid.wave.simulate.v1", "fluid.wave.verify.v1"}:
        from ..fluid_workflow import validate_payload as validator
    elif operation_id in {"irrigation.plan.v1", "irrigation.verify.v1"}:
        from ..irrigation_workflow import validate_payload as validator
    elif operation_id in {"atmosphere.compile.v1", "atmosphere.verify.v1"}:
        from ..atmosphere_workflow import validate_payload as validator
    elif operation_id in {"polymer.assess-cycle.v1", "polymer.copilot-context.v1",
                          "polymer.control-simulate.v1", "polymer.verify-cycle.v1"}:
        from ..polymer_workflow import validate_payload as validator
    elif operation_id in {"leakage.assess-balance.v1", "leakage.verify-balance.v1"}:
        from ..leakage_workflow import validate_payload as validator
    elif operation_id in {"system.compile.v1", "system.simulate.v1", "system.verify.v1",
                          "system.compare.v1", "system.study.v1"}:
        from ..system_workflow import validate_payload as validator
    else:
        validator = _VALIDATORS.get(operation_id)
        if validator is None:
            raise ValueError(f"No trusted saved-payload schema for {operation_id}")
    validator(operation_id, data, run, parameters, selection)
