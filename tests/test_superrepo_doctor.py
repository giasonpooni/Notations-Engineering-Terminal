"""Preflight availability cannot stand in for execution or source qualification."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
HEAD, TREE, PIN = "1" * 40, "2" * 40, "3" * 40


@pytest.fixture
def doctor(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("superrepo_doctor_test", ROOT / "scripts/superrepo_doctor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for requirement in module.GATE_PREREQUISITES.values():
        (scripts / requirement["script"]).write_text("# A local gate fixture, never executed.\n")
    state = {"source_calls": 0, "dirty": False, "head": HEAD, "missing": set(), "disconnected": set()}

    def audit_source(root):
        state["source_calls"] += 1
        if state["dirty"]:
            raise ValueError("Changed tracked source")
        return {"revision": state["head"], "source_tree": TREE}

    def git(root, *arguments):
        revision = arguments[-1].split("^", 1)[0] if arguments[0] == "cat-file" else arguments[-2]
        if (arguments[0] == "cat-file" and revision in state["missing"]
                or arguments[0] == "merge-base" and revision in state["disconnected"]):
            raise subprocess.CalledProcessError(1, ["git", *arguments])
        return b""

    monkeypatch.setattr(module, "verify_terminal_source", audit_source)
    monkeypatch.setattr(module, "verify_imports", lambda root: {"provider": TREE})
    monkeypatch.setattr(module, "git", git)
    monkeypatch.setattr(module, "sys", SimpleNamespace(version_info=(3, 12, 14), executable=sys.executable))
    return module, root, state


def _checks(report, group):
    return {check["name"]: check for check in report["groups"][group]["checks"]}


def _verbose(name, version="1.90.0", host="x86_64-unknown-linux-gnu", commit=None):
    commit = commit or ("a" * 40 if name == "cargo" else "b" * 40)
    return (f"{name} {version} ({commit[:9]} 2025-09-14)\n"
            f"release: {version}\nhost: {host}\ncommit-hash: {commit}\n")


@pytest.fixture
def tools(doctor, monkeypatch, tmp_path):
    module, root, state = doctor
    directory = tmp_path / "bin"
    directory.mkdir()
    for name in ("node", "npm", "uv", "cargo", "rustc", "rustdoc"):
        path = directory / name
        path.write_text("#!/usr/bin/env node\n" if name == "npm" else "#!/bin/sh\n")
        path.chmod(0o755)
    versions = {"node": "v24.19.0\n", "npm": "11.9.0\n", "uv": "uv 0.12.19 (x86_64-unknown-linux-gnu)\n",
                **{name: _verbose(name) for name in ("cargo", "rustc", "rustdoc")}}
    calls = []

    def discover(command, path=None):
        # These owned fixture files are never executed; version subprocesses
        # below are simulated. Discover their literal names on every platform
        # rather than applying Windows PATHEXT to POSIX-shaped test launchers.
        # File presence and selected PATH/directory ordering remain real.
        for directory_name in (path or "").split(module.os.pathsep):
            candidate = Path(directory_name) / command
            if candidate.is_file() and module.os.access(candidate, module.os.X_OK):
                return str(candidate)
        return None

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[1:] in (["--version"], ["--version", "--verbose"])
        # Native wrapper fixtures can have .cmd/.exe suffixes; the simulated
        # version result still identifies the requested tool, not its wrapper.
        value = versions[Path(command[0]).stem]
        if Path(command[0]).stem == "npm":
            environment = {key.lower(): item for key, item in kwargs["env"].items()}
            user, global_config = (Path(environment[key]) for key in ("npm_config_userconfig", "npm_config_globalconfig"))
            assert user.resolve() != global_config.resolve()
            assert user.read_bytes() == global_config.read_bytes() == b""
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(returncode=0, stdout=value, stderr="")

    monkeypatch.setenv("PATH", str(directory))
    for key in ("RUSTC", "RUSTDOC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(module, "shutil", SimpleNamespace(which=discover))
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, PIPE=subprocess.PIPE,
                                                             SubprocessError=subprocess.SubprocessError))
    return directory, versions, calls


def test_available_is_only_preflight_with_fresh_occurrence_and_stable_source(doctor):
    module, root, state = doctor
    first = module.inspect_workspace(root=root, groups=["measurement", "measurement"])
    second = module.inspect_workspace(root=root, groups=["measurement"])
    assert first["status"] == second["status"] == "available"
    assert list(first["groups"]) == ["measurement"]
    assert first["preflight_id"] != second["preflight_id"]
    assert first["terminal_source"] == second["terminal_source"] == {"revision": HEAD, "source_tree": TREE}
    assert first["qualification"] == first["groups"]["measurement"]["qualification"] == "not_performed"
    assert first["dependencies"]["installation"] == "not_performed"
    assert first["network"] == "not_attempted"
    assert first["admission"] == "not_performed"
    assert first["independent_verification"] is False
    assert "verification_id" not in first
    assert state["source_calls"] == 4


def test_python_minimum_is_lane_specific_and_distinct_from_configured_ci(doctor):
    module, root, _ = doctor
    module.sys.version_info = (3, 11, 9)
    result = module.inspect_workspace(root=root, groups=["measurement", "inference", "surface"])
    assert _checks(result, "measurement")["python"]["status"] == "available"
    assert _checks(result, "inference")["python"]["status"] == "missing"
    assert _checks(result, "surface")["python"]["status"] == "available"
    assert result["groups"]["surface"]["configured_ci_python"] == ["3.12", "3.13"]
    assert result["groups"]["surface"]["qualification"] == "not_performed"


def test_refused_source_is_not_used_to_probe_toolchains(doctor, tools):
    module, root, state = doctor
    _, _, calls = tools
    state["dirty"] = True
    result = module.inspect_workspace(root=root, groups=["web", "operations"])
    assert result["status"] == result["source_audit"]["status"] == "refused"
    assert calls == []
    assert "terminal_source" not in result


@pytest.mark.parametrize("change", ["dirty", "head"])
def test_source_change_during_version_probe_refuses_preflight(doctor, tools, monkeypatch, change):
    module, root, state = doctor
    directory, _, _ = tools
    original = module.subprocess.run

    def run(*args, **kwargs):
        state[change] = True if change == "dirty" else "4" * 40
        return original(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", run)
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    assert report["source_audit"]["status"] == report["status"] == "refused"
    assert report["qualification"] == "not_performed"


def test_available_cargo_does_not_substitute_for_missing_rustdoc(doctor, tools):
    module, root, _ = doctor
    directory, _, _ = tools
    (directory / "rustdoc").unlink()
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    checks = _checks(report, "operations")
    assert checks["cargo"]["status"] == checks["rustc"]["status"] == "available"
    assert checks["rustdoc"]["status"] == checks["rust_toolchain_context"]["status"] == "missing"
    assert report["status"] == "missing"


def test_explicit_cargo_outside_path_forwards_the_actual_compiler_and_rustdoc_context(doctor, tools, monkeypatch, tmp_path):
    module, root, _ = doctor
    directory, _, calls = tools
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    checks = _checks(report, "operations")
    assert checks["cargo"]["status"] == "available"
    assert checks["rustc"]["status"] == checks["rustdoc"]["status"] == "available"
    assert checks["rust_toolchain_context"]["status"] == "available"
    assert len(calls) == 3
    assert all(kwargs["env"]["PATH"].split(module.os.pathsep)[0] == str(directory) for _, kwargs in calls)


def test_explicit_cargo_proxy_preserves_invocation_name_bin_context_and_resolved_identity(doctor, tools, monkeypatch, tmp_path):
    module, root, _ = doctor
    directory, versions, calls = tools
    proxy_directory = tmp_path / "proxy"
    proxy_directory.mkdir()
    proxy = proxy_directory / "rustup"
    proxy.write_text("#!/bin/sh\n")
    proxy.chmod(0o755)
    versions["rustup"] = "rustup 1.28.0\n"
    cargo = directory / "cargo"
    cargo.unlink()
    try:
        cargo.symlink_to(proxy)
    except OSError:
        # Native Windows runners may lack symlink privilege. Model just the
        # proxy's resolved identity while retaining real executable selection
        # and lexical invocation assertions; no platform coverage is skipped.
        cargo.write_text("#!/bin/sh\n")
        cargo.chmod(0o755)
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return proxy if path.absolute() == cargo else original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=cargo)
    selected = _checks(report, "operations")["cargo"]
    assert report["status"] == "available"
    assert selected["entrypoint"] == str(cargo)
    assert selected["executable"] == str(proxy)
    assert calls[0][0][0] == str(cargo)
    assert all(kwargs["env"]["PATH"].split(module.os.pathsep)[0] == str(directory) for _, kwargs in calls)


def test_probe_disables_rustup_auto_install_without_changing_toolchain_selection_or_check_environment(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, calls = tools
    monkeypatch.setenv("RUSTUP_AUTO_INSTALL", "1")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "selected-existing-toolchain")
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    assert report["status"] == "available"
    for _, kwargs in calls:
        assert kwargs["env"]["RUSTUP_AUTO_INSTALL"] == "0"
        assert kwargs["env"]["RUSTUP_TOOLCHAIN"] == "selected-existing-toolchain"
    assert module.configured_environment(cargo=directory / "cargo")["RUSTUP_AUTO_INSTALL"] == "1"
    assert module.os.environ["RUSTUP_AUTO_INSTALL"] == "1"


def test_missing_rustup_toolchain_remains_refused_without_auto_install(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, _ = tools
    monkeypatch.setenv("RUSTUP_AUTO_INSTALL", "1")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "selected-absent-toolchain")

    def absent_toolchain(command, **kwargs):
        assert kwargs["env"]["RUSTUP_AUTO_INSTALL"] == "0"
        assert kwargs["env"]["RUSTUP_TOOLCHAIN"] == "selected-absent-toolchain"
        return SimpleNamespace(returncode=1, stdout="", stderr="toolchain is not installed")

    monkeypatch.setattr(module.subprocess, "run", absent_toolchain)
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    assert report["status"] == "refused"
    assert _checks(report, "operations")["cargo"]["exit_code"] == 1
    assert report["dependencies"]["installation"] == "not_performed"
    assert report["qualification"] == "not_performed"


def test_shared_toolchain_environment_has_stable_order_and_preserves_explicit_compilers(doctor, tools, monkeypatch, tmp_path):
    module, _, _ = doctor
    directory, _, _ = tools
    node_bin = tmp_path / "node-bin"
    node_bin.mkdir()
    monkeypatch.setenv("RUSTC", "/selected/compiler")
    monkeypatch.setenv("RUSTDOC", "/selected/documentation")
    monkeypatch.setenv("PYTHONPATH", "/do-not-import")
    monkeypatch.setenv("GIT_INDEX_FILE", "/do-not-use-index")
    inherited = dict(module.os.environ)
    environment = module.configured_environment(cargo=directory / "cargo", node_bin=node_bin)
    assert environment["PATH"].split(module.os.pathsep)[:2] == [str(node_bin), str(directory)]
    assert environment["RUSTC"] == "/selected/compiler"
    assert environment["RUSTDOC"] == "/selected/documentation"
    assert "PYTHONPATH" not in environment and "GIT_INDEX_FILE" not in environment
    assert dict(module.os.environ) == inherited


def test_explicit_compiler_and_rustdoc_environment_identifies_matching_context(doctor, tools, monkeypatch, tmp_path):
    module, root, _ = doctor
    directory, _, _ = tools
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("RUSTC", str(directory / "rustc"))
    monkeypatch.setenv("RUSTDOC", str(directory / "rustdoc"))
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    context = _checks(report, "operations")["rust_toolchain_context"]
    assert report["status"] == context["status"] == "available"
    assert context["configured_ci_pin_match"] is True
    assert context["compilation"] == "not_performed"


@pytest.mark.parametrize("field,value", [("version", "1.91.0"), ("host", "aarch64-unknown-linux-gnu"), ("commit", "c" * 40)])
def test_different_compiler_and_rustdoc_contexts_are_refused(doctor, tools, field, value):
    module, root, _ = doctor
    _, versions, _ = tools
    versions["rustdoc"] = _verbose("rustdoc", **{field: value})
    report = module.inspect_workspace(root=root, groups=["operations"])
    assert _checks(report, "operations")["rust_toolchain_context"]["status"] == report["status"] == "refused"


def test_new_matching_rust_version_records_ci_difference_without_arbitrary_refusal(doctor, tools):
    module, root, _ = doctor
    _, versions, _ = tools
    versions.update({name: _verbose(name, "1.91.0") for name in ("cargo", "rustc", "rustdoc")})
    report = module.inspect_workspace(root=root, groups=["operations"])
    context = _checks(report, "operations")["rust_toolchain_context"]
    assert context["status"] == "available"
    assert context["configured_ci_pin"] == "1.90.0"
    assert context["configured_ci_pin_match"] is False
    assert report["qualification"] == "not_performed"


def test_compiler_wrapper_is_explicitly_uninspected(doctor, tools, monkeypatch):
    module, root, _ = doctor
    monkeypatch.setenv("RUSTC_WRAPPER", "do-not-execute-wrapper")
    report = module.inspect_workspace(root=root, groups=["operations"])
    assert _checks(report, "operations")["rust_wrapper_context"]["status"] == "refused"


def test_selected_node_pair_is_available_without_provider_credentials_or_npm_auth(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, calls = tools
    monkeypatch.setenv("CIW_PROVIDER_READ_TOKEN", "do-not-retain-secret")
    monkeypatch.setenv("npm_config_registry", "https://do-not-contact.invalid")
    monkeypatch.setenv("NODE_OPTIONS", "--require do-not-load")
    monkeypatch.setenv("NODE_PATH", "/selected/node/modules")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/selected/libraries")
    monkeypatch.setenv("LD_PRELOAD", "/selected/loader.so")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "selected-rust-toolchain")
    monkeypatch.setenv("PYTHONPATH", "do-not-import")
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    assert report["status"] == "available"
    assert _checks(report, "web")["node_npm_context"]["status"] == "available"
    for command, kwargs in calls:
        # Windows normalizes os.environ keys to uppercase; the child context
        # has the same values regardless of that platform casing convention.
        environment = {key.upper(): value for key, value in kwargs["env"].items()}
        if Path(command[0]).stem == "npm":
            user = Path(environment["NPM_CONFIG_USERCONFIG"])
            global_config = Path(environment["NPM_CONFIG_GLOBALCONFIG"])
            assert user != global_config
            assert not user.exists() and not global_config.exists()
        else:
            assert environment["NPM_CONFIG_USERCONFIG"] == module.os.devnull
        assert not {"CIW_PROVIDER_READ_TOKEN", "PYTHONPATH"} & environment.keys()
        assert environment["NODE_OPTIONS"] == "--require do-not-load"
        assert environment["NODE_PATH"] == "/selected/node/modules"
        assert environment["LD_LIBRARY_PATH"] == "/selected/libraries"
        assert environment["LD_PRELOAD"] == "/selected/loader.so"
        assert environment["RUSTUP_TOOLCHAIN"] == "selected-rust-toolchain"
        assert environment["NPM_CONFIG_REGISTRY"] == "https://do-not-contact.invalid"
    assert "do-not-retain-secret" not in json.dumps(report)


def test_installed_posix_npm_version_uses_distinct_empty_configs_and_cleans_them(doctor, monkeypatch):
    module, root, _ = doctor
    if os.name != "posix":
        pytest.skip("Real POSIX npm version regression; opaque native Windows launchers are tested separately")
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("Installed npm is optional for this real-tool regression")
    observed = []
    actual_run = subprocess.run

    def run(command, **kwargs):
        environment = {key.lower(): value for key, value in kwargs["env"].items()}
        configs = tuple(Path(environment[key]) for key in ("npm_config_userconfig", "npm_config_globalconfig"))
        assert configs[0].resolve() != configs[1].resolve()
        assert all(path.read_bytes() == b"" for path in configs)
        observed.extend(configs)
        return actual_run(command, **kwargs)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, PIPE=subprocess.PIPE,
                                                             SubprocessError=subprocess.SubprocessError))
    source_files = set(root.rglob("*"))
    result = module._tool("npm", root, module._probe_environment(), Path(npm))
    assert result["status"] == "available", result
    assert result["version"]
    assert len(observed) == 2
    assert all(not path.parent.exists() for path in observed)
    assert set(root.rglob("*")) == source_files


def test_incomplete_explicit_node_bin_does_not_probe_fallback_npm(doctor, tools, tmp_path):
    module, root, _ = doctor
    _, _, calls = tools
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    node = chosen / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=chosen)
    assert _checks(report, "web")["npm"]["status"] == "missing"
    assert len(calls) == 1
    assert Path(calls[0][0][0]) == node


@pytest.mark.parametrize("filename,launcher", [("npm.cmd", "@ECHO OFF\n"), ("npm.exe", "opaque-native-launcher\n")])
def test_opaque_native_npm_launcher_is_refused_even_when_its_version_probe_succeeds(doctor, tools, monkeypatch, filename, launcher):
    module, root, _ = doctor
    directory, _, _ = tools
    original = module.shutil.which
    native = directory / filename
    native.write_text(launcher)
    native.chmod(0o755)

    def discover(command, path=None):
        return str(native) if command == "npm" else original(command, path=path)

    monkeypatch.setattr(module.shutil, "which", discover)
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    checks = _checks(report, "web")
    assert checks["npm"]["status"] == "available"
    assert checks["node_npm_context"]["status"] == report["status"] == "refused"
    assert "launcher" in checks["node_npm_context"]["reason"]
    assert report["qualification"] == "not_performed"


@pytest.mark.parametrize("problem", ["old_node", "different_npm_node", "absent_directory"])
def test_unavailable_node_context_is_not_reported_ready(doctor, tools, problem):
    module, root, _ = doctor
    directory, versions, calls = tools
    if problem == "old_node":
        versions["node"] = "v22.0.0\n"
    elif problem == "different_npm_node":
        (directory / "npm").write_text("#!/another/node\n")
    else:
        directory = directory / "absent"
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    assert report["status"] in {"missing", "refused"}
    if problem == "absent_directory":
        assert calls == []


def test_surface_requires_uv_without_installing_its_dependencies(doctor, tools):
    module, root, _ = doctor
    directory, _, calls = tools
    (directory / "uv").unlink()
    report = module.inspect_workspace(root=root, groups=["surface"])
    assert _checks(report, "surface")["uv"]["status"] == report["status"] == "missing"
    assert calls == []
    assert report["dependencies"]["status"] == "not_checked"


@pytest.mark.parametrize("output", ["", "pretend-tool 1.2.3", "uv unknown-version"])
def test_unidentified_version_output_is_refused(doctor, tools, output):
    module, root, _ = doctor
    _, versions, _ = tools
    versions["uv"] = output
    report = module.inspect_workspace(root=root, groups=["surface"])
    assert _checks(report, "surface")["uv"]["status"] == "refused"


def test_version_timeout_is_retained_as_refusal(doctor, tools):
    module, root, _ = doctor
    _, versions, _ = tools
    versions["uv"] = subprocess.TimeoutExpired(["uv", "--version"], 10)
    report = module.inspect_workspace(root=root, groups=["surface"])
    assert _checks(report, "surface")["uv"]["error_type"] == "TimeoutExpired"
    assert report["status"] == "refused"


@pytest.mark.parametrize("state_key,status", [("missing", "missing"), ("disconnected", "refused")])
def test_exact_helper_history_must_be_present_and_reachable(doctor, state_key, status):
    module, root, state = doctor
    helper = root / "scripts/check_monorepo.py"
    helper.write_text(f"SET_REVISION = '{PIN}'\n")
    state[state_key].add(PIN)
    report = module.inspect_workspace(root=root, groups=["measurement"])
    history = _checks(report, "measurement")["history:check_monorepo.py:SET_REVISION"]
    assert history["revision"] == PIN
    assert history["status"] == report["status"] == status
    assert history["network"] == "not_attempted"


def test_helpers_are_parsed_recursively_without_executing_their_top_level_code(doctor):
    module, root, _ = doctor
    marker = root / "do-not-create"
    (root / "scripts/check_monorepo.py").write_text(
        f"from check_monorepo_other import helper\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    (root / "scripts/check_monorepo_other.py").write_text(f"OTHER_REVISION = '{PIN}'\n")
    report = module.inspect_workspace(root=root, groups=["measurement"])
    assert report["status"] == "available"
    assert "helper:check_monorepo_other.py" in _checks(report, "measurement")
    assert not marker.exists()


@pytest.mark.parametrize("problem,status", [("missing", "missing"), ("syntax", "refused"), ("symlink", "refused")])
def test_gate_helper_input_problems_are_visible(doctor, problem, status):
    module, root, _ = doctor
    helper = root / "scripts/check_monorepo.py"
    if problem == "missing":
        helper.unlink()
    elif problem == "syntax":
        helper.write_text("def broken(:\n")
    else:
        original = root / "original.py"
        helper.rename(original)
        helper.symlink_to(original)
    report = module.inspect_workspace(root=root, groups=["measurement"])
    assert _checks(report, "measurement")["helper:check_monorepo.py"]["status"] == report["status"] == status


def test_cli_json_is_reviewable_and_nonqualifying(doctor, capsys):
    module, root, _ = doctor
    assert module.main(["--root", str(root), "--group", "measurement", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "notations.superrepo-preflight.v1"
    assert report["status"] == "available"
    assert report["qualification"] == "not_performed"
    assert not list(root.rglob("report.json"))


def test_unknown_group_is_rejected_before_any_source_or_tool_probe(doctor):
    module, root, state = doctor
    with pytest.raises(ValueError, match="registered"):
        module.inspect_workspace(root=root, groups=["unexpected"])
    assert state["source_calls"] == 0
