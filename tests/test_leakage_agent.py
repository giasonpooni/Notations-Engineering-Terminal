"""Explicit native leakage instrument over the existing NET agent/MCP boundary."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import pytest

from ciw.agent_api import encode, parse
from ciw.agent_mcp import VERSIONS, demo_config, from_profile, leakage_config, polymer_config
from ciw.control_plane import plan_graph
from ciw.core.identities import new_identity
from ciw.leakage_native import NativeLeakageBackend, PIN
from ciw.leakage_workflow import ASSESS, VERIFY, OPERATIONS, AUTHORITY, capability_registry
from ciw.operations.registry import default_registry
from ciw.operations.runner import seal
from ciw.session import Session


@pytest.fixture(scope="module")
def native():
    checkout = Path(os.environ.get("CIW_LEAKAGE_PROVIDER_CHECKOUT",
                                   "/dev/shm/polymer-leakage-provider-c53383586a0d"))
    if not checkout.is_dir():
        pytest.skip("Exact operator-provisioned FlowState checkout is unavailable")
    return NativeLeakageBackend(checkout)


@pytest.fixture
def small_tmp():
    with tempfile.TemporaryDirectory(prefix="net-leakage-agent-") as directory:
        yield Path(directory)


@pytest.mark.parametrize("basis", ["volume", "mass"])
def test_native_profile_executes_twoop_graph_idempotent_retry_and_fresh_replay(small_tmp, native, basis):
    profile = leakage_config(small_tmp / "profile", basis)
    original = {p.name: p.read_bytes() for p in profile.parent.iterdir()}
    graph = parse(original["experiment.json"])
    assert plan_graph(graph, capability_registry(native)) == ["assessment", "verification"]
    host = from_profile(profile, instrument="leakage", leakage_backend=native)
    catalog = host.call("net_capabilities", {})["catalog"]
    assert set(catalog["operations"]) == OPERATIONS
    assert catalog["authorizes_execution"] is False
    assert all(row["bound"] and row["agent_execution_enabled"] for row in catalog["operations"].values())
    first = host.call("net_execute", {"source": "source", "graph": "baseline", "attempt": "first"})
    assert first["status"] == "completed", first
    retained = parse((profile.parent / "agent-output" / "first" / "workspace.json").read_bytes())
    results = {row["operation_id"]: row for row in retained["results"]}
    assert len(retained["results"]) == len(retained["executions"]) == 2
    assert results[ASSESS]["runtime"]["native"]["revision"] == PIN
    assert results[ASSESS]["data"]["calculation"]["basis"] == basis
    assert results[VERIFY]["data"]["report"]["status"] == "PASS"
    assert results[VERIFY]["parameters"]["assessment"] == results[ASSESS]
    assert results[VERIFY]["data"]["assessment_result_id"] == results[ASSESS]["result_id"]
    assert all(row["data"]["authority"] == AUTHORITY for row in results.values())
    retry = host.call("net_execute", {"source": "source", "graph": "baseline", "attempt": "first"})
    assert retry["reused_response"] is True and retry["execution_ids"] == first["execution_ids"]
    replay = host.call("net_replay", {"original_attempt": "first", "new_attempt": "second"})
    assert replay["status"] == "completed", replay
    assert set(replay["execution_ids"]).isdisjoint(first["execution_ids"])
    assert set(replay["result_ids"]).isdisjoint(first["result_ids"])
    second = parse((profile.parent / "agent-output" / "second" / "workspace.json").read_bytes())
    replayed = {row["operation_id"]: row for row in second["results"]}
    assert second["run"]["evidence_id"] == retained["run"]["evidence_id"]
    assert replayed[ASSESS]["data"] == results[ASSESS]["data"]
    assert replayed[VERIFY]["data"]["verification_id"] != results[VERIFY]["data"]["verification_id"]
    assert original == {name: (profile.parent / name).read_bytes() for name in original}


def test_leakage_requires_selector_and_constructed_backend_without_default_grants(small_tmp, native):
    profile = leakage_config(small_tmp / "leakage")
    with pytest.raises(ValueError, match="not advertised"):
        from_profile(profile)
    with pytest.raises(ValueError, match="not advertised"):
        from_profile(profile, instrument="polymer")
    with pytest.raises(ValueError, match="explicitly constructed"):
        from_profile(profile, instrument="leakage")
    with pytest.raises(ValueError, match="explicitly constructed"):
        from_profile(profile, instrument="leakage", leakage_backend={"checkout": str(native.adapter.repository_root)})
    with pytest.raises(ValueError, match="explicit leakage instrument selector"):
        from_profile(profile, leakage_backend=native)
    builtin = from_profile(demo_config(small_tmp / "builtin"))
    assert not OPERATIONS.intersection(builtin.capabilities()["catalog"]["operations"])
    polymer = from_profile(polymer_config(small_tmp / "polymer"), instrument="polymer")
    assert not OPERATIONS.intersection(polymer.capabilities()["catalog"]["operations"])
    assert not OPERATIONS.intersection(row["operation_id"] for row in default_registry().describe())


@pytest.mark.parametrize("instrument", ["leakage.provider", "Leakage", "", None])
def test_arbitrary_selector_refuses_before_reading_profile(small_tmp, instrument):
    with pytest.raises(ValueError, match="fixed operator-selected instrument"):
        from_profile(small_tmp / "not-read.json", instrument=instrument)


@pytest.mark.parametrize("field", ["instrument", "provider_checkout", "python", "leakage_backend", "runtime"])
def test_saved_profile_cannot_bind_native_code_or_runtime(small_tmp, native, monkeypatch, field):
    profile = leakage_config(small_tmp / "profile")
    value = parse(profile.read_bytes())
    value[field] = "/saved/data/cannot/select/executable"
    profile.write_bytes(encode(value))

    def forbidden(*args, **kwargs):
        pytest.fail("Forged profile probed or calculated the native provider")

    monkeypatch.setattr(native, "runtime_identity", forbidden)
    monkeypatch.setattr(native, "calculate", forbidden)
    with pytest.raises(ValueError, match="contract fields"):
        from_profile(profile, instrument="leakage", leakage_backend=native)


@pytest.mark.parametrize("allow", [[], [ASSESS]])
def test_missing_grants_refuse_complete_graph_before_native_calculation(small_tmp, native, monkeypatch, allow):
    profile = leakage_config(small_tmp / "profile")
    value = parse(profile.read_bytes())
    value["allow_operations"] = allow
    profile.write_bytes(encode(value))

    def forbidden(*args, **kwargs):
        pytest.fail("A missing grant dispatched a native calculation")

    monkeypatch.setattr(native, "calculate", forbidden)
    host = from_profile(profile, instrument="leakage", leakage_backend=native)
    catalog = host.capabilities()["catalog"]["operations"]
    assert {operation for operation, row in catalog.items() if row["agent_execution_enabled"]} == set(allow)
    with pytest.raises(ValueError, match="not enabled"):
        host.call("net_execute", {"source": "source", "graph": "baseline", "attempt": "denied"})
    assert host.capabilities()["execution_budget"]["used"] == 0
    assert list((profile.parent / "agent-output").iterdir()) == []


@pytest.mark.parametrize("challenge", ["missing", "wrong_port", "wrong_producer", "literal", "graph_literal"])
def test_saved_graph_requires_actual_typed_assessment_before_native_dispatch(small_tmp, native, monkeypatch, challenge):
    profile = leakage_config(small_tmp / "profile")
    path = profile.parent / "experiment.json"
    graph = parse(path.read_bytes())
    if challenge == "missing":
        graph["nodes"][1]["inputs"] = {}
    elif challenge == "wrong_port":
        graph["nodes"][1]["inputs"]["assessment"]["port"] = "data"
    elif challenge == "wrong_producer":
        graph["nodes"].append({"node_id": "another-verification", "operation_id": VERIFY,
                               "parameters": {}, "depends_on": [], "inputs": {
                                   "assessment": {"node_id": "verification", "port": "result"}}})
    elif challenge == "literal":
        graph["nodes"][0]["parameters"] = {"provider_checkout": "/saved/provider"}
    else:
        graph["parameters"] = {"assessment": {"result_id": "imagined"}}
    path.write_bytes(encode(seal(graph)))

    def forbidden(*args, **kwargs):
        pytest.fail("Malformed graph dispatched a native calculation")

    monkeypatch.setattr(native, "calculate", forbidden)
    with pytest.raises(ValueError):
        from_profile(profile, instrument="leakage", leakage_backend=native)
    assert not (profile.parent / "agent-output").exists()


def _session_call(session, operation, parameters=None):
    response = session.handle({"protocol_version": 1, "request_id": uuid.uuid4().hex,
                               "type": "operation.execute", "payload": {
                                   "operation_id": operation, "parameters": parameters or {}}})
    assert response["type"] == "response", response
    return response["payload"]


@pytest.mark.parametrize("challenge", ["invented_occurrence", "changed_content", "other_session"])
def test_forged_dependency_refuses_before_runtime_and_verifier_dispatch(small_tmp, native, monkeypatch, challenge):
    from ciw import leakage_workflow as workflow
    from ciw.leakage_contract import example_request

    source = workflow.make_source(example_request())
    source_session = Session(source, small_tmp / "original", operations=workflow.registry(native))
    assessed = _session_call(source_session, ASSESS)
    assert assessed["status"] == "completed", assessed
    candidate = deepcopy(assessed["result"])
    target = source_session
    if challenge == "invented_occurrence":
        candidate["result_id"] = new_identity("result")
        candidate["execution_id"] = new_identity("execution")
        seal(candidate)
    elif challenge == "changed_content":
        candidate["data"]["decision"]["cause_status"] = "VERIFIED_LEAK"
        seal(candidate)
    else:
        target = Session(source, small_tmp / "other", operations=workflow.registry(native))

    def forbidden(*args, **kwargs):
        pytest.fail("Unretained dependency reached runtime or verifier dispatch")

    monkeypatch.setattr(workflow, "runtime_identity", forbidden)
    monkeypatch.setattr(workflow, "_verify", forbidden)
    monkeypatch.setattr(native, "calculate", forbidden)
    before_results, before_executions = len(target.results), len(target.executions)
    refused = _session_call(target, VERIFY, {"assessment": candidate})
    assert refused["status"] == "refused" and refused["result"] is None
    assert len(target.results) == before_results
    assert len(target.executions) == before_executions + 1
    assert "actually retained" in refused["execution"]["refusal"]["message"]
    assert refused["execution"]["runtime"] is None
    retained_path = small_tmp / "retained-refusal.json"
    target.save_workspace(retained_path)
    original = retained_path.read_bytes()
    restored = Session.from_workspace(retained_path, small_tmp / "offline-reopen")
    assert len(restored.results) == before_results
    assert len(restored.executions) == before_executions + 1
    assert retained_path.read_bytes() == original


@pytest.mark.parametrize("basis", ["volume", "mass"])
def test_config_is_data_only_create_only_and_grants_exact_twoops(small_tmp, monkeypatch, basis):
    from ciw import leakage_workflow

    def forbidden(*args, **kwargs):
        pytest.fail("Data-only profile configuration invoked native or operation code")

    for name in ("runtime_identity", "_assess", "_verify"):
        monkeypatch.setattr(leakage_workflow, name, forbidden)
    monkeypatch.setattr(NativeLeakageBackend, "__init__", forbidden)
    profile = leakage_config(small_tmp / "profile", basis)
    value = parse(profile.read_bytes())
    assert value["allow_operations"] == [ASSESS, VERIFY]
    assert value["candidate_domains"] == {}
    assert parse((profile.parent / "request.json").read_bytes())["basis"] == basis
    assert not (profile.parent / "agent-output").exists()
    before = {p.name: p.read_bytes() for p in profile.parent.iterdir()}
    with pytest.raises(FileExistsError):
        leakage_config(profile.parent, basis)
    assert before == {p.name: p.read_bytes() for p in profile.parent.iterdir()}


def test_config_cli_is_data_only_selected_basis(small_tmp, capsys, monkeypatch):
    from ciw.agent_mcp import main

    def forbidden(*args, **kwargs):
        pytest.fail("Config command constructed a provider")

    monkeypatch.setattr(NativeLeakageBackend, "__init__", forbidden)
    destination = small_tmp / "mass-config"
    assert main(["leakage-config", "--basis", "mass", "--output-dir", str(destination)]) == 0
    assert Path(capsys.readouterr().out.strip()) == destination / "profile.json"


@pytest.mark.parametrize("instrument,flags", [
    ("builtin", ["--provider-checkout", "/saved/provider"]),
    ("polymer", ["--python", "/saved/interpreter"]),
    ("leakage", []),
    ("leakage", ["--provider-checkout", "relative/provider"]),
    ("leakage", ["--provider-checkout", "/saved/provider", "--python", "relative/python"]),
])
def test_cli_refuses_missing_or_unexpected_native_flags_before_binding(small_tmp, capsys, monkeypatch, instrument, flags):
    from ciw.agent_mcp import main

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid native launch selected a provider")

    monkeypatch.setattr(NativeLeakageBackend, "__init__", forbidden)
    assert main(["serve", "--instrument", instrument, "--profile", str(small_tmp / "not-read.json"), *flags]) == 1
    assert "startup refused" in capsys.readouterr().err


def test_native_mcp_stdio_full_graph_retry_and_fresh_replay(small_tmp, native):
    profile = leakage_config(small_tmp / "profile")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": VERSIONS[0], "capabilities": {},
            "clientInfo": {"name": "leakage-native-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "net_capabilities", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "net_execute", "arguments": {
            "source": "source", "graph": "baseline", "attempt": "first"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "net_execute", "arguments": {
            "source": "source", "graph": "baseline", "attempt": "first"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "net_replay", "arguments": {
            "original_attempt": "first", "new_attempt": "second"}}},
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-m", "ciw.agent_mcp", "serve", "--instrument", "leakage",
                             "--profile", str(profile), "--provider-checkout", str(native.adapter.repository_root)],
                            input=b"\n".join(encode(message) for message in messages) + b"\n",
                            capture_output=True, timeout=30, env=environment)
    assert result.returncode == 0, result.stderr.decode()
    replies = {row["id"]: row for row in map(json.loads, result.stdout.splitlines())}
    assert set(replies) == {1, 2, 3, 4, 5}
    assert set(replies[2]["result"]["structuredContent"]["catalog"]["operations"]) == OPERATIONS
    first = replies[3]["result"]["structuredContent"]
    assert first["status"] == "completed"
    assert replies[4]["result"]["structuredContent"]["reused_response"] is True
    replay = replies[5]["result"]["structuredContent"]
    assert replay["status"] == "completed"
    assert set(replay["execution_ids"]).isdisjoint(first["execution_ids"])


def test_cli_rejects_forged_profile_before_native_construction(small_tmp, capsys, monkeypatch):
    from ciw.agent_mcp import main

    profile = leakage_config(small_tmp / "profile")
    value = parse(profile.read_bytes())
    value["provider_checkout"] = "/saved/path/cannot/select/a/runtime"
    profile.write_bytes(encode(value))

    def forbidden(*args, **kwargs):
        pytest.fail("Forged saved profile reached native construction")

    monkeypatch.setattr(NativeLeakageBackend, "__init__", forbidden)
    assert main(["serve", "--instrument", "leakage", "--profile", str(profile),
                 "--provider-checkout", str(small_tmp / "explicit-operator-path")]) == 1
    assert "contract fields" in capsys.readouterr().err


def test_agent_arguments_cannot_select_native_paths_from_saved_catalog(small_tmp, native, monkeypatch):
    profile = leakage_config(small_tmp / "profile")
    host = from_profile(profile, instrument="leakage", leakage_backend=native)
    before = deepcopy(host.capabilities()["catalog"])

    def forbidden(*args, **kwargs):
        pytest.fail("Agent provider-path argument dispatched a native calculation")

    monkeypatch.setattr(native, "calculate", forbidden)
    with pytest.raises(ValueError, match="tool arguments"):
        host.call("net_execute", {"source": "source", "graph": "baseline", "attempt": "forged",
                                  "provider_checkout": "/catalog/path/is/data", "python": "/saved/interpreter"})
    assert host.capabilities()["catalog"] == before
    assert host.capabilities()["execution_budget"]["used"] == 0
