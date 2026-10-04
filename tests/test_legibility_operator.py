"""Operator paths exercise retained artifacts with fresh verification."""
import json

import pytest

from ciw.legibility_cli import _publish
from ciw.legibility_workflow import demo_source
from ciw.net import main


@pytest.fixture
def published(tmp_path, capsys):
    pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    destination = tmp_path / "specimen"
    assert main(["legibility", "demo", "--output-dir", str(destination)]) == 0
    capsys.readouterr()
    return destination


def test_net_review_rechecks_bytes_and_does_not_reuse_publication_report(published, tmp_path, capsys):
    (published / "verification.json").write_text('{"signature_valid": true}')
    output = tmp_path / "fresh.html"
    assert main(["legibility", "review", str(published), "--trust",
                 str(published / "demo-trust.json"), "--expected-version", "1",
                 "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["artifact_status"] == report["export_status"] == "verified"
    assert report["signature_valid"] and report["issuer_trusted"]
    assert "Signature valid" in output.read_text(encoding="utf-8")
    assert report["physical_validation_status"] == "not_assessed"


def test_review_exposes_export_failure_and_refuses_overwrite(published, tmp_path, capsys):
    (published / "reasoning.json").write_text('{}')
    output = tmp_path / "failure.html"
    assert main(["legibility", "review", str(published), "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["export_status"] == "failed"
    assert "export_mismatch" in output.read_text(encoding="utf-8")
    assert "Export mismatch" in output.read_text(encoding="utf-8")
    original = output.read_bytes()
    assert main(["legibility", "review", str(published), "--output", str(output)]) == 2
    assert output.read_bytes() == original


def test_invalid_publication_does_not_leave_destination(tmp_path):
    _, contract, artifacts = demo_source(tmp_path / "fixture")
    artifacts[next(iter(artifacts))] = b"wrong bytes"
    destination = tmp_path / "invalid"
    with pytest.raises(ValueError, match="integrity"):
        _publish(destination, contract, artifacts)
    assert not destination.exists()


@pytest.mark.parametrize("profile", [[], ["crush"], ["plate"]])
def test_existing_impact_engine_to_legibility_operator_path(tmp_path, capsys, profile):
    request = tmp_path / "request.json"
    impact = tmp_path / "impact"
    bundle = tmp_path / "bundle"
    assert main(["impact", *profile, "example", "--output", str(request)]) == 0
    assert main(["impact", *profile, "run", str(request), "--output-dir", str(impact)]) == 0
    capsys.readouterr()
    assert main(["legibility", "import-impact", str(impact / "workspace.json"),
                 "--object-id", "test:specimen", "--version", "1",
                 "--output-dir", str(bundle)]) == 0
    capsys.readouterr()
    assert main(["legibility", "verify", str(bundle), "--expected-object-id",
                 "test:specimen", "--expected-version", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["content_intact"]
    assert report["artifact_status"] == report["export_status"] == "verified"
    assert report["physical_validation_status"] == "not_assessed"
