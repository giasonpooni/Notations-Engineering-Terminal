"""Read-only prerequisite inspection; this never qualifies an engineering lane."""
from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

if __package__:
    from .check_monorepo import _environment
    from .monorepo import ROOT, git, verify_imports, verify_terminal_source
else:
    from check_monorepo import _environment
    from monorepo import ROOT, git, verify_imports, verify_terminal_source


GATE_PREREQUISITES = {
    "measurement": {"script": "check_monorepo.py", "minimum_python": (3, 11),
                    "ci_python": ("3.11", "3.12"), "basis": "Terminal and original provider package requirements"},
    "inference": {"script": "check_monorepo_inference.py", "minimum_python": (3, 12),
                  "ci_python": ("3.12", "3.13"), "basis": "Explicit gate requirement"},
    "math": {"script": "check_monorepo_math.py", "minimum_python": (3, 12),
             "ci_python": ("3.12", "3.13"), "basis": "Explicit gate requirement"},
    "flowstate": {"script": "check_monorepo_flowstate.py", "minimum_python": (3, 12),
                  "ci_python": ("3.12", "3.13"), "basis": "Explicit gate requirement"},
    "operations": {"script": "check_monorepo_operations.py", "minimum_python": (3, 12),
                   "ci_python": ("3.12", "3.13"), "basis": "Explicit gate requirement"},
    "surface": {"script": "check_monorepo_surface.py", "minimum_python": (3, 11),
                "ci_python": ("3.12", "3.13"), "basis": "Explicit gate minimum; configured CI uses 3.12 and 3.13"},
    "web": {"script": "check_monorepo_web.py", "minimum_python": (3, 12),
            "ci_python": (), "basis": "Conservative operator floor; web CI selects Node 24 and runner-default Python"},
}
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?"
_TOOLS = {"node": rf"v(?P<version>{_VERSION})", "npm": rf"(?P<version>{_VERSION})",
          "uv": rf"uv (?P<version>{_VERSION})(?: [^\r\n]*)?",
          **{name: rf"{name} (?P<version>{_VERSION})(?: \([^\r\n]*\))?"
             for name in ("cargo", "rustc", "rustdoc")}}


def _status(checks):
    states = {check["status"] for check in checks}
    return "refused" if "refused" in states else "missing" if "missing" in states else "available"


def configured_environment(cargo=None, node_bin=None):
    """Match the coordinator's explicit toolchain bindings without mutating HOME or PATH."""
    environment = _environment()
    if cargo is not None:
        cargo_bin = Path(cargo).expanduser().absolute().parent
        environment["PATH"] = str(cargo_bin) + os.pathsep + environment.get("PATH", "")
    if node_bin is not None:
        environment["PATH"] = str(Path(node_bin).expanduser().resolve()) + os.pathsep + environment.get("PATH", "")
    return environment


def _probe_environment(cargo=None, node_bin=None):
    # Keep the configured functional context: wrappers, loader paths, Rustup
    # selection and Node options can change which executable actually runs.
    # Version probes neither need credentials nor print their environment.
    credentials = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_KEY|API_KEY|ACCESS_KEY|(?:^|_)AUTH(?:_|$)")
    environment = {name: value for name, value in configured_environment(cargo, node_bin).items()
                   if not credentials.search(name.upper())}
    # The real web gate also disables npm authentication/configuration files.
    # Do not read those files merely to discover npm's version.
    environment.update(npm_config_userconfig=os.devnull, npm_config_globalconfig=os.devnull,
                       npm_config_update_notifier="false", npm_config_audit="false", npm_config_fund="false")
    # Rustup proxies may install a missing selected toolchain even when asked
    # only for its version. Inspection must retain selection without installing.
    environment["RUSTUP_AUTO_INSTALL"] = "0"
    return environment


def _tool(name, root, environment, supplied=None):
    selection = "explicit" if supplied is not None else "PATH"
    requested = str(supplied) if supplied is not None else name
    candidate = (str(Path(requested).expanduser().absolute())
                 if supplied is not None and (isinstance(supplied, Path) or Path(requested).is_absolute() or os.path.dirname(requested))
                 else shutil.which(requested, path=environment.get("PATH", "")))
    result = {"name": name, "selection": selection, "status": "missing"}
    if candidate is None:
        return {**result, "reason": "Executable is not available in the selected environment"}
    entrypoint = Path(candidate).absolute()
    result.update(entrypoint=str(entrypoint), executable=str(entrypoint.resolve()))
    if not entrypoint.exists():
        return {**result, "reason": "Selected executable does not exist"}
    if not entrypoint.is_file() or not os.access(entrypoint, os.X_OK):
        return {**result, "status": "refused", "reason": "Selected path is not an executable file"}
    command = [str(entrypoint), "--version"]
    if name in {"cargo", "rustc", "rustdoc"}:
        command.append("--verbose")
    try:
        with ExitStack() as temporary:
            probe_environment = environment
            if name == "npm":
                directory = Path(temporary.enter_context(tempfile.TemporaryDirectory(prefix="notations-npm-doctor-")))
                user_config, global_config = directory / "user.npmrc", directory / "global.npmrc"
                user_config.write_text("")
                global_config.write_text("")
                # npm refuses to load the same path as both user and global
                # config. Keep distinct empty files alive through the probe,
                # replacing inherited casing variants on Windows as well.
                probe_environment = {key: value for key, value in environment.items()
                                     if key.lower() not in {"npm_config_userconfig", "npm_config_globalconfig"}}
                probe_environment.update(npm_config_userconfig=str(user_config), npm_config_globalconfig=str(global_config))
            process = subprocess.run(command, cwd=root, env=probe_environment, check=False, timeout=10,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        return {**result, "status": "refused", "reason": "Version probe could not complete",
                "error_type": type(error).__name__}
    if process.returncode:
        return {**result, "status": "refused", "reason": "Version probe failed", "exit_code": process.returncode}
    lines = process.stdout.strip().splitlines()
    match = re.fullmatch(_TOOLS[name], lines[0]) if lines else None
    if match is None:
        return {**result, "status": "refused", "reason": "Version probe did not identify the selected tool"}
    result.update(status="available", version=match["version"])
    if name in {"cargo", "rustc", "rustdoc"}:
        context = {}
        for line in lines[1:]:
            key, separator, value = line.partition(": ")
            if separator and key in {"release", "host", "commit-hash"}:
                pattern = _VERSION if key == "release" else (r"[0-9a-f]{40}" if key == "commit-hash" else r"[A-Za-z0-9_.-]+")
                if re.fullmatch(pattern, value):
                    context[key] = value
        result["context"] = context
    return result


def _rust_context(tools):
    result = {"name": "rust_toolchain_context", "status": _status(tools),
              "configured_ci_pin": "1.90.0", "compilation": "not_performed"}
    if result["status"] != "available":
        return {**result, "reason": "Cargo availability cannot substitute for compiler and Rustdoc availability"}
    cargo, compiler, documentation = tools
    contexts = [tool.get("context", {}) for tool in tools]
    if any(not {"release", "host", "commit-hash"} <= set(context) for context in contexts):
        return {**result, "status": "refused", "reason": "Tool versions lack an identifiable compiler context"}
    if (any(tool["version"] != context["release"] for tool, context in zip(tools, contexts))
            or len({context["release"] for context in contexts}) != 1
            or len({context["host"] for context in contexts}) != 1
            or compiler["context"]["commit-hash"] != documentation["context"]["commit-hash"]):
        return {**result, "status": "refused", "reason": "Cargo, compiler and Rustdoc contexts do not agree"}
    return {**result, "release": compiler["context"]["release"], "host": compiler["context"]["host"],
            "compiler_commit": compiler["context"]["commit-hash"],
            "configured_ci_pin_match": cargo["version"] == compiler["version"] == documentation["version"] == "1.90.0",
            "reason": "Matching version context is a prerequisite observation, not a successful build"}


def _node_context(node, npm, node_bin):
    result = {"name": "node_npm_context", "status": _status([node, npm]), "minimum_node": 24}
    if result["status"] != "available":
        return result
    if int(node["version"].split(".", 1)[0]) < 24:
        return {**result, "status": "refused", "reason": "The original Globe suite requires Node 24 or newer"}
    if node_bin is not None and any(Path(tool["entrypoint"]).parent.resolve() != node_bin for tool in (node, npm)):
        return {**result, "status": "refused", "reason": "Selected Node directory does not provide both Node and npm"}
    try:
        with Path(npm["executable"]).open("rb") as stream:
            launcher = stream.readline(256).decode("ascii", errors="replace").strip()
    except OSError:
        return {**result, "status": "refused", "reason": "npm launcher context could not be inspected"}
    if launcher in {"#!/usr/bin/env node", "#!/usr/bin/env -S node"}:
        binding = "selected PATH Node"
    elif launcher.startswith("#!") and Path(launcher[2:]).is_absolute() and Path(launcher[2:]).resolve() == Path(node["executable"]):
        binding = "selected absolute Node"
    else:
        return {**result, "status": "refused", "reason": "npm launcher does not identify the selected Node context"}
    return {**result, "npm_node_binding": binding, "reason": "Version and launcher inspection only; package locks and builds are unqualified"}


def _helper_checks(root, script):
    """Read local helper syntax and exact commit declarations without importing it."""
    pending, observed, checks = [script], set(), []
    while pending:
        filename = pending.pop()
        if filename in observed:
            continue
        observed.add(filename)
        path = root / "scripts" / filename
        check = {"name": "helper:" + filename, "status": "available"}
        if not path.is_file():
            checks.append({**check, "status": "missing", "reason": "Local gate helper is absent"})
            continue
        if path.is_symlink() or path.resolve() != path:
            checks.append({**check, "status": "refused", "reason": "Local helper traverses a symlink"})
            continue
        try:
            raw = path.read_bytes()
            tree = ast.parse(raw, filename=filename)
        except (OSError, SyntaxError, ValueError) as error:
            checks.append({**check, "status": "refused", "reason": "Local helper cannot be parsed",
                           "error_type": type(error).__name__})
            continue
        checks.append({**check, "sha256": sha256(raw).hexdigest(), "execution": "not_performed"})
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                module = node.module.rsplit(".", 1)[-1]
                if module == "monorepo" or module.startswith("check_monorepo"):
                    pending.append(module + ".py")
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                continue
            revision = node.value.value
            names = [target.id for target in node.targets if isinstance(target, ast.Name) and target.id.endswith("REVISION")]
            if not names or not _REVISION.fullmatch(revision):
                continue
            binding = {"name": "history:" + filename + ":" + names[0], "revision": revision,
                       "status": "available", "network": "not_attempted"}
            try:
                git(root, "cat-file", "-e", revision + "^{commit}")
            except (OSError, subprocess.SubprocessError):
                checks.append({**binding, "status": "missing", "reason": "Exact historical commit is not present locally"})
                continue
            try:
                git(root, "merge-base", "--is-ancestor", revision, "HEAD")
            except (OSError, subprocess.SubprocessError):
                checks.append({**binding, "status": "refused", "reason": "Exact historical commit is not reachable from HEAD"})
            else:
                checks.append(binding)
    return sorted(checks, key=lambda check: check["name"])


def inspect_workspace(*, root=ROOT, groups=None, cargo=None, node_bin=None, uv=None):
    selected = list(dict.fromkeys(groups or GATE_PREREQUISITES))
    if not set(selected) <= set(GATE_PREREQUISITES):
        raise ValueError("Select registered qualification lanes")
    root = Path(root).expanduser().resolve()
    report = {"schema": "notations.superrepo-preflight.v1", "preflight_id": "preflight:" + uuid.uuid4().hex,
              "created_at": datetime.now(timezone.utc).isoformat(), "status": "refused", "groups": {},
              "qualification": "not_performed", "independent_verification": False, "admission": "not_performed",
              "network": "not_attempted", "source_audit": {"status": "refused"},
              "python": {"executable": str(Path(sys.executable).resolve()), "version": list(sys.version_info[:3])},
              "scope": "Read-only prerequisite observations. Available tools and configured CI matrices do not qualify the current checkout.",
              "dependencies": {"status": "not_checked", "installation": "not_performed",
                               "reason": "Original gates install isolated project dependencies; availability, registry access and downloads are unqualified"}}
    try:
        source = verify_terminal_source(root)
        imports = verify_imports(root)
        report.update(terminal_source=source, source_audit={"status": "available", "imports": imports})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["source_audit"].update(reason="Source identity audit refused this checkout", error_type=type(error).__name__)
        return report
    configured_bin = Path(node_bin).expanduser().resolve() if node_bin is not None else None
    environment = _probe_environment(cargo, configured_bin)
    for group in selected:
        requirements = GATE_PREREQUISITES[group]
        minimum = requirements["minimum_python"]
        checks = [{"name": "python", "minimum": list(minimum), "basis": requirements["basis"],
                   "status": "available" if sys.version_info[:2] >= minimum else "missing"}]
        checks += _helper_checks(root, requirements["script"])
        if group == "web":
            if configured_bin is not None and not configured_bin.is_dir():
                checks.append({"name": "node_bin", "status": "refused" if configured_bin.exists() else "missing",
                               "reason": "Selected Node toolchain directory is unavailable"})
            else:
                paths = {name: (Path(shutil.which(name, path=str(configured_bin)) or configured_bin / name)
                                if configured_bin is not None else None) for name in ("node", "npm")}
                node, npm = (_tool(name, root, environment, paths[name]) for name in ("node", "npm"))
                checks += [node, npm, _node_context(node, npm, configured_bin)]
        elif group == "surface":
            checks.append(_tool("uv", root, environment, uv))
        elif group == "operations":
            selected_cargo = Path(cargo).expanduser().absolute() if cargo is not None else None
            tools = [_tool("cargo", root, environment, selected_cargo),
                     _tool("rustc", root, environment, environment.get("RUSTC")),
                     _tool("rustdoc", root, environment, environment.get("RUSTDOC"))]
            checks += tools + [_rust_context(tools)]
            if os.environ.get("RUSTC_WRAPPER") or os.environ.get("RUSTC_WORKSPACE_WRAPPER"):
                checks.append({"name": "rust_wrapper_context", "status": "refused",
                               "reason": "Configured compiler wrappers were not executed or inspected"})
        report["groups"][group] = {"status": _status(checks), "checks": checks,
                                    "configured_ci_python": list(requirements["ci_python"]),
                                    "qualification": "not_performed"}
    try:
        if verify_terminal_source(root) != source or verify_imports(root) != imports:
            raise ValueError("Source identity changed during preflight")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["source_audit"].update(status="refused", reason="Source identity changed or could not be re-audited",
                                      error_type=type(error).__name__)
    report["status"] = _status([report["source_audit"], *report["groups"].values()])
    return report


def doctor(args):
    report = inspect_workspace(root=getattr(args, "root", ROOT), groups=getattr(args, "group", None),
                               cargo=getattr(args, "cargo", None), node_bin=getattr(args, "node_bin", None),
                               uv=getattr(args, "uv", None))
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Superrepo preflight " + report["status"] + "; qualification not performed")
        print("Source audit: " + report["source_audit"]["status"])
        for group, result in report["groups"].items():
            print(group + ": " + result["status"])
            for check in result["checks"]:
                if check["status"] != "available":
                    print("  " + check["name"] + ": " + check["status"] + " — " + check.get("reason", "requirement unavailable"))
    return 0 if report["status"] == "available" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=list(GATE_PREREQUISITES), action="append")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cargo", type=Path, help="Trusted Cargo executable; compiler and Rustdoc still use the current environment")
    parser.add_argument("--node-bin", type=Path, help="Trusted directory containing Node 24 or newer and npm")
    parser.add_argument("--uv", type=Path, help="Trusted uv executable for Surface's locked lane")
    parser.add_argument("--json", action="store_true")
    return doctor(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
