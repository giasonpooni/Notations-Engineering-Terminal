from copy import deepcopy
import json
import subprocess
import sys

import pytest

from ciw.control_contracts import state
from ciw.control_plane import builtin_registry
from ciw.operations.runner import seal
from ciw.preservation_contracts import (
    admission_gate_from_spec,
    compose_contracts,
    contract_from_spec,
    validate_admission_gate,
    validate_composition,
    validate_contract,
    validate_verification,
    verification_from_spec,
)
from ciw.representation_morphisms import registry_from_specs
from ciw.semantic_capabilities import builtin_semantic_registry


def semantic():
    concrete = builtin_registry(bind=True)
    return builtin_semantic_registry(concrete)


def rep(rep_id):
    return {
        "representation_id": rep_id,
        "role": "STATE",
        "source_state_type": "synthetic-preservation-fixture",
        "schema_id": "ciw.synthetic-preservation-state.v1",
        "quantity_semantics": "synthetic preservation properties",
        "unit_semantics": "declared by property contract",
        "frame_semantics": "synthetic",
        "time_semantics": "one transformation step",
        "scale": {
            "length_m": None,
            "time_s": None,
            "energy_j": None,
            "resolution": None,
            "label": "synthetic",
        },
        "uncertainty_semantics": "not established by this fixture",
        "equivalence_contract": "TASK_SPECIFIC",
        "preserved_queries": [],
        "supported_interventions": [],
        "recovery_route": None,
        "provenance_refs": [],
        "notes": "Synthetic representation for preservation-contract tests.",
    }


def morphism(morphism_id, domain, codomain):
    return {
        "morphism_id": morphism_id,
        "kind": "TRANSFORM",
        "domain_representation_id": domain,
        "codomain_representation_id": codomain,
        "semantic_capability": None,
        "parameter_names": [],
        "preconditions": [],
        "validity": {
            "assumptions": ["synthetic fixture"],
            "operating_regime": ["declared test domain"],
            "failure_conditions": [],
        },
        "preservation": {
            "queries": [],
            "interventions": [],
            "invariants": [],
            "approximation_tolerance": None,
        },
        "loss": {
            "class": "TASK_SPECIFIC",
            "description": "Typed preservation contract supplies finer property effects.",
            "metrics": {},
        },
        "uncertainty": {"behavior": "UNKNOWN", "method": None},
        "reversibility": "UNKNOWN",
        "authority_requirements": [],
        "verification_requirements": [],
        "provenance_refs": [],
        "notes": "Synthetic morphism for composition tests.",
    }


def registry():
    sem = semantic()
    value = registry_from_specs(
        [rep("preservation.fine.v1"), rep("preservation.mid.v1"), rep("preservation.coarse.v1")],
        [
            morphism("preservation.first.v1", "preservation.fine.v1", "preservation.mid.v1"),
            morphism("preservation.second.v1", "preservation.mid.v1", "preservation.coarse.v1"),
        ],
        sem,
    )
    return sem, value


def preserve(property_id):
    return {
        "property_id": property_id,
        "effect": "PRESERVE",
        "output_property_id": property_id,
        "transform_id": None,
        "bound": None,
        "notes": "",
    }


def transform(source, target, rule):
    return {
        "property_id": source,
        "effect": "TRANSFORM",
        "output_property_id": target,
        "transform_id": rule,
        "bound": None,
        "notes": "",
    }


def bound(property_id, upper, metric="absolute-error", unit="Pa"):
    return {
        "property_id": property_id,
        "effect": "BOUND",
        "output_property_id": property_id,
        "transform_id": None,
        "bound": {
            "metric": metric,
            "upper_bound": upper,
            "unit": unit,
            "composition": "ADDITIVE_ABSOLUTE",
        },
        "notes": "",
    }


def forget(property_id):
    return {
        "property_id": property_id,
        "effect": "FORGET",
        "output_property_id": None,
        "transform_id": None,
        "bound": None,
        "notes": "",
    }


def first_spec():
    return {
        "contract_id": "preservation.contract-first.v1",
        "morphism_id": "preservation.first.v1",
        "requires": [],
        "effects": [
            preserve("physical.mass.v1"),
            transform("state.position.v1", "state.centroid.v1", "transform.centroid.v1"),
            bound("error.pressure.v1", 1.0),
            forget("detail.microstate.v1"),
        ],
        "notes": "First typed preservation contract.",
    }


def second_spec(requires=None, pressure_effect=None):
    return {
        "contract_id": "preservation.contract-second.v1",
        "morphism_id": "preservation.second.v1",
        "requires": requires if requires is not None else [
            "physical.mass.v1",
            "state.centroid.v1",
            "error.pressure.v1",
        ],
        "effects": [
            preserve("physical.mass.v1"),
            transform(
                "state.centroid.v1",
                "state.region-centroid.v1",
                "transform.region-centroid.v1",
            ),
            pressure_effect if pressure_effect is not None else bound("error.pressure.v1", 2.0),
        ],
        "notes": "Second typed preservation contract.",
    }


def contracts():
    sem, reg = registry()
    left = contract_from_spec(reg, sem, first_spec())
    right = contract_from_spec(reg, sem, second_spec())
    return sem, reg, left, right


def fake_ref(char):
    return "sha256:" + char * 64


def verification_spec(contract, statuses=None):
    statuses = statuses or {}
    checks = []
    for property_id in contract["requires"]:
        status = statuses.get(("REQUIRE", property_id), "VERIFIED")
        checks.append({
            "kind": "REQUIRE",
            "property_id": property_id,
            "status": status,
            "method": "EXACT_ALGEBRA" if status != "UNRESOLVED" else "NOT_PERFORMED",
            "evidence_ref": fake_ref("a") if status != "UNRESOLVED" else None,
            "notes": "",
        })
    for effect in contract["effects"]:
        key = (effect["effect"], effect["property_id"])
        status = statuses.get(key, "VERIFIED")
        checks.append({
            "kind": effect["effect"],
            "property_id": effect["property_id"],
            "status": status,
            "method": "EXACT_ALGEBRA" if status != "UNRESOLVED" else "NOT_PERFORMED",
            "evidence_ref": fake_ref("b") if status != "UNRESOLVED" else None,
            "notes": "",
        })
    return {
        "verification_id": "preservation.verification-test.v1",
        "source_state_ref": fake_ref("c"),
        "candidate_state_ref": fake_ref("d"),
        "checks": checks,
        "notes": "Synthetic verification receipt.",
    }


def test_contract_is_exactly_bound_to_scientific_morphism():
    sem, reg = registry()
    contract = contract_from_spec(reg, sem, first_spec())
    checked = validate_contract(contract, reg, sem)
    morphism = reg["morphisms"]["preservation.first.v1"]
    assert checked["registry_ref"] == reg["record_digest"]
    assert checked["morphism_ref"] == morphism["record_digest"]
    assert checked["domain_representation_id"] == "preservation.fine.v1"
    assert checked["codomain_representation_id"] == "preservation.mid.v1"
    assert checked["claims"]["unmentioned_properties_are_unknown"] is True
    assert checked["claims"]["state_admission_authority"] is False


def test_contract_refuses_unknown_morphism():
    sem, reg = registry()
    spec = first_spec()
    spec["morphism_id"] = "preservation.missing.v1"
    with pytest.raises(ValueError, match="unknown scientific morphism"):
        contract_from_spec(reg, sem, spec)


def test_contract_refuses_multiple_effects_for_same_source_property():
    sem, reg = registry()
    spec = first_spec()
    spec["effects"].append(preserve("physical.mass.v1"))
    with pytest.raises(ValueError, match="multiple effects"):
        contract_from_spec(reg, sem, spec)


def test_preserve_requires_identity_on_property():
    sem, reg = registry()
    spec = first_spec()
    spec["effects"][0]["output_property_id"] = "physical.other.v1"
    with pytest.raises(ValueError, match="identical output"):
        contract_from_spec(reg, sem, spec)


def test_forget_cannot_smuggle_output_property():
    sem, reg = registry()
    spec = first_spec()
    spec["effects"][-1]["output_property_id"] = "detail.recovered.v1"
    with pytest.raises(ValueError, match="FORGET"):
        contract_from_spec(reg, sem, spec)


def test_composition_preserves_transforms_bounds_and_loss_without_upgrading_unknowns():
    sem, reg, left, right = contracts()
    value = compose_contracts(reg, sem, left, right)
    assert value["status"] == "COMPOSABLE"
    assert {row["status"] for row in value["requirement_checks"]} == {"PASS"}
    rows = {row["source_property_id"]: row for row in value["lineages"]}
    assert rows["physical.mass.v1"]["effect"] == "PRESERVE"
    assert rows["state.position.v1"]["effect"] == "TRANSFORM"
    assert rows["state.position.v1"]["output_property_id"] == "state.region-centroid.v1"
    assert rows["state.position.v1"]["transform_chain"] == [
        "transform.centroid.v1",
        "transform.region-centroid.v1",
    ]
    assert rows["error.pressure.v1"]["effect"] == "BOUND"
    assert rows["error.pressure.v1"]["bound"]["upper_bound"] == 3.0
    assert rows["detail.microstate.v1"]["effect"] == "FORGET"


def test_downstream_requirement_on_explicitly_forgotten_property_refuses_composition():
    sem, reg = registry()
    left = contract_from_spec(reg, sem, first_spec())
    right = contract_from_spec(
        reg,
        sem,
        second_spec(requires=["detail.microstate.v1"]),
    )
    value = compose_contracts(reg, sem, left, right)
    assert value["status"] == "REFUSED"
    assert value["requirement_checks"] == [{
        "property_id": "detail.microstate.v1",
        "status": "FAIL",
        "reason": "explicitly_unavailable_after_left_contract",
    }]


def test_unestablished_requirement_is_unresolved_not_assumed():
    sem, reg = registry()
    left = contract_from_spec(reg, sem, first_spec())
    right = contract_from_spec(
        reg,
        sem,
        second_spec(requires=["state.velocity.v1"]),
    )
    value = compose_contracts(reg, sem, left, right)
    assert value["status"] == "UNRESOLVED"
    assert value["requirement_checks"][0]["status"] == "UNRESOLVED"


def test_unsupported_bound_transform_chain_is_unresolved():
    sem, reg = registry()
    left = contract_from_spec(reg, sem, first_spec())
    right = contract_from_spec(
        reg,
        sem,
        second_spec(
            requires=[],
            pressure_effect=transform(
                "error.pressure.v1",
                "error.normalized-pressure.v1",
                "transform.normalize-pressure.v1",
            ),
        ),
    )
    value = compose_contracts(reg, sem, left, right)
    rows = {row["source_property_id"]: row for row in value["lineages"]}
    assert value["status"] == "UNRESOLVED"
    assert rows["error.pressure.v1"]["effect"] == "UNKNOWN"
    assert rows["error.pressure.v1"]["reason"] == "effect_combination_has_no_v1_composition_rule"


def test_resealed_fake_composable_result_fails_recomputation():
    sem, reg, left, right = contracts()
    value = compose_contracts(reg, sem, left, right)
    forged = deepcopy(value)
    forged["status"] = "REFUSED"
    forged.pop("record_digest")
    forged = seal(forged)
    with pytest.raises(ValueError, match="differs from recomputed"):
        validate_composition(forged, reg, sem, left, right)


def test_verification_requires_exact_obligation_coverage():
    sem, reg, left, _ = contracts()
    spec = verification_spec(left)
    spec["checks"].pop()
    with pytest.raises(ValueError, match="exactly cover"):
        verification_from_spec(reg, sem, left, spec)


def test_verified_receipt_does_not_admit_state():
    sem, reg, left, _ = contracts()
    value = verification_from_spec(reg, sem, left, verification_spec(left))
    checked = validate_verification(value, reg, sem, left)
    assert checked["status"] == "VERIFIED"
    assert checked["claims"]["verification_is_not_admission"] is True
    assert checked["claims"]["canonical_state_mutated"] is False


def test_unresolved_check_keeps_verification_unresolved():
    sem, reg, left, _ = contracts()
    key = ("PRESERVE", "physical.mass.v1")
    value = verification_from_spec(
        reg, sem, left, verification_spec(left, {key: "UNRESOLVED"})
    )
    assert value["status"] == "UNRESOLVED"


def test_refuted_check_refutes_verification():
    sem, reg, left, _ = contracts()
    key = ("PRESERVE", "physical.mass.v1")
    value = verification_from_spec(
        reg, sem, left, verification_spec(left, {key: "REFUTED"})
    )
    assert value["status"] == "REFUTED"


def test_resolved_verification_requires_evidence():
    sem, reg, left, _ = contracts()
    spec = verification_spec(left)
    spec["checks"][0]["evidence_ref"] = None
    with pytest.raises(ValueError, match="canonical SHA256"):
        verification_from_spec(reg, sem, left, spec)


def test_not_performed_cannot_claim_verified():
    sem, reg, left, _ = contracts()
    spec = verification_spec(left)
    spec["checks"][0]["method"] = "NOT_PERFORMED"
    with pytest.raises(ValueError, match="Resolved verification"):
        verification_from_spec(reg, sem, left, spec)


def test_admission_gate_can_only_declare_eligibility():
    sem, reg, left, _ = contracts()
    verification = verification_from_spec(reg, sem, left, verification_spec(left))
    gate = admission_gate_from_spec(
        reg,
        sem,
        left,
        verification,
        {
            "gate_id": "preservation.admission-test.v1",
            "forbidden_forgets": [],
            "notes": "",
        },
    )
    assert gate["decision"] == "ELIGIBLE"
    assert gate["claims"]["eligibility_not_admission"] is True
    assert gate["claims"]["state_admission_performed"] is False
    assert gate["claims"]["canonical_state_mutated"] is False


def test_admission_gate_refuses_forbidden_information_loss():
    sem, reg, left, _ = contracts()
    verification = verification_from_spec(reg, sem, left, verification_spec(left))
    gate = admission_gate_from_spec(
        reg,
        sem,
        left,
        verification,
        {
            "gate_id": "preservation.admission-loss-policy.v1",
            "forbidden_forgets": ["detail.microstate.v1"],
            "notes": "",
        },
    )
    assert gate["decision"] == "REFUSED"
    assert gate["policy_violations"] == ["detail.microstate.v1"]


@pytest.mark.parametrize("required_only", [False, True])
def test_admission_gate_unknown_protected_effect_remains_unresolved(required_only):
    sem, reg = registry()
    spec = first_spec()
    protected = "safety.interlock.v1"
    if required_only:
        spec["requires"] = [protected]
    contract = contract_from_spec(reg, sem, spec)
    verification = verification_from_spec(reg, sem, contract, verification_spec(contract))
    gate = admission_gate_from_spec(reg, sem, contract, verification, {
        "gate_id": "preservation.admission-unknown.v1", "forbidden_forgets": [protected], "notes": ""})
    assert verification["status"] == "VERIFIED"
    assert gate["decision"] == "UNRESOLVED"
    assert gate["reasons"] == ["loss_policy_properties_not_established_by_contract"]
    assert gate["policy_violations"] == []  # Unknown loss is not a demonstrated FORGET.
    assert gate["claims"]["state_admission_performed"] is False
    validate_admission_gate(gate, reg, sem, contract, verification)
    forged = deepcopy(gate)
    forged.update(decision="ELIGIBLE", reasons=["preservation_contract_verified_under_declared_loss_policy"])
    seal(forged)
    with pytest.raises(ValueError, match="contradicts"):
        validate_admission_gate(forged, reg, sem, contract, verification)


def test_known_forbidden_loss_takes_precedence_over_unknown_protected_property():
    sem, reg, contract, _ = contracts()
    verification = verification_from_spec(reg, sem, contract, verification_spec(contract))
    gate = admission_gate_from_spec(reg, sem, contract, verification, {
        "gate_id": "preservation.admission-loss-unknown.v1",
        "forbidden_forgets": ["detail.microstate.v1", "safety.interlock.v1"], "notes": ""})
    assert gate["decision"] == "REFUSED"
    assert gate["policy_violations"] == ["detail.microstate.v1"]


@pytest.mark.parametrize("protected", ["physical.mass.v1", "state.position.v1", "error.pressure.v1"])
def test_verified_declared_nonforget_effects_satisfy_loss_policy(protected):
    sem, reg, contract, _ = contracts()
    verification = verification_from_spec(reg, sem, contract, verification_spec(contract))
    gate = admission_gate_from_spec(reg, sem, contract, verification, {
        "gate_id": "preservation.admission-declared.v1", "forbidden_forgets": [protected], "notes": ""})
    assert gate["decision"] == "ELIGIBLE"


def test_admission_gate_propagates_unresolved_verification():
    sem, reg, left, _ = contracts()
    verification = verification_from_spec(
        reg,
        sem,
        left,
        verification_spec(left, {("BOUND", "error.pressure.v1"): "UNRESOLVED"}),
    )
    gate = admission_gate_from_spec(
        reg,
        sem,
        left,
        verification,
        {
            "gate_id": "preservation.admission-unresolved.v1",
            "forbidden_forgets": [],
            "notes": "",
        },
    )
    assert gate["decision"] == "UNRESOLVED"


def test_admission_gate_propagates_refutation():
    sem, reg, left, _ = contracts()
    verification = verification_from_spec(
        reg,
        sem,
        left,
        verification_spec(left, {("BOUND", "error.pressure.v1"): "REFUTED"}),
    )
    gate = admission_gate_from_spec(
        reg,
        sem,
        left,
        verification,
        {
            "gate_id": "preservation.admission-refuted.v1",
            "forbidden_forgets": [],
            "notes": "",
        },
    )
    assert gate["decision"] == "REFUSED"


def test_resealed_fake_eligibility_fails_gate_recomputation():
    sem, reg, left, _ = contracts()
    verification = verification_from_spec(reg, sem, left, verification_spec(left))
    gate = admission_gate_from_spec(
        reg,
        sem,
        left,
        verification,
        {
            "gate_id": "preservation.admission-forge.v1",
            "forbidden_forgets": ["detail.microstate.v1"],
            "notes": "",
        },
    )
    forged = deepcopy(gate)
    forged["decision"] = "ELIGIBLE"
    forged.pop("record_digest")
    forged = seal(forged)
    with pytest.raises(ValueError, match="contradicts"):
        validate_admission_gate(forged, reg, sem, left, verification)


def test_cli_create_compose_verify_and_gate(tmp_path):
    sem, reg = registry()
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(reg), encoding="utf-8")

    left_spec_path = tmp_path / "left-spec.json"
    right_spec_path = tmp_path / "right-spec.json"
    left_spec_path.write_text(json.dumps(first_spec()), encoding="utf-8")
    right_spec_path.write_text(json.dumps(second_spec()), encoding="utf-8")
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"

    for spec_path, output in ((left_spec_path, left_path), (right_spec_path, right_path)):
        subprocess.run([
            sys.executable, "-m", "ciw.net", "preservation", "create",
            str(registry_path), str(spec_path), "--output", str(output),
        ], check=True)

    composition_path = tmp_path / "composition.json"
    subprocess.run([
        sys.executable, "-m", "ciw.net", "preservation", "compose",
        str(registry_path), str(left_path), str(right_path), "--output", str(composition_path),
    ], check=True)
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    assert composition["status"] == "COMPOSABLE"

    left = json.loads(left_path.read_text(encoding="utf-8"))
    verification_spec_path = tmp_path / "verification-spec.json"
    verification_spec_path.write_text(json.dumps(verification_spec(left)), encoding="utf-8")
    verification_path = tmp_path / "verification.json"
    subprocess.run([
        sys.executable, "-m", "ciw.net", "preservation", "verify",
        str(registry_path), str(left_path), str(verification_spec_path),
        "--output", str(verification_path),
    ], check=True)

    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({
        "gate_id": "preservation.cli-admission.v1",
        "forbidden_forgets": [],
        "notes": "",
    }), encoding="utf-8")
    gate_path = tmp_path / "gate.json"
    subprocess.run([
        sys.executable, "-m", "ciw.net", "preservation", "gate",
        str(registry_path), str(left_path), str(verification_path), str(policy_path),
        "--output", str(gate_path),
    ], check=True)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate["decision"] == "ELIGIBLE"
    assert gate["claims"]["state_admission_performed"] is False
