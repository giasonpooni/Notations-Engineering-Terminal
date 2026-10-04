"""Retained, startup-aware temporal accuracy studies for the reference rod.

Only the thermal cell-average trajectory is compared with the independent heat
solution. Study records retain compact error histories and content links, rather
than whole synthetic trajectories. Structural inspection does not execute any
solver, reconstruct any Fourier series, or assert experimental validity.
"""
from __future__ import annotations

import copy
import math
import re

from .operations.runner import check_seal, seal
from .system_models import _analytic_cell_averages, simulate
from .system_runtime import runtime_identity
from .system_spec import COMPILER_ID, compile_spec, validate_spec


SCHEMA = "ciw.system-temporal-study.v1"
PROVIDER_ID = "ciw.scientific-system.temporal-study.v1"
REFERENCE_ID = "dirichlet-uniform-initial-fourier-cell-average.v1"
MAX_STEPS = 2000
MAX_WORK_ITEMS = 2_000_000
MAX_REFERENCE_WORK = 2_000_000
TAIL_LIMIT_K = 1e-8
IMPROVEMENT_SLACK_K = 1e-9
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RUNTIME_SOURCES = {"system_spec.py", "system_models.py", "system_workflow.py", "system_source.py",
                    "system_study.py", "system_execution.py", "system_runtime.py", "core/identities.py"}
ASSUMPTIONS = [
    "The same declared physical model, spatial mesh, initial condition and baths are retained at every step size.",
    "Only the integration step and the same-step coupling intervals change.",
    "Accuracy compares thermal cell averages at every base-clock sample, including the first heating startup sample.",
    "Uniform initial temperature and constant Dirichlet baths admit a separated-variable heat reference.",
    "Fourier tail bounds concern series truncation; floating-point roundoff is not certified.",
    "Error differences are observed for this fixed mesh and these times; no convergence order is assumed.",
    "Mechanical and synthetic sensor accuracy, unsampled times and experimental validation are not assessed.",
    "Retained candidate digests link generated content; compact errors do not reconstruct whole trajectories.",
]


def _fields(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"{label} requires its exact versioned fields")


def _number(value, label, *, positive=False, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if positive and value <= 0 or nonnegative and value < 0:
        raise ValueError(f"{label} has an invalid sign")
    return value


def _digest(value, label):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a content digest")


def _source_spec(base, step):
    spec = copy.deepcopy(base)
    spec["clock"]["dt"] = step
    for coupling in spec["couplings"]:
        coupling["timing"]["interval_s"] = step
    return spec


def _study_plans(spec, step_sizes_s):
    base = validate_spec(spec)
    plan = compile_spec(base)
    dt, duration = base["clock"]["dt"], base["clock"]["duration"]
    if step_sizes_s is None:
        steps = [dt]
        for factor in (2, 4):
            step = dt / factor
            if duration / step <= MAX_STEPS:
                steps.append(step)
    else:
        if type(step_sizes_s) is not list:
            raise ValueError("step_sizes_s must be a list")
        steps = copy.deepcopy(step_sizes_s)
    if not 2 <= len(steps) <= 6:
        raise ValueError("A temporal study requires 2 to 6 legal step sizes")
    for step in steps:
        _number(step, "study step size", positive=True)
        ratio = dt / step
        if (not math.isfinite(ratio) or ratio < 1 or ratio > MAX_STEPS
                or not math.isclose(ratio, round(ratio), rel_tol=0, abs_tol=1e-9)):
            raise ValueError("Every study step must exactly subdivide the base clock")
    if steps[0] != dt or any(a <= b for a, b in zip(steps, steps[1:])):
        raise ValueError("Study steps must start at the base step and strictly decrease")
    plans = [compile_spec(_source_spec(base, step)) for step in steps]
    counts = [round(duration / step) for step in steps]
    if any(count > MAX_STEPS for count in counts):
        raise ValueError("Temporal study exceeds the per-plan step limit")
    work = base["discretization"]["cells"] * sum(counts)
    if work > MAX_WORK_ITEMS:
        raise ValueError("Temporal study exceeds its aggregate solver work budget")
    count = round(duration / dt)
    common_times = [i * dt for i in range(count)] + [duration]
    return base, plan, plans, steps, counts, common_times, work


def normalized_step_sizes(spec, step_sizes_s=None):
    """Resolve a bounded requested study clock without scientific execution."""
    return _study_plans(spec, step_sizes_s)[3]


def _reference_schedule(plan, common_times):
    """Derive scalar eligibility and work accounting without evaluating a series."""
    model, cells = plan["model"], plan["mesh"]["cells"]
    initial = [model["initial_temperature_k"]] * cells
    metadata = []
    work = 0
    equilibrium = (model["initial_temperature_k"] == model["left_temperature_k"]
                   == model["right_temperature_k"])
    for time in common_times:
        if time == 0 or equilibrium:
            metadata.append({"time_s": time, "status": "ASSESSED", "tail_bound_k": 0.0,
                             "series_terms": 0, "reason": "exact_initial_state" if time == 0 else "exact_uniform_equilibrium"})
            continue
        reason = None
        try:
            diffusivity = model["conductivity_w_m_k"] / (model["density_kg_m3"] * model["heat_capacity_j_kg_k"])
            c = diffusivity * math.pi**2 * time / model["length_m"]**2
            if not math.isfinite(c) or c <= 0:
                raise ValueError("Unrepresentable reference scale")
            quotient = 45 / c
            if not math.isfinite(quotient):
                raise ValueError("Unrepresentable Fourier mode estimate")
            terms = min(4096, max(8, math.ceil(math.sqrt(quotient))))
            maximum = max(abs(initial[0] - model["left_temperature_k"]), abs(initial[0] - model["right_temperature_k"]))
            tail = 8 * maximum * cells / (math.pi**2 * terms) * math.exp(-c * (terms + 1)**2)
            if not math.isfinite(tail) or tail > TAIL_LIMIT_K:
                reason = "Fourier truncation tail exceeds the reference bound"
            elif work + cells * terms > MAX_REFERENCE_WORK:
                reason = "Aggregate reference work budget exhausted"
            else:
                metadata.append({"time_s": time, "status": "ASSESSED", "tail_bound_k": tail,
                                 "series_terms": terms, "reason": "bounded_fourier_cell_average"})
                work += cells * terms
                continue
        except (OverflowError, ValueError, ZeroDivisionError):
            reason = "Reference arithmetic scale is unsupported"
        metadata.append({"time_s": time, "status": "NOT_ASSESSED", "tail_bound_k": None,
                         "series_terms": 0, "reason": reason})
    return metadata, work


def _references(plan, common_times):
    metadata, work = _reference_schedule(plan, common_times)
    model, cells = plan["model"], plan["mesh"]["cells"]
    initial = [model["initial_temperature_k"]] * cells
    values = []
    for sample in metadata:
        if sample["status"] == "NOT_ASSESSED":
            values.append(None)
        elif sample["series_terms"] == 0:
            values.append(list(initial))
        else:
            reference = _analytic_cell_averages(model, cells, initial, sample["time_s"])
            if (reference is None or any(not math.isfinite(value) for value in reference[0])
                    or reference[1:] != (sample["tail_bound_k"], sample["series_terms"])):
                raise ValueError("Qualified Fourier reference did not yield its declared finite solution")
            values.append(reference[0])
    return values, metadata, work


def _accuracy_summary(errors):
    assessed = [error for error in errors[1:] if error is not None]
    complete = len(assessed) == len(errors) - 1
    status = "ASSESSED" if complete else ("PARTIALLY_ASSESSED" if assessed else "NOT_ASSESSED")
    return status, max(assessed) if assessed else None, errors[1]


def _improvements(studies):
    comparisons = []
    for before, after in zip(studies, studies[1:]):
        fully_assessed = before["accuracy_status"] == after["accuracy_status"] == "ASSESSED"
        change = after["max_error_k"] - before["max_error_k"] if fully_assessed else None
        startup_change = (after["startup_error_k"] - before["startup_error_k"]
                          if before["startup_error_k"] is not None and after["startup_error_k"] is not None else None)
        if change is None:
            trend = "NOT_ASSESSED"
        elif change < -IMPROVEMENT_SLACK_K:
            trend = "IMPROVED"
        elif change > IMPROVEMENT_SLACK_K:
            trend = "WORSENED"
        else:
            trend = "UNCHANGED"
        comparisons.append({"from_step_s": before["step_s"], "to_step_s": after["step_s"],
                            "max_error_change_k": change, "startup_error_change_k": startup_change,
                            "trend": trend, "assumed_convergence_order": None})
    return comparisons


def _accuracy_checks(studies, threshold):
    checks = []
    for index, study in enumerate(studies):
        # Any assessed counterexample refutes the sampled accuracy claim, even
        # when a reference-work bound leaves other samples unassessed.
        if study["max_error_k"] is not None and study["max_error_k"] > threshold:
            status = "FAIL"
        elif study["accuracy_status"] != "ASSESSED":
            status = "NOT_ASSESSED"
        else:
            status = "PASS"
        checks.append({"check_id": f"sampled_thermal_accuracy_{index}", "step_s": study["step_s"],
                       "status": status, "value": study["max_error_k"], "limit": threshold, "unit": "K",
                       "scope": "base_clock_samples_including_first_heating_startup"})
    return checks


def _report_status(checks):
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL"
    if any(check["status"] == "NOT_ASSESSED" for check in checks):
        return "NOT_ASSESSED"
    return "PASS"


def run_temporal_study(spec, step_sizes_s=None):
    """Compute a bounded study and retain errors at a shared startup-aware clock."""
    base, base_plan, plans, steps, counts, common_times, work = _study_plans(spec, step_sizes_s)
    references, metadata, reference_work = _references(base_plan, common_times)
    studies = []
    for plan, step, count in zip(plans, steps, counts):
        candidate = simulate(plan)
        ratio = round(base["clock"]["dt"] / step)
        errors = []
        for index, (time, reference) in enumerate(zip(common_times, references)):
            trajectory_index = index * ratio
            actual_time = candidate["trajectory"]["time_s"][trajectory_index]
            if not math.isclose(actual_time, time, rel_tol=1e-13, abs_tol=1e-13):
                raise ValueError("Compiled trajectory does not contain an exact common clock sample")
            temperature = candidate["trajectory"]["temperature_k"][trajectory_index]
            error = max(abs(a - b) for a, b in zip(temperature, reference)) if reference is not None else None
            if error is not None:
                _number(error, "computed temporal error", nonnegative=True)
            errors.append(error)
        status, maximum, startup = _accuracy_summary(errors)
        studies.append({"step_s": step, "spec_digest": plan["spec_digest"],
                        "configuration_digest": plan["configuration_digest"], "plan_digest": plan["plan_digest"],
                        "candidate_digest": candidate["record_digest"], "steps": count,
                        "accuracy_status": status, "max_error_k": maximum, "startup_error_k": startup,
                        "error_by_time_k": errors})
    threshold = base_plan["verification"]["analytic_temperature_abs_k"]
    checks = _accuracy_checks(studies, threshold)
    return seal({
        "schema": SCHEMA, "spec_digest": base_plan["spec_digest"],
        "configuration_digest": base_plan["configuration_digest"], "plan_digest": base_plan["plan_digest"],
        "compiler_id": COMPILER_ID, "provider_id": PROVIDER_ID,
        "model_ids": [node["model_id"] for node in base["nodes"]], "runtime": runtime_identity(),
        "execution": {"engine": "local", "effect": "simulation",
                      "resources": copy.deepcopy(base["execution"]["resources"]), "resource_limits_enforced": False},
        "step_sizes_s": steps, "common_time_s": common_times, "startup_time_s": common_times[1],
        "samples_scope": "thermal_cell_averages_at_base_clock_samples_including_startup",
        "analytic_reference": {"reference_id": REFERENCE_ID, "initial_condition": "uniform",
                               "boundary_condition": "constant_dirichlet", "tail_limit_k": TAIL_LIMIT_K,
                               "roundoff_certified": False, "samples": metadata},
        "studies": studies, "improvement": _improvements(studies), "checks": checks,
        "status": _report_status(checks),
        "validity": {"time_range_s": [common_times[1], common_times[-1]], "duration_s": base["clock"]["duration"],
                     "cells": base["discretization"]["cells"], "frame": base["frame"],
                     "accuracy_limit_k": threshold, "unsampled_times": "not_assessed",
                     "mechanical_and_sensor_accuracy": "not_assessed", "assumed_convergence_order": None},
        "work_budget": {"maximum_steps_per_plan": MAX_STEPS, "maximum_solver_cell_steps": MAX_WORK_ITEMS,
                        "solver_cell_steps": work, "maximum_reference_cell_modes": MAX_REFERENCE_WORK,
                        "reference_cell_modes": reference_work},
        "assumptions": list(ASSUMPTIONS), "canonical_admission": False,
        "physical_validation_status": "not_assessed",
    })


def validate_temporal_study(report, spec):
    """Inspect retained shape, bindings and derived decisions without recomputation.

    This verifies internal consistency of reported errors, not that those errors
    were truthfully obtained. The retained execution/result occurrence supplies
    provenance; re-assessing science requires a newly executed study.
    """
    check_seal(report)
    _fields(report, {"schema", "spec_digest", "configuration_digest", "plan_digest", "compiler_id", "provider_id",
                     "model_ids", "runtime", "execution", "step_sizes_s", "common_time_s", "startup_time_s", "samples_scope",
                     "analytic_reference", "studies", "improvement", "checks", "status", "validity", "work_budget",
                     "assumptions", "canonical_admission", "physical_validation_status", "record_digest"}, "temporal study")
    if report["schema"] != SCHEMA or report["compiler_id"] != COMPILER_ID or report["provider_id"] != PROVIDER_ID:
        raise ValueError("Unsupported temporal study provider or schema")
    base, base_plan, plans, steps, counts, common_times, work = _study_plans(spec, report["step_sizes_s"])
    for key in ("spec_digest", "configuration_digest", "plan_digest"):
        if report[key] != base_plan[key]:
            raise ValueError("Temporal study base specification binding mismatch")
    if report["model_ids"] != [node["model_id"] for node in base["nodes"]]:
        raise ValueError("Temporal study model identities mismatch")
    expected_execution = {"engine": "local", "effect": "simulation",
                          "resources": base["execution"]["resources"], "resource_limits_enforced": False}
    _fields(report["execution"], expected_execution, "temporal study execution")
    if report["execution"] != expected_execution or report["execution"]["resource_limits_enforced"] is not False:
        raise ValueError("Temporal study execution must declare actual local simulation and unenforced resource limits")
    runtime = report["runtime"]
    _fields(runtime, {"provider", "version", "python", "source_files"}, "temporal study runtime")
    if runtime["provider"] != "ciw.scientific-system" or runtime["version"] != "2" or not isinstance(runtime["python"], str):
        raise ValueError("Temporal study runtime identity mismatch")
    if type(runtime["source_files"]) is not dict or set(runtime["source_files"]) != _RUNTIME_SOURCES:
        raise ValueError("Temporal study requires source identities")
    for name, identity in runtime["source_files"].items():
        if not isinstance(name, str) or not name:
            raise ValueError("Temporal study source identity name is invalid")
        _digest(identity, "runtime source")
    if (report["common_time_s"] != common_times or report["startup_time_s"] != common_times[1]
            or report["samples_scope"] != "thermal_cell_averages_at_base_clock_samples_including_startup"):
        raise ValueError("Temporal study common sampling clock mismatch")
    for time in report["common_time_s"]:
        _number(time, "retained common time", nonnegative=True)
    reference = report["analytic_reference"]
    _fields(reference, {"reference_id", "initial_condition", "boundary_condition", "tail_limit_k", "roundoff_certified", "samples"}, "analytic reference")
    if (reference["reference_id"] != REFERENCE_ID or reference["initial_condition"] != "uniform"
            or reference["boundary_condition"] != "constant_dirichlet" or reference["tail_limit_k"] != TAIL_LIMIT_K
            or reference["roundoff_certified"] is not False):
        raise ValueError("Temporal study reference contract mismatch")
    if type(reference["samples"]) is not list or len(reference["samples"]) != len(common_times):
        raise ValueError("Temporal reference sampling dimensions mismatch")
    expected_reference_samples, expected_reference_work = _reference_schedule(base_plan, common_times)
    if reference["samples"] != expected_reference_samples:
        raise ValueError("Temporal reference qualification differs from the deterministic scalar contract")
    reference_work = 0
    for index, (time, sample) in enumerate(zip(common_times, reference["samples"])):
        _fields(sample, {"time_s", "status", "tail_bound_k", "series_terms", "reason"}, "reference sample")
        _number(sample["time_s"], "reference sample time", nonnegative=True)
        if sample["time_s"] != time or not isinstance(sample["reason"], str) or not sample["reason"]:
            raise ValueError("Temporal reference clock or diagnostic mismatch")
        if type(sample["series_terms"]) is not int or not 0 <= sample["series_terms"] <= 4096:
            raise ValueError("Temporal reference work count is invalid")
        if sample["status"] == "ASSESSED":
            _number(sample["tail_bound_k"], "Fourier tail", nonnegative=True)
            if sample["tail_bound_k"] > TAIL_LIMIT_K:
                raise ValueError("Temporal reference tail exceeds the declared bound")
            reference_work += base["discretization"]["cells"] * sample["series_terms"]
        elif sample["status"] == "NOT_ASSESSED":
            if sample["tail_bound_k"] is not None or sample["series_terms"] != 0:
                raise ValueError("An unassessed reference must not fabricate a tail or work count")
        else:
            raise ValueError("Unsupported temporal reference assessment status")
        if index == 0 and sample != {"time_s": 0.0, "status": "ASSESSED", "tail_bound_k": 0.0,
                                     "series_terms": 0, "reason": "exact_initial_state"}:
            raise ValueError("Temporal study must explicitly retain its exact initial state")
    if reference_work > MAX_REFERENCE_WORK:
        raise ValueError("Temporal reference exceeds its work budget")
    if reference_work != expected_reference_work:
        raise ValueError("Temporal reference work differs from its scalar qualification contract")
    if type(report["studies"]) is not list or len(report["studies"]) != len(steps):
        raise ValueError("Temporal study count mismatch")
    for study, plan, step, count in zip(report["studies"], plans, steps, counts):
        _fields(study, {"step_s", "spec_digest", "configuration_digest", "plan_digest", "candidate_digest", "steps",
                       "accuracy_status", "max_error_k", "startup_error_k", "error_by_time_k"}, "temporal study sample")
        if study["step_s"] != step or type(study["steps"]) is not int or study["steps"] != count:
            raise ValueError("Temporal step binding mismatch")
        for key in ("spec_digest", "configuration_digest", "plan_digest"):
            if study[key] != plan[key]:
                raise ValueError("Temporal refined specification binding mismatch")
        _digest(study["candidate_digest"], "study candidate")
        errors = study["error_by_time_k"]
        if type(errors) is not list or len(errors) != len(common_times) or errors[0] != 0:
            raise ValueError("Temporal error dimensions or exact initial state mismatch")
        for error, sample in zip(errors, reference["samples"]):
            if sample["status"] == "ASSESSED":
                _number(error, "reported temporal error", nonnegative=True)
            elif error is not None:
                raise ValueError("Temporal accuracy cannot fabricate an unassessed reference error")
        status, maximum, startup = _accuracy_summary(errors)
        if study["max_error_k"] is not None:
            _number(study["max_error_k"], "retained maximum error", nonnegative=True)
        if study["startup_error_k"] is not None:
            _number(study["startup_error_k"], "retained startup error", nonnegative=True)
        if (study["accuracy_status"], study["max_error_k"], study["startup_error_k"]) != (status, maximum, startup):
            raise ValueError("Temporal error summaries differ from the retained history")
    threshold = base_plan["verification"]["analytic_temperature_abs_k"]
    checks = _accuracy_checks(report["studies"], threshold)
    if report["checks"] != checks or report["status"] != _report_status(checks):
        raise ValueError("Temporal accuracy decision differs from the retained errors")
    if report["improvement"] != _improvements(report["studies"]):
        raise ValueError("Temporal improvement claims differ from the observed errors")
    expected_validity = {"time_range_s": [common_times[1], common_times[-1]], "duration_s": base["clock"]["duration"],
                         "cells": base["discretization"]["cells"], "frame": base["frame"], "accuracy_limit_k": threshold,
                         "unsampled_times": "not_assessed", "mechanical_and_sensor_accuracy": "not_assessed",
                         "assumed_convergence_order": None}
    expected_work = {"maximum_steps_per_plan": MAX_STEPS, "maximum_solver_cell_steps": MAX_WORK_ITEMS,
                     "solver_cell_steps": work, "maximum_reference_cell_modes": MAX_REFERENCE_WORK,
                     "reference_cell_modes": reference_work}
    if report["validity"] != expected_validity or report["work_budget"] != expected_work:
        raise ValueError("Temporal validity scope or work accounting mismatch")
    if report["assumptions"] != ASSUMPTIONS or report["canonical_admission"] is not False or report["physical_validation_status"] != "not_assessed":
        raise ValueError("Temporal study assumptions or authority mismatch")
    return report
