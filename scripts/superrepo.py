"""Operate the consolidated engineering workspace without changing package boundaries."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from monorepo import ROOT, git, load_manifest, verify_imports, verify_terminal_source
from superrepo_doctor import configured_environment

GATES = {
    "measurement": "check_monorepo.py",
    "inference": "check_monorepo_inference.py",
    "math": "check_monorepo_math.py",
    "flowstate": "check_monorepo_flowstate.py",
    "operations": "check_monorepo_operations.py",
    "surface": "check_monorepo_surface.py",
    "web": "check_monorepo_web.py",
}


def check(args):
    groups = list(dict.fromkeys(args.group or GATES))
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "notations.superrepo-qualification.v1",
        "verification_id": "verification:" + uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "terminal_revision": git(ROOT, "rev-parse", "HEAD").decode().strip(),
        "independent_verification": False,
        "admission": "not_performed",
        "groups": {},
        "status": "running",
    }
    try:
        report["terminal_source"] = verify_terminal_source(ROOT)
        if report["terminal_source"]["revision"] != report["terminal_revision"]:
            raise ValueError("The repository revision changed before qualification")
        report["imports"] = verify_imports(ROOT)
        run_output = output / report["verification_id"].split(":", 1)[1]
        run_output.mkdir(exist_ok=False)
        verification_ids = {report["verification_id"]}
        environment = configured_environment(cargo=args.cargo, node_bin=args.node_bin)
        if args.temp_root is not None:
            temp_root = args.temp_root.expanduser().resolve()
            if not temp_root.is_dir():
                raise ValueError("--temp-root must be an existing directory")
            # Python's implicit temp-directory selection can silently fall back
            # when a configured directory is unusable. Fail before execution.
            with tempfile.TemporaryFile(dir=temp_root):
                pass
            report["temp_root"] = str(temp_root)
            environment.update({name: str(temp_root) for name in ("TMPDIR", "TEMP", "TMP")})
        if args.node_bin:
            node_bin = args.node_bin.expanduser().resolve()
            if not node_bin.is_dir():
                raise ValueError("--node-bin must be an existing trusted toolchain directory")
        for group in groups:
            # Each invocation has fresh evidence paths; a previous passed
            # report cannot stand in for a child that failed to write one.
            group_output = run_output / group
            command = [sys.executable, str(ROOT / "scripts" / GATES[group]),
                       "--output-dir", str(group_output)]
            if group == "operations" and args.cargo:
                command += ["--cargo", str(args.cargo.expanduser().absolute())]
            if group == "surface" and getattr(args, "uv", None):
                command += ["--uv", str(args.uv.expanduser().resolve())]
            if group == "flowstate" and args.full_reproduction:
                command += ["--full-reproduction"]
            print("Qualifying " + group, flush=True)
            process = subprocess.run(command, cwd=ROOT, env=environment, check=False)
            evidence = group_output / "report.json"
            result = {"exit_code": process.returncode, "report": str(evidence),
                      "status": "failed"}
            report["groups"][group] = result
            if evidence.is_file():
                evidence_bytes = evidence.read_bytes()
                result["report_sha256"] = sha256(evidence_bytes).hexdigest()
                qualification = json.loads(evidence_bytes)
                if not isinstance(qualification, dict):
                    raise ValueError("Child qualification evidence must be an object: " + group)
                schema = ("notations.monorepo-gate-report.v1" if group == "measurement"
                          else "notations.monorepo-" + group + "-gate.v1")
                if qualification.get("schema") != schema:
                    raise ValueError("Unexpected child qualification schema: " + group)
                result["verification_id"] = qualification.get("verification_id")
                identity = result["verification_id"]
                if (not isinstance(identity, str) or not identity.startswith("verification:")
                        or len(identity.split(":", 1)[1]) != 32):
                    raise ValueError("Child evidence lacks a fresh verification identity: " + group)
                if uuid.UUID(hex=identity.split(":", 1)[1]).hex != identity.split(":", 1)[1] or identity in verification_ids:
                    raise ValueError("Child evidence lacks a fresh verification identity: " + group)
                verification_ids.add(identity)
                if qualification.get("terminal_revision") != report["terminal_revision"]:
                    raise ValueError("The repository revision changed during qualification")
                if "error" in qualification:
                    result["error"] = qualification["error"]
                result["terminal_source"] = qualification.get("terminal_source")
                if result["terminal_source"] != report["terminal_source"]:
                    raise ValueError("Child qualification source identity differs from the aggregate: " + group)
                if process.returncode == 0 and qualification.get("status") == "passed":
                    result["status"] = "passed"
            else:
                result["error"] = {"type": "MissingEvidence", "message": "The child did not write its qualification report"}
        report["post_execution_imports"] = verify_imports(ROOT)
        if git(ROOT, "rev-parse", "HEAD").decode().strip() != report["terminal_revision"]:
            raise ValueError("The repository revision changed during qualification")
        if verify_terminal_source(ROOT) != report["terminal_source"]:
            raise ValueError("Terminal source identity changed during qualification")
        report["status"] = "passed" if all(r["status"] == "passed" for r in report["groups"].values()) else "failed"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("Superrepo qualification " + report["status"] + ": " + str(output / "report.json"))
    return 0 if report["status"] == "passed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    inventory = subcommands.add_parser("list", help="List original package identities and execution pins")
    inventory.add_argument("--json", action="store_true")
    subcommands.add_parser("audit", help="Verify original histories, source bytes, licenses and execution bindings")
    preflight = subcommands.add_parser("doctor", help="Inspect source and toolchain prerequisites without running qualification")
    preflight.add_argument("--group", choices=list(GATES), action="append")
    preflight.add_argument("--cargo", type=Path, help="Trusted Cargo executable; its directory supplies compiler and Rustdoc")
    preflight.add_argument("--node-bin", type=Path, help="Trusted directory containing Node 24 or newer and npm")
    preflight.add_argument("--uv", type=Path, help="Trusted uv executable for Surface's locked lane")
    preflight.add_argument("--json", action="store_true")
    preparation = subcommands.add_parser("module-prepare", help="Prepare a source-audited candidate from a clean committed one-module draft")
    preparation.add_argument("--role", required=True)
    preparation.add_argument("--base", required=True, help="Full baseline commit identity from before editing the module")
    preparation.add_argument("--branch", required=True, help="Absent review branch to create when the plan is applied")
    preparation.add_argument("--output-plan", type=Path, required=True)
    publication = subcommands.add_parser("module-apply", help="Revalidate a prepared candidate and create its new review branch")
    publication.add_argument("--plan", type=Path, required=True)
    qualification = subcommands.add_parser("check", help="Run isolated original-package and composed-workflow gates")
    qualification.add_argument("--group", choices=list(GATES), action="append", help="Select lanes; repeat to combine. Default: all")
    qualification.add_argument("--output-dir", type=Path, default=ROOT / "results/superrepo")
    qualification.add_argument("--temp-root", type=Path, help="Existing directory for child gates' temporary files and worktrees")
    qualification.add_argument("--cargo", type=Path, help="Trusted Cargo executable for the operations lane")
    qualification.add_argument("--node-bin", type=Path, help="Trusted directory containing Node 24 and npm")
    qualification.add_argument("--uv", type=Path, help="Trusted uv executable for Surface's locked lane")
    qualification.add_argument("--full-reproduction", action="store_true", help="Include original slow FlowState reproductions")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args)
    if args.command == "doctor":
        from superrepo_doctor import doctor
        return doctor(args)
    if args.command in {"module-prepare", "module-apply"}:
        from module_update import apply, prepare
        try:
            result = (prepare(ROOT, args.role, args.base, args.branch, args.output_plan)
                      if args.command == "module-prepare" else apply(ROOT, args.plan))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print(json.dumps({"status": "refused", "error": {"type": type(error).__name__, "message": str(error)}}, indent=2))
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if (args.command == "module-prepare" or
                     result["status"] == "published" and "publication_error" not in result) else 1
    if args.command == "audit":
        print(json.dumps({"schema": "notations.monorepo-audit.v1", "imports": verify_imports(ROOT)}, indent=2))
        return 0
    modules = load_manifest(ROOT)["modules"]
    if args.json:
        print(json.dumps(modules, indent=2))
    else:
        print("ROLE\tPACKAGE\tVERSION\tBUILD\tPATH\tNET RUNTIME")
        for module in modules:
            print("\t".join(str(value) for value in (module["role"], module["distribution"], module["version"],
                module.get("build_kind", "python"), module["path"], module["runtime_revision"] or "undeclared")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
