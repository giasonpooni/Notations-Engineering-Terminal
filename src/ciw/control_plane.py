"""Optional, bounded NET coordination over the existing CIW Session and operations.

No optimizer, physics implementation, engine clock, plugin loader or evidence store.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from itertools import product
import math
import random
import threading
from typing import Any
import uuid

from .adapters.protocol import AdapterRefusal, InstrumentManifest
from .core.identities import content_identity
from .operations.registry import Operation, OperationRegistry, valid_operation_id
from .control_contracts import (
    MAX_SAMPLES, _base, content_ref, detached, keys, number,
    observation, record, text, validate_artifact, validate_observation, validate_state,
)


@dataclass(frozen=True)
class Interval:
    low: float
    high: float
    unit: str = "1"

    def specification(self) -> dict:
        if number(self.low) > number(self.high):
            raise ValueError("Interval bounds are reversed")
        return {"kind": "interval", "low": self.low, "high": self.high, "unit": text(self.unit)}


@dataclass(frozen=True)
class Choice:
    values: tuple
    unit: str = "1"

    def specification(self) -> dict:
        values = list(self.values)
        if not values or len(values) > 256:
            raise ValueError("Choice requires 1..256 values")
        if any(value is None or type(value) not in (str, bool, int, float) for value in values):
            raise ValueError("Choices must be scalar JSON values")
        detached(values)
        if len({content_identity(value) for value in values}) != len(values):
            raise ValueError("Duplicate choice")
        return {"kind": "choice", "values": values, "unit": text(self.unit)}


@dataclass(frozen=True)
class Fixed:
    value: Any
    unit: str = "1"

    def specification(self) -> dict:
        if self.value is None or type(self.value) not in (str, bool, int, float):
            raise ValueError("Fixed parameters require a scalar JSON value")
        return {"kind": "fixed", "value": detached(self.value), "unit": text(self.unit)}


class ParameterSpace:
    """Own the domain, not the optimization algorithm. Finite grids/samples only."""

    def __init__(self, parameters: dict[str, Interval | Choice | Fixed]):
        if not parameters or len(parameters) > 64:
            raise ValueError("Parameter spaces require 1..64 dimensions")
        self._spec = {text(name): value.specification() for name, value in parameters.items()}

    def to_dict(self) -> dict:
        return record("parameter-space", parameters=self._spec)

    @classmethod
    def from_dict(cls, value: dict) -> ParameterSpace:
        _base(value, "parameter-space", {"parameters"})
        if type(value["parameters"]) is not dict:
            raise ValueError("Parameters must be an object")
        parameters = {}
        for name, spec in value["parameters"].items():
            if type(spec) is not dict:
                raise ValueError("Parameter specification must be an object")
            kind = spec.get("kind")
            if kind == "interval":
                keys(spec, {"kind", "low", "high", "unit"})
                parameters[name] = Interval(spec["low"], spec["high"], spec["unit"])
            elif kind == "choice":
                keys(spec, {"kind", "values", "unit"})
                if type(spec["values"]) is not list:
                    raise ValueError("Choice values must be a list")
                parameters[name] = Choice(tuple(spec["values"]), spec["unit"])
            elif kind == "fixed":
                keys(spec, {"kind", "value", "unit"})
                parameters[name] = Fixed(spec["value"], spec["unit"])
            else:
                raise ValueError("Unknown parameter domain")
        return cls(parameters)

    def validate(self, values: dict) -> dict:
        keys(values, set(self._spec))
        for name, spec in self._spec.items():
            value = values[name]
            if spec["kind"] == "interval":
                if not spec["low"] <= number(value) <= spec["high"]:
                    raise ValueError(f"Parameter outside interval: {name}")
            else:
                allowed = spec["values"] if spec["kind"] == "choice" else [spec["value"]]
                if content_identity(detached(value)) not in {content_identity(item) for item in allowed}:
                    raise ValueError(f"Parameter outside domain: {name}")
        return detached(values)

    def grid(self, intervals: dict[str, list], *, max_cases: int = 1024) -> list[dict]:
        _count(max_cases, 10000)
        required = {name for name, spec in self._spec.items() if spec["kind"] == "interval"}
        keys(intervals, required)
        axes = []
        for name, spec in self._spec.items():
            values = (intervals[name] if spec["kind"] == "interval" else
                      spec["values"] if spec["kind"] == "choice" else [spec["value"]])
            if type(values) is not list or not values or len(values) > max_cases:
                raise ValueError("Grid axes must be nonempty bounded lists")
            if len({content_identity(detached(v)) for v in values}) != len(values):
                raise ValueError("Duplicate grid coordinate")
            axes.append(values)
        if math.prod(len(axis) for axis in axes) > max_cases:
            raise ValueError("Grid exceeds case budget")
        return [self.validate(dict(zip(self._spec, row))) for row in product(*axes)]

    def sample(self, count: int, *, seed: int) -> list[dict]:
        _count(count, 10000)
        if type(seed) is not int:
            raise ValueError("Sampling requires an explicit integer seed")
        rng = random.Random(seed)
        result = []
        for _ in range(count):
            values = {}
            for name, spec in self._spec.items():
                values[name] = (rng.uniform(spec["low"], spec["high"]) if spec["kind"] == "interval"
                                else rng.choice(spec["values"]) if spec["kind"] == "choice"
                                else spec["value"])
            result.append(self.validate(values))
        return result


def _count(value: int, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"Require a count in 1..{maximum}")


class ObservationBus:
    """Bounded in-process append-only stream; overflow refuses rather than dropping."""

    def __init__(self, capacity: int = 4096):
        _count(capacity, MAX_SAMPLES)
        self.capacity = capacity
        self._items: list[dict] = []
        self._last: dict[tuple, float] = {}
        self._descriptors: dict[tuple, tuple] = {}
        self._lock = threading.RLock()

    def observe(self, **fields: Any) -> dict:
        return self.publish(observation(**fields))

    def publish(self, value: dict) -> dict:
        value = detached(value)
        validate_observation(value)
        identity = value["identity"]
        key = (identity["model_id"], identity["entity_id"], identity["execution_id"],
               value["provenance"]["provider"], value["quantity"], value["clock"]["id"])
        descriptor = (value["unit"], value["frame"],
                      value["provenance"]["semantics"],
                      len(value["value"]) if type(value["value"]) is list else None)
        with self._lock:
            if key in self._descriptors and self._descriptors[key] != descriptor:
                raise AdapterRefusal("observation_descriptor", "Unit, frame, semantics or shape changed within one stream")
            if len(self._items) >= self.capacity:
                raise AdapterRefusal("observation_capacity", "Observation bus is full; no sample was dropped")
            stamp = value["clock"]["time_s"]
            if key in self._last and stamp <= self._last[key]:
                raise AdapterRefusal("observation_order", "Duplicate or out-of-order sample for one stream")
            self._items.append(value)
            self._last[key] = stamp
            self._descriptors[key] = descriptor
        return deepcopy(value)

    def snapshot(self) -> dict:
        with self._lock:
            return record("observation-stream", observations=self._items)


def observations_from_run(run: dict, *, channel: str, entity_id: str, clock_id: str,
                          model_id: str, semantics: str, execution_id: str | None = None) -> list[dict]:
    """Explicit projection only; a caller cannot promote a source to acquired data.

    Observed projections require a retained observed-origin declaration. This
    checks source semantics, not acquisition authenticity or physical validity.
    """
    from .instruments import validate_run
    from .core.identities import validate_evidence_identity
    validate_run(run)
    validate_evidence_identity(run)
    if channel not in run["channels"]:
        raise ValueError("Unknown recording channel")
    if semantics == "observed":
        from .adapters.registry import default_registry
        provenance = run["metadata"]["provenance"]
        role = default_registry().resolve(run).manifest.role
        # The legacy analytic adapter predates structured origin metadata; its
        # fixed identity already establishes that it is not sensor acquisition.
        nonobserved = ("analytic", "synthetic", "simulated", "estimated", "reference", "computed")
        retained_channel = run["channels"][channel]
        # These scalar markers carry semantic meaning. Descriptive source
        # metadata may remain structured JSON; never infer nested declarations.
        for owner, names in ((provenance, ("semantics", "kind", "origin", "source_class")),
                             (retained_channel, ("semantics", "kind"))):
            for name in names:
                if name in owner and (not isinstance(owner[name], str) or not owner[name].strip()):
                    raise ValueError(f"Observed projection marker {name} must be a nonempty string")
        for name in ("observed", "synthetic"):
            if name in provenance and type(provenance[name]) is not bool:
                raise ValueError(f"Observed projection marker {name} must be a boolean")
        source = provenance.get("source")
        nonobserved_channel_kinds = nonobserved + (
            "estimated_state", "simulated_observation", "declared_initial_condition")
        if (run["instrument"] == "analytic-damped-oscillator.v1"
                or role not in {"instrument", "record_only", "measurement_adapter"}
                or provenance.get("semantics") != "observed"
                or provenance.get("observed") is False
                or provenance.get("synthetic") is True
                or provenance.get("kind") in nonobserved
                or (isinstance(source, str) and source in nonobserved)
                or provenance.get("origin") in nonobserved + ("computed_model_output",)
                or provenance.get("source_class") in nonobserved + ("synthetic_fixture", "simulated_observation")
                or retained_channel.get("kind") in nonobserved_channel_kinds
                or retained_channel.get("semantics", "observed") != "observed"):
            raise ValueError("Observed projection requires retained observed source semantics; computed or unknown origins cannot be promoted")
    if len(run["time_s"]) > MAX_SAMPLES:
        raise ValueError("Recording exceeds observation budget")
    return [observation(identity={"model_id": model_id, "entity_id": entity_id,
                                  "execution_id": execution_id},
                        clock={"id": clock_id, "time_s": stamp},
                        frame=run["metadata"]["coordinate_frame"], quantity=channel,
                        value=value, unit=run["channels"][channel]["unit"],
                        provenance={"provider": run["instrument"], "sources": [run["evidence_id"]],
                                    "semantics": semantics})
            for stamp, value in zip(run["time_s"], run["channels"][channel]["values"])]


@dataclass(frozen=True)
class Port:
    schema: str
    unit: str | None = None
    frame: str | None = None

    def to_dict(self) -> dict:
        text(self.schema)
        for value in (self.unit, self.frame):
            if value is not None:
                text(value)
        return {"schema": self.schema, "unit": self.unit, "frame": self.frame}

    @classmethod
    def from_dict(cls, value: dict) -> Port:
        keys(value, {"schema", "unit", "frame"})
        result = cls(**value)
        result.to_dict()
        return result

    def accepts(self, value: dict) -> None:
        if type(value) is not dict or value.get("schema") != self.schema:
            raise ValueError("Artifact schema differs from declared port")
        for key in ("unit", "frame"):
            if getattr(self, key) is not None and value.get(key) != getattr(self, key):
                raise ValueError(f"Artifact {key} differs from declared port; no implicit conversion")
        if self.schema == "ciw.state.v1":
            validate_state(value)
        elif self.schema == "ciw.observation.v1":
            validate_observation(value)
        elif self.schema == "ciw.artifact.v1":
            validate_artifact(value)
        # Other output schemas are validated by the existing trusted CIW payload validator.


class CapabilityRegistry:
    """Declarative catalog with explicit binding into CIW's existing OperationRegistry."""

    def __init__(self):
        self.operations = OperationRegistry()
        self._providers: dict[str, dict] = {}
        self._contracts: dict[str, dict] = {}

    def advertise(self, manifest: InstrumentManifest, *, runtime: dict,
                  capabilities: dict[str, list[str]], inputs: dict | None = None,
                  outputs: dict | None = None) -> None:
        manifest = InstrumentManifest.from_dict(manifest.to_dict())
        if manifest.instrument_id in self._providers:
            raise ValueError("Provider is already advertised")
        if type(runtime) is not dict or not runtime:
            raise ValueError("An advertised provider needs an explicit runtime identity")
        detached(runtime)
        keys(capabilities, set(manifest.supported_operations))
        inputs = detached({} if inputs is None else inputs)
        outputs = detached({} if outputs is None else outputs)
        if type(inputs) is not dict or type(outputs) is not dict:
            raise ValueError("Ports must be objects")
        if set(inputs) - set(capabilities) or set(outputs) - set(capabilities):
            raise ValueError("Ports refer to an undeclared operation")
        staged = {}
        for operation_id, names in capabilities.items():
            if not valid_operation_id(operation_id) or operation_id in self._contracts:
                raise ValueError("Invalid or ambiguous operation identity")
            if type(names) is not list or not names or len(names) != len(set(names)):
                raise ValueError("Require unique capability names")
            for name in names:
                text(name)
            ins = inputs.get(operation_id, {})
            outs = outputs.get(operation_id, {"result": {
                "type": Port("ciw.operation-result.v1").to_dict(), "path": []}})
            if type(ins) is not dict or type(outs) is not dict or not outs:
                raise ValueError("Require input/output port objects")
            for name, port in ins.items():
                text(name)
                Port.from_dict(port)
            for name, spec in outs.items():
                text(name)
                keys(spec, {"type", "path"})
                Port.from_dict(spec["type"])
                if type(spec["path"]) is not list or len(spec["path"]) > 16:
                    raise ValueError("Require bounded output selector path")
                for part in spec["path"]:
                    text(part)
            staged[operation_id] = detached(dict(provider=manifest.instrument_id, runtime=runtime,
                capabilities=names, inputs=ins, outputs=outs))
        self._contracts.update(staged)
        self._providers[manifest.instrument_id] = manifest.to_dict()

    def bind(self, operation: Operation) -> None:
        """Caller supplies trusted code. Neither a manifest nor a saved graph can do this."""
        contract = self.contract(operation.operation_id)
        expected = contract["runtime"]

        def identity():
            actual = detached(operation.runtime_identity())
            if actual != expected:
                raise AdapterRefusal("runtime_mismatch", "Provider identity differs from explicit binding")
            return actual

        def execute(run, parameters):
            identity()
            value = operation.execute(run, parameters)
            identity()
            return value

        self.operations.register(Operation(operation.operation_id, operation.role, execute, identity))

    def contract(self, operation_id: str) -> dict:
        if operation_id not in self._contracts:
            raise AdapterRefusal("capability_unavailable", "Operation has no declared provider contract")
        return deepcopy(self._contracts[operation_id])

    def catalog(self, capability: str | None = None) -> dict:
        contracts = {key: deepcopy(value) for key, value in self._contracts.items()
                     if capability is None or capability in value["capabilities"]}
        providers = {value["provider"] for value in contracts.values()}
        bound = {value["operation_id"] for value in self.operations.describe()}
        return {"schema": "ciw.provider-catalog.v1", "authorizes_execution": False,
                "providers": {key: deepcopy(value) for key, value in self._providers.items() if key in providers},
                "operations": {key: {**value, "bound": key in bound} for key, value in contracts.items()}}


def builtin_registry(*, bind: bool = False) -> CapabilityRegistry:
    # Fixed installed built-in only; no entry-point scan, import-by-name or external probing.
    from .adapters.oscillator import OscillatorAdapter
    registry = CapabilityRegistry()
    registry.advertise(OscillatorAdapter.manifest,
        runtime={"provider": "ciw.oscillator", "version": "1"},
        capabilities={"statistics.v1": ["analyze", "statistics"],
                      "spectrum.periodogram.v1": ["analyze", "spectrum"]})
    if bind:
        from .operations.registry import default_registry
        existing = default_registry()
        # CIW's default registry also has separate specialist operations. Bind
        # only this NET registry's explicitly advertised provider contracts.
        for operation_id in registry.catalog()["operations"]:
            registry.bind(existing.get(operation_id))
    return registry


def experiment(experiment_id: str, *, model_id: str, nodes: list[dict],
               parameters: dict | None = None, parent_checkpoint: str | None = None) -> dict:
    value = record("experiment", experiment_id=text(experiment_id), model_id=text(model_id),
                   nodes=nodes, parameters={} if parameters is None else parameters, parent_checkpoint=parent_checkpoint)
    _experiment(value)
    return value


def _experiment(value: dict) -> list[str]:
    _base(value, "experiment", {"experiment_id", "model_id", "nodes", "parameters", "parent_checkpoint"})
    text(value["experiment_id"])
    text(value["model_id"])
    if value["parent_checkpoint"] is not None:
        content_ref(value["parent_checkpoint"])
    if type(value["parameters"]) is not dict:
        raise ValueError("Experiment parameters must be an object")
    nodes = value["nodes"]
    if type(nodes) is not list or not 1 <= len(nodes) <= 64:
        raise ValueError("An experiment requires 1..64 nodes")
    seen, dependencies = set(), {}
    for node in nodes:
        keys(node, {"node_id", "operation_id", "parameters", "inputs", "depends_on"})
        name = text(node["node_id"])
        if name in seen or not valid_operation_id(node["operation_id"]):
            raise ValueError("Duplicate node or unversioned operation")
        seen.add(name)
        if type(node["parameters"]) is not dict or type(node["inputs"]) is not dict:
            raise ValueError("Node inputs/parameters must be objects")
        if type(node["depends_on"]) is not list:
            raise ValueError("Dependencies must be an array")
        deps = set()
        for dep in node["depends_on"]:
            text(dep)
            if dep in deps:
                raise ValueError("Duplicate dependency")
            deps.add(dep)
        for name_in, edge in node["inputs"].items():
            text(name_in)
            keys(edge, {"node_id", "port"})
            deps.add(text(edge["node_id"]))
            text(edge["port"])
        if set(node["parameters"]) & set(node["inputs"]):
            raise ValueError("An input cannot replace a literal parameter")
        dependencies[name] = deps
    if any(deps - seen for deps in dependencies.values()):
        raise ValueError("Missing graph dependency")
    order = []
    while len(order) < len(nodes):
        ready = [node["node_id"] for node in nodes if node["node_id"] not in order
                 and dependencies[node["node_id"]] <= set(order)]
        if not ready:
            raise ValueError("Computational cycle")
        order.extend(ready)
    return order


def _plan_contracts(value: dict, contracts: dict) -> list[str]:
    """Validate the captured data-only plan, shared by dispatch and inspection."""
    order = _experiment(value)
    nodes = {node["node_id"]: node for node in value["nodes"]}
    keys(contracts, {node["operation_id"] for node in nodes.values()})
    for contract in contracts.values():
        keys(contract, {"provider", "runtime", "capabilities", "inputs", "outputs"})
        text(contract["provider"])
        if type(contract["runtime"]) is not dict or not contract["runtime"]:
            raise ValueError("Captured operation requires an explicit runtime")
        names = contract["capabilities"]
        if type(names) is not list or not names:
            raise ValueError("Captured operation requires capabilities")
        for name in names:
            text(name)
        if len(names) != len(set(names)):
            raise ValueError("Duplicate captured capability")
        if type(contract["inputs"]) is not dict or type(contract["outputs"]) is not dict or not contract["outputs"]:
            raise ValueError("Captured input/output ports must be objects")
        for name, port in contract["inputs"].items():
            text(name)
            Port.from_dict(port)
        for name, spec in contract["outputs"].items():
            text(name)
            keys(spec, {"type", "path"})
            Port.from_dict(spec["type"])
            if type(spec["path"]) is not list or len(spec["path"]) > 16:
                raise ValueError("Invalid captured output path")
            for part in spec["path"]:
                text(part)
    for node in nodes.values():
        contract = contracts[node["operation_id"]]
        keys(node["inputs"], set(contract["inputs"]))
        if set(value["parameters"]) & set(node["inputs"]):
            raise ValueError("A graph input cannot replace an experiment parameter")
        for name, edge in node["inputs"].items():
            upstream = contracts[nodes[edge["node_id"]]["operation_id"]]
            output = upstream["outputs"].get(edge["port"])
            if output is None or output["type"] != contract["inputs"][name]:
                raise ValueError("Graph port schema/unit/frame mismatch")
    return order


def plan_graph(value: dict, registry: CapabilityRegistry) -> list[str]:
    _experiment(value)
    return _plan_contracts(value, {node["operation_id"]: registry.contract(node["operation_id"])
                                   for node in value["nodes"]})


def _outputs(payload: dict, contract: dict) -> dict:
    if payload["execution"]["runtime"] != contract["runtime"]:
        raise ValueError("Executed runtime differs from planned runtime")
    extracted = {}
    for port_name, spec in contract["outputs"].items():
        item = payload["result"]
        for key in spec["path"]:
            item = item[key]
        Port.from_dict(spec["type"]).accepts(item)
        extracted[port_name] = detached(item)
    return extracted


def run_graph(session, value: dict, registry: CapabilityRegistry) -> dict:
    """Bounded sequential DAG; every calculation goes through Session.handle.

    Refused/error nodes block all descendants. Independent nodes may continue.
    Calling again is explicit re-execution with fresh CIW occurrence identities.
    """
    value = detached(value)
    order = plan_graph(value, registry)
    if session.operations is not registry.operations:
        raise ValueError("Session must use the explicitly bound existing OperationRegistry")
    contracts = {node["operation_id"]: registry.contract(node["operation_id"]) for node in value["nodes"]}
    # All bindings preflight before the first provider is invoked.
    for operation_id in contracts:
        registry.operations.get(operation_id)
    nodes = {node["node_id"]: node for node in value["nodes"]}
    outcomes, outputs = {}, {}
    for name in order:
        node = nodes[name]
        deps = set(node["depends_on"]) | {edge["node_id"] for edge in node["inputs"].values()}
        blocked = sorted(dep for dep in deps if outcomes[dep]["status"] != "completed")
        if blocked:
            outcomes[name] = {"status": "blocked", "dependencies": blocked}
            continue
        parameters = {**value["parameters"], **node["parameters"]}
        for key, edge in node["inputs"].items():
            parameters[key] = deepcopy(outputs[edge["node_id"]][edge["port"]])
        response = session.handle({"protocol_version": 1, "request_id": uuid.uuid4().hex,
            "type": "operation.execute", "payload": {"operation_id": node["operation_id"], "parameters": parameters}})
        if response["type"] == "error":
            outcomes[name] = {"status": "error", "refusal": response["payload"]}
            continue
        payload = response["payload"]
        outcomes[name] = detached(payload)
        if payload["status"] != "completed":
            continue
        try:
            outputs[name] = _outputs(payload, contracts[node["operation_id"]])
        except (KeyError, TypeError, ValueError) as exc:
            # Keep the original CIW execution/result; never rewrite it to claim refusal.
            outcomes[name] = {"status": "output_rejected", "reason": str(exc), "retained": payload}
    status = "completed" if all(item["status"] == "completed" for item in outcomes.values()) else "incomplete"
    return record("graph-run", experiment=value, contracts=contracts, session_id=session.session_id,
                  source_evidence_id=session.run["evidence_id"], order=order, nodes=outcomes, status=status)
