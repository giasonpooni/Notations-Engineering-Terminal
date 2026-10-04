from copy import deepcopy
import json

import pytest

from ciw.core.identities import content_identity
from ciw.legibility import compile_bundle
from ciw.legibility_cli import main
from ciw.legibility_compare import compare_bundles
from ciw.legibility_workflow import demo_source


@pytest.fixture
def source(tmp_path):
    return demo_source(tmp_path / "fixture")[1]


def test_comparison_preserves_uncertainty_and_identifies_claim_changes(source):
    newer = deepcopy(source)
    newer["object"]["version"] = "2"
    newer["claims"][0]["status"] = "unresolved"
    newer["semantics"]["properties"][0]["uncertainty"] = {
        "status": "declared", "standard_uncertainty": 0.5}
    result = compare_bundles(compile_bundle(source), compile_bundle(newer))
    assert result["changes"][0]["path"][0] == "claims"
    assert any("uncertainty" in change["path"] for change in result["changes"])
    assert result["physical_validation_status"] == "not_assessed"
    assert result["canonical_admission"] is False
    assert not result["same_version_content_conflict"]
    assert result["comparison_id"] == content_identity({k: v for k, v in result.items() if k != "comparison_id"})


def test_id_lists_ignore_reordering_but_source_digest_still_changes(source):
    newer = deepcopy(source)
    newer["claims"].reverse()
    result = compare_bundles(compile_bundle(source), compile_bundle(newer))
    assert result["changes"] == []
    assert result["source_changed"]
    assert result["same_version_content_conflict"]


def test_comparison_records_additions_and_removals(source):
    newer = deepcopy(source)
    removed = newer["claims"].pop()
    newer["claims"].append({**removed, "claim_id": "new-claim"})
    result = compare_bundles(compile_bundle(source), compile_bundle(newer))
    assert {change["kind"] for change in result["changes"]} == {"added", "removed"}


def test_comparison_refuses_tampering_and_different_objects(source):
    before = compile_bundle(source)
    tampered = deepcopy(before)
    tampered["representations"]["human"]["label"] = "tampered"
    with pytest.raises(ValueError, match="intact"):
        compare_bundles(before, tampered)
    newer = deepcopy(source)
    newer["object"]["object_id"] = "another-object"
    with pytest.raises(ValueError, match="same object"):
        compare_bundles(before, compile_bundle(newer))


def test_cli_conflict_exit_and_exclusive_output(source, tmp_path, capsys):
    newer = deepcopy(source)
    newer["object"]["label"] = "changed label"
    paths = [tmp_path / "before.json", tmp_path / "after.json"]
    for path, contract in zip(paths, (source, newer)):
        path.write_text(json.dumps(compile_bundle(contract)))
    output = tmp_path / "comparison.json"
    assert main(["compare", *map(str, paths), "--output", str(output)]) == 2
    assert json.loads(output.read_text())["same_version_content_conflict"]
    capsys.readouterr()
    assert main(["compare", *map(str, paths), "--output", str(output)]) == 2
    assert "File exists" in capsys.readouterr().err
