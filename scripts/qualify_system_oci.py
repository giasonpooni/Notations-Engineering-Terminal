"""Qualify a real digest-selected OCI worker against retained NET references.

Build/pull steps use networking explicitly. Scientific workers run with the
existing OCI adapter's network-disabled policy. The local registry is a unique,
loopback-only fixture; no authenticated or public registry receives an image.
No unavailable Docker backend is skipped or replaced by another engine.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
BASE_TAG = "python:3.12.14-slim"
REGISTRY_TAG = "registry:2"
_DIGEST_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}\Z")


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _sha(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Commands:
    """Retain each actual argument vector, return code and separate log bytes."""

    def __init__(self, destination):
        self.destination = Path(destination)
        self.records = []
        (self.destination / "logs").mkdir(parents=True)

    def run(self, arguments, *, cwd=ROOT, timeout=600, check=True):
        command = [str(value) for value in arguments]
        index = len(self.records) + 1
        log = self.destination / "logs" / f"{index:02d}.log"
        record = {"arguments": command, "cwd": str(cwd), "timeout_s": timeout,
                  "started_at": datetime.now(timezone.utc).isoformat(), "log": str(log.relative_to(self.destination))}
        self.records.append(record)
        try:
            completed = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       timeout=timeout, check=False, shell=False)
            log.write_bytes(completed.stdout)
            record.update(returncode=completed.returncode, log_digest=_sha(log))
        except subprocess.TimeoutExpired as exc:
            log.write_bytes(exc.stdout or b"")
            record.update(returncode=None, timed_out=True, log_digest=_sha(log))
            _write(self.destination / "commands.json", self.records)
            raise RuntimeError(f"Qualification command timed out; inspect {log}") from exc
        _write(self.destination / "commands.json", self.records)
        if check and completed.returncode:
            raise RuntimeError(f"Qualification command failed ({completed.returncode}); inspect {log}")
        return completed.stdout.decode("utf-8", errors="replace")


def _inspect(commands, docker, reference):
    value = json.loads(commands.run([docker, "image", "inspect", reference], timeout=30))
    if type(value) is not list or len(value) != 1:
        raise RuntimeError("Docker image inspection did not identify exactly one image")
    return value[0]


def _repository_digest(inspection, repository=None):
    refs = inspection.get("RepoDigests", [])
    matches = [reference for reference in refs if type(reference) is str
               and _DIGEST_REFERENCE.fullmatch(reference)
               and (repository is None or reference.split("@", 1)[0] == repository)]
    if len(matches) != 1:
        raise RuntimeError("Image has no unique immutable repository digest for the selected repository")
    return matches[0]


def _registry_ready(port, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Loopback registry did not become ready")


def _declare_reduction(spec, target):
    if not any(item["target_cells"] == target for item in spec["representations"]):
        spec["representations"].append({"representation_id": f"thermal-reduced-{target}",
            "kind": "cell-average.v1", "source_node": "thermal", "target_cells": target,
            "assumptions": ["Equal-volume averaging discards subcell temperature gradients."]})
    return spec


def _probe_policy(commands, docker, image, resources, expected_runtime):
    """Inspect an actual created container and execute its packaged runtime.

    This checks a separate policy probe, not the already-removed solver
    container. Solver provenance remains its retained adapter receipt.
    """
    name = "net-system-policy-" + uuid.uuid4().hex
    code = ("import json,sys,numpy,websockets; from ciw.system_runtime import runtime_identity; "
            "print(json.dumps({'python':sys.version.split()[0],'numpy':numpy.__version__,"
            "'websockets':websockets.__version__,'runtime':runtime_identity()}))")
    command = [docker, "create", "--name", name, "--pull", "never", "--network", "none",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--pids-limit", "64", "--cpus", str(resources["cpu"]),
               "--memory", f"{resources['memory_mb']}m", "--user", f"{os.getuid()}:{os.getgid()}",
               "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--entrypoint", "python", image, "-c", code]
    commands.run(command, timeout=30)
    try:
        inspection = json.loads(commands.run([docker, "container", "inspect", name], timeout=30))[0]
        host = inspection["HostConfig"]
        expected = {"Memory": resources["memory_mb"] * 1024 * 1024,
                    "NanoCpus": resources["cpu"] * 1_000_000_000,
                    "PidsLimit": 64, "ReadonlyRootfs": True, "NetworkMode": "none"}
        if any(host.get(field) != value for field, value in expected.items()):
            raise RuntimeError("Actual policy probe container differs from requested resource/isolation policy")
        if "ALL" not in host.get("CapDrop", []) or "no-new-privileges" not in host.get("SecurityOpt", []):
            raise RuntimeError("Actual policy probe container lacks requested privilege limits")
        if inspection["Config"].get("User") != f"{os.getuid()}:{os.getgid()}":
            raise RuntimeError("Actual policy probe user differs from the invoking worker user")
        runtime = json.loads(commands.run([docker, "start", "--attach", name], timeout=60))
        if ((runtime["python"], runtime["numpy"], runtime["websockets"]) != ("3.12.14", "2.4.3", "16.0")
                or runtime["runtime"]["source_files"] != expected_runtime["source_files"]):
            raise RuntimeError("Packaged worker versions/source identity differ from the qualified local source")
        _write(commands.destination / "policy-probe.json", {"inspection": inspection, "packaged_runtime": runtime,
               "scope": "separate_created_container_with_same_resource_and_isolation_arguments"})
        return inspection["Image"]
    finally:
        commands.run([docker, "rm", "--force", name], timeout=15, check=False)


def qualify(destination):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Qualification requires an empty output directory")
    destination.mkdir(parents=True, exist_ok=True)
    commands = Commands(destination)
    _write(destination / "checks.json", {"schema": "ciw.system-oci-qualification.v1", "status": "RUNNING",
           "physical_validation_status": "not_assessed", "canonical_admission": False})
    docker = shutil.which("docker")
    registry_name = None
    try:
        if docker is None:
            raise RuntimeError("Actual OCI qualification requires Docker; no backend substitution or skip is permitted")
        if sys.version_info[:3] != (3, 12, 14) or not hasattr(os, "getuid"):
            raise RuntimeError("OCI qualification requires POSIX and Python 3.12.14")
        commands.run([docker, "info", "--format", "{{json .}}"], timeout=30)
        version = commands.run([docker, "version", "--format", "{{json .}}"], timeout=30)
        _write(destination / "docker-version.json", json.loads(version))

        # Mutable tags are only resolver inputs. Every build/runtime selection
        # below is an observed immutable digest retained in provenance.
        commands.run([docker, "pull", BASE_TAG], timeout=300)
        base = _inspect(commands, docker, BASE_TAG)
        base_reference = _repository_digest(base)
        immutable_base = BASE_TAG + "@" + base_reference.split("@", 1)[1]
        commands.run([docker, "pull", REGISTRY_TAG], timeout=300)
        registry = _inspect(commands, docker, REGISTRY_TAG)
        registry_reference = _repository_digest(registry)

        commit = commands.run(["git", "rev-parse", "HEAD"], timeout=15).strip()
        tree = commands.run(["git", "rev-parse", "HEAD^{tree}"], timeout=15).strip()
        dirty = commands.run(["git", "status", "--porcelain", "--untracked-files=all"], timeout=15)
        source_files = {str(path.relative_to(ROOT)): _sha(path)
                        for path in sorted((ROOT / "src" / "ciw").rglob("*.py"))}
        source_files.update({name: _sha(ROOT / name) for name in (
            "pyproject.toml", "containers/system-reference/Dockerfile", "scripts/qualify_system_oci.py")})
        build = destination / "build"
        wheels = build / "dist"
        wheels.mkdir(parents=True)
        commands.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                      "--wheel-dir", str(wheels), str(ROOT)], timeout=300)
        wheel_files = list(wheels.glob("*.whl"))
        if len(wheel_files) != 1:
            raise RuntimeError("Build context must contain exactly one NET wheel")
        provenance = {"schema": "ciw.system-oci-build-provenance.v1", "commit": commit, "git_tree": tree,
            "working_tree_changes": dirty.splitlines(), "source_files": source_files,
            "wheel": {"filename": wheel_files[0].name, "digest": _sha(wheel_files[0])},
            "python_base": {"resolver_tag": BASE_TAG, "observed_repo_digest": base_reference,
                            "build_reference": immutable_base, "inspection": base},
            "registry_fixture": {"resolver_tag": REGISTRY_TAG, "observed_repo_digest": registry_reference,
                                 "inspection": registry},
            "build_dependency_versions": {"numpy": "2.4.3", "websockets": "16.0"},
            "reproducible_image_bytes": "not_established"}
        _write(destination / "build-provenance.json", provenance)

        registry_name = "net-system-registry-" + uuid.uuid4().hex
        commands.run([docker, "run", "--detach", "--rm", "--pull", "never", "--name", registry_name,
                      "--publish", "127.0.0.1::5000", registry_reference], timeout=30)
        published = commands.run([docker, "port", registry_name, "5000/tcp"], timeout=15).strip()
        match = re.fullmatch(r"127\.0\.0\.1:([0-9]+)", published)
        if not match:
            raise RuntimeError("Registry fixture must publish exactly one IPv4 loopback port")
        port = int(match.group(1))
        _registry_ready(port)
        repository = f"localhost:{port}/net-system-reference"
        tag = repository + ":qualification"
        commands.run([docker, "build", "--pull=false", "--file", str(ROOT / "containers/system-reference/Dockerfile"),
                      "--build-arg", f"PYTHON_BASE={immutable_base}", "--tag", tag, str(build)], timeout=600)
        built = _inspect(commands, docker, tag)
        commands.run([docker, "push", tag], timeout=300)
        commands.run([docker, "pull", tag], timeout=300)
        resolved = _inspect(commands, docker, tag)
        image_reference = _repository_digest(resolved, repository)
        commands.run([docker, "pull", image_reference], timeout=300)
        selected = _inspect(commands, docker, image_reference)
        if selected["Id"] != built["Id"]:
            raise RuntimeError("Registry-selected immutable worker differs from the actual build image")
        provenance.update(worker_image={"reference": image_reference, "image_id": selected["Id"], "inspection": selected},
                          registry_fixture={**provenance["registry_fixture"], "container_name": registry_name,
                                            "loopback_port": port, "public_upload": False})
        _write(destination / "build-provenance.json", provenance)

        # Import the trusted local source only after the build inputs have been
        # captured. The installed wheel inside OCI must carry identical bytes.
        sys.path.insert(0, str(ROOT / "src"))
        from ciw.session import Session
        from ciw.system_spec import demo_spec
        from ciw.system_workflow import COMPARE, execute, run_specification, source_run
        from ciw.system_cli import summary
        from ciw.system_runtime import runtime_identity
        fine = _declare_reduction(_declare_reduction(demo_spec(cells=128), 64), 128)
        coarse = demo_spec(cells=64)
        resources = fine["execution"]["resources"]
        probe_image = _probe_policy(commands, docker, image_reference, resources, runtime_identity())
        if probe_image != selected["Id"]:
            raise RuntimeError("Policy probe executed a different worker image")
        session = Session(source_run([fine, coarse]), destination / "session")
        results = {
            "local": run_specification(session, fine),
            "reduced_local": run_specification(session, coarse),
            "subprocess": run_specification(session, fine, engine="subprocess"),
            "oci": run_specification(session, fine, engine="oci", image=image_reference),
        }
        candidates = {name: value["candidate"]["data"]["simulation"] for name, value in results.items()}
        if not candidates["local"] == candidates["subprocess"] == candidates["oci"]:
            raise RuntimeError("Actual local, subprocess and OCI candidates differ")
        receipt = results["oci"]["candidate"]["data"]["execution_runtime"]
        if (receipt["engine"] != "container" or receipt["image_reference"] != image_reference
                or receipt["image_digest"] != image_reference.split("@", 1)[1]
                or receipt["resources"] != resources or receipt["resource_limits_enforced"] is not True):
            raise RuntimeError("Actual OCI execution receipt differs from selected image/resources")
        comparisons = []
        for name in ("subprocess", "oci", "reduced_local"):
            left, right = results["local"]["candidate"], results[name]["candidate"]
            comparisons.append(execute(session, COMPARE, {"left": left, "right": right,
                "source_result_id": left["result_id"], "source_result_ids": [left["result_id"], right["result_id"]]}))
        if any(value["verification"]["data"]["report"]["status"] != "PASS" for value in results.values()):
            raise RuntimeError("Actual deployment numerical verification failed")
        if any(value["data"]["report"]["status"] != "PASS" for value in comparisons):
            raise RuntimeError("Actual deployment/reduction comparison failed")
        for comparison in comparisons[:2]:
            commutation = comparison["data"]["report"]["commutation"]
            if commutation["max_temperature_error_k"] != 0.0 or commutation["max_displacement_error_m"] != 0.0:
                raise RuntimeError("Same-configuration deployment comparison is not exact")
        workspace = session.save_workspace(destination / "session" / "workspace.json")
        before = workspace.read_bytes()
        restored = Session.from_workspace(workspace, destination / "reopened")
        if restored.results != session.results or restored.executions != session.executions or workspace.read_bytes() != before:
            raise RuntimeError("OCI retained session did not restore exactly")
        _write(destination / "summary.json", summary(session))
        checks = {"schema": "ciw.system-oci-qualification.v1", "status": "PASS",
            "worker_image_reference": image_reference, "worker_image_id": selected["Id"],
            "workspace_digest": _sha(workspace), "source_evidence_id": session.run["evidence_id"],
            "checks": {"immutable_python_base": True, "immutable_registry_fixture": True,
                       "immutable_worker_image": True, "built_image_matches_selected_image": True,
                       "actual_container_policy_probe": True, "packaged_source_matches_local": True,
                       "local_subprocess_oci_exact_candidate_parity": True,
                       "same_configuration_comparison_error_zero": True,
                       "declared_128_to_64_reduction_passes": True,
                       "separate_numerical_verification_passes": True, "retained_session_restores_exactly": True},
            "execution_results": {name: {"result_id": result["candidate"]["result_id"],
                "execution_id": result["candidate"]["execution_id"],
                "verification_id": result["verification"]["data"]["verification_id"]} for name, result in results.items()},
            "physical_validation_status": "not_assessed", "canonical_admission": False,
            "environment_attestation": "not_established", "public_registry_upload": False}
        _write(destination / "checks.json", checks)
        return checks
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as exc:
        _write(destination / "checks.json", {"schema": "ciw.system-oci-qualification.v1", "status": "FAIL",
            "reason": str(exc), "physical_validation_status": "not_assessed", "canonical_admission": False,
            "actual_oci_qualification": "incomplete"})
        raise
    finally:
        if docker is not None and registry_name is not None:
            commands.run([docker, "logs", registry_name], timeout=15, check=False)
            commands.run([docker, "rm", "--force", registry_name], timeout=15, check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = qualify(args.output_dir)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "image": result["worker_image_reference"],
                      "output_dir": str(args.output_dir)}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
