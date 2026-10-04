"""The capability index names existing surfaces and neither executes nor qualifies them."""
import json
import subprocess
import sys
from pathlib import Path

from ciw.adapters.protocol import AdapterRefusal
from ciw.capabilities import catalog, get
from ciw.cli import main, parser


ROOT = Path(__file__).resolve().parents[1]
INDEXED = (
    "synthetic-oscillator-demo",
    "statistics.v1",
    "spectrum.periodogram.v1",
    "legibility.compile.v1",
    "system.compile.v1",
    "system.simulate.v1",
    "system.verify.v1",
    "system.compare.v1",
    "system.study.v1",
    "learning.oscillator-rms",
    "doctor.preflight",
    "linear-response.library",
)


def test_index_does_not_authorize_qualify_or_close_discovery():
    index = catalog()
    assert index["schema"] == "ciw.capability-catalog.v1"
    assert index["status"] == "index_only"
    assert index["authorizes_execution"] is False
    assert index["qualification"] == "not_performed"
    assert index["closes_shared_discovery"] is False
    assert index["discovers_private_providers"] is False
    assert "device gateway" in index["not_indexed"]
    assert "shared profile and capability discovery" in index["not_indexed"]
    ids = [item["capability_id"] for item in index["capabilities"]]
    assert ids == list(INDEXED)
    for item in index["capabilities"]:
        assert item["disposition"] == "implemented"
        assert item["authorizes_execution"] is False
        assert item["qualification"] == "not_performed"
        assert item["requires_private_checkout"] is False
    assert "device-gateway" not in ids
    assert "typed-composition" not in ids


def test_records_are_detached_copies():
    record = get("statistics.v1")
    record["authorizes_execution"] = True
    catalog()["capabilities"][0]["qualification"] = "qualified"
    assert get("statistics.v1")["authorizes_execution"] is False
    assert catalog()["qualification"] == "not_performed"
    assert catalog()["capabilities"][0]["qualification"] == "not_performed"


def test_unknown_id_refuses_without_executing():
    for capability_id in ("not-a-capability", "", "device-gateway", None, 1):
        try:
            get(capability_id)
        except AdapterRefusal as exc:
            assert exc.code == "capability_unavailable"
        else:
            raise AssertionError(capability_id)


def test_registered_operations_match_the_default_registry():
    from ciw.operations.registry import default_registry
    registry = default_registry()
    described = {item["operation_id"]: item["role"] for item in registry.describe()}
    indexed = {item["operation_id"]: item for item in catalog()["capabilities"]
               if item["kind"] == "registered_operation"}
    assert set(indexed) == set(described)
    for operation_id, item in indexed.items():
        operation = registry.get(operation_id)
        assert item["role"] == described[operation_id] == operation.role
        assert item["runtime_identity"] == operation.runtime_identity()
        assert item["capability_id"] == operation_id


def test_named_profiles_and_lesson_match_their_owners():
    from ciw.doctor import PROFILES
    from ciw.learning import TOPIC
    from ciw.linear_response import PROFILES as RESPONSE_PROFILES
    assert tuple(get("doctor.preflight")["profiles"]) == PROFILES
    assert tuple(get("linear-response.library")["profiles"]) == RESPONSE_PROFILES
    assert get("linear-response.library")["command"] is None
    assert get("linear-response.library")["module"] == "ciw.linear_response"
    assert get("learning.oscillator-rms")["topic"] == TOPIC


def test_indexed_commands_exist_and_the_library_has_no_cli():
    choices = parser()._subparsers._group_actions[0].choices
    for name in ("demo", "analyze", "doctor", "math", "capabilities"):
        assert name in choices
    operation = next(action for action in choices["analyze"]._actions if action.dest == "operation")
    assert list(operation.choices) == ["stats", "spectrum"]
    math_choices = choices["math"]._subparsers._group_actions[0].choices
    assert list(get("learning.oscillator-rms")["lesson_commands"]) == list(math_choices)
    capability_choices = choices["capabilities"]._subparsers._group_actions[0].choices
    assert set(capability_choices) == {"list", "show"}
    try:
        parser().parse_args(["linear-response"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("linear response must not grow a CLI from the index")


def test_cli_lists_and_refuses(capsys):
    assert main(["capabilities"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == catalog()
    assert main(["capabilities", "list"]) == 0
    assert json.loads(capsys.readouterr().out)["closes_shared_discovery"] is False
    assert main(["capabilities", "show", "doctor.preflight"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["capability_id"] == "doctor.preflight"
    assert shown["qualification"] == "not_performed"
    assert main(["capabilities", "show", "not-a-capability"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "capability_unavailable" in captured.err
    assert "not-a-capability" in captured.err


def test_import_and_known_lookup_do_not_load_execution_modules():
    script = r"""
import json, sys
import ciw.capabilities
ciw.capabilities.get("statistics.v1")
ciw.capabilities.catalog()
forbidden = {
    "ciw.linear_response", "ciw.session", "ciw.instruments", "ciw.learning",
    "ciw.doctor", "ciw.operations", "ciw.operations.registry",
    "ciw.adapters", "ciw.adapters.oscillator", "ciw.adapters.protocol",
    "ciw.adapters.registry", "ciw.server", "ciw.cli",
}
print(json.dumps(sorted(forbidden & set(sys.modules))))
"""
    completed = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == []


def test_refusal_does_not_load_scientific_modules():
    script = r"""
import json, sys
import ciw.capabilities
try:
    ciw.capabilities.get("not-a-capability")
except Exception as exc:
    assert type(exc).__name__ == "AdapterRefusal"
    assert exc.code == "capability_unavailable"
else:
    raise SystemExit("missing refusal")
forbidden = {
    "ciw.linear_response", "ciw.session", "ciw.instruments", "ciw.learning",
    "ciw.doctor", "ciw.operations.registry", "ciw.adapters.oscillator", "ciw.server",
}
print(json.dumps(sorted(forbidden & set(sys.modules))))
"""
    completed = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == []


def test_docs_leave_the_discovery_gap_open():
    page = (ROOT / "docs" / "CAPABILITIES.md").read_text(encoding="utf-8")
    gaps = (ROOT / "docs" / "DEVELOPMENT_GAPS.md").read_text(encoding="utf-8")
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "stays open" in page
    assert "not_performed" in page
    assert "CAPABILITIES.md" in index
    assert "| Shared profile/capability discovery |" in gaps
