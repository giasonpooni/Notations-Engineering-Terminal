"""Calculation parity and adversarial process/OCI adapter boundary checks."""
import json
import os
from pathlib import Path
import sys

import pytest

from ciw import system_execution as execution
from ciw.system_spec import compile_spec, demo_spec
from ciw.system_models import simulate


def _candidate():
    return {"schema": execution.CANDIDATE_SCHEMA, "test_marker": "adapter-boundary"}


def _fake_worker(command, *, cwd, env, timeout_s, output_path):
    output_path.write_text(json.dumps(_candidate()), encoding="utf-8")
    return b""


@pytest.fixture
def posix_container_identity(monkeypatch):
    """Unit-test Linux container arguments on any host; no Docker is launched."""
    monkeypatch.setattr(execution.os, "getuid", lambda: 1001, raising=False)
    monkeypatch.setattr(execution.os, "getgid", lambda: 1001, raising=False)


def test_real_subprocess_matches_direct_candidate():
    spec = demo_spec(cells=8, force_n=5.0, boundary="free")
    direct = simulate(compile_spec(spec))
    reply = execution.execute_worker(spec)
    assert reply["candidate"] == direct
    assert reply["execution_runtime"]["engine"] == "subprocess"
    assert reply["execution_runtime"]["python"] == sys.version.split()[0]
    assert reply["execution_runtime"]["environment_attestation"] == "not_established"
    assert reply["execution_runtime"]["resources"] == spec["execution"]["resources"]
    assert reply["execution_runtime"]["resource_limits_enforced"] is False
    assert reply["execution_runtime"]["timeout_s"] == 60.0


@pytest.mark.parametrize("image", [None, "net-worker:latest", "net-worker:1", "net-worker@sha256:abc",
                                    "--privileged", "worker@sha256:" + "G" * 64,
                                    "worker@sha256:" + "0" * 64 + " --privileged"])
def test_container_requires_immutable_image_before_launch(monkeypatch, image):
    def no_launch(*args, **kwargs):
        pytest.fail("invalid image must not launch a worker")
    monkeypatch.setattr(execution, "_run_bounded", no_launch)
    with pytest.raises(execution.SystemExecutionError, match="immutable image"):
        execution.execute_worker(demo_spec(), engine="container", image=image)


def test_container_missing_engine_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(execution.shutil, "which", lambda _name: None)
    monkeypatch.setattr(execution, "_run_bounded", lambda *args, **kwargs: pytest.fail("no fallback"))
    with pytest.raises(execution.SystemExecutionError, match="no fallback"):
        execution.execute_worker(demo_spec(), engine="oci", image="worker@sha256:" + "a" * 64)


def test_container_requires_real_posix_identity_without_fallback(monkeypatch):
    monkeypatch.setattr(execution.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.delattr(execution.os, "getuid", raising=False)
    monkeypatch.delattr(execution.os, "getgid", raising=False)
    monkeypatch.setattr(execution, "_run_bounded", lambda *args, **kwargs: pytest.fail("no unsafe launch"))
    with pytest.raises(execution.SystemExecutionError, match="POSIX uid and gid"):
        execution.execute_worker(demo_spec(), engine="oci", image="worker@sha256:" + "a" * 64)


def test_container_argv_uses_one_task_mount_and_explicit_limits(monkeypatch, posix_container_identity):
    observed = {}
    monkeypatch.setattr(execution.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setenv("DO_NOT_INHERIT_SECRET", "secret-value")
    monkeypatch.setenv("PYTHONPATH", "/outside/untrusted")

    def fake(command, **kwargs):
        observed.update(command=command, **kwargs)
        return _fake_worker(command, **kwargs)
    monkeypatch.setattr(execution, "_run_bounded", fake)
    image = "registry.example:5000/net-worker@sha256:" + "b" * 64
    reply = execution.execute_worker(demo_spec(), engine="container", image=image)
    command = observed["command"]
    for flag, value in [("--network", "none"), ("--cap-drop", "ALL"),
                        ("--security-opt", "no-new-privileges"), ("--pids-limit", "64"),
                        ("--cpus", "1"), ("--memory", "256m"), ("--pull", "never"),
                        ("--user", f"{os.getuid()}:{os.getgid()}")]:
        assert command[command.index(flag) + 1] == value
    assert "--read-only" in command and "--privileged" not in command
    assert command.count("--mount") == 1 and "-v" not in command
    mount = command[command.index("--mount") + 1]
    # Docker --mount accepts key=value fields here; bind mounts are writable
    # by default. The volume shorthand's bare `rw` is invalid --mount syntax.
    assert mount == f"type=bind,source={observed['cwd']},target=/work"
    assert all("=" in field for field in mount.split(","))
    assert observed["cwd"].name.startswith("net-system-worker-")
    assert not observed["cwd"].exists(), "task directory must be removed after return"
    assert "DO_NOT_INHERIT_SECRET" not in observed["env"]
    assert observed["env"]["PYTHONPATH"] != "/outside/untrusted"
    assert reply["execution_runtime"]["image_digest"] == "sha256:" + "b" * 64
    assert reply["execution_runtime"]["image_reference"] == image
    assert reply["execution_runtime"]["resources"] == {"cpu": 1, "memory_mb": 256}
    assert reply["execution_runtime"]["resource_limits_enforced"] is True
    assert reply["execution_runtime"]["timeout_s"] == 60.0
    assert not any(str(observed["cwd"]) in arg for arg in reply["execution_runtime"]["command"])


def test_container_honors_nondefault_declared_resource_quotas(monkeypatch, posix_container_identity):
    observed = {}
    monkeypatch.setattr(execution.shutil, "which", lambda _name: "/usr/bin/docker")
    def fake(command, **kwargs):
        observed.update(command=command, **kwargs)
        return _fake_worker(command, **kwargs)
    monkeypatch.setattr(execution, "_run_bounded", fake)
    spec = demo_spec()
    spec["execution"]["resources"] = {"cpu": 3, "memory_mb": 1024}
    reply = execution.execute_worker(spec, engine="oci", image="worker@sha256:" + "d" * 64,
                                     timeout_s=12.5)
    command = observed["command"]
    assert command[command.index("--cpus") + 1] == "3"
    assert command[command.index("--memory") + 1] == "1024m"
    assert observed["timeout_s"] == 12.5
    assert reply["execution_runtime"]["resources"] == {"cpu": 3, "memory_mb": 1024}
    assert reply["execution_runtime"]["resource_limits_enforced"] is True
    assert reply["execution_runtime"]["timeout_s"] == 12.5
    spec["execution"]["resources"]["cpu"] = 9
    assert reply["execution_runtime"]["resources"]["cpu"] == 3


def test_subprocess_receipt_records_requested_resources_without_enforcement(monkeypatch):
    monkeypatch.setattr(execution, "_run_bounded", _fake_worker)
    spec = demo_spec()
    spec["execution"]["resources"] = {"cpu": 4, "memory_mb": 512}
    reply = execution.execute_worker(spec, timeout_s=9)
    assert reply["execution_runtime"]["resources"] == {"cpu": 4, "memory_mb": 512}
    assert reply["execution_runtime"]["resource_limits_enforced"] is False
    assert reply["execution_runtime"]["timeout_s"] == 9.0


@pytest.mark.parametrize("timeout", [0, -1, True, "60", float("nan"), float("inf"), 301, 10 ** 1000])
def test_timeout_envelope_refused(monkeypatch, timeout):
    monkeypatch.setattr(execution, "_run_bounded", lambda *args, **kwargs: pytest.fail("no launch"))
    with pytest.raises(execution.SystemExecutionError, match="timeout must"):
        execution.execute_worker(demo_spec(), timeout_s=timeout)


@pytest.mark.parametrize("bad_spec", [{1: "not-a-string-key"}, {"value": float("nan")},
                                       {"value": (1, 2)}, {"value": object()}])
def test_input_requires_strict_json_before_compilation(monkeypatch, bad_spec):
    monkeypatch.setattr(execution, "_run_bounded", lambda *args, **kwargs: pytest.fail("no launch"))
    with pytest.raises(execution.SystemExecutionError):
        execution.execute_worker(bad_spec)


def test_input_byte_budget_before_compilation():
    with pytest.raises(execution.SystemExecutionError, match="1 MiB"):
        execution.execute_worker({"oversized": "x" * execution.MAX_SPEC_BYTES})


@pytest.mark.parametrize("raw, message", [
    (b'{"schema":"other"}', "mismatched candidate schema"),
    (b'{"schema":"ciw.system-simulation.v1","value":1e9999}', "nonfinite"),
    (b'{"schema":"ciw.system-simulation.v1","value":NaN}', "nonfinite"),
    (b'{"schema":"ciw.system-simulation.v1","schema":"other"}', "duplicate"),
    (b'not json', "invalid strict JSON"),
])
def test_worker_output_boundary_refuses_invalid_candidates(monkeypatch, raw, message):
    def fake(_command, *, output_path, **_kwargs):
        output_path.write_bytes(raw)
        return b""
    monkeypatch.setattr(execution, "_run_bounded", fake)
    with pytest.raises(execution.SystemExecutionError, match=message):
        execution.execute_worker(demo_spec())


def test_candidate_is_required(monkeypatch):
    monkeypatch.setattr(execution, "_run_bounded", lambda *args, **kwargs: b"")
    with pytest.raises(execution.SystemExecutionError, match="did not produce"):
        execution.execute_worker(demo_spec())


def test_candidate_symlink_refused(tmp_path):
    original = tmp_path / "outside.json"
    original.write_text(json.dumps(_candidate()), encoding="utf-8")
    link = tmp_path / "candidate.json"
    link.symlink_to(original)
    with pytest.raises(execution.SystemExecutionError, match="regular file"):
        execution._read_candidate(link)


def test_candidate_byte_budget(tmp_path):
    candidate = tmp_path / "candidate.json"
    with candidate.open("wb") as stream:
        stream.truncate(execution.MAX_CANDIDATE_BYTES + 1)
    with pytest.raises(execution.SystemExecutionError, match="16 MiB"):
        execution._read_candidate(candidate)


def test_bounded_runner_reports_timeout(tmp_path):
    with pytest.raises(execution.SystemExecutionError, match="timeout after"):
        execution._run_bounded([sys.executable, "-c", "import time; time.sleep(10)"],
                               cwd=tmp_path, env=execution._worker_environment(),
                               timeout_s=0.1, output_path=tmp_path / "candidate.json")


def test_bounded_runner_reports_nonzero_exit(tmp_path):
    with pytest.raises(execution.SystemExecutionError, match="exit code 7: calculation refused"):
        execution._run_bounded([sys.executable, "-c", "import sys; print('calculation refused', file=sys.stderr); sys.exit(7)"],
                               cwd=tmp_path, env=execution._worker_environment(),
                               timeout_s=5, output_path=tmp_path / "candidate.json")


def test_bounded_runner_refuses_diagnostic_overflow(tmp_path):
    with pytest.raises(execution.SystemExecutionError, match="64 KiB"):
        execution._run_bounded([sys.executable, "-c", "import sys; sys.stdout.write('x'*100000); sys.stdout.flush()"],
                               cwd=tmp_path, env=execution._worker_environment(),
                               timeout_s=5, output_path=tmp_path / "candidate.json")


def test_container_timeout_cleanup_uses_only_named_task(monkeypatch, posix_container_identity):
    calls = []
    monkeypatch.setattr(execution.shutil, "which", lambda _name: "/usr/bin/docker")
    def fail(command, **_kwargs):
        calls.append(command)
        raise execution.SystemExecutionError("System worker timeout after 1 seconds")
    def cleanup(command, **kwargs):
        calls.append(command)
        assert kwargs["shell"] is False and kwargs["timeout"] == 5
        return None
    monkeypatch.setattr(execution, "_run_bounded", fail)
    monkeypatch.setattr(execution.subprocess, "run", cleanup)
    with pytest.raises(execution.SystemExecutionError, match="timeout"):
        execution.execute_worker(demo_spec(), engine="oci", image="worker@sha256:" + "c" * 64)
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["/usr/bin/docker", "rm", "--force", name]
    assert name.startswith("net-system-worker-")


def test_bounded_runner_never_uses_shell(monkeypatch, tmp_path):
    real_popen = execution.subprocess.Popen
    seen = []
    def observe(command, **kwargs):
        seen.append((command, kwargs))
        return real_popen(command, **kwargs)
    monkeypatch.setattr(execution.subprocess, "Popen", observe)
    execution._run_bounded([sys.executable, "-c", "print('ok')"], cwd=tmp_path,
                           env=execution._worker_environment(), timeout_s=5,
                           output_path=tmp_path / "candidate.json")
    assert seen[0][1]["shell"] is False
    assert seen[0][1]["stdin"] == execution.subprocess.DEVNULL
