"""Compile, sign and inspect the Notations Legibility Instrument."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys

from .core.identities import canonical_json, content_identity
from .legibility import compile_bundle, sign_bundle, verify_bundle
from .legibility_workflow import compile_in_session, demo_source, import_impact
from .session import read_json, write_json


def _key(path):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Require an Ed25519 PEM private key")
    return key


def _trust(path):
    if path is None:
        return {}
    document = read_json(path)
    if not isinstance(document, dict) or set(document) != {"schema", "keys"} or document["schema"] != "ciw.legibility-trust.v1":
        raise ValueError("Require a legibility trust document")
    if not isinstance(document["keys"], dict):
        raise ValueError("Trust keys must be an explicit key_id-to-base64 mapping")
    return {key_id: base64.b64decode(value, validate=True) for key_id, value in document["keys"].items()}


def _read_artifacts(directory, bundle):
    mapping = read_json(Path(directory) / "artifact-map.json")
    if not isinstance(mapping, dict) or set(mapping) != {item["artifact_id"] for item in bundle["source"]["artifacts"]}:
        raise ValueError("Artifact map differs from the source contract")
    root = Path(directory).resolve()
    artifacts = {}
    for artifact_id, relative in mapping.items():
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("Artifact paths must be relative to the bundle directory")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Artifact path escapes the bundle directory")
        artifacts[artifact_id] = path.read_bytes()
    return artifacts


def _check_exports(directory, bundle, report):
    """Verify standalone transport records as well as the embedded views."""
    exports = {"contract.json": bundle["source"],
               **{name + ".json": view for name, view in bundle["representations"].items()}}
    report["export_status"] = "verified"
    for filename, expected in exports.items():
        try:
            if canonical_json(read_json(Path(directory) / filename)) != canonical_json(expected):
                raise ValueError("Export differs from its committed bundle representation")
        except (OSError, ValueError, TypeError) as exc:
            report["export_status"] = "failed"
            report["errors"].append({"code": "export_mismatch", "message": filename + ": " + str(exc)})
    report["review_html_integrity"] = "not_assessed"
    report["report_id"] = content_identity({key: value for key, value in report.items() if key != "report_id"})
    return report


def _publish(destination, contract, artifacts, *, run=None, private_key=None, demo=False):
    from .legibility_view import render_html
    destination = Path(destination)
    # Reject invalid metadata/bytes before creating a publication directory.
    compile_bundle(contract, artifacts=artifacts)
    destination.mkdir(parents=True, exist_ok=False)
    payload = compile_in_session(run, contract, destination / "compilation") if run is not None else None
    bundle = payload["result"]["data"] if payload else compile_bundle(contract)
    # Bytes are checked separately from deterministic metadata compilation.
    compile_bundle(contract, artifacts=artifacts)
    write_json(destination / "contract.json", contract)
    write_json(destination / "bundle.json", bundle)
    for name, representation in bundle["representations"].items():
        write_json(destination / (name + ".json"), representation)
    paths = {}
    (destination / "artifacts").mkdir()
    for index, item in enumerate(contract["artifacts"]):
        suffix = {"image/svg+xml": ".svg", "application/json": ".json"}.get(item["media_type"], ".bin")
        relative = f"artifacts/artifact-{index:03d}" + suffix
        (destination / relative).write_bytes(artifacts[item["artifact_id"]])
        paths[item["artifact_id"]] = relative
    write_json(destination / "artifact-map.json", paths)
    envelope, trusted_keys = None, {}
    if private_key is not None:
        envelope = sign_bundle(bundle, private_key)
        write_json(destination / "envelope.json", envelope)
    if demo and envelope is not None:
        trusted_keys = {envelope["key_id"]: base64.b64decode(envelope["public_key_b64"], validate=True)}
        write_json(destination / "demo-trust.json", {"schema": "ciw.legibility-trust.v1",
                   "keys": {envelope["key_id"]: envelope["public_key_b64"]}})
    report = verify_bundle(bundle, envelope, artifacts=artifacts, trusted_keys=trusted_keys,
                           expected_object_id=contract["object"]["object_id"],
                           expected_version=contract["object"]["version"],
                           expected_source_digest=content_identity(contract))
    _check_exports(destination, bundle, report)
    write_json(destination / "verification.json", report)
    (destination / "review.html").write_text(render_html(bundle, report), encoding="utf-8")
    if payload:
        write_json(destination / "compilation-binding.json", {"schema": "ciw.legibility-compilation-binding.v1",
                   "source_bindings": contract["bindings"], "compilation_operation_id": payload["result"]["operation_id"],
                   "compilation_execution_id": payload["result"]["execution_id"],
                   "compilation_result_id": payload["result"]["result_id"],
                   "envelope_verification_id": report["verification_id"], "canonical_admission": False})
    return {"directory": str(destination), "bundle_id": bundle["bundle_id"], "verification": report}


def parser():
    root = argparse.ArgumentParser(prog="ciw legibility", description=__doc__)
    actions = root.add_subparsers(dest="action", required=True)
    compare = actions.add_parser("compare", help="Compare intact bundle sources; signatures and bytes require verify")
    compare.add_argument("before", type=Path, help="Earlier bundle.json")
    compare.add_argument("after", type=Path, help="Later bundle.json")
    compare.add_argument("--output", type=Path)
    demo = actions.add_parser("demo", help="Build a signed synthetic impact specimen and NET compilation session")
    demo.add_argument("--output-dir", type=Path, required=True)
    compile_ = actions.add_parser("compile", help="Compile a source contract with explicit artifact paths")
    compile_.add_argument("contract", type=Path)
    compile_.add_argument("--artifact-map", type=Path, required=True, help="JSON artifact_id to explicit file path")
    compile_.add_argument("--run", type=Path, help="Attach compilation to this NET run through Session")
    compile_.add_argument("--private-key", type=Path)
    compile_.add_argument("--output-dir", type=Path, required=True)
    impact = actions.add_parser("import-impact", help="Read retained NET impact workspace without rerunning physics")
    impact.add_argument("workspace", type=Path)
    impact.add_argument("--object-id", required=True)
    impact.add_argument("--version", required=True)
    impact.add_argument("--label", default="Retained impact specimen")
    impact.add_argument("--private-key", type=Path)
    impact.add_argument("--output-dir", type=Path, required=True)
    sign = actions.add_parser("sign", help="Sign an existing validated bundle")
    sign.add_argument("bundle", type=Path)
    sign.add_argument("--private-key", type=Path, required=True)
    sign.add_argument("--output", type=Path, required=True)
    for action in ("inspect", "verify", "review"):
        verify = actions.add_parser(action, help="Check retained bytes and explicitly supplied trust/current version")
        verify.add_argument("directory", type=Path)
        verify.add_argument("--trust", type=Path)
        verify.add_argument("--expected-object-id")
        verify.add_argument("--expected-version")
        verify.add_argument("--expected-source-digest")
        verify.add_argument("--output", type=Path, required=action == "review",
                            help="New HTML file for review; JSON report for inspect/verify")
    keygen = actions.add_parser("keygen", help="Generate an operator-owned local key; never overwrite an existing key")
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--trust", type=Path, required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "compare":
            from .legibility_compare import compare_bundles
            result = compare_bundles(read_json(args.before), read_json(args.after))
            if args.output:
                with args.output.open("x", encoding="utf-8") as stream:
                    json.dump(result, stream, indent=2, allow_nan=False)
            print(json.dumps(result, indent=2, allow_nan=False))
            return 2 if result["same_version_content_conflict"] else 0
        elif args.action == "demo":
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            import tempfile
            with tempfile.TemporaryDirectory(prefix="ciw-legibility-fixture-") as temporary:
                run, contract, artifacts = demo_source(Path(temporary))
                result = _publish(args.output_dir, contract, artifacts, run=run,
                                  private_key=Ed25519PrivateKey.generate(), demo=True)
        elif args.action == "compile":
            contract, mapping = read_json(args.contract), read_json(args.artifact_map)
            if not isinstance(mapping, dict) or not all(isinstance(value, str) for value in mapping.values()):
                raise ValueError("Artifact map must map artifact IDs to explicit paths")
            artifacts = {key: (args.artifact_map.parent / value).read_bytes() for key, value in mapping.items()}
            result = _publish(args.output_dir, contract, artifacts,
                              run=read_json(args.run) if args.run else None,
                              private_key=_key(args.private_key) if args.private_key else None)
        elif args.action == "import-impact":
            run, contract, artifacts = import_impact(args.workspace, object_id=args.object_id,
                                                    version=args.version, label=args.label)
            result = _publish(args.output_dir, contract, artifacts, run=run,
                              private_key=_key(args.private_key) if args.private_key else None)
        elif args.action == "sign":
            result = sign_bundle(read_json(args.bundle), _key(args.private_key))
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, allow_nan=False)
        elif args.action == "keygen":
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            import hashlib
            if args.private_key.exists() or args.trust.exists():
                raise ValueError("Key/trust destination already exists")
            key = Ed25519PrivateKey.generate()
            raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            key_id = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
            private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            fd = os.open(args.private_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(private)
            with args.trust.open("x", encoding="utf-8") as stream:
                json.dump({"schema": "ciw.legibility-trust.v1", "keys": {key_id: base64.b64encode(raw).decode()}}, stream, indent=2)
            result = {"key_id": key_id, "private_key": str(args.private_key), "trust": str(args.trust)}
        else:
            bundle = read_json(args.directory / "bundle.json")
            envelope_path = args.directory / "envelope.json"
            result = verify_bundle(bundle, read_json(envelope_path) if envelope_path.exists() else None,
                                   artifacts=_read_artifacts(args.directory, bundle), trusted_keys=_trust(args.trust),
                                   expected_object_id=args.expected_object_id, expected_version=args.expected_version,
                                   expected_source_digest=args.expected_source_digest)
            _check_exports(args.directory, bundle, result)
            if args.output:
                with args.output.open("x", encoding="utf-8") as stream:
                    if args.action == "review":
                        from .legibility_view import render_html
                        stream.write(render_html(bundle, result))
                    else:
                        json.dump(result, stream, indent=2, allow_nan=False)
            print(json.dumps(result, indent=2, allow_nan=False))
            failed = (not result["content_intact"] or result["artifact_status"] != "verified" or result["export_status"] != "verified"
                      or result["signature_valid"] is False
                      or any(result[name] is False for name in ("version_current", "object_matches", "source_matches"))
                      or (args.trust is not None and not result["issuer_trusted"]))
            return 2 if failed else 0
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except ImportError:
        print("ciw legibility: signing needs the optional legibility dependency: install .[legibility]", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, StopIteration) as exc:
        print("ciw legibility: " + (str(exc) or type(exc).__name__), file=sys.stderr)
        return 2
