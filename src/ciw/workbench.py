"""Shared retained sources and native instrument results for one CIW session.

This catalog does not estimate state. GSIE's retained posterior and conditional
prediction remain separate, explicitly selected contexts. Repository bindings
are trusted process configuration and never enter the saved workspace.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import RLock

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import _json
DECLARED_KINDS = frozenset({"schematic-assessment", "numerical-heat", "proved-heat", "schematic-companions", "bim-quantity", "acquired-dataset", "residual-monitor", "measurement-chain", "geometric-circle", "identified-stability", "flat-torus-reference", "curved-path-transfer", "covariance-geometry", "mesh-path", "translation-flow", "variational-free-energy", "energy-accuracy", "instrument-exchange", "thermal-observer", "machine-manifest", "julia-oscillator", "native-interop", "project-graph"})
REPRODUCED_KINDS = DECLARED_KINDS - {"proved-heat"}
UPSTREAM_KINDS = {"identified-design": "calibrated-observable", "schematic-companions": "schematic-assessment",
                  "acquired-calibrated-window": "acquired-dataset", "identified-stability": "identified-design"}
INSTRUMENT_ROLES = frozenset({"ppda", "tbrt", "mcur", "stfe", "gsie", "cbsr", "fdir", "oit", "sra", "scr", "cse", "rci", "fsrt", "jspt", "gte", "plsr", "ftr", "csg", "cggt", "isgt", "tsde", "energy", "exchange", "thermal", "machine", "julia", "project"})

SCHEMA = "ciw.retained-workbench.v1"
SOURCE_SCHEMA = "ciw.workbench-source.v1"
OPERATIONS = {
    "calibrated-observable": "ciw.calibrated-observable.v1",
    "identified-design": "ciw.identified-design.v1",
    "telemetry": "ciw.telemetry.v1",
    "calibrated-window": "ciw.calibrated-window.v1",
    "schematic-assessment": "ciw.schematic-assessment.v1",
    "numerical-heat": "ciw.numerical-heat.v1",
    "proved-heat": "ciw.proved-heat.v1",
    "schematic-companions": "ciw.schematic-companions.v1",
    "bim-quantity": "ciw.bim-quantity.v1",
    "acquired-dataset": "ciw.acquired-dataset.v1",
    "acquired-calibrated-window": "ciw.acquired-calibrated-window.v1",
    "residual-monitor": "ciw.residual-monitor.v1",
    "measurement-chain": "ciw.measurement-chain.v1",
    "geometric-circle": "ciw.geometric-circle.v1",
    "identified-stability": "ciw.identified-stability.v1",
    "flat-torus-reference": "ciw.flat-torus-reference.v1",
    "curved-path-transfer": "ciw.curved-path-transfer.v1",
    "covariance-geometry": "ciw.covariance-geometry.v1",
    "mesh-path": "ciw.mesh-path.v1",
    "translation-flow": "ciw.translation-flow.v1",
    "variational-free-energy": "ciw.variational-free-energy.v1",
    "energy-accuracy": "ciw.energy-accuracy.v1",
    "instrument-exchange": "ciw.instrument-exchange.v1",
    "thermal-observer": "ciw.thermal-observer.v1",
    "machine-manifest": "ciw.encoder-position.v1",
    "julia-oscillator": "ciw.julia-oscillator.v1",
    "native-interop": "ciw.native-interop.v1",
    "project-graph": "ciw.project-graph.v1",
}
WORKFLOW_OPERATION_IDS = frozenset(OPERATIONS.values())
from .candidate_evidence import OPERATIONS as CANDIDATE_OPERATIONS
WORKBENCH_OPERATION_IDS = WORKFLOW_OPERATION_IDS | CANDIDATE_OPERATIONS.keys()
MAX_SOURCES = 64
MAX_BUNDLES = 128
MAX_BYTES = 64 * 1024 * 1024
_OVERHEAD = 4096


def _workflow(kind):
    if kind == "system-specification":
        from . import system_source
        return system_source
    if kind == "reference-evidence":
        from . import reference_evidence
        return reference_evidence
    if kind == "project-graph":
        from .project_workflow import ProjectGraphWorkflow
        return ProjectGraphWorkflow()
    if kind == "native-interop":
        from .native_interop import NativeInteropWorkflow
        return NativeInteropWorkflow()
    if kind == "machine-manifest":
        from .machine_workflow import MachineManifestWorkflow
        return MachineManifestWorkflow()
    if kind == "thermal-observer":
        from .thermal_workflow import ThermalWorkflow
        return ThermalWorkflow()
    if kind == "julia-oscillator":
        from .julia_oscillator import JuliaOscillatorWorkflow
        return JuliaOscillatorWorkflow()
    if kind == "instrument-exchange":
        from . import exchange_adapter
        return exchange_adapter
    if kind == "energy-accuracy":
        from .energy_workflow import EnergyAccuracyWorkflow
        return EnergyAccuracyWorkflow()
    if kind == "variational-free-energy":
        from .free_energy_workflow import FreeEnergyWorkflow
        return FreeEnergyWorkflow()
    # Lazy imports avoid the existing workflows' Session persistence dependency.
    if kind in {"covariance-geometry", "mesh-path", "translation-flow"}:
        from .geometry_research import GeometryResearchWorkflow
        return GeometryResearchWorkflow(kind)
    if kind == "proved-heat":
        from .proved_heat import ProvedHeatWorkflow
        return ProvedHeatWorkflow()
    if kind in {"flat-torus-reference", "curved-path-transfer"}:
        from .geodesic_reference import GeodesicReferenceWorkflow
        return GeodesicReferenceWorkflow(kind)
    if kind in {"schematic-assessment", "numerical-heat"}:
        from .declared_workload import DeclaredWorkflow
        return DeclaredWorkflow(kind)
    if kind == "schematic-companions":
        from .schematic_companions import SchematicCompanionWorkflow
        return SchematicCompanionWorkflow()
    if kind == "bim-quantity":
        from .bim_quantity import workflow
        return workflow
    if kind == "acquired-dataset":
        from .acquired_dataset import workflow
        return workflow
    if kind == "acquired-calibrated-window":
        from . import acquired_window
        return acquired_window
    if kind == "residual-monitor":
        from .residual_monitor import workflow
        return workflow
    if kind == "measurement-chain":
        from .measurement_chain import workflow
        return workflow
    if kind == "geometric-circle":
        from .geometric_circle import workflow
        return workflow
    if kind == "identified-stability":
        from .identified_stability import workflow
        return workflow
    if kind == "geographic-context":
        from . import spatial_view
        return spatial_view
    if kind == "calibrated-observable":
        from . import calibrated_observable
        return calibrated_observable
    if kind == "identified-design":
        from . import identified_design
        return identified_design
    if kind == "telemetry":
        from . import telemetry
        return telemetry
    if kind == "calibrated-window":
        from . import calibrated_window
        return calibrated_window
    raise ValueError("Unknown workbench source kind")


def _canonical(value):
    from .telemetry import canonical
    return canonical(value)


def _digest(value):
    return "sha256:" + sha256(_canonical(value)).hexdigest()


def _keys(value, required, optional=()):
    if (not isinstance(value, dict) or
            not set(required) <= value.keys() <= set(required) | set(optional)):
        raise ValueError("Unexpected or missing workbench fields")


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{name} must be a nonempty bounded string")


def _descriptor(source):
    return {k: v for k, v in source.items() if k != "bytes_b64"}


def _source(payload):
    _keys(payload, {"kind", "label", "bytes_b64"})
    kind, label, encoded = payload["kind"], payload["label"], payload["bytes_b64"]
    _text(kind, "Source kind")
    _text(label, "Source label")
    workflow = _workflow(kind)
    if not isinstance(encoded, str) or len(encoded) > 4 * ((workflow.MAX_BYTES + 2) // 3):
        raise ValueError("Source bytes exceed the workflow byte budget")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Source bytes must use canonical base64") from exc
    if len(raw) > workflow.MAX_BYTES or base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("Source bytes must use bounded canonical base64")
    if kind != "identified-design":
        declaration = workflow._source(raw)
    else:
        # The full design validator needs an explicitly selected retained prior.
        declaration = _json(raw)
        if not isinstance(declaration, dict) or declaration.get("schema") != workflow.SOURCE_SCHEMA:
            raise ValueError("Unsupported identified-design source")
        _canonical(declaration)
    descriptor = {"schema": SOURCE_SCHEMA, "kind": kind, "label": label,
        "source_schema": declaration["schema"],
        "evidence_id": "sha256:" + sha256(raw).hexdigest(), "byte_count": len(raw)}
    descriptor["source_id"] = "source:" + _digest(descriptor)
    return {**descriptor, "bytes_b64": encoded}


def _summary(record):
    native = record["native"]
    steps = _catalog_steps(native)
    verification = native.get("verification", {})
    summary = {"bundle_id": record["bundle_id"], "kind": record["kind"],
        "source_id": record["source_id"], "upstream_bundle_id": record["upstream_bundle_id"],
        "session_id": native["session_id"], "operation_id": OPERATIONS[record["kind"]],
        "result_ids": [s["result_id"] for s in steps],
        "execution_ids": [s["execution_id"] for s in steps],
        "verification_id": verification.get("verification_id"),
        "retained_verification_outcome": verification.get("outcome"),
        "validation": "content_consistent", "numerical_replay": "not_performed_by_inspection",
        "state_admission": "not_performed"}
    if record["kind"] == "proved-heat":
        summary["cryptographic_verification"] = "not_performed_by_inspection"
        summary["verification_trust_scope"] = verification["trust_scope"]
    if record["kind"] == "residual-monitor":
        summary["upstream_bundle_ids"] = _workflow(record["kind"]).requested_upstream_ids(
            base64.b64decode(native["source"]["evidence"][0]["bytes_b64"], validate=True))
    return summary


def _claims(record):
    """Reject identity aliasing while allowing exact embedded upstream reuse."""
    claims = {}

    def claim(identity, role, value):
        _text(identity, "Retained identity")
        content = (role, _digest(value))
        if identity in claims and claims[identity] != content:
            raise ValueError("Retained identity collision")
        claims[identity] = content

    def verification(value):
        claim(value["verification_id"], "verification", value)
        if value.get("schema") == "ciw.proved-heat-verification.v1":
            claim(value["verification_operation_id"], "verification_execution", value)
        if value.get("schema") == "ciw.declared-workload-verification.v1":
            step = value["reproduction"]
            claim(step["execution_id"], "execution", {"step": step})
            claim(step["result_id"], "result", {"result": step["result"]})
            claim(step["numerical_result_id"], "numerical_result", step["numerical_result"])

    def bundle(value):
        owner = value["bundle_digest"]
        body = {k: v for k, v in value.items() if k not in {"verification", "replay_receipts"}}
        claim(owner, "bundle", body)
        claim(value["session_id"], "session", owner)
        for evidence in value["source"]["evidence"]:
            claim(evidence["artifact_ref"], "evidence", evidence["bytes_b64"])
        for step in value["steps"]:
            step_owner = value.get("child_window", value)["bundle_digest"]
            claim(step["operation_id"], "operation", step["operation_id"])
            declared = value["schema"] in {"ciw." + kind + "-session.v1" for kind in DECLARED_KINDS}
            claim(step["execution_id"], "execution", {"step": step} if declared else {"bundle_id": step_owner, "step": step})
            if value["schema"] == "ciw.telemetry-session.v1" and step["runtime_ref"] == "ppda":
                # A replay reprojects the same acquired evidence. Its batch ID
                # stays stable while every execution occurrence remains fresh.
                claim(step["result_id"], "observation_batch", step["result"])
            else:
                claim(step["result_id"], "result", {"result": step["result"]} if declared else {"bundle_id": step_owner, "result": step["result"]})
            claim(step["numerical_result_id"], "numerical_result", step["numerical_result"])
        if "verification" in value:
            verification(value["verification"])
        for receipt in value.get("replay_receipts", []):
            claim(receipt["replay_id"], "replay", receipt)
            verification(receipt["verification"])
        if "child_window" in value:
            # Exposed native steps retain the child's ownership and SET scope.
            # The mapping wrapper has its own source and verification identity.
            child = value["child_window"]
            claim(child["bundle_digest"], "bundle", {k: v for k, v in child.items()
                                                     if k not in {"verification", "replay_receipts"}})
            claim(child["session_id"], "session", child["bundle_digest"])
            for evidence in child["source"]["evidence"]:
                claim(evidence["artifact_ref"], "evidence", evidence["bytes_b64"])
            verification(child["verification"])
            for receipt in child.get("replay_receipts", []):
                claim(receipt["replay_id"], "replay", receipt)
                verification(receipt["verification"])
        if "upstream" in value:
            bundle(value["upstream"])
            bundle(value["upstream_replay"]["session"])
            receipt = value["upstream_replay"]["replay_receipt"]
            claim(receipt["replay_id"], "replay", receipt)
            verification(receipt["verification"])

    bundle(record["native"])
    if record["kind"] == "measurement-chain":
        from .measurement_chain import identity_claims
        for step in _catalog_steps(record["native"]):
            claim(step["operation_id"], "operation", step["operation_id"])
        for identity, (role, body) in identity_claims(record["native"]).items():
            claim(identity, role, body)
    if record["kind"] == "variational-free-energy":
        from .free_energy_workflow import identity_claims
        for identity, (role, body) in identity_claims(record["native"]).items():
            claim(identity, role, body)
    if record["kind"] == "energy-accuracy":
        native = record["native"]
        data = native["steps"][0]["result"]["data"]
        occurrence = native["source"]["experiment_id"]
        # Reanalysis may reuse the same log; one capture occurrence cannot be
        # rebound to changed evidence and masquerade as another measurement.
        claim(occurrence, "retained_energy_log_occurrence", {"log_digest": data["log_digest"], "origin": data["origin"]})
        claim(data["log_digest"], "retained_energy_log", {"run_id": occurrence, "origin": data["origin"]})
    return claims


def _catalog_steps(native):
    """Expose retained native occurrences without manufacturing new results."""
    steps = list(native["steps"])
    if native["schema"] == "ciw.measurement-chain-session.v1":
        from .measurement_chain import catalog_steps
        steps.extend(catalog_steps(native))
    if native["schema"] == "ciw.variational-free-energy-session.v1":
        from .free_energy_workflow import catalog_steps
        steps.extend(catalog_steps(native))
    return steps


def _source_claims(source):
    return {source["source_id"]: ("source", _digest(_descriptor(source))),
        source["evidence_id"]: ("evidence", _digest(source["bytes_b64"]))}


def _validate_record(record, sources):
    _keys(record, {"kind", "source_id", "upstream_bundle_id", "bundle_id", "native"})
    _text(record["kind"], "Bundle kind")
    if record["kind"] not in OPERATIONS:
        raise ValueError("Source-only contexts cannot declare an execution bundle")
    _text(record["source_id"], "Source identity")
    _text(record["bundle_id"], "Bundle identity")
    if record["upstream_bundle_id"] is not None:
        _text(record["upstream_bundle_id"], "Upstream bundle identity")
    workflow = _workflow(record["kind"])
    source = sources.get(record["source_id"])
    if source is None or source["kind"] != record["kind"]:
        raise ValueError("Bundle must name its retained source of the same kind")
    native = record["native"]
    if not isinstance(native, dict):
        raise ValueError("Native bundle must be an object")
    raw = workflow._validate_retained(native) if record["kind"] == "telemetry" else workflow._validate(native)
    if (native["bundle_digest"] != record["bundle_id"] or
            raw != base64.b64decode(source["bytes_b64"], validate=True)):
        raise ValueError("Bundle differs from its exact retained source bytes")
    if "verification" not in native:
        raise ValueError("A retained workflow must preserve its native verification artifact")
    if record["kind"] not in UPSTREAM_KINDS:
        if record["upstream_bundle_id"] is not None:
            raise ValueError("This workflow has no implicit upstream bundle")
    elif record["upstream_bundle_id"] is None:
        raise ValueError("This workflow needs an explicitly selected upstream bundle")
    _validate_receipts(native, record["kind"])
    return _claims(record)


def _validate_receipts(native, kind):
    receipts = native.get("replay_receipts", [])
    if not isinstance(receipts, list) or len(receipts) > 1:
        raise ValueError("A native replay retains one receipt")
    for receipt in receipts:
        _keys(receipt, {"schema", "source_bundle_digest", "replayed_bundle_digest", "numerical_match",
                        "verification", "admission", "replay_id"})
        if (receipt["schema"] != "ciw." + kind + "-replay.v1" or
                receipt["replayed_bundle_digest"] != native["bundle_digest"] or
                receipt["source_bundle_digest"] == native["bundle_digest"] or
                receipt["numerical_match"] is not True or receipt["admission"] != "not_performed" or
                receipt["replay_id"] != _digest({k: v for k, v in receipt.items() if k != "replay_id"})):
            raise ValueError("Replay receipt does not bind the retained fresh bundle")
        from .exchange import _identity
        _identity(receipt["verification"], "verification_id")
        if receipt["verification"]["subject_ref"] != receipt["source_bundle_digest"]:
            raise ValueError("Replay verification subject differs from replay source")


def _validate_links(record, bundles):
    native = record["native"]
    if record["kind"] == "acquired-calibrated-window":
        occurrences = {step[key] for step in native["steps"] for key in ("execution_id", "result_id")}
        for other in bundles.values():
            if other["bundle_id"] != record["bundle_id"] and other["kind"] == record["kind"]:
                used = {step[key] for step in other["native"]["steps"] for key in ("execution_id", "result_id")}
                if not occurrences.isdisjoint(used):
                    raise ValueError("Acquired windows must retain fresh native execution and result occurrences")
    if record["kind"] == "residual-monitor":
        _workflow(record["kind"]).validate_upstreams(native, {key: value["native"] for key, value in bundles.items()})
    if record["kind"] == "measurement-chain":
        from .measurement_chain import native_occurrences
        occurrences = native_occurrences(native)
        for other in bundles.values():
            if other["bundle_id"] != record["bundle_id"] and other["kind"] == record["kind"]:
                if not occurrences.isdisjoint(native_occurrences(other["native"])):
                    raise ValueError("Measurement chains must retain fresh native execution occurrences")
    if record["kind"] == "schematic-companions":
        from .schematic_companions import native_occurrences
        occurrences = native_occurrences(native)
        for other in bundles.values():
            if (other["bundle_id"] != record["bundle_id"] and other["kind"] == "schematic-companions"
                    and not occurrences.isdisjoint(native_occurrences(other["native"]))):
                raise ValueError("Companion bundles must retain fresh native execution occurrences")
    if record["kind"] == "variational-free-energy":
        from .free_energy_workflow import native_occurrences
        occurrences = native_occurrences(native)
        for other in bundles.values():
            if other["bundle_id"] != record["bundle_id"] and other["kind"] == "variational-free-energy" and not occurrences.isdisjoint(native_occurrences(other["native"])):
                raise ValueError("Free-energy experiments require fresh native stage occurrences")
    if record["kind"] == "native-interop":
        from .native_interop import validate_dependency
        raw = _workflow("native-interop")._validate(native)
        validate_dependency(_workflow("native-interop")._source(raw), bundles)
    if record["kind"] in DECLARED_KINDS and record["kind"] != "instrument-exchange":
        occurrences = {native["steps"][0]["execution_id"]}
        if record["kind"] in REPRODUCED_KINDS:
            occurrences.add(native["verification"]["reproduction"]["execution_id"])
        for other in bundles.values():
            if other["bundle_id"] == record["bundle_id"]:
                continue
            used = {s["execution_id"] for s in other["native"]["steps"]}
            if other["kind"] in REPRODUCED_KINDS:
                used.add(other["native"]["verification"]["reproduction"]["execution_id"])
            if not occurrences.isdisjoint(used):
                raise ValueError("Declared workload bundles must have distinct execution and reproduction occurrences")
    upstream_id = record["upstream_bundle_id"]
    if upstream_id is not None:
        upstream = bundles.get(upstream_id)
        if upstream is None or upstream["kind"] != UPSTREAM_KINDS.get(record["kind"]):
            raise ValueError("Upstream must name a retained bundle of the declared kind")
        if record["kind"] == "schematic-companions":
            from .schematic_companions import validate_upstream
            validate_upstream(native, upstream["native"])
        elif record["kind"] == "acquired-calibrated-window":
            _workflow(record["kind"]).validate_upstream(native, upstream["native"])
        elif record["kind"] == "identified-stability":
            _workflow(record["kind"]).validate_upstream(native, upstream["native"])
        elif _canonical(native["upstream"]) != _canonical(upstream["native"]):
            raise ValueError("Design upstream must exactly match a retained calibrated bundle")
    for receipt in native.get("replay_receipts", []):
        original = bundles.get(receipt["source_bundle_digest"])
        if (original is None or original["kind"] != record["kind"] or
                original["source_id"] != record["source_id"] or
                original["upstream_bundle_id"] != upstream_id):
            raise ValueError("Replay source must already belong to this workbench")
        old_steps, new_steps = original["native"]["steps"], native["steps"]
        if record["kind"] == "acquired-calibrated-window":
            _workflow(record["kind"]).validate_replay(original["native"], native, receipt)
        if record["kind"] == "proved-heat":
            _workflow("proved-heat").validate_replay(original["native"], native, receipt)
        if record["kind"] in REPRODUCED_KINDS and record["kind"] != "instrument-exchange":
            workflow = _workflow(record["kind"])
            raw = workflow._validate(original["native"])
            workflow._check_verification(original["native"], receipt["verification"], workflow._source(raw),
                                         original["native"]["source"]["evidence"][0]["artifact_ref"])
            if receipt["verification"]["reproduction"] != new_steps[0]:
                raise ValueError("Declared replay verification must bind the exact fresh step")
            if workflow._runtime_projection(native["runtimes"][workflow.role]) != workflow._runtime_projection(original["native"]["runtimes"][workflow.role]):
                raise ValueError("Declared replay runtime identity mismatch")
            old_occurrences = {old_steps[0]["execution_id"], original["native"]["verification"]["reproduction"]["execution_id"]}
            new_occurrences = {new_steps[0]["execution_id"], native["verification"]["reproduction"]["execution_id"]}
            if not old_occurrences.isdisjoint(new_occurrences):
                raise ValueError("Replay cannot reuse a prior reproduction occurrence")
        if (_canonical(original["native"]["configuration"]) != _canonical(native["configuration"]) or
                [step["operation_id"] for step in old_steps] != [step["operation_id"] for step in new_steps]):
            raise ValueError("Replay must preserve the original operation graph and configuration")
        if any((old["numerical_result_id"] != new["numerical_result_id"] and record["kind"] != "native-interop") or
               old["execution_id"] == new["execution_id"] or
               (old["result_id"] == new["result_id"] and not
                (record["kind"] == "telemetry" and old["runtime_ref"] == "ppda" and old["result"] == new["result"]))
               for old, new in zip(old_steps, new_steps)):
            raise ValueError("Replay must preserve numerical identity and create fresh occurrences")


def _context(record, sources):
    native = record["native"]
    if record["kind"] in {"telemetry", "calibrated-window", "acquired-calibrated-window"}:
        return _telemetry_context(record, sources)
    calibrated = record["kind"] == "calibrated-observable"
    step = native["steps"][4 if calibrated else 2]
    state = step["result"]["data"]
    configuration = native["configuration"]
    source = sources[record["source_id"]]
    common = {"context_id": "context:" + _digest({"bundle_id": record["bundle_id"],
                                                   "result_id": step["result_id"]}),
        "bundle_id": record["bundle_id"], "source_id": record["source_id"],
        "evidence_id": source["evidence_id"], "upstream_bundle_id": record["upstream_bundle_id"],
        "state_kind": "posterior" if calibrated else "conditional_prediction",
        "owner": "gsie", "state_id": state["state_id"], "result_id": step["result_id"],
        "execution_id": step["execution_id"], "event_time": state["time"],
        "frame_id": state["frame_id"], "units": state["units"],
        "mean": state["mean"], "covariance": state["covariance"],
        "validation": "content_consistent", "numerical_replay": "not_performed_by_inspection",
        "state_admission": "not_performed",
        "verification_id": native["verification"]["verification_id"],
        "retained_verification_outcome": native["verification"].get("outcome")}
    if calibrated:
        experiment = _json(base64.b64decode(source["bytes_b64"], validate=True))
        observable = native["steps"][3]["result"]["data"]
        reconciled = native["steps"][5]["result"]["data"]
        faults = native["steps"][6]["result"]["data"]
        common.update(state_names=configuration["observability"]["state_names"],
            model_id=configuration["gsie"]["observation_model"]["model_id"],
            dynamics_model_id=configuration["gsie"]["dynamics"]["model_id"],
            clock_frames=[c["clock_model"]["reference_frame"] for c in experiment["channels"]],
            channel_ids=[c["channel_id"] for c in experiment["channels"]],
            claim_scope=experiment["claim_scope"],
            observability={k: observable[k] for k in ("status", "rank", "condition_number", "condition_limit")},
            reconciliation={"result_id": native["steps"][5]["result_id"],
                            "status": reconciled["status"], "reason": reconciled["reason"]},
            fault_assessment={"result_id": native["steps"][6]["result_id"],
                "detection": faults["detection"]["status"], "isolability": faults["isolability"]["status"],
                "isolated_fault": faults["isolability"].get("isolated_fault"),
                "cross_covariance_policy": faults["isolability"]["cross_covariance_policy"]})
    else:
        previous = native["upstream"]
        previous_faults = previous["steps"][6]["result"]["data"]
        common.update(state_names=configuration["identification"]["state_names"],
            model_id=state["model_result_id"],
            declared_model_id=configuration["identification"]["model_id"],
            clock_frame=configuration["identification"]["clock_frame"],
            predecessor_state_id=state["predecessor_state_id"],
            uncertainty_scope=state["uncertainty_scope"],
            parameter_covariance_status=state["parameter_covariance_status"],
            upstream_assessment={"bundle_id": record["upstream_bundle_id"],
                "reconciliation_status": previous["steps"][5]["result"]["data"]["status"],
                "detection_status": previous_faults["detection"]["status"],
                "isolability_status": previous_faults["isolability"]["status"]},
            decision=native["decision"])
    return common


def _telemetry_context(record, sources):
    native = record["native"]
    window = record["kind"] in {"calibrated-window", "acquired-calibrated-window"}
    step = next(s for s in native["steps"] if s["runtime_ref"] == "gsie")
    state = step["result"]["result_artifact"]
    source = sources[record["source_id"]]
    declaration = _json(base64.b64decode(source["bytes_b64"], validate=True))
    if record["kind"] == "acquired-calibrated-window":
        declaration = _workflow(record["kind"]).mapped_source(native)
    configuration = native.get("child_window", native)["configuration"]
    cbsr = next((s for s in native["steps"] if s["runtime_ref"] == "cbsr"), None)
    context = {"context_id": "context:" + _digest({"bundle_id": record["bundle_id"], "result_id": step["result_id"]}),
        "bundle_id": record["bundle_id"], "source_id": record["source_id"], "evidence_id": source["evidence_id"],
        "upstream_bundle_id": record["upstream_bundle_id"], "owner": "gsie", "state_kind": "window_feature_posterior",
        "state_id": state["state_id"], "result_id": step["result_id"], "execution_id": step["execution_id"],
        "event_time": state["observation_binding"]["elapsed_seconds"], "epoch": declaration["epoch"],
        "mean": [item["value"] for item in state["components"]], "covariance": state["covariance"]["matrix"],
        "state_names": [item["name"] for item in state["components"]], "units": [item["unit"] for item in state["components"]],
        "frame_id": state["covariance"]["frame"]["id"], "clock_frame": declaration["receipt_clock"]["clock_id"] if window else declaration["clock_basis"],
        "channel_ids": [declaration["channel_id"]], "observation_batch_id": None if window else native["steps"][0]["result_id"],
        "feature_result_id": next(s["result_id"] for s in native["steps"] if s["runtime_ref"] == "stfe"), "window": deepcopy(configuration["window"]),
        "feature_observation_semantics": configuration["gsie"]["feature_observation_semantics"],
        "model_id": configuration["gsie"]["observation_model"]["model_id"],
        "cross_covariance_policy": declaration["joint_covariance"]["cross_covariance_policy"] if window else declaration["crosscov_policy"],
        "prior_measurement_crosscov_policy": configuration["gsie"]["prior_measurement_crosscov_policy"],
        "observability": {"status": "unresolved", "reason": "not_evaluated_by_telemetry_profile"}, "calibration_validity": "not_assessed",
        "reconciliation": {"result_id": cbsr["result_id"], "status": cbsr["result"]["status"]} if cbsr else {"status": "not_run"},
        "fault_assessment": {"status": "not_run"}, "state_admission": "not_performed",
        "validation": "content_consistent", "numerical_replay": "not_performed_by_inspection",
        "verification_id": native["verification"]["verification_id"],
        "retained_verification_outcome": native["verification"].get("outcome")}
    if window:
        context.update(calibration_validity="checked_at_nominal_mapped_event_times",
            clock_result_id=native["steps"][0]["result_id"], calibration_result_id=native["steps"][1]["result_id"],
            time_policy=configuration["composition"]["time_policy"],
            joint_time_value_covariance=deepcopy(native["steps"][1]["result"]["data"]["joint_time_value_covariance"]),
            uncertainty_scope="first_order_conditional_on_declared_nominal_grid",
            calibration_feature_compatibility=deepcopy(native["steps"][1]["result"]["data"]["compatibility"]),
            observability={"status": "unresolved", "reason": "not_evaluated_by_calibrated_window_profile"})
    if record["kind"] == "acquired-calibrated-window":
        context.update(acquisition_binding=deepcopy(native["acquisition_binding"]),
                       native_window_bundle_id=native["child_window"]["bundle_digest"],
                       numerical_verification_id=native["child_window"]["verification"]["verification_id"],
                       reference_prior_policy="explicit_per_window_no_posterior_feedback",
                       inter_window_covariance="not_declared_by_this_window")
    return context


class Workbench:
    """Thread-safe append-only shared catalog with explicit trusted bindings."""

    def __init__(self):
        self._lock = RLock()
        self._sources = {}
        self._bundles = {}
        self._failures = {}
        self._bindings = {"energy-accuracy": {}, "thermal-observer": {}, "machine-manifest": {}, "project-graph": {}}
        self._candidate_adapters = {}
        self._candidates = {}
        self._identities = {operation: ("operation", _digest(operation)) for operation in WORKFLOW_OPERATION_IDS}
        self._used_bytes = 0
        self._pending = 0
        self._reserved_bytes = 0
        self._revision = 0

    def bind_workflow(self, kind, repositories):
        """Bind host-selected repositories; no saved/client value calls this."""
        if kind not in OPERATIONS:
            raise ValueError("This source kind has no executable operation")
        workflow = _workflow(kind)
        required = workflow.ROLES - {"cbsr"} if kind == "telemetry" else workflow.ROLES
        if not isinstance(repositories, dict) or not required <= set(repositories) <= workflow.ROLES:
            raise ValueError("Bind exactly the repositories declared by the workflow")
        if any(not isinstance(path, (str, Path)) or not str(path).strip() for path in repositories.values()):
            raise ValueError("Workflow repository bindings must be explicit host paths")
        bindings = {role: Path(path).resolve() for role, path in repositories.items()}
        # Validate trusted provider identities before advertising availability.
        # The workflows check them again at each execution and replay.
        if kind == "telemetry":
            workflow._adapters({"cbsr": {}} if "cbsr" in bindings else {}, bindings)
        else:
            workflow._adapters(bindings)
        with self._lock:
            self._bindings[kind] = bindings

    @property
    def pending_operations(self):
        with self._lock:
            return self._pending

    @property
    def revision(self):
        with self._lock:
            return self._revision

    def describe_operations(self):
        with self._lock:
            return [{"operation_id": operation, "role": {"identified-design": "decision", "schematic-assessment": "schematic_assessment", "numerical-heat": "numerical_execution", "proved-heat": "proved_numerical_execution", "schematic-companions": "local_model_analysis", "bim-quantity": "construction_quantity", "acquired-dataset": "evidence_acquisition", "residual-monitor": "residual_diagnostics", "measurement-chain": "measurement_chain_testbed", "geometric-circle": "geometric_reconciliation", "identified-stability": "stability_assessment", "flat-torus-reference": "geometric_reference", "curved-path-transfer": "geometric_sensitivity", "covariance-geometry": "covariance_geometry", "mesh-path": "mesh_path_baseline", "translation-flow": "translation_dynamics", "variational-free-energy": "variational_inference", "energy-accuracy": "offline_energy_accuracy_analysis", "instrument-exchange": "typed_exchange_adapter", "thermal-observer": "thermal_observer_reference", "machine-manifest": "machine_manifest_reference", "julia-oscillator": "julia_tsit5_provider", "native-interop": "native_computation", "project-graph": "project_graph_inspection"}.get(kind, "state_estimator"),
                     "source_kind": kind, "available": kind in self._bindings,
                     "requires_upstream_bundle": kind in UPSTREAM_KINDS,
                     **({"requires_upstream_bundles": "explicit_ordered_source_selection"} if kind == "residual-monitor" else {})}
                    for kind, operation in OPERATIONS.items()] + [
                        {"operation_id": operation, "role": "candidate_evidence", "requires_bundle": "explicit_retained_native_bundle",
                         "available": any(action == "inspect" or adapter.capture_available for adapter in self._candidate_adapters.values()),
                         "available_bundle_kinds": sorted(kind for kind, adapter in self._candidate_adapters.items()
                                                          if action == "inspect" or adapter.capture_available),
                         "canonical_admission": "not_performed"}
                        for operation, action in CANDIDATE_OPERATIONS.items()]

    def bind_candidate_adapter(self, configuration):
        """Trusted host binding only; never recovered from workspace records."""
        from .candidate_evidence import CandidateAdapter
        adapter = CandidateAdapter(configuration)
        with self._lock:
            self._candidate_adapters[adapter.kind] = adapter

    def instrument_views(self):
        with self._lock:
            return deepcopy([{ "bundle_id": record["bundle_id"], "source_id": record["source_id"],
                "instrument": step["runtime_ref"], "operation_id": step["operation_id"],
                "result_id": step["result_id"], "execution_id": step["execution_id"],
                "view": "retained_native_result", "state_admission": "not_performed"}
                for record in self._bundles.values()
                for step in {s["runtime_ref"]: s for s in _catalog_steps(record["native"])}.values()
                if step["runtime_ref"] in INSTRUMENT_ROLES])

    def inspect_instrument(self, payload):
        _keys(payload, {"bundle_id", "instrument"})
        _text(payload["bundle_id"], "Bundle identity")
        if payload["instrument"] not in INSTRUMENT_ROLES:
            raise ValueError("Choose a retained instrument result")
        with self._lock:
            native = self.get_bundle(payload["bundle_id"])
            steps = {step["runtime_ref"]: step for step in _catalog_steps(native)}
            if payload["instrument"] not in steps:
                raise ValueError("Instrument did not execute in this bundle; explicitly select its upstream bundle")
            record = self._bundles[payload["bundle_id"]]
            return deepcopy({"bundle_id": record["bundle_id"], "source_id": record["source_id"],
                "instrument": payload["instrument"], "step": steps[payload["instrument"]],
                "fusion_context": None if record["kind"] in DECLARED_KINDS else _context(record, self._sources),
                "linked_results": {role: step["result_id"] for role, step in steps.items()},
                "numerical_replay": "not_performed_by_inspection", "state_admission": "not_performed"})

    def execute_candidate(self, operation_id, parameters):
        from .candidate_evidence import MAX_RESPONSE
        action = CANDIDATE_OPERATIONS[operation_id]
        required = {"bundle_id", "inspected_at"} if action == "inspect" else {"bundle_id", "evidence_id", "workflow_id", "retained_at"}
        _keys(parameters, required)
        for key in required:
            _text(parameters[key], key)
        parameters = deepcopy(parameters)
        reserved = MAX_RESPONSE * 2 + 2 * 1024 * 1024
        with self._lock:
            bundle = self.get_bundle(parameters["bundle_id"])
            kind = self._bundles[parameters["bundle_id"]]["kind"]
            if kind not in {"telemetry", "calibrated-observable"}:
                raise ValueError("ESM requires an explicitly selected calibrated or telemetry bundle")
            adapter = self._candidate_adapters.get(kind)
            if adapter is None:
                raise AdapterRefusal("operation_unavailable", "No operator-bound ESM candidate adapter")
            if (len(self._candidates) + self._pending >= MAX_BUNDLES or
                    self._used_bytes + self._reserved_bytes + reserved + _OVERHEAD > MAX_BYTES):
                raise AdapterRefusal("workbench_capacity", "Candidate receipt capacity exceeded")
            self._pending += 1
            self._reserved_bytes += reserved
        try:
            from uuid import uuid4
            evidence = adapter.execute(action, parameters, bundle)
            record = {"schema": "ciw.candidate-action.v1", "execution_id": "candidate-execution:" + str(uuid4()),
                      "operation_id": operation_id, "parameters": parameters, **evidence}
            record["candidate_id"] = "candidate:" + _digest(record)
            self._validate_candidate(record)
            size = len(_canonical(record))
            with self._lock:
                if size > reserved:
                    raise AdapterRefusal("workbench_capacity", "Candidate receipt exceeds reservation; capture may have retained bytes")
                self._candidates[record["candidate_id"]] = record
                self._used_bytes += size
                self._revision += 1
            return self.get_candidate(record["candidate_id"])
        finally:
            with self._lock:
                self._pending -= 1
                self._reserved_bytes -= reserved

    def _validate_candidate(self, record):
        from .candidate_evidence import MAX_RESPONSE, validate_response
        _keys(record, {"schema", "candidate_id", "execution_id", "operation_id", "parameters", "response_bytes_b64", "operator_policy", "adapter_identity"})
        if record["schema"] != "ciw.candidate-action.v1" or record["operation_id"] not in CANDIDATE_OPERATIONS:
            raise ValueError("Unsupported candidate action record")
        _text(record["execution_id"], "Candidate execution identity")
        if record["candidate_id"] != "candidate:" + _digest({k: v for k, v in record.items() if k != "candidate_id"}):
            raise ValueError("Candidate receipt content mismatch")
        action = CANDIDATE_OPERATIONS[record["operation_id"]]
        _keys(record["operator_policy"], {"review_context"} | ({"capture_registration"} if action == "capture" else set()))
        if any(not isinstance(value, dict) for value in record["operator_policy"].values()):
            raise ValueError("Candidate receipt needs explicit operator policy records")
        _keys(record["adapter_identity"], {"repository", "revision", "artifact_sha256", "helper_sha256", "replay_ciw_revision", "node_sha256"})
        from re import fullmatch
        if (record["adapter_identity"]["repository"] != "giasonpooni/Evidence-and-State-Management" or
                any(not isinstance(value, str) or not fullmatch("[a-f0-9]{40}" if key.endswith("revision") else "[a-f0-9]{64}", value)
                    for key, value in record["adapter_identity"].items() if key != "repository")):
            raise ValueError("Candidate adapter identity is malformed")
        _keys(record["parameters"], {"bundle_id", "inspected_at"} if action == "inspect" else
              {"bundle_id", "evidence_id", "workflow_id", "retained_at"})
        for key, value in record["parameters"].items():
            _text(value, key)
        encoded = record["response_bytes_b64"]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_RESPONSE + 2) // 3):
            raise ValueError("Candidate receipt byte limit")
        output = base64.b64decode(encoded, validate=True)
        if base64.b64encode(output).decode("ascii") != encoded:
            raise ValueError("Candidate receipt must retain canonical base64 bytes")
        bundle = self.get_bundle(record["parameters"]["bundle_id"])
        if bundle["schema"] not in {"ciw.calibrated-observable-session.v1", "ciw.telemetry-session.v1"}:
            raise ValueError("Candidate receipt requires a retained calibrated or telemetry bundle")
        validate_response(_json(output), action, bundle, _canonical(bundle), record["parameters"], record["operator_policy"])

    def list_candidates(self):
        with self._lock:
            return [{"candidate_id": record["candidate_id"], "bundle_id": record["parameters"]["bundle_id"],
                     "operation_id": record["operation_id"], "execution_id": record["execution_id"],
                     "state": _json(base64.b64decode(record["response_bytes_b64"]))["state"],
                     "eligibility": "historical_receipt_only", "state_admission": "not_performed"}
                    for record in self._candidates.values()]

    def get_candidate(self, candidate_id):
        _text(candidate_id, "Candidate action identity")
        with self._lock:
            if candidate_id not in self._candidates:
                raise ValueError("Unknown candidate action")
            record = deepcopy(self._candidates[candidate_id])
            return {**record, "native_response": _json(base64.b64decode(record["response_bytes_b64"])),
                    "eligibility": "historical_receipt_only", "state_admission": "not_performed"}

    def candidate_executions(self):
        with self._lock:
            return [{"execution_id": record["execution_id"], "operation_id": record["operation_id"],
                     "runtime_ref": "esm", "bundle_id": record["parameters"]["bundle_id"],
                     "candidate_id": record["candidate_id"], "status": "completed",
                     "outcome": _json(base64.b64decode(record["response_bytes_b64"]))["state"],
                     "state_admission": "not_performed"} for record in self._candidates.values()]

    def _check_claims(self, claims):
        for identity, claim in claims.items():
            if identity in self._identities and self._identities[identity] != claim:
                raise ValueError("Identity collision across retained workbench artifacts")

    def add_source(self, payload):
        try:
            source = _source(payload)
        except (RecursionError, OverflowError) as exc:
            raise ValueError("Source exceeds the JSON nesting or numeric budget") from exc
        size, claims = len(_canonical(source)), _source_claims(source)
        with self._lock:
            existing = self._sources.get(source["source_id"])
            if existing is not None:
                if _canonical(existing) != _canonical(source):
                    raise ValueError("Source identity collision")
                return deepcopy(_descriptor(existing))
            if len(self._sources) >= MAX_SOURCES or self._used_bytes + self._reserved_bytes + size + _OVERHEAD > MAX_BYTES:
                raise AdapterRefusal("workbench_capacity", "Retained source capacity exceeded")
            self._check_claims(claims)
            self._sources[source["source_id"]] = source
            self._identities.update(claims)
            self._used_bytes += size
            self._revision += 1
            return deepcopy(_descriptor(source))

    def list_sources(self):
        with self._lock:
            return deepcopy([_descriptor(value) for value in self._sources.values()])

    def get_source(self, source_id):
        _text(source_id, "Source identity")
        with self._lock:
            if source_id not in self._sources:
                raise ValueError("Unknown retained workbench source")
            return deepcopy(self._sources[source_id])

    def retained_sources(self):
        """Capture detached exact source descriptors and bytes under one lock."""
        with self._lock:
            return deepcopy(self._sources)

    def list_bundles(self):
        with self._lock:
            return deepcopy([_summary(value) for value in self._bundles.values()])

    def get_bundle(self, bundle_id):
        _text(bundle_id, "Bundle identity")
        with self._lock:
            if bundle_id not in self._bundles:
                raise ValueError("Unknown retained workbench bundle")
            return deepcopy(self._bundles[bundle_id]["native"])

    def _reserve(self, kind, failure_bytes=0):
        if kind not in self._bindings:
            raise AdapterRefusal("operation_unavailable", "No trusted repositories bound for this workbench workflow")
        reserved = _workflow(kind).MAX_BYTES + _OVERHEAD + failure_bytes
        if (len(self._bundles) + len(self._failures) + self._pending >= MAX_BUNDLES or
                self._used_bytes + self._reserved_bytes + reserved + _OVERHEAD > MAX_BYTES):
            raise AdapterRefusal("workbench_capacity", "Retained bundle capacity exceeded")
        self._pending += 1
        self._reserved_bytes += reserved
        return dict(self._bindings[kind]), reserved

    def list_failed_executions(self):
        with self._lock:
            return deepcopy(list(self._failures.values()))

    def failed_execution_summaries(self):
        """Bound live projections; exact requests remain in saved/local history."""
        fields = ("execution_id", "scope", "kind", "action", "operation_id", "source_id",
                  "evidence_id", "request_sha256", "started_at", "finished_at", "phase",
                  "status", "runtime", "runtime_status", "result_id", "bundle_id",
                  "failure", "record_sha256")
        with self._lock:
            return deepcopy([{"schema": "ciw.workflow-failure-summary.v1",
                **{key: record[key] for key in fields},
                "replay_of_bundle_id": record["request"]["bundle_id"] if record["action"] == "replay" else None}
                for record in self._failures.values()])

    def _retain_failure(self, context, phase, exc, reserved):
        from . import workflow_attempts as attempts
        record = attempts.finish(context, phase, exc)
        size = len(_canonical(record))
        with self._lock:
            attempts.validate(record, self._sources, self._bundles, OPERATIONS, UPSTREAM_KINDS)
            claims = attempts.claims(record)
            self._check_claims(claims)
            if record["execution_id"] in self._failures:
                raise ValueError("Duplicate failed workflow execution identity")
            if size > reserved or self._used_bytes + size + _OVERHEAD > MAX_BYTES:
                raise ValueError("Failed workflow record exceeded its reservation")
            self._failures[record["execution_id"]] = record
            self._identities.update(claims)
            self._used_bytes += size
            self._revision += 1

    def _retain(self, kind, source, upstream_id, native):
        native = deepcopy(native)
        record = {"kind": kind, "source_id": source["source_id"],
            "upstream_bundle_id": upstream_id, "bundle_id": native.get("bundle_digest"), "native": native}
        claims = _validate_record(record, {source["source_id"]: source})
        size = len(_canonical(record))
        with self._lock:
            if record["bundle_id"] in self._bundles:
                raise ValueError("Workflow must return a fresh native bundle identity")
            _validate_links(record, self._bundles)
            self._check_claims(claims)
            if self._used_bytes + size + _OVERHEAD > MAX_BYTES:
                raise AdapterRefusal("workbench_capacity", "Retained bundle byte capacity exceeded")
            self._bundles[record["bundle_id"]] = record
            self._identities.update(claims)
            self._used_bytes += size
            self._revision += 1
            return deepcopy(_summary(record))

    def execute(self, payload):
        _keys(payload, {"operation_id", "source_id"}, {"upstream_bundle_id", "configuration"})
        payload = deepcopy(payload)
        _text(payload["operation_id"], "Operation identity")
        _text(payload["source_id"], "Source identity")
        kind = next((kind for kind, operation in OPERATIONS.items() if operation == payload["operation_id"]), None)
        if kind is None:
            raise AdapterRefusal("operation_unavailable", "Unknown shared workbench operation")
        if kind == "telemetry":
            _keys(payload, {"operation_id", "source_id", "configuration"})
            if not isinstance(payload["configuration"], dict):
                raise ValueError("Telemetry requires an explicit window and estimator configuration")
            configuration = deepcopy(payload["configuration"])
        elif "configuration" in payload:
            raise ValueError("Only telemetry accepts a separate operation configuration")
        upstream_id = payload.get("upstream_bundle_id")
        if kind in UPSTREAM_KINDS:
            _text(upstream_id, "Explicit upstream bundle identity")
        elif upstream_id is not None:
            raise ValueError("This workflow does not accept an upstream bundle")
        with self._lock:
            source = self.get_source(payload["source_id"])
            if source["kind"] != kind:
                raise ValueError("Operation source kind mismatch")
            upstream = None
            if upstream_id is not None:
                if upstream_id not in self._bundles or self._bundles[upstream_id]["kind"] != UPSTREAM_KINDS[kind]:
                    raise ValueError("Select a retained upstream bundle of the declared kind")
                upstream = deepcopy(self._bundles[upstream_id]["native"])
            if kind == "residual-monitor":
                raw = base64.b64decode(source["bytes_b64"], validate=True)
                requested = _workflow(kind).requested_upstream_ids(raw)
                if any(identity not in self._bundles for identity in requested):
                    raise ValueError("Select window bundles already retained in this workbench")
                upstream = {identity: deepcopy(self._bundles[identity]["native"]) for identity in requested}
            if kind == "native-interop":
                from .native_interop import validate_dependency
                validate_dependency(_workflow(kind)._source(base64.b64decode(source["bytes_b64"], validate=True)), self._bundles)
            from . import workflow_attempts as attempts
            context = attempts.start(kind, "execute", payload, source, operation_id=OPERATIONS[kind])
            failure_bytes = len(_canonical(payload)) + attempts.MAX_OVERHEAD
            bindings, reserved = self._reserve(kind, failure_bytes)
        phase = "dispatch"
        try:
            raw = base64.b64decode(source["bytes_b64"], validate=True)
            workflow = _workflow(kind)
            if kind == "telemetry":
                roles = {"ppda", "stfe", "gsie", "set"} | ({"cbsr"} if "cbsr" in configuration else set())
                if not roles <= bindings.keys():
                    raise AdapterRefusal("operation_unavailable", "Telemetry reconciliation needs an explicitly bound CBSR checkout")
                native = workflow.create_session(raw, configuration, {role: bindings[role] for role in roles})
            else:
                native = workflow.create_session(raw, bindings) if upstream is None else workflow.create_session(raw, upstream, bindings)
            phase = "retention"
            return self._retain(kind, source, upstream_id, native)
        except Exception as exc:
            self._retain_failure(context, phase, exc, failure_bytes)
            raise
        finally:
            with self._lock:
                self._pending -= 1
                self._reserved_bytes -= reserved

    def replay(self, payload):
        _keys(payload, {"bundle_id"})
        payload = deepcopy(payload)
        _text(payload["bundle_id"], "Bundle identity")
        with self._lock:
            if payload["bundle_id"] not in self._bundles:
                raise ValueError("Unknown retained workbench bundle")
            record = deepcopy(self._bundles[payload["bundle_id"]])
            source = deepcopy(self._sources[record["source_id"]])
            from . import workflow_attempts as attempts
            context = attempts.start(record["kind"], "replay", payload, source,
                                     operation_id=OPERATIONS[record["kind"]])
            failure_bytes = len(_canonical(payload)) + attempts.MAX_OVERHEAD
            bindings, reserved = self._reserve(record["kind"], failure_bytes)
        phase = "dispatch"
        try:
            if record["kind"] == "telemetry":
                roles = set(record["native"]["runtimes"])
                if not roles <= bindings.keys():
                    raise AdapterRefusal("operation_unavailable", "Replay requires the original telemetry provider set")
                bindings = {role: bindings[role] for role in roles}
            replayed = _workflow(record["kind"]).replay_session(record["native"], bindings)
            phase = "retention"
            native, receipt = replayed["session"], replayed["replay_receipt"]
            if (receipt["source_bundle_digest"] != record["bundle_id"] or
                    _canonical(native.get("replay_receipts")) != _canonical([receipt])):
                raise ValueError("Replay receipt differs from the returned native bundle")
            summary = self._retain(record["kind"], source, record["upstream_bundle_id"], native)
            return {"bundle": summary, "replay_receipt": deepcopy(receipt)}
        except Exception as exc:
            self._retain_failure(context, phase, exc, failure_bytes)
            raise
        finally:
            with self._lock:
                self._pending -= 1
                self._reserved_bytes -= reserved

    def fusion_contexts(self):
        with self._lock:
            return deepcopy([_context(record, self._sources) for record in self._bundles.values() if record["kind"] not in DECLARED_KINDS])

    def spatial_sources(self):
        with self._lock:
            return deepcopy([_descriptor(source) for source in self._sources.values()
                             if source["kind"] == "geographic-context"])

    def inspect_spatial(self, payload):
        from .spatial_view import project
        _keys(payload, {"source_id"})
        _text(payload["source_id"], "Source identity")
        with self._lock:
            source = self.get_source(payload["source_id"])
            if source["kind"] != "geographic-context":
                raise ValueError("Select an explicitly declared geographic context")
            return project(source)

    def inspect_experiment(self, payload):
        from .experiment_view import project
        _keys(payload, {"bundle_id"})
        _text(payload["bundle_id"], "bundle_id")
        with self._lock:
            record = self._bundles.get(payload["bundle_id"])
            if record is None:
                raise ValueError("Unknown retained workbench bundle")
            source = self._sources[record["source_id"]]
            declaration = _json(base64.b64decode(source["bytes_b64"], validate=True))
            if record["kind"] == "energy-accuracy":
                from .energy_view import project as project_energy
                return project_energy(record, source, declaration, self._revision)
            if record["kind"] == "variational-free-energy":
                from .free_energy_view import project as project_free_energy
                return project_free_energy(record, source, declaration, self._revision)
            if record["kind"] in {"covariance-geometry", "mesh-path", "translation-flow"}:
                from .geometry_research_view import project as project_geometry_research
                return project_geometry_research(record, source, declaration, self._revision)
            if record["kind"] in {"flat-torus-reference", "curved-path-transfer"}:
                from .geodesic_reference_view import project as project_reference
                return project_reference(record, source, declaration, self._revision)
            if record["kind"] == "residual-monitor":
                from .residual_view import project as project_residual
                return project_residual(record, source, declaration, self._revision)
            if record["kind"] == "measurement-chain":
                from .measurement_chain_view import project as project_measurement
                return project_measurement(record, source, declaration, self._revision)
            if record["kind"] == "geometric-circle":
                from .geometric_circle_view import project as project_geometry
                return project_geometry(record, source, declaration, self._revision)
            if record["kind"] == "identified-stability":
                from .identified_stability_view import project as project_stability
                return project_stability(record, source, declaration, self._revision)
            if record["kind"] == "instrument-exchange":
                from .exchange_view import project as project_exchange
                return project_exchange(record, source, declaration, self._revision)
            if record["kind"] == "acquired-calibrated-window":
                declaration = _workflow(record["kind"]).mapped_source(record["native"])
            if record["kind"] in DECLARED_KINDS:
                from .workload_view import project as project_workload
                return project_workload(record, source, declaration, self._revision)
            return project(record, source, declaration, _context(record, self._sources), self._revision)

    def _native_steps(self):
        """Expose embedded replay occurrences without counting reused upstream twice."""
        seen = set()

        def visit(record, native):
            for step in _catalog_steps(native):
                if step["execution_id"] not in seen:
                    seen.add(step["execution_id"])
                    yield record, native, step
            if "upstream" in native:
                yield from visit(record, native["upstream"])
                yield from visit(record, native["upstream_replay"]["session"])

        # Prefer the explicit catalog owner for original upstream occurrences.
        for record in self._bundles.values():
            for step in _catalog_steps(record["native"]):
                if step["execution_id"] not in seen:
                    seen.add(step["execution_id"])
                    yield record, record["native"].get("child_window", record["native"]), step
        for record in self._bundles.values():
            yield from visit(record, record["native"])

    def native_results(self):
        with self._lock:
            unique = {step["result_id"]: step["result"] for _, _, step in self._native_steps()}
            return deepcopy(list(unique.values()))

    def native_result_summaries(self):
        with self._lock:
            unique = {}
            for _, _, step in self._native_steps():
                if step["result_id"] not in unique:
                    unique[step["result_id"]] = {
                        **{key: step["result"][key] for key in ("schema", "execution_ref", "execution_id") if key in step["result"]},
                        "result_id": step["result_id"], "operation_id": step["operation_id"],
                        **({"identity_kind": "observation_batch", "batch_id": step["result_id"]} if step["runtime_ref"] == "ppda" else {})}
            return deepcopy(list(unique.values()))

    def get_native_result(self, result_id):
        with self._lock:
            for _, _, step in self._native_steps():
                if step["result_id"] == result_id:
                    return deepcopy(step["result"])
        return None

    def native_executions(self):
        with self._lock:
            return deepcopy([{**{k: step[k] for k in ("execution_id", "operation_id", "runtime_ref", "input_refs", "result_id")},
                "bundle_id": record["bundle_id"], "native_bundle_id": native["bundle_digest"],
                "source_id": (record["source_id"] if native["bundle_digest"] == record["bundle_id"] or record["kind"] == "acquired-calibrated-window"
                              else self._bundles[record["upstream_bundle_id"]]["source_id"]),
                **({"native_source_evidence_id": native["source"]["evidence"][0]["artifact_ref"]}
                   if record["kind"] == "acquired-calibrated-window" else {}),
                "status": "completed"}
                for record, native, step in self._native_steps()]) + self.failed_execution_summaries()

    def dependency_artifacts(self):
        """Capture the lightweight catalog dependency surface under one lock."""
        with self._lock:
            bundles = self.list_bundles()
            for bundle in bundles:
                if bundle["kind"] == "native-interop":
                    source = self._sources[bundle["source_id"]]
                    declaration = _json(base64.b64decode(source["bytes_b64"], validate=True))
                    upstream = declaration.get("upstream")
                    if upstream is not None:
                        bundle["upstream_bundle_ids"] = [upstream["bundle_digest"]]
            return deepcopy({"sources": self.list_sources(), "bundles": bundles,
                "executions": [entry for entry in self.native_executions()
                               if entry["status"] == "completed"]})

    def snapshot(self):
        with self._lock:
            return {"schema": SCHEMA, "revision": self._revision, "sources": self.list_sources(),
                "bundles": self.list_bundles(), "fusion_contexts": self.fusion_contexts(),
                "instruments": self.instrument_views(), "candidates": self.list_candidates(),
                **({"failed_executions": self.failed_execution_summaries()} if self._failures else {}),
                "operations": self.describe_operations()}

    def serialize(self):
        with self._lock:
            return deepcopy({"schema": "ciw.retained-workbench.v3" if self._failures else "ciw.retained-workbench.v2" if self._candidates else SCHEMA, "revision": self._revision,
                "sources": list(self._sources.values()), "bundles": list(self._bundles.values()),
                **({"candidates": list(self._candidates.values())} if self._candidates or self._failures else {}),
                **({"failed_executions": list(self._failures.values())} if self._failures else {})})

    @classmethod
    def restore(cls, value):
        """Validate saved data; only builtin offline analysis remains available."""
        try:
            if len(_canonical(value)) > MAX_BYTES:
                raise ValueError("Retained workbench exceeds the byte budget")
            value = deepcopy(value)
            failures_version = value.get("schema") == "ciw.retained-workbench.v3"
            new = value.get("schema") in {"ciw.retained-workbench.v2", "ciw.retained-workbench.v3"}
            _keys(value, {"schema", "revision", "sources", "bundles"} | ({"candidates"} if new else set())
                  | ({"failed_executions"} if failures_version else set()))
            candidates = value.get("candidates", [])
            failures = value.get("failed_executions", [])
            if (not isinstance(failures, list) or len(failures) > MAX_BUNDLES
                    or (failures_version and not failures)):
                raise ValueError("Malformed failed workflow catalog")
            if not isinstance(candidates, list) or len(candidates) > MAX_BUNDLES:
                raise ValueError("Malformed candidate receipt catalog")
            if (value["schema"] not in {SCHEMA, "ciw.retained-workbench.v2", "ciw.retained-workbench.v3"} or type(value["revision"]) is not int or
                    not isinstance(value["sources"], list) or not isinstance(value["bundles"], list) or
                    len(value["sources"]) > MAX_SOURCES or len(value["bundles"]) + len(failures) > MAX_BUNDLES or
                    value["revision"] != len(value["sources"]) + len(value["bundles"]) + len(candidates) + len(failures)):
                raise ValueError("Malformed retained workbench catalog")
            restored = cls()
            for retained in value["sources"]:
                _keys(retained, {"schema", "kind", "label", "source_schema", "evidence_id", "byte_count", "source_id", "bytes_b64"})
                source = _source({k: retained[k] for k in ("kind", "label", "bytes_b64")})
                if _canonical(source) != _canonical(retained) or source["source_id"] in restored._sources:
                    raise ValueError("Retained source identity/content mismatch")
                claims = _source_claims(source)
                restored._check_claims(claims)
                restored._sources[source["source_id"]] = source
                restored._identities.update(claims)
                restored._used_bytes += len(_canonical(source))
            for record in value["bundles"]:
                claims = _validate_record(record, restored._sources)
                if record["bundle_id"] in restored._bundles:
                    raise ValueError("Duplicate retained bundle identity")
                restored._check_claims(claims)
                restored._bundles[record["bundle_id"]] = record
                restored._identities.update(claims)
                restored._used_bytes += len(_canonical(record))
            for record in restored._bundles.values():
                _validate_links(record, restored._bundles)
            from . import workflow_attempts as attempts
            for record in failures:
                attempts.validate(record, restored._sources, restored._bundles, OPERATIONS, UPSTREAM_KINDS)
                if record["execution_id"] in restored._failures:
                    raise ValueError("Duplicate failed workflow execution identity")
                claims = attempts.claims(record)
                restored._check_claims(claims)
                restored._failures[record["execution_id"]] = record
                restored._identities.update(claims)
                restored._used_bytes += len(_canonical(record))
            occurrences = {entry["execution_id"] for entry in restored.native_executions()}
            for record in candidates:
                restored._validate_candidate(record)
                if record["candidate_id"] in restored._candidates or record["execution_id"] in occurrences:
                    raise ValueError("Duplicate candidate action identity")
                occurrences.add(record["execution_id"])
                restored._candidates[record["candidate_id"]] = record
                restored._used_bytes += len(_canonical(record))
            if restored._used_bytes + _OVERHEAD > MAX_BYTES:
                raise ValueError("Retained workbench exceeds the storage byte budget")
            restored._revision = value["revision"]
            # Check projections now, so a restored catalog can always be read.
            restored.fusion_contexts()
            return restored
        except (KeyError, TypeError, IndexError, OverflowError, RecursionError) as exc:
            raise ValueError("Malformed retained workbench") from exc
