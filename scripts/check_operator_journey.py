"""Validate the installed operator journey outside its source checkout.

Run this with a regular wheel installation, not an editable install. Every
instrument subprocess uses Python's isolated mode. The retained report covers
bounded synthetic workflows and local Session transport, not physical
validation, hardware use or qualification of every imported provider.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


SOURCE = Path(__file__).resolve().parents[1]


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_identity(root: Path) -> dict:
    def git(*arguments):
        return subprocess.check_output(["git", "--no-replace-objects", "-C", str(root), *arguments],
                                       text=True, timeout=30).strip()
    return {"root": str(root), "revision": git("rev-parse", "HEAD"),
            "source_tree": git("rev-parse", "HEAD^{tree}"),
            "tracked_changes_present": bool(git("status", "--porcelain", "--untracked-files=no"))}


class Journey:
    def __init__(self, python: Path, output: Path):
        self.python = python.expanduser().absolute()
        require(self.python.is_file(), "Installed Python executable is unavailable")
        self.output = output.resolve()
        require(SOURCE not in self.output.parents and self.output != SOURCE,
                "Installed checks must execute outside the source checkout")
        self.output.mkdir(parents=True, exist_ok=False)
        self.logs = self.output / "commands"
        self.logs.mkdir()
        self.environment = dict(os.environ)
        # The subprocesses also use -I. Removing these makes provider child
        # processes inherit no source-tree Python path from the invoking shell.
        self.environment.pop("PYTHONPATH", None)
        self.environment.pop("PYTHONHOME", None)
        self.environment["PYTHONUNBUFFERED"] = "1"
        temporary = self.output / "temporary"
        temporary.mkdir()
        self.environment.update(TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary))
        self.report = {"schema": "ciw.installed-operator-check.v1", "status": "failed",
                       "scope": "installed_bounded_synthetic_operator_journey",
                       "physical_validation": "not_established", "state_admission": "not_performed",
                       "hardware_actuation": "not_performed", "all_provider_qualification": "not_performed",
                       "commands": [], "checks": {}}
        self.processes: list[subprocess.Popen] = []
        self.streams = []

    def invoke(self, module: str | None, arguments: list[str], *, expected: int | tuple[int, ...] = 0,
               timeout: float = 60, json_output: bool = True):
        command = [str(self.python), "-I", "-B", "-m", module, *arguments] if module else [str(self.python), "-I", "-B", *arguments]
        index = len(self.report["commands"]) + 1
        prefix = self.logs / f"{index:03d}"
        row = {"argv": command, "cwd": str(self.output), "expected_exit": expected,
               "stdout": str(prefix.with_suffix(".stdout.txt")),
               "stderr": str(prefix.with_suffix(".stderr.txt"))}
        self.report["commands"].append(row)
        try:
            result = subprocess.run(command, cwd=self.output, env=self.environment, text=True,
                                    encoding="utf-8", errors="replace", capture_output=True,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            row["timed_out"] = True
            prefix.with_suffix(".stdout.txt").write_bytes(exc.stdout or b"")
            prefix.with_suffix(".stderr.txt").write_bytes(exc.stderr or b"")
            raise
        prefix.with_suffix(".stdout.txt").write_text(result.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
        row["exit_code"] = result.returncode
        acceptable = (expected,) if isinstance(expected, int) else expected
        require(result.returncode in acceptable,
                f"Command returned {result.returncode}, expected {expected}: {command}; "
                f"{(result.stderr or result.stdout)[-4000:]}")
        if not json_output:
            return result
        return json.loads(result.stdout if result.stdout.strip() else result.stderr)

    def net(self, *arguments, **options):
        return self.invoke("ciw.net", list(map(str, arguments)), **options)

    def ciw(self, *arguments, **options):
        return self.invoke("ciw", list(map(str, arguments)), **options)

    def send(self, kind: str, url: str, payload: dict | None = None) -> dict:
        value = self.ciw("send", kind, "--url", url, "--payload", json.dumps(payload or {}))
        require(value.get("type") == "response", f"Session refused {kind}: {value}")
        return value["payload"]

    def check_artifact(self, manifest: Path, expected_sha256: str, revision: str, *, final: bool = False) -> None:
        """Bind this installed journey to the single wheel selected by the build job."""
        source = source_identity(SOURCE)
        require(source["revision"] == revision, "Harness checkout differs from the candidate revision")
        require(source["tracked_changes_present"] is False, "Harness checkout has tracked changes")
        value = self.invoke(None, [str(SOURCE / "scripts/operator_artifact.py"), "verify",
            "--wheel-dir", str(manifest.parent), "--manifest", str(manifest),
            "--revision", revision, "--sha256", expected_sha256,
            "--installed", "--source-root", str(SOURCE)])
        require(value["status"] == "passed" and value["installed_package_checked"] is True,
                "Exact installed artifact check did not pass")
        self.report["artifact_after" if final else "artifact"] = value

    def check_installation(self) -> None:
        probe = """import importlib.metadata as m, json, pathlib, platform, sys, ciw
d = m.distribution('computational-instrumentation-workbench')
direct = d.read_text('direct_url.json')
print(json.dumps({'module': str(pathlib.Path(ciw.__file__).resolve()),
 'distribution_module': str(pathlib.Path(d.locate_file('ciw/__init__.py')).resolve()),
 'version': d.version, 'direct_url': json.loads(direct) if direct else None,
 'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
 'isolated': sys.flags.isolated}))"""
        value = self.invoke(None, ["-c", probe])
        module = Path(value["module"])
        require(value["isolated"] == 1, "Installed probes must run in isolated mode")
        require(module == Path(value["distribution_module"]), "Imported CIW differs from the installed distribution")
        require(SOURCE not in module.parents and SOURCE not in self.output.parents,
                "Installed checks must execute outside the source checkout")
        require(not (value["direct_url"] or {}).get("dir_info", {}).get("editable", False),
                "Use a regular wheel installation, not an editable install")
        self.report["installation"] = value
        self.report["source"] = source_identity(SOURCE)
        doctor = self.net("doctor", "--profile", "core")
        require(doctor["status"] == "preflight_passed", "Installed core dependencies failed preflight")
        require(all(row["status"] == "passed" for row in doctor["checks"]), "A core dependency check failed")
        self.report["checks"]["core_doctor"] = doctor

    def check_cold_start(self) -> None:
        # Expose only the installed CIW package through its own package path.
        # Adding its entire site-packages directory would also expose NumPy,
        # defeating the missing-dependency check that -S is intended to make.
        module = self.report["installation"]["module"]
        bootstrap = f"""import importlib.util, pathlib, sys
path = pathlib.Path({module!r})
spec = importlib.util.spec_from_file_location('ciw', path, submodule_search_locations=[str(path.parent)])
package = importlib.util.module_from_spec(spec)
sys.modules['ciw'] = package
spec.loader.exec_module(package)
from ciw.net import main
code = main()
if 'numpy' in sys.modules or 'websockets' in sys.modules:
    raise RuntimeError('Blocked setup imported scientific dependencies')
raise SystemExit(code)"""
        directory = self.output / "blocked-first-use"
        summary = self.invoke(None, ["-S", "-c", bootstrap, "start", "--output-dir", str(directory)], expected=2)
        receipt = read(directory / "readiness.json")
        require(summary["status"] == receipt["status"] == "failed"
                and receipt["workflows"] == {}
                and receipt["checks"][0]["check"] == "core_preflight"
                and receipt["checks"][0]["status"] == "failed", "Cold setup did not retain a blocked preflight receipt")
        preflight = read(directory / "preflight.json")
        require(preflight["status"] == "blocked" and "dependency_unavailable" in preflight["classifications"],
                "Cold setup did not classify missing dependencies")
        require("net doctor" in summary["failure"]["reason"], "Cold setup omitted its actionable doctor instruction")
        self.report["checks"]["cold_start"] = {"status": "passed", "receipt": str(directory / "readiness.json"),
            "missing_dependencies_classified": True, "scientific_execution_performed": False}

    def check_first_use(self) -> dict:
        catalog = self.net("catalog", "--json")
        require(catalog["read_only"] is True and catalog["authorizes_execution"] is False,
                "Catalog must remain read-only navigation")
        names = {row["command"] for row in catalog["commands"]}
        require(len(names) == len(catalog["commands"]), "Catalog contains duplicate commands")
        require({"net catalog", "net doctor", "net start", "net workbench", "net provision",
                 "net science", "net atmosphere", "net dsp", "net board", "net compose"} <= names,
                "Installed catalog omits operator or specialist entry points")
        workflows = {row["workflow"]: row for row in catalog["workflows"]}
        require(len(workflows) == len(catalog["workflows"]), "Catalog contains duplicate workflows")
        require({"thermal-observer", "residual-monitor", "curved-path-transfer", "calibrated-observable"} <= workflows.keys(),
                "Installed catalog omits declared scientific workflows")
        self.report["checks"]["catalog"] = {"commands": sorted(names), "workflow_count": len(workflows),
                                               "workflow_operation_ids": sorted(row["operation_id"] for row in workflows.values())}
        first = self.output / "first-use"
        summary = self.net("start", "--output-dir", first, timeout=180)
        receipt_path = first / "readiness.json"
        require(summary["status"] == "completed" and summary["receipt_sha256"] == digest(receipt_path),
                "First-use completion receipt is missing or has the wrong digest")
        receipt = read(receipt_path)
        require(receipt["status"] == "completed" and receipt["authority"]["scope"] == "bounded_synthetic_core_workflows",
                "First use did not complete the bounded core workflows")
        require(receipt["authority"]["external_provider_qualification"] == "not_performed"
                and receipt["authority"]["physical_validation"] == "not_established"
                and receipt["authority"]["state_admission"] == "not_performed",
                "First-use receipt expanded its authority")
        require(all(row["status"] == "passed" for row in receipt["checks"]), "A first-use step failed")
        for artifact in receipt["artifacts"]:
            path = (first / artifact["path"]).resolve()
            require(first in path.parents, "Artifact escaped its retained first-use directory")
            require(path.stat().st_size == artifact["byte_count"] and digest(path) == artifact["sha256"],
                    "Retained first-use artifact differs from its receipt: " + artifact["path"])
        thermal = receipt["workflows"]["thermal"]
        require(thermal["checks"]["replay-comparison"]["status"] == "PASS", "Thermal posterior replay is not exact")
        for original, replay in (("source_bundle_id", "replayed_bundle_id"),
                                 ("original_execution_id", "replay_execution_id"),
                                 ("original_result_id", "replay_result_id")):
            require(thermal[original] != thermal[replay], "Thermal replay reused a retained occurrence identity")
        require(thermal["verification"]["outcome"] == "passed" and thermal["verification"]["independent"] is False,
                "Thermal reproduction verification scope changed")
        inspected = read(first / "atmosphere/inspection.json")
        verified = read(first / "atmosphere/verification-report.json")
        require(inspected["status"] == verified["status"] == "LOCAL"
                and inspected["fresh_numerical_verification"] is False
                and verified["fresh_numerical_verification"] is True
                and verified["fresh_execution"] is False, "Atmosphere inspection/recheck semantics changed")
        require(bool(verified["checks"]) and all(row["status"] == "PASS" for row in verified["checks"]),
                "Independent atmosphere checks did not pass")
        for identity in ("evidence_id", "execution_id", "result_id", "verification_execution_id", "verification_id"):
            require(inspected[identity] == verified[identity], "Atmosphere recheck changed retained identities")
        before = {row["path"]: digest(first / row["path"]) for row in receipt["artifacts"]}
        refusal = self.net("start", "--output-dir", first, expected=1)
        require(refusal["status"] == "refused", "Existing first-use output was accepted")
        require(digest(receipt_path) == summary["receipt_sha256"]
                and before == {path: digest(first / path) for path in before}, "Refused first use changed retained artifacts")
        self.report["checks"]["first_use"] = {"receipt": str(receipt_path), "receipt_sha256": digest(receipt_path),
            "thermal": thermal, "atmosphere_check_count": len(verified["checks"]),
            "existing_output_refused_without_change": True}
        return catalog

    def launch(self, directory: Path, port: int) -> subprocess.Popen:
        number = len(self.processes) + 1
        stream = (self.output / f"workbench-{number}.log").open("w", encoding="utf-8")
        self.streams.append(stream)
        process = subprocess.Popen([str(self.python), "-I", "-B", "-u", "-m", "ciw.net", "workbench",
                                    "--output-dir", str(directory), "--port", str(port), "--bind", "127.0.0.1"],
                                   cwd=self.output, env=self.environment, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process

    @staticmethod
    def stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def await_health(self, process: subprocess.Popen, url: str) -> dict:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            require(process.poll() is None, "Owned Workbench process exited before becoming healthy; inspect its log")
            try:
                return self.ciw("health", "--url", url, timeout=8)
            except AssertionError:
                time.sleep(0.1)
        raise AssertionError("Owned Workbench process did not become healthy within 30 seconds")

    def check_workbench(self, catalog: dict) -> None:
        directory = self.output / "workbench"
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        url = f"ws://127.0.0.1:{port}"
        process = self.launch(directory, port)
        health = self.await_health(process, url)
        require(health["status"] == "healthy", "Live Workbench health failed")
        session = self.send("session.get", url)
        operations = self.send("operation.list", url)["operations"]
        declared = {row["operation_id"] for row in operations if "source_kind" in row}
        require(declared == {row["operation_id"] for row in catalog["workflows"]},
                "Unified catalog does not cover every live Session workflow")
        completed = self.send("operation.execute", url, {"operation_id": "statistics.v1", "parameters": {}})
        require(completed["status"] == "completed", "Live Session statistics operation failed")
        result = completed["result"]
        require(result["execution_id"] == completed["execution"]["execution_id"], "Live result lost its execution link")
        require(result["result_id"] in {row["result_id"] for row in self.send("result.list", url)["results"]},
                "Completed live result is absent from the result catalog")
        saved = self.send("workspace.save", url)
        path = Path(saved["workspace_file"])
        original = read(path)
        self.stop(process)
        restarted = self.launch(directory, port)
        self.await_health(restarted, url)
        reopened = self.send("session.get", url)
        require(reopened["run"]["evidence_id"] == session["run"]["evidence_id"], "Resume replaced retained evidence")
        require(self.send("result.get", url, {"result_id": result["result_id"]}) == result,
                "Resume changed a retained live result")
        self.send("workspace.save", url)
        restored = read(path)
        for key in ("run", "selection", "results", "executions"):
            require(restored[key] == original[key], "Resume changed retained workspace " + key)
        self.stop(restarted)
        self.report["checks"]["workbench"] = {"status": "passed", "url": url,
            "workspace": str(path), "source_evidence_id": session["run"]["evidence_id"],
            "result_id": result["result_id"], "execution_id": result["execution_id"],
            "workflow_operation_count": len(declared), "restart_preserved_records_without_reexecution": True}

    def check_public_provider(self, monorepo: Path) -> None:
        monorepo = monorepo.resolve(strict=True)
        require(monorepo not in Path(self.report["installation"]["module"]).parents,
                "The imported installed package comes from the provisioning source")
        plan = self.net("provision", "--plan", "--monorepo", monorepo, "--workflow", "residual-monitor", expected=(0, 1))
        require(plan["read_only"] is True and plan["qualification"] == "not_performed",
                "Public provider planning changed its authority")
        providers = self.output / "public-providers"
        provisioned = self.net("provision", "--monorepo", monorepo, "--workflow", "curved-path-transfer",
                               "--output-dir", providers, timeout=180)
        require(provisioned["status"] == "provisioned", "Public curved-path provider was not provisioned")
        source = self.output / "curved-source.json"
        raw = subprocess.check_output(["git", "--no-replace-objects", "-C", str(monorepo), "show",
            provisioned["plan"]["source"]["revision"] + ":examples/curved-path-study/baseline.json"], timeout=30)
        source.write_bytes(raw)
        bindings = providers / "bindings.json"
        context = self.output / "first-use/oscillator/workspace.json"
        context_digest = digest(context)
        native = self.output / "curved-original"
        selected = self.net("science", "run", "--workspace", context, "--kind", "curved-path-transfer",
            "--source", source, "--label", "existing synthetic curved-path baseline", "--bindings-file", bindings,
            "--output-dir", native, "--json", timeout=180)
        require(selected["validation"] == "content_consistent" and selected["retained_verification_outcome"] == "passed",
                "Provisioned public workflow did not retain a passing reproduction")
        workspace = native / "workspace.json"
        before = digest(workspace)
        replay = self.net("science", "replay", "--workspace", workspace, "--bundle", selected["bundle_id"],
            "--bindings-file", bindings, "--output-dir", self.output / "curved-replay", "--json", timeout=180)
        fresh = replay["bundle"]
        require(replay["replay_receipt"]["numerical_match"] is True, "Public curved-path replay did not numerically match")
        require(selected["bundle_id"] != fresh["bundle_id"]
                and set(selected["execution_ids"]).isdisjoint(fresh["execution_ids"])
                and set(selected["result_ids"]).isdisjoint(fresh["result_ids"]),
                "Public workflow replay reused native occurrence identities")
        require(before == digest(workspace) and context_digest == digest(context), "Public workflow changed an input workspace")
        self.report["checks"]["public_provider"] = {"status": "passed", "workflow": "curved-path-transfer",
            "source": provisioned["plan"]["source"], "source_sha256": digest(source),
            "providers": provisioned["plan"]["providers"], "bindings_file": str(bindings),
            "original": selected, "replay": replay, "residual_monitor_plan": plan,
            "scope": "existing_declared_synthetic_case_and_same_runtime_reproduction"}

    def finish(self) -> None:
        for process in self.processes:
            self.stop(process)
        for stream in self.streams:
            stream.close()
        write(self.output / "qualification.json", self.report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="Python from a regular installed wheel environment")
    parser.add_argument("--output-dir", type=Path, required=True, help="New retained report directory outside the checkout")
    parser.add_argument("--monorepo", type=Path, help="Explicit local retained Git objects for public provider activation; no fetch")
    parser.add_argument("--wheel-manifest", type=Path, help="Manifest for the one retained candidate wheel")
    parser.add_argument("--wheel-sha256", help="Expected wheel digest supplied independently of the manifest")
    parser.add_argument("--candidate-revision", help="Exact checkout revision used to build the candidate")
    args = parser.parse_args()
    artifact_arguments = (args.wheel_manifest, args.wheel_sha256, args.candidate_revision)
    if any(value is not None for value in artifact_arguments) and not all(value is not None for value in artifact_arguments):
        parser.error("wheel-manifest, wheel-sha256 and candidate-revision must be supplied together")
    if args.wheel_manifest is not None:
        args.wheel_manifest = args.wheel_manifest.resolve(strict=True)
    journey = Journey(args.python, args.output_dir)
    try:
        if args.wheel_manifest is not None:
            journey.check_artifact(args.wheel_manifest, args.wheel_sha256, args.candidate_revision)
        else:
            journey.report["artifact"] = {"status": "not_requested", "qualification": "not_performed"}
        journey.check_installation()
        journey.check_cold_start()
        catalog = journey.check_first_use()
        journey.check_workbench(catalog)
        if args.monorepo is not None:
            journey.check_public_provider(args.monorepo)
        else:
            journey.report["checks"]["public_provider"] = {"status": "not_requested", "qualification": "not_performed"}
        if args.wheel_manifest is not None:
            journey.check_artifact(args.wheel_manifest, args.wheel_sha256, args.candidate_revision, final=True)
        journey.report["status"] = "passed"
    except Exception as exc:
        journey.report["failure"] = {"error_type": type(exc).__name__, "reason": str(exc)}
    finally:
        journey.finish()
    print(json.dumps({"status": journey.report["status"], "report": str(journey.output / "qualification.json"),
                      **({"failure": journey.report["failure"]} if "failure" in journey.report else {})}, indent=2))
    return 0 if journey.report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
