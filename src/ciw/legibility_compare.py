"""Deterministic source comparison without inferring scientific improvement."""

from copy import deepcopy

from .core.identities import content_identity
from .legibility import verify_bundle


_KEYS = {"properties": "property_id", "claims": "claim_id",
         "artifacts": "artifact_id", "annotations": "annotation_id"}


def compare_bundles(before, after):
    """Compare intact bundles of one object; versions are opaque identifiers.

    Reports check content consistency only. Signature, artifact-byte, trust and
    freshness inspection remain separate. No admission or physical validation
    is performed. Lists with explicit IDs are compared independent of ordering.
    """
    reports = {"before": verify_bundle(before), "after": verify_bundle(after)}
    if not all(report["content_intact"] for report in reports.values()):
        raise ValueError("Comparison requires two intact legibility bundles")
    old, new = before["source"], after["source"]
    if old["object"]["object_id"] != new["object"]["object_id"]:
        raise ValueError("Comparison requires the same object identity")
    changes = []

    def walk(left, right, path):
        if type(left) is dict and type(right) is dict:
            for key in sorted(set(left) | set(right)):
                child = path + [key]
                if key not in left:
                    record("added", child, None, right[key])
                elif key not in right:
                    record("removed", child, left[key], None)
                else:
                    walk(left[key], right[key], child)
        elif type(left) is list and type(right) is list and path[-1] in _KEYS:
            field = _KEYS[path[-1]]
            walk({item[field]: item for item in left},
                 {item[field]: item for item in right}, path)
        elif type(left) is not type(right) or left != right:
            record("changed", path, left, right)

    def record(kind, path, left, right):
        changes.append({"kind": kind, "path": path,
                        "before": deepcopy(left), "after": deepcopy(right)})

    walk(old, new, [])
    # Put scientific/evidence changes ahead of object labels and version text.
    order = {"claims": 0, "qualification": 1, "bindings": 2,
             "semantics": 3, "artifacts": 4, "vision": 5, "object": 6}
    changes.sort(key=lambda change: (order.get(change["path"][0], 7), change["path"]))
    same_version = old["object"]["version"] == new["object"]["version"]
    source_changed = before["manifest"]["source_digest"] != after["manifest"]["source_digest"]
    result = {"schema": "ciw.legibility-comparison.v1",
              "object_id": old["object"]["object_id"],
              "before": {"bundle_id": before["bundle_id"], "version": old["object"]["version"]},
              "after": {"bundle_id": after["bundle_id"], "version": new["object"]["version"]},
              "source_changed": source_changed,
              "same_version_content_conflict": same_version and source_changed,
              "changes": changes, "physical_validation_status": "not_assessed",
              "canonical_admission": False}
    result["comparison_id"] = content_identity(result)
    return result
