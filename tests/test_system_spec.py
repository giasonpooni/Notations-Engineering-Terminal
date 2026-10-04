"""Boundary and identity checks for the closed reference system compiler."""

from copy import deepcopy
import math

import pytest

from ciw.core.identities import content_identity
from ciw.system_spec import compile_spec, demo_spec, validate_spec


def _node(spec, role):
    return next(node for node in spec["nodes"] if node["node_id"] == role)


def test_compiler_constructs_typed_state_and_executable_dependencies():
    plan = compile_spec(demo_spec())
    assert plan["schema"] == "ciw.system-plan.v1"
    tasks = plan["tasks"]
    assert [task["task_id"] for task in tasks] == ["thermal", "mechanical", "sensor"]
    assert [task["dependencies"] for task in tasks] == [[], ["thermal"], ["mechanical", "thermal"]]
    states = {state["node_id"]: state for state in plan["state_schema"]}
    assert states["thermal"]["variables"][0]["shape"] == [16]
    assert states["mechanical"]["variables"][0]["shape"] == [17]
    assert states["mechanical"]["ports"]["temperature"]["unit"] == "K"
    assert states["sensor"]["variables"][0]["kind"] == "stochastic-observation"
    assert all(task["execution_semantics"]["effect"] == "simulation" for task in tasks)
    assert plan["canonical_admission"] is False
    assert plan["physical_validation_status"] == "not_assessed"


def test_compilation_is_independent_deterministic_and_order_normalized():
    spec = demo_spec()
    plan = compile_spec(spec)
    reordered = deepcopy(spec)
    for key in ("nodes", "couplings", "representations"):
        reordered[key].reverse()
    assert compile_spec(reordered) == plan
    assert plan["spec_digest"] == content_identity(plan["specification"])
    assert plan["plan_digest"] == content_identity({key: value for key, value in plan.items() if key != "plan_digest"})
    plan["specification"]["clock"]["duration"] = 1
    plan["tasks"][0]["resources"]["cpu"] = 4
    assert spec["clock"]["duration"] == 600.0
    assert spec["execution"]["resources"]["cpu"] == 1
    assert compile_spec(spec)["tasks"][0]["resources"]["cpu"] == 1


def test_configuration_family_retains_distinct_geometry_and_constraints():
    free = compile_spec(demo_spec(16, "free"))
    fixed = compile_spec(demo_spec(16, "fixed"))
    refined = compile_spec(demo_spec(32, "free"))
    assert len({plan["configuration_digest"] for plan in (free, fixed, refined)}) == 3
    assert free["model"]["right_constraint"] == "free"
    assert fixed["model"]["right_constraint"] == "fixed"
    assert refined["state_schema"][0]["variables"][0]["shape"] == [32]
    renamed = demo_spec()
    renamed["system_id"] = "another-investigation"
    changed_metadata = compile_spec(renamed)
    assert free["configuration_digest"] == changed_metadata["configuration_digest"]
    assert free["spec_digest"] != changed_metadata["spec_digest"]


def test_identifier_renaming_keeps_trusted_model_roles_and_graph_semantics():
    spec = demo_spec()
    replacements = {"thermal": "heat", "mechanical": "structure", "sensor": "probe"}
    for node in spec["nodes"]:
        node["node_id"] = replacements[node["node_id"]]
    for edge in spec["couplings"]:
        for endpoint in ("source", "target"):
            edge[endpoint]["node"] = replacements[edge[endpoint]["node"]]
    spec["representations"][0]["source_node"] = "heat"
    plan = compile_spec(spec)
    assert [task["task_id"] for task in plan["tasks"]] == ["heat", "structure", "probe"]
    assert plan["sensors"][0]["sensor_id"] == "probe"


@pytest.mark.parametrize("change, message", [
    (lambda spec: spec.update(schema="ciw.system-spec.v2"), "schema"),
    (lambda spec: spec.update(code="import os"), "exactly"),
    (lambda spec: _node(spec, "thermal").update(model_id="custom.user-python.v1"), "Unsupported registered model"),
    (lambda spec: _node(spec, "thermal").update(ports={}), "exactly"),
    (lambda spec: _node(spec, "mechanical")["parameters"].update(boundary="servo"), "boundary"),
    (lambda spec: _node(spec, "mechanical")["parameters"].update(boundary="fixed"), "force_n=0"),
    (lambda spec: _node(spec, "thermal")["parameters"]["length_m"].update(unit="mm"), "requires unit m"),
    (lambda spec: _node(spec, "mechanical")["parameters"]["area_m2"].update(value=2e-4), "same area"),
    (lambda spec: _node(spec, "sensor")["parameters"]["position_m"].update(value=.2), "lie on the rod"),
    (lambda spec: _node(spec, "sensor")["parameters"].update(seed=True), "seed"),
    (lambda spec: _node(spec, "sensor")["parameters"]["temperature_std_k"].update(value=-1), "nonnegative"),
    (lambda spec: spec["execution"].update(effect="actuator", retry="idempotent"), "physical actions"),
    (lambda spec: spec["execution"].update(retry="at-least-once"), "idempotent"),
    (lambda spec: spec["execution"].update(engine="python.eval.v1"), "engine"),
    (lambda spec: spec["execution"]["resources"].update(cpu=0), "resources.cpu"),
    (lambda spec: spec["execution"]["resources"].update(memory_mb=math.inf), "finite"),
    (lambda spec: spec["discretization"].update(cells=1), "cells"),
    (lambda spec: spec["discretization"].update(cells=257), "cells"),
    (lambda spec: spec["discretization"].update(cells=True), "cells"),
    (lambda spec: spec["clock"].update(duration=10005), "steps"),
    (lambda spec: spec["clock"].update(duration=601), "steps"),
    (lambda spec: spec["clock"].update(dt=0), "positive"),
    (lambda spec: spec["validity"].update(temperature_min_K=340), "temperature_min_K"),
    (lambda spec: _node(spec, "thermal")["parameters"]["left_temperature_k"].update(value=400), "validity"),
    (lambda spec: spec["tolerances"].update(energy_balance_J=-1), "positive"),
    (lambda spec: spec["tolerances"].update(analytic_temperature_K=0), "positive"),
    (lambda spec: spec["representations"][0].update(target_cells=3), "divide"),
    (lambda spec: spec["representations"][0].update(kind="learned-projection.v1"), "representations"),
    (lambda spec: spec["representations"][0].update(assumptions=[]), "assumptions"),
])
def test_compiler_refuses_unsupported_or_inconsistent_contracts(change, message):
    spec = demo_spec()
    change(spec)
    with pytest.raises(ValueError, match=message):
        compile_spec(spec)


@pytest.mark.parametrize("change, message", [
    (lambda spec: spec["couplings"][0].update(frame="lab.frame.v1"), "frame"),
    (lambda spec: spec["couplings"][0].update(unit="mm"), "unit"),
    (lambda spec: spec["couplings"][0].update(mapping="implicit-feedback.v1"), "mapping"),
    (lambda spec: spec["couplings"][0]["timing"].update(interval_s=10), "timing"),
    (lambda spec: spec["couplings"][0]["timing"].update(kind="asynchronous"), "timing"),
    (lambda spec: spec["couplings"][0]["source"].update(node="missing"), "unknown node"),
    (lambda spec: spec["couplings"][0]["source"].update(node="sensor"), "registered output port"),
    (lambda spec: spec["couplings"][0]["target"].update(port="force"), "registered input port"),
])
def test_couplings_require_registered_ports_frames_mappings_and_time(change, message):
    spec = demo_spec()
    change(spec)
    with pytest.raises(ValueError, match=message):
        validate_spec(spec)


def test_duplicate_nodes_edges_and_input_ports_are_rejected():
    spec = demo_spec()
    spec["nodes"][1]["node_id"] = spec["nodes"][0]["node_id"]
    with pytest.raises(ValueError, match="duplicate node_id"):
        validate_spec(spec)
    spec = demo_spec()
    spec["couplings"][1]["coupling_id"] = spec["couplings"][0]["coupling_id"]
    with pytest.raises(ValueError, match="duplicate coupling_id"):
        validate_spec(spec)
    spec = demo_spec()
    spec["couplings"][1] = deepcopy(spec["couplings"][2])
    spec["couplings"][1]["coupling_id"] = "duplicate-target"
    with pytest.raises(ValueError, match="duplicate target ports"):
        validate_spec(spec)


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -math.inf, "0.1", 10**400])
def test_quantities_cannot_smuggle_nonfinite_or_untyped_values(value):
    spec = demo_spec()
    _node(spec, "thermal")["parameters"]["length_m"]["value"] = value
    with pytest.raises(ValueError, match="finite number"):
        validate_spec(spec)


def test_json_cycles_and_python_objects_are_refused_before_copying():
    spec = demo_spec()
    spec["assumptions"].append(spec)
    with pytest.raises(ValueError, match="cycle"):
        validate_spec(spec)
    spec = demo_spec()
    spec["assumptions"].append(object())
    with pytest.raises(ValueError, match="only JSON"):
        validate_spec(spec)


def test_declared_analytic_tolerance_is_preserved_in_plan_and_bound_to_identity():
    original = compile_spec(demo_spec())
    spec = demo_spec()
    spec["tolerances"]["analytic_temperature_K"] = 0.25
    changed = compile_spec(spec)
    assert changed["verification"]["analytic_temperature_abs_k"] == 0.25
    assert changed["configuration_digest"] == original["configuration_digest"]
    assert changed["spec_digest"] != original["spec_digest"]
    assert changed["plan_digest"] != original["plan_digest"]


def test_finite_inputs_with_unrepresentable_assembled_coefficients_are_refused():
    spec = demo_spec()
    _node(spec, "thermal")["parameters"]["length_m"]["value"] = 1e-300
    _node(spec, "sensor")["parameters"]["position_m"]["value"] = 0
    with pytest.raises(ValueError, match="assembled model coefficients"):
        validate_spec(spec)


def test_finite_coefficients_cannot_overflow_the_thermal_boundary_rhs():
    spec = demo_spec()
    thermal, mechanical, sensor = (_node(spec, role) for role in ("thermal", "mechanical", "sensor"))
    thermal["parameters"]["length_m"]["value"] = 1e-100
    thermal["parameters"]["area_m2"]["value"] = 1e100
    thermal["parameters"]["k_w_per_m_k"]["value"] = 1e105
    mechanical["parameters"]["area_m2"]["value"] = 1e100
    sensor["parameters"]["position_m"]["value"] = 0
    with pytest.raises(ValueError, match="assembled solver arithmetic"):
        compile_spec(spec)
