"""Installed acceptance for three retained correction and replay journeys.

The harness uses existing Session requests and provider-free NET workflows.
A PASS is scripted synthetic software acceptance, not a new-operator study,
physical validation, private-provider qualification, admission, or actuation.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "net_existing_operator_harness", ROOT / "scripts/check_operator_installed.py")
existing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(existing)

KINDS = ("machine-manifest", "thermal-observer", "project-graph")
AUTHORITY = {
    "independent_human_operator_acceptance": "not_performed",
    "physical_validation": "not_performed",
    "state_admission": "not_performed",
    "hardware_actuation": "not_performed",
    "private_provider_qualification": "not_performed",
}
PROVENANCE = """
import importlib.metadata as m, importlib.util, json, sys
d = m.distribution('computational-instrumentation-workbench')
direct = d.read_text('direct_url.json')
print(json.dumps({'distribution': d.metadata['Name'], 'version': d.version,
 'python': sys.version, 'interpreter': sys.executable, 'isolated': bool(sys.flags.isolated),
 'ciw_origin': importlib.util.find_spec('ciw').origin, 'package_root': str(d.locate_file('ciw')),
 'direct_url': json.loads(direct) if direct else None,
 'dependencies': {name: m.version(name) for name in ('numpy', 'websockets')}}))
"""


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _project_sources():
    from ciw import project_model as project, project_workflow
    value = project.create("project:release-acceptance", "Synthetic revision review")
    signal = {"object_id": "signal:position", "kind": "signal", "label": "Position",
              "content": {"quantity": "position", "unit": "m", "frame": "frame:carriage",
                          "time_basis": "clock:fixture", "semantics": "observed"}}
    value = project.put(value, signal)
    revision = project.inspect(value)["objects"][0]["revision"]
    result = {"object_id": "result:position", "kind": "result", "label": "Declared estimate",
              "content": {"value": 1.0, "input_revisions": {"signal:position": revision},
                          "unit": "m", "frame": "frame:carriage", "time_basis": "clock:fixture",
                          "semantics": "estimated", "claim_scope": "synthetic_declared_example"}}
    value = project.put(value, result)
    value = project.connect(value, {"edge_id": "edge:input-result", "relation": "computation",
                                   "from": "signal:position", "to": "result:position",
                                   "resolution": "resolved"})
    signal["content"]["frame"] = "frame:inspection"
    old_project = project.put(value, signal, expected_revision=revision)
    changed_signal = next(row for row in project.inspect(old_project)["objects"]
                          if row["object_id"] == "signal:position")
    result["content"]["input_revisions"]["signal:position"] = changed_signal["revision"]
    result["content"]["frame"] = "frame:inspection"
    corrected_project = project.put(old_project, result)
    source = {"schema": project_workflow.SOURCE_SCHEMA,
              "experiment_id": "project:release-acceptance",
              "configuration": deepcopy(project_workflow.CONFIGURATION)}
    return encoded({**source, "project": old_project}), encoded({**source, "project": corrected_project})


def sources(kind, fixture_root):
    """Retain exact source bytes; amendments describe synthetic inputs only."""
    if kind == "machine-manifest":
        return tuple((Path(fixture_root) / name).read_bytes()
                     for name in ("encoder-original.json", "encoder-corrected.json"))
    if kind == "thermal-observer":
        from importlib.resources import files
        raw = files("ciw").joinpath("operator_examples", "thermal-source.json").read_bytes()
        original = json.loads(raw)
        original["experiment_id"] = "thermal:release-synthetic-biased-observation"
        # Keep the retained noise declaration consistent with the deliberately
        # biased synthetic observation. No real measurement is being relabelled.
        original["request"]["observations"][0][0] += 5.0
        original["evaluation"]["measurement_noise"][0][0] += 5.0
        return encoded(original), raw
    if kind == "project-graph":
        return _project_sources()
    raise ValueError("Unsupported release journey")


def metric(kind, bundle, source):
    data = bundle["steps"][0]["result"]["data"]
    if kind == "machine-manifest":
        return {"name": "absolute_position_residual_m",
                "value": abs(data["position"]["position"] - 0.998), "limit": 0.0005,
                "reference": 0.998, "origin": "synthetic_fixture"}
    if kind == "thermal-observer":
        expected = source["evaluation"]["states"][0][0]
        return {"name": "first_core_posterior_absolute_error_k",
                "value": abs(data["observer"]["trace"][0]["posterior_mean"][0] - expected),
                "limit": 1.0, "reference": expected, "origin": "synthetic_fixture"}
    if kind == "project-graph":
        return {"name": "declared_results_needing_reevaluation",
                "value": sum(row["result_status"] == "needs_reevaluation"
                             for row in data["inspection"]["objects"]),
                "limit": 0, "origin": "synthetic_declaration"}
    raise ValueError("Unsupported release journey")


def _readonly_guard():
    """Prove restore/inspection never invokes workflow execution or replay."""
    from ciw import machine_workflow, thermal_workflow, project_workflow
    stack = ExitStack()
    for cls in (machine_workflow.MachineManifestWorkflow,
                thermal_workflow.ThermalWorkflow, project_workflow.ProjectGraphWorkflow):
        for method in ("create_session", "replay_session"):
            stack.enter_context(mock.patch.object(
                cls, method, side_effect=AssertionError("Read-only inspection executed a workflow")))
    stack.enter_context(mock.patch(
        "ciw.operations.runner.execute", side_effect=AssertionError("Read-only inspection executed an operation")))
    stack.enter_context(mock.patch(
        "ciw.session.execute_operation", side_effect=AssertionError("Read-only inspection executed a Session operation")))
    return stack


def run_journey(kind, output, fixture_root):
    from ciw.instruments import make_demo_run
    from ciw.session import Session
    from ciw.workbench import OPERATIONS

    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Use a new journey directory to preserve retained evidence")
    output.mkdir(parents=True, exist_ok=False)
    transcript = output / "requests"
    transcript.mkdir()
    report = {"schema": "ciw.release-operator-journey.v1", "kind": kind, "status": "FAIL",
              "origin": "scripted_synthetic_fixture", "authority": dict(AUTHORITY),
              "started_at": datetime.now(timezone.utc).isoformat(), "checks": [], "requests": []}

    def require(name, condition, observed=None):
        report["checks"].append({"check": name, "status": "PASS" if condition else "FAIL",
                                 "observed": observed})
        if not condition:
            raise ValueError(name)

    def call(session, action, **payload):
        request = {"protocol_version": 1, "request_id": "acceptance-" + str(len(report["requests"]) + 1),
                   "type": action, "payload": payload}
        response = session.handle(request)
        path = transcript / (f"{len(report['requests']) + 1:03d}-" + action + ".json")
        existing._write(path, {"request": request, "response": response})
        report["requests"].append({"type": action, "path": path.relative_to(output).as_posix(),
                                   "sha256": existing._hash(path)})
        if response["type"] != "response":
            raise ValueError(f"{action} refused: {response.get('payload')}")
        return response["payload"]

    def add(session, raw, label):
        return call(session, "source.add", kind=kind, label=label,
                    bytes_b64=base64.b64encode(raw).decode("ascii"))

    def execute(session, source):
        summary = call(session, "operation.execute", operation_id=OPERATIONS[kind],
                       parameters={"source_id": source["source_id"]})
        require("execution-completed-" + source["source_id"], bool(summary.get("bundle_id")), summary)
        return summary, call(session, "bundle.get", bundle_id=summary["bundle_id"])

    try:
        old_raw, new_raw = sources(kind, fixture_root)
        require("source-amendment-is-explicit", old_raw != new_raw)
        (output / "original-source.json").write_bytes(old_raw)
        (output / "corrected-source.json").write_bytes(new_raw)
        session = Session(make_demo_run(), output / "original")
        old_source = add(session, old_raw, "Synthetic original for operator acceptance")
        original_source = call(session, "source.get", source_id=old_source["source_id"])
        old_summary, old_bundle = execute(session, old_source)
        old_step = old_bundle["steps"][0]
        claim = call(session, "claim.add", claim_type="estimated",
                     predicate="Original result remains eligible for this declared source",
                     scope="Synthetic release operator acceptance only",
                     basis="Explicit retained result dependency", dependencies=[old_step["result_id"]])
        require("claim-grants-no-authority", claim["execution_authorized"] is False
                and claim["state_admission"] == "unadmitted"
                and claim["verification_status"] == "not_verified")
        original_file = session.save_workspace(output / "original/workspace.json")
        original_snapshot = existing._snapshot(output / "original")
        existing._write(output / "original-bundle.json", old_bundle)
        before = metric(kind, old_bundle, json.loads(old_raw))
        require("original-discrepancy-detected", before["value"] > before["limit"], before)

        # Inspection uses a new directory and does not rewrite the saved original.
        with _readonly_guard():
            corrected = Session.from_workspace(original_file, output / "corrected")
            inspected = call(corrected, "bundle.get", bundle_id=old_summary["bundle_id"])
            result = call(corrected, "result.get", result_id=old_step["result_id"])
            view = call(corrected, "experiment.inspect", bundle_id=old_summary["bundle_id"])
        require("read-only-inspection-preserves-retained-result",
                inspected == old_bundle and result == old_step["result"])
        existing._write(output / "inspection.json", view)
        require("original-directory-unchanged-after-inspection",
                existing._snapshot(output / "original") == original_snapshot)

        new_source = add(corrected, new_raw, "Synthetic corrected source for operator acceptance")
        # Bundle counts alone cannot detect ordinary Session operations.
        with _readonly_guard():
            proposal = call(corrected, "correction.propose",
                            old_source_id=old_source["source_id"], new_source_id=new_source["source_id"],
                            kind="synthetic-source-amendment",
                            reason="Resolve the retained synthetic discrepancy with an explicit replacement source")
            pending = call(corrected, "dependency.inspect")
            require("proposal-does-not-invalidate-or-execute",
                    pending["artifact_status"][old_step["result_id"]]["status"] == "current"
                    and len(call(corrected, "bundle.list")["bundles"]) == 1)
            call(corrected, "correction.review", correction_id=proposal["correction_id"],
                 decision="accept", expected_revision=pending["revision"],
                 reviewer="scripted-release-acceptance",
                 reason="Scripted dependency review only; human and physical acceptance remain unperformed")
            reviewed = call(corrected, "dependency.inspect")
            stale = [old_source["source_id"], old_summary["bundle_id"], old_step["execution_id"],
                     old_step["result_id"], claim["claim_id"]]
            require("accepted-amendment-invalidates-explicit-descendants",
                    all(reviewed["artifact_status"][identity]["status"] == "stale" for identity in stale))
            require("review-itself-does-not-execute", len(call(corrected, "bundle.list")["bundles"]) == 1)
        new_summary, new_bundle = execute(corrected, new_source)
        new_step = new_bundle["steps"][0]
        after = metric(kind, new_bundle, json.loads(new_raw))
        require("corrected-comparison-meets-declared-fixture-bound",
                after["value"] <= after["limit"] and after["value"] < before["value"],
                {"before": before, "after": after})
        require("correction-produces-new-occurrence-and-content",
                new_step["execution_id"] != old_step["execution_id"]
                and new_step["result_id"] != old_step["result_id"]
                and new_step["numerical_result_id"] != old_step["numerical_result_id"])
        historical = call(corrected, "bundle.replay", bundle_id=old_summary["bundle_id"])
        state = call(corrected, "dependency.inspect")
        historical_ids = historical["bundle"]["result_ids"]
        require("historical-replay-stays-stale",
                historical_ids and all(state["artifact_status"][identity]["status"] == "stale"
                                       for identity in historical_ids))
        require("corrected-result-is-current",
                state["artifact_status"][new_step["result_id"]]["status"] == "current")
        require("amendment-does-not-rewrite-prior-records",
                call(corrected, "bundle.get", bundle_id=old_summary["bundle_id"]) == old_bundle
                and call(corrected, "source.get", source_id=old_source["source_id"]) == original_source)
        existing._write(output / "corrected-bundle.json", new_bundle)
        existing._write(output / "comparison.json", {"before": before, "after": after})
        corrected_file = corrected.save_workspace(output / "corrected/workspace.json")
        corrected_snapshot = existing._snapshot(output / "corrected")
        with _readonly_guard():
            replay_session = Session.from_workspace(corrected_file, output / "replay")
        replay = call(replay_session, "bundle.replay", bundle_id=new_summary["bundle_id"])
        replay_bundle = call(replay_session, "bundle.get", bundle_id=replay["bundle"]["bundle_id"])
        replay_step = replay_bundle["steps"][0]
        require("replay-reproduces-content-with-fresh-occurrences",
                replay_step["numerical_result"] == new_step["numerical_result"]
                and replay_step["numerical_result_id"] == new_step["numerical_result_id"]
                and replay_step["execution_id"] != new_step["execution_id"]
                and replay_step["result_id"] != new_step["result_id"]
                and replay_bundle["bundle_digest"] != new_bundle["bundle_digest"])
        require("replay-receipt-binds-original-and-fresh-bundles",
                replay["replay_receipt"]["source_bundle_digest"] == new_summary["bundle_id"]
                and replay["replay_receipt"]["replayed_bundle_digest"] == replay["bundle"]["bundle_id"]
                and replay["replay_receipt"]["numerical_match"] is True
                and replay["replay_receipt"]["admission"] == "not_performed")
        for name, bundle in (("original", old_bundle), ("corrected", new_bundle), ("replayed", replay_bundle)):
            authority = bundle["verification"]["authority"]
            require(name + "-verification-has-no-admission-or-physical-authority",
                    bundle["verification"]["outcome"] == "passed"
                    and bundle["verification"]["independent"] is False
                    and authority["state_admission"] == "not_performed"
                    and authority["physical_validation"] in {"not_performed", "not_established"},
                    authority)
        existing._write(output / "replayed-bundle.json", replay_bundle)
        replay_file = replay_session.save_workspace(output / "replay/workspace.json")
        replay_snapshot = existing._snapshot(output / "replay")
        with _readonly_guard():
            restored = Session.from_workspace(replay_file, output / "reopened")
            require("reopen-restores-exact-content-journal-and-eligibility",
                    restored.workbench.serialize() == replay_session.workbench.serialize()
                    and restored.correction_journal.serialize() == replay_session.correction_journal.serialize()
                    and restored.dependency_status() == replay_session.dependency_status()
                    and restored.run == replay_session.run)
        require("all-prior-stage-directories-remain-unchanged",
                existing._snapshot(output / "original") == original_snapshot
                and existing._snapshot(output / "corrected") == corrected_snapshot
                and existing._snapshot(output / "replay") == replay_snapshot)
        report.update(status="PASS", correction_id=proposal["correction_id"],
                      original=old_summary, corrected=new_summary, replay=replay,
                      comparison={"before": before, "after": after})
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["artifacts"] = existing._snapshot(output)
    existing._write(output / "report.json", report)
    return report


def qualify(output, *, expected_wheel=None, timeout=180):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Output directory already exists; choose a new path")
    if output.resolve().is_relative_to(ROOT):
        raise ValueError("Installed acceptance output must be outside the source checkout")
    if not 1 <= timeout <= 600:
        raise ValueError("Timeout must be within 1..600 seconds")
    output.mkdir(parents=True, exist_ok=False)
    q = existing.Qualification(output, timeout)
    report = {"schema": "ciw.release-operator-acceptance.v1", "status": "FAIL",
              "scope": "three_installed_scripted_synthetic_correction_journeys",
              "authority": dict(AUTHORITY), "commands": q.commands, "checks": q.checks,
              "journeys": {}, "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        provenance = q.execute("installed-provenance", ["-c", PROVENANCE])
        report["installed_package"] = provenance
        package_root = Path(provenance["package_root"]).resolve(strict=True)
        q.require("installed-package-isolated-and-not-editable",
                  provenance["isolated"] is True
                  and Path(provenance["ciw_origin"]).resolve() == package_root / "__init__.py"
                  and not package_root.is_relative_to(ROOT)
                  and (provenance.get("direct_url") or {}).get("dir_info", {}).get("editable") is not True)
        report["wheel_byte_proof"] = (existing._wheel_proof(q, Path(expected_wheel), package_root)
                                     if expected_wheel else {"status": "not_requested"})
        doctor = q.ciw("core-preflight", "doctor", "--profile", "core")
        q.require("core-preflight", doctor["status"] == "preflight_passed"
                  and doctor["qualification"] == "not_performed")
        inputs = output / "fixtures"
        inputs.mkdir()
        for name in ("encoder-original.json", "encoder-corrected.json"):
            (inputs / name).write_bytes((ROOT / "examples/corrections" / name).read_bytes())
        report["fixture_hashes"] = existing._snapshot(inputs)
        report["harness_hashes"] = {str(path.relative_to(ROOT)): existing._hash(path)
                                  for path in (Path(__file__).resolve(), ROOT / "scripts/check_operator_installed.py")}
        for kind in KINDS:
            try:
                q.execute(kind, [str(Path(__file__).resolve()), "--worker", kind,
                                 "--output-dir", str(output / kind), "--fixture-root", str(inputs)])
            except (OSError, ValueError) as exc:
                report["journeys"][kind] = {"status": "FAIL", "reason": str(exc)}
            else:
                child = existing._read(output / kind / "report.json")
                q.require(kind + "-worker-report",
                          isinstance(child, dict)
                          and child.get("schema") == "ciw.release-operator-journey.v1"
                          and child.get("kind") == kind
                          and child.get("status") == "PASS"
                          and child.get("origin") == "scripted_synthetic_fixture"
                          and child.get("authority") == AUTHORITY
                          and isinstance(child.get("checks"), list)
                          and bool(child["checks"])
                          and all(isinstance(check, dict) and check.get("status") == "PASS"
                                  for check in child["checks"]))
                report["journeys"][kind] = {"status": child["status"],
                    "report": kind + "/report.json", "sha256": existing._hash(output / kind / "report.json"),
                    "checks": len(child["checks"]), "comparison": child["comparison"]}
        q.require("three-complete-installed-journeys",
                  set(report["journeys"]) == set(KINDS)
                  and all(row["status"] == "PASS" for row in report["journeys"].values()))
        report["status"] = "PASS"
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    existing._write(output / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-wheel", type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--worker", choices=KINDS, help=argparse.SUPPRESS)
    parser.add_argument("--fixture-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        report = (run_journey(args.worker, args.output_dir, args.fixture_root) if args.worker
                  else qualify(args.output_dir, expected_wheel=args.expected_wheel, timeout=args.timeout))
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}))
        return 2
    print(json.dumps({"status": report["status"],
                      "report": str(args.output_dir.absolute() / "report.json"),
                      "authority": report["authority"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
