"""Public CI routing must retain pinned execution and fail-closed behavior."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("public_provider_gate", ROOT / "scripts/check_public_provider_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name,count", [("declared-workloads", 2),
                                        ("calibrated-observable", 8), ("identified-design", 11),
                                        ("calibrated-window", 5)])
def test_public_gate_reads_exact_existing_lane_pins(gate, name, count):
    pins = gate.gate_pins(name)
    assert len(pins) == count
    manifest = json.loads((ROOT / "instruments/manifest.json").read_text())
    assert set(pins) <= {entry["role"] for entry in manifest["modules"] if entry["visibility"] == "public"}
    if name == "declared-workloads":
        original = gate._constant(ROOT / "scripts/check_declared_workloads.py", "PROVIDERS")
        assert pins == {role: revision for role, (_, revision) in original.items()}


def test_public_declarations_cannot_expand_to_private_provider(gate, tmp_path):
    package = tmp_path / "src/ciw"
    package.mkdir(parents=True)
    pins = {role: {"revision": revision} for role, revision in gate.gate_pins("calibrated-observable").items()}
    pins["ppda"] = {"revision": "1" * 40}
    (package / "calibrated-observable-runtimes.json").write_text(json.dumps(pins))
    with pytest.raises(ValueError, match="complete distinct provider set"):
        gate.gate_pins("calibrated-observable", tmp_path)


@pytest.fixture
def retained_route(gate, monkeypatch, tmp_path):
    source = tmp_path / "stack/sra"
    source.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    # Exact-byte source audits must not depend on host newline conversion.
    subprocess.run(["git", "-C", str(source), "config", "core.autocrlf", "false"], check=True)
    (source / "provider.py").write_bytes(b"value = 1\n")
    subprocess.run(["git", "-C", str(source), "add", "provider.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Route Test", "-c",
                    "user.email=route-test@invalid.example", "commit", "--quiet", "-m", "Fixture"], check=True)
    revision = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                              check=True, text=True, capture_output=True).stdout.strip()
    state = {"source": source, "revision": revision, "closed": False}
    monkeypatch.setattr(gate, "gate_pins", lambda name: {"sra": revision})

    @contextmanager
    def retained(root, **kwargs):
        state["binding"] = kwargs
        try:
            yield {"sra": source}
        finally:
            state["closed"] = True

    monkeypatch.setattr(gate, "provider_worktrees", retained)
    return state


def test_unchanged_native_gate_failure_propagates_and_private_credentials_do_not(gate, monkeypatch, retained_route):
    monkeypatch.setenv("CIW_PROVIDER_READ_TOKEN", "private-test-secret")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "private-test-helper")
    monkeypatch.setenv("SCR_READ_TOKEN", "private-test-secret")
    seen = {}

    def child(command, **kwargs):
        seen.update(command=command, **kwargs)
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(gate, "subprocess", SimpleNamespace(run=child))
    assert gate.run_gate("declared-workloads") == 7
    assert seen["command"] == [sys.executable, str(ROOT / "scripts/check_declared_workloads.py"),
                               "--stack-root", str(retained_route["source"].parent)]
    assert not any(name.startswith("GIT_") or name in {"CIW_PROVIDER_READ_TOKEN", "SCR_READ_TOKEN"}
                   for name in seen["env"])
    assert seen["check"] is False
    assert retained_route["binding"] == {"roles": ["sra"], "overrides": {"sra": retained_route["revision"]}}
    assert retained_route["closed"] is True


def test_calibrated_window_forwards_output_to_the_original_gate(gate, monkeypatch, retained_route, tmp_path):
    seen = {}

    def child(command, **kwargs):
        seen.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate, "subprocess", SimpleNamespace(run=child))
    output = tmp_path / "window-evidence"
    assert gate.run_gate("calibrated-window", output) == 0
    assert seen["command"] == [
        sys.executable, str(ROOT / "scripts/check_calibrated_window.py"),
        "--stack-root", str(retained_route["source"].parent),
        "--output-dir", str(output.resolve()),
    ]
    assert retained_route["closed"] is True


def test_public_gate_refuses_hidden_tracked_drift_before_child(gate, monkeypatch, retained_route):
    source = retained_route["source"]
    subprocess.run(["git", "-C", str(source), "update-index", "--assume-unchanged", "provider.py"], check=True)
    (source / "provider.py").write_bytes(b"value = 2\n")
    monkeypatch.setattr(gate, "subprocess", SimpleNamespace(run=lambda *a, **k: pytest.fail("Dirty source cannot execute")))
    with pytest.raises(ValueError, match="tracked bytes differ"):
        gate.run_gate("declared-workloads")
    assert retained_route["closed"] is True


def test_public_gate_rechecks_sources_after_failed_execution(gate, monkeypatch, retained_route):
    calls = []

    def child(*args, **kwargs):
        calls.append(True)
        (retained_route["source"] / "provider.py").write_bytes(b"value = 3\n")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(gate, "subprocess", SimpleNamespace(run=child))
    with pytest.raises(ValueError, match="tracked bytes differ"):
        gate.run_gate("declared-workloads")
    assert calls == [True]
    assert retained_route["closed"] is True


def test_missing_retained_history_never_falls_back_to_network(gate, monkeypatch, retained_route):
    @contextmanager
    def missing(*args, **kwargs):
        raise ValueError("Retained source history unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(gate, "provider_worktrees", missing)
    monkeypatch.setattr(gate, "subprocess", SimpleNamespace(run=lambda *a, **k: pytest.fail("No remote fallback")))
    with pytest.raises(ValueError, match="Retained source history unavailable"):
        gate.run_gate("declared-workloads")


def test_public_workflows_keep_native_gates_and_require_complete_history(gate):
    import yaml

    for name in gate.GATES:
        path = ROOT / ".github/workflows" / (name + ".yml")
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in workflow["jobs"].values():
            assert "CIW_PROVIDER_READ_TOKEN" not in job.get("env", {})
            checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
            assert checkout["with"]["fetch-depth"] == "0"
            assert checkout["with"]["persist-credentials"] == "false"
            assert any("python scripts/check_public_provider_gate.py " + name in step.get("run", "")
                       for step in job["steps"])
