"""Reference thermal/thermoelastic composition for a one-dimensional polymer rod.

The heat state contains finite-volume *cell averages*, and the displacement state
contains nodal values.  These are numerical candidates, never measurements or
physical validation.  Constant material coefficients and prescribed bath
temperatures make the approximation and its verification deliberately narrow.
"""
from __future__ import annotations

import bisect
import copy
import math
import random

from .operations.runner import check_seal, digest, seal


SIMULATION_SCHEMA = "ciw.system-simulation.v1"
VERIFICATION_SCHEMA = "ciw.system-numerical-verification.v1"
COMPARISON_SCHEMA = "ciw.system-reduction-comparison.v1"
UNITS = {
    "time_s": "s", "temperature_k": "K", "displacement_m": "m",
    "axial_force_n": "N", "strain": "1", "stress_pa": "Pa", "sensor_temperature_k": "K",
    "sensor_displacement_m": "m", "energy_residual_j": "J",
    "equilibrium_residual_n": "N", "boundary_heat_input_w": "W",
    "cumulative_boundary_heat_j": "J", "internal_energy_change_j": "J",
}
ASSUMPTIONS = [
    "One-dimensional cell-centered conduction with constant k, density and heat capacity.",
    "Fixed Dirichlet baths; end-face conductance uses half a cell width.",
    "Implicit Euler advances heat; boundary heat is integrated at the end of each step.",
    "Quasi-static linear elasticity, constant modulus and thermal expansion; small strain.",
    "The left end is fixed; the right end is free with prescribed force or fixed with zero load.",
    "Thermal-to-mechanical coupling only; mechanical work does not feed back into heat.",
    "Sensors are synthetic interpolation with explicitly seeded Gaussian bias/noise.",
    "Synthetic temperature and displacement noise are independent with declared zero cross covariance.",
    "No molecular prediction, constitutive calibration or experimental validation is claimed.",
]


def _finite(value, name, *, positive=False, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0 or nonnegative and value < 0:
        raise ValueError(f"{name} has an invalid sign")
    return float(value)


def _parameters(plan):
    if not isinstance(plan, dict):
        raise ValueError("A compiled system plan is required")
    from .system_spec import compile_spec
    if "specification" not in plan or compile_spec(plan["specification"]) != plan:
        raise ValueError("Compiled system plan differs from its declared specification")
    for key in ("spec_digest", "configuration_digest", "plan_digest"):
        if not isinstance(plan.get(key), str) or not plan[key]:
            raise ValueError(f"Missing {key}")
    p = copy.deepcopy(plan["model"])
    for key in ("length_m", "area_m2", "conductivity_w_m_k", "density_kg_m3",
                "heat_capacity_j_kg_k", "young_modulus_pa", "reference_temperature_k",
                "left_temperature_k", "right_temperature_k"):
        p[key] = _finite(p[key], key, positive=True)
    for key in ("expansion_per_k", "axial_force_n"):
        p[key] = _finite(p[key], key)
    n = plan["mesh"]["cells"]
    if type(n) is not int or not 1 <= n <= 4096:
        raise ValueError("Reference mesh must have 1 to 4096 cells")
    if p["right_constraint"] not in ("free", "fixed"):
        raise ValueError("Unsupported mechanical right constraint")
    if p["right_constraint"] == "fixed" and p["axial_force_n"] != 0:
        raise ValueError("A fixed right end cannot also prescribe a nonzero force")
    initial = p["initial_temperature_k"]
    if type(initial) in (int, float):
        initial = [_finite(initial, "initial_temperature_k", positive=True)] * n
    elif isinstance(initial, list) and len(initial) == n:
        initial = [_finite(v, "initial_temperature_k", positive=True) for v in initial]
    else:
        raise ValueError("Initial temperatures must be a scalar or one value per cell")
    duration = _finite(plan["clock"]["duration_s"], "duration_s", positive=True)
    step = _finite(plan["clock"]["step_s"], "step_s", positive=True)
    count = math.ceil(duration / step)
    if count > 20000 or count * n > 8_000_000:
        raise ValueError("Reference solver work limit exceeded")
    sensors = copy.deepcopy(plan.get("sensors", []))
    ids = set()
    for sensor in sensors:
        sid = sensor["sensor_id"]
        if not isinstance(sid, str) or not sid or sid in ids:
            raise ValueError("Sensor identities must be nonempty and distinct")
        ids.add(sid)
        x = _finite(sensor["position_m"], "sensor position")
        if not 0 <= x <= p["length_m"]:
            raise ValueError("Sensor position lies outside the rod")
        for key in ("temperature_bias_k", "displacement_bias_m"):
            sensor[key] = _finite(sensor.get(key, 0), key)
        for key in ("temperature_std_k", "displacement_std_m"):
            sensor[key] = _finite(sensor.get(key, 0), key, nonnegative=True)
        if type(sensor.get("seed")) is not int:
            raise ValueError("Each synthetic sensor needs an integer seed")
    return p, n, initial, duration, step, sensors


def _times(duration, step):
    count = math.ceil(duration / step)
    times = [i * step for i in range(count)]
    # Avoid a near-zero last step caused by roundoff in duration / step.
    if len(times) > 1 and math.isclose(times[-1], duration, rel_tol=1e-13, abs_tol=1e-13):
        times.pop()
    return times + [duration]


def _tridiagonal(lower, diagonal, upper, rhs):
    """Thomas elimination for the strictly diagonally dominant heat matrix."""
    d, b = list(diagonal), list(rhs)
    for i in range(1, len(d)):
        weight = lower[i - 1] / d[i - 1]
        d[i] -= weight * upper[i - 1]
        b[i] -= weight * b[i - 1]
    result = [0.0] * len(d)
    result[-1] = b[-1] / d[-1]
    for i in range(len(d) - 2, -1, -1):
        result[i] = (b[i] - upper[i] * result[i + 1]) / d[i]
    return result


def _heat_step(p, old, dt):
    n = len(old)
    dx = p["length_m"] / n
    capacity = p["density_kg_m3"] * p["heat_capacity_j_kg_k"] * p["area_m2"] * dx
    g = p["conductivity_w_m_k"] * p["area_m2"] / dx
    diagonal = [capacity / dt + (g if i > 0 else 2 * g)
                + (g if i < n - 1 else 2 * g) for i in range(n)]
    rhs = [capacity / dt * v for v in old]
    rhs[0] += 2 * g * p["left_temperature_k"]
    rhs[-1] += 2 * g * p["right_temperature_k"]
    return _tridiagonal([-g] * (n - 1), diagonal, [-g] * (n - 1), rhs)


def _mechanics(p, temperatures):
    n = len(temperatures)
    dx = p["length_m"] / n
    ea = p["young_modulus_pa"] * p["area_m2"]
    mean_delta = math.fsum(t - p["reference_temperature_k"] for t in temperatures) / n
    force = (p["axial_force_n"] if p["right_constraint"] == "free"
             else -ea * p["expansion_per_k"] * mean_delta)
    displacements = [0.0]
    for temperature in temperatures:
        strain = force / ea + p["expansion_per_k"] * (temperature - p["reference_temperature_k"])
        displacements.append(displacements[-1] + dx * strain)
    residual = max(abs(ea * ((displacements[i + 1] - displacements[i]) / dx
                             - p["expansion_per_k"] * (temperatures[i] - p["reference_temperature_k"]))
                       - force) for i in range(n))
    return displacements, force, residual


def _interpolate(positions, values, x):
    j = bisect.bisect_right(positions, x)
    if j == 0:
        return values[0]
    if j == len(positions):
        return values[-1]
    weight = (x - positions[j - 1]) / (positions[j] - positions[j - 1])
    return values[j - 1] * (1 - weight) + values[j] * weight


def _fields(p, n, initial, duration, step):
    times = _times(duration, step)
    temperatures = [list(initial)]
    displacement, force, residual = _mechanics(p, initial)
    displacements, forces, equilibrium = [displacement], [force], [residual]
    capacity = p["density_kg_m3"] * p["heat_capacity_j_kg_k"] * p["area_m2"] * p["length_m"] / n
    boundary_g = 2 * p["conductivity_w_m_k"] * p["area_m2"] * n / p["length_m"]
    input_w = [boundary_g * (p["left_temperature_k"] - initial[0]
                            + p["right_temperature_k"] - initial[-1])]
    heat, change, energy = [0.0], [0.0], [0.0]
    for i in range(1, len(times)):
        dt = times[i] - times[i - 1]
        temperature = _heat_step(p, temperatures[-1], dt)
        temperatures.append(temperature)
        displacement, force, residual = _mechanics(p, temperature)
        displacements.append(displacement)
        forces.append(force)
        equilibrium.append(residual)
        input_w.append(boundary_g * (p["left_temperature_k"] - temperature[0]
                                     + p["right_temperature_k"] - temperature[-1]))
        heat.append(heat[-1] + dt * input_w[-1])
        change.append(capacity * math.fsum(t - t0 for t, t0 in zip(temperature, initial)))
        energy.append(change[-1] - heat[-1])
    strains = [[force / (p["young_modulus_pa"] * p["area_m2"])
                + p["expansion_per_k"] * (temperature - p["reference_temperature_k"])
                for temperature in row] for force, row in zip(forces, temperatures)]
    stresses = [[force / p["area_m2"]] * n for force in forces]
    return {
        "time_s": times, "temperature_k": temperatures, "displacement_m": displacements,
        "axial_force_n": forces, "strain": strains, "stress_pa": stresses,
    }, {
        "energy_residual_j": energy, "equilibrium_residual_n": equilibrium,
        "boundary_heat_input_w": input_w, "cumulative_boundary_heat_j": heat,
        "internal_energy_change_j": change,
    }


def simulate(plan):
    """Execute a compiled, side-effect-free composition as synthetic evidence."""
    p, n, initial, duration, step, sensors = _parameters(plan)
    trajectory, balances = _fields(p, n, initial, duration, step)
    dx = p["length_m"] / n
    thermal_x = [0.0] + [(i + 0.5) * dx for i in range(n)] + [p["length_m"]]
    displacement_x = [i * dx for i in range(n + 1)]
    rngs = [random.Random(sensor["seed"]) for sensor in sensors]
    observations = []
    trajectory.update(
        configuration_digest=plan["configuration_digest"], spec_digest=plan["spec_digest"],
        temperature_cell_centers_m=thermal_x[1:-1], displacement_nodes_m=displacement_x,
        sensor_ids=[s["sensor_id"] for s in sensors], sensor_temperature_k=[],
        sensor_displacement_m=[], snapshot_state_digests=[],
    )
    for index, time in enumerate(trajectory["time_s"]):
        temperature = trajectory["temperature_k"][index]
        displacement = trajectory["displacement_m"][index]
        state_digest = digest({
            "configuration_digest": plan["configuration_digest"], "time_s": time,
            "temperature_k": temperature, "displacement_m": displacement,
            "axial_force_n": trajectory["axial_force_n"][index],
            "strain": trajectory["strain"][index], "stress_pa": trajectory["stress_pa"][index],
            "frame": plan.get("frame"),
        })
        trajectory["snapshot_state_digests"].append(state_digest)
        ts, us = [], []
        for sensor, rng in zip(sensors, rngs):
            truth_t = _interpolate(thermal_x, [p["left_temperature_k"]] + temperature
                                   + [p["right_temperature_k"]], sensor["position_m"])
            truth_u = _interpolate(displacement_x, displacement, sensor["position_m"])
            observed_t = truth_t + sensor["temperature_bias_k"] + rng.gauss(0, sensor["temperature_std_k"])
            observed_u = truth_u + sensor["displacement_bias_m"] + rng.gauss(0, sensor["displacement_std_m"])
            ts.append(observed_t)
            us.append(observed_u)
            observations.append({
                "sensor_id": sensor["sensor_id"], "node_id": sensor.get("node_id"),
                "time_s": time, "configuration_digest": plan["configuration_digest"],
                "state_digest": state_digest, "frame": plan.get("frame"),
                "temperature_k": observed_t, "displacement_m": observed_u,
                "synthetic": True, "source_kind": "synthetic_model_observation",
                "truth_temperature_k": truth_t, "truth_displacement_m": truth_u,
                "noise": {"distribution": "gaussian", "seed": sensor["seed"],
                          "temperature_bias_k": sensor["temperature_bias_k"],
                          "displacement_bias_m": sensor["displacement_bias_m"]},
                "uncertainty": {"kind": "declared_synthetic_standard_deviation",
                                "temperature_std_k": sensor["temperature_std_k"],
                                "displacement_std_m": sensor["displacement_std_m"],
                                "noise_independence": "temperature_and_displacement",
                                "temperature_variance_k2": sensor["temperature_std_k"]**2,
                                "displacement_variance_m2": sensor["displacement_std_m"]**2,
                                "cross_covariance_k_m": 0.0},
                "physical_validation_status": "not_assessed",
            })
        trajectory["sensor_temperature_k"].append(ts)
        trajectory["sensor_displacement_m"].append(us)
    return seal({
        "schema": SIMULATION_SCHEMA, "spec_digest": plan["spec_digest"],
        "configuration_digest": plan["configuration_digest"], "plan_digest": plan["plan_digest"],
        "observations": observations, "trajectory": trajectory, "balances": balances,
        "units": copy.deepcopy(UNITS), "frame": plan.get("frame"), "assumptions": list(ASSUMPTIONS),
        "numerical_method": {"thermal": "cell_centered_finite_volume_implicit_euler",
                             "mechanical": "quasi_static_linear_thermoelastic_bar",
                             "boundary_face_distance_m": dx / 2},
        "canonical_admission": False, "physical_validation_status": "not_assessed",
    })


def _analytic_cell_averages(p, n, initial, time):
    """Independent separated-variable heat solution, averaged over each cell.

    Uniform initial heat and either equal or unequal constant Dirichlet baths are
    supported. The exponentially damped Fourier tail bound is returned explicitly.
    """
    if not all(value == initial[0] for value in initial) or time <= 0:
        return None
    length = p["length_m"]
    diffusivity = p["conductivity_w_m_k"] / (p["density_kg_m3"] * p["heat_capacity_j_kg_k"])
    c = diffusivity * math.pi**2 * time / length**2
    maximum = max(abs(initial[0] - p["left_temperature_k"]),
                  abs(initial[0] - p["right_temperature_k"]))
    count = min(4096, max(8, math.ceil(math.sqrt(45 / c))))
    # |b_j * average(sin)| <= 8 M n / (pi^2 j^2). Summing the tail
    # uses integral 1/N and monotonic exponential damping.
    tail = 8 * maximum * n / (math.pi**2 * count) * math.exp(-c * (count + 1)**2)
    if tail > 1e-8:
        return None
    values = [p["left_temperature_k"] + (p["right_temperature_k"] - p["left_temperature_k"])
              * (i + 0.5) / n for i in range(n)]
    for mode in range(1, count + 1):
        sign = -1 if mode % 2 else 1
        coefficient = (2 / (mode * math.pi) * ((initial[0] - p["left_temperature_k"])
                        - sign * (initial[0] - p["right_temperature_k"]))
                       * math.exp(-c * mode**2))
        for i in range(n):
            mean_sine = n / (mode * math.pi) * (math.cos(mode * math.pi * i / n)
                                              - math.cos(mode * math.pi * (i + 1) / n))
            values[i] += coefficient * mean_sine
    return values, tail, count


def _check(check_id, value=None, limit=None, unit=None, *, status=None, details=None):
    record = {"check_id": check_id, "status": status or ("PASS" if value <= limit else "FAIL")}
    if value is not None:
        record["value"] = value
    if limit is not None:
        record["limit"] = limit
    if unit is not None:
        record["unit"] = unit
    if details is not None:
        record["details"] = details
    return record


def _verification_record(plan, candidate, checks, benchmarks):
    failed = any(check["status"] == "FAIL" for check in checks)
    return seal({
        "schema": VERIFICATION_SCHEMA, "spec_digest": plan["spec_digest"],
        "configuration_digest": plan["configuration_digest"], "plan_digest": plan["plan_digest"],
        "candidate_digest": candidate.get("record_digest"), "checks": checks,
        "benchmarks": benchmarks, "status": "FAIL" if failed else "PASS",
        "scope": "numerical_consistency_and_declared_reference_benchmarks",
        "canonical_admission": False, "physical_validation_status": "not_assessed",
    })


def validate_candidate(plan, candidate):
    """Validate retained data without rerunning evolution or numerical checks.

    Content identity and dimensions protect restore/browse operations; they do
    not imply that a sealed trajectory satisfies its governing equations.
    """
    p, n, initial, duration, step, sensors = _parameters(plan)
    check_seal(candidate)
    if candidate.get("schema") != SIMULATION_SCHEMA:
        raise ValueError("Unsupported simulation schema")
    if any(candidate.get(key) != plan[key] for key in ("spec_digest", "configuration_digest", "plan_digest")):
        raise ValueError("Candidate identity does not bind this plan")
    if candidate.get("canonical_admission") is not False or candidate.get("physical_validation_status") != "not_assessed":
        raise ValueError("Reference simulation cannot claim admission or physical validation")
    if candidate.get("units") != UNITS or candidate.get("frame") != plan.get("frame"):
        raise ValueError("Candidate units or frame mismatch")
    t = candidate["trajectory"]
    times = _times(duration, step)
    if t["time_s"] != times:
        raise ValueError("Candidate clock differs from the plan")
    for key in ("temperature_k", "displacement_m", "strain", "stress_pa", "axial_force_n", "snapshot_state_digests",
                "sensor_temperature_k", "sensor_displacement_m"):
        if not isinstance(t.get(key), list) or len(t[key]) != len(times):
            raise ValueError("Candidate field dimensions differ from its clock")
    if t.get("sensor_ids") != [s["sensor_id"] for s in sensors]:
        raise ValueError("Candidate sensor identities differ from the plan")
    if t.get("configuration_digest") != plan["configuration_digest"] or t.get("spec_digest") != plan["spec_digest"]:
        raise ValueError("Trajectory state/configuration identity mismatch")
    dx = p["length_m"] / n
    if (t.get("temperature_cell_centers_m") != [(i + 0.5) * dx for i in range(n)]
            or t.get("displacement_nodes_m") != [i * dx for i in range(n + 1)]):
        raise ValueError("Candidate coordinate support differs from its mesh")
    state_digests = []
    for i, (time, temperature, displacement, force) in enumerate(zip(
            times, t["temperature_k"], t["displacement_m"], t["axial_force_n"])):
        if not isinstance(temperature, list) or not isinstance(displacement, list) or len(temperature) != n or len(displacement) != n + 1:
            raise ValueError("Candidate state dimensions differ from its mesh")
        for value in temperature + displacement + [force]:
            _finite(value, "candidate state")
        for key in ("strain", "stress_pa"):
            if not isinstance(t[key][i], list) or len(t[key][i]) != n:
                raise ValueError("Candidate mechanical state dimensions differ from its mesh")
            for value in t[key][i]:
                _finite(value, "candidate mechanical state")
        for key in ("sensor_temperature_k", "sensor_displacement_m"):
            if not isinstance(t[key][i], list) or len(t[key][i]) != len(sensors):
                raise ValueError("Candidate observation dimensions differ from its sensors")
            for value in t[key][i]:
                _finite(value, "synthetic observation")
        state_digests.append(digest({
            "configuration_digest": plan["configuration_digest"], "time_s": time,
            "temperature_k": temperature, "displacement_m": displacement,
            "axial_force_n": force, "strain": t["strain"][i], "stress_pa": t["stress_pa"][i],
            "frame": plan.get("frame"),
        }))
    if t["temperature_k"][0] != initial:
        raise ValueError("Candidate initial state differs from the plan")
    if t["snapshot_state_digests"] != state_digests:
        raise ValueError("Snapshot state identity mismatch")
    balance_keys = {"energy_residual_j", "equilibrium_residual_n", "boundary_heat_input_w",
                    "cumulative_boundary_heat_j", "internal_energy_change_j"}
    if not isinstance(candidate.get("balances"), dict) or set(candidate["balances"]) != balance_keys:
        raise ValueError("Candidate balance record structure mismatch")
    for values in candidate["balances"].values():
        if not isinstance(values, list) or len(values) != len(times):
            raise ValueError("Candidate balance clock mismatch")
        for value in values:
            _finite(value, "balance")
    observations = candidate.get("observations")
    if not isinstance(observations, list) or len(observations) != len(times) * len(sensors):
        raise ValueError("Candidate observation count mismatch")
    for index, time in enumerate(times):
        for sensor_index, sensor in enumerate(sensors):
            observation = observations[index * len(sensors) + sensor_index]
            expected_fields = {
                "sensor_id": sensor["sensor_id"], "node_id": sensor.get("node_id"),
                "time_s": time, "configuration_digest": plan["configuration_digest"],
                "state_digest": state_digests[index], "frame": plan.get("frame"),
                "synthetic": True, "source_kind": "synthetic_model_observation",
                "physical_validation_status": "not_assessed",
                "noise": {"distribution": "gaussian", "seed": sensor["seed"],
                          "temperature_bias_k": sensor["temperature_bias_k"],
                          "displacement_bias_m": sensor["displacement_bias_m"]},
                "uncertainty": {"kind": "declared_synthetic_standard_deviation",
                                "temperature_std_k": sensor["temperature_std_k"],
                                "displacement_std_m": sensor["displacement_std_m"],
                                "noise_independence": "temperature_and_displacement",
                                "temperature_variance_k2": sensor["temperature_std_k"]**2,
                                "displacement_variance_m2": sensor["displacement_std_m"]**2,
                                "cross_covariance_k_m": 0.0},
            }
            if not isinstance(observation, dict) or any(observation.get(key) != value for key, value in expected_fields.items()):
                raise ValueError("Candidate synthetic observation contract mismatch")
            for key in ("temperature_k", "displacement_m", "truth_temperature_k", "truth_displacement_m"):
                _finite(observation.get(key), "synthetic observation")
            if (observation["temperature_k"] != t["sensor_temperature_k"][index][sensor_index]
                    or observation["displacement_m"] != t["sensor_displacement_m"][index][sensor_index]):
                raise ValueError("Candidate observation/trajectory mapping mismatch")
    return candidate


validate_simulation = validate_candidate


def verify_simulation(plan, candidate):
    """Recompute balances and independent reference errors; never promote physics."""
    p, n, initial, duration, step, sensors = _parameters(plan)
    checks, benchmarks = [], []
    try:
        validate_candidate(plan, candidate)
        t = candidate["trajectory"]
        times = _times(duration, step)
        checks.append(_check("candidate_integrity_and_binding", status="PASS"))
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        checks.append(_check("candidate_integrity_and_binding", status="FAIL", details=str(exc)))
        return _verification_record(plan, candidate, checks, benchmarks)
    limits = plan.get("verification", {})
    energy_limit = _finite(limits.get("energy_abs_j", 1e-8), "energy limit", nonnegative=True)
    force_limit = _finite(limits.get("equilibrium_abs_n", 1e-8), "force limit", nonnegative=True)
    dx = p["length_m"] / n
    capacity = p["density_kg_m3"] * p["heat_capacity_j_kg_k"] * p["area_m2"] * dx
    g = p["conductivity_w_m_k"] * p["area_m2"] / dx
    ea = p["young_modulus_pa"] * p["area_m2"]
    heat, energy_residual, equation_residual, mechanical_residual, endpoint_residual = 0.0, 0.0, 0.0, 0.0, 0.0
    max_strain, max_thermal_strain, max_elastic_strain = 0.0, 0.0, 0.0
    strain_consistency, stress_consistency = 0.0, 0.0
    reconstructed_balances = {key: [] for key in candidate["balances"]}
    expected_keys = set(UNITS) & {"energy_residual_j", "equilibrium_residual_n", "boundary_heat_input_w",
                                "cumulative_boundary_heat_j", "internal_energy_change_j"}
    if set(reconstructed_balances) != expected_keys:
        checks.append(_check("balance_record_structure", status="FAIL"))
        reconstructed_balances = {key: [] for key in expected_keys}
    for index, time in enumerate(times):
        temperature, displacement, force = t["temperature_k"][index], t["displacement_m"][index], t["axial_force_n"][index]
        flux = 2 * g * (p["left_temperature_k"] - temperature[0] + p["right_temperature_k"] - temperature[-1])
        if index:
            dt = time - times[index - 1]
            heat += dt * flux
            for cell, value in enumerate(temperature):
                left_flux = (g * (temperature[cell - 1] - value) if cell else 2 * g * (p["left_temperature_k"] - value))
                right_flux = (g * (temperature[cell + 1] - value) if cell < n - 1 else 2 * g * (p["right_temperature_k"] - value))
                residual = capacity * (value - t["temperature_k"][index - 1][cell]) - dt * (left_flux + right_flux)
                equation_residual = max(equation_residual, abs(residual))
        internal = capacity * math.fsum(value - old for value, old in zip(temperature, initial))
        er = internal - heat
        energy_residual = max(energy_residual, abs(er))
        mr = 0.0
        for cell, value in enumerate(temperature):
            strain = (displacement[cell + 1] - displacement[cell]) / dx
            max_strain = max(max_strain, abs(strain))
            thermal_strain = p["expansion_per_k"] * (value - p["reference_temperature_k"])
            max_thermal_strain = max(max_thermal_strain, abs(thermal_strain))
            max_elastic_strain = max(max_elastic_strain, abs(strain - thermal_strain))
            strain_consistency = max(strain_consistency, abs(strain - t["strain"][index][cell]))
            expected_stress = p["young_modulus_pa"] * (strain - p["expansion_per_k"] * (value - p["reference_temperature_k"]))
            stress_consistency = max(stress_consistency, abs(expected_stress - t["stress_pa"][index][cell]))
            mr = max(mr, abs(ea * (strain - p["expansion_per_k"] * (value - p["reference_temperature_k"])) - force))
        if p["right_constraint"] == "free":
            mr = max(mr, abs(force - p["axial_force_n"]))
        endpoint_residual = max(endpoint_residual, abs(displacement[0]),
                                abs(displacement[-1]) if p["right_constraint"] == "fixed" else 0)
        mechanical_residual = max(mechanical_residual, mr)
        for key, value in (("energy_residual_j", er), ("equilibrium_residual_n", mr),
                           ("boundary_heat_input_w", flux), ("cumulative_boundary_heat_j", heat),
                           ("internal_energy_change_j", internal)):
            reconstructed_balances[key].append(value)
    checks.extend([
        _check("discrete_global_heat_balance", energy_residual, energy_limit, "J"),
        _check("finite_volume_step_equation", equation_residual, energy_limit, "J"),
        _check("axial_equilibrium_and_load", mechanical_residual, force_limit, "N"),
        _check("strain_field_matches_displacement", strain_consistency, 1e-12, "1"),
        _check("stress_field_matches_constitutive_law", stress_consistency * p["area_m2"], force_limit, "N"),
        _check("displacement_boundary_conditions", endpoint_residual, 1e-12, "m"),
        _check("small_strain_regime", max_strain, limits.get("max_strain", 0.01), "1"),
        _check("small_thermal_strain_regime", max_thermal_strain, limits.get("max_strain", 0.01), "1"),
        _check("small_elastic_strain_regime", max_elastic_strain, limits.get("max_strain", 0.01), "1"),
    ])
    temps = [value for row in t["temperature_k"] for value in row]
    bounds = [limits.get("temperature_min_k", 273.15), limits.get("temperature_max_k", 373.15)]
    regime_error = max(0.0, bounds[0] - min(temps), max(temps) - bounds[1])
    principle = [min(initial + [p["left_temperature_k"], p["right_temperature_k"]]),
                 max(initial + [p["left_temperature_k"], p["right_temperature_k"]])]
    principle_error = max(0.0, principle[0] - min(temps), max(temps) - principle[1])
    checks.append(_check("declared_temperature_regime", regime_error, 0.0, "K", details={"bounds_k": bounds}))
    checks.append(_check("discrete_maximum_principle", principle_error, 1e-10, "K"))
    for check_id, keys, unit, threshold in (
        ("reported_energy_balances_match_recomputation",
         ("energy_residual_j", "cumulative_boundary_heat_j", "internal_energy_change_j"), "J", energy_limit),
        ("reported_force_balance_matches_recomputation", ("equilibrium_residual_n",), "N", force_limit),
        ("reported_boundary_flux_matches_recomputation", ("boundary_heat_input_w",), "W",
         energy_limit / min(b - a for a, b in zip(times, times[1:]))),
    ):
        mismatch = max(abs(actual - expected) for key in keys
                       for actual, expected in zip(candidate["balances"][key], reconstructed_balances[key]))
        checks.append(_check(check_id, mismatch, threshold, unit))
    # A deterministic reference replay verifies that sensor identities,
    # uncertainties and seeded values describe this configured evolution.
    replay = simulate(plan)
    if (candidate.get("observations") != replay["observations"]
            or t.get("sensor_temperature_k") != replay["trajectory"]["sensor_temperature_k"]
            or t.get("sensor_displacement_m") != replay["trajectory"]["sensor_displacement_m"]):
        checks.append(_check("synthetic_sensor_contract", status="FAIL"))
    else:
        checks.append(_check("synthetic_sensor_contract", status="PASS", details={"synthetic": True, "sensor_count": len(sensors)}))
    indices = sorted(set([max(1, (len(times) - 1) // 2), len(times) - 1]))
    references = [(index, _analytic_cell_averages(p, n, initial, times[index])) for index in indices]
    if all(reference is not None for _, reference in references):
        errors = [max(abs(value - exact) for value, exact in zip(t["temperature_k"][index], reference[0]))
                  for index, reference in references]
        analytic_limit = limits.get("analytic_temperature_abs_k", 2.0)
        checks.append(_check("independent_fourier_cell_average_benchmark", max(errors), analytic_limit, "K",
                             details={"scope": "sampled_time_accuracy", "time_s": [times[index] for index in indices]}))
        benchmarks.append({"kind": "independent_dirichlet_heat_fourier_series", "status": "ASSESSED",
                           "time_s": [times[index] for index in indices], "max_error_k": errors,
                           "tail_bound_k": [reference[1] for _, reference in references],
                           "series_terms": [reference[2] for _, reference in references],
                           "initial_condition": "uniform", "comparison_state": "cell_average_temperature",
                           "scope": "sampled_time_accuracy"})
        if n * 2 <= 4096 and math.ceil(duration / (step / 2)) * n * 2 <= 2_000_000:
            fine, _ = _fields(p, n * 2, [initial[0]] * (n * 2), duration, step / 2)
            # Selected observation times are present exactly for ordinary clocks;
            # a final short step is always retained at the exact duration.
            fine_errors = []
            for index in indices:
                j = min(range(len(fine["time_s"])), key=lambda k: abs(fine["time_s"][k] - times[index]))
                reference = _analytic_cell_averages(p, n * 2, [initial[0]] * (n * 2), fine["time_s"][j])
                fine_errors.append(max(abs(a - b) for a, b in zip(fine["temperature_k"][j], reference[0])))
            slack = 1e-9
            # At equilibrium both errors can be at roundoff, so no artificial
            # requirement of a strictly positive convergence ratio is imposed.
            increase = max(max(0.0, fine_error - coarse_error) for fine_error, coarse_error in zip(fine_errors, errors))
            checks.append(_check("mesh_and_time_refinement_reduces_reference_error", increase, slack, "K"))
            benchmarks.append({"kind": "combined_mesh_time_refinement", "status": "ASSESSED",
                               "coarse_cells": n, "fine_cells": n * 2, "coarse_step_s": step,
                               "fine_step_s": step / 2, "coarse_error_k": errors,
                               "fine_error_k": fine_errors, "criterion": "fine_error <= coarse_error + 1e-9 K"})
        else:
            benchmarks.append({"kind": "combined_mesh_time_refinement", "status": "NOT_ASSESSED",
                               "reason": "Bounded verification work limit"})
    else:
        checks.append(_check("independent_fourier_cell_average_benchmark", status="NOT_ASSESSED",
                             details="Benchmark requires uniform initial heat and a bounded Fourier tail"))
        benchmarks.append({"kind": "independent_dirichlet_heat_fourier_series", "status": "NOT_ASSESSED",
                           "reason": "Unsupported initial condition or Fourier work limit"})
        benchmarks.append({"kind": "combined_mesh_time_refinement", "status": "NOT_ASSESSED",
                           "reason": "No independent reference for this initial condition"})
    steady = [p["left_temperature_k"] + (p["right_temperature_k"] - p["left_temperature_k"])
              * (i + 0.5) / n for i in range(n)]
    # This is an independent steady *operator* benchmark; it does not assert that
    # the transient result has already equilibrated at the configured duration.
    steady_next = _heat_step(p, steady, step)
    steady_error = max(abs(a - b) for a, b in zip(steady, steady_next))
    checks.append(_check("independent_linear_steady_solution", steady_error, 1e-9, "K"))
    benchmarks.append({"kind": "linear_dirichlet_steady_operator", "status": "ASSESSED", "max_error_k": steady_error})
    return _verification_record(plan, candidate, checks, benchmarks)


def compare_configurations(plan_a, result_a, plan_b, result_b):
    """Check finite-time commutation for a volume-averaging mesh reduction.

    This is an empirical error bound for these trajectories and conditions only.
    It does not establish that arbitrary abstraction or parameter changes commute.
    """
    pa, na, ia, da, sa, _ = _parameters(plan_a)
    pb, nb, ib, db, sb, _ = _parameters(plan_b)
    for plan, candidate in ((plan_a, result_a), (plan_b, result_b)):
        validate_candidate(plan, candidate)
    if na >= nb:
        fine_plan, fine, pf, nf, initial_f = plan_a, result_a, pa, na, ia
        coarse_plan, coarse, pc, nc, initial_c = plan_b, result_b, pb, nb, ib
    else:
        fine_plan, fine, pf, nf, initial_f = plan_b, result_b, pb, nb, ib
        coarse_plan, coarse, pc, nc, initial_c = plan_a, result_a, pa, na, ia
    if nf % nc:
        raise ValueError("Reduction requires a divisible uniform finite-volume mesh")
    thermal_nodes = {state["node_id"] for state in fine_plan["state_schema"]
                     if state["model_id"] == "thermal.rod-fv.v1"}
    representations = [representation for representation in fine_plan["representations"]
                       if representation["kind"] == "cell-average.v1"
                       and representation["source_node"] in thermal_nodes
                       and representation["target_cells"] == nc]
    if not representations:
        raise ValueError("Reduction requires a declared fine-to-coarse representation mapping")
    representation = representations[0]
    mapping_digest = digest({"representation": representation,
                             "fine_configuration_digest": fine_plan["configuration_digest"],
                             "coarse_configuration_digest": coarse_plan["configuration_digest"]})
    physical = [key for key in pf if key != "initial_temperature_k"]
    if any(pf[key] != pc[key] for key in physical) or plan_a.get("frame") != plan_b.get("frame"):
        raise ValueError("Reduction requires identical physical parameters, constraints and frame")
    if (da, sa) != (db, sb) or fine["trajectory"]["time_s"] != coarse["trajectory"]["time_s"]:
        raise ValueError("Reduction requires matching clocks")
    ratio = nf // nc
    reduced_initial = [math.fsum(initial_f[i * ratio:(i + 1) * ratio]) / ratio for i in range(nc)]
    if any(abs(a - b) > 1e-12 for a, b in zip(reduced_initial, initial_c)):
        raise ValueError("Coarse initial state is not the reduction of the fine initial state")
    errors_t, errors_u, reduced_t, reduced_u = [], [], [], []
    for ft, fu, ct, cu in zip(fine["trajectory"]["temperature_k"], fine["trajectory"]["displacement_m"],
                              coarse["trajectory"]["temperature_k"], coarse["trajectory"]["displacement_m"]):
        rt = [math.fsum(ft[i * ratio:(i + 1) * ratio]) / ratio for i in range(nc)]
        # Equal-length, constant-area cells have equal volumes. Coarse nodes
        # coincide with fine nodes, hence nodal restriction equals interpolation.
        ru = [fu[i * ratio] for i in range(nc + 1)]
        reduced_t.append(rt)
        reduced_u.append(ru)
        errors_t.append(max(abs(a - b) for a, b in zip(rt, ct)))
        errors_u.append(max(abs(a - b) for a, b in zip(ru, cu)))
    limits = coarse_plan.get("verification", {})
    threshold_t = limits.get("reduction_temperature_abs_k", 2.0)
    threshold_u = limits.get("reduction_displacement_abs_m", 2e-5)
    checks = [_check("thermal_reduction_commutation", max(errors_t), threshold_t, "K"),
              _check("mechanical_reduction_commutation", max(errors_u), threshold_u, "m")]
    return seal({
        "schema": COMPARISON_SCHEMA, "fine_plan_digest": fine_plan["plan_digest"],
        "coarse_plan_digest": coarse_plan["plan_digest"],
        "fine_configuration_digest": fine_plan["configuration_digest"],
        "coarse_configuration_digest": coarse_plan["configuration_digest"],
        "fine_candidate_digest": fine["record_digest"], "coarse_candidate_digest": coarse["record_digest"],
        "representation_id": representation["representation_id"], "mapping_digest": mapping_digest,
        "mapping": {"thermal": "constant_area_cell_volume_average", "mechanical": "nodal_linear_interpolation",
                    "representation_id": representation["representation_id"], "mapping_digest": mapping_digest,
                    "fine_cells": nf, "coarse_cells": nc, "group_size": ratio,
                    "information_discarded": "within_group_temperature_structure_and_interior_displacement_nodes"},
        "commutation": {"relation": "R(Phi_fine(t,x)) approximately Phi_coarse(t,R(x))",
                        "time_s": fine["trajectory"]["time_s"], "temperature_error_k": errors_t,
                        "displacement_error_m": errors_u, "max_temperature_error_k": max(errors_t),
                        "max_displacement_error_m": max(errors_u),
                        "reduced_temperature_k": reduced_t, "reduced_displacement_m": reduced_u},
        "checks": checks, "status": "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS",
        "validity": {"scope": "specified_parameters_initial_state_and_finite_observation_times",
                     "duration_s": da, "step_s": sa, "frame": plan_a.get("frame"),
                     "temperature_threshold_k": threshold_t, "displacement_threshold_m": threshold_u},
        "canonical_admission": False, "physical_validation_status": "not_assessed",
    })
