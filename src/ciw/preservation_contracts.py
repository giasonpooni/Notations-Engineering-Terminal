"""Typed preservation contracts beneath proposal/search machinery.

This module makes preservation, transformation, bounded error, explicit loss and
requirements first-class records over existing scientific morphisms. It does
not execute providers, mutate canonical state or admit a candidate.

V1 deliberately implements a small composition algebra:
- FORGET is absorbing for a tracked source property;
- PRESERVE composes transparently;
- TRANSFORM chains named transformation rules;
- additive absolute BOUND contracts compose only when metric/unit/rule match;
- missing or unsupported combinations remain UNKNOWN/UNRESOLVED;
- downstream REQUIRE obligations must be explicitly available, otherwise the
  composition is REFUSED or UNRESOLVED.

The narrow rules are intentional: absence of a proof rule never becomes a
preservation claim.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from .control_contracts import content_ref, detached, keys, record, text
from .operations.runner import check_seal
from .representation_morphisms import validate_registry
from .semantic_capabilities import SemanticRegistry

ID = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_-]*)+\.v[1-9][0-9]*$")
EFFECTS = {"PRESERVE", "TRANSFORM", "BOUND", "FORGET"}
LINEAGE_EFFECTS = EFFECTS | {"UNKNOWN"}
BOUND_COMPOSITION = {"ADDITIVE_ABSOLUTE"}
VERIFICATION_STATUS = {"VERIFIED", "REFUTED", "UNRESOLVED"}
VERIFICATION_METHODS = {
    "NOT_PERFORMED",
    "FORMAL_PROOF",
    "EXACT_ALGEBRA",
    "NUMERICAL_BOUND",
    "STATISTICAL_BOUND",
    "EMPIRICAL_TEST",
    "CALIBRATION_EVIDENCE",
    "HUMAN_ATTESTATION",
}
COMPOSITION_STATUS = {"COMPOSABLE", "REFUSED", "UNRESOLVED"}
ADMISSION_DECISIONS = {"ELIGIBLE", "REFUSED", "UNRESOLVED"}
MAX_ITEMS = 128


def _versioned(value: Any, label: str) -> str:
    if type(value) is not str or ID.fullmatch(value) is None or len(value) > 180:
        raise ValueError(f"{label} must be a bounded versioned hierarchical ID")
    return value


def _unique_properties(values: Any, label: str) -> list[str]:
    if type(values) is not list or len(values) > MAX_ITEMS:
        raise ValueError(f"{label} must be a bounded list")
    result = [_versioned(value, label) for value in values]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains duplicates")
    return result


def _notes(value: Any, label: str, limit: int = 4096) -> str:
    if type(value) is not str or len(value) > limit:
        raise ValueError(f"{label} must be bounded text")
    return value


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ValueError(f"{label} must be numerical")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 1e150:
        raise ValueError(f"{label} must be finite, nonnegative and bounded")
    return result


def _bound(value: Any) -> dict:
    keys(value, {"metric", "upper_bound", "unit", "composition"})
    if value["composition"] not in BOUND_COMPOSITION:
        raise ValueError("Unknown preservation-bound composition rule")
    return {
        "metric": text(value["metric"]),
        "upper_bound": _number(value["upper_bound"], "bound upper_bound"),
        "unit": text(value["unit"]),
        "composition": value["composition"],
    }


def _effect(value: Any) -> dict:
    keys(value, {
        "property_id", "effect", "output_property_id", "transform_id", "bound", "notes",
    })
    property_id = _versioned(value["property_id"], "property_id")
    effect = value["effect"]
    if effect not in EFFECTS:
        raise ValueError("Unknown preservation effect")
    output = value["output_property_id"]
    transform_id = value["transform_id"]
    bound = value["bound"]

    if effect == "PRESERVE":
        if output != property_id or transform_id is not None or bound is not None:
            raise ValueError("PRESERVE requires identical output property and no transform/bound")
    elif effect == "TRANSFORM":
        output = _versioned(output, "transformed output property")
        transform_id = _versioned(transform_id, "transform_id")
        if bound is not None:
            raise ValueError("TRANSFORM cannot also declare a V1 bound")
    elif effect == "BOUND":
        if output != property_id or transform_id is not None or bound is None:
            raise ValueError("BOUND requires identical output property, no transform, and a bound")
        bound = _bound(bound)
    else:
        if output is not None or transform_id is not None or bound is not None:
            raise ValueError("FORGET has no output property, transform or bound")

    return {
        "property_id": property_id,
        "effect": effect,
        "output_property_id": output,
        "transform_id": transform_id,
        "bound": deepcopy(bound),
        "notes": _notes(value["notes"], "effect notes", 2048),
    }


def _effects(values: Any) -> list[dict]:
    if type(values) is not list or not 1 <= len(values) <= MAX_ITEMS:
        raise ValueError("Preservation contract requires 1..128 effects")
    result = [_effect(value) for value in values]
    ids = [item["property_id"] for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("Preservation contract assigns multiple effects to one source property")
    return result


def contract_from_spec(registry: dict, semantic: SemanticRegistry, spec: dict) -> dict:
    """Bind a typed preservation contract to one exact scientific morphism."""
    registry = validate_registry(registry, semantic)
    keys(spec, {"contract_id", "morphism_id", "requires", "effects", "notes"})
    morphism_id = _versioned(spec["morphism_id"], "morphism_id")
    if morphism_id not in registry["morphisms"]:
        raise ValueError("Preservation contract references an unknown scientific morphism")
    morphism = registry["morphisms"][morphism_id]
    value = record(
        "preservation-contract",
        contract_id=_versioned(spec["contract_id"], "contract_id"),
        registry_ref=registry["record_digest"],
        morphism_id=morphism_id,
        morphism_ref=morphism["record_digest"],
        domain_representation_id=morphism["domain_representation_id"],
        codomain_representation_id=morphism["codomain_representation_id"],
        requires=_unique_properties(spec["requires"], "required property"),
        effects=_effects(spec["effects"]),
        notes=_notes(spec["notes"], "preservation contract notes", 8192),
        claims={
            "typed_preservation_contract": True,
            "unmentioned_properties_are_unknown": True,
            "does_not_upgrade_textual_morphism_claims": True,
            "physical_validity_established": False,
            "execution_authority": False,
            "state_admission_authority": False,
        },
    )
    validate_contract(value, registry, semantic)
    return value


def validate_contract(value: dict, registry: dict, semantic: SemanticRegistry) -> dict:
    registry = validate_registry(registry, semantic)
    keys(value, {
        "schema", "record_digest", "contract_id", "registry_ref", "morphism_id",
        "morphism_ref", "domain_representation_id", "codomain_representation_id",
        "requires", "effects", "notes", "claims",
    })
    if value["schema"] != "ciw.preservation-contract.v1":
        raise ValueError("Wrong preservation contract schema")
    check_seal(value)
    _versioned(value["contract_id"], "contract_id")
    if value["registry_ref"] != registry["record_digest"]:
        raise ValueError("Preservation contract is bound to a different morphism registry")
    content_ref(value["registry_ref"])
    morphism_id = _versioned(value["morphism_id"], "morphism_id")
    if morphism_id not in registry["morphisms"]:
        raise ValueError("Preservation contract morphism is absent from registry")
    morphism = registry["morphisms"][morphism_id]
    if value["morphism_ref"] != morphism["record_digest"]:
        raise ValueError("Preservation contract morphism identity differs from registry")
    content_ref(value["morphism_ref"])
    for field in ("domain_representation_id", "codomain_representation_id"):
        _versioned(value[field], field)
    if value["domain_representation_id"] != morphism["domain_representation_id"]:
        raise ValueError("Preservation contract domain differs from scientific morphism")
    if value["codomain_representation_id"] != morphism["codomain_representation_id"]:
        raise ValueError("Preservation contract codomain differs from scientific morphism")
    _unique_properties(value["requires"], "required property")
    _effects(value["effects"])
    _notes(value["notes"], "preservation contract notes", 8192)
    if value["claims"] != {
        "typed_preservation_contract": True,
        "unmentioned_properties_are_unknown": True,
        "does_not_upgrade_textual_morphism_claims": True,
        "physical_validity_established": False,
        "execution_authority": False,
        "state_admission_authority": False,
    }:
        raise ValueError("Preservation contract claims exceed declarative scope")
    return detached(value)


def _available_after(contract: dict) -> tuple[set[str], set[str]]:
    available: set[str] = set()
    unavailable: set[str] = set()
    for effect in contract["effects"]:
        source = effect["property_id"]
        if effect["effect"] == "FORGET":
            unavailable.add(source)
        elif effect["effect"] == "TRANSFORM":
            available.add(effect["output_property_id"])
            if effect["output_property_id"] != source:
                unavailable.add(source)
        else:
            available.add(effect["output_property_id"])
    return available, unavailable


def _lineage_unknown(source: str, intermediate: str | None, reason: str) -> dict:
    return {
        "source_property_id": source,
        "effect": "UNKNOWN",
        "output_property_id": None,
        "transform_chain": [],
        "bound": None,
        "reason": reason,
        "intermediate_property_id": intermediate,
    }


def _lineage(left: dict, right_by_source: dict[str, dict]) -> dict:
    source = left["property_id"]
    if left["effect"] == "FORGET":
        return {
            "source_property_id": source,
            "effect": "FORGET",
            "output_property_id": None,
            "transform_chain": [],
            "bound": None,
            "reason": "left_contract_forgets_property",
            "intermediate_property_id": None,
        }

    intermediate = left["output_property_id"]
    right = right_by_source.get(intermediate)
    if right is None:
        return _lineage_unknown(source, intermediate, "right_contract_has_no_declared_effect")

    if right["effect"] == "FORGET":
        return {
            "source_property_id": source,
            "effect": "FORGET",
            "output_property_id": None,
            "transform_chain": [],
            "bound": None,
            "reason": "right_contract_forgets_intermediate_property",
            "intermediate_property_id": intermediate,
        }

    if left["effect"] == "PRESERVE" and right["effect"] == "PRESERVE":
        return {
            "source_property_id": source,
            "effect": "PRESERVE",
            "output_property_id": source,
            "transform_chain": [],
            "bound": None,
            "reason": "preserved_by_both_contracts",
            "intermediate_property_id": intermediate,
        }

    if left["effect"] in {"PRESERVE", "TRANSFORM"} and right["effect"] in {"PRESERVE", "TRANSFORM"}:
        chain = []
        if left["effect"] == "TRANSFORM":
            chain.append(left["transform_id"])
        if right["effect"] == "TRANSFORM":
            chain.append(right["transform_id"])
        return {
            "source_property_id": source,
            "effect": "TRANSFORM" if chain else "PRESERVE",
            "output_property_id": right["output_property_id"],
            "transform_chain": chain,
            "bound": None,
            "reason": "declared_transform_chain",
            "intermediate_property_id": intermediate,
        }

    if left["effect"] == "BOUND" and right["effect"] == "PRESERVE":
        return {
            "source_property_id": source,
            "effect": "BOUND",
            "output_property_id": source,
            "transform_chain": [],
            "bound": deepcopy(left["bound"]),
            "reason": "left_bound_preserved_by_right",
            "intermediate_property_id": intermediate,
        }

    if left["effect"] == "PRESERVE" and right["effect"] == "BOUND":
        return {
            "source_property_id": source,
            "effect": "BOUND",
            "output_property_id": source,
            "transform_chain": [],
            "bound": deepcopy(right["bound"]),
            "reason": "right_bound_after_exact_preservation",
            "intermediate_property_id": intermediate,
        }

    if left["effect"] == "BOUND" and right["effect"] == "BOUND":
        a, b = left["bound"], right["bound"]
        if (
            a["composition"] == b["composition"] == "ADDITIVE_ABSOLUTE"
            and a["metric"] == b["metric"]
            and a["unit"] == b["unit"]
        ):
            return {
                "source_property_id": source,
                "effect": "BOUND",
                "output_property_id": source,
                "transform_chain": [],
                "bound": {
                    "metric": a["metric"],
                    "upper_bound": a["upper_bound"] + b["upper_bound"],
                    "unit": a["unit"],
                    "composition": "ADDITIVE_ABSOLUTE",
                },
                "reason": "compatible_additive_absolute_bounds",
                "intermediate_property_id": intermediate,
            }
        return _lineage_unknown(source, intermediate, "bound_composition_rule_not_established")

    return _lineage_unknown(source, intermediate, "effect_combination_has_no_v1_composition_rule")


def _composition_fields(left: dict, right: dict) -> dict:
    if left["codomain_representation_id"] != right["domain_representation_id"]:
        raise ValueError("Preservation contracts do not share an exact intermediate representation")

    available, unavailable = _available_after(left)
    requirement_checks = []
    for property_id in right["requires"]:
        if property_id in available:
            status = "PASS"
            reason = "explicitly_available_from_left_contract"
        elif property_id in unavailable:
            status = "FAIL"
            reason = "explicitly_unavailable_after_left_contract"
        else:
            status = "UNRESOLVED"
            reason = "left_contract_does_not_establish_required_property"
        requirement_checks.append({
            "property_id": property_id,
            "status": status,
            "reason": reason,
        })

    right_by_source = {effect["property_id"]: effect for effect in right["effects"]}
    lineages = [_lineage(effect, right_by_source) for effect in left["effects"]]
    if any(check["status"] == "FAIL" for check in requirement_checks):
        status = "REFUSED"
    elif (
        any(check["status"] == "UNRESOLVED" for check in requirement_checks)
        or any(lineage["effect"] == "UNKNOWN" for lineage in lineages)
    ):
        status = "UNRESOLVED"
    else:
        status = "COMPOSABLE"

    return {
        "domain_representation_id": left["domain_representation_id"],
        "intermediate_representation_id": left["codomain_representation_id"],
        "codomain_representation_id": right["codomain_representation_id"],
        "requirement_checks": requirement_checks,
        "lineages": lineages,
        "status": status,
    }


def compose_contracts(
    registry: dict,
    semantic: SemanticRegistry,
    left: dict,
    right: dict,
) -> dict:
    left = validate_contract(left, registry, semantic)
    right = validate_contract(right, registry, semantic)
    fields = _composition_fields(left, right)
    value = record(
        "preservation-composition",
        left_contract_ref=left["record_digest"],
        right_contract_ref=right["record_digest"],
        **fields,
        claims={
            "finite_typed_composition_rules_applied": True,
            "universal_composition_theorem_claimed": False,
            "unknown_is_not_preservation": True,
            "execution_authority": False,
            "state_admission_authority": False,
        },
    )
    validate_composition(value, registry, semantic, left, right)
    return value


def _validate_lineage(value: dict) -> dict:
    keys(value, {
        "source_property_id", "effect", "output_property_id", "transform_chain",
        "bound", "reason", "intermediate_property_id",
    })
    _versioned(value["source_property_id"], "source_property_id")
    if value["effect"] not in LINEAGE_EFFECTS:
        raise ValueError("Unknown preservation lineage effect")
    if value["output_property_id"] is not None:
        _versioned(value["output_property_id"], "lineage output property")
    if value["intermediate_property_id"] is not None:
        _versioned(value["intermediate_property_id"], "lineage intermediate property")
    if type(value["transform_chain"]) is not list or len(value["transform_chain"]) > MAX_ITEMS:
        raise ValueError("Transform chain must be bounded")
    for item in value["transform_chain"]:
        _versioned(item, "transform chain item")
    if value["bound"] is not None:
        _bound(value["bound"])
    text(value["reason"])
    return detached(value)


def validate_composition(
    value: dict,
    registry: dict,
    semantic: SemanticRegistry,
    left: dict | None = None,
    right: dict | None = None,
) -> dict:
    keys(value, {
        "schema", "record_digest", "left_contract_ref", "right_contract_ref",
        "domain_representation_id", "intermediate_representation_id",
        "codomain_representation_id", "requirement_checks", "lineages", "status", "claims",
    })
    if value["schema"] != "ciw.preservation-composition.v1":
        raise ValueError("Wrong preservation composition schema")
    check_seal(value)
    content_ref(value["left_contract_ref"])
    content_ref(value["right_contract_ref"])
    for field in (
        "domain_representation_id", "intermediate_representation_id", "codomain_representation_id",
    ):
        _versioned(value[field], field)
    if type(value["requirement_checks"]) is not list or len(value["requirement_checks"]) > MAX_ITEMS:
        raise ValueError("Composition requirement checks must be bounded")
    for row in value["requirement_checks"]:
        keys(row, {"property_id", "status", "reason"})
        _versioned(row["property_id"], "required property")
        if row["status"] not in {"PASS", "FAIL", "UNRESOLVED"}:
            raise ValueError("Unknown composition requirement status")
        text(row["reason"])
    if type(value["lineages"]) is not list or not value["lineages"]:
        raise ValueError("Composition requires at least one retained property lineage")
    [_validate_lineage(row) for row in value["lineages"]]
    if value["status"] not in COMPOSITION_STATUS:
        raise ValueError("Unknown preservation composition status")
    if value["claims"] != {
        "finite_typed_composition_rules_applied": True,
        "universal_composition_theorem_claimed": False,
        "unknown_is_not_preservation": True,
        "execution_authority": False,
        "state_admission_authority": False,
    }:
        raise ValueError("Preservation composition claims exceed V1 rules")

    if left is not None or right is not None:
        if left is None or right is None:
            raise ValueError("Composition recomputation requires both source contracts")
        left_checked = validate_contract(left, registry, semantic)
        right_checked = validate_contract(right, registry, semantic)
        if value["left_contract_ref"] != left_checked["record_digest"]:
            raise ValueError("Composition left contract identity mismatch")
        if value["right_contract_ref"] != right_checked["record_digest"]:
            raise ValueError("Composition right contract identity mismatch")
        expected = _composition_fields(left_checked, right_checked)
        for key, expected_value in expected.items():
            if value[key] != expected_value:
                raise ValueError("Preservation composition differs from recomputed V1 rules")
    return detached(value)


def _obligations(contract: dict) -> list[tuple[str, str]]:
    rows = [("REQUIRE", property_id) for property_id in contract["requires"]]
    rows.extend((effect["effect"], effect["property_id"]) for effect in contract["effects"])
    return rows


def _verification_check(value: Any) -> dict:
    keys(value, {"kind", "property_id", "status", "method", "evidence_ref", "notes"})
    kind = value["kind"]
    if kind not in EFFECTS | {"REQUIRE"}:
        raise ValueError("Unknown preservation verification obligation kind")
    property_id = _versioned(value["property_id"], "verification property")
    status = value["status"]
    if status not in VERIFICATION_STATUS:
        raise ValueError("Unknown preservation verification status")
    method = value["method"]
    if method not in VERIFICATION_METHODS:
        raise ValueError("Unknown preservation verification method")
    evidence = value["evidence_ref"]
    if status in {"VERIFIED", "REFUTED"}:
        if method == "NOT_PERFORMED":
            raise ValueError("Resolved verification cannot use NOT_PERFORMED")
        evidence = content_ref(evidence)
    elif evidence is not None:
        evidence = content_ref(evidence)
    return {
        "kind": kind,
        "property_id": property_id,
        "status": status,
        "method": method,
        "evidence_ref": evidence,
        "notes": _notes(value["notes"], "verification check notes", 2048),
    }


def verification_from_spec(registry: dict, semantic: SemanticRegistry, contract: dict, spec: dict) -> dict:
    keys(spec, {
        "verification_id", "source_state_ref", "candidate_state_ref", "checks", "notes",
    })
    contract = validate_contract(contract, registry, semantic)
    source_ref = content_ref(spec["source_state_ref"])
    candidate_ref = content_ref(spec["candidate_state_ref"])
    checks = spec["checks"]
    if type(checks) is not list or not 1 <= len(checks) <= 2 * MAX_ITEMS:
        raise ValueError("Preservation verification requires a bounded nonempty check list")
    normalized = [_verification_check(row) for row in checks]
    keys_seen = [(row["kind"], row["property_id"]) for row in normalized]
    if len(keys_seen) != len(set(keys_seen)):
        raise ValueError("Duplicate preservation verification obligation")
    expected = set(_obligations(contract))
    if set(keys_seen) != expected:
        raise ValueError("Verification checks must exactly cover contract requirements and effects")
    if any(row["status"] == "REFUTED" for row in normalized):
        aggregate = "REFUTED"
    elif all(row["status"] == "VERIFIED" for row in normalized):
        aggregate = "VERIFIED"
    else:
        aggregate = "UNRESOLVED"
    value = record(
        "preservation-verification",
        verification_id=_versioned(spec["verification_id"], "verification_id"),
        contract_ref=contract["record_digest"],
        source_state_ref=source_ref,
        candidate_state_ref=candidate_ref,
        checks=normalized,
        status=aggregate,
        notes=_notes(spec["notes"], "preservation verification notes", 8192),
        claims={
            "typed_verification_receipt": True,
            "method_classes_not_interchangeable": True,
            "verification_is_not_admission": True,
            "canonical_state_mutated": False,
            "execution_authority": False,
        },
    )
    validate_verification(value, registry, semantic, contract)
    return value


def validate_verification(value: dict, registry: dict, semantic: SemanticRegistry, contract: dict) -> dict:
    keys(value, {
        "schema", "record_digest", "verification_id", "contract_ref",
        "source_state_ref", "candidate_state_ref", "checks", "status", "notes", "claims",
    })
    if value["schema"] != "ciw.preservation-verification.v1":
        raise ValueError("Wrong preservation verification schema")
    check_seal(value)
    _versioned(value["verification_id"], "verification_id")
    contract = validate_contract(contract, registry, semantic)
    if value["contract_ref"] != contract["record_digest"]:
        raise ValueError("Verification references a different preservation contract")
    for field in ("contract_ref", "source_state_ref", "candidate_state_ref"):
        content_ref(value[field])
    if type(value["checks"]) is not list or not value["checks"]:
        raise ValueError("Verification requires checks")
    normalized = [_verification_check(row) for row in value["checks"]]
    seen = [(row["kind"], row["property_id"]) for row in normalized]
    if len(seen) != len(set(seen)):
        raise ValueError("Duplicate preservation verification obligation")
    if set(seen) != set(_obligations(contract)):
        raise ValueError("Verification coverage differs from preservation contract")
    expected_status = (
        "REFUTED" if any(row["status"] == "REFUTED" for row in normalized)
        else "VERIFIED" if all(row["status"] == "VERIFIED" for row in normalized)
        else "UNRESOLVED"
    )
    if value["status"] != expected_status:
        raise ValueError("Verification aggregate status contradicts retained checks")
    _notes(value["notes"], "preservation verification notes", 8192)
    if value["claims"] != {
        "typed_verification_receipt": True,
        "method_classes_not_interchangeable": True,
        "verification_is_not_admission": True,
        "canonical_state_mutated": False,
        "execution_authority": False,
    }:
        raise ValueError("Preservation verification claims exceed verification scope")
    return detached(value)


def _admission_outcome(contract: dict, verification: dict, forbidden: list[str]) -> tuple[list[str], str, list[str]]:
    """An omitted protected property is unknown, never evidence of no loss."""
    forgotten = {
        effect["property_id"] for effect in contract["effects"] if effect["effect"] == "FORGET"
    }
    violations = sorted(set(forbidden) & forgotten)
    declared = {effect["property_id"] for effect in contract["effects"]}
    if violations:
        decision = "REFUSED"
        reasons = ["contract_forgets_policy_required_properties"]
    elif verification["status"] == "REFUTED":
        decision = "REFUSED"
        reasons = ["preservation_verification_refuted"]
    elif set(forbidden) - declared:
        decision = "UNRESOLVED"
        reasons = ["loss_policy_properties_not_established_by_contract"]
    elif verification["status"] == "UNRESOLVED":
        decision = "UNRESOLVED"
        reasons = ["preservation_verification_unresolved"]
    else:
        decision = "ELIGIBLE"
        reasons = ["preservation_contract_verified_under_declared_loss_policy"]
    return violations, decision, reasons


def admission_gate_from_spec(
    registry: dict, semantic: SemanticRegistry, contract: dict, verification: dict, spec: dict
) -> dict:
    """Evaluate admission eligibility only; never mutate or admit canonical state."""
    validate_verification(verification, registry, semantic, contract)
    keys(spec, {"gate_id", "forbidden_forgets", "notes"})
    forbidden = _unique_properties(spec["forbidden_forgets"], "forbidden forgotten property")
    violations, decision, reasons = _admission_outcome(contract, verification, forbidden)

    value = record(
        "preservation-admission-gate",
        gate_id=_versioned(spec["gate_id"], "gate_id"),
        contract_ref=contract["record_digest"],
        verification_ref=verification["record_digest"],
        source_state_ref=verification["source_state_ref"],
        candidate_state_ref=verification["candidate_state_ref"],
        forbidden_forgets=forbidden,
        policy_violations=violations,
        decision=decision,
        reasons=reasons,
        notes=_notes(spec["notes"], "admission-gate notes", 8192),
        claims={
            "eligibility_not_admission": True,
            "state_admission_performed": False,
            "canonical_state_mutated": False,
            "execution_authority": False,
        },
    )
    validate_admission_gate(value, registry, semantic, contract, verification)
    return value


def validate_admission_gate(
    value: dict, registry: dict, semantic: SemanticRegistry, contract: dict, verification: dict
) -> dict:
    keys(value, {
        "schema", "record_digest", "gate_id", "contract_ref", "verification_ref",
        "source_state_ref", "candidate_state_ref", "forbidden_forgets",
        "policy_violations", "decision", "reasons", "notes", "claims",
    })
    if value["schema"] != "ciw.preservation-admission-gate.v1":
        raise ValueError("Wrong preservation admission-gate schema")
    check_seal(value)
    _versioned(value["gate_id"], "gate_id")
    validate_verification(verification, registry, semantic, contract)
    if value["contract_ref"] != contract["record_digest"]:
        raise ValueError("Admission gate references a different preservation contract")
    if value["verification_ref"] != verification["record_digest"]:
        raise ValueError("Admission gate references a different verification receipt")
    for field in (
        "contract_ref", "verification_ref", "source_state_ref", "candidate_state_ref",
    ):
        content_ref(value[field])
    if value["source_state_ref"] != verification["source_state_ref"]:
        raise ValueError("Admission gate source identity differs from verification")
    if value["candidate_state_ref"] != verification["candidate_state_ref"]:
        raise ValueError("Admission gate candidate identity differs from verification")
    forbidden = _unique_properties(value["forbidden_forgets"], "forbidden forgotten property")
    violations = _unique_properties(value["policy_violations"], "policy violation")
    expected_violations, expected_decision, expected_reasons = _admission_outcome(contract, verification, forbidden)
    if violations != expected_violations:
        raise ValueError("Admission gate policy violations differ from contract")
    if value["decision"] not in ADMISSION_DECISIONS:
        raise ValueError("Unknown preservation admission decision")
    if type(value["reasons"]) is not list or not value["reasons"]:
        raise ValueError("Admission gate requires at least one reason")
    [text(reason) for reason in value["reasons"]]
    if value["decision"] != expected_decision:
        raise ValueError("Admission gate decision contradicts contract/verification")
    if value["reasons"] != expected_reasons:
        raise ValueError("Admission gate reasons contradict contract/verification")
    _notes(value["notes"], "admission-gate notes", 8192)
    if value["claims"] != {
        "eligibility_not_admission": True,
        "state_admission_performed": False,
        "canonical_state_mutated": False,
        "execution_authority": False,
    }:
        raise ValueError("Preservation admission-gate claims exceed gate authority")
    return detached(value)
