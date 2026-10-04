"""Closed scientific-system contracts and a deterministic reference compiler.

The v1 compiler supports one homogeneous rod, quasistatic axial mechanics and
a synthetic observation model.  Model identifiers resolve only to this trusted
registry; a specification cannot provide executable code or actuator effects.
State schemas and content identities do not constitute physical validation.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from .core.identities import content_identity


SPEC_SCHEMA = "ciw.system-spec.v1"
PLAN_SCHEMA = "ciw.system-plan.v1"
COMPILER_ID = "ciw.system-compiler.v1"
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")

# Registered contracts are defined by the implementation, never by user input.
_MODELS = {
    "thermal.rod-fv.v1": {
        "role": "thermal",
        "parameters": {
            "length_m": "m", "k_w_per_m_k": "W/(m*K)",
            "density_kg_per_m3": "kg/m^3", "heat_capacity_j_per_kg_k": "J/(kg*K)",
            "area_m2": "m^2", "initial_temperature_k": "K",
            "left_temperature_k": "K", "right_temperature_k": "K",
        },
        "ports": {"temperature": {"direction": "output", "quantity": "temperature", "unit": "K", "support": "cell"}},
    },
    "mechanical.axial-thermoelastic.v1": {
        "role": "mechanical",
        "parameters": {
            "youngs_modulus_pa": "Pa", "area_m2": "m^2", "expansion_per_k": "1/K",
            "reference_temperature_k": "K", "force_n": "N", "boundary": None,
        },
        "ports": {
            "temperature": {"direction": "input", "quantity": "temperature", "unit": "K", "support": "cell"},
            "displacement": {"direction": "output", "quantity": "displacement", "unit": "m", "support": "node"},
        },
    },
    "sensor.rod-observer.v1": {
        "role": "sensor",
        "parameters": {
            "position_m": "m", "temperature_bias_k": "K", "displacement_bias_m": "m",
            "temperature_std_k": "K", "displacement_std_m": "m", "seed": None,
        },
        "ports": {
            "temperature": {"direction": "input", "quantity": "temperature", "unit": "K", "support": "cell"},
            "displacement": {"direction": "input", "quantity": "displacement", "unit": "m", "support": "node"},
        },
    },
}
_EDGE_CONTRACTS = {
    ("thermal", "temperature", "mechanical", "temperature"): ("temperature", "K", "cell-average-to-strain.v1", "one-way-quasistatic"),
    ("thermal", "temperature", "sensor", "temperature"): ("temperature", "K", "cell-center-linear.v1", "observe"),
    ("mechanical", "displacement", "sensor", "displacement"): ("displacement", "m", "nodal-linear.v1", "observe"),
}


def _fields(value: Any, expected: set[str], path: str) -> None:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{path} requires exactly the fields {', '.join(sorted(expected))}")


def _number(value: Any, path: str, *, positive: bool = False, nonnegative: bool = False) -> int | float:
    if type(value) not in (int, float):
        raise ValueError(f"{path} must be a finite number")
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise ValueError(f"{path} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{path} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{path} must be nonnegative")
    return value


def _integer(value: Any, path: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{path} must be an integer in [{lower}, {upper}]")
    return value


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{path} must be a bounded identifier")
    return value


def _strings(value: Any, path: str, *, nonempty: bool = True) -> None:
    if type(value) is not list or (nonempty and not value):
        raise ValueError(f"{path} must be a nonempty string array")
    for item in value:
        if type(item) is not str or not item.strip() or len(item) > 2000:
            raise ValueError(f"{path} contains an invalid assumption")
    if len(value) != len(set(value)):
        raise ValueError(f"{path} contains duplicate assumptions")


def _json(value: Any, path: str = "specification", ancestors: set[int] | None = None) -> None:
    """Reject non-JSON objects, cycles, nonfinite values and unpaired surrogates."""
    if type(value) is str:
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError(f"{path} contains an unpaired Unicode surrogate")
        return
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        _number(value, path)
        return
    if type(value) not in (dict, list):
        raise ValueError(f"{path} must contain only JSON values")
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ValueError(f"{path} contains a cycle")
    ancestors.add(id(value))
    try:
        children = value.items() if type(value) is dict else enumerate(value)
        for key, item in children:
            if type(value) is dict and type(key) is not str:
                raise ValueError(f"{path} object keys must be strings")
            if type(value) is dict:
                _json(key, f"{path} key", ancestors)
            _json(item, f"{path}.{key}", ancestors)
    finally:
        ancestors.remove(id(value))


def _array(value: Any, path: str, minimum: int, maximum: int) -> list:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError(f"{path} must be an array with {minimum} to {maximum} entries")
    return value


def _quantity(value: Any, unit: str, path: str) -> int | float:
    _fields(value, {"value", "unit"}, path)
    if value["unit"] != unit:
        raise ValueError(f"{path} requires unit {unit}")
    return _number(value["value"], f"{path}.value")


def _value(node: dict, key: str) -> int | float:
    return node["parameters"][key]["value"]


def _roles(spec: dict) -> dict[str, dict]:
    return {_MODELS[node["model_id"]]["role"]: node for node in spec["nodes"]}


def _dependencies(nodes: list[dict], couplings: list[dict]) -> tuple[dict[str, list[str]], list[str]]:
    dependencies = {node["node_id"]: set() for node in nodes}
    for edge in couplings:
        dependencies[edge["target"]["node"]].add(edge["source"]["node"])
    remaining = {key: set(value) for key, value in dependencies.items()}
    order = []
    while remaining:
        ready = sorted(key for key, values in remaining.items() if not values)
        if not ready:
            raise ValueError("couplings contain a dependency cycle; implicit coupling is unsupported")
        for key in ready:
            order.append(key)
            del remaining[key]
        for values in remaining.values():
            values.difference_update(ready)
    return {key: sorted(value) for key, value in dependencies.items()}, order


def validate_spec(spec: dict) -> dict:
    """Validate the closed v1 contract and return an independent normalized copy."""
    _json(spec)
    _fields(spec, {"schema", "system_id", "version", "configuration_id", "frame", "clock", "discretization", "nodes", "couplings", "representations", "assumptions", "execution", "validity", "tolerances"}, "specification")
    if spec["schema"] != SPEC_SCHEMA:
        raise ValueError("Unsupported scientific-system schema")
    for field in ("system_id", "version", "configuration_id", "frame"):
        _identifier(spec[field], field)
    clock = spec["clock"]
    _fields(clock, {"unit", "duration", "dt"}, "clock")
    if clock["unit"] != "s":
        raise ValueError("clock requires unit s")
    duration = _number(clock["duration"], "clock.duration", positive=True)
    dt = _number(clock["dt"], "clock.dt", positive=True)
    steps = duration / dt
    if not math.isfinite(steps) or not 1 <= steps <= 2000 or not math.isclose(steps, round(steps), abs_tol=1e-9, rel_tol=0):
        raise ValueError("clock requires an integral number of steps in [1, 2000]")
    _fields(spec["discretization"], {"cells"}, "discretization")
    cells = _integer(spec["discretization"]["cells"], "discretization.cells", 2, 256)
    _strings(spec["assumptions"], "assumptions")

    nodes = _array(spec["nodes"], "nodes", 3, 3)
    node_ids = set()
    roles = {}
    for node in nodes:
        _fields(node, {"node_id", "model_id", "parameters"}, "node")
        node_id = _identifier(node["node_id"], "node.node_id")
        if node_id in node_ids:
            raise ValueError("nodes contain duplicate node_id values")
        node_ids.add(node_id)
        model_id = node["model_id"]
        if type(model_id) is not str or model_id not in _MODELS:
            raise ValueError(f"Unsupported registered model: {model_id}")
        model = _MODELS[model_id]
        if model["role"] in roles:
            raise ValueError("v1 requires exactly one thermal, mechanical and sensor model")
        roles[model["role"]] = node
        parameters = node["parameters"]
        _fields(parameters, set(model["parameters"]), f"node.{node_id}.parameters")
        for name, unit in model["parameters"].items():
            if unit is not None:
                _quantity(parameters[name], unit, f"node.{node_id}.{name}")
        if model["role"] == "mechanical" and parameters["boundary"] not in ("free", "fixed"):
            raise ValueError("mechanical.boundary must be free or fixed")
        if model["role"] == "sensor":
            _integer(parameters["seed"], "sensor.seed", 0, 2**32 - 1)
    if set(roles) != {"thermal", "mechanical", "sensor"}:
        raise ValueError("v1 requires exactly one thermal, mechanical and sensor model")
    thermal, mechanical, sensor = (roles[role] for role in ("thermal", "mechanical", "sensor"))
    for key in ("length_m", "k_w_per_m_k", "density_kg_per_m3", "heat_capacity_j_per_kg_k", "area_m2"):
        _number(_value(thermal, key), f"thermal.{key}", positive=True)
    for key in ("youngs_modulus_pa", "area_m2", "reference_temperature_k"):
        _number(_value(mechanical, key), f"mechanical.{key}", positive=True)
    if _value(thermal, "area_m2") != _value(mechanical, "area_m2"):
        raise ValueError("thermal and mechanical models must declare the same area_m2")
    if mechanical["parameters"]["boundary"] == "fixed" and _value(mechanical, "force_n") != 0:
        raise ValueError("fixed boundary requires force_n=0; an applied-load reaction formalism is unsupported")
    length = _value(thermal, "length_m")
    if not 0 <= _value(sensor, "position_m") <= length:
        raise ValueError("sensor.position_m must lie on the rod")
    for key in ("temperature_std_k", "displacement_std_m"):
        _number(_value(sensor, key), f"sensor.{key}", nonnegative=True)

    validity = spec["validity"]
    _fields(validity, {"temperature_min_K", "temperature_max_K", "strain_abs_limit"}, "validity")
    lower = _number(validity["temperature_min_K"], "validity.temperature_min_K", positive=True)
    upper = _number(validity["temperature_max_K"], "validity.temperature_max_K", positive=True)
    if lower >= upper:
        raise ValueError("validity requires temperature_min_K < temperature_max_K")
    _number(validity["strain_abs_limit"], "validity.strain_abs_limit", positive=True)
    for key in ("initial_temperature_k", "left_temperature_k", "right_temperature_k"):
        if not lower <= _value(thermal, key) <= upper:
            raise ValueError(f"thermal.{key} lies outside the declared validity interval")
    if not lower <= _value(mechanical, "reference_temperature_k") <= upper:
        raise ValueError("mechanical.reference_temperature_k lies outside the declared validity interval")
    tolerances = spec["tolerances"]
    _fields(tolerances, {"energy_balance_J", "equilibrium_N", "analytic_temperature_K", "reduction_temperature_K", "reduction_displacement_m"}, "tolerances")
    for key, value in tolerances.items():
        _number(value, f"tolerances.{key}", positive=True)
    # Valid finite inputs can nevertheless overflow or underflow assembled laws.
    try:
        dx = length / cells
        volumetric_capacity = _value(thermal, "density_kg_per_m3") * _value(thermal, "heat_capacity_j_per_kg_k")
        derived = [dx, volumetric_capacity,
                   _value(thermal, "k_w_per_m_k") * dt / (volumetric_capacity * dx * dx),
                   volumetric_capacity * _value(thermal, "area_m2") * dx,
                   _value(mechanical, "youngs_modulus_pa") * _value(mechanical, "area_m2")]
        if any(not math.isfinite(value) or value <= 0 for value in derived):
            raise ValueError("assembled model coefficients must be positive and finite")
        # Qualify the arithmetic assembled by the reference solver, including
        # its bath RHS and reaction terms.  Finite individual SI parameters do
        # not imply that products or sums fit the binary64 execution envelope.
        conductance = _value(thermal, "k_w_per_m_k") * _value(thermal, "area_m2") / dx
        capacity_rate = derived[3] / dt
        temperature_delta = max(abs(lower - _value(mechanical, "reference_temperature_k")),
                                abs(upper - _value(mechanical, "reference_temperature_k")))
        thermal_strain = abs(_value(mechanical, "expansion_per_k")) * temperature_delta
        elastic_strain = abs(_value(mechanical, "force_n")) / derived[4]
        coefficient_bounds = [conductance, capacity_rate, capacity_rate + 4 * conductance]
        arithmetic_bounds = [capacity_rate * temperature + 4 * conductance * temperature
                             for temperature in (lower, upper)]
        arithmetic_bounds.extend([
            volumetric_capacity * _value(thermal, "area_m2") * length * upper,
            thermal_strain, elastic_strain, thermal_strain + elastic_strain,
            derived[4] * thermal_strain,
            derived[4] * abs(_value(mechanical, "expansion_per_k")) * temperature_delta,
            length * (thermal_strain + elastic_strain),
        ])
        if (any(not math.isfinite(value) or value <= 0 for value in coefficient_bounds)
                or any(not math.isfinite(value) for value in arithmetic_bounds)):
            raise ValueError("assembled solver arithmetic must be finite")
    except (ZeroDivisionError, OverflowError) as exc:
        raise ValueError("assembled model coefficients must be positive and finite") from exc

    execution = spec["execution"]
    _fields(execution, {"engine", "effect", "retry", "resources"}, "execution")
    if execution["engine"] != "python.reference.v1":
        raise ValueError("Unsupported execution engine; deployment is selected separately")
    if execution["effect"] != "simulation" or execution["retry"] != "idempotent":
        raise ValueError("v1 permits only idempotent simulation effects; physical actions are unsupported")
    _fields(execution["resources"], {"cpu", "memory_mb"}, "execution.resources")
    _integer(execution["resources"]["cpu"], "execution.resources.cpu", 1, 64)
    _integer(execution["resources"]["memory_mb"], "execution.resources.memory_mb", 64, 65536)

    couplings = _array(spec["couplings"], "couplings", 3, 3)
    by_id = {node["node_id"]: node for node in nodes}
    edge_ids, target_ports, signatures = set(), set(), set()
    for edge in couplings:
        _fields(edge, {"coupling_id", "source", "target", "quantity", "unit", "frame", "mapping", "method", "timing"}, "coupling")
        edge_id = _identifier(edge["coupling_id"], "coupling.coupling_id")
        if edge_id in edge_ids:
            raise ValueError("couplings contain duplicate coupling_id values")
        edge_ids.add(edge_id)
        endpoint_roles = []
        for endpoint, direction in (("source", "output"), ("target", "input")):
            point = edge[endpoint]
            _fields(point, {"node", "port"}, f"coupling.{endpoint}")
            _identifier(point["node"], f"coupling.{endpoint}.node")
            _identifier(point["port"], f"coupling.{endpoint}.port")
            if point["node"] not in by_id:
                raise ValueError("coupling references an unknown node")
            model = _MODELS[by_id[point["node"]]["model_id"]]
            port = model["ports"].get(point["port"])
            if port is None or port["direction"] != direction:
                raise ValueError(f"coupling.{endpoint} is not a registered {direction} port")
            if edge["quantity"] != port["quantity"] or edge["unit"] != port["unit"]:
                raise ValueError("coupling quantity and unit must match both registered ports")
            endpoint_roles.extend((model["role"], point["port"]))
        target_port = (edge["target"]["node"], edge["target"]["port"])
        if target_port in target_ports:
            raise ValueError("couplings contain duplicate target ports")
        target_ports.add(target_port)
        signature = tuple(endpoint_roles)
        expected = _EDGE_CONTRACTS.get(signature)
        if expected is None or (edge["quantity"], edge["unit"], edge["mapping"], edge["method"]) != expected:
            raise ValueError("Unsupported coupling mapping or method for registered ports")
        if edge["frame"] != spec["frame"]:
            raise ValueError("coupling frame must match system frame; undeclared coordinate transforms are unsupported")
        _fields(edge["timing"], {"kind", "interval_s"}, "coupling.timing")
        if edge["timing"]["kind"] != "same-step" or _number(edge["timing"]["interval_s"], "coupling.timing.interval_s", positive=True) != dt:
            raise ValueError("coupling timing must use the same-step system clock")
        signatures.add(signature)
    if signatures != set(_EDGE_CONTRACTS):
        raise ValueError("couplings must connect every required model input exactly once")
    _dependencies(nodes, couplings)

    representations = _array(spec["representations"], "representations", 1, 16)
    representation_ids = set()
    for representation in representations:
        _fields(representation, {"representation_id", "kind", "source_node", "target_cells", "assumptions"}, "representation")
        identifier = _identifier(representation["representation_id"], "representation.representation_id")
        if identifier in representation_ids:
            raise ValueError("representations contain duplicate representation_id values")
        representation_ids.add(identifier)
        if representation["kind"] != "cell-average.v1" or representation["source_node"] != thermal["node_id"]:
            raise ValueError("v1 representations support only cell averaging of the thermal model")
        target_cells = _integer(representation["target_cells"], "representation.target_cells", 2, cells)
        if cells % target_cells:
            raise ValueError("cell-average representation requires target_cells to divide source cells")
        _strings(representation["assumptions"], "representation.assumptions")

    normalized = deepcopy(spec)
    for key, field in (("nodes", "node_id"), ("couplings", "coupling_id"), ("representations", "representation_id")):
        normalized[key].sort(key=lambda item: item[field])
    return normalized


def compile_spec(spec: dict) -> dict:
    """Compile admitted declarative contracts into a sealed reference plan."""
    checked = validate_spec(spec)
    roles = _roles(checked)
    thermal, mechanical, sensor = (roles[role] for role in ("thermal", "mechanical", "sensor"))
    cells = checked["discretization"]["cells"]
    dependencies, order = _dependencies(checked["nodes"], checked["couplings"])
    by_id = {node["node_id"]: node for node in checked["nodes"]}
    resources = checked["execution"]["resources"]
    semantics = {key: checked["execution"][key] for key in ("engine", "effect", "retry")}
    semantics.update({"physical_actions": False, "deployment_separate": True})
    state_schema = []
    for node_id in order:
        node = by_id[node_id]
        model = _MODELS[node["model_id"]]
        role = model["role"]
        variables = {
            "thermal": [{"name": "temperature", "unit": "K", "shape": [cells], "support": "cell", "kind": "continuous"}],
            "mechanical": [{"name": "displacement", "unit": "m", "shape": [cells + 1], "support": "node", "kind": "algebraic"},
                           {"name": "strain", "unit": "1", "shape": [cells], "support": "cell", "kind": "algebraic"},
                           {"name": "stress", "unit": "Pa", "shape": [cells], "support": "cell", "kind": "algebraic"}],
            "sensor": [{"name": "observed_temperature", "unit": "K", "shape": [1], "support": "point", "kind": "stochastic-observation"},
                       {"name": "observed_displacement", "unit": "m", "shape": [1], "support": "point", "kind": "stochastic-observation"}],
        }[role]
        state_schema.append({"state_id": f"{checked['configuration_id']}:{node_id}", "node_id": node_id,
                             "model_id": node["model_id"], "frame": checked["frame"],
                             "variables": variables, "ports": deepcopy(model["ports"])})
    model = {
        "length_m": _value(thermal, "length_m"), "area_m2": _value(thermal, "area_m2"),
        "conductivity_w_m_k": _value(thermal, "k_w_per_m_k"), "density_kg_m3": _value(thermal, "density_kg_per_m3"),
        "heat_capacity_j_kg_k": _value(thermal, "heat_capacity_j_per_kg_k"), "young_modulus_pa": _value(mechanical, "youngs_modulus_pa"),
        "expansion_per_k": _value(mechanical, "expansion_per_k"), "reference_temperature_k": _value(mechanical, "reference_temperature_k"),
        "initial_temperature_k": _value(thermal, "initial_temperature_k"), "left_temperature_k": _value(thermal, "left_temperature_k"),
        "right_temperature_k": _value(thermal, "right_temperature_k"), "axial_force_n": _value(mechanical, "force_n"),
        "right_constraint": mechanical["parameters"]["boundary"],
    }
    configuration_fields = ("configuration_id", "frame", "clock", "discretization", "nodes", "couplings", "representations", "assumptions", "validity")
    validity, tolerances = checked["validity"], checked["tolerances"]
    plan = {
        "schema": PLAN_SCHEMA, "compiler_id": COMPILER_ID, "specification": checked, "frame": checked["frame"],
        "spec_digest": content_identity(checked),
        "configuration_digest": content_identity({key: checked[key] for key in configuration_fields}),
        "state_schema": state_schema,
        "tasks": [{"task_id": node_id, "operation_id": by_id[node_id]["model_id"], "dependencies": dependencies[node_id],
                   "resources": deepcopy(resources), "execution_semantics": deepcopy(semantics)} for node_id in order],
        "mappings": deepcopy(checked["couplings"]), "representations": deepcopy(checked["representations"]),
        "model": model, "mesh": {"cells": cells},
        "clock": {"duration_s": checked["clock"]["duration"], "step_s": checked["clock"]["dt"]},
        "sensors": [{"sensor_id": sensor["node_id"], "node_id": sensor["node_id"],
                     **{key: _value(sensor, key) for key in _MODELS[sensor["model_id"]]["parameters"] if key != "seed"},
                     "seed": sensor["parameters"]["seed"]}],
        "verification": {"energy_abs_j": tolerances["energy_balance_J"], "equilibrium_abs_n": tolerances["equilibrium_N"],
                         "analytic_temperature_abs_k": tolerances["analytic_temperature_K"], "max_strain": validity["strain_abs_limit"],
                         "temperature_min_k": validity["temperature_min_K"], "temperature_max_k": validity["temperature_max_K"],
                         "reduction_temperature_abs_k": tolerances["reduction_temperature_K"],
                         "reduction_displacement_abs_m": tolerances["reduction_displacement_m"]},
        "canonical_admission": False, "physical_validation_status": "not_assessed",
    }
    plan["plan_digest"] = content_identity(plan)
    return plan


def demo_spec(cells: int = 16, boundary: str = "free", force_n: float | None = None) -> dict:
    """Return a declared SI polymer-rod workload; constants are illustrative."""
    _integer(cells, "cells", 2, 256)
    if force_n is None:
        force_n = 5.0 if boundary == "free" else 0.0
    reduced_cells = next((count for count in (4, 3, 2) if count <= cells and cells % count == 0), cells)
    def q(value: int | float, unit: str) -> dict:
        return {"value": value, "unit": unit}
    frame = "rod.material-axis.v1"
    def edge(identifier: str, source: str, port: str, target: str, quantity: str, unit: str, mapping: str, method: str) -> dict:
        return {"coupling_id": identifier, "source": {"node": source, "port": port},
                "target": {"node": target, "port": port}, "quantity": quantity, "unit": unit,
                "frame": frame, "mapping": mapping, "method": method,
                "timing": {"kind": "same-step", "interval_s": 5.0}}
    spec = {
        "schema": SPEC_SCHEMA, "system_id": "polymer-component", "version": "v1",
        "configuration_id": f"polymer-{boundary}-{cells}-cells", "frame": frame,
        "clock": {"unit": "s", "duration": 600.0, "dt": 5.0}, "discretization": {"cells": cells},
        "nodes": [
            {"node_id": "thermal", "model_id": "thermal.rod-fv.v1", "parameters": {
                "length_m": q(0.02, "m"), "k_w_per_m_k": q(0.25, "W/(m*K)"), "density_kg_per_m3": q(1200.0, "kg/m^3"),
                "heat_capacity_j_per_kg_k": q(1500.0, "J/(kg*K)"), "area_m2": q(1e-4, "m^2"),
                "initial_temperature_k": q(293.15, "K"), "left_temperature_k": q(313.15, "K"), "right_temperature_k": q(293.15, "K")}},
            {"node_id": "mechanical", "model_id": "mechanical.axial-thermoelastic.v1", "parameters": {
                "youngs_modulus_pa": q(1e9, "Pa"), "area_m2": q(1e-4, "m^2"), "expansion_per_k": q(8e-5, "1/K"),
                "reference_temperature_k": q(293.15, "K"), "force_n": q(force_n, "N"), "boundary": boundary}},
            {"node_id": "sensor", "model_id": "sensor.rod-observer.v1", "parameters": {
                "position_m": q(0.01, "m"), "temperature_bias_k": q(0.0, "K"), "displacement_bias_m": q(0.0, "m"),
                "temperature_std_k": q(0.05, "K"), "displacement_std_m": q(2e-7, "m"), "seed": 7}},
        ],
        "couplings": [
            edge("thermal-mechanical", "thermal", "temperature", "mechanical", "temperature", "K", "cell-average-to-strain.v1", "one-way-quasistatic"),
            edge("thermal-sensor", "thermal", "temperature", "sensor", "temperature", "K", "cell-center-linear.v1", "observe"),
            edge("mechanical-sensor", "mechanical", "displacement", "sensor", "displacement", "m", "nodal-linear.v1", "observe"),
        ],
        "representations": [{"representation_id": "thermal-reduced", "kind": "cell-average.v1", "source_node": "thermal", "target_cells": reduced_cells,
                             "assumptions": ["Equal-volume cell blocks; averaged temperature discards subcell gradients.", "Reduction error is measured for this workload and tolerance."]}],
        "assumptions": ["Homogeneous constant thermal and elastic material coefficients.",
                        "One-dimensional rod; transverse and viscoelastic dynamics are omitted.",
                        "Quasistatic axial mechanics with fixed left end and configured right constraint.",
                        "Synthetic observations; physical validation has not been assessed."],
        "execution": {"engine": "python.reference.v1", "effect": "simulation", "retry": "idempotent", "resources": {"cpu": 1, "memory_mb": 256}},
        "validity": {"temperature_min_K": 273.15, "temperature_max_K": 333.15, "strain_abs_limit": 0.01},
        "tolerances": {"energy_balance_J": 1e-8, "equilibrium_N": 1e-8, "analytic_temperature_K": 2.0, "reduction_temperature_K": 0.75, "reduction_displacement_m": 1e-6},
    }
    return validate_spec(spec)
