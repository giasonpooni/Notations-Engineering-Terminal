"""Exercise real correction/replay journeys and evidence-preserving refusals.

These are source-suite tests of the harness. Installed-package qualification is
performed separately by check_release_operator.py with a regular wheel.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_operator_under_test", ROOT / "scripts/check_release_operator.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
FIXTURES = ROOT / "examples/corrections"


class ReleaseOperatorJourneys(unittest.TestCase):
    def test_three_real_journeys_retain_correction_replay_and_exact_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            for kind in gate.KINDS:
                with self.subTest(kind=kind):
                    output = Path(directory) / kind
                    report = gate.run_journey(kind, output, FIXTURES)
                    self.assertEqual(report["status"], "PASS", report.get("failure"))
                    self.assertGreaterEqual(len(report["checks"]), 20)
                    self.assertTrue(all(row["status"] == "PASS" for row in report["checks"]))
                    self.assertEqual(report["authority"], gate.AUTHORITY)
                    self.assertEqual(report["origin"], "scripted_synthetic_fixture")
                    self.assertEqual(json.loads((output / "report.json").read_text()), report)
                    self.assertNotEqual(report["original"]["bundle_id"], report["corrected"]["bundle_id"])
                    self.assertNotEqual(report["corrected"]["bundle_id"], report["replay"]["bundle"]["bundle_id"])
                    before, after = report["comparison"]["before"], report["comparison"]["after"]
                    self.assertGreater(before["value"], before["limit"])
                    self.assertLessEqual(after["value"], after["limit"])
                    self.assertLess(after["value"], before["value"])
                    actions = [row["type"] for row in report["requests"]]
                    for action in ("source.add", "operation.execute", "experiment.inspect", "claim.add",
                                   "correction.propose", "correction.review", "dependency.inspect", "bundle.replay"):
                        self.assertIn(action, actions)
                    for path, digest in report["artifacts"].items():
                        self.assertEqual(gate.existing._hash(output / path), digest)
                    for stage in ("original", "corrected", "replay"):
                        self.assertTrue((output / stage / "workspace.json").is_file())

    def test_existing_journey_output_is_refused_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "retained"
            output.mkdir()
            sentinel = output / "original.json"
            sentinel.write_bytes(b'{"retained":true}\n')
            before = gate.existing._snapshot(output)
            with self.assertRaisesRegex(ValueError, "new journey directory"):
                gate.run_journey("project-graph", output, FIXTURES)
            self.assertEqual(gate.existing._snapshot(output), before)

    def test_malformed_source_retains_failed_report_and_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "malformed"
            with mock.patch.object(gate, "sources", return_value=(b"{}", b'{"changed":true}')):
                report = gate.run_journey("project-graph", output, FIXTURES)
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("source.add refused", report["failure"]["reason"])
            self.assertEqual((output / "original-source.json").read_bytes(), b"{}")
            self.assertTrue((output / "report.json").is_file())
            self.assertEqual(report["requests"][0]["type"], "source.add")

    def test_unchanged_source_cannot_masquerade_as_a_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unchanged"
            with mock.patch.object(gate, "sources", return_value=(b"{}", b"{}")):
                report = gate.run_journey("project-graph", output, FIXTURES)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failure"]["reason"], "source-amendment-is-explicit")
            self.assertEqual(report["requests"], [])

    def test_false_replay_receipt_fails_acceptance_and_preserves_original(self):
        from ciw.workbench import Workbench
        real = Workbench.replay

        def false_receipt(workbench, payload):
            result = real(workbench, payload)
            result = deepcopy(result)
            result["replay_receipt"]["numerical_match"] = False
            return result

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "false-replay"
            with mock.patch.object(Workbench, "replay", false_receipt):
                report = gate.run_journey("project-graph", output, FIXTURES)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failure"]["reason"], "replay-receipt-binds-original-and-fresh-bundles")
            self.assertTrue((output / "original/workspace.json").is_file())
            self.assertTrue((output / "corrected/workspace.json").is_file())
            self.assertTrue((output / "report.json").is_file())

    def test_journal_actions_refuse_incidental_session_execution(self):
        from ciw.session import Session
        real_handle = Session.handle
        for action in ("correction.propose", "correction.review"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                attempted, completed = [], []
                def inject_execution(session, request):
                    if request["type"] == action:
                        attempted.append(action)
                        response = real_handle(session, {
                            "protocol_version": 1, "request_id": "unexpected-journal-execution",
                            "type": "operation.execute",
                            "payload": {"operation_id": "statistics.v1", "parameters": {}},
                        })
                        self.assertEqual(response["type"], "response")
                        completed.append(response)
                    return real_handle(session, request)
                output = Path(directory) / "unexpected-execution"
                with mock.patch.object(Session, "handle", inject_execution):
                    report = gate.run_journey("project-graph", output, FIXTURES)
                self.assertEqual(attempted, [action])
                self.assertEqual(completed, [])
                self.assertEqual(report["status"], "FAIL")
                self.assertIn("Read-only inspection executed a Session operation",
                              report["failure"]["reason"])
                self.assertTrue((output / "original/workspace.json").is_file())
                self.assertEqual(json.loads((output / "report.json").read_text()), report)

    def test_readonly_guard_blocks_real_execution_entrypoints(self):
        from ciw import machine_workflow, thermal_workflow, project_workflow
        classes = (machine_workflow.MachineManifestWorkflow,
                   thermal_workflow.ThermalWorkflow, project_workflow.ProjectGraphWorkflow)
        with gate._readonly_guard():
            for cls in classes:
                for method in ("create_session", "replay_session"):
                    with self.subTest(cls=cls.__name__, method=method):
                        with self.assertRaisesRegex(AssertionError, "Read-only"):
                            getattr(cls(), method)(b"{}", {})

    def test_readonly_guard_blocks_session_bound_execution_alias(self):
        from ciw.instruments import make_demo_run
        from ciw.session import Session
        with tempfile.TemporaryDirectory() as directory:
            session = Session(make_demo_run(), Path(directory) / "session")
            executions = deepcopy(session.executions)
            results = deepcopy(session.results)
            with gate._readonly_guard(), self.assertRaisesRegex(AssertionError, "Session operation"):
                session.handle({"protocol_version": 1, "request_id": "readonly-alias-probe",
                                "type": "operation.execute",
                                "payload": {"operation_id": "statistics.v1", "parameters": {}}})
            self.assertEqual(session.executions, executions)
            self.assertEqual(session.results, results)

    def test_fixture_sources_validate_under_existing_workflow_contracts(self):
        from ciw import machine_workflow, thermal_workflow, project_workflow
        validators = {
            "machine-manifest": machine_workflow.validate_source,
            "thermal-observer": thermal_workflow.contract.validate_source,
            "project-graph": project_workflow.validate_source,
        }
        for kind, validator in validators.items():
            with self.subTest(kind=kind):
                old, corrected = gate.sources(kind, FIXTURES)
                self.assertNotEqual(old, corrected)
                validator(old)
                validator(corrected)

    def test_project_fixture_repairs_explicit_historical_input_pins(self):
        from ciw import project_model
        old, corrected = map(json.loads, gate.sources("project-graph", FIXTURES))
        before = project_model.inspect(old["project"])
        after = project_model.inspect(corrected["project"])
        self.assertEqual(next(row for row in before["objects"] if row["kind"] == "result")["result_status"],
                         "needs_reevaluation")
        self.assertEqual(next(row for row in after["objects"] if row["kind"] == "result")["result_status"],
                         "current_for_declared_inputs")
        self.assertEqual(old["project"]["history"], corrected["project"]["history"][:-1])
        self.assertEqual(after["authority"]["execution"], "not_performed")

    def test_thermal_amendment_preserves_coherent_synthetic_evidence(self):
        old, corrected = map(json.loads, gate.sources("thermal-observer", FIXTURES))
        self.assertEqual(old["request"]["observations"][0][0] - corrected["request"]["observations"][0][0], 5.0)
        self.assertEqual(old["evaluation"]["measurement_noise"][0][0]
                         - corrected["evaluation"]["measurement_noise"][0][0], 5.0)
        self.assertEqual(old["evaluation"]["states"], corrected["evaluation"]["states"])
        self.assertEqual(corrected["evaluation"]["origin"], "synthetic_fixture")

    def test_unsupported_journey_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            gate.sources("unbound-private-provider", FIXTURES)


class InstalledAcceptancePreconditions(unittest.TestCase):
    def test_existing_installed_output_is_refused_before_child_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            sentinel = output / "evidence.json"
            sentinel.write_bytes(b"retained")
            with mock.patch.object(gate.existing.Qualification, "execute",
                                   side_effect=AssertionError("preflight launched child")):
                with self.assertRaisesRegex(ValueError, "already exists"):
                    gate.qualify(output)
            self.assertEqual(sentinel.read_bytes(), b"retained")

    def test_source_checkout_output_is_refused_before_creation(self):
        output = ROOT / "results" / "release-operator-precondition-test"
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, "outside the source"):
            gate.qualify(output)
        self.assertFalse(output.exists())

    def test_unbounded_or_nonfinite_timeout_is_refused(self):
        for timeout in (0, -1, 601, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "acceptance"
                with self.assertRaisesRegex(ValueError, "Timeout"):
                    gate.qualify(output, timeout=timeout)
                self.assertFalse(output.exists())


class InstalledWorkerReportValidation(unittest.TestCase):
    def qualify_with_worker_change(self, directory, changes):
        package = Path(directory) / "installed" / "ciw"
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"# test distribution\n")
        output = Path(directory) / "acceptance"
        def execute(qualification, name, arguments, expected=0):
            if name == "installed-provenance":
                return {"isolated": True, "package_root": str(package),
                        "ciw_origin": str(package / "__init__.py"), "direct_url": None}
            if name == "core-preflight":
                return {"status": "preflight_passed", "qualification": "not_performed"}
            self.assertIn(name, gate.KINDS)
            # Isolate receipt validation; the real three-journey test remains mandatory.
            child = {"schema": "ciw.release-operator-journey.v1", "kind": name,
                     "status": "PASS", "origin": "scripted_synthetic_fixture",
                     "authority": dict(gate.AUTHORITY),
                     "checks": [{"check": "fixture-check", "status": "PASS"}],
                     "comparison": {"before": {"value": 2, "limit": 1},
                                    "after": {"value": 0, "limit": 1}}}
            if name == gate.KINDS[0]:
                child.update(deepcopy(changes))
            destination = output / name
            destination.mkdir()
            (destination / "report.json").write_text(json.dumps(child), encoding="utf-8")
            return {"status": "PASS", "authority": dict(gate.AUTHORITY),
                    "report": str(destination / "report.json")}
        with mock.patch.object(gate.existing.Qualification, "execute", execute):
            return gate.qualify(output)

    def test_matching_worker_reports_pass_the_parent_receipt_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.qualify_with_worker_change(directory, {})
        self.assertEqual(report["status"], "PASS", report.get("failure"))
        self.assertEqual(set(report["journeys"]), set(gate.KINDS))

    def test_mismatched_or_incomplete_worker_reports_fail_acceptance(self):
        mutations = (
            {"kind": "project-graph"}, {"schema": "different-worker.v1"},
            {"origin": "physical_measurement"},
            {"authority": {**gate.AUTHORITY, "state_admission": "performed"}},
            {"checks": []},
            {"checks": [{"check": "failed-worker-check", "status": "FAIL"}]},
            {"checks": [{}]},
        )
        for changes in mutations:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                report = self.qualify_with_worker_change(directory, changes)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["failure"]["reason"], gate.KINDS[0] + "-worker-report")
                self.assertNotIn(gate.KINDS[0], report["journeys"])
                self.assertEqual(report["checks"][-1]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
