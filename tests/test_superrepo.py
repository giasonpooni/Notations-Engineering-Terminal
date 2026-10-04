"""Coordinator evidence must be fresh, well formed and tied to one revision."""
import errno
import importlib.util
from hashlib import sha256
import json
import os
from pathlib import Path
from subprocess import CalledProcessError, SubprocessError, TimeoutExpired
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD = "1" * 40
SOURCE_TREE = "a" * 40
SOURCE = {"revision": HEAD, "source_tree": SOURCE_TREE}
MEASUREMENT_SCHEMA = "notations.monorepo-gate-report.v1"


@pytest.fixture
def coordinator(monkeypatch):
    # Load the operator without changing the installed ciw package or Git state.
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("superrepo_test_monorepo", ROOT / "scripts/monorepo.py")
    monorepo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monorepo)
    monkeypatch.setitem(sys.modules, "monorepo", monorepo)
    spec = importlib.util.spec_from_file_location("superrepo_test_coordinator", ROOT / "scripts/superrepo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {"head": HEAD, "source_tree": SOURCE_TREE, "dirty": False,
             "audits": 0, "source_audits": 0}

    def audit(root):
        state["audits"] += 1
        return {"provider": "preserved-source-tree"}

    def source_audit(root):
        state["source_audits"] += 1
        if state["dirty"]:
            raise ValueError("Terminal tracked working bytes differ from the reported source revision")
        return {"revision": state["head"], "source_tree": state["source_tree"]}

    monkeypatch.setattr(module, "git", lambda *args: state["head"].encode())
    monkeypatch.setattr(module, "verify_imports", audit)
    monkeypatch.setattr(module, "verify_terminal_source", source_audit)
    return module, state


def _arguments(output, *, temp_root=None):
    return SimpleNamespace(group=["measurement"], output_dir=output,
                           temp_root=temp_root, node_bin=None, cargo=None, full_reproduction=False)


def _fresh_report(**overrides):
    return {"schema": MEASUREMENT_SCHEMA, "terminal_revision": HEAD,
            "terminal_source": dict(SOURCE),
            "verification_id": "verification:" + uuid.uuid4().hex,
            "status": "passed", **overrides}


def _child(monkeypatch, coordinator, payload, *, returncode=0, after=None):
    module, _ = coordinator

    def run(command, **kwargs):
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True)
        (output / "report.json").write_text(json.dumps(payload))
        if after is not None:
            after()
        return SimpleNamespace(returncode=returncode)

    # Substitute only this coordinator's process object, not global subprocess.
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, SubprocessError=SubprocessError))


def _report(output):
    return json.loads((output / "report.json").read_text())


_TEMPFILE_PROBE = """
import errno
import json
import os
from pathlib import Path
import sys
import tempfile

output = Path(sys.argv[1])
report = json.loads(sys.argv[2])

class FailingCleanup(tempfile.TemporaryDirectory):
    @classmethod
    def _rmtree(cls, name, **kwargs):
        raise OSError(errno.ENOTEMPTY, "Injected persistent cleanup failure", name)

temporary_directory = FailingCleanup if sys.argv[3] == "fail" else tempfile.TemporaryDirectory
try:
    with temporary_directory(prefix="superrepo-probe-") as directory:
        report["temporary_directory"] = directory
        report["temporary_environment"] = {name: os.environ.get(name) for name in ("TMPDIR", "TEMP", "TMP")}
        (Path(directory) / "probe").write_text("child execution completed")
except OSError as error:
    report.update(status="failed", error={"type": type(error).__name__, "message": str(error), "errno": error.errno})
output.mkdir(parents=True)
(output / "report.json").write_text(json.dumps(report))
sys.exit(0 if report["status"] == "passed" else 1)
"""


def _tempfile_child(monkeypatch, coordinator, *, cleanup_fails=False):
    """Use a real child interpreter while keeping scientific gates out of this test."""
    module, _ = coordinator

    def run(command, **kwargs):
        output = command[command.index("--output-dir") + 1]
        return subprocess.run(
            [sys.executable, "-c", _TEMPFILE_PROBE, output, json.dumps(_fresh_report()),
             "fail" if cleanup_fails else "pass"], **kwargs)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, SubprocessError=SubprocessError))


def test_temp_root_places_real_child_tempfiles_and_overrides_inherited_scratch_paths(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator
    inherited = tmp_path / "scratch"
    inherited.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(inherited))
    # The existing import/Git environment sanitation must still apply.
    monkeypatch.setenv("PYTHONHOME", "untrusted-override")
    _tempfile_child(monkeypatch, coordinator)
    output = tmp_path / "evidence"
    assert module.main(["check", "--group", "measurement", "--output-dir", str(output),
                        "--temp-root", str(private / ".." / "private")]) == 0
    aggregate = _report(output)
    child = json.loads(Path(aggregate["groups"]["measurement"]["report"]).read_text())
    assert aggregate["temp_root"] == str(private.resolve())
    assert Path(child["temporary_directory"]).parent == private.resolve()
    assert child["temporary_environment"] == {name: str(private.resolve()) for name in ("TMPDIR", "TEMP", "TMP")}
    assert {name: module.os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")} == {
        name: str(inherited) for name in ("TMPDIR", "TEMP", "TMP")}
    assert not Path(child["temporary_directory"]).exists()
    assert list(private.iterdir()) == list(inherited.iterdir()) == []


def test_default_temp_environment_is_preserved(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator
    expected = {name: str(tmp_path / name) for name in ("TMPDIR", "TEMP", "TMP")}
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    observed = {}

    def run(command, **kwargs):
        observed.update(kwargs["env"])
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True)
        (output / "report.json").write_text(json.dumps(_fresh_report()))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, SubprocessError=SubprocessError))
    assert module.check(_arguments(tmp_path)) == 0
    assert {name: observed[name] for name in expected} == expected
    assert "temp_root" not in _report(tmp_path)


def test_explicit_cargo_supplies_real_child_compiler_context_and_uv_is_forwarded(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator
    tool_bin = tmp_path / "trusted-tools"
    tool_bin.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("cargo", "rustc", "rustdoc", "uv"):
        path = tool_bin / (name + suffix)
        path.write_text("Owned tool discovery fixture, never executed.\n")
        path.chmod(0o755)
    inherited_path = os.environ.get("PATH", "")
    observed = []
    probe = """
import json
from pathlib import Path
import shutil
import sys
output = Path(sys.argv[1])
report = json.loads(sys.argv[2])
report['compiler_paths'] = {name: shutil.which(name) for name in ('cargo', 'rustc', 'rustdoc')}
output.mkdir(parents=True)
(output / 'report.json').write_text(json.dumps(report))
"""

    def run(command, **kwargs):
        observed.append(command)
        group = "operations" if command[1].endswith("check_monorepo_operations.py") else "surface"
        return subprocess.run([sys.executable, "-c", probe,
                               command[command.index("--output-dir") + 1],
                               json.dumps(_fresh_report(schema="notations.monorepo-" + group + "-gate.v1"))], **kwargs)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, SubprocessError=SubprocessError))
    cargo, uv = tool_bin / ("cargo" + suffix), tool_bin / ("uv" + suffix)
    output = tmp_path / "evidence"
    assert module.main(["check", "--group", "operations", "--group", "surface",
                        "--cargo", str(cargo), "--uv", str(uv), "--output-dir", str(output)]) == 0
    aggregate = _report(output)
    for group in ("operations", "surface"):
        child = json.loads(Path(aggregate["groups"][group]["report"]).read_text())
        assert all(value is not None for value in child["compiler_paths"].values())
        assert {name: Path(value) for name, value in child["compiler_paths"].items()} == {
            name: tool_bin / (name + suffix) for name in ("cargo", "rustc", "rustdoc")
        }
    assert observed[1][observed[1].index("--uv") + 1] == str(uv.resolve())
    assert os.environ.get("PATH", "") == inherited_path


@pytest.mark.parametrize("result,exit_code", [
    ({"status": "published"}, 0),
    ({"status": "published", "publication_timeout": True}, 0),
    ({"status": "unpublished", "publication_timeout": True}, 1),
    ({"status": "concurrent_state", "publication_timeout": True}, 1),
    ({"status": "published", "publication_error": {"type": "OSError", "message": "cleanup failed"}}, 1),
])
def test_module_apply_cli_retains_actual_publication_outcome_and_lifecycle_failure(coordinator, monkeypatch, tmp_path, capsys, result, exit_code):
    module, _ = coordinator
    monkeypatch.setitem(sys.modules, "module_update", SimpleNamespace(
        apply=lambda root, plan: result, prepare=lambda *args: pytest.fail("Apply must not prepare another candidate")))
    assert module.main(["module-apply", "--plan", str(tmp_path / "plan.json")]) == exit_code
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_temp_root_requires_an_existing_directory_before_child_execution(coordinator, monkeypatch, tmp_path, kind):
    module, _ = coordinator
    temp_root = tmp_path / "temp-root"
    if kind == "file":
        temp_root.write_text("not a directory")
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: pytest.fail("Invalid temp root must not execute a child"),
        SubprocessError=SubprocessError))
    assert module.check(_arguments(tmp_path / "evidence", temp_root=temp_root)) == 1
    aggregate = _report(tmp_path / "evidence")
    assert aggregate["status"] == "failed"
    assert aggregate["groups"] == {}
    assert aggregate["error"] == {"type": "ValueError", "message": "--temp-root must be an existing directory"}


def test_unusable_temp_root_fails_before_child_can_fall_back(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator
    private = tmp_path / "private"
    private.mkdir()

    def unwritable(*args, **kwargs):
        assert kwargs["dir"] == private.resolve()
        raise PermissionError(errno.EACCES, "temp root is not writable", str(private))

    monkeypatch.setattr(module, "tempfile", SimpleNamespace(TemporaryFile=unwritable))
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: pytest.fail("Unusable temp root must not execute a child"),
        SubprocessError=SubprocessError))
    output = tmp_path / "evidence"
    assert module.check(_arguments(output, temp_root=private)) == 1
    aggregate = _report(output)
    assert aggregate["status"] == "failed"
    assert aggregate["groups"] == {}
    assert aggregate["error"]["type"] == "PermissionError"
    assert "not writable" in aggregate["error"]["message"]


@pytest.mark.parametrize("retry_fails", [False, True], ids=["successful-retry", "persistent-cleanup-failure"])
def test_temp_root_retry_retains_failed_evidence_and_fresh_identities(coordinator, monkeypatch, tmp_path, retry_fails):
    module, _ = coordinator
    private = tmp_path / "private"
    private.mkdir()
    output = tmp_path / "evidence"
    _tempfile_child(monkeypatch, coordinator, cleanup_fails=True)
    assert module.check(_arguments(output, temp_root=private)) == 1
    previous = _report(output)
    previous_lane = previous["groups"]["measurement"]
    previous_path = Path(previous_lane["report"])
    previous_bytes = previous_path.read_bytes()
    assert previous["status"] == previous_lane["status"] == "failed"
    assert previous_lane["error"]["type"] == "OSError"
    assert previous_lane["error"]["errno"] == errno.ENOTEMPTY
    assert "persistent cleanup failure" in previous_lane["error"]["message"]

    _tempfile_child(monkeypatch, coordinator, cleanup_fails=retry_fails)
    assert module.check(_arguments(output, temp_root=private)) == (1 if retry_fails else 0)
    current = _report(output)
    current_lane = current["groups"]["measurement"]
    expected_status = "failed" if retry_fails else "passed"
    assert current["status"] == current_lane["status"] == expected_status
    if retry_fails:
        assert current_lane["error"]["errno"] == errno.ENOTEMPTY
    assert previous_path.read_bytes() == previous_bytes
    assert previous_lane["report_sha256"] == sha256(previous_bytes).hexdigest()
    assert current_lane["report_sha256"] == sha256(Path(current_lane["report"]).read_bytes()).hexdigest()
    assert current["verification_id"] != previous["verification_id"]
    assert current_lane["verification_id"] != previous_lane["verification_id"]
    assert current_lane["report"] != previous_lane["report"]
    assert current["terminal_source"] == previous["terminal_source"] == SOURCE
    assert current_lane["terminal_source"] == previous_lane["terminal_source"] == SOURCE


def test_old_same_revision_evidence_cannot_substitute_for_a_missing_child_report(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator
    old = tmp_path / "measurement/report.json"
    old.parent.mkdir()
    old.write_text(json.dumps(_fresh_report()))
    previous = old.read_bytes()
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0), SubprocessError=SubprocessError))
    assert module.check(_arguments(tmp_path)) == 1
    result = _report(tmp_path)
    assert result["status"] == "failed"
    assert result["groups"]["measurement"]["error"]["type"] == "MissingEvidence"
    assert Path(result["groups"]["measurement"]["report"]) != old
    assert old.read_bytes() == previous


def test_child_launch_removes_import_and_git_environment_overrides(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTEST_PLUGINS", "GIT_DIR",
                 "GIT_INDEX_FILE", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        monkeypatch.setenv(name, "untrusted-override")
    observed = {}

    def run(command, **kwargs):
        observed.update(kwargs["env"])
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True)
        (output / "report.json").write_text(json.dumps(_fresh_report()))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, SubprocessError=SubprocessError))
    assert module.check(_arguments(tmp_path)) == 0
    assert not {"PYTHONPATH", "PYTHONHOME", "PYTEST_PLUGINS", "GIT_DIR",
                "GIT_INDEX_FILE", "GIT_ALTERNATE_OBJECT_DIRECTORIES"}.intersection(observed)
    assert observed["PYTHONNOUSERSITE"] == "1"


@pytest.mark.parametrize("payload", [
    [],
    _fresh_report(verification_id="verification:" + "g" * 32),
    _fresh_report(schema="notations.unrelated-gate.v1"),
])
def test_malformed_or_unrelated_child_evidence_fails_with_a_final_report(coordinator, monkeypatch, tmp_path, payload):
    module, _ = coordinator
    _child(monkeypatch, coordinator, payload)
    assert module.check(_arguments(tmp_path)) == 1
    result = _report(tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ValueError"


def test_revision_change_after_a_successful_child_invalidates_the_aggregate(coordinator, monkeypatch, tmp_path):
    module, state = coordinator
    _child(monkeypatch, coordinator, _fresh_report(), after=lambda: state.update(head="2" * 40))
    assert module.check(_arguments(tmp_path)) == 1
    result = _report(tmp_path)
    assert result["status"] == "failed"
    assert "revision changed" in result["error"]["message"]
    assert state["audits"] == 2


def test_fresh_valid_evidence_passes_and_preserves_its_identity(coordinator, monkeypatch, tmp_path):
    module, state = coordinator
    payload = _fresh_report()
    _child(monkeypatch, coordinator, payload)
    assert module.check(_arguments(tmp_path)) == 0
    result = _report(tmp_path)
    lane = result["groups"]["measurement"]
    assert result["status"] == lane["status"] == "passed"
    assert lane["verification_id"] == payload["verification_id"]
    assert lane["terminal_source"] == result["terminal_source"] == SOURCE
    assert lane["report_sha256"] == sha256(Path(lane["report"]).read_bytes()).hexdigest()
    assert state["audits"] == 2
    assert state["source_audits"] == 2


def test_failed_child_retains_its_concrete_error(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator
    error = {"type": "RuntimeError", "message": "original package check failed"}
    _child(monkeypatch, coordinator, _fresh_report(status="failed", error=error), returncode=1)
    assert module.check(_arguments(tmp_path)) == 1
    lane = _report(tmp_path)["groups"]["measurement"]
    assert lane["status"] == "failed"
    assert lane["error"] == error


def test_audit_timeout_retains_a_failed_report(coordinator, monkeypatch, tmp_path):
    module, _ = coordinator

    def timeout(root):
        raise TimeoutExpired(["git", "ls-tree"], 30)

    monkeypatch.setattr(module, "verify_imports", timeout)
    assert module.check(_arguments(tmp_path)) == 1
    result = _report(tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "TimeoutExpired"


def test_dirty_terminal_source_is_rejected_before_any_child_executes(coordinator, monkeypatch, tmp_path):
    module, state = coordinator
    state["dirty"] = True
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: pytest.fail("Dirty source must not execute a child"),
        CalledProcessError=CalledProcessError, SubprocessError=SubprocessError))
    assert module.check(_arguments(tmp_path)) == 1
    result = _report(tmp_path)
    assert result["groups"] == {}
    assert "working bytes differ" in result["error"]["message"]
    assert state["audits"] == 0


def test_working_source_change_during_child_execution_invalidates_aggregate(coordinator, monkeypatch, tmp_path):
    module, state = coordinator
    _child(monkeypatch, coordinator, _fresh_report(), after=lambda: state.update(dirty=True))
    assert module.check(_arguments(tmp_path)) == 1
    result = _report(tmp_path)
    assert result["status"] == "failed"
    assert "working bytes differ" in result["error"]["message"]
    assert result["terminal_revision"] == HEAD
    assert state["source_audits"] == 2


@pytest.mark.parametrize("child_source", [None, {"revision": HEAD, "source_tree": "b" * 40}])
def test_child_source_binding_is_required_and_matches_aggregate(coordinator, monkeypatch, tmp_path, child_source):
    module, _ = coordinator
    payload = _fresh_report(terminal_source=child_source)
    _child(monkeypatch, coordinator, payload)
    assert module.check(_arguments(tmp_path)) == 1
    result = _report(tmp_path)
    assert "source identity differs" in result["error"]["message"]
    lane = result["groups"]["measurement"]
    assert lane["status"] == "failed"
    assert lane["terminal_source"] == child_source
    assert lane["report_sha256"] == sha256(Path(lane["report"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["inference", "math", "flowstate", "operations", "surface", "web"])
@pytest.mark.parametrize("dirty_before", [True, False], ids=["dirty-before", "changed-during"])
def test_each_gate_binds_actual_terminal_source_around_execution(monkeypatch, tmp_path, name, dirty_before):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    path = ROOT / "scripts" / ("check_monorepo_" + name + ".py")
    spec = importlib.util.spec_from_file_location("superrepo_test_gate_" + name, path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    state = {"dirty": dirty_before, "executions": 0, "audits": 0}

    def source_audit(*args, **kwargs):
        state["audits"] += 1
        if state["dirty"]:
            raise ValueError("Terminal tracked working bytes differ from the reported source revision")
        return dict(SOURCE)

    def qualify(*args, **kwargs):
        state["executions"] += 1
        state["dirty"] = True

    monkeypatch.setattr(gate, "_git", lambda *args: HEAD)
    monkeypatch.setattr(gate, "verify_terminal_source", source_audit)
    monkeypatch.setattr(gate, "qualify" if name in {"surface", "web"} else "_qualify", qualify)
    assert gate.main(["--output-dir", str(tmp_path)]) == 1
    result = _report(tmp_path)
    assert result["status"] == "failed"
    assert "working bytes differ" in result["error"]["message"]
    assert state["executions"] == (0 if dirty_before else 1)
    assert state["audits"] == (1 if dirty_before else 2)
    if not dirty_before:
        assert result["terminal_source"] == SOURCE


def test_surface_retry_preserves_old_evidence_before_rejecting_dirty_current_source(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("superrepo_test_surface_retry", ROOT / "scripts/check_monorepo_surface.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    old_log = tmp_path / "commands.log"
    old_log.write_text("original fixture failure")
    old_revision = "2" * 40
    previous = {"terminal_revision": old_revision, "verification_id": "verification:" + uuid.uuid4().hex,
                "status": "failed", "log": str(old_log), "created_at": "2026-10-03T00:00:00Z"}
    previous_bytes = json.dumps(previous).encode()
    (tmp_path / "report.json").write_bytes(previous_bytes)
    monkeypatch.setattr(gate, "_git", lambda *args: HEAD)

    def dirty_source(*args, **kwargs):
        raise ValueError("Terminal tracked working bytes differ from the reported source revision")

    monkeypatch.setattr(gate, "verify_terminal_source", dirty_source)
    monkeypatch.setattr(gate, "resume_remaining", lambda *args: pytest.fail("Dirty source must not resume execution"))
    assert gate.main(["--output-dir", str(tmp_path), "--resume-remaining"]) == 1
    result = _report(tmp_path)
    retained = result["prefix_evidence"]["report"]
    assert result["terminal_revision"] == HEAD
    assert retained["terminal_revision"] == old_revision
    assert retained["verification_id"] == previous["verification_id"]
    assert Path(retained["path"]).read_bytes() == previous_bytes
    assert retained["sha256"] == sha256(previous_bytes).hexdigest()
    assert old_log.read_text() == "original fixture failure"
