"""Qualification evidence must name an actual immutable OCI image/runtime."""
import importlib.util
from pathlib import Path

import pytest


_PATH = Path(__file__).parents[1] / "scripts" / "qualify_system_oci.py"
_SPEC = importlib.util.spec_from_file_location("qualify_system_oci", _PATH)
qualification = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(qualification)


@pytest.mark.parametrize("inspection", [
    {"Id": "sha256:" + "1" * 64, "RepoDigests": []},
    {"RepoDigests": ["worker:latest"]},
    {"RepoDigests": ["worker@sha256:" + "1" * 64, "worker@sha256:" + "2" * 64]},
])
def test_image_id_mutable_tag_or_ambiguous_digest_cannot_select_qualified_image(inspection):
    with pytest.raises(RuntimeError, match="unique immutable repository digest"):
        qualification._repository_digest(inspection)


def test_selected_local_fixture_repository_must_match_observed_digest():
    expected = "localhost:54931/net-system-reference@sha256:" + "a" * 64
    observed = {"RepoDigests": ["different-repository@sha256:" + "b" * 64, expected]}
    assert qualification._repository_digest(observed, "localhost:54931/net-system-reference") == expected
    with pytest.raises(RuntimeError):
        qualification._repository_digest(observed, "localhost:54932/net-system-reference")


def test_missing_docker_retains_failed_qualification_and_runs_no_alternative_engine(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(qualification.shutil, "which", lambda name: None)
    def unexpected(*args, **kwargs):
        raise AssertionError("Unavailable OCI qualification must not launch another backend")
    monkeypatch.setattr(qualification.subprocess, "run", unexpected)
    with pytest.raises(RuntimeError, match="requires Docker"):
        qualification.qualify(tmp_path)
    report = json.loads((tmp_path / "checks.json").read_text())
    assert report["status"] == "FAIL"
    assert report["actual_oci_qualification"] == "incomplete"
    assert report["physical_validation_status"] == "not_assessed"
    assert report["canonical_admission"] is False
    assert not (tmp_path / "session").exists()
