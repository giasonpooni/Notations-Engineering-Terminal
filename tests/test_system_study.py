"""Startup coverage, bounded work and honest retained temporal-study decisions."""
import copy
import math

import pytest

from ciw.operations.runner import check_seal, seal
from ciw.system_spec import compile_spec, demo_spec
from ciw.system_study import (
    MAX_REFERENCE_WORK,
    MAX_WORK_ITEMS,
    normalized_step_sizes,
    run_temporal_study,
    validate_temporal_study,
)


def spec(cells=128, duration=600.0, step=5.0, **thermal_values):
    value = demo_spec(cells=cells)
    value["clock"].update(duration=duration, dt=step)
    for coupling in value["couplings"]:
        coupling["timing"]["interval_s"] = step
    for name, parameter in thermal_values.items():
        node = next(node for node in value["nodes"] if name in node["parameters"])
        node["parameters"][name]["value"] = parameter
    return value


@pytest.fixture(scope="module")
def baseline():
    source = spec()
    return source, run_temporal_study(source)


def test_default_study_retains_first_heating_startup_and_real_accuracy_failure(baseline):
    source, report = baseline
    check_seal(report)
    validate_temporal_study(report, source)
    assert report["step_sizes_s"] == [5, 2.5, 1.25]
    assert report["common_time_s"] == list(range(0, 601, 5))
    assert report["startup_time_s"] == 5
    assert report["status"] == "FAIL"
    assert report["studies"][0]["startup_error_k"] > 2
    assert report["checks"][0]["status"] == "FAIL"
    assert report["checks"][1]["status"] == report["checks"][2]["status"] == "PASS"
    assert report["canonical_admission"] is False
    assert report["physical_validation_status"] == "not_assessed"
    assert report["validity"]["unsampled_times"] == "not_assessed"
    assert report["validity"]["mechanical_and_sensor_accuracy"] == "not_assessed"


def test_observed_improvement_has_no_assumed_order_or_whole_time_claim(baseline):
    _, report = baseline
    errors = [item["max_error_k"] for item in report["studies"]]
    assert errors[2] < errors[1] < errors[0]
    for comparison in report["improvement"]:
        assert comparison["trend"] == "IMPROVED"
        assert comparison["max_error_change_k"] < 0
        assert comparison["assumed_convergence_order"] is None
    assert report["validity"]["time_range_s"] == [5, 600]
    assert report["analytic_reference"]["roundoff_certified"] is False
    assert report["analytic_reference"]["samples"][1]["tail_bound_k"] <= 1e-8


def test_study_links_the_base_and_each_refined_compilation_and_compact_candidate(baseline):
    source, report = baseline
    base = compile_spec(source)
    assert report["plan_digest"] == base["plan_digest"]
    assert report["spec_digest"] == base["spec_digest"]
    assert report["studies"][0]["configuration_digest"] == base["configuration_digest"]
    assert len({item["candidate_digest"] for item in report["studies"]}) == 3
    assert len({item["plan_digest"] for item in report["studies"]}) == 3
    for item in report["studies"]:
        assert item["candidate_digest"].startswith("sha256:")
        assert "trajectory" not in item
        assert item["error_by_time_k"][0] == 0
        assert item["max_error_k"] == max(item["error_by_time_k"])
        assert item["startup_error_k"] == item["error_by_time_k"][1]
    assert "system_study.py" in report["runtime"]["source_files"]


def test_custom_base_clock_is_fully_covered_and_source_is_untouched():
    source = spec(cells=16, duration=20, step=2)
    untouched = copy.deepcopy(source)
    report = run_temporal_study(source, [2, 1, 0.5])
    assert source == untouched
    assert report["common_time_s"] == list(range(0, 21, 2))
    assert [item["steps"] for item in report["studies"]] == [10, 20, 40]
    validate_temporal_study(report, source)


def test_uniform_isothermal_equilibrium_is_exact_with_zero_fourier_work():
    source = spec(cells=16, duration=20, left_temperature_k=293.15, right_temperature_k=293.15)
    report = run_temporal_study(source)
    assert report["status"] == "PASS"
    assert report["work_budget"]["reference_cell_modes"] == 0
    assert all(item["max_error_k"] < 1e-10 for item in report["studies"])
    assert all(sample["series_terms"] == 0 for sample in report["analytic_reference"]["samples"])
    validate_temporal_study(report, source)


def test_unqualified_fourier_tail_is_unassessed_and_never_zero_error():
    source = spec(cells=16, duration=20, k_w_per_m_k=1e-12)
    report = run_temporal_study(source)
    assert report["status"] == "NOT_ASSESSED"
    for item in report["studies"]:
        assert item["accuracy_status"] == "NOT_ASSESSED"
        assert item["max_error_k"] is item["startup_error_k"] is None
        assert item["error_by_time_k"] == [0, None, None, None, None]
    assert all(check["status"] == "NOT_ASSESSED" for check in report["checks"])
    assert report["work_budget"]["reference_cell_modes"] == 0
    validate_temporal_study(report, source)


@pytest.mark.parametrize("steps", [[], [5], [5, 2.5, 1.25, 1, 0.5, 0.25, 0.125], (5, 2.5),
                                  [2.5, 1.25], [5, 5], [5, 10], [5, 4], [5, 0], [5, -1],
                                  [5, float("nan")], [5, float("inf")], [5, True], [5, "2.5"]])
def test_illegal_study_step_sequences_are_rejected_before_execution(steps, monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("Invalid clocks must be refused before execution")
    monkeypatch.setattr("ciw.system_study.simulate", prohibited)
    with pytest.raises(ValueError):
        run_temporal_study(spec(), steps)


def test_finer_step_must_obey_compiler_step_limit():
    with pytest.raises(ValueError, match="steps"):
        run_temporal_study(spec(), [5, 0.25])


def test_default_steps_clip_at_compiler_limit_and_refuse_a_single_level():
    assert normalized_step_sizes(spec(step=1)) == [1, 0.5]
    with pytest.raises(ValueError, match="2 to 6"):
        normalized_step_sizes(spec(step=0.5))


def test_aggregate_solver_work_is_refused_before_any_simulation(monkeypatch):
    source = spec(cells=256, step=600)
    steps = [600] + [600 / count for count in range(1996, 2001)]
    assert 256 * (1 + sum(range(1996, 2001))) > MAX_WORK_ITEMS
    def prohibited(*args, **kwargs):
        raise AssertionError("Aggregate work must be checked before executing")
    monkeypatch.setattr("ciw.system_study.simulate", prohibited)
    with pytest.raises(ValueError, match="aggregate solver work"):
        run_temporal_study(source, steps)


def test_reference_work_exhaustion_marks_remaining_samples_unassessed(monkeypatch):
    monkeypatch.setattr("ciw.system_study.MAX_REFERENCE_WORK", 4000)
    source = spec(cells=128, duration=20)
    report = run_temporal_study(source)
    assert report["work_budget"]["reference_cell_modes"] <= 4000
    assert any(sample["status"] == "NOT_ASSESSED" for sample in report["analytic_reference"]["samples"][1:])
    assert report["status"] == "NOT_ASSESSED"
    validate_temporal_study(report, source)


def test_offline_validation_cannot_execute_simulation_or_fourier_reference(baseline, monkeypatch):
    source, report = baseline
    def prohibited(*args, **kwargs):
        raise AssertionError("Retained inspection must never recompute numerical science")
    monkeypatch.setattr("ciw.system_study.simulate", prohibited)
    monkeypatch.setattr("ciw.system_study._analytic_cell_averages", prohibited)
    monkeypatch.setattr("ciw.system_study._references", prohibited)
    monkeypatch.setattr("ciw.system_study.run_temporal_study", prohibited)
    assert validate_temporal_study(report, source) is report
    assert normalized_step_sizes(source) == [5, 2.5, 1.25]


@pytest.mark.parametrize("mutation", [
    "schema", "base_spec", "base_plan", "refined_plan", "candidate", "sample_clock", "startup_time",
    "step_sizes", "study_count", "error_shape", "error_sign", "max_error", "startup_error", "accuracy_status",
    "pass_status", "improvement", "convergence_order", "authority", "physical_validation", "tail_bound",
    "tail_shape", "tail_status", "reference_work", "work_budget", "extra_field", "boolean_time", "boolean_max",
])
def test_resealed_shape_identity_and_scientific_claim_forgeries_are_rejected(baseline, mutation):
    source, original = baseline
    report = copy.deepcopy(original)
    study = report["studies"][0]
    if mutation == "schema":
        report["schema"] = "ciw.system-temporal-study.v2"
    elif mutation == "base_spec":
        report["spec_digest"] = "sha256:" + "0" * 64
    elif mutation == "base_plan":
        report["plan_digest"] = report["studies"][1]["plan_digest"]
    elif mutation == "refined_plan":
        report["studies"][1]["plan_digest"] = study["plan_digest"]
    elif mutation == "candidate":
        study["candidate_digest"] = "candidate-no-content-identity"
    elif mutation == "sample_clock":
        report["common_time_s"][1] = 10
    elif mutation == "startup_time":
        report["startup_time_s"] = 300
    elif mutation == "step_sizes":
        report["step_sizes_s"] = [5, 1.25, 0.625]
    elif mutation == "study_count":
        report["studies"].pop()
    elif mutation == "error_shape":
        study["error_by_time_k"].pop()
    elif mutation == "error_sign":
        study["error_by_time_k"][1] = -1
    elif mutation == "max_error":
        study["max_error_k"] = 0.0
    elif mutation == "startup_error":
        study["startup_error_k"] = study["error_by_time_k"][-1]
    elif mutation == "accuracy_status":
        study["accuracy_status"] = "NOT_ASSESSED"
    elif mutation == "pass_status":
        report["status"] = "PASS"
    elif mutation == "improvement":
        report["improvement"][0]["trend"] = "WORSENED"
    elif mutation == "convergence_order":
        report["improvement"][0]["assumed_convergence_order"] = 1
    elif mutation == "authority":
        report["canonical_admission"] = True
    elif mutation == "physical_validation":
        report["physical_validation_status"] = "validated"
    elif mutation == "tail_bound":
        report["analytic_reference"]["samples"][1]["tail_bound_k"] = 0.1
    elif mutation == "tail_shape":
        report["analytic_reference"]["samples"].pop()
    elif mutation == "tail_status":
        report["analytic_reference"]["samples"][1]["status"] = "NOT_ASSESSED"
    elif mutation == "reference_work":
        report["work_budget"]["reference_cell_modes"] = MAX_REFERENCE_WORK + 1
    elif mutation == "work_budget":
        report["work_budget"]["solver_cell_steps"] = 0
    elif mutation == "extra_field":
        study["observed_order"] = 1
    elif mutation == "boolean_time":
        report["common_time_s"][0] = False
    elif mutation == "boolean_max":
        study["max_error_k"] = False
    seal(report)
    with pytest.raises(ValueError):
        validate_temporal_study(report, source)


def test_report_cannot_transplant_to_different_physical_source(baseline):
    _, report = baseline
    other = spec(k_w_per_m_k=0.3)
    with pytest.raises(ValueError, match="binding"):
        validate_temporal_study(report, other)


def test_unsealed_retained_report_is_rejected(baseline):
    source, original = baseline
    changed = copy.deepcopy(original)
    changed["studies"][0]["max_error_k"] = 0
    with pytest.raises(ValueError, match="integrity"):
        validate_temporal_study(changed, source)


def test_reference_tail_bound_is_finite_for_early_startup(baseline):
    _, report = baseline
    startup = report["analytic_reference"]["samples"][1]
    assert startup["time_s"] == 5
    assert startup["status"] == "ASSESSED"
    assert 0 <= startup["tail_bound_k"] <= 1e-8
    assert math.isfinite(startup["tail_bound_k"])
    assert 8 <= startup["series_terms"] <= 4096


def test_coherent_fabricated_unassessed_reference_cannot_hide_startup_failure(baseline):
    from ciw.system_study import _accuracy_checks, _accuracy_summary, _improvements, _report_status
    source, original = baseline
    altered = copy.deepcopy(original)
    altered["analytic_reference"]["samples"][1].update(
        status="NOT_ASSESSED", tail_bound_k=None, series_terms=0, reason="Invented unsupported condition")
    for study in altered["studies"]:
        study["error_by_time_k"][1] = None
        status, maximum, startup = _accuracy_summary(study["error_by_time_k"])
        study.update(accuracy_status=status, max_error_k=maximum, startup_error_k=startup)
    altered["checks"] = _accuracy_checks(altered["studies"], 2.0)
    altered["status"] = _report_status(altered["checks"])
    altered["improvement"] = _improvements(altered["studies"])
    altered["work_budget"]["reference_cell_modes"] = 128 * sum(
        sample["series_terms"] for sample in altered["analytic_reference"]["samples"])
    seal(altered)
    assert altered["status"] == "NOT_ASSESSED"
    with pytest.raises(ValueError, match="scalar contract"):
        validate_temporal_study(altered, source)


def test_coherent_forged_reference_term_count_cannot_invent_work_accounting(baseline):
    source, original = baseline
    altered = copy.deepcopy(original)
    altered["analytic_reference"]["samples"][1]["series_terms"] += 1
    altered["work_budget"]["reference_cell_modes"] += 128
    seal(altered)
    with pytest.raises(ValueError, match="scalar contract"):
        validate_temporal_study(altered, source)


def test_runtime_requires_known_source_inventory_without_rereading_historical_sources(baseline):
    source, original = baseline
    altered = copy.deepcopy(original)
    altered["runtime"]["source_files"] = {"fake.py": "sha256:" + "0" * 64}
    seal(altered)
    with pytest.raises(ValueError, match="source identities"):
        validate_temporal_study(altered, source)
    # Historic implementation hashes remain provenance; source upgrades do not
    # cause numerical execution during inspection of a structurally valid report.
    historical = copy.deepcopy(original)
    historical["runtime"]["source_files"] = {
        name: "sha256:" + "0" * 64 for name in historical["runtime"]["source_files"]}
    seal(historical)
    validate_temporal_study(historical, source)


def test_empirical_worsening_is_reported_without_assuming_a_convergence_order():
    from ciw.system_study import _improvements
    comparisons = _improvements([
        {"step_s": 5, "accuracy_status": "ASSESSED", "max_error_k": 0.1, "startup_error_k": 0.1},
        {"step_s": 2.5, "accuracy_status": "ASSESSED", "max_error_k": 0.2, "startup_error_k": 0.2},
    ])
    assert comparisons[0]["trend"] == "WORSENED"
    assert comparisons[0]["max_error_change_k"] == pytest.approx(0.1)
    assert comparisons[0]["assumed_convergence_order"] is None


def test_partial_reference_coverage_preserves_an_assessed_accuracy_counterexample():
    from ciw.system_study import _accuracy_checks
    studies = [
        {"step_s": 5, "accuracy_status": "PARTIALLY_ASSESSED", "max_error_k": 2.5},
        {"step_s": 2.5, "accuracy_status": "PARTIALLY_ASSESSED", "max_error_k": 1.5},
        {"step_s": 1.25, "accuracy_status": "NOT_ASSESSED", "max_error_k": None},
    ]
    assert [check["status"] for check in _accuracy_checks(studies, 2)] == ["FAIL", "NOT_ASSESSED", "NOT_ASSESSED"]


@pytest.mark.parametrize("mutation", ["engine", "effect", "resources", "limits", "extra"])
def test_study_declares_actual_local_execution_without_claiming_resource_enforcement(baseline, mutation):
    source, report = baseline
    assert report["execution"] == {"engine": "local", "effect": "simulation",
                                  "resources": source["execution"]["resources"], "resource_limits_enforced": False}
    altered = copy.deepcopy(report)
    if mutation == "engine":
        altered["execution"]["engine"] = "container"
    elif mutation == "effect":
        altered["execution"]["effect"] = "physical_control"
    elif mutation == "resources":
        altered["execution"]["resources"]["cpu"] += 1
    elif mutation == "limits":
        altered["execution"]["resource_limits_enforced"] = True
    else:
        altered["execution"]["container_started"] = True
    seal(altered)
    with pytest.raises(ValueError, match="execution"):
        validate_temporal_study(altered, source)
