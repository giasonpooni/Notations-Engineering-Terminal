from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("operator_artifact", Path(__file__).resolve().parents[1] / "operator_artifact.py")
artifact = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(artifact)
REVISION = "a" * 40
NAME = "computational_instrumentation_workbench-0.1.0-py3-none-any.whl"
PACKAGE = {"__init__.py": b"# installed\n", "resources/model.json": b'{"value": 1}\n'}


def make_wheel(path, entries=None):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in (entries if entries is not None else [("ciw/" + k, v) for k, v in PACKAGE.items()]):
            info = zipfile.ZipInfo(name)
            # ZipInfo normalizes host separators on Windows; retain the exact
            # malicious archive spelling so refusal fixtures test the reader.
            info.filename = name
            archive.writestr(info, data)


@pytest.fixture
def setup(tmp_path):
    wheels = tmp_path / "wheel"
    wheels.mkdir()
    wheel = wheels / NAME
    make_wheel(wheel)
    manifest = wheels / "operator-wheel.json"
    sealed = artifact.seal(wheels, manifest, REVISION)
    installed = tmp_path / "environment" / "ciw"
    for name, data in PACKAGE.items():
        target = installed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return SimpleNamespace(wheels=wheels, wheel=wheel, manifest=manifest,
                           sealed=sealed, installed=installed, tmp=tmp_path)


def verify(setup, **kwargs):
    return artifact.verify(setup.wheels, setup.manifest, REVISION,
                           expected_sha256=setup.sealed["wheel_sha256"], **kwargs)


def rewrite(setup, **changes):
    value = dict(setup.sealed)
    value.update(changes)
    setup.manifest.write_text(json.dumps(value), encoding="utf-8")


def test_sealed_artifact_and_exact_installed_resource_closure(setup):
    report = verify(setup, package_root=setup.installed)
    assert report["status"] == "passed"
    assert report["wheel_sha256"] == hashlib.sha256(setup.wheel.read_bytes()).hexdigest()
    assert report["ciw_files"] == 2 and report["installed_package_checked"] is True
    assert report["scientific_qualification"] == "not_performed"
    assert report["source_to_binary_attestation"] == "not_performed"


def test_artifact_only_does_not_claim_installed_check(setup):
    assert verify(setup)["installed_package_checked"] is False


def test_manifest_is_create_only(setup):
    before = setup.manifest.read_bytes()
    with pytest.raises(FileExistsError):
        artifact.seal(setup.wheels, setup.manifest, REVISION)
    assert setup.manifest.read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"candidate_revision": "b" * 40}, {"wheel_sha256": "0" * 64},
    {"wheel": "../" + NAME}, {"wheel_bytes": True}, {"wheel_bytes": 1},
    {"schema": "other.v1"}, {"unexpected": "field"},
])
def test_manifest_identity_and_fields_are_exact(setup, changes):
    rewrite(setup, **changes)
    with pytest.raises(ValueError):
        verify(setup)


@pytest.mark.parametrize("text", [
    '{"schema":"net.operator-wheel.v1","schema":"net.operator-wheel.v1"}',
    '{"wheel_bytes":NaN}', '{"wheel_bytes":Infinity}', '[]', '{}',
])
def test_manifest_strict_json(setup, text):
    setup.manifest.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        verify(setup)


def test_wrong_external_revision_and_digest_are_refused(setup):
    with pytest.raises(ValueError):
        artifact.verify(setup.wheels, setup.manifest, "b" * 40,
                        expected_sha256=setup.sealed["wheel_sha256"])
    with pytest.raises(ValueError):
        artifact.verify(setup.wheels, setup.manifest, REVISION, expected_sha256="f" * 64)


def test_resealed_other_wheel_still_fails_independent_digest(setup):
    make_wheel(setup.wheel, [("ciw/__init__.py", b"changed\n")])
    rewrite(setup, wheel_sha256=hashlib.sha256(setup.wheel.read_bytes()).hexdigest(),
            wheel_bytes=setup.wheel.stat().st_size)
    with pytest.raises(ValueError, match="independent wheel digest"):
        verify(setup)


def test_archive_tamper_without_reseal(setup):
    setup.wheel.write_bytes(setup.wheel.read_bytes() + b"unexpected")
    with pytest.raises(ValueError, match="wheel bytes"):
        verify(setup)


@pytest.mark.parametrize("mutation", ["modified", "missing", "extra", "orphan-cache"])
def test_installed_package_drift_is_refused(setup, mutation):
    if mutation == "modified":
        (setup.installed / "resources/model.json").write_bytes(b"different")
    elif mutation == "missing":
        (setup.installed / "__init__.py").unlink()
    elif mutation == "extra":
        (setup.installed / "shadow.py").write_bytes(b"# unexpected\n")
    else:
        cache = setup.installed / "__pycache__"
        cache.mkdir()
        (cache / "shadow.cpython-312.pyc").write_bytes(b"cache")
    with pytest.raises(ValueError):
        verify(setup, package_root=setup.installed)


@pytest.mark.parametrize("path", [
    "__pycache__/__init__.cpython-312.pyc",
    "__pycache__/__init__.cpython-312.opt-1.pyc",
    "__init__.pyc",
    "__PYCACHE__/__init__.CPYTHON-312.PYC",
    "__init__.PYC",
])
def test_even_source_backed_executable_caches_are_refused(setup, path):
    cache = setup.installed / path
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"unverified executable cache")
    with pytest.raises(ValueError, match="caches"):
        verify(setup, package_root=setup.installed)


@pytest.mark.parametrize("path", [
    "ciw/../outside.py", "/ciw/absolute.py", "ciw\\windows.py", "ciw/C:drive.py",
    "ciw//empty.py", "ciw/./dot.py", "ciw/trailing. ", "CIW/shadow.py",
    "x.data/purelib/ciw/shadow.py", "ciw/__pycache__/x.cpython-312.pyc",
    "x.DATA/purelib/ciw/shadow.py", "ciw/__PYCACHE__/x.CPYTHON-312.PYC",
    "ciw/x.PYC", "ciw/resources/NUL.json", "ciw/CON.py", "ciw/COM1.txt",
    "ciw/lpt9/entry.py", "ciw/COM¹.txt", "ciw/control\x01.py",
    "ciw/question?.json", "ciw/wildcard*.json",
])
def test_ambiguous_or_relocated_wheel_paths_refused(tmp_path, path):
    make_wheel(tmp_path / NAME, [("ciw/__init__.py", b"ok"), (path, b"bad")])
    with pytest.raises(ValueError):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)


def test_case_collision_refused_on_every_platform(tmp_path):
    make_wheel(tmp_path / NAME, [("ciw/__init__.py", b"ok"), ("ciw/A.py", b"a"), ("ciw/a.py", b"b")])
    with pytest.raises(ValueError, match="ambiguous"):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)


def test_duplicate_archive_entry_refused(tmp_path):
    with pytest.warns(UserWarning, match="Duplicate name"):
        make_wheel(tmp_path / NAME, [("ciw/__init__.py", b"ok"), ("ciw/__init__.py", b"bad")])
    with pytest.raises(ValueError, match="duplicate"):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)


def test_multiple_or_nonportable_wheels_refused(setup):
    other = setup.wheels / "computational_instrumentation_workbench-0.2.0-py3-none-any.whl"
    other.write_bytes(setup.wheel.read_bytes())
    with pytest.raises(ValueError, match="exactly one"):
        verify(setup)
    other.unlink()
    setup.wheel.rename(setup.wheels / "computational_instrumentation_workbench-0.1.0-cp312-cp312-win_amd64.whl")
    with pytest.raises(ValueError, match="portable"):
        verify(setup)


@pytest.mark.parametrize("revision", ["main", "a" * 39, "A" * 40, "a" * 41])
def test_revision_must_be_immutable(tmp_path, revision):
    make_wheel(tmp_path / NAME)
    with pytest.raises(ValueError):
        artifact.seal(tmp_path, tmp_path / "manifest.json", revision)


def bind_distribution(monkeypatch, setup, *, editable=False, origin=None):
    class Distribution:
        def locate_file(self, name):
            assert name == "ciw"
            return setup.installed

        def read_text(self, name):
            assert name == "direct_url.json"
            return json.dumps({"dir_info": {"editable": editable}})

    monkeypatch.setattr(artifact.importlib.metadata, "distribution", lambda name: Distribution())
    monkeypatch.setattr(artifact.importlib.util, "find_spec", lambda name:
                        SimpleNamespace(origin=str(origin or setup.installed / "__init__.py")))


@pytest.mark.parametrize("mode", ["editable", "shadow", "source"])
def test_installed_origin_refusals(setup, monkeypatch, mode):
    bind_distribution(monkeypatch, setup, editable=mode == "editable",
                      origin=setup.tmp / "shadow/__init__.py" if mode == "shadow" else None)
    source = setup.installed.parent if mode == "source" else setup.tmp / "checkout"
    with pytest.raises(ValueError):
        artifact.verify_installed(setup.wheels, setup.manifest, REVISION,
            expected_sha256=setup.sealed["wheel_sha256"], source_root=source)


def test_regular_installed_distribution_qualifies_bytes_only(setup, monkeypatch):
    bind_distribution(monkeypatch, setup)
    result = artifact.verify_installed(setup.wheels, setup.manifest, REVISION,
        expected_sha256=setup.sealed["wheel_sha256"], source_root=setup.tmp / "checkout")
    assert result["installed_package_checked"] is True
    assert result["scientific_qualification"] == "not_performed"


def test_archive_symlink_is_refused_without_host_symlink_support(tmp_path):
    with zipfile.ZipFile(tmp_path / NAME, "w") as archive:
        archive.writestr("ciw/__init__.py", b"ok")
        link = zipfile.ZipInfo("ciw/shadow.py")
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"target")
    with pytest.raises(ValueError, match="symlinks"):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.parametrize("unrelated_wheel", [False, True])
def test_missing_package_or_wheel_does_not_create_a_manifest(tmp_path, unrelated_wheel):
    if unrelated_wheel:
        make_wheel(tmp_path / NAME, [("unrelated/package.py", b"ok")])
    with pytest.raises(ValueError):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.parametrize("entries", [
    [("ciw/A/x.py", b"x"), ("ciw/a/y.py", b"y")],
    [("ciw/resources/Model/x.py", b"x"), ("ciw/resources/model/y.py", b"y")],
])
def test_directory_case_aliases_are_refused_before_installation(tmp_path, entries):
    make_wheel(tmp_path / NAME, [("ciw/__init__.py", b"ok"), *entries])
    with pytest.raises(ValueError, match="ambiguous.*prefix"):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)


@pytest.mark.parametrize("reverse", [False, True])
def test_file_directory_prefix_collisions_are_refused_in_either_order(tmp_path, reverse):
    entries = [("ciw/node", b"file"), ("ciw/node/child.py", b"child")]
    if reverse:
        entries.reverse()
    make_wheel(tmp_path / NAME, [("ciw/__init__.py", b"ok"), *entries])
    with pytest.raises(ValueError, match="ambiguous.*prefix"):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)


def test_explicit_directory_after_its_children_is_not_an_alias(tmp_path):
    make_wheel(tmp_path / NAME, [
        ("ciw/__init__.py", b"ok"), ("ciw/resources/model.json", b"{}"),
        ("ciw/resources/", b""), ("ciw/", b"")])
    artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)


def test_implicit_directory_tree_obeys_the_entry_budget(tmp_path, monkeypatch):
    make_wheel(tmp_path / NAME, [
        ("ciw/__init__.py", b"ok"), ("ciw/a/b/c/model.json", b"{}")])
    monkeypatch.setattr(artifact, "MAX_WHEEL_ENTRIES", 4)
    with pytest.raises(ValueError, match="prefix budget"):
        artifact.seal(tmp_path, tmp_path / "manifest.json", REVISION)


def test_unexpected_installed_file_is_rejected_before_reading_its_bytes(setup, monkeypatch):
    extra = setup.installed / "unexpected.bin"
    extra.write_bytes(b"unrelated")
    original_hash = artifact._hash

    def guarded_hash(path):
        assert path != extra, "unexpected installed bytes were read"
        return original_hash(path)

    monkeypatch.setattr(artifact, "_hash", guarded_hash)
    with pytest.raises(ValueError, match="closure differs"):
        verify(setup, package_root=setup.installed)


def test_oversized_installed_file_is_rejected_before_hashing(setup, monkeypatch):
    files = artifact._package(setup.wheel)
    oversized = setup.installed / "__init__.py"
    oversized.write_bytes(b"x" * 33)
    monkeypatch.setattr(artifact, "MAX_PACKAGE_BYTES", 32)
    original_hash = artifact._hash

    def guarded_hash(path):
        assert path != oversized, "oversized installed bytes were read"
        return original_hash(path)

    monkeypatch.setattr(artifact, "_hash", guarded_hash)
    with pytest.raises(ValueError, match="byte budget"):
        artifact._installed(files, setup.installed)


def test_installed_total_is_bounded_before_reading_another_file(setup, monkeypatch):
    files = artifact._package(setup.wheel)
    budget = max(len(data) for data in PACKAGE.values())
    monkeypatch.setattr(artifact, "MAX_PACKAGE_BYTES", budget)
    original_hash = artifact._hash
    read_bytes = 0

    def guarded_hash(path):
        nonlocal read_bytes
        read_bytes += path.stat().st_size
        assert read_bytes <= budget, "installed hashing exceeded its byte budget"
        return original_hash(path)

    monkeypatch.setattr(artifact, "_hash", guarded_hash)
    with pytest.raises(ValueError, match="byte budget"):
        artifact._installed(files, setup.installed)


def test_installed_empty_directories_cannot_exhaust_traversal_budget(setup, monkeypatch):
    files = artifact._package(setup.wheel)
    # The valid installed fixture has two files and one implicit directory.
    monkeypatch.setattr(artifact, "MAX_WHEEL_ENTRIES", 3)
    (setup.installed / "extra-directory").mkdir()
    with pytest.raises(ValueError, match="entry budget"):
        artifact._installed(files, setup.installed)
