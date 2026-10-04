"""Bind an operator qualification run to one portable wheel and installed bytes.

The declared candidate revision is a build-workflow binding. This helper does
not attest source-to-binary correspondence or grant scientific authority.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

SCHEMA = "net.operator-wheel.v1"
FIELDS = {"schema", "candidate_revision", "wheel", "wheel_sha256", "wheel_bytes"}
WHEEL_NAME = re.compile(r"computational_instrumentation_workbench-[A-Za-z0-9_.+]+-py3-none-any\.whl\Z")
MAX_WHEEL_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_WHEEL_ENTRIES = 20000


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _revision(value: str) -> None:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None,
            "candidate revision must be an exact lowercase Git SHA")


def _digest(value: str) -> None:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "expected wheel digest must be an exact lowercase SHA-256")


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON field: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite JSON value: " + value)


def _json(text: str):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


def _wheel(directory: Path) -> Path:
    require(directory.is_dir() and not directory.is_symlink(), "wheel directory must be a real directory")
    wheels = list(directory.glob("*.whl"))
    require(len(wheels) == 1, "exactly one wheel is required")
    wheel = wheels[0]
    require(wheel.is_file() and not wheel.is_symlink(), "wheel must be a regular file")
    require(WHEEL_NAME.fullmatch(wheel.name) is not None, "expected a portable CIW py3-none-any wheel")
    require(0 < wheel.stat().st_size <= MAX_WHEEL_BYTES, "wheel size is outside the artifact budget")
    return wheel


def _package(wheel: Path) -> dict[str, str]:
    files = {}
    seen = set()
    prefixes = {}
    total = 0
    with zipfile.ZipFile(wheel) as archive:
        require(len(archive.infolist()) <= MAX_WHEEL_ENTRIES, "wheel entry budget exceeded")
        for item in archive.infolist():
            # ZipInfo may normalize Windows separators or truncate NULs.
            # Validate the original archive spelling before trusting filename.
            name = item.orig_filename
            require(name and "\\" not in name and not any(ord(char) < 32 or char in '<>:"|?*' for char in name),
                    "invalid wheel path")
            path = PurePosixPath(name)
            require(not path.is_absolute() and all(part not in ("", ".", "..")
                    and part == part.rstrip(" .") for part in name.rstrip("/").split("/")),
                    "invalid wheel path")
            require(not any(re.fullmatch(r"(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])",
                                         part.split(".", 1)[0].rstrip(" ").casefold())
                            for part in path.parts), "reserved Windows device path")
            key = name.rstrip("/").casefold()
            require(key not in seen, "duplicate or platform-ambiguous wheel path")
            seen.add(key)
            # Implicit directories must have one spelling and cannot also be
            # files, including when a later entry declares the directory.
            parts = name.rstrip("/").split("/")
            for index in range(len(parts)):
                prefix = "/".join(parts[:index + 1])
                node = (prefix, index < len(parts) - 1 or item.is_dir())
                prior = prefixes.setdefault(prefix.casefold(), node)
                require(prior == node, "platform-ambiguous wheel path prefix")
                require(len(prefixes) <= MAX_WHEEL_ENTRIES, "wheel path-prefix budget exceeded")
            require(not stat.S_ISLNK(item.external_attr >> 16), "wheel symlinks are unsupported")
            require(not any(part.casefold().endswith(".data") for part in path.parts),
                    "wheel .data relocation is unsupported")
            require(not name.casefold().startswith("ciw/") or name.startswith("ciw/"),
                    "platform-ambiguous CIW package root")
            if item.is_dir() or not name.startswith("ciw/"):
                continue
            relative = name[len("ciw/"):]
            require("__pycache__" not in [part.casefold() for part in path.parts]
                    and not relative.casefold().endswith(".pyc"),
                    "wheel must not contain Python caches")
            total += item.file_size
            require(0 <= item.file_size <= MAX_PACKAGE_BYTES and total <= MAX_PACKAGE_BYTES,
                    "wheel package byte budget exceeded")
            files[relative] = hashlib.sha256(archive.read(item)).hexdigest()
    require("__init__.py" in files, "wheel does not contain the CIW package")
    return files


def _installed(files: dict[str, str], root: Path) -> None:
    require(root.is_dir() and not root.is_symlink(), "installed CIW root must be a real directory")
    actual = {}
    total = 0
    entries = 0
    for path in root.rglob("*"):
        entries += 1
        require(entries <= MAX_WHEEL_ENTRIES, "installed CIW entry budget exceeded")
        require(not path.is_symlink(), "installed CIW symlinks are unsupported")
        relative = path.relative_to(root)
        require("__pycache__" not in [part.casefold() for part in relative.parts]
                and not relative.name.casefold().endswith(".pyc"),
                "installed CIW caches are unsupported; install with --no-compile and execute with -B")
        if path.is_dir():
            continue
        require(path.is_file(), "installed CIW contains a nonregular file")
        name = relative.as_posix()
        require(name in files, "installed CIW package closure differs from the exact wheel")
        size = path.stat().st_size
        total += size
        require(0 <= size <= MAX_PACKAGE_BYTES and total <= MAX_PACKAGE_BYTES,
                "installed CIW package byte budget exceeded")
        actual[name] = _hash(path)
    require(actual == files, "installed CIW package closure differs from the exact wheel")


def seal(wheel_dir: Path, manifest_path: Path, revision: str) -> dict:
    _revision(revision)
    wheel = _wheel(wheel_dir)
    _package(wheel)
    manifest = {"schema": SCHEMA, "candidate_revision": revision, "wheel": wheel.name,
                "wheel_sha256": _hash(wheel), "wheel_bytes": wheel.stat().st_size}
    with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return manifest


def verify(wheel_dir: Path, manifest_path: Path, revision: str, *,
           expected_sha256: str, package_root: Path | None = None) -> dict:
    _revision(revision)
    _digest(expected_sha256)
    require(manifest_path.is_file() and not manifest_path.is_symlink(), "manifest must be a regular file")
    require(manifest_path.stat().st_size <= 16384, "manifest size budget exceeded")
    manifest = _json(manifest_path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict) and set(manifest) == FIELDS, "unexpected manifest fields")
    require(manifest["schema"] == SCHEMA, "unsupported artifact manifest schema")
    require(manifest["candidate_revision"] == revision, "candidate revision mismatch")
    require(manifest["wheel_sha256"] == expected_sha256, "independent wheel digest mismatch")
    require(type(manifest["wheel_bytes"]) is int, "wheel byte count must be an integer")
    wheel = _wheel(wheel_dir)
    require(manifest["wheel"] == wheel.name, "wheel filename mismatch")
    require(manifest["wheel_bytes"] == wheel.stat().st_size and _hash(wheel) == expected_sha256,
            "wheel bytes differ from the declared artifact")
    files = _package(wheel)
    if package_root is not None:
        _installed(files, package_root)
    return {"schema": "net.operator-artifact-check.v1", "status": "passed",
            "candidate_revision": revision, "wheel": wheel.name,
            "wheel_sha256": expected_sha256, "wheel_bytes": wheel.stat().st_size,
            "ciw_files": len(files), "installed_package_checked": package_root is not None,
            "source_to_binary_attestation": "not_performed",
            "scientific_qualification": "not_performed"}


def verify_installed(wheel_dir: Path, manifest_path: Path, revision: str, *,
                     expected_sha256: str, source_root: Path | None = None) -> dict:
    distribution = importlib.metadata.distribution("computational-instrumentation-workbench")
    spec = importlib.util.find_spec("ciw")
    require(spec is not None and spec.origin is not None, "installed CIW module is unavailable")
    root = Path(distribution.locate_file("ciw"))
    require(Path(spec.origin).resolve() == (root / "__init__.py").resolve(),
            "imported CIW differs from the installed distribution")
    direct = distribution.read_text("direct_url.json")
    if direct:
        origin = _json(direct)
        require(isinstance(origin, dict) and not origin.get("dir_info", {}).get("editable", False),
                "editable installations are not qualified")
    if source_root is not None:
        source = source_root.resolve()
        require(root.resolve() != source and source not in root.resolve().parents,
                "installed CIW resolves inside the source checkout")
    result = verify(wheel_dir, manifest_path, revision, expected_sha256=expected_sha256,
                    package_root=root)
    result["installed_package"] = str(root.resolve())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("seal", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--wheel-dir", required=True, type=Path)
        command.add_argument("--manifest", required=True, type=Path)
        command.add_argument("--revision", required=True)
        if name == "verify":
            command.add_argument("--sha256", required=True)
            command.add_argument("--installed", action="store_true")
            command.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "seal":
            result = seal(args.wheel_dir, args.manifest, args.revision)
        else:
            require(args.source_root is None or args.installed, "source-root requires installed verification")
            if args.installed:
                result = verify_installed(args.wheel_dir, args.manifest, args.revision,
                                          expected_sha256=args.sha256, source_root=args.source_root)
            else:
                result = verify(args.wheel_dir, args.manifest, args.revision, expected_sha256=args.sha256)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile,
            importlib.metadata.PackageNotFoundError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
