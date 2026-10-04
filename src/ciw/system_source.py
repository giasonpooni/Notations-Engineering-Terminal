"""Data-only retained scientific specifications; importing never executes them.

Raw bytes retain their acquisition content identity.  Compilation uses the
validated normalized specification identity, which is a distinct binding.
Neither identity establishes experimental validation or execution authority.
"""
from __future__ import annotations

import base64
from copy import deepcopy

from .adapters.subprocess import _json
from .core.identities import canonical_json
from .system_spec import SPEC_SCHEMA, validate_spec

SOURCE_SCHEMA = SPEC_SCHEMA
SOURCE_KIND = "system-specification"
MAX_BYTES = 256 * 1024


def _source(raw: bytes) -> dict:
    """Parse bounded exact JSON bytes using the strict trusted model contract."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError("System specification requires bounded exact JSON bytes")
    value = validate_spec(_json(raw))
    canonical_json(value)
    return value


def validate_source(source: dict, specification: dict) -> dict:
    """Check every descriptor binding and its normalized scientific content."""
    from .workbench import SOURCE_SCHEMA as DESCRIPTOR_SCHEMA, _source as retained_source
    fields = {"schema", "kind", "label", "source_schema", "evidence_id", "byte_count", "source_id", "bytes_b64"}
    if (type(source) is not dict or set(source) != fields
            or source["schema"] != DESCRIPTOR_SCHEMA or source["kind"] != SOURCE_KIND
            or source["source_schema"] != SOURCE_SCHEMA):
        raise ValueError("System compilation requires a full retained system-specification source")
    checked = retained_source({key: source[key] for key in ("kind", "label", "bytes_b64")})
    if canonical_json(checked) != canonical_json(source):
        raise ValueError("System retained source descriptor differs from its exact bytes")
    declared = _source(base64.b64decode(source["bytes_b64"], validate=True))
    normalized = validate_spec(specification)
    if canonical_json(declared) != canonical_json(normalized):
        raise ValueError("System specification differs from retained source content")
    return deepcopy(normalized)
