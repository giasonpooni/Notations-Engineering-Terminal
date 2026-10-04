"""Read-only review-coverage audit projected from NET's existing authorities.

This is an audit obligation list, never an executable registry. A passing gate
means that every indexed entry has a reviewed scope and existing reference
files. It does not mean that referenced tests ran or the science was qualified.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path


# Keys refer to the existing operator catalog. Values name review evidence;
# they cannot introduce commands, workflows, providers, or executable bindings.
COMMAND_REVIEWS = {
    "catalog": "operator_catalog", "doctor": "operator_doctor",
    "start": "operator_start", "workbench": "operator_workbench",
    "provision": "operator_provision", "legibility": "legibility_workflow",
    "polymer": "polymer_workflow", "rewrite": "hypergraph_rewrite",
    "compose": "workflow_algebra", "fluid": "fluid_independent_audit",
    "irrigation": "irrigation_workflow", "polymer leakage": "leakage_workflow",
    "system": "system_workflow",
    "dsp": "dsp", "impact": "impact_workflow", "lab": "preservation_experiments",
    "foundry": "foundry", "atmosphere": "atmosphere_integration_audit",
    "object": "computational_objects", "semantic": "semantic_capabilities",
    "instrument": "instrument_contracts", "nise": "nise_handoff",
    "annotation": "annotations", "efficiency": "investigation_efficiency",
    "container": "container_calculus", "needle": "needle", "board": "system_board",
    "parameter": "parameter_program", "preservation": "preservation_contracts",
    "transition": "industrial_transition", "interop": "interop_ingress",
    "interop-bim": "bim_interop", "morphism": "representation_morphisms",
    "provenance": "artifact_provenance", "columnar": "columnar_optional",
    "workcell": "workcell", "history": "historical_perspective",
    "production": "production", "tools": "csr_microtools",
    "simulation": "simulation_control", "simulate": "simulation_study",
    "math": "math_inspector", "view": "information_display",
    "check": "check_suite", "science": "scientific",
    "providers": "control_plane", "capabilities": "control_plane",
    "inspect": "computational_objects", "compare": "computational_objects",
    "run": "control_plane", "demo": "control_plane",
}

# Nested catalog entries retain their actual implementation source rather than
# falling back to the root command dispatcher.
COMMAND_SOURCE_OVERRIDES = {"polymer leakage": "leakage_cli"}

# (existing source/contract module, existing test module)
WORKFLOW_REVIEWS = {
    "calibrated-observable": ("calibrated_observable", "calibrated_observable"),
    "identified-design": ("identified_design", "identified_design"),
    "telemetry": ("telemetry", "telemetry"),
    "calibrated-window": ("calibrated_window", "calibrated_window"),
    "schematic-assessment": ("declared_workload", "declared_workloads"),
    "numerical-heat": ("declared_workload", "declared_workloads"),
    "proved-heat": ("proved_heat", "proved_heat_audit_gate"),
    "schematic-companions": ("schematic_companions", "schematic_companions"),
    "bim-quantity": ("bim_quantity", "bim_quantity"),
    "acquired-dataset": ("acquired_dataset", "acquired_dataset"),
    "acquired-calibrated-window": ("acquired_window", "acquired_window"),
    "residual-monitor": ("residual_monitor", "residual_monitor"),
    "measurement-chain": ("measurement_chain", "measurement_chain"),
    "geometric-circle": ("geometric_circle", "geometric_circle"),
    "identified-stability": ("identified_stability", "identified_stability"),
    "flat-torus-reference": ("geodesic_reference", "geodesic_reference"),
    "curved-path-transfer": ("geodesic_reference", "curved_path_study"),
    "covariance-geometry": ("geometry_covariance_contract", "geometry_covariance_contract"),
    "mesh-path": ("geometry_mesh_contract", "geometry_mesh_contract"),
    "translation-flow": ("geometry_translation_contract", "geometry_translation_contract"),
    "variational-free-energy": ("free_energy_contract", "free_energy_contract"),
    "energy-accuracy": ("energy_workflow", "energy_workflow"),
    "instrument-exchange": ("exchange_adapter", "exchange_adapter"),
    "thermal-observer": ("thermal_contract", "thermal_contract"),
    "machine-manifest": ("machine_workflow", "machine_manifest"),
    "julia-oscillator": ("julia_oscillator", "julia_oscillator"),
    "native-interop": ("native_interop_contract", "native_interop_contract"),
    "project-graph": ("project_workflow", "project_workflow"),
}
# Reviewed operation versions, checked against rather than supplied to execution.
WORKFLOW_IDENTITIES = {name: "ciw." + name + ".v1" for name in WORKFLOW_REVIEWS}
WORKFLOW_IDENTITIES["machine-manifest"] = "ciw.encoder-position.v1"

# These ladder rows describe scoped controls and explicitly retain unresolved
# obligations. A referenced file is evidence to inspect, not a test result.
LADDER = (
    ("identity", "Evidence, operation, execution and verification are distinct records.",
     "core/identities", "operation_runner", "Content consistency does not authenticate physical provenance."),
    ("composition", "Typed ports, declared schemas and explicit provider bindings.",
     "control_plane", "control_plane", "No universal dimensional-algebra checker for every domain port."),
    ("geometry", "Declared frames and bounded geometric preservation checks.",
     "preservation_contracts", "preservation_contracts", "An adapter only preserves its explicitly checked quantities."),
    ("dynamics", "Bounded domain equations and source/result validators.",
     "fluid_contract", "fluid_independent_audit", "No general multiphysics or multiscale validity guarantee."),
    ("measurement_uncertainty", "Domain-specific covariance and calibration contracts.",
     "thermal_contract", "covariance_records", "Calibration traceability and empirical uncertainty validity remain workload-specific."),
    ("signals_time", "Explicit observation clocks and bounded signal-processing profiles.",
     "control_plane", "dsp", "Generic telemetry does not establish clock synchronization or anti-aliasing adequacy."),
    ("inference", "Declared state estimators and explicit retained uncertainty.",
     "workbench", "calibrated_observable", "Telemetry and calibrated-window inspection explicitly leave observability unresolved."),
    ("intervention", "Bounded local proposals and declared transition authority.",
     "industrial_transition", "industrial_transition", "Numerical acceptance does not authorize physical actuation."),
    ("cyber_physical_coordination", "Bounded simulation modes and retained workflow occurrences.",
     "workflow_algebra", "workflow_algebra", "General hybrid reachability, real-time deadlines and distributed fault tolerance are not established."),
    ("assurance", "Domain checks, replay and scoped verification records.",
     "check_suite", "check_suite", "Replay, numerical verification, formal proof and experimental validation remain distinct."),
)


def audit(*, repository: Path | None = None, inventory: dict | None = None) -> dict:
    """Audit indexed surfaces; never import, bind, run or qualify a provider.

    With repository supplied, verify every declared source/test reference is a
    regular file within that checkout. Without it, installed-package inventory
    drift is checked and source-reference existence is explicitly unverified.
    """
    from .operator_commands import COMMANDS
    if inventory is None:
        from .operator_catalog import catalog
        inventory = catalog()
    value = deepcopy(inventory)
    issues = []
    commands = [row["command"].removeprefix("net ") for row in value["commands"]]
    workflows = [row["workflow"] for row in value["workflows"]]
    for label, actual, reviewed in (("command", commands, COMMAND_REVIEWS),
                                     ("workflow", workflows, WORKFLOW_REVIEWS)):
        if len(actual) != len(set(actual)):
            issues.append({"code": "duplicate_surface", "surface": label})
        for name in sorted(set(actual) - reviewed.keys()):
            issues.append({"code": "unreviewed_surface", "surface": label, "name": name})
        for name in sorted(reviewed.keys() - set(actual)):
            issues.append({"code": "stale_review", "surface": label, "name": name})

    modules = {name: module for name, module, _ in COMMANDS}
    modules.update(COMMAND_SOURCE_OVERRIDES)
    command_rows = [{"command": "net " + name,
                     "review": "reference_declared",
                     "source": "src/ciw/" + modules.get(name, "net") + ".py",
                     "test": "tests/test_" + COMMAND_REVIEWS[name] + ".py"}
                    for name in sorted(set(commands) & COMMAND_REVIEWS.keys())]
    by_workflow = {row["workflow"]: row for row in value["workflows"]}
    for name in sorted(set(workflows) & WORKFLOW_IDENTITIES.keys()):
        if by_workflow[name].get("operation_id") != WORKFLOW_IDENTITIES[name]:
            issues.append({"code": "changed_operation_identity", "workflow": name,
                           "reviewed": WORKFLOW_IDENTITIES[name],
                           "actual": by_workflow[name].get("operation_id")})
    workflow_rows = [{"workflow": name, "operation_id": by_workflow[name]["operation_id"],
                      "review": "reference_declared",
                      "source": "src/ciw/" + WORKFLOW_REVIEWS[name][0] + ".py",
                      "test": "tests/test_" + WORKFLOW_REVIEWS[name][1] + ".py"}
                     for name in sorted(set(workflows) & WORKFLOW_REVIEWS.keys())]
    ladder = [{"level": level, "existing_control": control,
               "source": "src/ciw/" + module + ".py", "test": "tests/test_" + test + ".py",
               "remaining_obligation": gap, "qualification": "not_performed_by_audit"}
              for level, control, module, test, gap in LADDER]
    references = sorted({row[key] for row in command_rows + workflow_rows + ladder
                         for key in ("source", "test")})
    reference_status = "not_checked_without_repository"
    if repository is not None:
        root = Path(repository).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Audit repository must be a directory")
        for reference in references:
            path = root / reference
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                issues.append({"code": "missing_or_unsafe_reference", "path": reference})
        reference_status = "failed" if any(item["code"] == "missing_or_unsafe_reference" for item in issues) else "files_exist_only"
    return {"schema": "ciw.scientific-coverage-audit.v1",
            "status": "failed" if issues else "coverage_gate_passed_with_open_obligations",
            "read_only": True, "authorizes_execution": False, "authorizes_state_admission": False,
            "physical_validation": "not_established", "qualification": "not_performed_by_audit",
            "tests_executed": False, "source_references": reference_status,
            "counts": {"commands": len(commands), "workflows": len(workflows), "ladder_levels": len(ladder)},
            "commands": command_rows, "workflows": workflow_rows, "ladder": ladder,
            "open_obligations": [
                {"id": "operation_contract_coverage", "status": "open",
                 "detail": "Operation registration requires identity and role but does not require a universal scientific declaration; specialist payload validators retain domain responsibility."},
                {"id": "portable_manifest_semantics", "status": "open",
                 "detail": "Portable model and verification metadata are not a general mathematical proof or independent qualification."},
                {"id": "inventory_boundary", "status": "explicit_scope_limit",
                 "detail": "This gate covers indexed CLI commands and Workbench workflows; nested subcommands, library functions and provider-internal operations need their existing domain suites."},
                {"id": "test_evidence", "status": "not_executed",
                 "detail": "References identify checks to run; file existence supplies no pass/fail, source revision or installation qualification evidence."}],
            "issues": issues}
