"""Public control-plane qualification. Native engines are not represented by doubles.

Hook doubles below test interface/lifecycle behavior only. The Session tests
execute the existing built-in statistics and spectrum providers on synthetic data.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from ciw.adapters.protocol import AdapterRefusal, InstrumentManifest
from ciw.core.identities import content_identity, evidence_id
from ciw.core.covariance import create_covariance_artifact
from ciw.control_contracts import (
    artifact, bytes_ref, load, observation, record, save_new, specification_id,
    state, validate_artifact, validate_observation, validate_state,
)
from ciw.control_plane import (
    CapabilityRegistry, Choice, Fixed, Interval, ObservationBus, ParameterSpace,
    Port, builtin_registry, experiment, observations_from_run, plan_graph, run_graph,
)
from ciw.control_checks import compare, inspect_record, overall, verify, validate_comparison, validate_verification
from ciw.control_checkpoint import capture_checkpoint, restore_checkpoint, validate_checkpoint
from ciw.instruments import make_demo_run
from ciw.operations.registry import Operation
from ciw.operations.runner import seal
from ciw.operations.schemas import register_payload_validator
from ciw.session import Session
from ciw.net import inspect_path, main, _registry

REF = content_identity({"fixture": "contract-only"})
IDENTITY = {"model_id": "test-model.v1", "entity_id": "body-1", "execution_id": "source-1"}
PROVENANCE = {"provider": "contract-fixture", "sources": [REF], "semantics": "simulated"}


def obs(value=1.0, t=0.0, **changes):
    fields = dict(identity=IDENTITY, clock={"id": "test-clock", "time_s": t},
                  frame="test-scene/world", quantity="position", value=value, unit="m", provenance=PROVENANCE)
    fields.update(changes)
    return observation(**fields)


def series(values=(1.0, 2.0)):
    return [obs(value, float(i)) for i, value in enumerate(values)]


def st(variables=None, covariance=None):
    return state(identity=IDENTITY, clock={"id": "test-clock", "time_s": 0.0},
                 frame="test-scene/world", variables=variables or {"position": {"value": 1.0, "unit": "m"}},
                 provenance=PROVENANCE, uncertainty=covariance)


def covariance(matrix=((1.0, 0.4), (0.4, 2.0)), values=(1.0, 2.0)):
    return create_covariance_artifact(matrix=[list(row) for row in matrix],
        quantity_ids=["position[0]", "position[1]"], units=["m", "m"], frame="test-scene/world",
        reference_values=list(values), method="declared test covariance",
        basis={"kind": "estimated_state", "id": "test-state"},
        provenance={"provider": "contract-fixture", "source_evidence_ids": [REF], "source_covariance_ids": []},
        assumptions=["Synthetic contract fixture; no physical calibration"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "20", [], [True], [[1]], {}])
def test_observation_rejects_non_numeric_or_ambiguous(value):
    with pytest.raises((ValueError, OverflowError)):
        obs(value)


@pytest.mark.parametrize("field,value", [("unit", ""), ("frame", ""), ("quantity", ""),
    ("clock", {"id": "", "time_s": 0}), ("identity", {"entity_id": "e"}),
    ("provenance", {"provider": "p", "sources": [], "semantics": "observed"})])
def test_observation_requires_semantics(field, value):
    with pytest.raises(ValueError):
        obs(**{field: value})


def test_state_full_covariance_binding_and_detachment():
    cov = covariance()
    variables = {"position": {"value": [1.0, 2.0], "unit": "m"}}
    value = st(variables, cov)
    cov["matrix"][0][1] = 99
    variables["position"]["value"][0] = 99
    assert value["uncertainty"]["matrix"][0][1] == .4
    assert value["variables"]["position"]["value"][0] == 1.0
    validate_state(value)
    changed = deepcopy(value)
    changed["variables"]["position"]["unit"] = "kg"
    seal(changed)
    with pytest.raises(ValueError):
        validate_state(changed)


@pytest.mark.parametrize("change", ["frame", "reference", "axes", "partial"])
def test_state_covariance_resealed_mismatches_refuse(change):
    cov = covariance()
    variables = {"position": {"value": [1.0, 2.0], "unit": "m"}}
    if change == "frame":
        cov["frame"] = "other/world"
    elif change == "reference":
        cov["reference_values"][0] = 7
    elif change == "axes":
        cov["quantity_ids"] = ["a", "b"]
    else:
        variables["temperature"] = {"value": 300, "unit": "K"}
    with pytest.raises(ValueError):
        st(variables, cov)


def test_missing_values_are_not_zero_and_uncertainty_is_not_invented():
    assert obs(None)["value"] is None
    assert st()["uncertainty"] is None
    assert obs([1, None])["value"] == [1, None]


def projection_source(provenance, *, role="record_only", channel_semantics=None):
    manifest = InstrumentManifest("test.recorded-observation.v1", role=role,
                                  units={"position": "m"}, frames=("test-scene/world",))
    source = {"run_schema": "run.v1", "run_id": "retained-observation-fixture",
              "instrument": manifest.instrument_id, "time_s": [0.0], "render": {},
              "channels": {"position": {"unit": "m", "values": [1.0]}},
              "metadata": {"duration_s": 1.0, "sample_count": 1,
                           "coordinate_frame": "test-scene/world", "manifest": manifest.to_dict(),
                           "provenance": provenance}}
    if channel_semantics is not None:
        source["channels"]["position"]["semantics"] = channel_semantics
    source["evidence_id"] = evidence_id(source)
    return source


def project(source, *, channel="position", semantics="observed"):
    return observations_from_run(source, channel=channel, entity_id="test/body", clock_id="test/clock",
                                 model_id="test.model.v1", semantics=semantics)


@pytest.mark.parametrize("provenance", [
    {}, {"semantics": "reference"}, {"semantics": "simulated"}, {"semantics": "estimated"},
    {"semantics": "observed", "kind": "synthetic"},
    {"semantics": "observed", "source": "computed"},
    {"semantics": "observed", "observed": False},
    {"semantics": "observed", "synthetic": True},
])
def test_projection_cannot_promote_computed_or_unknown_origin_to_observed(provenance):
    with pytest.raises(ValueError, match="Observed projection requires"):
        project(projection_source(provenance))


@pytest.mark.parametrize("role,channel_semantics", [
    ("synthetic_model_configuration", None), ("state_estimator", None),
    ("record_only", "estimated"), ("record_only", "reference"),
])
def test_projection_observed_declaration_cannot_override_computed_role_or_channel(role, channel_semantics):
    with pytest.raises(ValueError, match="Observed projection requires"):
        project(projection_source({"semantics": "observed"}, role=role, channel_semantics=channel_semantics))


@pytest.mark.parametrize("declared", [False, True])
def test_analytic_source_cannot_be_projected_as_sensor_acquisition(declared):
    source = make_demo_run()
    if declared:
        source["metadata"]["provenance"]["semantics"] = "observed"
        source["evidence_id"] = evidence_id(source)
    with pytest.raises(ValueError, match="Observed projection requires"):
        project(source, channel="q")


@pytest.mark.parametrize("semantics", ["reference", "simulated", "estimated"])
def test_projection_retains_compatible_nonacquisition_semantics(semantics):
    source = make_demo_run()
    result = project(source, channel="q", semantics=semantics)
    assert result[0]["provenance"] == {
        "provider": source["instrument"], "sources": [source["evidence_id"]], "semantics": semantics}
    assert result[0]["value"] == source["channels"]["q"]["values"][0]


def test_projection_retains_explicit_observed_source_declaration_without_new_occurrence():
    source = projection_source({"semantics": "observed"})
    result = project(source)
    assert result[0]["provenance"]["semantics"] == "observed"
    assert result[0]["provenance"]["sources"] == [source["evidence_id"]]
    assert result[0]["identity"]["execution_id"] is None


def test_artifact_hash_and_specification_identity():
    value = artifact(b"mesh bytes", media_type="model/gltf-binary", producer="test-author", experiment_id="test")
    validate_artifact(value, b"mesh bytes")
    with pytest.raises(ValueError):
        validate_artifact(value, b"different bytes")
    arguments = dict(model="test-model.v1", artifacts=[value["sha256"]], parameters={"mass": 1.0},
                     initial_state=st(), runtime={"provider": "test", "revision": "1"}, seed=1)
    first = specification_id(**arguments)
    assert first == specification_id(**arguments)
    assert first != specification_id(**{**arguments, "parameters": {"mass": 2.0}})
    assert first.startswith("sha256:") and not first.startswith("execution-")


def test_bus_no_drop_no_mutation_and_independent_clocks():
    bus = ObservationBus(2)
    original = obs()
    bus.publish(original)
    original["value"] = 999
    assert bus.snapshot()["observations"][0]["value"] == 1.0
    with pytest.raises(AdapterRefusal, match="order"):
        bus.publish(obs())
    bus.publish(obs(clock={"id": "another-clock", "time_s": -2}))
    with pytest.raises(AdapterRefusal, match="full"):
        bus.publish(obs(t=3))
    assert len(bus.snapshot()["observations"]) == 2
    snapshot = bus.snapshot()
    snapshot["observations"].clear()
    assert len(bus.snapshot()["observations"]) == 2


def test_bus_concurrent_capacity():
    bus = ObservationBus(4)
    def publish(i):
        try:
            bus.publish(obs(identity={**IDENTITY, "entity_id": f"body-{i}"}))
            return True
        except AdapterRefusal:
            return False
    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(publish, range(20))) == 4
    assert len(bus.snapshot()["observations"]) == 4


def test_parameter_spaces_enumerate_sample_and_detach():
    choices = ["rk4", "verlet"]
    space = ParameterSpace({"mass": Interval(.1, 1., "kg"), "solver": Choice(choices), "gravity": Fixed(9.8, "m/s^2")})
    choices.append("external-mutation")
    assert len(space.grid({"mass": [.1, 1.]})) == 4
    assert space.sample(5, seed=17) == space.sample(5, seed=17)
    assert space.sample(5, seed=17) != space.sample(5, seed=18)
    assert ParameterSpace.from_dict(space.to_dict()).to_dict() == space.to_dict()
    with pytest.raises(ValueError):
        space.grid({"mass": [.1, 1.]}, max_cases=3)
    with pytest.raises(ValueError):
        space.grid({"mass": [0.]})


@pytest.mark.parametrize("spec", [Interval(2, 1), Interval(True, 2), Choice(()), Choice((1, 1)), Fixed(None)])
def test_invalid_parameter_domains(spec):
    with pytest.raises(ValueError):
        ParameterSpace({"x": spec})


@pytest.mark.parametrize("values", [{"x": True}, {"x": -1}, {}, {"x": 1, "y": 2}])
def test_parameter_domain_refuses_bad_assignments(values):
    with pytest.raises(ValueError):
        ParameterSpace({"x": Interval(0, 1)}).validate(values)


def test_comparison_metrics_and_numeric_policy():
    value = compare(series((1., 3.)), series((1., 2.)), atol=.5)
    assert value["outcome"]["status"] == "FAIL"
    assert value["outcome"]["metrics"]["rmse"] == pytest.approx(1 / 2**.5)
    assert value["outcome"]["metrics"]["max_abs_error"] == 1.
    assert value["outcome"]["component_count"] == 2
    validate_comparison(value)
    assert compare(series(), series(), atol=0)["outcome"]["status"] == "PASS"
    assert compare(series((1., 2.1)), series(), atol=0, rtol=.1)["outcome"]["status"] == "PASS"


@pytest.mark.parametrize("mode", ["empty", "missing", "length", "timestamp", "frame", "unit", "clock", "entity", "model", "shape"])
def test_incomparable_evidence_never_passes(mode):
    left, right = series(), series()
    if mode == "empty":
        right = []
    elif mode == "missing":
        right[0] = obs(None)
    elif mode == "length":
        right.pop()
    elif mode == "timestamp":
        right[1] = obs(2., t=1.1)
    elif mode in {"frame", "unit"}:
        right = [obs(v, float(i), **{mode: "other"}) for i, v in enumerate((1., 2.))]
    elif mode == "clock":
        right = [obs(v, clock={"id": "another", "time_s": float(i)}) for i, v in enumerate((1., 2.))]
    elif mode in {"entity", "model"}:
        right = [obs(v, float(i), identity={**IDENTITY, f"{mode}_id": "other"}) for i, v in enumerate((1., 2.))]
    else:
        right = [obs([v], float(i)) for i, v in enumerate((1., 2.))]
    result = compare(left, right, atol=0)
    assert result["outcome"]["status"] == "INDETERMINATE"
    assert result["outcome"]["metrics"] is None
    validate_comparison(result)


@pytest.mark.parametrize("atol,rtol", [(-1, 0), (0, -1), (float("nan"), 0), (True, 0)])
def test_comparison_refuses_invalid_policy(atol, rtol):
    with pytest.raises(ValueError):
        compare(series(), series(), atol=atol, rtol=rtol)


def test_resealed_forged_pass_does_not_validate():
    result = compare(series((1, 9)), series(), atol=0)
    result["outcome"]["status"] = "PASS"
    seal(result)
    with pytest.raises(ValueError, match="contradicts"):
        validate_comparison(result)
    check = verify("bound", "bounded", series(), minimum=0, maximum=1, unit="m")
    check["outcome"]["status"] = "PASS"
    seal(check)
    with pytest.raises(ValueError):
        validate_verification(check)


@pytest.mark.parametrize("kind,policy", [("bounded", {"minimum": 0, "maximum": 2, "unit": "m"}),
    ("less_than", {"limit": 3, "unit": "m"}), ("conserved", {"tolerance": 0, "unit": "m"})])
def test_three_value_assertions(kind, policy):
    good = verify("test", kind, series((1., 1.)), **policy)
    assert good["outcome"]["status"] == "PASS"
    assert verify("test", kind, series((1., 9.)), **policy)["outcome"]["status"] == "FAIL"
    assert verify("test", kind, None, **policy)["outcome"]["status"] == "INDETERMINATE"
    assert verify("test", kind, series((1., None)), **policy)["outcome"]["status"] == "INDETERMINATE"
    assert good["verification_id"] is None
    assert good["state_admission"] == "not_performed"
    validate_verification(good)


def test_no_vacuous_conservation_or_overall_success():
    assert verify("mass", "conserved", [obs()], tolerance=0, unit="m")["outcome"]["status"] == "INDETERMINATE"
    assert overall([]) == "INDETERMINATE"
    passed = verify("close", "close_to", compare(series(), series(), atol=0))
    unknown = verify("close", "close_to", compare([], series(), atol=0))
    failed = verify("close", "close_to", compare(series((1, 8)), series(), atol=0))
    assert overall([passed]) == "PASS"
    assert overall([passed, unknown]) == "INDETERMINATE"
    assert overall([unknown, failed]) == "FAIL"


def test_covariance_assertion_preserves_full_matrix_and_binding():
    policy = dict(quantity_ids=["position[0]", "position[1]"], units=["m", "m"], frame="test-scene/world", margin=1e-12)
    cov = covariance()
    result = verify("cov", "covariance_positive_definite", cov, **policy)
    assert result["outcome"]["status"] == "PASS"
    assert result["evidence"]["matrix"][0][1] == .4
    singular = covariance(((0, 0), (0, 1)))
    assert verify("cov", "covariance_positive_definite", singular, **policy)["outcome"]["status"] == "FAIL"
    with pytest.raises(ValueError):
        verify("cov", "covariance_positive_definite", cov, **{**policy, "frame": "other"})


def node(name="statistics", operation="statistics.v1", *, inputs=None, depends=None, parameters=None):
    return {"node_id": name, "operation_id": operation, "inputs": inputs or {}, "depends_on": depends or [],
            "parameters": parameters or {}}


def test_builtin_registry_is_data_only_until_explicit_binding():
    registry = builtin_registry()
    catalog = registry.catalog("statistics")
    assert set(catalog["operations"]) == {"statistics.v1"}
    assert catalog["authorizes_execution"] is False
    assert catalog["operations"]["statistics.v1"]["bound"] is False
    with pytest.raises(AdapterRefusal):
        registry.operations.get("statistics.v1")
    assert registry.catalog("simulate")["operations"] == {}


def test_net_binding_keeps_separate_ciw_specialists_unadvertised():
    from ciw.operations.registry import default_registry

    # A CIW specialist may be executable in CIW without becoming a declared NET
    # provider or inheriting the oscillator's runtime identity.
    assert default_registry().get("legibility.compile.v1").role == "backend"
    registry = builtin_registry(bind=True)
    assert set(registry.catalog()["operations"]) == {
        "statistics.v1", "spectrum.periodogram.v1"}
    assert all(item["bound"] for item in registry.catalog()["operations"].values())
    with pytest.raises(AdapterRefusal):
        registry.operations.get("legibility.compile.v1")
    with pytest.raises(AdapterRefusal):
        registry.bind(default_registry().get("legibility.compile.v1"))


@pytest.mark.parametrize("nodes", [
    [node("x"), node("x")], [node("x", depends=["missing"])],
    [node("a", depends=["b"]), node("b", depends=["a"])], [node("a", operation="unversioned")],
])
def test_graph_structure_refuses_before_execution(nodes):
    with pytest.raises(ValueError):
        experiment("bad", model_id="test", nodes=nodes)


def test_real_session_graph_fresh_reexecution_and_read_only_workspace(tmp_path):
    registry = builtin_registry(bind=True)
    session = Session(make_demo_run(), tmp_path / "session", operations=registry.operations)
    graph = experiment("test", model_id="analytic-damped-oscillator.v1", nodes=[
        node("stats"), node("spectrum", "spectrum.periodogram.v1", depends=["stats"])])
    first, second = run_graph(session, graph, registry), run_graph(session, graph, registry)
    assert first["status"] == second["status"] == "completed"
    assert len(session.executions) == 4
    for name in ("stats", "spectrum"):
        a, b = first["nodes"][name], second["nodes"][name]
        assert a["execution"]["execution_id"] != b["execution"]["execution_id"]
        assert a["result"]["result_id"] != b["result"]["result_id"]
        assert a["result"]["data"] == b["result"]["data"]
        assert a["result"]["verification_status"] == "not_verified"
    inspect_record(first)
    path = session.save_workspace(tmp_path / "workspace.json")
    before = path.read_bytes()
    snapshot = inspect_path(path)
    assert len(snapshot["executions"]) == 4
    assert path.read_bytes() == before


# Explicit trusted fixture schema. No saved document registers this function.
OP_A, OP_B = "control-fixture.emit.v1", "control-fixture.consume.v1"


def fixture_validator(operation, data, run, parameters, selection):
    if set(data) != {"state"}:
        raise ValueError("Invalid fixture output")
    validate_state(data["state"])


register_payload_validator(OP_A, fixture_validator)
register_payload_validator(OP_B, fixture_validator)


def fixture_registry(*, fail=False, wrong_frame=False):
    registry = CapabilityRegistry()
    runtime = {"provider": "contract-fixture", "revision": "1"}
    manifest = InstrumentManifest("contract-fixture", supported_operations=(OP_A, OP_B))
    port = Port("ciw.state.v1", frame="test-scene/world").to_dict()
    out = {"state": {"type": port, "path": ["data", "state"]}}
    registry.advertise(manifest, runtime=runtime, capabilities={OP_A: ["emit"], OP_B: ["consume"]},
                       inputs={OP_B: {"initial_state": port}}, outputs={OP_A: out, OP_B: out})
    calls = []
    def emit(run, parameters):
        calls.append("emit")
        if fail:
            raise AdapterRefusal("fixture_refusal", "Deliberate contract challenge")
        value = st()
        if wrong_frame:
            value["frame"] = "other/world"
            seal(value)
        return {"state": value}
    def consume(run, parameters):
        calls.append("consume")
        return {"state": parameters["initial_state"]}
    registry.bind(Operation(OP_A, "backend", emit, lambda: deepcopy(runtime)))
    registry.bind(Operation(OP_B, "backend", consume, lambda: deepcopy(runtime)))
    graph = experiment("typed-fixture", model_id="test-model.v1", nodes=[node("emit", OP_A),
        node("consume", OP_B, inputs={"initial_state": {"node_id": "emit", "port": "state"}})])
    return registry, graph, runtime, calls


def test_typed_graph_passes_exact_artifact_via_existing_session(tmp_path):
    registry, graph, runtime, calls = fixture_registry()
    session = Session(make_demo_run(), tmp_path, operations=registry.operations)
    result = run_graph(session, graph, registry)
    assert result["status"] == "completed" and calls == ["emit", "consume"]
    assert result["nodes"]["consume"]["execution"]["parameters"]["initial_state"] == st()
    inspect_record(result)


@pytest.mark.parametrize("mode", ["refusal", "output-frame", "runtime"])
def test_failed_upstream_never_dispatches_descendants(tmp_path, mode):
    registry, graph, runtime, calls = fixture_registry(fail=mode == "refusal", wrong_frame=mode == "output-frame")
    if mode == "runtime":
        runtime["revision"] = "changed"
    session = Session(make_demo_run(), tmp_path, operations=registry.operations)
    result = run_graph(session, graph, registry)
    assert result["status"] == "incomplete"
    assert result["nodes"]["consume"]["status"] == "blocked"
    assert "consume" not in calls
    if mode == "runtime":
        assert calls == []
    assert len(session.executions) == 1
    inspect_record(result)


def test_static_port_mismatch_and_no_available_code_do_not_run(tmp_path):
    registry, graph, _, calls = fixture_registry()
    registry._contracts[OP_B]["inputs"]["initial_state"]["frame"] = "another/world"
    with pytest.raises(ValueError, match="mismatch"):
        plan_graph(graph, registry)
    assert calls == []
    unbound = builtin_registry()
    session = Session(make_demo_run(), tmp_path, operations=unbound.operations)
    with pytest.raises(AdapterRefusal):
        run_graph(session, experiment("x", model_id="x", nodes=[node()]), unbound)
    assert not session.executions


class HookFixture:
    """Opaque lifecycle double, not an engine, simulator or numerical reference."""
    def __init__(self, owner="one"):
        self.info = {"runtime": {"provider": "hook-fixture", "revision": "1"}, "model_id": "model.v1",
                     "simulation_id": "logical-sim", "owner_id": owner, "state_revision": 0,
                     "clock": {"id": "declared-clock", "time_s": 0.0}}
        self.calls = []
    def identity(self):
        return deepcopy(self.info)
    def snapshot(self):
        self.calls.append("snapshot")
        return json.dumps(self.info["clock"]).encode()
    def restore(self, data):
        self.calls.append("restore")
        self.info["clock"] = json.loads(data)
        self.info["state_revision"] += 1
    def step(self, dt):
        self.info["clock"]["time_s"] += dt
        self.info["state_revision"] += 1
    def observe(self):
        return self.identity()


def test_checkpoint_owned_bytes_fresh_owner_and_branch_metadata():
    source = HookFixture()
    source.step(2)
    checkpoint, payload = capture_checkpoint(source, experiment_id="test")
    calls = list(source.calls)
    inspect_record(checkpoint)
    assert source.calls == calls
    target = HookFixture("two")
    restored = restore_checkpoint(target, checkpoint, payload)
    assert restored["clock"]["time_s"] == 2
    assert restored["owner_id"] == "two"
    assert source.info["owner_id"] == "one"
    branch = experiment("counterfactual", model_id="model.v1", nodes=[node()],
                        parameters={"mass": 2.0}, parent_checkpoint=checkpoint["record_digest"])
    assert branch["parent_checkpoint"] == checkpoint["record_digest"]


@pytest.mark.parametrize("mode", ["tamper", "same-owner", "used-owner", "runtime", "model", "clock"])
def test_checkpoint_mismatch_refuses_before_restore(mode):
    source, target = HookFixture(), HookFixture("two")
    checkpoint, payload = capture_checkpoint(source, experiment_id="test")
    if mode == "tamper":
        payload += b"x"
    elif mode == "same-owner":
        target = source
    elif mode == "used-owner":
        target.step(1)
    elif mode == "runtime":
        target.info["runtime"]["revision"] = "different"
    elif mode == "model":
        target.info["model_id"] = "other"
    else:
        target.info["clock"]["id"] = "other"
    with pytest.raises(ValueError):
        restore_checkpoint(target, checkpoint, payload)
    assert "restore" not in target.calls


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b''])
def test_strict_bounded_json_loader(tmp_path, raw):
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load(path)


def test_create_only_atomic_output_and_competing_writers(tmp_path):
    path = tmp_path / "record.json"
    save_new(path, st())
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_new(path, obs())
    assert path.read_bytes() == before
    def write(i):
        try:
            save_new(tmp_path / "concurrent.json", {"writer": i})
            return True
        except FileExistsError:
            return False
    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(write, range(12))) == 1
    assert type(load(tmp_path / "concurrent.json")["writer"]) is int


def test_cli_catalog_roundtrip_never_restores_execution_authority(tmp_path, capsys):
    path = tmp_path / "catalog.json"
    save_new(path, builtin_registry(bind=True).catalog())
    assert not any(item["bound"] for item in _registry(path).catalog()["operations"].values())
    assert main(["capabilities", "simulate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["operations"] == {}
    assert main(["providers", "--catalog", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["authorizes_execution"] is False


def test_cli_create_inspect_compare_exit_codes_and_non_overwrite(tmp_path, capsys):
    output = tmp_path / "demo"
    assert main(["demo", "--output-dir", str(output)]) == 0
    capsys.readouterr()
    originals = {str(p): p.read_bytes() for p in output.iterdir() if p.is_file()}
    assert main(["demo", "--output-dir", str(output)]) == 1
    capsys.readouterr()
    assert originals == {str(p): p.read_bytes() for p in output.iterdir() if p.is_file()}
    assert main(["inspect", "workspace", str(output / "workspace.json"), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["executions"]) == 2
    left, right = tmp_path / "left.json", tmp_path / "right.json"
    save_new(left, record("observation-stream", observations=series()))
    save_new(right, record("observation-stream", observations=series((1., 9.))))
    assert main(["compare", str(left), str(right), "--atol", "0", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["outcome"]["status"] == "FAIL"
    assert main(["compare", str(left), str(left), "--atol", "0", "--json"]) == 0
    capsys.readouterr()
    unknown = tmp_path / "unknown.json"
    save_new(unknown, record("observation-stream", observations=[]))
    assert main(["compare", str(left), str(unknown), "--atol", "0", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["outcome"]["status"] == "INDETERMINATE"


def test_inspector_fresh_process_never_calls_numerical_provider(tmp_path):
    registry = builtin_registry(bind=True)
    session = Session(make_demo_run(), tmp_path / "source", operations=registry.operations)
    run_graph(session, experiment("x", model_id="oscillator", nodes=[node()]), registry)
    path = session.save_workspace(tmp_path / "workspace.json")
    script = '''
import sys
from pathlib import Path
import ciw.operations.runner as runner
import ciw.adapters.oscillator as oscillator
from ciw.net import inspect_path

def forbidden(*args, **kwargs):
    raise AssertionError("Provider calculation attempted during inspection")
runner.execute = forbidden
oscillator.compute_statistics = forbidden
oscillator.compute_spectrum = forbidden
value = inspect_path(Path(sys.argv[1]))
assert len(value["executions"]) == 1
'''
    completed = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("field,value", [("unit", "kg"), ("frame", "another/world"),
    ("value", [1.0]), ("provenance", {**PROVENANCE, "semantics": "observed"})])
def test_one_bus_stream_cannot_change_descriptor(field, value):
    bus = ObservationBus()
    bus.publish(obs(t=0))
    with pytest.raises(AdapterRefusal, match="changed"):
        bus.publish(obs(t=1, **{field: value}))
    assert len(bus.snapshot()["observations"]) == 1


def test_huge_integer_and_invalid_optional_objects_refuse():
    with pytest.raises(ValueError):
        obs(10 ** 1000)
    with pytest.raises(ValueError):
        st(covariance=[])
    with pytest.raises(ValueError):
        experiment("x", model_id="x", nodes=[node()], parameters=[])
    with pytest.raises(ValueError):
        artifact(b"x", media_type="text/plain", producer="test", experiment_id="x", metadata=[])
    bad = ParameterSpace({"x": Choice(("a", "b"))}).to_dict()
    bad["parameters"]["x"]["values"] = "ab"
    seal(bad)
    with pytest.raises(ValueError):
        ParameterSpace.from_dict(bad)


@pytest.mark.parametrize("change", ["parameters", "runtime", "source", "operation", "result_id",
    "verification", "dependency", "port", "aggregate", "duplicate_execution", "unsupported_block"])
def test_resealed_graph_binding_forgery_refuses(tmp_path, change):
    registry, graph, _, _ = fixture_registry()
    session = Session(make_demo_run(), tmp_path, operations=registry.operations)
    value = run_graph(session, graph, registry)
    item = value["nodes"]["consume"]
    ex, result = item["execution"], item["result"]
    if change == "parameters":
        ex["parameters"]["channel"] = result["parameters"]["channel"] = "v"
    elif change == "runtime":
        ex["runtime"]["revision"] = "forged"
        result["runtime"]["revision"] = "forged"
    elif change == "source":
        result["evidence_id"] = REF
    elif change == "operation":
        result["operation_id"] = OP_A
    elif change == "result_id":
        result["result_id"] = "result-" + "0" * 32
    elif change == "verification":
        result["verification_id"] = "not-a-proof"
        result["verification_status"] = "verified"
    elif change == "dependency":
        value["nodes"]["emit"] = {"status": "error", "refusal": {"code": "x", "message": "x"}}
        value["status"] = "incomplete"
    elif change == "port":
        value["contracts"][OP_B]["inputs"]["initial_state"]["frame"] = "other/world"
    elif change == "aggregate":
        value["status"] = "incomplete"
    elif change == "duplicate_execution":
        ex["execution_id"] = result["execution_id"] = value["nodes"]["emit"]["execution"]["execution_id"]
    else:
        value["nodes"]["consume"] = {"status": "blocked", "dependencies": []}
        value["status"] = "incomplete"
    seal(ex)
    seal(result)
    seal(value)
    with pytest.raises(ValueError):
        inspect_record(value)


def test_declared_experiment_parameter_cannot_be_silently_replaced_by_edge():
    registry, graph, _, calls = fixture_registry()
    graph["parameters"]["initial_state"] = "literal-conflict"
    seal(graph)
    with pytest.raises(ValueError):
        plan_graph(graph, registry)
    assert calls == []


def test_snapshot_change_during_capture_refuses():
    provider = HookFixture()
    original = provider.snapshot
    def snapshot():
        payload = original()
        provider.step(1)
        return payload
    provider.snapshot = snapshot
    with pytest.raises(ValueError, match="changed"):
        capture_checkpoint(provider, experiment_id="test")


def test_restore_wrong_clock_is_not_success():
    source, target = HookFixture(), HookFixture("fresh")
    source.step(1)
    checkpoint, payload = capture_checkpoint(source, experiment_id="test")
    target.restore = lambda payload: None
    with pytest.raises(ValueError, match="clock"):
        restore_checkpoint(target, checkpoint, payload)
