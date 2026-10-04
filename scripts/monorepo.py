"""Audit preserved module imports and bind their original standalone Git histories.

This operator helper never changes a runtime pin or loads executable paths from
an investigation. Detached worktrees preserve the existing adapter's strict
repository-root, commit, file-byte and interpreter checks.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import errno
from hashlib import sha1
from importlib.util import source_from_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
_PATHS = {
    "mcur": "instruments/measurement/calibration",
    "tbrt": "instruments/measurement/clocksync",
    "oit": "instruments/mathematics/observability",
    "gsie": "instruments/inference/state-inference",
    "cbsr": "instruments/inference/state-recompiler",
    "fdir": "instruments/inference/faultsense",
    "set": "instruments/verification/estimator-bench",
    "fsrt": "instruments/domain/flowstate",
    "edspt": "instruments/mathematics/sensor-design",
    "sidt": "instruments/mathematics/linear-dynamics",
    "jspt": "instruments/mathematics/sensitivity",
    "rci": "instruments/measurement/metrology",
    "stfe": "instruments/measurement/signal-processing",
    "tsde": "instruments/mathematics/polygon-trajectories",
    "csg": "instruments/mathematics/surface",
    "sra": "instruments/composition/retrieval-agent",
    "ywir": "instruments/resources/yield-weighted",
    "cse": "instruments/domain/bim-estimator",
    "scr": "instruments/execution/compute-runtime",
    "gsv": "instruments/views/real-time-globe",
    "framemapper": "instruments/representation/frame-mapper",
}
_CONTRACTS = {
    "mcur": ("mcur", "src", "MPL-2.0", {"text": "MPL-2.0"}, "NOTICE.md"),
    "tbrt": ("tbrt", "src", "MPL-2.0", {"text": "MPL-2.0"}, "NOTICE.md"),
    "oit": ("oit", "src", "MPL-2.0", {"text": "MPL-2.0"}, "NOTICE.md"),
    "gsie": ("geometric_state_inference", "src", "MPL-2.0", "MPL-2.0", "NOTICE.md"),
    "cbsr": ("cbsr", "src", "AGPL-3.0", {"file": "LICENSE"}, None),
    "fdir": ("fdir", "src", "MPL-2.0", {"text": "MPL-2.0"}, "NOTICE.md"),
    "set": ("state_estimation_testbed", ".", "Apache-2.0", None, None),
    "fsrt": ("set_lcm", "src", "MIT", "MIT", None),
    "edspt": ("edspt", "src", "MPL-2.0", {"text": "MPL-2.0"}, "NOTICE.md"),
    "sidt": ("sidt", "src", "MPL-2.0", {"text": "MPL-2.0"}, "NOTICE.md"),
    "jspt": ("sensitivity", "src", "MIT", "MIT", None),
    "rci": ("instrument_chain", "src", "MIT", "MIT", None),
    "stfe": ("stfe", ".", "MPL-2.0", {"text": "MPL-2.0"}, None),
    "tsde": ("translation_surface_dynamics", "src", "MIT", "MIT", None),
    "csg": ("geodesic_testbed", "src", "MPL-2.0", "MPL-2.0", None),
    "sra": ("schematics", "src", "MIT", "MIT", None),
    "ywir": ("ywir", "src", "MIT", "MIT", None),
    "cse": ("gat", ".", "MIT", {"text": "MIT"}, None),
    "scr": (None, ".", "Apache-2.0", None, None),
    "gsv": (None, ".", "GPL-3.0", None, None),
    "framemapper": (None, ".", "GPL-3.0", None, None),
}
_BUILD_KINDS = {"scr": "python-source", "gsv": "npm", "framemapper": "npm"}
_ADDITIONAL_HISTORY_ROOTS = {
    "tbrt": ["edb4e5b99ec0ce384437e1c0f1c820ef04598e33"],
    "set": ["2f838f4e196f453efc3a59045b0b3ec4b5680296"],
    "fsrt": ["e13e46facc125682776f165ce1b0460b4ff9410a"],
    "framemapper": ["bdfcb041836e86ab1ce93688b127f8960e8d08cf"],
    "rci": ["f863bdd69d49224e0cdc871943bbb052e5b0a975"],
    "csg": ["e8f0938ab243a1905792ba2c43439cb4f40cd4be", "0b00e837c2df3206a3d38b497799f85b72de80f7"],
    "scr": ["98ab2f312cb7f9f4dbb18b25f762593eba56653e", "91a6d3b37f28623332acd485e9f8a12953acf71e"],
    "gsv": ["06c47bd851d8ea8b363a6bbe60a96c7448cbe12e"],
}
_JSON_PINS = {role: ("calibrated-observable-runtimes.json", role)
              for role in ("mcur", "tbrt", "oit", "gsie", "cbsr", "fdir", "set", "fsrt")}
_JSON_PINS.update({role: ("identified-design-runtimes.json", role) for role in ("edspt", "sidt", "ywir")})
_JSON_PINS.update({role: ("adapter-runtimes.json", role) for role in ("rci", "jspt")})
_JSON_PINS["stfe"] = ("telemetry-runtimes.json", "stfe")
_PYTHON_PINS = {
    "sra": ("declared_workload.py", "PINS", "schematic-assessment"),
    "scr": ("declared_workload.py", "PINS", "numerical-heat"),
    "cse": ("bim_quantity.py", "PIN", None),
    "csg": ("geodesic_reference.py", "PINS", "curved-path-transfer"),
    "tsde": ("geometry_research.py", "PINS", "translation-flow"),
}
_ENVIRONMENTS = {".venv", "venv"}
_ENVIRONMENT_CONTENTS = {"bin", "Scripts", "lib", "lib64", "Lib", "include", "Include", "share"}
_PYTEST_CACHE_FILES = {"README.md", ".gitignore", "CACHEDIR.TAG", "v/cache/nodeids",
                       "v/cache/lastfailed", "v/cache/stepwise"}
_TOOL_CACHE_MARKERS = {".gitignore", "CACHEDIR.TAG"}
_PACKAGE_METADATA = {"PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt",
                     "requires.txt", "top_level.txt", "not-zip-safe", "zip-safe"}
_IMPORT_STATUSES = {
    **{role: "first-wave-import; independent package; source repository retained"
       for role in ("mcur", "tbrt")},
    **{role: "second-wave-import; independent package; source repository retained"
       for role in ("oit", "gsie", "cbsr", "fdir", "set")},
    **{role: "superrepo import; independent build and release boundary; source repository retained"
       for role in _PATHS if role not in {"mcur", "tbrt", "oit", "gsie", "cbsr", "fdir", "set"}},
}


def _generated_file(path, boundary, *, environments=False, package_metadata=False):
    """Permit known generated files without exempting source by directory name.

    Environments belong at a module's root and must have a pyvenv marker. Tool
    caches belong at the audited boundary's root; Python bytecode belongs
    directly inside __pycache__. Nested venv packages remain executable source.
    """
    path, boundary = Path(path), Path(boundary)
    relative = path.relative_to(boundary)
    parts = relative.parts
    if len(parts) < 2:
        return False
    if any((boundary / Path(*parts[:index])).is_symlink() for index in range(1, len(parts))):
        return False
    if environments and parts[0] in _ENVIRONMENTS:
        marker = boundary / parts[0] / "pyvenv.cfg"
        if not marker.is_file() or marker.is_symlink():
            return False
        # A marker cannot turn the environment root into an importable package.
        if parts[1] in _ENVIRONMENT_CONTENTS:
            return len(parts) > 2 or path.is_dir()
        return len(parts) == 2 and parts[1] in {"pyvenv.cfg", ".gitignore", "CACHEDIR.TAG"}
    if path.is_symlink() or not path.is_file() or path.resolve() != path or path.stat().st_mode & 0o111:
        return False
    if parts[-2] == "__pycache__":
        try:
            source = Path(source_from_cache(str(path)))
        except ValueError:
            # Pytest appends its dotted version to the interpreter cache tag;
            # importlib accepts only the conventional one-tag cache filename.
            # Recognize that exact rewrite shape without accepting orphan code.
            tag = sys.implementation.cache_tag
            rewrite = (re.fullmatch(
                r"(?P<stem>.+)\." + re.escape(tag) + r"-pytest-"
                r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?"
                r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
                r"(?:\+[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*)?\.pyc", path.name)
                if tag else None)
            if rewrite is None:
                return False
            source = path.parent.parent / (rewrite["stem"] + ".py")
        return source.is_file() and not source.is_symlink()
    # Recognize generated data by its declared shape, not by the absence of a
    # familiar source suffix. Unknown filenames cannot hide inside tool caches.
    cache_name = Path(*parts[1:]).as_posix()
    if parts[0] == ".pytest_cache":
        return cache_name in _PYTEST_CACHE_FILES
    if parts[0] == ".mypy_cache":
        return cache_name in _TOOL_CACHE_MARKERS or re.fullmatch(
            r"[0-9]+\.[0-9]+/(?:[A-Za-z_][A-Za-z0-9_]*/)*[A-Za-z_][A-Za-z0-9_]*\.(?:data|meta)\.json",
            cache_name) is not None
    if parts[0] == ".ruff_cache":
        return cache_name in _TOOL_CACHE_MARKERS or re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+/[0-9]+", cache_name) is not None
    if package_metadata and parts[-2].endswith(".egg-info"):
        # Build metadata belongs immediately under a declared source boundary.
        # A nested package directory cannot exempt arbitrary source or metadata.
        metadata_root = len(parts) == 2 or (len(parts) == 3 and parts[0] == "src")
        return metadata_root and parts[-1] in _PACKAGE_METADATA
    return False


def git(root, *arguments):
    environment = dict(os.environ)
    # Repository selection is explicit; caller environment cannot replace it.
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(name, None)
    return subprocess.run(
        ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c",
         "core.autocrlf=false", "-C", str(root), *arguments],
        env=environment, check=True, capture_output=True, timeout=30,
    ).stdout


@contextmanager
def worktree_mutation_lock(root):
    """Serialize managed worktree add/remove operations across processes.

    Git scans sibling worktree metadata during these mutations, so distinct
    destination names do not isolate simultaneous adds and removals. Linked
    worktrees share this lock in their actual Git common directory. Keep the
    lock file after release: removing it would permit a second lock inode.
    """
    root = Path(root).resolve()
    common = Path(os.fsdecode(git(root, "rev-parse", "--git-common-dir")).strip())
    if not common.is_absolute():
        common = root / common
    lock_path = common.resolve() / "notations-worktree-mutation.lock"
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            # Windows byte-range locks require a byte to exist in the file.
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _constant(path, name):
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Missing literal source declaration: " + name)


def runtime_pin(role, root=ROOT):
    """Read an existing NET declaration without executing its source code."""
    root = Path(root)
    if role in _JSON_PINS:
        filename, key = _JSON_PINS[role]
        pin = dict(json.loads((root / "src/ciw" / filename).read_text())[key])
    elif role in _PYTHON_PINS:
        filename, name, key = _PYTHON_PINS[role]
        value = _constant(root / "src/ciw" / filename, name)
        pin = dict(value if key is None else value[key])
    elif role in {"gsv", "framemapper"}:
        return None
    else:
        raise ValueError("Unknown registered provider role")
    pin.setdefault("source_root", "src")
    return pin


def project_version(path, project):
    if "version" in project:
        return project["version"]
    # Surface's original Hatch declaration owns its dynamic release version.
    if project.get("name") == "curved-surface-geodesic-sensitivity":
        return _constant(Path(path) / "src/geodesic_testbed/engine/contract.py", "RUNTIME_VERSION")
    raise ValueError("Unsupported original dynamic package version")


def _retained(root, revision, module):
    error = None
    for history in [module["import_revision"], *module.get("additional_history_roots", [])]:
        try:
            git(root, "merge-base", "--is-ancestor", revision, history)
            return
        except subprocess.CalledProcessError as caught:
            error = caught
    raise error


def load_manifest(root=ROOT):
    root = Path(root).resolve()
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate manifest key")
            result[key] = value
        return result
    manifest = json.loads((root / "instruments/manifest.json").read_text(), object_pairs_hook=unique)
    if set(manifest) != {"schema", "modules"} or manifest["schema"] != "notations.monorepo-imports.v1":
        raise ValueError("Unsupported import manifest")
    modules = manifest["modules"]
    if not isinstance(modules, list) or len(modules) != len(_PATHS):
        raise ValueError("Import manifest must name exactly the registered modules")
    roles, identifiers = set(), set()
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError("Each module must be an object")
        role = module.get("role")
        if not isinstance(role, str) or role not in _PATHS or role in roles or module.get("path") != _PATHS[role]:
            raise ValueError("Duplicate or unsupported module role/path")
        roles.add(role)
        # These are required source declarations, not authenticated ownership
        # or legal provenance. Historical source labels remain unchanged.
        identifier = module.get("id")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier)
                or identifier in identifiers):
            raise ValueError("A unique formatted module provenance id is required")
        identifiers.add(identifier)
        repository = module.get("repository")
        if (not isinstance(repository, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository)
                or repository.rsplit("/", 1)[-1] in {".", ".."}):
            raise ValueError("A formatted owner/repository provenance declaration is required")
        repository_id = module.get("repository_id")
        if type(repository_id) is not int or repository_id <= 0:
            raise ValueError("A positive integer repository provenance id is required")
        ownership = module.get("ownership")
        if (not isinstance(ownership, str) or not ownership or ownership != ownership.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in ownership)):
            raise ValueError("A nonempty ownership provenance declaration is required")
        if module.get("status") != _IMPORT_STATUSES[role]:
            raise ValueError("Module provenance status must retain its declared import boundary")
        python_import, source_root, license_name, project_license, notice = _CONTRACTS[role]
        if module.get("visibility") != "public" or module.get("license") != license_name:
            raise ValueError("Imports require declared public source and their original per-module license")
        for key in ("import_revision", "import_tree"):
            if not isinstance(module.get(key), str) or not re.fullmatch(r"[0-9a-f]{40}", module[key]):
                raise ValueError("Full SHA-1 source identities are required")
        pin = runtime_pin(role, root)
        if module.get("runtime_revision") != (pin["revision"] if pin else None):
            raise ValueError("Runtime pin differs from the existing NET declaration")
        if module.get("additional_history_roots", []) != _ADDITIONAL_HISTORY_ROOTS.get(role, []):
            raise ValueError("Additional public source history differs from the reviewed declaration")
        if module.get("build_kind", "python") != _BUILD_KINDS.get(role, "python"):
            raise ValueError("Original package build boundary changed")
        if (module.get("source_root") != source_root or module.get("python_import") != python_import
                or (pin is not None and source_root != pin["source_root"])):
            raise ValueError("Module import contract changed")
        path = (root / module["path"]).resolve()
        if not path.is_relative_to(root) or path != root / module["path"]:
            raise ValueError("Imported module paths must not traverse symlinks")
        project = (json.loads((path / "package.json").read_text()) if role in {"gsv", "framemapper"}
                   else tomllib.loads((path / "pyproject.toml").read_text())["project"])
        if (project["name"] != module["distribution"] or project_version(path, project) != module["version"]
                or project.get("license") != project_license):
            raise ValueError("Package identity or license differs from the import declaration")
        if module.get("notice") != notice or not (path / "LICENSE").is_file():
            raise ValueError("Preserved license and notice declarations are required")
        if notice is not None and not (path / notice).is_file():
            raise ValueError("The original module notice must remain present")
    return manifest


def verify_imports(root=ROOT):
    root = Path(root).resolve()
    if Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve() != root:
        raise ValueError("Monorepo root must be a Git repository root")
    if git(root, "rev-parse", "--show-object-format").strip() != b"sha1":
        raise ValueError("This import manifest declares SHA-1 source repositories")
    result = {}
    for module in load_manifest(root)["modules"]:
        prefix, revision, tree = module["path"], module["import_revision"], module["import_tree"]
        git(root, "merge-base", "--is-ancestor", revision, "HEAD")
        for history in module.get("additional_history_roots", []):
            git(root, "merge-base", "--is-ancestor", history, "HEAD")
        if module["runtime_revision"] is not None:
            _retained(root, module["runtime_revision"], module)
        if git(root, "rev-parse", revision + "^{tree}").decode().strip() != tree:
            raise ValueError("Original source tree does not match declared import")
        if git(root, "rev-parse", "HEAD:" + prefix).decode().strip() != tree:
            raise ValueError("Imported subtree differs from the original source tree")
        # Hash actual working bytes as well: Git index flags must not hide drift.
        for entry in git(root, "ls-tree", "-r", "-z", revision).split(b"\0"):
            if not entry:
                continue
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, expected = metadata.decode("ascii").split()
            path = root / prefix / os.fsdecode(raw_name)
            if kind != "blob" or mode not in {"100644", "100755"} or path.is_symlink() or not path.is_file():
                raise ValueError("Imported files must retain their regular-file types")
            if os.name == "posix" and bool(path.stat().st_mode & 0o111) != (mode == "100755"):
                raise ValueError("Imported executable mode changed")
            data = path.read_bytes()
            if sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest() != expected:
                raise ValueError("Imported working file differs from the preserved source")
        git(root, "diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv", "HEAD", "--", prefix)
        for raw_name in git(root, "ls-files", "--others", "-z", "--", prefix).split(b"\0"):
            if raw_name and not _generated_file(root / os.fsdecode(raw_name), root / prefix, environments=True, package_metadata=True):
                raise ValueError("Unexpected untracked file inside an imported module")
        result[module["role"]] = tree
    # All qualification lanes copy or execute Terminal tooling as well as the
    # providers. A clean imported subtree cannot authorize dirty root sources.
    verify_terminal_source(root)
    return result


def verify_terminal_source(root=ROOT):
    """Bind the actual tracked checkout and executable source paths to HEAD.

    This audit reads committed objects, the index and actual file bytes rather
    than trusting Git status, which can hide changes behind index flags. Gate
    output may live outside the executable source directories; generated Python
    caches and package metadata are also permitted within those directories.
    """
    root = Path(root).resolve()
    if Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve() != root:
        raise ValueError("Terminal source must be a Git repository root")
    if git(root, "rev-parse", "--show-object-format").strip() != b"sha1":
        raise ValueError("Terminal source qualification requires SHA-1 Git objects")
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    tree = git(root, "rev-parse", revision + "^{tree}").decode().strip()
    entries = []
    for entry in git(root, "ls-tree", "-r", "-z", revision).split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, expected = metadata.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Terminal tracked source must retain regular-file types")
        entries.append((raw_name, mode, expected))
    committed_index = sorted((name, mode, expected, "0") for name, mode, expected in entries)
    observed_index = []
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if entry:
            metadata, raw_name = entry.split(b"\t", 1)
            mode, expected, stage = metadata.decode("ascii").split()
            observed_index.append((raw_name, mode, expected, stage))
    if sorted(observed_index) != committed_index:
        raise ValueError("Terminal index differs from the reported source revision")
    for raw_name, mode, expected in entries:
        path = root / os.fsdecode(raw_name)
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError("Terminal tracked source changed type or traverses a symlink")
        if os.name == "posix" and bool(path.stat().st_mode & 0o111) != (mode == "100755"):
            raise ValueError("Terminal tracked executable mode differs from its source revision")
        data = path.read_bytes()
        if sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest() != expected:
            raise ValueError("Terminal tracked working bytes differ from the reported source revision")
    # Do not apply exclude-standard: ignored shadow modules are executable too.
    for raw_name in git(root, "ls-files", "--others", "-z", "--", "src", "scripts", "tests").split(b"\0"):
        if not raw_name:
            continue
        relative = Path(os.fsdecode(raw_name))
        boundary = root / relative.parts[0]
        if _generated_file(root / relative, boundary, package_metadata=relative.parts[0] == "src"):
            continue
        raise ValueError("Terminal executable source contains an untracked file: " + os.fsdecode(raw_name))
    if git(root, "rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("Terminal source revision changed during qualification")
    return {"revision": revision, "source_tree": tree}


@contextmanager
def provider_worktrees(root=ROOT, revisions="runtime", roles=None, overrides=None):
    """Yield explicit original-pin bindings, then remove their Git worktrees."""
    if revisions not in {"runtime", "import"}:
        raise ValueError("revisions must be runtime or import")
    root = Path(root).resolve()
    verify_imports(root)
    modules = load_manifest(root)["modules"]
    selected = ({module["role"] for module in modules if revisions == "import" or module["runtime_revision"] is not None}
                if roles is None else set(roles))
    if not selected or not selected <= set(_PATHS):
        raise ValueError("Select nonempty registered provider roles")
    overrides = {} if overrides is None else dict(overrides)
    if not set(overrides) <= selected:
        raise ValueError("Revision overrides must refer to selected roles")
    modules = [module for module in modules if module["role"] in selected]
    for module in modules:
        revision = overrides.get(module["role"], module[revisions + "_revision"])
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Provider revisions must be full source commit identities")
        # Overrides are explicit operator bindings for existing exchange pins,
        # never executable instructions taken from persisted investigations.
        _retained(root, revision, module)
    created = []
    with tempfile.TemporaryDirectory(prefix="notations-providers-") as temporary:
        providers = {}
        try:
            for module in modules:
                path = Path(temporary) / module["role"]
                revision = overrides.get(module["role"], module[revisions + "_revision"])
                with worktree_mutation_lock(root):
                    git(root, "worktree", "add", "--detach", str(path), revision)
                created.append(path)
                providers[module["role"]] = path
            yield providers
        finally:
            for path in reversed(created):
                with worktree_mutation_lock(root):
                    git(root, "worktree", "remove", "--force", str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps({"schema": "notations.monorepo-audit.v1", "imports": verify_imports(args.root)}, indent=2))


if __name__ == "__main__":
    main()
