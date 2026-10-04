"""Meaningful physical limits and failure cases for the polymer reference engine."""
import copy
import math

import pytest

from ciw.operations.runner import check_seal, digest, seal
from ciw.system_models import (
    _analytic_cell_averages,
    _fields,
    compare_configurations,
    simulate,
    validate_candidate,
    verify_simulation,
)
from ciw.system_spec import compile_spec, demo_spec


def plan(cells=16, boundary="free", force_n=None, duration=600.0, step=5.0, **values):
    spec = demo_spec(cells=cells, boundary=boundary, force_n=force_n)
    spec["clock"].update(duration=duration, dt=step)
    for coupling in spec["couplings"]:
        coupling["timing"]["interval_s"] = step
    for name, value in values.items():
        node = next(node for node in spec["nodes"] if name in node["parameters"])
        if isinstance(node["parameters"][name], dict):
            node["parameters"][name]["value"] = value
        else:
            node["parameters"][name] = value
    return compile_spec(spec)


def check_by_id(report, name):
    return next(check for check in report["checks"] if check["check_id"] == name)


def rebind_state_ids(candidate):
    """Allow testing resealed scientifically false data, beyond integrity checks."""
    t = candidate["trajectory"]
    t["snapshot_state_digests"] = [digest({
        "configuration_digest": candidate["configuration_digest"], "time_s": time,
        "temperature_k": temperature, "displacement_m": displacement, "axial_force_n": force,
        "strain": strain, "stress_pa": stress, "frame": candidate["frame"],
    }) for time, temperature, displacement, force, strain, stress in zip(
        t["time_s"], t["temperature_k"], t["displacement_m"], t["axial_force_n"], t["strain"], t["stress_pa"])]
    by_time = dict(zip(t["time_s"], t["snapshot_state_digests"]))
    for observation in candidate["observations"]:
        observation["state_digest"] = by_time[observation["time_s"]]
    return seal(candidate)


def test_demo_is_deterministic_and_numerically_verified_without_physical_admission():
    p = plan()
    untouched = copy.deepcopy(p)
    first, second = simulate(p), simulate(p)
    assert first == second
    assert p == untouched
    check_seal(first)
    assert validate_candidate(p, first) is first
    report = verify_simulation(p, first)
    check_seal(report)
    assert report["status"] == "PASS"
    assert report["candidate_digest"] == first["record_digest"]
    assert first["canonical_admission"] is False
    assert report["physical_validation_status"] == "not_assessed"
    assert check_by_id(report, "independent_fourier_cell_average_benchmark")["status"] == "PASS"
    assert check_by_id(report, "mesh_and_time_refinement_reduces_reference_error")["status"] == "PASS"
    assert first["frame"] == "rod.material-axis.v1"


def test_uniform_isothermal_loaded_free_bar_has_exact_elongation_and_no_heat():
    p = plan(left_temperature_k=293.15, right_temperature_k=293.15)
    result = simulate(p)
    length, area, modulus = (p["model"][key] for key in ("length_m", "area_m2", "young_modulus_pa"))
    expected = 5 * length / (modulus * area)
    for temperature, displacement in zip(result["trajectory"]["temperature_k"], result["trajectory"]["displacement_m"]):
        assert temperature == pytest.approx([293.15] * 16, abs=2e-12)
        assert displacement[-1] == pytest.approx(expected, abs=1e-15)
    assert max(map(abs, result["balances"]["energy_residual_j"])) < 1e-8


def test_half_cell_boundary_conductance_and_exact_discrete_balance():
    p = plan(cells=2, duration=10, step=5, left_temperature_k=313.15, right_temperature_k=313.15)
    r = simulate(p)
    m = p["model"]
    dx = m["length_m"] / 2
    expected_boundary_flux = 2 * m["conductivity_w_m_k"] * m["area_m2"] / dx * (20 + 20)
    assert r["balances"]["boundary_heat_input_w"][0] == pytest.approx(expected_boundary_flux)
    assert r["numerical_method"]["boundary_face_distance_m"] == dx / 2
    assert max(map(abs, r["balances"]["energy_residual_j"])) < 1e-10
    assert r["balances"]["cumulative_boundary_heat_j"][-1] == pytest.approx(
        r["balances"]["internal_energy_change_j"][-1], abs=1e-10)


def test_equal_hot_baths_are_monotone_bounded_and_conservative():
    p = plan(left_temperature_k=313.15, right_temperature_k=313.15)
    r = simulate(p)
    ts = r["trajectory"]["temperature_k"]
    assert all(293.15 - 1e-12 <= value <= 313.15 + 1e-12 for row in ts for value in row)
    assert all(current >= old - 1e-12 for old_row, row in zip(ts, ts[1:]) for old, current in zip(old_row, row))
    assert all(q >= 0 for q in r["balances"]["boundary_heat_input_w"])
    assert max(map(abs, r["balances"]["energy_residual_j"])) < 1e-8


def test_fourier_cell_average_reference_matches_independent_symmetric_formula():
    p = plan(left_temperature_k=313.15, right_temperature_k=313.15)
    m = p["model"]
    n, time = 16, 400.0
    computed, tail, _ = _analytic_cell_averages(m, n, [293.15] * n, time)
    diffusivity = m["conductivity_w_m_k"] / (m["density_kg_m3"] * m["heat_capacity_j_kg_k"])
    expected = []
    for cell in range(n):
        total = 313.15
        for mode in range(1, 401, 2):
            average_sine = n / (mode * math.pi) * (math.cos(mode * math.pi * cell / n)
                                                   - math.cos(mode * math.pi * (cell + 1) / n))
            total += -80 / (mode * math.pi) * average_sine * math.exp(
                -diffusivity * (mode * math.pi / m["length_m"])**2 * time)
        expected.append(total)
    assert computed == pytest.approx(expected, abs=1e-10)
    assert tail < 1e-8


def test_spatial_and_temporal_refinement_reduce_independent_reference_error():
    def error(cells, step):
        p = plan(cells=cells, step=step, left_temperature_k=313.15, right_temperature_k=313.15)
        r = simulate(p)
        exact, _, _ = _analytic_cell_averages(p["model"], cells, [293.15] * cells, 600)
        return max(abs(value - truth) for value, truth in zip(r["trajectory"]["temperature_k"][-1], exact))
    assert error(32, 5) < error(8, 5)
    assert error(32, 1) < error(32, 10)


def test_uniform_thermal_free_and_fixed_strain_limits():
    free = plan(force_n=0, initial_temperature_k=303.15, left_temperature_k=303.15, right_temperature_k=303.15)
    fixed = plan(boundary="fixed", initial_temperature_k=303.15, left_temperature_k=303.15, right_temperature_k=303.15)
    rf, rc = simulate(free), simulate(fixed)
    alpha, length = free["model"]["expansion_per_k"], free["model"]["length_m"]
    assert rf["trajectory"]["displacement_m"][0][-1] == pytest.approx(alpha * 10 * length)
    assert rf["trajectory"]["strain"][0] == pytest.approx([alpha * 10] * 16)
    assert rf["trajectory"]["stress_pa"][0] == [0] * 16
    ea = fixed["model"]["young_modulus_pa"] * fixed["model"]["area_m2"]
    assert rc["trajectory"]["axial_force_n"][0] == pytest.approx(-ea * alpha * 10)
    assert rc["trajectory"]["displacement_m"][0] == pytest.approx([0] * 17, abs=1e-18)
    assert verify_simulation(fixed, rc)["status"] == "PASS"


def test_fixed_nonzero_force_is_rejected_even_when_a_plan_is_forged():
    p = plan(boundary="fixed")
    p["model"]["axial_force_n"] = 2
    with pytest.raises(ValueError, match="declared specification"):
        simulate(p)


def test_sensor_seed_bias_zero_noise_and_declared_covariance():
    p = plan(temperature_std_k=0, displacement_std_m=0, temperature_bias_k=2, displacement_bias_m=3e-6)
    r = simulate(p)
    for obs in r["observations"]:
        assert obs["synthetic"] is True
        assert obs["temperature_k"] == obs["truth_temperature_k"] + 2
        assert obs["displacement_m"] == obs["truth_displacement_m"] + 3e-6
        assert obs["uncertainty"]["cross_covariance_k_m"] == 0
        assert obs["uncertainty"]["noise_independence"] == "temperature_and_displacement"
    noisy = plan()
    changed = plan(seed=noisy["sensors"][0]["seed"] + 1)
    assert simulate(noisy)["trajectory"]["sensor_temperature_k"] != simulate(changed)["trajectory"]["sensor_temperature_k"]
    assert simulate(noisy)["trajectory"]["temperature_k"] == simulate(changed)["trajectory"]["temperature_k"]


@pytest.mark.parametrize("endpoint", [0, 1])
def test_temperature_sensor_boundary_anchor_is_exact(endpoint):
    p = plan(temperature_std_k=0, displacement_std_m=0, position_m=endpoint * 0.02)
    r = simulate(p)
    expected = p["model"]["right_temperature_k" if endpoint else "left_temperature_k"]
    assert all(obs["temperature_k"] == expected for obs in r["observations"])


def test_nonuniform_initial_state_is_not_silently_admitted_by_the_scalar_contract():
    p = plan()
    n = p["mesh"]["cells"]
    m = p["model"]
    initial = [m["left_temperature_k"]
        + (m["right_temperature_k"] - m["left_temperature_k"]) * (i + 0.5) / n for i in range(n)]
    assert _analytic_cell_averages(m, n, initial, 600) is None
    trajectory, _ = _fields(m, n, initial, 600, 5)
    assert trajectory["temperature_k"][-1] == pytest.approx(initial, abs=1e-10)
    p["model"]["initial_temperature_k"] = initial
    with pytest.raises(ValueError, match="declared specification"):
        simulate(p)


def test_fourier_work_limit_has_honest_unassessed_benchmark():
    p = plan(k_w_per_m_k=1e-12)
    r = simulate(p)
    v = verify_simulation(p, r)
    assert v["status"] == "PASS"
    assert check_by_id(v, "independent_fourier_cell_average_benchmark")["status"] == "NOT_ASSESSED"
    assert next(b for b in v["benchmarks"] if b["kind"] == "combined_mesh_time_refinement")["status"] == "NOT_ASSESSED"


def test_configuration_reduction_has_initial_commutation_and_reports_real_divergence():
    pf, pc = plan(cells=16), plan(cells=4)
    report = compare_configurations(pf, simulate(pf), pc, simulate(pc))
    check_seal(report)
    assert report["mapping"]["group_size"] == 4
    assert report["commutation"]["temperature_error_k"][0] == 0
    assert report["commutation"]["displacement_error_m"][0] < 1e-15
    assert report["commutation"]["max_temperature_error_k"] > 0.75
    assert report["status"] == "FAIL"
    assert report["physical_validation_status"] == "not_assessed"
    identity_spec = copy.deepcopy(pf["specification"])
    identity_spec["representations"].append({**identity_spec["representations"][0],
                                           "representation_id": "thermal-identity", "target_cells": 16})
    identity = compile_spec(identity_spec)
    assert compare_configurations(identity, simulate(identity), identity, simulate(identity))["status"] == "PASS"
    assert report["representation_id"] == "thermal-reduced"
    assert report["mapping_digest"].startswith("sha256:")


@pytest.mark.parametrize("difference", ["mesh", "clock", "material", "boundary", "frame", "initial"])
def test_reduction_rejects_incompatible_families(difference):
    pf, pc = plan(cells=16), plan(cells=4)
    if difference == "mesh":
        pc = plan(cells=3)
    elif difference == "clock":
        pc = plan(cells=4, step=10)
    elif difference == "material":
        pc = plan(cells=4, youngs_modulus_pa=2e9)
    elif difference == "boundary":
        pc = plan(cells=4, boundary="fixed")
    elif difference == "frame":
        spec = copy.deepcopy(pc["specification"])
        spec["frame"] = "other-frame"
        for coupling in spec["couplings"]:
            coupling["frame"] = "other-frame"
        pc = compile_spec(spec)
    elif difference == "initial":
        pc = plan(cells=4, initial_temperature_k=294.15)
    with pytest.raises(ValueError, match="Reduction|Coarse"):
        compare_configurations(pf, simulate(pf), pc, simulate(pc))


def test_resealed_temperature_change_fails_independent_equations():
    p = plan()
    altered = simulate(p)
    altered["trajectory"]["temperature_k"][15][2] += 0.1
    rebind_state_ids(altered)
    validate_candidate(p, altered)
    report = verify_simulation(p, altered)
    assert report["status"] == "FAIL"
    assert check_by_id(report, "finite_volume_step_equation")["status"] == "FAIL"
    assert check_by_id(report, "discrete_global_heat_balance")["status"] == "FAIL"


def test_resealed_displacement_change_fails_equilibrium_and_strain_consistency():
    p = plan()
    altered = simulate(p)
    altered["trajectory"]["displacement_m"][15][2] += 1e-5
    rebind_state_ids(altered)
    report = verify_simulation(p, altered)
    assert report["status"] == "FAIL"
    assert check_by_id(report, "axial_equilibrium_and_load")["status"] == "FAIL"
    assert check_by_id(report, "strain_field_matches_displacement")["status"] == "FAIL"


def test_temperature_and_strain_outside_declared_regime_fail():
    with pytest.raises(ValueError, match="validity interval"):
        plan(initial_temperature_k=350, left_temperature_k=350, right_temperature_k=350)
    p = plan()
    result = simulate(p)
    result["trajectory"]["temperature_k"][-1][0] = 350
    rebind_state_ids(result)
    v = verify_simulation(p, result)
    assert check_by_id(v, "declared_temperature_regime")["status"] == "FAIL"
    loaded = plan(force_n=2000)
    assert check_by_id(verify_simulation(loaded, simulate(loaded)), "small_strain_regime")["status"] == "FAIL"


@pytest.mark.parametrize("field", ["time_s", "temperature_k", "strain", "stress_pa", "sensor_temperature_k", "snapshot_state_digests"])
def test_offline_structure_validation_rejects_resealed_shape_changes(field):
    p = plan()
    altered = simulate(p)
    altered["trajectory"][field].pop()
    seal(altered)
    with pytest.raises(ValueError):
        validate_candidate(p, altered)
    assert verify_simulation(p, altered)["status"] == "FAIL"


def test_offline_structure_validation_does_not_run_solver(monkeypatch):
    p = plan()
    candidate = simulate(p)
    def prohibited(*args, **kwargs):
        raise AssertionError("Offline structural validation must not execute")
    monkeypatch.setattr("ciw.system_models.simulate", prohibited)
    monkeypatch.setattr("ciw.system_models._heat_step", prohibited)
    monkeypatch.setattr("ciw.system_models.verify_simulation", prohibited)
    assert validate_candidate(p, candidate) is candidate


def test_private_final_short_step_is_conservative_but_public_clock_contract_is_integral():
    with pytest.raises(ValueError, match="integral number"):
        plan(duration=12.0, step=5.0)
    p = plan()
    trajectory, balances = _fields(p["model"], 16, [293.15] * 16, 12, 5)
    assert trajectory["time_s"] == [0, 5, 10, 12]
    assert max(map(abs, balances["energy_residual_j"])) < 1e-8


def test_unsealed_and_transplanted_candidates_fail_without_scientific_checks():
    p = plan()
    r = simulate(p)
    r["trajectory"]["temperature_k"][1][0] += 1
    v = verify_simulation(p, r)
    assert v["status"] == "FAIL"
    assert len(v["checks"]) == 1
    other = plan(cells=8)
    assert verify_simulation(other, simulate(p))["status"] == "FAIL"
