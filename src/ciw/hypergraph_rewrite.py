"""Bounded, provider-free rewrites of typed candidate configurations.

No executable rules, imports, dimensional conversion or solver authority are
loaded from documents. Derivation dependencies describe these rewrites only.
"""
from __future__ import annotations

from copy import deepcopy
import math
import json
from pathlib import Path
import platform
import re

from .control_contracts import MAX_BYTES, content_ref, detached, keys, number, text
from .core.identities import content_identity, new_identity, validate_identity
from .operations.runner import check_seal, seal

MAX_NODES, MAX_EDGES, MAX_RULES = 64, 128, 32
MAX_STATES, MAX_DEPTH, MAX_ATTEMPTS = 64, 16, 256
MAX_GRAPH_BYTES = 128 * 1024
# Reserve a bounded snapshot and event before executing another attempt.
_ATTEMPT_STORAGE = 576 * 1024
AUTHORITY = {"authorizes_execution": False, "state_admission": "not_performed",
             "physical_validation": "not_performed", "numerical_execution": "not_performed"}
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,95}\Z")


def _name(value):
    if type(value) is not str or not _NAME.fullmatch(value):
        raise ValueError("Expected a bounded identifier")
    return value


def _list(value, maximum, *, minimum=0):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError("List exceeds its declared budget")
    return value


def _quantity(value):
    keys(value, {"value", "unit"})
    number(value["value"])
    text(value["unit"])


def _parameters(value):
    if type(value) is not dict or len(value) > 32:
        raise ValueError("Parameter budget exceeded")
    for name, quantity in value.items():
        _name(name); _quantity(quantity)


def _storage(value, level=0):
    raw = json.dumps(value, indent=2, allow_nan=False)
    return len(raw.encode()) + 2 * level * (raw.count("\n") + 1) + 2


def _edge(edge, nodes=None):
    keys(edge, {"id", "kind", "unit", "frame", "parameters", "endpoints"})
    _name(edge["id"]); _name(edge["kind"]); text(edge["unit"]); text(edge["frame"])
    _parameters(edge["parameters"])
    roles = set()
    for endpoint in _list(edge["endpoints"], 8, minimum=2):
        keys(endpoint, {"node", "role", "kind", "port"})
        for value in endpoint.values():
            _name(value)
        if endpoint["role"] in roles:
            raise ValueError("Duplicate endpoint role")
        roles.add(endpoint["role"])
        if nodes is not None:
            target = nodes.get(endpoint["node"])
            if target is None or target["kind"] != endpoint["kind"]:
                raise ValueError("Endpoint kind mismatch or dangling endpoint")
            if target["frame"] != edge["frame"]:
                raise ValueError("Endpoint frame mismatch")
            if target["ports"].get(endpoint["port"]) != edge["unit"]:
                raise ValueError("Endpoint port unit mismatch")


def validate_graph(value):
    detached(value)
    keys(value, {"schema", "nodes", "edges"})
    if value["schema"] != "ciw.hypergraph.v1":
        raise ValueError("Unsupported hypergraph schema")
    nodes = {}
    for node in _list(value["nodes"], MAX_NODES):
        keys(node, {"id", "kind", "frame", "parameters", "ports"})
        _name(node["id"]); _name(node["kind"]); text(node["frame"])
        if node["id"] in nodes:
            raise ValueError("Duplicate node identity")
        _parameters(node["parameters"])
        if type(node["ports"]) is not dict or len(node["ports"]) > 16:
            raise ValueError("Port budget exceeded")
        for name, unit in node["ports"].items():
            _name(name); text(unit)
        nodes[node["id"]] = node
    seen = set()
    for edge in _list(value["edges"], MAX_EDGES):
        _edge(edge, nodes)
        if edge["id"] in seen or edge["id"] in nodes:
            raise ValueError("Duplicate edge or node/edge identity collision")
        seen.add(edge["id"])
    if _storage(value) > MAX_GRAPH_BYTES:
        raise ValueError("Graph exceeds serialized byte budget")
    return value


def _canonical(graph):
    value = deepcopy(validate_graph(graph))
    value["nodes"].sort(key=lambda row: row["id"])
    value["edges"].sort(key=lambda row: row["id"])
    for edge in value["edges"]:
        edge["endpoints"].sort(key=lambda row: row["role"])
    return value


def graph_id(graph):
    """Exact identifier-sensitive equivalence, with list ordering normalized."""
    return content_identity(_canonical(graph))


def _invariants(value):
    for item in _list(value, 16):
        keys(item, {"kind", "parameter", "unit", "expected", "atol"})
        _name(item["kind"]); _name(item["parameter"]); text(item["unit"])
        number(item["expected"])
        if number(item["atol"]) < 0:
            raise ValueError("Invariant tolerance must be nonnegative")


def _checks(graph, invariants):
    checks, reads = [], set()
    for item in invariants:
        values = []
        reads.add("nodes")
        for node in graph["nodes"]:
            if node["kind"] == item["kind"]:
                quantity = node["parameters"].get(item["parameter"])
                if quantity is None or quantity["unit"] != item["unit"]:
                    raise ValueError("Invariant requires an exact declared quantity and unit")
                values.append(number(quantity["value"]))
                reads.add("node:" + node["id"])
        actual = math.fsum(values)
        if not math.isclose(actual, item["expected"], rel_tol=0, abs_tol=item["atol"]):
            raise ValueError("Declared sum invariant failed")
        checks.append({"invariant": deepcopy(item), "actual": actual, "status": "PASS"})
    return checks, reads


def validate_rule(rule):
    detached(rule)
    if type(rule) is not dict:
        raise ValueError("Expected a rewrite rule")
    kind = rule.get("kind")
    fields = {
        "replicate": {"source", "target", "expected"},
        "parameter": {"node", "parameter", "expected", "value", "unit", "minimum", "maximum", "qualification"},
        "couple": {"edge"},
        "split_thermal": {"source", "expected", "children", "capacities", "interface", "conductance", "atol"},
    }
    if kind not in fields:
        raise ValueError("Unsupported trusted rewrite kind")
    keys(rule, {"rule_id", "kind"} | fields[kind])
    _name(rule["rule_id"])
    if kind in {"replicate", "split_thermal"}:
        _name(rule["source"]); content_ref(rule["expected"])
    if kind == "replicate":
        _name(rule["target"])
    elif kind == "parameter":
        _name(rule["node"]); _name(rule["parameter"]); _quantity(rule["expected"])
        text(rule["unit"])
        low, high, selected = (number(rule[key]) for key in ("minimum", "maximum", "value"))
        if not low <= selected <= high or rule["expected"]["unit"] != rule["unit"]:
            raise ValueError("Parameter outside declared bounds or unit mismatch")
        if rule["qualification"] is not None:
            content_ref(rule["qualification"])
    elif kind == "couple":
        _edge(rule["edge"])
    elif kind == "split_thermal":
        children = _list(rule["children"], 2, minimum=2)
        for child in children:
            _name(child)
        if len(set(children)) != 2 or rule["source"] in children:
            raise ValueError("Refinement requires distinct fresh children")
        for value in _list(rule["capacities"], 2, minimum=2):
            if number(value) <= 0:
                raise ValueError("Heat capacities must be positive")
        _name(rule["interface"]); _quantity(rule["conductance"])
        if rule["conductance"]["unit"] != "W/K" or number(rule["conductance"]["value"]) <= 0:
            raise ValueError("Interface conductance requires positive W/K")
        if number(rule["atol"]) < 0:
            raise ValueError("Preservation tolerance must be nonnegative")
    return rule


def validate_request(request):
    detached(request)
    keys(request, {"schema", "graph", "rules", "schedule", "invariants", "limits"})
    if request["schema"] != "ciw.hypergraph-request.v1":
        raise ValueError("Unsupported rewrite request")
    if _storage(request) > MAX_BYTES // 4:
        raise ValueError("Request exceeds serialized byte budget")
    validate_graph(request["graph"])
    rules = {}
    for rule in _list(request["rules"], MAX_RULES):
        validate_rule(rule)
        if rule["rule_id"] in rules:
            raise ValueError("Duplicate rule identity")
        rules[rule["rule_id"]] = rule
    for name in _list(request["schedule"], MAX_DEPTH):
        if _name(name) not in rules:
            raise ValueError("Schedule names an absent rule")
    _invariants(request["invariants"])
    _checks(request["graph"], request["invariants"])
    limits = request["limits"]
    keys(limits, {"states", "depth", "attempts"})
    for name, maximum, minimum in (("states", MAX_STATES, 1), ("depth", MAX_DEPTH, 0), ("attempts", MAX_ATTEMPTS, 1)):
        if type(limits[name]) is not int or not minimum <= limits[name] <= maximum:
            raise ValueError("Invalid exploration budget")
    return rules


def apply(graph, rule, invariants=None):
    """Atomic local transformation; caller retains refusals as execution attempts."""
    candidate = _canonical(graph)
    validate_rule(rule)
    invariants = [] if invariants is None else detached(invariants)
    _invariants(invariants)
    _, reads = _checks(candidate, invariants)
    writes, checks = set(), []
    nodes = {node["id"]: node for node in candidate["nodes"]}
    ids = set(nodes) | {edge["id"] for edge in candidate["edges"]}
    kind = rule["kind"]

    def source(name):
        reads.add("node:" + name)
        if name not in nodes:
            raise ValueError("Source node is absent")
        return nodes[name]

    def fresh(name, entity):
        reads.update({"node:" + name, "edge:" + name})
        if name in ids:
            raise ValueError("Target identity is not fresh")
        ids.add(name)
        writes.add(entity + ":" + name)

    if kind in {"replicate", "split_thermal"}:
        original = source(rule["source"])
        if content_identity(original) != rule["expected"]:
            raise ValueError("Source identity precondition failed")
    if kind == "replicate":
        if len(nodes) >= MAX_NODES:
            raise ValueError("Node budget exceeded")
        fresh(rule["target"], "node")
        replica = deepcopy(original)
        replica["id"] = rule["target"]
        candidate["nodes"].append(replica)
        writes.add("nodes")
    elif kind == "parameter":
        node = source(rule["node"])
        if node["parameters"].get(rule["parameter"]) != rule["expected"]:
            raise ValueError("Parameter precondition failed")
        node["parameters"][rule["parameter"]] = {"value": rule["value"], "unit": rule["unit"]}
        writes.add("node:" + node["id"])
        checks.append({"check": "declared_parameter_bounds", "status": "PASS",
                       "qualification": rule["qualification"], "qualification_checked": False})
    elif kind == "couple":
        edge = deepcopy(rule["edge"])
        # Validate shape before inspecting identifiers or allocating an edge.
        _edge(edge)
        for endpoint in _list(edge["endpoints"], 8, minimum=2):
            keys(endpoint, {"node", "role", "kind", "port"})
            source(_name(endpoint["node"]))
        if len(candidate["edges"]) >= MAX_EDGES:
            raise ValueError("Edge budget exceeded")
        fresh(edge["id"], "edge")
        candidate["edges"].append(edge)
        writes.add("edges")
    else:
        quantity = original["parameters"].get("heat_capacity")
        if original["kind"] != "thermal_zone" or quantity is None or quantity["unit"] != "J/K" or quantity["value"] <= 0:
            raise ValueError("Thermal refinement requires positive heat capacity in J/K")
        if original["ports"].get("thermal") != "W":
            raise ValueError("Thermal refinement requires an explicit thermal port in W")
        reads.add("edges")
        if any(endpoint["node"] == original["id"] for edge in candidate["edges"] for endpoint in edge["endpoints"]):
            raise ValueError("Refinement of connected source requires an explicit reconnection rule")
        total = math.fsum(rule["capacities"])
        if not math.isclose(total, quantity["value"], rel_tol=0, abs_tol=rule["atol"]):
            raise ValueError("Heat capacity preservation failed")
        if len(nodes) + 1 > MAX_NODES or len(candidate["edges"]) >= MAX_EDGES:
            raise ValueError("Refinement graph budget exceeded")
        for child in rule["children"]:
            fresh(child, "node")
        fresh(rule["interface"], "edge")
        candidate["nodes"].remove(original)
        for child, capacity in zip(rule["children"], rule["capacities"]):
            node = deepcopy(original)
            node["id"] = child
            node["parameters"]["heat_capacity"] = {"value": capacity, "unit": "J/K"}
            candidate["nodes"].append(node)
        candidate["edges"].append({"id": rule["interface"], "kind": "thermal_interface", "unit": "W",
            "frame": original["frame"], "parameters": {"conductance": deepcopy(rule["conductance"])}, "endpoints": [
                {"node": child, "role": role, "kind": "thermal_zone", "port": "thermal"}
                for child, role in zip(rule["children"], ("left", "right"))]})
        writes.update({"node:" + original["id"], "nodes", "edges"})
        checks.append({"check": "declared_heat_capacity", "before": quantity["value"],
                       "after": total, "unit": "J/K", "atol": rule["atol"], "status": "PASS"})
    candidate = _canonical(candidate)
    invariant_checks, invariant_reads = _checks(candidate, invariants)
    return {"graph": candidate, "reads": sorted(reads | invariant_reads),
            "writes": sorted(writes), "checks": checks + invariant_checks}


def runtime_identity():
    from .control_contracts import bytes_ref
    root = Path(__file__).parent
    files = (Path(__file__), root / "control_contracts.py", root / "core" / "identities.py", root / "operations" / "runner.py")
    return {"provider": "ciw.hypergraph_rewrite", "version": "1", "python": platform.python_version(),
            "source_files": {path.relative_to(root).as_posix(): bytes_ref(path.read_bytes()) for path in files}}


def _branch(root, path):
    return content_identity({"root": root, "rule_path": path})


def _compute(request, mode):
    request = detached(request)
    rules = validate_request(request)
    if mode not in {"schedule", "explore"}:
        raise ValueError("Unsupported rewrite mode")
    limits = request["limits"]
    initial = _canonical(request["graph"])
    root = graph_id(initial)
    states = [{"branch_id": _branch(root, []), "parent_branch_id": None, "path": [],
               "depth": 0, "graph_id": root, "graph": initial}]
    events, producers, hits = [], {states[0]["branch_id"]: {}}, set()
    used = _storage(request, 1) + _storage(states[0], 2) + 4096
    if used + _ATTEMPT_STORAGE > MAX_BYTES:
        raise ValueError("Request leaves insufficient history byte budget")
    status = "COMPLETE"
    queue = [0]
    while queue:
        parent = states[queue.pop(0)]
        names = sorted(rules) if mode == "explore" else request["schedule"][parent["depth"]:parent["depth"] + 1]
        if not names:
            continue
        if parent["depth"] >= limits["depth"]:
            hits.add("depth")
            continue
        for name in names:
            if used + _ATTEMPT_STORAGE > MAX_BYTES:
                hits.add("bytes")
                break
            if len(events) >= limits["attempts"] or len(states) >= limits["states"]:
                hits.add("attempts" if len(events) >= limits["attempts"] else "states")
                break
            rule = rules[name]
            path = parent["path"] + [content_identity(rule)]
            event = {"execution_id": new_identity("execution"), "result_id": None,
                     "parent_branch_id": parent["branch_id"], "branch_id": _branch(root, path),
                     "before_id": parent["graph_id"], "after_id": None,
                     "rule_id": name, "rule_digest": content_identity(rule), "status": "REFUSE",
                     "refusal": None, "reads": [], "writes": [], "parents": [], "checks": []}
            try:
                outcome = apply(parent["graph"], rule, request["invariants"])
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                event["refusal"] = str(exc) or type(exc).__name__
                used += _storage(event, 2)
                events.append(event)
                if mode == "schedule":
                    status = "REFUSE"
                    queue.clear()
                    break
                continue
            event.update(status="COMPLETE", result_id=new_identity("result"),
                         after_id=graph_id(outcome["graph"]), reads=outcome["reads"],
                         writes=outcome["writes"], checks=outcome["checks"])
            previous = producers[parent["branch_id"]]
            event["parents"] = sorted({previous[key] for key in event["reads"] + event["writes"] if key in previous})
            current = dict(previous)
            current.update({key: event["result_id"] for key in event["writes"]})
            producers[event["branch_id"]] = current
            events.append(event)
            state = {"branch_id": event["branch_id"], "parent_branch_id": parent["branch_id"],
                           "path": path, "depth": parent["depth"] + 1,
                           "graph_id": event["after_id"], "graph": outcome["graph"]}
            used += _storage(event, 2) + _storage(state, 2)
            states.append(state)
            queue.append(len(states) - 1)
    if hits and status != "REFUSE":
        status = "TRUNCATED"
    return seal({"schema": "ciw.hypergraph-history.v1", "mode": mode, "request": request,
                 "request_id": content_identity(request), "execution_id": new_identity("execution"),
                 "result_id": new_identity("result"), "runtime": runtime_identity(),
                 "states": states, "events": events, "attempt_count": len(events),
                 "limits_hit": sorted(hits), "status": status, "authority": deepcopy(AUTHORITY)})


def run(request):
    return _compute(request, "schedule")


def explore(request):
    """Finite BFS retaining path-distinct branches even when graphs coincide."""
    return _compute(request, "explore")


def _history(record):
    detached(record)
    if _storage(record) > MAX_BYTES:
        raise ValueError("History exceeds serialized byte budget")
    keys(record, {"schema", "mode", "request", "request_id", "execution_id", "result_id", "runtime",
                  "states", "events", "attempt_count", "limits_hit", "status", "authority", "record_digest"})
    check_seal(record)
    if record["schema"] != "ciw.hypergraph-history.v1" or record["mode"] not in {"schedule", "explore"}:
        raise ValueError("Unsupported rewrite history")
    if content_identity(record["authority"]) != content_identity(AUTHORITY) or record["status"] not in {"COMPLETE", "REFUSE", "TRUNCATED"}:
        raise ValueError("Invalid rewrite authority or status")
    keys(record["runtime"], {"provider", "version", "python", "source_files"})
    if record["runtime"]["provider"] != "ciw.hypergraph_rewrite" or record["runtime"]["version"] != "1":
        raise ValueError("Unsupported rewrite runtime")
    text(record["runtime"]["python"])
    keys(record["runtime"]["source_files"], {"hypergraph_rewrite.py", "control_contracts.py", "core/identities.py", "operations/runner.py"})
    for reference in record["runtime"]["source_files"].values():
        content_ref(reference)
    for hit in _list(record["limits_hit"], 4):
        if hit not in {"states", "depth", "attempts", "bytes"}:
            raise ValueError("Unknown exploration limit")
    if len(set(record["limits_hit"])) != len(record["limits_hit"]):
        raise ValueError("Duplicate exploration limit")
    rules = validate_request(record["request"])
    if record["request_id"] != content_identity(record["request"]):
        raise ValueError("Request binding mismatch")
    occurrences = set()
    for field, kind in (("execution_id", "execution"), ("result_id", "result")):
        validate_identity(record[field], kind)
        occurrences.add(record[field])
    _list(record["events"], record["request"]["limits"]["attempts"])
    _list(record["states"], record["request"]["limits"]["states"], minimum=1)
    if type(record["attempt_count"]) is not int or record["attempt_count"] != len(record["events"]):
        raise ValueError("Attempt count mismatch")
    states = {}
    root = graph_id(record["request"]["graph"])
    for state in record["states"]:
        keys(state, {"branch_id", "parent_branch_id", "path", "depth", "graph_id", "graph"})
        content_ref(state["branch_id"])
        if state["branch_id"] in states:
            raise ValueError("Duplicate branch identity")
        for reference in _list(state["path"], record["request"]["limits"]["depth"]):
            content_ref(reference)
        if type(state["depth"]) is not int or state["depth"] != len(state["path"]):
            raise ValueError("Branch depth mismatch")
        if state["graph_id"] != graph_id(state["graph"]):
            raise ValueError("Graph binding mismatch")
        if state["branch_id"] != _branch(root, state["path"]):
            raise ValueError("Branch path identity mismatch")
        if not states:
            if state["parent_branch_id"] is not None or state["path"] or state["graph_id"] != root:
                raise ValueError("Invalid root branch")
        else:
            parent = states.get(state["parent_branch_id"])
            if parent is None or state["path"][:-1] != parent["path"] or state["depth"] != parent["depth"] + 1:
                raise ValueError("Invalid branch ancestry")
        states[state["branch_id"]] = state
    completed = {}
    produced = {record["states"][0]["branch_id"]}
    for event in record["events"]:
        keys(event, {"execution_id", "result_id", "parent_branch_id", "branch_id", "before_id", "after_id",
                     "rule_id", "rule_digest", "status", "refusal", "reads", "writes", "parents", "checks"})
        validate_identity(event["execution_id"], "execution")
        if event["execution_id"] in occurrences:
            raise ValueError("Occurrence identity reused")
        occurrences.add(event["execution_id"])
        for key in ("branch_id", "parent_branch_id", "before_id", "rule_digest"):
            content_ref(event[key])
        if event["parent_branch_id"] not in produced:
            raise ValueError("Missing parent branch")
        parent = states[event["parent_branch_id"]]
        if event["rule_id"] not in rules or event["rule_digest"] != content_identity(rules[event["rule_id"]]):
            raise ValueError("Rule binding mismatch")
        if event["before_id"] != parent["graph_id"] or event["branch_id"] != _branch(root, parent["path"] + [event["rule_digest"]]):
            raise ValueError("Event source/path binding mismatch")
        for causal_parent in _list(event["parents"], MAX_ATTEMPTS):
            if causal_parent not in completed:
                raise ValueError("Causal parent is not a prior completed rewrite")
            ancestor_path = states[completed[causal_parent]]["path"]
            if parent["path"][:len(ancestor_path)] != ancestor_path:
                raise ValueError("Causal parent belongs to a different branch")
        for field in ("reads", "writes"):
            for item in _list(event[field], MAX_NODES + MAX_EDGES + 4):
                text(item)
        _list(event["checks"], 17)
        if event["status"] == "COMPLETE":
            validate_identity(event["result_id"], "result")
            if event["result_id"] in occurrences or event["refusal"] is not None:
                raise ValueError("Invalid completed occurrence")
            occurrences.add(event["result_id"])
            completed[event["result_id"]] = event["branch_id"]
            content_ref(event["after_id"])
            if event["branch_id"] not in states:
                raise ValueError("Missing result branch")
            state = states[event["branch_id"]]
            if event["branch_id"] in produced or state["parent_branch_id"] != parent["branch_id"] or state["graph_id"] != event["after_id"]:
                raise ValueError("Result branch binding mismatch")
            produced.add(event["branch_id"])
        elif event["status"] != "REFUSE" or event["result_id"] is not None or event["after_id"] is not None:
            raise ValueError("Invalid refused occurrence")
        else:
            text(event["refusal"])
            if event["reads"] or event["writes"] or event["parents"] or event["checks"]:
                raise ValueError("Refusal cannot claim completed dependencies or checks")
    if produced != set(states):
        raise ValueError("Branch lacks a completed rewrite event")
    return record


def inspect(record):
    """Static integrity inspection. No rewrite application or provider execution."""
    _history(record)
    return {"schema": "ciw.hypergraph-inspection.v1", "record_digest": record["record_digest"],
            "status": record["status"], "mode": record["mode"], "states": len(record["states"]),
            "attempts": record["attempt_count"], "limits_hit": deepcopy(record["limits_hit"]),
            "fresh_execution": False, "rewrite_validation": "not_performed", "authority": deepcopy(AUTHORITY)}


def _projection(record):
    result = deepcopy(record)
    for key in ("execution_id", "result_id", "record_digest"):
        result.pop(key)
    indices = {event["result_id"]: index for index, event in enumerate(result["events"]) if event["result_id"] is not None}
    for event in result["events"]:
        event.pop("execution_id"); event.pop("result_id")
        event["parents"] = sorted(indices[parent] for parent in event["parents"])
    return result


def verify(record):
    """Fresh deterministic replay compares results, refusals and causal dependencies."""
    _history(record)
    if record["runtime"] != runtime_identity():
        raise ValueError("Rewrite runtime differs from retained source identity")
    replay = _compute(record["request"], record["mode"])
    if _projection(record) != _projection(replay):
        raise ValueError("Retained derivation differs from fresh replay")
    return seal({"schema": "ciw.hypergraph-verification.v1", "verification_id": new_identity("verification"),
                 "candidate_record_digest": record["record_digest"], "replay_execution_id": replay["execution_id"],
                 "status": "PASS", "scope": "bounded_typed_rewrite_replay",
                 "derivation_complete": record["status"] == "COMPLETE",
                 "exploration_complete": record["mode"] == "explore" and record["status"] == "COMPLETE",
                 "authority": deepcopy(AUTHORITY)})


def compare_orders(request, left, right):
    validate_request(request)
    if sorted(left) != sorted(right):
        raise ValueError("Order comparison requires the same rule multiset")
    histories = []
    for schedule in (left, right):
        selected = deepcopy(request)
        selected["schedule"] = schedule
        histories.append(run(selected))
        if _storage(histories[-1], 2) > (MAX_BYTES - 8192) // 2:
            raise ValueError("Order history exceeds aggregate report budget; reduce request or schedule")
    accepted = all(item["status"] == "COMPLETE" for item in histories)
    ids = [item["states"][-1]["graph_id"] for item in histories]
    report = seal({"schema": "ciw.hypergraph-order-check.v1", "verification_id": new_identity("verification"),
                 "status": ("PASS" if ids[0] == ids[1] else "FAIL") if accepted else "INDETERMINATE",
                 "equivalence": "exact_identifiers_normalized_list_order", "terminal_graph_ids": ids,
                 "histories": histories, "general_confluence": "not_established",
                 "causal_invariance": "not_established", "authority": deepcopy(AUTHORITY)})
    if _storage(report) > MAX_BYTES:
        raise ValueError("Order report exceeds aggregate byte budget")
    return report


def example_request(profile="thermal"):
    quantity = lambda value, unit: {"value": value, "unit": unit}
    if profile == "thermal":
        node = {"id": "zone", "kind": "thermal_zone", "frame": "bench", "ports": {"thermal": "W"}, "parameters": {
            "heat_capacity": quantity(100, "J/K"), "temperature": quantity(300, "K")}}
        rules = [{"rule_id": "refine", "kind": "split_thermal", "source": "zone", "expected": content_identity(node),
                  "children": ["zone_left", "zone_right"], "capacities": [40, 60], "interface": "interface",
                  "conductance": quantity(2, "W/K"), "atol": 1e-12}]
        invariants = [{"kind": "thermal_zone", "parameter": "heat_capacity", "unit": "J/K", "expected": 100, "atol": 1e-12}]
        nodes = [node]
    elif profile == "ensemble":
        a = {"id": "model_a", "kind": "model", "frame": "bench", "ports": {"comparison": "1"}, "parameters": {"gain": quantity(1, "1")}}
        b = {**deepcopy(a), "id": "model_b"}
        nodes = [a, b]
        rules = [{"rule_id": "replicate", "kind": "replicate", "source": "model_a", "target": "model_c", "expected": content_identity(a)}]
        for name, node, value in (("vary_a", a, 1.1), ("vary_b", b, 0.9)):
            rules.append({"rule_id": name, "kind": "parameter", "node": node["id"], "parameter": "gain",
                          "expected": quantity(1, "1"), "value": value, "unit": "1", "minimum": 0.5,
                          "maximum": 1.5, "qualification": None})
        rules.append({"rule_id": "couple", "kind": "couple", "edge": {"id": "ensemble", "kind": "ensemble_relation",
                      "unit": "1", "frame": "bench", "parameters": {}, "endpoints": [
                          {"node": name, "role": role, "kind": "model", "port": "comparison"}
                          for name, role in zip(("model_a", "model_b", "model_c"), ("primary", "comparison", "replica"))]}})
        invariants = []
    else:
        raise ValueError("Unknown rewrite example profile")
    return {"schema": "ciw.hypergraph-request.v1", "graph": {"schema": "ciw.hypergraph.v1", "nodes": nodes, "edges": []},
            "rules": rules, "schedule": [rule["rule_id"] for rule in rules], "invariants": invariants,
            "limits": {"states": 32, "depth": 5, "attempts": 128}}
