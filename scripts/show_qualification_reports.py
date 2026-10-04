"""Display explicit retained JSON reports without changing qualification results.

Raw-byte hashes bind the displayed payloads to files retained by the following
artifact step. This diagnostic does not assert scientific validity or replace
the original gate, its exit status, or artifact review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

MAX_BYTES = 2 * 1024 * 1024


def main(paths):
    root = Path.cwd().resolve()
    for label in paths:
        try:
            path = (root / label).resolve()
            if not path.is_relative_to(root):
                raise ValueError("report path is outside the checkout")
            with path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("report exceeds the 2 MiB diagnostic limit")
            value = json.loads(raw)
            identity = {"path": label, "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()}
            print("RETAINED_REPORT_BEGIN " + json.dumps(identity, sort_keys=True))
            print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True))
            print("RETAINED_REPORT_END " + label)
        except (OSError, ValueError, RecursionError) as error:
            print("RETAINED_REPORT_UNAVAILABLE " + json.dumps(
                {"path": label, "error": str(error)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
