"""Read-only dependency projection over existing retained artifact identities.

Edges come from retained catalog links and explicit operation inputs. They do
not authenticate observations, establish a physical claim, or grant authority.
Unresolved native and legibility input references are reported rather than
guessed. Declared references project current dependency use; their presence
does not authenticate a referenced execution, verification, or qualification.
"""
from __future__ import annotations

from copy import deepcopy


def _legibility_input_refs(parameters):
    """Read only reference slots in the closed representation contract.

    Labels, property values, assumptions, relation names and target versions
    are not computational identities. References outside this Session remain
    unresolved; no artifact or verification authority is invented for them.
    """
    from .legibility import validate_contract

    contract = validate_contract(parameters["contract"])
    bindings = contract["bindings"]
    refs = {bindings["evidence_id"], bindings["execution_id"]}
    if bindings["verification_id"] is not None:
        refs.add(bindings["verification_id"])
    semantics = contract["semantics"]
    refs.update(relation["target_id"] for relation in semantics["relationships"])
    for declaration in [*semantics["properties"], *contract["claims"]]:
        refs.update(declaration["evidence_refs"])
    for field in ("calibration_refs", "verification_refs"):
        refs.update(contract["qualification"][field])
    return refs


def artifact_graph(run: dict, results: dict, executions: dict, workbench) -> dict:
    """Return nodes keyed by their existing identity, without invoking providers."""
    from .operations.schemas import dependency_result_ids
    nodes = {run["evidence_id"]: {
        "kind": "evidence", "dependencies": [], "run_id": run["run_id"],
    }}

    def add(identity, kind, dependencies=(), **metadata):
        node = {"kind": kind, "dependencies": sorted(set(dependencies)), **metadata}
        if identity in nodes and nodes[identity] != node:
            raise ValueError("Dependency projection contains conflicting artifact identities")
        nodes[identity] = node

    # Capture the Workbench in one lock, not separately changing read surfaces.
    retained = workbench.dependency_artifacts()
    sources = {source["source_id"]: source for source in retained["sources"]}
    bundles = {bundle["bundle_id"]: bundle for bundle in retained["bundles"]}
    for source in sources.values():
        nodes.setdefault(source["evidence_id"], {"kind": "evidence", "dependencies": []})
        add(source["source_id"], "source", [source["evidence_id"]],
            source_kind=source["kind"], evidence_id=source["evidence_id"], label=source["label"])
    for execution in executions.values():
        deps = [execution["evidence_id"]]
        if execution["operation_id"] == "system.compile.v1":
            source_id = execution.get("parameters", {}).get("source_id")
            if type(source_id) is str and source_id in sources:
                deps.append(source_id)
        deps.extend(dependency_result_ids(execution["operation_id"], execution.get("parameters", {})))
        upstream = execution.get("parameters", {}).get("source_result_id")
        if upstream is not None:
            deps.append(upstream)
        add(execution["execution_id"], "execution", deps,
            operation_id=execution["operation_id"], outcome=execution["status"])
    for result in results.values():
        deps = [result["evidence_id"]]
        if result["operation_id"] == "system.compile.v1":
            source_id = result.get("parameters", {}).get("source_id")
            if type(source_id) is str and source_id in sources:
                deps.append(source_id)
        deps.extend(dependency_result_ids(result["operation_id"], result.get("parameters", {})))
        upstream = result.get("parameters", {}).get("source_result_id")
        if upstream is not None:
            deps.append(upstream)
        if result["execution_id"] in executions:
            deps.append(result["execution_id"])
        else:
            # Flat legacy analyses retain the occurrence inside the result.
            add(result["execution_id"], "execution", deps,
                operation_id=result["operation_id"], outcome="completed")
            deps = [result["execution_id"]]
        add(result["result_id"], "result", deps, operation_id=result["operation_id"])
    # Native step inputs can refer to another native result, bundle or evidence.
    # Allocate all outputs before resolving the explicit references.
    for execution in retained["executions"]:
        deps = [execution["source_id"]]
        bundle = bundles[execution["bundle_id"]]
        deps.extend(bundle.get("upstream_bundle_ids", []))
        if bundle["upstream_bundle_id"] is not None:
            deps.append(bundle["upstream_bundle_id"])
        add(execution["execution_id"], "execution", deps,
            operation_id=execution["operation_id"], outcome=execution["status"])
        add(execution["result_id"], "result", [execution["execution_id"]],
            operation_id=execution["operation_id"])
    for bundle in bundles.values():
        deps = [bundle["source_id"], *bundle["result_ids"], *bundle.get("upstream_bundle_ids", [])]
        if bundle["upstream_bundle_id"] is not None:
            deps.append(bundle["upstream_bundle_id"])
        add(bundle["bundle_id"], "bundle", deps, source_kind=bundle["kind"])
    # Resolve the representation's declared inputs only after native and
    # recording outputs have all been allocated. A refused compilation has no
    # result and no validated contract, so it contributes no inferred edges.
    for result in results.values():
        if result["operation_id"] != "legibility.compile.v1":
            continue
        refs = _legibility_input_refs(result["parameters"])
        for identity in (result["execution_id"], result["result_id"]):
            node = nodes[identity]
            node["dependencies"] = sorted(set(node["dependencies"]) | {ref for ref in refs if ref in nodes})
            unresolved = sorted(refs - nodes.keys())
            if unresolved:
                node["unresolved_input_refs"] = unresolved
    for execution in retained["executions"]:
        node = nodes[execution["execution_id"]]
        refs = execution["input_refs"]
        node["dependencies"] = sorted(set(node["dependencies"]) | {ref for ref in refs if ref in nodes})
        unresolved = sorted({ref for ref in refs if ref not in nodes})
        if unresolved:
            node["unresolved_input_refs"] = unresolved
    return deepcopy(nodes)
