"""Operator binding must preserve actual original Git pins and explicit activation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

import pytest

from ciw import operator_provision as op
from ciw.provider_checkouts import ProviderCheckoutError, validate_checkout, validate_tracked_checkout


def git(root, *args):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(root), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def repository(path):
    path.mkdir()
    git(path, "init", "--quiet")
    git(path, "config", "user.name", "Operator fixture")
    git(path, "config", "user.email", "operator@example.invalid")
    git(path, "config", "core.autocrlf", "false")
    git(path, "config", "core.longpaths", "true")
    return path


@pytest.fixture
def source(tmp_path, monkeypatch):
    provider = repository(tmp_path / "original-provider")
    (provider / "source.txt").write_bytes(b"approved original computation\n")
    (provider / ".gitattributes").write_text("source.txt filter=operator-fixture\n")
    (provider / "LICENSE").write_bytes(b"MIT fixture\n")
    (provider / "pyproject.toml").write_text('[project]\nname="operator-fixture"\nversion="1.0"\nrequires-python=">=3.11"\ndependencies=["numpy==2.4.3"]\n')
    git(provider, "add", ".")
    git(provider, "commit", "--quiet", "-m", "Original runtime pin")
    runtime = git(provider, "rev-parse", "HEAD")
    runtime_tree = git(provider, "rev-parse", "HEAD^{tree}")
    (provider / "source.txt").write_bytes(b"newer imported source is distinct\n")
    git(provider, "add", ".")
    git(provider, "commit", "--quiet", "-m", "Later imported source")
    imported = git(provider, "rev-parse", "HEAD")
    imported_tree = git(provider, "rev-parse", "HEAD^{tree}")
    root = repository(tmp_path / "monorepo")
    (root / "README.md").write_text("Operator source fixture\n")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "Terminal")
    git(root, "fetch", "--quiet", str(provider), "HEAD")
    git(root, "merge", "--quiet", "--allow-unrelated-histories", "-s", "ours", "FETCH_HEAD", "-m", "Retain native history")
    imported_path = root / "instruments/composition/retrieval-agent"
    shutil.copytree(provider, imported_path, ignore=shutil.ignore_patterns(".git"))
    module = {"role": "sra", "path": "instruments/composition/retrieval-agent", "visibility": "public",
              "license": "MIT", "repository": "fixture/original", "import_revision": imported,
              "import_tree": imported_tree}
    (root / "instruments/manifest.json").write_text(json.dumps({"schema": "notations.monorepo-imports.v1", "modules": [module]}))
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "Co-locate source")
    requirements = {"workflow": "schematic-assessment", "operation_id": "ciw.schematic-assessment.v1",
                    "providers": {"sra": {"revision": runtime, "source_tree": runtime_tree,
                      "source_root": ".", "module": "fixture"}}, "external_bindings": {},
                    "requires_upstream_bundle": False, "qualification": "not_performed", "authorizes_execution": False}
    actual = op.workflow_requirements
    def requirement(kind):
        return deepcopy(requirements) if kind == "schematic-assessment" else actual(kind)
    monkeypatch.setattr(op, "workflow_requirements", requirement)
    return root, requirements, imported


def test_catalog_projects_all_installed_workflows_without_provider_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Listing declarations must not invoke a runtime, Git or network")
    monkeypatch.setattr(subprocess, "run", forbidden)
    from ciw.adapters.subprocess import PinnedSubprocessAdapter
    monkeypatch.setattr(PinnedSubprocessAdapter, "__init__", forbidden)
    value = op.catalog()
    assert {row["workflow"] for row in value["workflows"]} == set(op.OPERATIONS)
    selected = next(row for row in value["workflows"] if row["workflow"] == "calibrated-observable")
    assert len(selected["providers"]) == 8
    assert selected["providers"]["fsrt"]["revision"] == "d7c181fb9967883e085b3728d38096f59e54efbd"
    native = next(row for row in value["workflows"] if row["workflow"] == "numerical-heat")
    assert native["external_bindings"]["engine"]["binding_type"] == "executable_file"


def test_plan_is_read_only_and_provision_retains_original_pin_and_dependencies(source, tmp_path, monkeypatch):
    root, requirements, imported = source
    (root / "untracked-source.py").write_text("must never be copied or run\n")
    (root / "results").mkdir()
    (root / "results/session.json").write_text('{"retained":"operator"}')
    before = {name: (root / ".git" / name).read_bytes() for name in ("HEAD", "index", "config")}
    before_paths = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    original = op._git
    def local_only(path, *args):
        assert not {"fetch", "pull", "push", "submodule"}.intersection(args)
        return original(path, *args)
    monkeypatch.setattr(op, "_git", local_only)
    plan = op.plan(root, "schematic-assessment")
    assert plan["status"] == "ready_to_provision"
    assert plan["full_monorepo_import_audit"] == "not_performed"
    assert plan["providers"]["sra"]["dependencies"] == ["numpy==2.4.3"]
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == before_paths
    output = tmp_path / "providers"
    report = op.provision(root, "schematic-assessment", output)
    paths = op.load_bindings_file(output / "bindings.json", workflow="schematic-assessment")["bindings"]
    assert report["status"] == "provisioned" and report["qualification"] == "not_performed"
    assert git(paths["sra"], "rev-parse", "HEAD") == requirements["providers"]["sra"]["revision"] != imported
    assert Path(git(paths["sra"], "rev-parse", "--show-toplevel")).resolve() == paths["sra"].resolve()
    assert (paths["sra"] / ".git").is_dir()
    assert (paths["sra"] / "source.txt").read_bytes() == b"approved original computation\n"
    assert not (paths["sra"] / "untracked-source.py").exists()
    assert not (paths["sra"] / "results").exists()
    assert {name: (root / ".git" / name).read_bytes() for name in before} == before
    validate_checkout(paths["sra"], requirements["providers"]["sra"]["revision"])
    (paths["sra"] / "shadow.py").write_text("forbidden provider source\n")
    with pytest.raises(ProviderCheckoutError, match="untracked"):
        validate_checkout(paths["sra"], requirements["providers"]["sra"]["revision"])


def test_tracked_preflight_addition_does_not_weaken_existing_provider_validation(source):
    root, _, _ = source
    revision = git(root, "rev-parse", "HEAD")
    (root / "operator-report.json").write_text("{}")
    assert validate_tracked_checkout(root, revision) == root
    with pytest.raises(ProviderCheckoutError, match="untracked"):
        validate_checkout(root, revision)


@pytest.mark.parametrize("drift", ["working", "index", "assume-unchanged", "mode"])
def test_dirty_tracked_source_fails_before_creation(source, tmp_path, drift):
    root, _, _ = source
    target = root / "README.md"
    if drift == "mode":
        if os.name != "posix":
            pytest.skip("Native executable-bit validation is POSIX-scoped")
        target.chmod(0o755)
    else:
        if drift == "assume-unchanged":
            git(root, "update-index", "--assume-unchanged", "README.md")
        target.write_text("altered source\n")
        if drift == "index":
            git(root, "add", "README.md")
    with pytest.raises(ProviderCheckoutError):
        op.provision(root, "schematic-assessment", tmp_path / "refused")
    assert not (tmp_path / "refused").exists()


def test_unretained_private_history_and_wrong_pin_refuse_before_creation(source, tmp_path):
    root, requirements, _ = source
    with pytest.raises(op.ProvisionFailure) as private:
        op.provision(root, "identified-stability", tmp_path / "private")
    assert private.value.report["blockers"][0]["role"] == "plsr"
    assert not (tmp_path / "private").exists()
    requirements["providers"]["sra"]["revision"] = "0" * 40
    with pytest.raises(op.ProvisionFailure) as wrong:
        op.provision(root, "schematic-assessment", tmp_path / "wrong-pin")
    assert wrong.value.report["status"] == "blocked"
    assert not (tmp_path / "wrong-pin").exists()


def test_wrong_tree_and_fake_subtree_refuse_before_creation(source, tmp_path):
    root, requirements, _ = source
    requirements["providers"]["sra"]["source_tree"] = "0" * 40
    assert op.plan(root, "schematic-assessment")["status"] == "blocked"
    with pytest.raises(ValueError, match="repository root"):
        op.provision(root / "instruments/composition/retrieval-agent", "schematic-assessment", tmp_path / "fake-root")
    assert not (tmp_path / "fake-root").exists()


def test_create_only_and_normalized_sibling_output(source, tmp_path, monkeypatch):
    root, _, _ = source
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_bytes(b"operator state")
    with pytest.raises(FileExistsError):
        op.provision(root, "schematic-assessment", existing)
    assert (existing / "keep.txt").read_bytes() == b"operator state"
    monkeypatch.chdir(root)
    report = op.provision(root, "schematic-assessment", Path("../sibling-providers"))
    assert report["bindings_file"] == str(tmp_path / "sibling-providers/bindings.json")


def test_output_symlink_traversal_refuses(source, tmp_path):
    root, _, _ = source
    try:
        (tmp_path / "link").symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit creating a directory symlink fixture")
    with pytest.raises(ValueError, match="symlink"):
        op.provision(root, "schematic-assessment", tmp_path / "link" / ".." / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_partial_failure_keeps_created_provider_and_failure_receipt_without_bindings(source, tmp_path, monkeypatch):
    root, requirements, _ = source
    original = op._git
    def fail_checkout(path, *args):
        if "checkout" in args:
            raise subprocess.CalledProcessError(1, ["git", *args])
        return original(path, *args)
    monkeypatch.setattr(op, "_git", fail_checkout)
    output = tmp_path / "failed"
    with pytest.raises(op.ProvisionFailure):
        op.provision(root, "schematic-assessment", output)
    receipt = json.loads((output / "provision-report.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["providers"]["sra"]["revision"] == requirements["providers"]["sra"]["revision"]
    assert (output / "sra/.git").is_dir()
    assert not (output / "bindings.json").exists()
    with pytest.raises(FileExistsError):
        op.provision(root, "schematic-assessment", output)


@pytest.mark.parametrize("receipt_writable", [True, False])
def test_final_receipt_publication_failure_retains_bindings_and_structured_provider_state(source, tmp_path, monkeypatch, capsys, receipt_writable):
    root, _, _ = source
    original = op._write
    def fail_receipt(path, value):
        if Path(path).name == "provision-report.json" and (value["status"] == "provisioned" or not receipt_writable):
            raise OSError("Cannot publish final provisioning receipt")
        return original(path, value)
    monkeypatch.setattr(op, "_write", fail_receipt)
    output = tmp_path / "providers"
    assert op.main(["--monorepo", str(root), "--workflow", "schematic-assessment", "--output-dir", str(output)]) == 1
    report = json.loads(capsys.readouterr().err)
    assert report["schema"] == op.REPORT_SCHEMA and report["status"] == "failed"
    assert report["providers"]["sra"]["status"] == "source_preflight_passed"
    assert report["bindings_file"] == str(output / "bindings.json")
    assert (output / "bindings.json").is_file() and (output / "sra/.git").is_dir()
    if receipt_writable:
        assert json.loads((output / "provision-report.json").read_text())["status"] == "failed"
    else:
        assert report["receipt_write_error"] == "Cannot publish final provisioning receipt"


def test_failure_receipt_write_error_does_not_mask_original_failure(source, tmp_path, monkeypatch):
    root, _, _ = source
    original = op._write
    def fail_write(path, value):
        if Path(path).name == "bindings.json":
            raise OSError("Original bindings publication failure")
        if Path(path).name == "provision-report.json":
            raise OSError("Secondary failure receipt disk error")
        return original(path, value)
    monkeypatch.setattr(op, "_write", fail_write)
    output = tmp_path / "providers"
    with pytest.raises(op.ProvisionFailure) as exc:
        op.provision(root, "schematic-assessment", output)
    assert exc.value.report["reason"] == "Original bindings publication failure"
    assert exc.value.report["receipt_write_error"] == "Secondary failure receipt disk error"
    assert (output / "sra/.git").is_dir() and not (output / "bindings.json").exists()


def test_git_environment_cannot_redirect_original_pin(source, tmp_path, monkeypatch):
    root, requirements, _ = source
    wrong = repository(tmp_path / "wrong")
    (wrong / "wrong.txt").write_text("wrong source")
    git(wrong, "add", ".")
    git(wrong, "commit", "--quiet", "-m", "Wrong Git root")
    monkeypatch.setenv("GIT_DIR", str(wrong / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(wrong))
    output = tmp_path / "providers"
    op.provision(root, "schematic-assessment", output)
    assert (output / "sra/source.txt").read_bytes() == b"approved original computation\n"
    assert op.load_bindings_file(output / "bindings.json")["providers"]["sra"]["revision"] == requirements["providers"]["sra"]["revision"]


def test_configured_smudge_filter_cannot_execute_during_materialization(source, tmp_path, monkeypatch):
    root, _, _ = source
    marker = tmp_path / "unexpected-filter-execution"
    configuration = tmp_path / "git-config"
    configuration.touch()
    command = shlex.join([sys.executable, "-c", "import sys; from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('executed'); sys.stdout.buffer.write(sys.stdin.buffer.read())"])
    git(root, "config", "--file", str(configuration), "filter.operator-fixture.smudge", command)
    git(root, "config", "--file", str(configuration), "filter.operator-fixture.required", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(configuration))
    output = tmp_path / "providers"
    op.provision(root, "schematic-assessment", output)
    assert not marker.exists()
    assert (output / "sra/source.txt").read_bytes() == b"approved original computation\n"
    assert git(root, "config", "--file", str(configuration), "--get", "filter.operator-fixture.smudge") == command


def test_binding_envelope_requires_explicit_workflow_and_original_pin(source, tmp_path):
    root, _, _ = source
    output = tmp_path / "providers"
    op.provision(root, "schematic-assessment", output)
    file = output / "bindings.json"
    with pytest.raises(ValueError, match="different workflow"):
        op.load_bindings_file(file, workflow="measurement-chain")
    record = json.loads(file.read_text())
    record["providers"]["sra"]["revision"] = "0" * 40
    file.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="provider identity"):
        op.load_bindings_file(file)


@pytest.mark.parametrize("raw", ['{"schema":1,"schema":2}', '{"a":NaN}', "x" * 65537],
                         ids=["duplicate-key", "nonfinite-number", "byte-overflow"])
def test_binding_file_rejects_duplicates_nonfinite_and_byte_overflow(tmp_path, raw):
    path = tmp_path / "binding.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        op.load_bindings_file(path)


def test_binding_file_never_activates_on_read_or_workspace_reopen(source, tmp_path, monkeypatch):
    root, _, _ = source
    output = tmp_path / "providers"
    op.provision(root, "schematic-assessment", output)
    from ciw.workbench import Workbench
    from ciw.adapters.oscillator import make_demo_run
    from ciw.session import Session
    def forbidden(*args, **kwargs):
        raise AssertionError("A bindings file or saved workspace must not autoactivate providers")
    monkeypatch.setattr(Workbench, "bind_workflow", forbidden)
    selected = op.load_bindings_file(output / "bindings.json")
    workspace = Session(make_demo_run(), tmp_path / "context").save_workspace(tmp_path / "context.json")
    reopened = Session.from_workspace(workspace, output_dir=tmp_path / "reopened")
    assert selected["workflow"] == "schematic-assessment"
    assert not next(row for row in reopened.workbench.describe_operations() if row.get("source_kind") == selected["workflow"])["available"]


def test_science_cli_requires_explicit_flag_and_accepts_provider_free_binding_file(tmp_path, monkeypatch, capsys):
    from ciw.adapters.oscillator import make_demo_run
    from ciw.session import Session
    from ciw.scientific_cli import main
    workspace = Session(make_demo_run(), tmp_path / "context").save_workspace(tmp_path / "context.json")
    file = tmp_path / "bindings.json"
    _source = {"root": str(tmp_path), "revision": "0" * 40, "source_tree": "1" * 40}
    file.write_text(json.dumps({"schema": op.BINDINGS_SCHEMA, "workflow": "thermal-observer", "bindings": {},
        "source": _source, "providers": {}, "qualification": "not_performed", "authorizes_execution": False}))
    example = Path(__file__).resolve().parents[1] / "examples/thermal-observations/source.json"
    args = ["run", "--workspace", str(workspace), "--kind", "thermal-observer", "--source", str(example),
            "--label", "Synthetic operator reference", "--bindings-file", str(file), "--output-dir", str(tmp_path / "run"), "--json"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert main(["replay", "--workspace", str(tmp_path / "run/workspace.json"), "--bundle", result["bundle_id"],
                 "--bindings-file", str(file), "--output-dir", str(tmp_path / "replay"), "--json"]) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replay_receipt"]["numerical_match"] is True
    file.write_text(json.dumps({**json.loads(file.read_text()), "workflow": "machine-manifest"}))
    assert main(["replay", "--workspace", str(tmp_path / "run/workspace.json"), "--bundle", result["bundle_id"],
                 "--bindings-file", str(file), "--output-dir", str(tmp_path / "wrong-kind"), "--json"]) == 1
    assert "different retained workflow" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(args + ["--binding", "sra=" + str(tmp_path)])


def test_missing_engine_is_actionable_and_never_silently_built(source, tmp_path, monkeypatch):
    root, requirements, _ = source
    requirements["external_bindings"] = {"engine": {"binding_type": "executable_file"}}
    result = op.plan(root, "schematic-assessment")
    assert result["status"] == "blocked" and result["blockers"][0]["binding_type"] == "executable_file"
    assert "--binding engine=" in result["blockers"][0]["prerequisite"]
    engine = tmp_path / "explicit-engine"
    engine.write_bytes(b"not loaded or run")
    report = op.provision(root, "schematic-assessment", tmp_path / "providers", bindings={"engine": engine})
    selected = op.load_bindings_file(Path(report["bindings_file"]))
    assert selected["bindings"]["engine"] == engine
    assert report["plan"]["external_bindings"]["engine"]["identity"].startswith("reported_only")


@pytest.mark.integration
def test_actual_calibrated_pins_materialize_as_standalone_original_repositories(tmp_path):
    """Local retained objects prove provisioning; this does not run provider science."""
    configured = os.environ.get("CIW_OPERATOR_MONOREPO")
    if not configured:
        pytest.skip("Explicit CIW_OPERATOR_MONOREPO native integration source is unavailable")
    root = Path(configured)
    output = tmp_path / "actual-calibrated-pins"
    report = op.provision(root, "calibrated-observable", output)
    selected = op.load_bindings_file(output / "bindings.json", workflow="calibrated-observable")
    expected = op.workflow_requirements("calibrated-observable")["providers"]
    assert len(selected["bindings"]) == len(expected) == 8
    for role, path in selected["bindings"].items():
        assert git(path, "rev-parse", "HEAD") == expected[role]["revision"]
        assert Path(git(path, "rev-parse", "--show-toplevel")).resolve() == path.resolve()
        assert subprocess.run(["git", "-C", str(path), "symbolic-ref", "--quiet", "HEAD"], capture_output=True).returncode == 1
        validate_checkout(path, expected[role]["revision"])
    assert report["qualification"] == "not_performed"
