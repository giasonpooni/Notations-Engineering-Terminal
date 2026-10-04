"""Bounded configuration derivations, not numerical or physical validation."""
from copy import deepcopy
import json

import pytest

from ciw import hypergraph_rewrite as h
from ciw.core.identities import content_identity, validate_identity
from ciw.operations.runner import seal


def rule(request, name):
    return next(item for item in request["rules"] if item["rule_id"] == name)


def request(profile="thermal"):
    return h.example_request(profile)


def reseal(record):
    return seal(record)


def test_thermal_refinement_preserves_capacity_and_declares_interface():
    source = request()
    before = deepcopy(source)
    history = h.run(source)
    assert source == before
    assert set(history["runtime"]["source_files"]) == {
        "hypergraph_rewrite.py", "control_contracts.py",
        "core/identities.py", "operations/runner.py",
    }
    assert history["status"] == "COMPLETE"
    assert len(history["events"]) == 1
    graph = history["states"][-1]["graph"]
    assert [node["id"] for node in graph["nodes"]] == ["zone_left", "zone_right"]
    assert sum(node["parameters"]["heat_capacity"]["value"] for node in graph["nodes"]) == 100
    assert {node["parameters"]["heat_capacity"]["unit"] for node in graph["nodes"]} == {"J/K"}
    interface = graph["edges"][0]
    assert interface["id"] == "interface"
    assert interface["kind"] == "thermal_interface"
    assert interface["unit"] == "W"
    assert interface["parameters"]["conductance"] == {"value": 2, "unit": "W/K"}
    assert {endpoint["node"] for endpoint in interface["endpoints"]} == {"zone_left", "zone_right"}
    assert all(node["parameters"]["temperature"] == {"value": 300, "unit": "K"} for node in graph["nodes"])
    assert history["events"][0]["parents"] == []
    assert all(check["status"] == "PASS" for check in history["events"][0]["checks"])
    assert h.verify(history)["status"] == "PASS"
    assert history["authority"] == h.AUTHORITY
    assert history["authority"]["physical_validation"] == "not_performed"


def test_replication_variation_and_true_hyperedge_retain_actual_derivation_parents():
    history = h.run(request("ensemble"))
    assert history["status"] == "COMPLETE"
    events = {event["rule_id"]: event for event in history["events"]}
    graph = history["states"][-1]["graph"]
    gains = {node["id"]: node["parameters"]["gain"]["value"] for node in graph["nodes"]}
    assert gains == {"model_a": 1.1, "model_b": 0.9, "model_c": 1}
    edge = graph["edges"][0]
    assert len(edge["endpoints"]) == 3
    assert {endpoint["node"] for endpoint in edge["endpoints"]} == set(gains)
    assert {endpoint["role"] for endpoint in edge["endpoints"]} == {"primary", "comparison", "replica"}
    assert set(events["couple"]["parents"]) == {
        events[name]["result_id"] for name in ("replicate", "vary_a", "vary_b")}
    assert events["vary_a"]["parents"] == events["vary_b"]["parents"] == []
    assert not events["vary_a"]["checks"][0]["qualification_checked"]
    assert h.verify(history)["status"] == "PASS"


def test_distinct_rewrite_paths_remain_separate_when_graphs_coincide():
    source = request("ensemble")
    source["rules"] = [rule(source, name) for name in ("vary_a", "vary_b")]
    source["schedule"] = ["vary_a", "vary_b"]
    source["limits"] = {"states": 16, "depth": 3, "attempts": 32}
    history = h.explore(source)
    assert history["status"] == "COMPLETE"
    terminals = [state for state in history["states"] if state["depth"] == 2]
    assert len(terminals) == 2
    assert len({state["graph_id"] for state in terminals}) == 1
    assert len({state["branch_id"] for state in terminals}) == 2
    assert terminals[0]["path"] == list(reversed(terminals[1]["path"]))
    assert h.verify(history)["status"] == "PASS"


def test_graph_equivalence_normalizes_order_but_retains_identity_and_units():
    source = request("ensemble")
    graph = h.run(source)["states"][-1]["graph"]
    reordered = deepcopy(graph)
    reordered["nodes"].reverse()
    reordered["edges"].reverse()
    reordered["edges"][0]["endpoints"].reverse()
    assert h.graph_id(graph) == h.graph_id(reordered)
    changed = deepcopy(graph)
    changed["nodes"][0]["parameters"]["gain"]["unit"] = "dimensionless"
    assert h.graph_id(graph) != h.graph_id(changed)


def test_independent_order_check_has_narrow_equivalence_scope():
    report = h.compare_orders(request("ensemble"), ["vary_a", "vary_b"], ["vary_b", "vary_a"])
    assert report["status"] == "PASS"
    assert report["terminal_graph_ids"][0] == report["terminal_graph_ids"][1]
    assert report["equivalence"] == "exact_identifiers_normalized_list_order"
    assert report["general_confluence"] == report["causal_invariance"] == "not_established"
    assert report["authority"] == h.AUTHORITY


def test_dependent_order_check_does_not_accept_refused_schedule():
    report = h.compare_orders(request("ensemble"), ["replicate", "couple"], ["couple", "replicate"])
    assert report["status"] == "INDETERMINATE"
    assert [history["status"] for history in report["histories"]] == ["COMPLETE", "REFUSE"]


def test_order_comparison_requires_same_multiset():
    with pytest.raises(ValueError, match="same rule multiset"):
        h.compare_orders(request("ensemble"), ["vary_a"], ["vary_b"])


def test_order_aggregate_storage_guard_refuses_before_second_schedule(monkeypatch):
    source = request("ensemble")
    selected = deepcopy(source)
    selected["schedule"] = ["vary_a", "vary_b"]
    retained = h.run(selected)
    raw = json.dumps(retained, indent=2).encode()
    # The history fits by itself. Two embedded histories require additional
    # indentation and report metadata, so this budget cannot hold the pair.
    monkeypatch.setattr(h, "MAX_BYTES", 2 * len(raw) + 8192)
    assert h.inspect(retained)["status"] == "COMPLETE"
    calls = []
    def bounded_run(current):
        calls.append(current["schedule"])
        if len(calls) > 1:
            raise AssertionError("second schedule executed after aggregate storage was exhausted")
        return deepcopy(retained)
    monkeypatch.setattr(h, "run", bounded_run)
    with pytest.raises(ValueError, match="aggregate report budget"):
        h.compare_orders(source, ["vary_a", "vary_b"], ["vary_b", "vary_a"])
    assert calls == [["vary_a", "vary_b"]]


@pytest.mark.parametrize("limit,value,maximum_events,maximum_states", [
    ("states", 1, 0, 1), ("depth", 0, 0, 1), ("attempts", 1, 1, 2)])
def test_exploration_budget_exhaustion_is_explicit(limit, value, maximum_events, maximum_states):
    source = request("ensemble")
    source["limits"][limit] = value
    history = h.explore(source)
    assert history["status"] == "TRUNCATED"
    assert limit in history["limits_hit"]
    assert len(history["events"]) <= maximum_events
    assert len(history["states"]) <= maximum_states
    assert history["attempt_count"] == len(history["events"])
    verified = h.verify(history)
    assert verified["status"] == "PASS"
    assert verified["exploration_complete"] is False


def test_scheduled_verification_does_not_claim_exploration_completeness():
    witness = h.verify(h.run(request()))
    assert witness["status"] == "PASS"
    assert witness["derivation_complete"] is True
    assert witness["exploration_complete"] is False


def test_complete_exploration_witness_identifies_its_scope():
    source = request("ensemble")
    source["rules"] = [rule(source, "vary_a"), rule(source, "vary_b")]
    source["schedule"] = ["vary_a", "vary_b"]
    source["limits"] = {"states": 16, "depth": 3, "attempts": 32}
    witness = h.verify(h.explore(source))
    assert witness["derivation_complete"] is True
    assert witness["exploration_complete"] is True


@pytest.mark.parametrize("limit,value", [
    ("states", True), ("depth", False), ("attempts", True),
    ("states", 0), ("states", h.MAX_STATES + 1),
    ("depth", -1), ("depth", h.MAX_DEPTH + 1),
    ("attempts", 0), ("attempts", h.MAX_ATTEMPTS + 1),
    ("states", 1.0), ("depth", "1")])
def test_noninteger_and_out_of_range_budgets_refuse(limit, value):
    source = request()
    source["limits"][limit] = value
    with pytest.raises(ValueError, match="budget"):
        h.run(source)


def test_global_invariant_failure_refuses_without_partial_mutation():
    source = request()
    original = source["graph"]["nodes"][0]
    replication = {"rule_id": "replicate", "kind": "replicate", "source": original["id"],
                   "target": "replica", "expected": content_identity(original)}
    before = deepcopy(source["graph"])
    with pytest.raises(ValueError, match="sum invariant"):
        h.apply(source["graph"], replication, source["invariants"])
    assert source["graph"] == before
    source["rules"] = [replication]
    source["schedule"] = ["replicate"]
    history = h.run(source)
    assert history["status"] == "REFUSE"
    assert len(history["states"]) == 1
    assert history["events"][0]["result_id"] is None
    assert history["events"][0]["after_id"] is None
    assert h.verify(history)["status"] == "PASS"


@pytest.mark.parametrize("change", ["sum", "unit", "negative", "connected", "identity"])
def test_thermal_refinement_preconditions_are_enforced_atomically(change):
    source = request()
    selected = source["rules"][0]
    if change == "sum":
        selected["capacities"] = [40, 61]
    elif change == "unit":
        selected["conductance"]["unit"] = "W"
    elif change == "negative":
        selected["capacities"] = [0, 100]
    elif change == "identity":
        selected["expected"] = "sha256:" + "0" * 64
    else:
        second = deepcopy(source["graph"]["nodes"][0])
        second["id"] = "other"
        source["graph"]["nodes"].append(second)
        source["graph"]["edges"] = [{"id": "existing", "kind": "thermal_interface", "unit": "W",
            "frame": "bench", "parameters": {}, "endpoints": [
                {"node": "zone", "kind": "thermal_zone", "role": "left", "port": "thermal"},
                {"node": "other", "kind": "thermal_zone", "role": "right", "port": "thermal"}]}]
    before = deepcopy(source["graph"])
    with pytest.raises(ValueError, match="reconnection" if change == "connected" else None):
        h.apply(source["graph"], selected)
    assert source["graph"] == before


@pytest.mark.parametrize("change", [
    "dangling", "kind", "frame", "unit", "unit_mismatch", "absent_port", "node_port_unit",
    "duplicate_role", "identity_collision", "bool_quantity", "extra_key"])
def test_malformed_graph_declarations_refuse(change):
    graph = h.run(request("ensemble"))["states"][-1]["graph"]
    edge = graph["edges"][0]
    if change == "dangling":
        edge["endpoints"][0]["node"] = "absent"
    elif change == "kind":
        edge["endpoints"][0]["kind"] = "sensor"
    elif change == "frame":
        edge["frame"] = "other"
    elif change == "unit":
        edge["unit"] = None
    elif change == "unit_mismatch":
        edge["unit"] = "m"
    elif change == "absent_port":
        edge["endpoints"][0]["port"] = "absent"
    elif change == "node_port_unit":
        graph["nodes"][0]["ports"]["comparison"] = "m"
    elif change == "duplicate_role":
        edge["endpoints"][1]["role"] = edge["endpoints"][0]["role"]
    elif change == "identity_collision":
        edge["id"] = graph["nodes"][0]["id"]
    elif change == "bool_quantity":
        graph["nodes"][0]["parameters"]["gain"]["value"] = True
    else:
        edge["execute"] = "arbitrary_provider"
    with pytest.raises((ValueError, TypeError)):
        h.validate_graph(graph)


@pytest.mark.parametrize("field,value", [("unit", "m"), ("value", 2), ("maximum", 0.8)])
def test_parameter_preconditions_units_and_declared_bounds_refuse(field, value):
    source = request("ensemble")
    selected = rule(source, "vary_a")
    selected[field] = value
    before = deepcopy(source["graph"])
    with pytest.raises(ValueError):
        h.apply(source["graph"], selected)
    assert source["graph"] == before


def test_source_node_content_must_match_pinned_replication_precondition():
    source = request("ensemble")
    source["graph"]["nodes"][0]["parameters"]["gain"]["value"] = 1.2
    with pytest.raises(ValueError, match="identity precondition"):
        h.apply(source["graph"], rule(source, "replicate"))


def test_parameter_qualification_is_retained_as_an_unchecked_reference():
    source = request("ensemble")
    reference = "sha256:" + "1" * 64
    rule(source, "vary_a")["qualification"] = reference
    source["schedule"] = ["vary_a"]
    history = h.run(source)
    check = history["events"][0]["checks"][0]
    assert check["qualification"] == reference
    assert check["qualification_checked"] is False
    assert history["authority"]["physical_validation"] == "not_performed"


@pytest.mark.parametrize("change", ["unsupported_kind", "executable_field", "duplicate_rule", "unknown_schedule"])
def test_only_closed_declared_rules_are_accepted(change):
    source = request()
    if change == "unsupported_kind":
        source["rules"][0]["kind"] = "python_expression"
    elif change == "executable_field":
        source["rules"][0]["execute"] = "__import__('external_provider')"
    elif change == "duplicate_rule":
        source["rules"].append(deepcopy(source["rules"][0]))
    else:
        source["schedule"] = ["absent"]
    with pytest.raises(ValueError):
        h.run(source)


def test_initial_graph_must_satisfy_declared_invariants():
    source = request()
    source["invariants"][0]["expected"] = 99
    with pytest.raises(ValueError, match="sum invariant"):
        h.run(source)


def test_node_capacity_refuses_replication_and_refinement_without_mutation():
    source = request()
    original = source["graph"]["nodes"][0]
    source["graph"]["nodes"] += [
        {**deepcopy(original), "id": "zone" + str(index)} for index in range(1, h.MAX_NODES)]
    before = deepcopy(source["graph"])
    for selected in (source["rules"][0], {"rule_id": "replicate", "kind": "replicate", "source": "zone",
                     "target": "new_zone", "expected": content_identity(original)}):
        with pytest.raises(ValueError, match="budget"):
            h.apply(source["graph"], selected)
        assert source["graph"] == before


def test_edge_capacity_refuses_coupling_without_mutation():
    source = request("ensemble")
    selected = deepcopy(rule(source, "couple"))
    edge = selected["edge"]
    # A valid relation between the two initial nodes, repeated with fresh IDs.
    edge["endpoints"] = edge["endpoints"][:2]
    source["graph"]["edges"] = [
        {**deepcopy(edge), "id": "edge" + str(index)} for index in range(h.MAX_EDGES)]
    before = deepcopy(source["graph"])
    with pytest.raises(ValueError, match="budget"):
        h.apply(source["graph"], selected)
    assert source["graph"] == before


def test_graph_serialized_byte_budget_counts_escaped_text():
    graph = request("ensemble")["graph"]
    template = graph["nodes"][0]
    template["parameters"] = {"p" + str(index): {"value": 1, "unit": "\u03bb" * 512} for index in range(32)}
    graph["nodes"] = [{**deepcopy(template), "id": "model" + str(index)} for index in range(2)]
    # This uses legal identifiers, quantities and per-node counts; escaped JSON
    # serialization, rather than the in-memory character count, exceeds 128 KiB.
    with pytest.raises(ValueError, match="Graph exceeds serialized byte budget"):
        h.validate_graph(graph)


def test_request_serialized_byte_budget_refuses_before_execution(monkeypatch):
    source = request("ensemble")
    edge = deepcopy(rule(source, "couple")["edge"])
    edge["parameters"] = {"p" + str(index): {"value": 1, "unit": "\u03bb" * 512} for index in range(32)}
    source["rules"] = [{"rule_id": "couple" + str(index), "kind": "couple", "edge": {
        **deepcopy(edge), "id": "edge" + str(index)}} for index in range(32)]
    source["schedule"] = []
    def forbidden(*args, **kwargs):
        raise AssertionError("oversized request attempted a rewrite")
    monkeypatch.setattr(h, "apply", forbidden)
    with pytest.raises(ValueError, match="Request exceeds serialized byte budget"):
        h.run(source)


def test_insufficient_history_reservation_refuses_before_first_attempt(monkeypatch):
    monkeypatch.setattr(h, "MAX_BYTES", 500000)
    def forbidden(*args, **kwargs):
        raise AssertionError("insufficient storage attempted a rewrite")
    monkeypatch.setattr(h, "apply", forbidden)
    with pytest.raises(ValueError, match="insufficient history byte budget"):
        h.run(request())


def test_history_byte_budget_truncation_retains_replayable_partial_derivation(monkeypatch):
    source = request("ensemble")
    node = source["graph"]["nodes"][0]
    node["parameters"].update({"p" + str(index): {"value": 1, "unit": "u" * 500} for index in range(31)})
    selected = deepcopy(rule(source, "vary_a"))
    selected["value"] = 1  # A declared no-op leaves a repeatable, path-distinct rule.
    source["rules"] = [selected]
    source["schedule"] = []
    source["limits"] = {"states": 64, "depth": 16, "attempts": 256}
    monkeypatch.setattr(h, "MAX_BYTES", 800000)
    history = h.explore(source)
    assert history["status"] == "TRUNCATED"
    assert history["limits_hit"] == ["bytes"]
    assert 1 < len(history["states"]) < source["limits"]["states"]
    assert 0 < history["attempt_count"] < source["limits"]["attempts"]
    assert max(state["depth"] for state in history["states"]) < source["limits"]["depth"]
    assert len(json.dumps(history, indent=2).encode()) < h.MAX_BYTES
    assert h.inspect(history)["limits_hit"] == ["bytes"]
    witness = h.verify(history)
    assert witness["status"] == "PASS"
    assert witness["derivation_complete"] is False
    assert witness["exploration_complete"] is False


def test_static_inspection_refuses_oversized_retained_history(monkeypatch):
    history = h.run(request())
    monkeypatch.setattr(h, "MAX_BYTES", len(json.dumps(history, indent=2).encode()) - 1)
    with pytest.raises(ValueError, match="History exceeds serialized byte budget"):
        h.inspect(history)


def test_inspection_does_not_apply_rules_or_issue_verification(monkeypatch):
    history = h.run(request())
    def forbidden(*args, **kwargs):
        raise AssertionError("inspection applied a rewrite")
    monkeypatch.setattr(h, "apply", forbidden)
    inspected = h.inspect(history)
    assert inspected["status"] == "COMPLETE"
    assert inspected["fresh_execution"] is False
    assert inspected["rewrite_validation"] == "not_performed"
    assert "verification_id" not in inspected
    assert inspected["authority"] == h.AUTHORITY


@pytest.mark.parametrize("change", ["graph", "causal", "outcome", "read_set", "refusal", "runtime"])
def test_resealed_semantic_history_tampering_is_detected_by_fresh_replay(change):
    history = h.run(request("ensemble"))
    if change == "graph":
        state = history["states"][-1]
        state["graph"]["nodes"][0]["parameters"]["gain"]["value"] = 1.25
        state["graph_id"] = h.graph_id(state["graph"])
        history["events"][-1]["after_id"] = state["graph_id"]
    elif change == "causal":
        # This remains a prior, real result but is not the dependency set.
        history["events"][-1]["parents"] = [history["events"][0]["result_id"]]
    elif change == "outcome":
        history["status"] = "TRUNCATED"
        history["limits_hit"] = ["states"]
    elif change == "read_set":
        history["events"][-1]["reads"] = []
    elif change == "refusal":
        source = request("ensemble")
        source["schedule"] = ["couple"]
        history = h.run(source)
        history["events"][0]["refusal"] = "invented refusal"
    else:
        history["runtime"]["version"] = "other"
    reseal(history)
    with pytest.raises(ValueError, match="replay|runtime"):
        h.verify(history)


def test_unsealed_tamper_and_reused_occurrence_refuse_static_inspection():
    history = h.run(request("ensemble"))
    changed = deepcopy(history)
    changed["status"] = "TRUNCATED"
    with pytest.raises(ValueError, match="integrity"):
        h.inspect(changed)
    changed = deepcopy(history)
    changed["events"][1]["execution_id"] = changed["events"][0]["execution_id"]
    reseal(changed)
    with pytest.raises(ValueError, match="reused"):
        h.inspect(changed)


@pytest.mark.parametrize("change", [
    "root_parent", "root_graph", "branch_identity", "missing_parent", "wrong_ancestor", "wrong_path",
    "event_before", "event_rule", "event_after", "orphan_state"])
def test_resealed_structural_ancestry_tampering_refuses_static_inspection_without_apply(monkeypatch, change):
    history = h.run(request("ensemble"))
    states, events = history["states"], history["events"]
    if change == "root_parent":
        states[0]["parent_branch_id"] = states[0]["branch_id"]
    elif change == "root_graph":
        states[0]["graph"]["nodes"][0]["parameters"]["gain"]["value"] = 1.25
        states[0]["graph_id"] = h.graph_id(states[0]["graph"])
    elif change == "branch_identity":
        states[1]["branch_id"] = "sha256:" + "0" * 64
    elif change == "missing_parent":
        states[1]["parent_branch_id"] = "sha256:" + "0" * 64
    elif change == "wrong_ancestor":
        states[2]["parent_branch_id"] = states[0]["branch_id"]
    elif change == "wrong_path":
        states[2]["path"][0] = "sha256:" + "0" * 64
        states[2]["branch_id"] = content_identity({"root": states[0]["graph_id"], "rule_path": states[2]["path"]})
    elif change == "event_before":
        events[0]["before_id"] = "sha256:" + "0" * 64
    elif change == "event_rule":
        events[0]["rule_digest"] = "sha256:" + "0" * 64
    elif change == "event_after":
        events[0]["after_id"] = "sha256:" + "0" * 64
    else:
        extra = deepcopy(states[-1])
        extra["parent_branch_id"] = states[-1]["branch_id"]
        extra["path"].append(events[-1]["rule_digest"])
        extra["depth"] += 1
        extra["branch_id"] = content_identity({"root": states[0]["graph_id"], "rule_path": extra["path"]})
        states.append(extra)
    reseal(history)
    def forbidden(*args, **kwargs):
        raise AssertionError("static malformed-ancestry inspection attempted a rewrite")
    monkeypatch.setattr(h, "apply", forbidden)
    with pytest.raises(ValueError):
        h.inspect(history)


def test_static_inspection_rejects_real_causal_result_from_sibling_branch(monkeypatch):
    source = request("ensemble")
    source["rules"] = [rule(source, "vary_a"), rule(source, "vary_b")]
    source["schedule"] = ["vary_a", "vary_b"]
    source["limits"] = {"states": 16, "depth": 3, "attempts": 32}
    history = h.explore(source)
    first, sibling = history["events"][:2]
    assert first["status"] == sibling["status"] == "COMPLETE"
    assert first["parent_branch_id"] == sibling["parent_branch_id"]
    assert first["branch_id"] != sibling["branch_id"]
    sibling["parents"] = [first["result_id"]]
    reseal(history)
    def forbidden(*args, **kwargs):
        raise AssertionError("static sibling-causal inspection attempted a rewrite")
    monkeypatch.setattr(h, "apply", forbidden)
    with pytest.raises(ValueError, match="different branch"):
        h.inspect(history)


def test_execution_result_and_verification_occurrences_are_fresh_for_same_content():
    source = request()
    first, second = h.run(source), h.run(source)
    assert first["request_id"] == second["request_id"]
    assert first["states"] == second["states"]
    assert first["execution_id"] != second["execution_id"]
    assert first["result_id"] != second["result_id"]
    assert first["events"][0]["execution_id"] != second["events"][0]["execution_id"]
    assert first["events"][0]["result_id"] != second["events"][0]["result_id"]
    witnesses = [h.verify(first), h.verify(first)]
    assert witnesses[0]["verification_id"] != witnesses[1]["verification_id"]
    assert witnesses[0]["replay_execution_id"] != witnesses[1]["replay_execution_id"]
    assert all(item["candidate_record_digest"] == first["record_digest"] for item in witnesses)
    for record, field, kind in [(first, "execution_id", "execution"), (first, "result_id", "result"),
                                (witnesses[0], "verification_id", "verification")]:
        validate_identity(record[field], kind)


@pytest.mark.parametrize("profile", ["thermal", "ensemble"])
def test_cli_lifecycle_create_only_artifacts_and_order_check(tmp_path, capsys, profile):
    from ciw import net
    request_path = tmp_path / "request.json"
    history_path = tmp_path / "history.json"
    witness_path = tmp_path / "verification.json"
    assert net.main(["rewrite", "example", "--profile", profile, "--output", str(request_path)]) == 0
    original = request_path.read_bytes()
    assert net.main(["rewrite", "example", "--profile", profile, "--output", str(request_path)]) == 1
    assert request_path.read_bytes() == original
    assert net.main(["rewrite", "run", str(request_path), "--output", str(history_path)]) == 0
    history = json.loads(history_path.read_text())
    assert history["status"] == "COMPLETE"
    assert net.main(["rewrite", "inspect", str(history_path)]) == 0
    assert net.main(["rewrite", "verify", str(history_path), "--output", str(witness_path)]) == 0
    assert json.loads(witness_path.read_text())["status"] == "PASS"
    original_history = history_path.read_bytes()
    assert net.main(["rewrite", "run", str(request_path), "--output", str(history_path)]) == 1
    assert history_path.read_bytes() == original_history
    assert net.main(["rewrite", "verify", str(history_path), "--output", str(witness_path)]) == 1
    if profile == "ensemble":
        order_path = tmp_path / "orders.json"
        assert net.main(["rewrite", "orders", str(request_path), "--left", "vary_a,vary_b", "--right",
                         "vary_b,vary_a", "--output", str(order_path)]) == 0
        assert json.loads(order_path.read_text())["status"] == "PASS"
    capsys.readouterr()


def test_cli_malformed_input_does_not_create_output(tmp_path, capsys):
    from ciw import net
    source, output = tmp_path / "bad.json", tmp_path / "bad-history.json"
    source.write_text('{"schema":"one","schema":"two"}')
    assert net.main(["rewrite", "run", str(source), "--output", str(output)]) == 1
    assert not output.exists()
    assert '"status": "REFUSE"' in capsys.readouterr().err


def test_cli_exploration_reports_truncation_and_preserves_verifiable_history(tmp_path, capsys):
    from ciw import net
    source = request("ensemble")
    source["limits"]["states"] = 2
    input_path, output_path = tmp_path / "input.json", tmp_path / "bounded.json"
    input_path.write_text(json.dumps(source))
    assert net.main(["rewrite", "explore", str(input_path), "--output", str(output_path)]) == 2
    history = json.loads(output_path.read_text())
    assert history["status"] == "TRUNCATED"
    assert len(history["states"]) == 2
    assert h.verify(history)["status"] == "PASS"
    capsys.readouterr()
