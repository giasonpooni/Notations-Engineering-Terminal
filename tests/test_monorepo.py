"""Migration must retain real source histories and the existing verifier gates."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import venv

import pytest

from ciw.adapters.protocol import AdapterRefusal
from ciw.adapters.subprocess import PinnedSubprocessAdapter

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("monorepo_operator", ROOT / "scripts/monorepo.py")
monorepo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monorepo)


@pytest.fixture
def measurement(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    monkeypatch.setitem(sys.modules, "monorepo", monorepo)
    spec = importlib.util.spec_from_file_location("measurement_routes", ROOT / "scripts/check_monorepo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_set(tmp_path, measurement):
    path = tmp_path / "legacy-set"
    subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout",
                    "--config", "core.longpaths=true", "--config", "core.autocrlf=false",
                    str(ROOT), str(path)], check=True)
    monorepo.git(path, "checkout", "--quiet", "--detach", measurement.SET_REVISION)
    return path


def _retained_set_route(measurement, monkeypatch, path):
    state = {"closed": False}

    @contextmanager
    def retained(**kwargs):
        state["binding"] = kwargs
        try:
            yield {"set": path}
        finally:
            state["closed"] = True

    monkeypatch.setattr(measurement, "provider_worktrees", retained)
    return state


def test_measurement_SET_default_uses_retained_legacy_source_without_downloading(
    measurement, legacy_set, monkeypatch, tmp_path,
):
    state = _retained_set_route(measurement, monkeypatch, legacy_set)
    original_run = measurement._run

    def no_download(arguments, **kwargs):
        assert "clone" not in [str(argument) for argument in arguments]
        return original_run(arguments, **kwargs)

    monkeypatch.setattr(measurement, "_run", no_download)
    with pytest.raises(RuntimeError, match="execution fixture"):
        with measurement._set_checkout(None, tmp_path, tmp_path / "commands.log") as source:
            assert source == legacy_set
            assert measurement._git(source, "rev-parse", "HEAD") == measurement.SET_REVISION
            assert state["binding"]["overrides"] == {"set": measurement.SET_REVISION}
            raise RuntimeError("execution fixture")
    assert state["closed"] is True


def test_measurement_SET_explicit_external_root_remains_authoritative(
    measurement, legacy_set, monkeypatch, tmp_path,
):
    monkeypatch.setattr(measurement, "provider_worktrees",
                        lambda **kwargs: pytest.fail("An explicit root must not select local history"))
    with measurement._set_checkout(legacy_set, tmp_path, tmp_path / "commands.log") as source:
        assert source == legacy_set
        assert measurement._git(source, "rev-parse", "HEAD") == measurement.SET_REVISION
    assert legacy_set.is_dir()


@pytest.mark.parametrize("route", ["retained", "external"])
@pytest.mark.parametrize("drift", ["working_bytes", "staged_index"])
def test_measurement_SET_routes_reject_hidden_source_and_index_drift(
    measurement, legacy_set, monkeypatch, tmp_path, route, drift,
):
    state = _retained_set_route(measurement, monkeypatch, legacy_set)
    relative = "state_estimation_testbed/contracts.py"
    path = legacy_set / relative
    original = path.read_bytes()
    if drift == "working_bytes":
        monorepo.git(legacy_set, "update-index", "--assume-unchanged", relative)
        path.write_bytes(original + b"\n# hidden source drift\n")
        diagnostic = "tracked bytes differ"
    else:
        path.write_bytes(original + b"\n# staged source drift\n")
        monorepo.git(legacy_set, "add", relative)
        path.write_bytes(original)
        diagnostic = "index differs"
    supplied = legacy_set if route == "external" else None
    with pytest.raises(RuntimeError, match=diagnostic):
        with measurement._set_checkout(supplied, tmp_path, tmp_path / "commands.log"):
            pytest.fail("A dirty legacy SET source must not bind")
    assert state["closed"] is (route == "retained")


def test_measurement_SET_explicit_wrong_pin_refuses_without_fallback(
    measurement, legacy_set, monkeypatch, tmp_path,
):
    monorepo.git(legacy_set, "checkout", "--quiet", "--detach",
                 "5e7bda36f521a5c1b0082b512f35e29803bffafc")
    monkeypatch.setattr(measurement, "provider_worktrees",
                        lambda **kwargs: pytest.fail("A wrong external pin must not fall back"))
    with pytest.raises(ValueError, match="exact revision"):
        with measurement._set_checkout(legacy_set, tmp_path, tmp_path / "commands.log"):
            pytest.fail("The current SET pin cannot replace the legacy exchange pin")


@pytest.fixture
def checkout(tmp_path):
    temporary = tempfile.TemporaryDirectory(dir=tmp_path, prefix="checkout-")
    path = Path(temporary.name) / "monorepo"
    # Only immutable source objects are shared; each test owns its refs and index.
    try:
        # Windows pytest paths can exceed MAX_PATH once full upstream fixtures
        # are appended. Keep both settings local to this disposable clone.
        subprocess.run(["git", "clone", "--quiet", "--shared", "--config", "core.longpaths=true",
                        "--config", "core.autocrlf=false", str(ROOT), str(path)], check=True)
        # Gate sources may still be staged during development; copy just helper metadata.
        (path / "instruments/manifest.json").write_bytes((ROOT / "instruments/manifest.json").read_bytes())
        yield path
    finally:
        # Each 21-module checkout is large. Retain test outputs while promptly
        # removing this fixture's disposable clone, including after failures.
        temporary.cleanup()


def test_both_imports_and_original_runtime_commits_remain_verifiable():
    assert set(monorepo.verify_imports()) == {
        "mcur", "tbrt", "oit", "gsie", "cbsr", "fdir", "set", "fsrt", "edspt", "sidt",
        "jspt", "rci", "stfe", "tsde", "csg", "sra", "ywir", "cse", "scr", "gsv", "framemapper",
    }
    with monorepo.provider_worktrees(roles=["mcur", "tbrt"]) as providers:
        bindings = []
        for role, path in providers.items():
            pin = next(m for m in monorepo.load_manifest()["modules"] if m["role"] == role)
            adapter = PinnedSubprocessAdapter(path, pin["runtime_revision"], role + (".core" if role == "mcur" else ".clock"))
            assert adapter.runtime_identity()["revision"] == pin["runtime_revision"]
            bindings.append(path)
    assert all(not path.exists() for path in bindings)


def test_subtree_directory_does_not_bypass_standalone_repository_check():
    module = monorepo.load_manifest()["modules"][0]
    with pytest.raises(AdapterRefusal, match="not a repository root"):
        PinnedSubprocessAdapter(ROOT / module["path"], module["runtime_revision"], "mcur.core")


def test_working_byte_changes_cannot_hide_behind_git_index_flags(checkout):
    relative = "instruments/measurement/calibration/src/mcur/core.py"
    monorepo.git(checkout, "update-index", "--assume-unchanged", relative)
    path = checkout / relative
    path.write_bytes(path.read_bytes() + b"\n# modified\n")
    with pytest.raises(ValueError, match="working file differs"):
        monorepo.verify_imports(checkout)


def test_staged_source_change_is_rejected_even_when_working_bytes_are_restored(checkout):
    relative = "instruments/measurement/calibration/src/mcur/core.py"
    path = checkout / relative
    original = path.read_bytes()
    path.write_bytes(original + b"\n# staged modification\n")
    monorepo.git(checkout, "add", relative)
    path.write_bytes(original)
    with pytest.raises(subprocess.CalledProcessError):
        monorepo.verify_imports(checkout)


def test_ignored_shadow_module_is_rejected(checkout):
    exclude = checkout / ".git/info/exclude"
    exclude.write_text(exclude.read_text() + "\ninstruments/measurement/clocksync/src/tbrt/shadow.py\n")
    path = checkout / "instruments/measurement/clocksync/src/tbrt/shadow.py"
    path.write_text("print('untracked shadow')\n")
    with pytest.raises(ValueError, match="untracked"):
        monorepo.verify_imports(checkout)


def test_import_snapshot_without_original_commit_ancestry_is_rejected(checkout):
    # Recommit the same tree without parents. File copies are insufficient.
    tree = monorepo.git(checkout, "rev-parse", "HEAD^{tree}").decode().strip()
    monorepo.git(checkout, "config", "user.name", "Migration test")
    monorepo.git(checkout, "config", "user.email", "migration-test@example.invalid")
    commit = monorepo.git(checkout, "commit-tree", tree, "-m", "snapshot without source ancestry").decode().strip()
    monorepo.git(checkout, "checkout", "--detach", commit)
    with pytest.raises(subprocess.CalledProcessError):
        monorepo.verify_imports(checkout)


def test_manifest_cannot_silently_change_existing_runtime_pin(checkout):
    path = checkout / "instruments/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["modules"][0]["runtime_revision"] = manifest["modules"][0]["import_revision"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Runtime pin differs"):
        monorepo.load_manifest(checkout)


def test_worktrees_are_removed_when_execution_fails():
    paths = []
    with pytest.raises(RuntimeError, match="fixture failure"):
        with monorepo.provider_worktrees(revisions="import") as providers:
            paths.extend(providers.values())
            raise RuntimeError("fixture failure")
    assert all(not path.exists() for path in paths)


def test_all_registered_runtime_bindings_use_unchanged_NET_pins():
    with monorepo.provider_worktrees() as providers:
        assert len(providers) == 19
        assert {"gsv", "framemapper"}.isdisjoint(providers)
        for role, path in providers.items():
            pin = monorepo.runtime_pin(role)
            adapter = PinnedSubprocessAdapter(path, pin["revision"], pin["module"], source_root=pin["source_root"])
            assert adapter.runtime_identity()["revision"] == pin["revision"]


def test_legacy_exchange_binding_keeps_distinct_SET_runtime_identity():
    modules = {m["role"]: m for m in monorepo.load_manifest()["modules"]}
    revision = "bd261a765281a95312f7c91a3857233476294c5b"
    with monorepo.provider_worktrees(roles=["set"], overrides={"set": revision}) as providers:
        assert set(providers) == {"set"}
        assert monorepo.git(providers["set"], "rev-parse", "HEAD").decode().strip() == revision
        adapter = PinnedSubprocessAdapter(providers["set"], revision, "state_estimation_testbed.contracts", source_root=".")
        assert adapter.runtime_identity()["revision"] == revision
    assert modules["set"]["runtime_revision"] == "5e7bda36f521a5c1b0082b512f35e29803bffafc"
    assert {m["role"]: m for m in monorepo.load_manifest()["modules"]} == modules


def test_calibrated_window_bindings_preserve_exact_sources_and_cleanup_on_failure():
    declarations = json.loads(
        (ROOT / "src/ciw/calibrated-window-runtimes.json").read_text()
    )
    pins = {role: pin["revision"] for role, pin in declarations.items()}
    assert set(pins) == {"tbrt", "mcur", "stfe", "gsie", "set"}
    assert pins["set"] == "2f838f4e196f453efc3a59045b0b3ec4b5680296"
    before = monorepo.load_manifest()
    paths = []
    with pytest.raises(RuntimeError, match="window execution fixture"):
        with monorepo.provider_worktrees(roles=sorted(pins), overrides=pins) as providers:
            assert set(providers) == set(pins)
            paths.extend(providers.values())
            for role, path in providers.items():
                assert path.name == role
                assert monorepo.git(path, "rev-parse", "HEAD").decode().strip() == pins[role]
                pin = declarations[role]
                adapter = PinnedSubprocessAdapter(
                    path, pin["revision"], pin["module"], source_root=pin["source_root"],
                )
                assert adapter.runtime_identity()["revision"] == pins[role]
            assert monorepo.git(
                providers["set"], "rev-parse", "HEAD^{tree}"
            ).decode().strip() == "54440032b98e24685cc500fd26abaa7ad7700a45"
            raise RuntimeError("window execution fixture")
    assert len(paths) == 5
    assert all(not path.exists() for path in paths)
    assert monorepo.load_manifest() == before
    module = next(m for m in before["modules"] if m["role"] == "set")
    assert module["runtime_revision"] == "5e7bda36f521a5c1b0082b512f35e29803bffafc"


@pytest.mark.parametrize("role, revision", [
    ("tbrt", "edb4e5b99ec0ce384437e1c0f1c820ef04598e33"),
    ("set", "2f838f4e196f453efc3a59045b0b3ec4b5680296"),
    ("csg", "0b00e837c2df3206a3d38b497799f85b72de80f7"),
    ("scr", "91a6d3b37f28623332acd485e9f8a12953acf71e"),
])
def test_native_gate_requires_its_reviewed_side_history(role, revision):
    module = next(m for m in monorepo.load_manifest()["modules"] if m["role"] == role)
    assert revision in module["additional_history_roots"]
    monorepo.git(ROOT, "merge-base", "--is-ancestor", revision, "HEAD")
    monorepo._retained(ROOT, revision, module)
    # Possession of the object does not authorize it through the import branch.
    without_side_history = {**module, "additional_history_roots": [
        history for history in module["additional_history_roots"] if history != revision
    ]}
    with pytest.raises(subprocess.CalledProcessError):
        monorepo._retained(ROOT, revision, without_side_history)


def test_source_override_cannot_bind_an_unrelated_terminal_commit():
    with pytest.raises(subprocess.CalledProcessError):
        with monorepo.provider_worktrees(roles=["set"], overrides={"set": "55d67d42beea95d7ce98af935b48bd84df5e9b4b"}):
            pytest.fail("Unrelated source override must not yield a provider binding")


def test_first_wave_history_remains_an_ancestor():
    monorepo.git(ROOT, "merge-base", "--is-ancestor", "4b9fd7a13e58122644d91467ba1639a5745a087e", "HEAD")
    monorepo.git(ROOT, "merge-base", "--is-ancestor", "9d8509d4a929920204a79531ba829c27e4a2e321", "HEAD")


def test_manifest_cannot_relabel_an_imported_license(checkout):
    path = checkout / "instruments/manifest.json"
    manifest = json.loads(path.read_text())
    module = next(module for module in manifest["modules"] if module["role"] == "cbsr")
    module["license"] = "MPL-2.0"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="original per-module license"):
        monorepo.load_manifest(checkout)


@pytest.mark.parametrize("role", ["gsv", "framemapper"])
def test_views_do_not_acquire_an_invented_scientific_runtime_pin(role):
    assert monorepo.runtime_pin(role) is None
    with pytest.raises(ValueError, match="full source commit identities"):
        with monorepo.provider_worktrees(roles=[role]):
            pytest.fail("A representation without a NET runtime declaration must not execute as a provider")


def test_reviewed_side_history_remains_distinct_from_current_metrology_source():
    revision = "f863bdd69d49224e0cdc871943bbb052e5b0a975"
    module = next(m for m in monorepo.load_manifest()["modules"] if m["role"] == "rci")
    with pytest.raises(subprocess.CalledProcessError):
        monorepo.git(ROOT, "merge-base", "--is-ancestor", revision, module["import_revision"])
    with monorepo.provider_worktrees(roles=["rci"]) as providers:
        assert monorepo.git(providers["rci"], "rev-parse", "HEAD").decode().strip() == revision
    assert module["version"] == "0.2.0"


def test_manifest_cannot_add_unrelated_history_to_authorize_source_execution(checkout):
    path = checkout / "instruments/manifest.json"
    manifest = json.loads(path.read_text())
    module = next(m for m in manifest["modules"] if m["role"] == "rci")
    module["additional_history_roots"].append("55d67d42beea95d7ce98af935b48bd84df5e9b4b")
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="reviewed declaration"):
        monorepo.load_manifest(checkout)


@pytest.mark.parametrize("revision", [
    "edb4e5b99ec0ce384437e1c0f1c820ef04598e33",
    "f863bdd69d49224e0cdc871943bbb052e5b0a975",
    "2f838f4e196f453efc3a59045b0b3ec4b5680296",
    "0b00e837c2df3206a3d38b497799f85b72de80f7",
    "91a6d3b37f28623332acd485e9f8a12953acf71e",
])
def test_retained_side_history_cannot_authorize_a_different_module(revision):
    with pytest.raises(subprocess.CalledProcessError):
        with monorepo.provider_worktrees(roles=["jspt"], overrides={"jspt": revision}):
            pytest.fail("A reviewed side branch grants no authority to an unrelated provider")


@pytest.mark.parametrize("field", ["id", "repository", "repository_id", "ownership", "status"])
def test_manifest_requires_source_provenance_declarations(checkout, field):
    path = checkout / "instruments/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["modules"][0].pop(field)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="provenance"):
        monorepo.load_manifest(checkout)


@pytest.mark.parametrize(("field", "value"), [
    ("id", ""), ("id", "../calibration"),
    ("repository", "https://github.com/giasonpooni/Notations-Calibration-Runtime"),
    ("repository", "owner/../source"), ("repository", "owner/.."),
    ("repository_id", True), ("repository_id", 0), ("repository_id", -1), ("repository_id", "1378878940"),
    ("ownership", " "), ("ownership", "owner\nsource"), ("ownership", None),
    ("status", "held"), ("status", "imported"),
])
def test_manifest_rejects_malformed_provenance_without_authenticating_labels(checkout, field, value):
    path = checkout / "instruments/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["modules"][0][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="provenance"):
        monorepo.load_manifest(checkout)


def test_manifest_module_provenance_ids_are_unique(checkout):
    path = checkout / "instruments/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["modules"][1]["id"] = manifest["modules"][0]["id"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unique formatted module provenance id"):
        monorepo.load_manifest(checkout)


@pytest.mark.parametrize("relative", [
    "src/mcur/venv/__init__.py", "src/mcur/__pycache__/shadow.py",
    "src/mcur/__pycache__/shadow.cpython-312.pyc", "src/mcur/__pycache__/nested/shadow.pyc",
    "src/mcur/.pytest_cache/shadow.py", ".pytest_cache/shadow.py", ".pytest_cache/shadow", "venv/shadow.py",
])
def test_import_cache_names_cannot_exempt_untracked_source(checkout, relative):
    path = checkout / "instruments/measurement/calibration" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("raise RuntimeError('untracked source')\n")
    if not path.suffix:
        path.chmod(0o755)
    with pytest.raises(ValueError, match="untracked"):
        monorepo.verify_imports(checkout)


def test_import_audit_allows_actual_root_environment_and_generated_caches(checkout):
    module = checkout / "instruments/measurement/calibration"
    venv.EnvBuilder(with_pip=False).create(module / ".venv")
    for relative in (".pytest_cache/v/cache/nodeids", ".mypy_cache/3.12/core.data.json",
                     ".ruff_cache/0.12.0/1274629"):
        path = module / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]\n")
    source = module / "src/mcur/core.py"
    py_compile.compile(str(source), doraise=True)
    assert len(monorepo.verify_imports(checkout)) == 21


def test_environment_marker_cannot_authorize_an_importable_environment_root(checkout):
    environment = checkout / "instruments/measurement/calibration/venv"
    environment.mkdir()
    (environment / "pyvenv.cfg").write_text("home = /operator/python\n")
    (environment / "__init__.py").write_text("raise RuntimeError('source hidden by environment marker')\n")
    with pytest.raises(ValueError, match="untracked"):
        monorepo.verify_imports(checkout)


def test_terminal_source_identity_allows_gate_output_and_runtime_caches(checkout):
    output = checkout / "results/monorepo/report.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"status":"running"}\n')
    py_compile.compile(str(checkout / "src/ciw/__init__.py"), doraise=True)
    for relative in ("tests/.pytest_cache/v/cache/nodeids", "src/.mypy_cache/3.12/ciw.data.json",
                     "scripts/.ruff_cache/0.12.0/1274629", "src/ciw.egg-info/PKG-INFO"):
        cache = checkout / relative
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("generated cache or package metadata\n")
    expected = {
        "revision": monorepo.git(checkout, "rev-parse", "HEAD").decode().strip(),
        "source_tree": monorepo.git(checkout, "rev-parse", "HEAD^{tree}").decode().strip(),
    }
    assert monorepo.verify_terminal_source(checkout) == expected


def test_terminal_source_allows_real_pytest_assertion_rewrite_cache(checkout):
    subprocess.run([sys.executable, "-m", "pytest", "-q",
                    "tests/test_monorepo.py::test_first_wave_history_remains_an_ancestor"],
                   cwd=checkout, check=True, capture_output=True, timeout=60)
    rewritten = list((checkout / "tests/__pycache__").glob(
        "test_monorepo." + sys.implementation.cache_tag + "-pytest-" + pytest.__version__ + ".pyc"))
    assert len(rewritten) == 1
    assert monorepo.verify_terminal_source(checkout)["revision"] == monorepo.git(
        checkout, "rev-parse", "HEAD").decode().strip()


@pytest.mark.parametrize("scope", ["import", "terminal"])
def test_pytest_rewrite_cache_cannot_authorize_an_orphan_source(checkout, scope):
    boundary = (checkout / "instruments/measurement/calibration" if scope == "import" else checkout)
    cache = boundary / "tests/__pycache__" / (
        "shadow." + sys.implementation.cache_tag + "-pytest-" + pytest.__version__ + ".pyc")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"orphan rewritten bytecode")
    audit = monorepo.verify_imports if scope == "import" else monorepo.verify_terminal_source
    with pytest.raises(ValueError, match="untracked"):
        audit(checkout)


def test_terminal_source_byte_changes_cannot_hide_behind_index_flags(checkout):
    relative = "src/ciw/__init__.py"
    monorepo.git(checkout, "update-index", "--assume-unchanged", relative)
    path = checkout / relative
    path.write_bytes(path.read_bytes() + b"\n# changed executable source\n")
    with pytest.raises(ValueError, match="working bytes differ"):
        monorepo.verify_terminal_source(checkout)


def test_terminal_source_rejects_staged_metadata_with_restored_working_bytes(checkout):
    path = checkout / "pyproject.toml"
    original = path.read_bytes()
    path.write_bytes(original + b"\n# staged build metadata drift\n")
    monorepo.git(checkout, "add", "pyproject.toml")
    path.write_bytes(original)
    with pytest.raises(ValueError, match="index differs"):
        monorepo.verify_terminal_source(checkout)


@pytest.mark.parametrize("relative", [
    "src/ciw/ignored_shadow.py", "scripts/ignored_shadow.py", "tests/ignored_shadow.py",
])
def test_terminal_source_rejects_ignored_executable_shadow_files(checkout, relative):
    exclude = checkout / ".git/info/exclude"
    exclude.write_text(exclude.read_text() + "\n" + relative + "\n")
    (checkout / relative).write_text("raise RuntimeError('untracked executable source')\n")
    with pytest.raises(ValueError, match="untracked file"):
        monorepo.verify_terminal_source(checkout)


@pytest.mark.parametrize("directory", [
    "venv", ".venv", ".venv-shadow", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "shadow.egg-info",
])
@pytest.mark.parametrize("scope", ["provider", "terminal"])
def test_directory_names_cannot_exempt_untracked_executable_sources(checkout, directory, scope):
    base = ("instruments/measurement/calibration/src/mcur" if scope == "provider"
            else "src/ciw")
    path = checkout / base / directory / "__init__.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("raise RuntimeError('unreviewed package')\n")
    (checkout / ".git/info/exclude").write_text(base + "/" + directory + "/\n")
    audit = monorepo.verify_imports if scope == "provider" else monorepo.verify_terminal_source
    with pytest.raises(ValueError, match="untracked"):
        audit(checkout)


def test_generated_artifact_names_cannot_exempt_symlinked_source(checkout):
    directory = checkout / "instruments/measurement/calibration/src/mcur/__pycache__"
    directory.mkdir()
    (directory / "shadow.pyc").symlink_to(checkout / "src/ciw/__init__.py")
    with pytest.raises(ValueError, match="untracked"):
        monorepo.verify_imports(checkout)


def test_import_audit_allows_only_named_generated_artifacts(checkout):
    base = checkout / "instruments/measurement/calibration"
    py_compile.compile(str(base / "src/mcur/core.py"), doraise=True)
    for relative in (".pytest_cache/v/cache/nodeids",
                     "src/mcur.egg-info/PKG-INFO"):
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"generated artifact")
    assert len(monorepo.verify_imports(checkout)) == 21


@pytest.mark.parametrize("relative", [
    "src/ciw/venv/__init__.py", "src/ciw/.venv-work/shadow.py",
    "src/ciw/__pycache__/shadow.py", "src/ciw/__pycache__/shadow.cpython-312.pyc",
    "src/ciw/__pycache__/nested/shadow.pyc", "src/ciw/.pytest_cache/shadow.py",
    "tests/.pytest_cache/shadow.py", "tests/.pytest_cache/shadow",
    "src/ciw.egg-info/shadow.py", "src/ciw.egg-info/nested/PKG-INFO", "src/venv/shadow.py",
])
def test_terminal_cache_and_metadata_names_cannot_exempt_source(checkout, relative):
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("raise RuntimeError('untracked source')\n")
    if not path.suffix:
        path.chmod(0o755)
    with pytest.raises(ValueError, match="untracked file"):
        monorepo.verify_terminal_source(checkout)


@pytest.mark.parametrize("relative", [
    ".pytest_cache/shadow.unknown", ".pytest_cache/v/cache/unrecognized",
    ".mypy_cache/3.12/shadow.unknown", ".ruff_cache/0.12.0/shadow.unknown",
])
@pytest.mark.parametrize("scope", ["import", "terminal"])
def test_unknown_filenames_cannot_hide_source_inside_tool_caches(checkout, relative, scope):
    boundary = (checkout / "instruments/measurement/calibration" if scope == "import"
                else checkout / "src")
    path = boundary / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unreviewed source with an unfamiliar suffix\n")
    audit = monorepo.verify_imports if scope == "import" else monorepo.verify_terminal_source
    with pytest.raises(ValueError, match="untracked"):
        audit(checkout)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not expose POSIX executable mode bits")
@pytest.mark.parametrize("scope", ["import", "terminal"])
def test_named_cache_data_cannot_exempt_executable_files(checkout, scope):
    boundary = (checkout / "instruments/measurement/calibration" if scope == "import"
                else checkout / "src")
    path = boundary / ".pytest_cache/v/cache/nodeids"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unreviewed executable\n")
    path.chmod(0o755)
    audit = monorepo.verify_imports if scope == "import" else monorepo.verify_terminal_source
    with pytest.raises(ValueError, match="untracked"):
        audit(checkout)
