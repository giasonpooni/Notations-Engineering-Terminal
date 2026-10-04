"""Offline human inspection of a Legibility Instrument bundle.

This renderer presents declarations and a supplied verification report. It does
not verify signatures, retrieve artifacts, perform vision detection, or admit
state. Every source-controlled value is escaped before entering the document.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
import json
from typing import Any

from .core.identities import content_identity


def _display(value: Any) -> str:
    if value is None:
        return "Not assessed"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _text(value: Any) -> str:
    return escape(_display(value), quote=True)


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _readable(value: Any) -> str:
    return _display(value).replace("_", " ")


def _pill(label: Any, tone: str = "neutral") -> str:
    # Tone is supplied only by renderer code; it never comes from the source.
    return f'<span class="pill {tone}">{_text(label)}</span>'


def _status_tone(value: Any) -> str:
    if value is None:
        return "unresolved"
    status = str(value).casefold().replace("-", "_")
    if status in {"failed", "fail", "invalid", "rejected", "false", "error", "unsupported"}:
        return "failed"
    if status in {"passed", "pass", "verified", "valid", "satisfied", "accepted", "true"}:
        return "passed"
    if status in {"measured", "simulated", "assumed"}:
        return "neutral"
    return "unresolved"


def _claim_priority(claim: Any) -> int:
    status = _mapping(claim).get("status")
    return {"failed": 0, "unresolved": 1, "assumed": 2, "simulated": 3, "measured": 4}.get(status, 1)


def _report_matches(bundle: Mapping, report: Mapping) -> bool:
    """Bind displayed checks to this bundle and the complete report seal."""
    if not isinstance(bundle.get("bundle_id"), str) or report.get("bundle_id") != bundle["bundle_id"]:
        return False
    try:
        payload = {key: value for key, value in report.items() if key != "report_id"}
        return (report.get("report_id") == content_identity(payload)
                and report.get("verified_bundle_digest") == content_identity(dict(bundle)))
    except (ValueError, TypeError, RecursionError, OverflowError):
        return False


def _refs(value: Any, empty: str = "No references declared") -> str:
    refs = _items(value)
    if not refs:
        return f'<span class="muted">{_text(empty)}</span>'
    return '<ul class="refs">' + "".join(
        f'<li><code>{_text(ref)}</code></li>' for ref in refs
    ) + "</ul>"


def _json_details(label: str, value: Any, *, opened: bool = False) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    return (
        f'<details{" open" if opened else ""}><summary>{escape(label)}</summary>'
        f'<pre>{escape(encoded, quote=True)}</pre></details>'
    )


def _check(label: str, value: Any, yes: str, no: str, explanation: str) -> str:
    if value is True:
        status, tone = yes, "passed"
    elif value is False:
        status, tone = no, "failed"
    else:
        status, tone = "Not assessed", "unresolved"
    return (
        '<article class="check"><h3>' + escape(label) + "</h3>"
        + _pill(status, tone) + '<p class="muted">' + escape(explanation) + "</p></article>"
    )


_CSS = """
:root{color-scheme:dark;--bg:#0b1119;--panel:#111c28;--border:#28394b;--ink:#edf3fa;
--muted:#a9b8c8;--accent:#85cbd9;--bad:#ffc1b9;--warn:#f8d693;--good:#a3e3c2}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1180px;margin:auto;padding:38px 28px 60px}header{margin-bottom:26px}
.eyebrow{font-size:11px;letter-spacing:.15em;color:var(--accent);font-weight:700;text-transform:uppercase}
h1{font-size:clamp(26px,4vw,42px);line-height:1.15;margin:10px 0 13px;letter-spacing:-.025em}
h2{font-size:19px;margin:0 0 15px}h3{font-size:13px;margin:0 0 11px}p{margin:9px 0}
.muted{color:var(--muted)}.subtitle{max-width:900px}.identity{display:flex;flex-wrap:wrap;gap:8px;
align-items:center}.pill{display:inline-block;border:1px solid var(--border);border-radius:5px;
padding:3px 9px;font-size:12px;line-height:1.5;color:var(--muted);background:#16212e}
.pill.failed{color:var(--bad);border-color:#774d4a;background:#382426}
.pill.unresolved{color:var(--warn);border-color:#675a3e;background:#302b21}
.pill.passed{color:var(--good);border-color:#3d675a;background:#1c302b}
.banner{padding:17px 20px;border:1px solid #675a3e;border-left:4px solid var(--warn);
background:#24231f;border-radius:7px;margin:23px 0}.banner strong{color:var(--warn)}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:22px;margin:18px 0}
.section-head{display:flex;gap:15px;align-items:baseline;justify-content:space-between;flex-wrap:wrap}
.section-head p{font-size:12px}.checks{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.check{background:#0e1722;border:1px solid var(--border);border-radius:7px;padding:16px}
.check p{font-size:12px;margin-top:12px}.assessment{display:grid;grid-template-columns:1fr 1fr;gap:16px;
margin-top:16px}.assessment>div{border-top:1px solid var(--border);padding-top:15px}
.note{font-size:13px;color:var(--muted);border-left:2px solid var(--accent);padding-left:12px;margin-top:17px}
.claim{border:1px solid var(--border);border-left:3px solid var(--border);border-radius:6px;
padding:16px;margin:12px 0;background:#0e1722}.claim.failed{border-left-color:var(--bad)}
.claim.unresolved{border-left-color:var(--warn)}.claim.passed{border-left-color:var(--good)}
.claim-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.claim p{font-size:15px}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}
th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600}
th,td{padding:12px 10px;vertical-align:top;border-bottom:1px solid var(--border)}
td:first-child,th:first-child{padding-left:0}td:last-child,th:last-child{padding-right:0}
tr:last-child td{border-bottom:0}code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
font-size:12px}code{overflow-wrap:anywhere;color:#bfd6e9}pre{white-space:pre-wrap;overflow-wrap:anywhere;
background:#0b131d;border:1px solid var(--border);border-radius:5px;padding:15px;max-height:620px;overflow:auto}
.refs{list-style:none;margin:0;padding:0}.refs li{margin:3px 0}.refs li::before{content:"↳ ";color:var(--muted)}
details{margin:12px 0;border-top:1px solid var(--border);padding-top:12px}
summary{cursor:pointer;color:var(--accent);font-size:13px;list-style-position:outside;margin-left:16px}
summary:focus-visible{outline:2px solid var(--accent);outline-offset:5px}
.assumptions{padding-left:22px}.assumptions li{padding:6px 0;overflow-wrap:anywhere}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:24px}.small{font-size:12px}
.empty{color:var(--muted);padding:10px 0}.break{overflow-wrap:anywhere}
footer{color:var(--muted);font-size:12px;margin-top:28px}.sr-only{position:absolute;width:1px;
height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:850px){.checks{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){main{padding:24px 16px 40px}.panel{padding:17px}.checks,.two-col,
.assessment{grid-template-columns:1fr}.check{padding:14px}.section-head{gap:0}th,td{padding:10px 7px}}
@media print{body{background:#fff;color:#111}main{max-width:none;padding:0}.panel,.check,.claim,
.banner{background:#fff;border-color:#aaa;break-inside:avoid}.muted,footer,th{color:#444}
code,summary,.eyebrow{color:#234}.pill{color:#111;background:#fff;border-color:#aaa}pre{color:#111;
background:#f7f7f7;max-height:none}.pill.failed{color:#800}.pill.passed{color:#063}.pill.unresolved{color:#653}}
"""


def render_html(bundle: Mapping[str, Any], verification_report: Mapping[str, Any] | None = None) -> str:
    """Return a self-contained, escaped HTML inspection document.

    ``source`` is the authoritative contract in a compiled bundle. A direct
    contract is also accepted for inspection before compilation. A missing
    verification field is shown as not assessed, never inferred as a pass.
    """
    bundle = _mapping(bundle)
    source = _mapping(bundle.get("source")) or _mapping(bundle.get("contract")) or bundle
    obj = _mapping(source.get("object"))
    semantics = _mapping(source.get("semantics"))
    qualification = _mapping(source.get("qualification"))
    vision = _mapping(source.get("vision"))
    bindings = _mapping(source.get("bindings"))
    supplied_report = _mapping(verification_report)
    report_accepted = verification_report is not None and _report_matches(bundle, supplied_report)
    report = supplied_report if report_accepted else {}
    properties = _items(semantics.get("properties"))
    claims = sorted(_items(source.get("claims")), key=_claim_priority)
    artifacts = _items(source.get("artifacts"))
    assumptions = _items(semantics.get("assumptions"))
    unresolved_count = sum(_mapping(claim).get("status") in {"failed", "unresolved"} for claim in claims)
    failed_count = sum(_mapping(claim).get("status") == "failed" for claim in claims)
    label = obj.get("label", "Unnamed object")

    # These badges describe explicit source wording, not inferred physical origin.
    declarations = json.dumps({"qualification": qualification, "assumptions": assumptions,
                               "claims": claims}, ensure_ascii=False, default=str).casefold()
    origin = []
    if source.get("synthetic") is True or "synthetic" in declarations:
        origin.append(_pill("Contains declared synthetic content", "unresolved"))
    if source.get("simulated") is True or "simulated" in declarations or "simulation" in declarations:
        origin.append(_pill("Contains declared simulation content", "unresolved"))

    header = (
        '<header><div class="eyebrow">NET / Notations Legibility Instrument</div>'
        f'<h1>{_text(label)}</h1><div class="identity">'
        + _pill(obj.get("kind", "Kind not declared"))
        + _pill("Version " + _display(obj.get("version")))
        + _pill("Unsigned convenience rendering", "unresolved")
        + "".join(origin) + '</div><p class="subtitle muted">'
        'Inspect one versioned object across declared meaning, evidence, perception, and execution. '
        'Verification and physical qualification remain separately visible. CLI verification checks '
        'the authoritative JSON exports and supplied artifact bytes; this HTML is an unsigned convenience view.</p></header>'
    )
    if unresolved_count:
        attention = (
            '<aside class="banner" aria-label="Claims requiring attention"><strong>'
            f'{failed_count} failed · {unresolved_count - failed_count} unresolved claims</strong>'
            '<p>Review these declarations and their supporting evidence before making a decision. '
            'Claim status is supplied by the contract.</p></aside>'
        )
    else:
        attention = '<p class="note">No failed or unresolved claims are declared. This is not a physical validation result.</p>'
    if verification_report is not None and not report_accepted:
        attention += (
            '<aside class="banner" aria-label="Verification report mismatch"><strong>Verification report mismatch</strong>'
            '<p>The report does not bind this bundle or its report seal is invalid. '
            'Its checks are not displayed as assessed. Inspect the supplied report and verify the authoritative JSON.</p></aside>'
        )

    checks = "".join([
        _check("Content integrity", report.get("content_intact"), "Content intact", "Content mismatch",
               "Integrity of the content covered by the supplied report."),
        _check("Signature", report.get("signature_valid"), "Signature valid", "Signature invalid",
               "Association with a signing key; physical truth is assessed separately."),
        _check("Issuer trust", report.get("issuer_trusted"), "Issuer trusted", "Issuer not trusted",
               "Trust under the caller's declared policy."),
        _check("Version currency", report.get("version_current"), "Version current", "Stale or mismatched version",
               "Comparison with a supplied expected version; no live registry lookup."),
        _check("Standalone exports", None if report.get("export_status") is None else report.get("export_status") == "verified",
               "Exports intact", "Export mismatch",
               "Contract and human, reasoning and vision JSON files checked against the bundle."),
    ])
    artifact_status = report.get("artifact_status")
    artifact_label = (
        "Artifact bytes not checked" if artifact_status == "not_checked"
        else "Not assessed" if artifact_status is None
        else _readable(artifact_status)
    )
    physical = report.get("physical_validation_status")
    declared = report.get("declared_qualification", qualification.get("status"))
    if isinstance(declared, Mapping):
        declared = declared.get("status")
    verification = (
        '<section class="panel" aria-labelledby="verification-heading"><div class="section-head">'
        '<h2 id="verification-heading">Verification boundaries</h2><p class="muted">'
        + ("Supplied report · subject and seal match" if report_accepted
           else "Supplied report refused" if verification_report is not None else "No report supplied")
        + '</p></div><div class="checks">' + checks + '</div><div class="assessment">'
        '<div><h3>Physical validation</h3>' + _pill(_readable(physical), _status_tone(physical))
        + '<p class="small muted">Declared qualification: <strong>' + _text(_readable(declared))
        + '</strong></p></div><div><h3>Artifact verification</h3>'
        + _pill(artifact_label, "unresolved" if artifact_status in (None, "not_checked") else _status_tone(artifact_status))
        + '<p class="small muted">Artifact hashes listed below are declarations until their bytes are checked.</p>'
        '</div></div><p class="note">A valid signature establishes integrity and association with a key. '
        'Calibration, model qualification, experimental validation, and canonical admission are separate decisions.</p>'
        + _json_details("Inspect supplied verification report", dict(supplied_report)) + '</section>'
    )

    claim_cards = []
    for item in claims:
        claim = _mapping(item)
        tone = _status_tone(claim.get("status"))
        claim_cards.append(
            f'<article class="claim {tone}"><div class="claim-head"><code>'
            + _text(claim.get("claim_id")) + '</code>'
            + _pill(_readable(claim.get("status")), tone) + '</div><p>'
            + _text(claim.get("statement", item)) + '</p><details><summary>Supporting evidence</summary>'
            + _refs(claim.get("evidence_refs")) + '</details></article>'
        )
    claim_section = (
        '<section class="panel" aria-labelledby="claims-heading"><div class="section-head">'
        '<h2 id="claims-heading">Claims and decision attention</h2><p class="muted">Failed and unresolved first</p>'
        '</div>' + ("".join(claim_cards) or '<p class="empty">No claims declared.</p>') + '</section>'
    )

    property_rows = []
    for item in properties:
        prop = _mapping(item)
        uncertainty = _mapping(prop.get("uncertainty"))
        uncertainty_status = uncertainty.get("status")
        standard = uncertainty.get("standard_uncertainty")
        uncertainty_text = _text(_readable(uncertainty_status))
        if standard is not None:
            uncertainty_text += '<br><span class="small">Standard uncertainty: '
            uncertainty_text += _text(standard) + " " + _text(prop.get("unit", "")) + '</span>'
        else:
            uncertainty_text += '<br><span class="small muted">Standard uncertainty not supplied</span>'
        property_rows.append(
            '<tr><td><code>' + _text(prop.get("property_id")) + '</code></td><td class="break">'
            + _text(prop.get("value")) + '</td><td>' + _text(prop.get("unit"))
            + '</td><td>' + uncertainty_text + '</td><td>' + _refs(prop.get("evidence_refs")) + '</td></tr>'
        )
    property_table = (
        '<div class="table-wrap"><table><caption class="sr-only">Declared properties, units, uncertainty and evidence</caption>'
        '<thead><tr><th>Property</th><th>Value</th><th>Unit</th><th>Uncertainty</th><th>Evidence</th></tr></thead>'
        '<tbody>' + "".join(property_rows) + '</tbody></table></div>'
        if property_rows else '<p class="empty">No properties declared.</p>'
    )
    assumption_list = (
        '<ol class="assumptions">' + "".join(f'<li>{_text(item)}</li>' for item in assumptions) + '</ol>'
        if assumptions else '<p class="empty">No assumptions declared.</p>'
    )
    relationship_rows = []
    for item in _items(semantics.get("relationships")):
        relation = _mapping(item)
        relationship_rows.append(
            '<tr><td>' + _text(relation.get("relation")) + '</td><td><code>'
            + _text(relation.get("target_id")) + '</code></td><td>'
            + _text(relation.get("target_version")) + '</td></tr>'
        )
    relationship_table = (
        '<table><thead><tr><th>Relation</th><th>Target identity</th><th>Target version</th></tr></thead><tbody>'
        + "".join(relationship_rows) + '</tbody></table>'
        if relationship_rows else '<p class="empty">No relationships declared.</p>'
    )
    semantic_section = (
        '<section class="panel" aria-labelledby="semantics-heading"><h2 id="semantics-heading">'
        'Meaning and uncertainty</h2><p class="small muted">Coordinate frame: <code>'
        + _text(semantics.get("coordinate_frame")) + '</code></p>' + property_table
        + '<details open><summary>All assumptions</summary>' + assumption_list + '</details>'
        + '<details><summary>Versioned relationships</summary><div class="table-wrap">'
        + relationship_table + '</div></details></section>'
    )

    identity_rows = [("Object identity", obj.get("object_id")), ("Object version", obj.get("version")),
                     ("Bundle identity", bundle.get("bundle_id")),
                     ("Source digest", _mapping(bundle.get("manifest")).get("source_digest"))]
    identity_rows.extend((label, bindings.get(key)) for label, key in [
        ("Evidence identity", "evidence_id"), ("Operation identity", "operation_id"),
        ("Execution identity", "execution_id"), ("Verification identity", "verification_id")])
    identity_table = '<table><tbody>' + "".join(
        '<tr><td>' + escape(label) + '</td><td><code>' + _text(value) + '</code></td></tr>'
        for label, value in identity_rows
    ) + '</tbody></table>'
    admission = qualification.get("canonical_admission")
    admission_label = (
        "Canonical admission: false" if admission is False
        else "Canonical admission: not assessed" if admission is None
        else "Canonical admission: declared " + _display(admission)
    )
    identity_section = (
        '<section class="panel" aria-labelledby="identity-heading"><h2 id="identity-heading">'
        'Identity and qualification</h2>' + _pill(admission_label)
        + '<p class="small muted">Distinct identities retain the boundaries between source evidence, '
        'the requested operation, its execution, and verification.</p>'
        + '<details><summary>Inspect identity and provenance</summary>' + identity_table + '</details>'
        + '<div class="two-col"><div><h3>Calibration references</h3>'
        + _refs(qualification.get("calibration_refs")) + '</div><div><h3>Qualification verification references</h3>'
        + _refs(qualification.get("verification_refs")) + '</div></div>'
        + _json_details("Inspect complete declared qualification", dict(qualification)) + '</section>'
    )

    artifact_rows = []
    for item in artifacts:
        artifact = _mapping(item)
        artifact_rows.append(
            '<tr><td><code>' + _text(artifact.get("artifact_id")) + '</code></td><td>'
            + _text(artifact.get("media_type")) + '</td><td>' + _text(artifact.get("size_bytes"))
            + '</td><td><code>' + _text(artifact.get("sha256")) + '</code></td></tr>'
        )
    artifact_table = (
        '<div class="table-wrap"><table><caption class="sr-only">Declared artifact identities and digests</caption>'
        '<thead><tr><th>Artifact identity</th><th>Media type</th><th>Bytes</th><th>Declared SHA-256</th></tr></thead>'
        '<tbody>' + "".join(artifact_rows) + '</tbody></table></div>'
        if artifact_rows else '<p class="empty">No artifacts declared.</p>'
    )
    vision_section = (
        '<section class="panel" aria-labelledby="vision-heading"><h2 id="vision-heading">'
        'Vision and artifact bindings</h2>' + _pill("Declared annotation metadata · detector unevaluated", "unresolved")
        + '<p class="small muted">Image artifact identity: <code>' + _text(vision.get("image_artifact_id"))
        + '</code></p><p class="note">Annotations are declarations. Detection, pose estimation, segmentation '
        'accuracy, lighting robustness, and occlusion performance have not been evaluated by this view.</p>'
        + _json_details("Inspect all declared annotations", vision.get("annotations", []))
        + '<h3>Artifact manifest</h3>' + artifact_table + '</section>'
    )
    raw_section = (
        '<section class="panel" aria-labelledby="representation-heading"><h2 id="representation-heading">'
        'Machine representations</h2><p class="small muted">Expand the exact supplied representations '
        'and contract when inspecting assumptions or adapter behavior.</p>'
        + _json_details("Human, reasoning, and vision representations", bundle.get("representations", {}))
        + _json_details("Complete source contract", dict(source))
        + _json_details("Complete bundle manifest", bundle.get("manifest", {})) + '</section>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
        'img-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        '<title>' + _text(label) + ' · Notations Legibility Instrument</title><style>' + _CSS
        + '</style></head><body><main>' + header + attention + verification + claim_section
        + semantic_section + identity_section + vision_section + raw_section
        + '<footer>Read-only offline inspection · Unsigned convenience rendering · No external assets or artifact bytes are loaded. '
        'This page checks report subject and seal, presents declarations, and does not execute cryptographic or physical verification or admit state.</footer>'
        '</main></body></html>'
    )
