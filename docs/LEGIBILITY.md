# Notations Legibility Instrument v1

NET now compiles one versioned scientific object into synchronized human,
reasoning and vision records. A separate Ed25519 envelope binds their manifest
to a signing key. The instrument belongs to Notation Systems Inc. and can serve
Notations Laboratories, Notations Manufacturing and Notations Gaming.

This is an additive root `ciw` instrument in the engineering superrepo. Existing
imported package trees, licences, historical execution pins and scientific
evidence hashes remain unchanged.

## Run the complete first slice

```sh
python -m pip install -e ".[dev,legibility]"
ciw doctor --profile legibility
ciw legibility demo --output-dir results/legibility-demo
ciw legibility verify results/legibility-demo \
  --trust results/legibility-demo/demo-trust.json \
  --expected-object-id notations:specimen:demo-coupon-001 --expected-version 1
```

Open `results/legibility-demo/review.html`. The view leads with an illustrative
failed acceptance criterion: a synthetic 200 N peak exceeds a demonstration
150 N limit. Units, unknown uncertainty, assumptions and evidence references
remain inspectable. The force samples are a fixture, not acquired observations
or a qualified impact simulation. A real Session summary operation precedes a
real Session compilation operation. Its coupon SVG is a diagram, not a specimen
photograph. The image annotation has its own normalized image frame; no transform
to the physical fixture frame is established.

The [installed operator guide](RUN_NET.md) joins this path to retained analysis,
independently verified impact and atmospheric handoffs. Doctor is a read-only
dependency preflight; its success does not perform numerical or signature
verification. Use the explicit workflow and verification commands for those
separate checks.

All subcommands are also available as `net legibility ...`. For a fresh offline
review after transporting a bundle, use:

```sh
net legibility review results/legibility-demo \
  --trust results/legibility-demo/demo-trust.json --expected-version 1 \
  --output results/legibility-demo/fresh-review.html
net legibility compare previous/bundle.json current/bundle.json
```

`review` rechecks retained artifact bytes, exports and the envelope using the
supplied trust and expectations. It does not reuse `verification.json`. It writes
a new HTML file and prints the fresh JSON report; verification failures return
exit code 2 even when a diagnostic HTML file can be generated. Existing output
files are never overwritten. Review cannot establish source dependency currency;
use Session dependency inspection for corrected scientific inputs.

Comparison checks intact source bundles for one object and reports changes by
explicit IDs. It does not verify signatures or artifact bytes; run `verify` on
both directories when those checks are required. See [comparison](LEGIBILITY_COMPARISON.md).

The demo generates an ephemeral key, discards its private bytes and exports a
**demonstration trust anchor**. That same-run anchor tests explicit key matching;
it does not establish organizational issuer identity. Operator trust must come
from an independently authenticated key distribution process.

## Transport and identities

| Artifact | Purpose |
| --- | --- |
| `contract.json` | One strict semantic source: object/version, bindings, units, uncertainty, claims, assumptions, qualification, annotations and artifact commitments |
| `bundle.json` | Source, deterministic projections, manifest and bundle content identity |
| `human.json` | Complete source semantics plus failed/unresolved attention order |
| `reasoning.json` | Complete source semantics and claims; independent claim validation remains false |
| `vision.json` | Object/version/source link and normalized image annotations; detector evaluation remains false |
| `envelope.json` | Optional domain-separated Ed25519 signature, algorithm/profile and key fingerprint |
| `artifact-map.json`, `artifacts/` | Local byte materialization; directory verification checks paths, exact IDs, lengths and hashes |
| `verification.json` | Fresh verification occurrence; separate content, byte, export, signature, trust and caller-expectation checks |
| `compilation/`, `compilation-binding.json` | NET Session evidence, operation, execution and result, linked to upstream source identities |
| `review.html` | Escaped, offline, unsigned convenience rendering with progressive disclosure |

`legibility.compile.v1` is available through NET's default operation registry and
capability index. It requires `{ "contract": ... }` and refuses source evidence
or physical-frame mismatch against the current Session. Compilation has its own
execution/result identities; upstream evidence, operation, execution and
scientific verification references retain their original meanings. Protocol-v1
results retain `verification_status=not_verified` and `verification_id=null`.
Envelope verification produces a distinct `verification-UUID` occurrence.

The compiler deep-copies source data, derives all projections and records their
content hashes. Verification recompiles the source with the known compiler and
compares the complete bundle. A report's sealed `verified_bundle_digest` binds
the entire verified bundle, preventing reuse of a good report over edited views
or source with an unchanged manifest. The HTML renderer refuses such reuse.

## Verification boundaries

| Check | What it establishes |
| --- | --- |
| `content_intact` | Source, manifest and all embedded projections match deterministic recompilation |
| `artifact_status` | Declared raw bytes were checked, failed, or were not supplied |
| `export_status` | Standalone contract/human/reasoning/vision JSON matches the bundle |
| `signature_valid` | The domain-separated manifest signature verifies against the indicated Ed25519 public key |
| `issuer_trusted` | The valid signing key exactly matches an explicitly supplied trust anchor |
| `object_matches`, `version_current`, `source_matches` | Match caller-supplied expectations; null when no expectation was supplied |
| `physical_validation_status` | Always `not_assessed`; declared qualification remains separately visible |
| `canonical_admission` | Always false; this instrument grants no scientific or operational authority |

A source/view may be altered while its untouched signed manifest still has a
valid signature; `content_intact` then fails. An attacker may re-sign an altered
bundle using another key; signature validity then passes while issuer trust
fails. A valid old envelope does not prove currentness. Verification checks the
expected version/source only when the caller supplies an authoritative current
reference. The demo's publication report checks against its just-created source,
which is a self-consistency test, not an external freshness service.

`verify` exits 2 for mismatched bundle/artifact/export bytes, invalid signatures,
caller identity/version/source mismatch, or failure to match a supplied trust
policy. An unsigned intact bundle can still be inspected successfully; its
signature check remains null. The report is a locally sealed consistency receipt,
not a third-party signed certificate. The HTML bytes are not covered by the
manifest (`review_html_integrity=not_assessed`).

## Reuse retained impact results

```sh
ciw legibility import-impact results/impact-plate/workspace.json \
  --object-id notations:specimen:plate-001 --version 1 \
  --label "Plate impact specimen" --output-dir results/legibility-plate
```

The read-only adapter accepts the existing elastic, crush and modal plate
Session-v2 pair. It reads the workspace once, validates scientific evidence
identity, record seals, execution coverage, request/result digests and the
verifier's exact candidate binding. It retains the original workspace bytes as
a committed artifact. It extracts scalar properties and existing numerical
report status without importing or executing physics engines. Numerical report
status is an imported declaration; physical validation is not assessed and no
fresh numerical verification is claimed. Other native receipt identity forms
can remain evidence references rather than being coerced into UUID event IDs.

The impact adapter currently rejects other workspace versions, including v4
workspaces carrying retained correction journals. Their dependency eligibility
requires an explicit adapter extension. Within a current Session, a compiled
representation's declared result and execution dependencies participate in
correction propagation. The sealed representation bytes remain historical;
signature integrity does not establish that its dependencies are current.

## Supply a real source and signing key

```sh
ciw legibility keygen --private-key operator-legibility.pem --trust operator-trust.json
ciw legibility compile contract.json --artifact-map artifact-paths.json \
  --run recording.json --private-key operator-legibility.pem \
  --output-dir results/legibility-specimen
ciw legibility verify results/legibility-specimen --trust operator-trust.json \
  --expected-object-id notations:specimen:coupon-042 --expected-version 3
```

`artifact-paths.json` maps artifact IDs to explicit file paths relative to that
map. The `--run` option retains compilation in Session; omitting it uses the pure
compiler. Source provenance references remain declarations except where a
specific adapter checks their retained bindings. Signing never promotes them to
verified physical claims. Key creation refuses existing destinations and creates
the PEM with owner-only POSIX permissions. Keys are operator files and should be
kept outside versioned source. This prototype supplies no key-revocation,
hardware-keystore, signer-organization registry, encryption or timestamp service.

## Canonicalization and standards

The explicit private profile is `ciw.json.sort-keys.ascii.binary64.v1`. It reuses
NET's existing sorted, compact, ASCII-escaped Python JSON content serialization
without changing legacy hashes. Strict contracts reject duplicates, nonfinite
numbers, invalid Unicode, unsupported schemas and missing semantic fields.
Scientific floats remain binary64; arbitrary Python integers are permitted.
JavaScript parsers can round integers above 2^53 and serialize floats differently.
Consumers can inspect the exports as data, but cryptographic verification must
use this profile/reference implementation. Broad cross-language signature
interoperability is not qualified.

This implementation does not claim JCS, JSON-LD, W3C Verifiable Credentials or
C2PA compliance. Future standards adapters can extend the substrate with explicit
profiles and preservation checks. Primary design references:

- [Cryptography Ed25519 API](https://cryptography.io/en/stable/hazmat/primitives/asymmetric/ed25519/)
- [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032.html)
- [RFC 8785 canonicalization](https://www.rfc-editor.org/rfc/rfc8785.html)
- [W3C credential trust model](https://www.w3.org/TR/vc-data-model-2.0/#trust-model)

## Validation and next experiments

Run `python -m pytest tests/test_legibility*.py tests/test_capabilities.py tests/test_protocol.py`. The dedicated workflow runs
the boundary tests and installed-wheel demo/verification on Linux and Windows,
Python 3.11 and 3.12. A configured workflow is not evidence that remote CI ran.

Adversarial checks include independent source/view/artifact alterations, export
tampering, stale signed versions, wrong/replaced keys, malformed signatures,
algorithm/profile downgrade, strict JSON, missing units/frame/uncertainty,
Session replay without science recomputation, and reused reports over edited
bundles. The root instrument leaves imported source trees and execution pins
untouched; `python scripts/superrepo.py audit` checks that invariant.

Human decision time/accuracy, agent retrieval/context/unsupported claims, actual
vision recognition across pose/light/occlusion, physical experiment integration,
cross-tool export preservation and standards interoperability remain acceptance
experiments. This build supplies their common identity and verification path;
it does not report unmeasured improvements.

## Supported operator scope

The usable v1 workflow is local contract compilation, optional operator signing,
byte/export inspection, fresh offline review and version comparison. Actual NET
elastic, crush and modal-plate execution outputs are exercised through retained
workspace import in the operator tests. Arbitrary scientific instruments can
supply a validated contract and explicit artifact map; automatic result adapters
are currently limited to the documented impact workspace-v2 pair. Correction
journals, general vision detection, live key revocation and cross-language
signature interoperability are not qualified by this workflow.
