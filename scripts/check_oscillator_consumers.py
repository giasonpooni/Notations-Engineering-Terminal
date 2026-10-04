#!/usr/bin/env python3
"""Qualify C++ and Godot consumers of an already-qualified oscillator library.

This gate does not export another implementation or replace the parent native
acceptance gate. Every consumer uses the exact parent library bytes. Linux x86-64
only. Native libraries are trusted build inputs, not sandboxed user programs.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import time
import uuid

from ciw.adapters.oscillator_kernel import OscillatorKernel, file_digest, finite_number, operation, OPERATION

ROOT = Path(__file__).resolve().parents[1]
GODOT_CPP_PIN = "e83fd0904c13356ed1d4c3d09f8bb9132bdc6b77"
REQUIRED_PARENT_GATES = ("julia_export", "python_vs_julia", "rust_vs_julia",
                         "existing_analytic_oracle", "ciw_retention", "retained_tsit5")


def parent_helpers():
    spec = importlib.util.spec_from_file_location("ciw_kernel_acceptance", ROOT / "scripts/check_oscillator_kernel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path, limit: int = 16 * 1024 * 1024):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def reject(value):
        raise ValueError("nonfinite JSON token: " + value)
    if path.stat().st_size > limit:
        raise ValueError("JSON file exceeds limit")
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=reject)


def check_parent(directory: Path) -> tuple[dict, dict]:
    """Integrity/compatibility checks; hashes do not authenticate untrusted evidence."""
    helpers = parent_helpers()
    report = read_json(directory / "gate-report.json")
    if report.get("status") != "passed" or any(
            report.get("gates", {}).get(name, {}).get("outcome") != "passed" for name in REQUIRED_PARENT_GATES):
        raise ValueError("parent native qualification is not complete")
    if report.get("report_digest") != helpers.identity({k: v for k, v in report.items() if k != "report_digest"}):
        raise ValueError("parent report integrity mismatch")
    artifacts = report.get("artifact_digests")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("parent artifact inventory missing")
    for name, expected in artifacts.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or not relative.parts:
            raise ValueError("unsafe parent artifact path")
        path = directory / name
        if not path.resolve(strict=True).is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError("parent artifact escapes directory or is not a file")
        if file_digest(path) != expected:
            raise ValueError("parent artifact drift: " + name)
    for name in ("oscillator.jl", "export.jl", "abi.c.in", "rust_binding.rs"):
        if report.get("sources", {}).get(name) != file_digest(ROOT / "runtimes/julia-oscillator-kernel" / name):
            raise ValueError("parent source is not the selected implementation")
    required = ("libciw_oscillator_kernel.so", "cases.tsv", "julia.tsv", "build.json", "retained-julia-session.json")
    if any(name not in artifacts for name in required):
        raise ValueError("parent acceptance evidence is incomplete")
    build = read_json(directory / "build.json")
    if (build["library_sha256"] != artifacts["libciw_oscillator_kernel.so"] or
            build["model_source_sha256"] != report["sources"]["oscillator.jl"]):
        raise ValueError("parent build bindings differ")
    return report, build


def probe_rows(data, source_sha256: str, count: int):
    fields = {"schema", "mode", "source_sha256", "rows", "refusal_checks"}
    if (not isinstance(data, dict) or set(data) != fields or data["schema"] != "oscillator-godot-probe.v1"
            or data["mode"] != "probe" or data["source_sha256"] != source_sha256
            or data["refusal_checks"] != "passed"):
        raise ValueError("Godot probe identity or refusal checks differ")
    rows = data["rows"]
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Godot sample count differs")
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("Godot derivative shape differs")
        for value in row:
            finite_number(value)
    return rows


def trajectory_output(data, source_sha256: str, times):
    if (set(data) != {"schema", "mode", "source_sha256", "trajectory"}
            or data["schema"] != "oscillator-godot-probe.v1" or data["mode"] != "trajectory"
            or data["source_sha256"] != source_sha256):
        raise ValueError("Godot trajectory identity differs")
    trace = data["trajectory"]
    if (set(trace) != {"time_s", "q_m", "v_m_s", "energy_j", "rk4_steps", "owner"}
            or trace["time_s"] != times or trace["owner"] != "godot-host-rk4"
            or type(trace["rk4_steps"]) is not int or not 1 <= trace["rk4_steps"] <= 200000):
        raise ValueError("Godot trajectory support or ownership differs")
    for key in ("time_s", "q_m", "v_m_s", "energy_j"):
        if not isinstance(trace[key], list) or len(trace[key]) != len(times):
            raise ValueError("Godot trajectory shape differs")
        for value in trace[key]:
            finite_number(value)
    return trace


def run(command, out: Path, report: dict, *, stdin=None, expected=0, timeout=120):
    """Retain commands/logs and reject unexpected exit codes, including false success."""
    index = len(report["commands"])
    entry = {"argv": [str(x) for x in command], "expected_exit": expected, "status": "failed"}
    report["commands"].append(entry)
    started = time.perf_counter()
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    try:
        completed = subprocess.run(entry["argv"], cwd=out, input=stdin,
            capture_output=True, timeout=timeout, check=False, env=env)
        for suffix, content in (("stdout", completed.stdout), ("stderr", completed.stderr)):
            path = out / f"command-{index:02d}.{suffix}"
            path.write_bytes(content)
            entry[suffix] = {"file": path.name, "sha256": file_digest(path)}
        entry["exit_code"] = completed.returncode
        if completed.returncode != expected:
            diagnostic = (completed.stderr or completed.stdout)[-8192:].decode("utf-8", "replace")
            raise RuntimeError(f"command {index} exited {completed.returncode}, expected {expected}; retained log tail:\n{diagnostic}")
        entry["status"] = "completed"
        return completed.stdout
    finally:
        entry["elapsed_s"] = time.perf_counter() - started


def qualify(args, out, report):
    helpers = parent_helpers()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("consumer profile is Linux x86-64 only")
    native = args.qualified_dir.resolve(strict=True)
    parent, build = check_parent(native)
    report["parent"] = {"report_sha256": file_digest(native / "gate-report.json"),
        "occurrence_id": parent["occurrence_id"], "library_sha256": build["library_sha256"],
        "model_source_sha256": build["model_source_sha256"]}
    report["gates"]["parent_qualification"] = {"outcome": "passed"}
    tools = {name: helpers.executable(getattr(args, name)) for name in ("cxx", "cmake", "godot", "readelf")}
    for name, path in tools.items():
        report["tools"][name] = {"sha256": file_digest(path),
            "version": run([path, "--version"], out, report).decode().strip()}
    if not report["tools"]["godot"]["version"].startswith("4.5.2.stable."):
        raise ValueError("expected Godot 4.5.2 standard executable")
    cpp = args.godot_cpp.resolve(strict=True)
    if run(["git", "-C", cpp, "rev-parse", "HEAD"], out, report).decode().strip() != GODOT_CPP_PIN:
        raise ValueError("godot-cpp source revision mismatch")
    if run(["git", "-C", cpp, "status", "--porcelain", "--untracked-files=all"], out, report).strip():
        raise ValueError("godot-cpp source is not clean")
    report["godot_cpp"] = {"revision": GODOT_CPP_PIN}
    source = out / "source"
    source.mkdir()
    headers = source / "kernel"
    headers.mkdir()
    for name in ("oscillator_abi.h", "oscillator_cpp.hpp", "cpp_probe.cpp"):
        shutil.copyfile(ROOT / "runtimes/julia-oscillator-kernel" / name, headers / name)
    extension_source = source / "godot"
    shutil.copytree(ROOT / "runtimes/godot-oscillator-kernel", extension_source)
    project = out / "project"
    shutil.copytree(extension_source / "project", project)
    (project / "bin").mkdir()
    library = project / "bin/libciw_oscillator_kernel.so"
    shutil.copyfile(native / library.name, library)
    shutil.copyfile(cpp / "LICENSE.md", project / "bin/LICENSE.godot-cpp.md")
    report["sources"] = {p.relative_to(source).as_posix(): file_digest(p) for p in sorted(source.rglob("*")) if p.is_file()}
    source_sha = build["model_source_sha256"][7:]
    cpp_probe = project / "bin/cpp-probe"
    run([tools["cxx"], "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
         "-fno-fast-math", "-ffp-contract=off", headers / "cpp_probe.cpp", "-L" + str(library.parent),
         "-lciw_oscillator_kernel", "-Wl,-rpath,$ORIGIN", "-o", cpp_probe], out, report)
    run([cpp_probe, source_sha, "--selftest"], out, report)
    raw = (native / "cases.tsv").read_bytes()
    reference = helpers.read_rows(native / "julia.tsv", 1024)
    (out / "cpp.tsv").write_bytes(run([cpp_probe, source_sha], out, report, stdin=raw))
    report["gates"]["cpp_vs_julia"] = helpers.compare(helpers.read_rows(out / "cpp.tsv", 1024), reference)
    run([tools["cmake"], "-S", extension_source, "-B", out / "build", "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_CXX_COMPILER=" + str(tools["cxx"]), "-DGODOT_CPP_DIR=" + str(cpp),
         "-DCIW_KERNEL_DIR=" + str(library.parent), "-DCIW_HEADERS_DIR=" + str(headers),
         "-DCIW_PROJECT_DIR=" + str(project)], out, report)
    run([tools["cmake"], "--build", out / "build", "--target", "ciw_oscillator_godot", "--parallel", "2"],
        out, report, timeout=900)
    extension = project / "bin/libciw_oscillator_godot.so"
    elf = run([tools["readelf"], "-d", extension], out, report).decode()
    if "Shared library: [libciw_oscillator_kernel.so]" not in elf or "[$ORIGIN]" not in elf:
        raise ValueError("extension must link the same library with origin-relative lookup")
    if file_digest(library) != build["library_sha256"]:
        raise ValueError("copied library differs from qualified parent")
    # Relocate after compilation and run with loader overrides removed. This
    # catches accidental reliance on the source/build directory's library.
    relocated = out / "relocated-project"
    project.rename(relocated)
    project = relocated
    library = project / "bin/libciw_oscillator_kernel.so"
    extension = project / "bin/libciw_oscillator_godot.so"
    # An empty-project control distinguishes basic editor startup from extension
    # startup. Godot issue #111048 documents a cold-cache documentation shutdown
    # race. Its import-only frame delay is explicit, bounded by the process
    # timeout, and never applied to the subsequent numerical runtime commands.
    control = out / "empty-editor-control"
    control.mkdir()
    (control / "project.godot").write_text("config_version=5\n")
    run([tools["godot"], "--headless", "--editor", "--path", control, "--import"], out, report)
    run([tools["godot"], "--headless", "--editor", "--path", project,
         "--import", "--frame-delay", "1000"], out, report)
    report["editor_startup"] = {"empty_project": "passed", "extension_import": "passed",
        "import_frame_delay_ms": 1000, "runtime_frame_delay_ms": 0,
        "workaround_reference": "godotengine/godot#111048"}
    command = [tools["godot"], "--headless", "--path", project, "--script", "res://probe.gd", "--"]
    for name in ("godot", "godot-repeat"):
        run(command + ["probe", source_sha, native / "cases.tsv", out / (name + ".json")], out, report)
    first = probe_rows(read_json(out / "godot.json"), source_sha, 1024)
    repeated = probe_rows(read_json(out / "godot-repeat.json"), source_sha, 1024)
    report["gates"]["godot_vs_julia"] = helpers.compare(first, reference)
    report["gates"]["repeat"] = helpers.compare(repeated, first, atol=0.0, rtol=0.0)
    # Wrong source and missing library must fail without a numerical result.
    refused = out / "must-not-exist.json"
    run(command + ["probe", "0" * 64, native / "cases.tsv", refused], out, report, expected=2)
    if refused.exists():
        raise ValueError("refused binding produced a result")
    moved = library.with_suffix(".absent")
    library.rename(moved)
    try:
        run(command + ["probe", source_sha, native / "cases.tsv", refused], out, report, expected=2)
        if refused.exists():
            raise ValueError("missing library produced a result")
    finally:
        moved.rename(library)
    report["gates"]["refusals_and_relocation"] = {"outcome": "passed"}
    comparisons = []
    for name in ("default", "mixed", "undamped", "high-frequency"):
        fixture = read_json(native / (name + "-trajectory.json"))
        request = out / (name + "-source.json")
        request.write_bytes(helpers.canonical(fixture["source"]))
        output = out / (name + "-godot.json")
        run(command + ["trajectory", source_sha, request, output], out, report)
        trace = trajectory_output(read_json(output), source_sha, fixture["source"]["time_s"])
        comparison = helpers.compare(helpers.trajectory_rows(trace), helpers.trajectory_rows(fixture["oracle"]), atol=2e-8, rtol=2e-8)
        comparison.update(name=name, rk4_steps=trace["rk4_steps"])
        comparisons.append(comparison)
    report["gates"]["godot_analytic_trajectories"] = {"outcome": "passed", "fixtures": comparisons}
    from ciw import julia_oscillator as jo
    bundle = read_json(native / "retained-julia-session.json")
    source_request = jo.validate_source(jo.workflow._validate(bundle))
    request = out / "retained-source.json"
    request.write_bytes(helpers.canonical(source_request))
    output = out / "retained-godot.json"
    run(command + ["trajectory", source_sha, request, output], out, report)
    trace = trajectory_output(read_json(output), source_sha, source_request["time_s"])
    reference_trace = bundle["steps"][0]["result"]["data"]["output"]
    report["gates"]["godot_retained_tsit5"] = helpers.compare(
        helpers.trajectory_rows(trace), helpers.trajectory_rows(reference_trace), atol=5e-8, rtol=5e-8)
    # Project actual observations into existing NET records. This operation
    # evaluates derivatives at recorded states; it does not replay Godot.
    from ciw.adapters.protocol import InstrumentManifest
    from ciw.core.identities import evidence_id
    from ciw.core.records import validate_run_structure
    from ciw.operations.registry import OperationRegistry
    from ciw.operations.runner import execute, validate_execution
    from ciw.operations.schemas import validate_payload
    manifest = InstrumentManifest("godot-native-oscillator-host.v1", role="record_only",
                                  units={"q": "m", "v": "m/s", "energy": "J"},
                                  frames=("oscillator-state",))
    observation = {"run_schema": "run.v1", "run_id": "run-" + uuid.uuid4().hex,
        "instrument": "godot-native-oscillator-host.v1",
        "time_s": trace["time_s"], "metadata": {"duration_s": trace["time_s"][-1] + trace["time_s"][-1] - trace["time_s"][-2],
        "sample_count": len(trace["time_s"]), "coordinate_frame": "oscillator-state",
        "manifest": manifest.to_dict(),
        "provenance": {"source": "Godot-owned RK4 trajectory using native RHS; simulated",
            "recorded_output_sha256": file_digest(output), "library_sha256": file_digest(library),
            "extension_sha256": file_digest(extension), "duration_policy": "last sample plus last spacing; no extra evolution"}},
        "channels": {"q": {"unit": "m", "values": trace["q_m"]}, "v": {"unit": "m/s", "values": trace["v_m_s"]},
                     "energy": {"unit": "J", "values": trace["energy_j"]}}}
    observation["evidence_id"] = evidence_id(observation)
    validate_run_structure(observation)
    kernel = OscillatorKernel(library, library_sha256=build["library_sha256"], source_sha256=build["model_source_sha256"])
    registry = OperationRegistry()
    registry.register(operation(kernel))
    selection = {"revision": 0, "channel": "q", "interval_s": [0.0, observation["metadata"]["duration_s"]]}
    params = {"gamma_s_inv": source_request["model"]["gamma_s_inv"], "omega_0_rad_s": source_request["model"]["omega_0_rad_s"],
              "channel": "q", "interval_s": selection["interval_s"]}
    execution, result = execute(registry, observation, selection, "godot-recording.json", OPERATION, params)
    if result is None or result["verification_status"] != "not_verified":
        raise ValueError("NET operation failed or claimed verification")
    saved_loader = ctypes.CDLL
    def denied(*a, **kw):
        raise AssertionError("offline validation attempted native loading")
    ctypes.CDLL = denied
    try:
        validate_execution(execution, observation, 0, {result["result_id"]: result})
        validate_payload(OPERATION, result["data"], observation, params, selection)
    finally:
        ctypes.CDLL = saved_loader
    (out / "godot-recording.json").write_bytes(helpers.canonical(observation))
    (out / "ciw-records.json").write_bytes(helpers.canonical({"execution": execution, "result": result, "selection": selection}))
    report["gates"]["net_retention"] = {"outcome": "passed", "verification_status": "not_verified"}
    report["binaries"] = {"native_sha256": file_digest(library), "extension_sha256": file_digest(extension)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cxx", "cmake", "godot", "readelf"):
        parser.add_argument("--" + name, required=True)
    for name in ("qualified-dir", "godot-cpp", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    out = args.output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    gates = ("parent_qualification", "cpp_vs_julia", "godot_vs_julia", "repeat", "refusals_and_relocation",
             "godot_analytic_trajectories", "godot_retained_tsit5", "net_retention")
    report = {"status": "failed", "occurrence_id": "consumer-gate-" + uuid.uuid4().hex,
        "scope": "Linux x86-64 native consumers; no physical validation, formal proof or frame-budget claim",
        "commands": [], "tools": {}, "gates": {name: {"outcome": "not_run"} for name in gates}}
    try:
        qualify(args, out, report)
        report["status"] = "passed"
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    helpers = parent_helpers()
    report["artifact_digests"] = {p.relative_to(out).as_posix(): file_digest(p)
        for p in sorted(out.rglob("*")) if p.is_file() and not p.is_relative_to(out / "build") and ".godot" not in p.parts}
    report["report_digest"] = helpers.identity(report)
    (out / "consumer-report.json").write_bytes(helpers.canonical(report))
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
