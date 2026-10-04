"""Read-only, standalone views over retained scientific system occurrences.

This module does not import a solver, verifier, provider, or worker.  Curves and
status badges display retained data; changing a view never creates evidence or
changes an immutable scientific record.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile


COMPILE = "system.compile.v1"
RUN = "system.simulate.v1"
VERIFY = "system.verify.v1"
COMPARE = "system.compare.v1"
STUDY = "system.study.v1"


def _pick(value, fields):
    return {key: deepcopy(value[key]) for key in fields if key in value}


def _compact_report(report):
    """Keep dispositions, bounds and identity; omit sampled histories."""
    if not isinstance(report, dict):
        return {"status": "NOT_ASSESSED"}
    output = _pick(report, ("status", "scope", "spec_digest",
        "configuration_digest", "plan_digest", "candidate_digest", "fine_plan_digest",
        "coarse_plan_digest", "fine_configuration_digest", "coarse_configuration_digest",
        "fine_candidate_digest", "coarse_candidate_digest", "representation_id", "mapping_digest",
        "physical_validation_status", "canonical_admission", "compiler_id", "provider_id", "model_ids",
        "step_sizes_s", "startup_time_s", "samples_scope", "runtime"))
    output.update(schema="ciw.system-report-projection.v1", retained_report_schema=report.get("schema"),
                  retained_report_digest=report.get("record_digest"))
    output["checks"] = [_pick(check, ("check_id", "status", "value", "limit", "unit", "step_s", "scope"))
                        for check in report.get("checks", []) if isinstance(check, dict)]
    for name in ("mapping", "validity"):
        if isinstance(report.get(name), dict):
            output[name] = _pick(report[name], ("thermal", "mechanical", "representation_id",
                "mapping_digest", "fine_cells", "coarse_cells", "group_size", "information_discarded",
                "scope", "duration_s", "step_s", "frame", "temperature_threshold_k", "displacement_threshold_m",
                "time_range_s", "cells", "accuracy_limit_k", "unsampled_times", "mechanical_and_sensor_accuracy",
                "assumed_convergence_order"))
    commutation = report.get("commutation")
    if isinstance(commutation, dict):
        output["commutation"] = _pick(commutation, ("relation", "max_temperature_error_k", "max_displacement_error_m"))
    if isinstance(report.get("studies"), list):
        output["studies"] = [_pick(study, ("step_s", "spec_digest", "configuration_digest", "plan_digest",
            "candidate_digest", "steps", "accuracy_status", "max_error_k", "startup_error_k"))
            for study in report["studies"] if isinstance(study, dict)]
        output["improvement"] = deepcopy(report.get("improvement", []))
    return output


def _dependency_summary(dependencies, *, full=False):
    if full:
        return deepcopy(dependencies)
    output = _pick(dependencies, ("schema", "revision", "nodes", "artifact_status", "scope",
                                 "physical_validation", "state_admission", "hardware_actuation"))
    output["claims"] = [_pick(claim, ("claim_id", "claim_type", "predicate", "scope", "dependencies",
        "verification_status", "state_admission", "execution_authorized"))
        for claim in dependencies.get("claims", [])]
    output["corrections"] = []
    for correction in dependencies.get("corrections", []):
        proposal, review = correction.get("proposal", {}), correction.get("review") or {}
        output["corrections"].append({"status": correction.get("status", "proposed"),
            "proposal": _pick(proposal, ("correction_id", "old_source_id", "new_source_id", "old_evidence_id",
                "new_evidence_id", "source_kind", "kind", "reason")),
            "review": _pick(review, ("decision_id", "decision", "reviewer", "reason", "reviewer_identity_basis"))})
    return output


def inspection_summary(session, *, full=False):
    """Inspect retained identities and findings without scientific computation."""
    dependencies = session.dependency_status()
    dispositions = dependencies.get("artifact_status", {})
    def current(record):
        return deepcopy(dispositions.get(record.get("result_id"), {"status": "unknown", "stale_by": []}))
    configurations, simulations, reports, comparisons, studies, other = [], [], [], [], [], []
    for record in session.results.values():
        data = record.get("data", {})
        if not isinstance(data, dict):
            data = {}
        identity = _pick(record, ("result_id", "execution_id", "record_digest", "evidence_id", "operation_id", "runtime"))
        identity["current_use"] = current(record)
        operation = record.get("operation_id")
        if operation == COMPILE:
            spec = data.get("specification", {})
            configurations.append({**identity, **_pick(data, ("schema", "spec_digest", "configuration_digest",
                "plan_digest", "state_schema", "frame", "constraints")),
                "configuration_id": spec.get("configuration_id"),
                "source_id": record.get("parameters", {}).get("source_id"),
                "source_evidence_id": record.get("parameters", {}).get("source", {}).get("evidence_id"),
                "known_bounds": deepcopy(spec.get("validity", {})),
                "declared_resources": deepcopy(spec.get("execution", {}).get("resources", {}))})
        elif operation == RUN:
            candidate = data.get("simulation", {})
            trajectory = candidate.get("trajectory", {})
            times = trajectory.get("time_s", [])
            simulation = {**identity, **_pick(candidate, ("schema", "spec_digest", "configuration_digest", "plan_digest",
                "frame", "physical_validation_status", "canonical_admission")),
                "candidate_digest": candidate.get("record_digest"),
                "execution_runtime": _pick(data.get("execution_runtime", {}), ("engine", "python", "image_reference",
                    "image_digest", "resources", "resource_limits_enforced", "timeout_s", "environment_attestation")),
                "sample_count": len(times), "time_range_s": [times[0], times[-1]] if times else [],
                "sensor_ids": deepcopy(trajectory.get("sensor_ids", []))}
            if full:
                simulation["simulation"] = deepcopy(candidate)
            simulations.append(simulation)
        elif operation in (VERIFY, COMPARE, STUDY):
            projected = {**identity, **_pick(data, ("schema", "verification_id", "candidate_result_id",
                "candidate_execution_id", "candidate_record_digest", "source_evidence_id", "left_result_id",
                "right_result_id", "left_record_digest", "right_record_digest", "source_result_ids",
                "plan_result_id", "plan_record_digest")),
                "report": deepcopy(data.get("report", {})) if full else _compact_report(data.get("report", {}))}
            {VERIFY: reports, COMPARE: comparisons, STUDY: studies}[operation].append(projected)
        else:
            other.append({**identity, **_pick(data, ("schema", "status"))})
    return {"schema": "ciw.system-inspection.v1", "detail": "full" if full else "compact",
        "evidence_id": session.run.get("evidence_id"), "run_id": session.run.get("run_id"),
        "configurations": configurations, "simulations": simulations, "numerical_reports": reports,
        "comparisons": comparisons, "temporal_studies": studies, "other_results": other,
        "executions": [_pick(item, ("execution_id", "operation_id", "status", "result_id"))
                       for item in session.executions.values()],
        "dependency_status": _dependency_summary(dependencies, full=full),
        "physical_validation_status": "not_assessed", "canonical_admission": False}


def review_data(session):
    """Extract plots from recorded trajectories; never regenerate samples."""
    overview = inspection_summary(session)
    entries = []
    for record in session.results.values():
        if record.get("operation_id") != RUN:
            continue
        parameters, payload = record.get("parameters", {}), record.get("data", {})
        plan = parameters.get("plan", {}).get("data", {})
        candidate = payload.get("simulation", {})
        spec = plan.get("specification", {})
        entries.append({"result_id": record.get("result_id"), "execution_id": record.get("execution_id"),
            "configuration_id": spec.get("configuration_id", "Unnamed configuration"),
            "plan_digest": plan.get("plan_digest"), "configuration_digest": candidate.get("configuration_digest"),
            "candidate_digest": candidate.get("record_digest"), "frame": candidate.get("frame"),
            "trajectory": _pick(candidate.get("trajectory", {}), ("time_s", "temperature_k", "displacement_m",
                "temperature_cell_centers_m", "displacement_nodes_m", "sensor_ids", "sensor_temperature_k",
                "sensor_displacement_m", "snapshot_state_digests")),
            "sensors": deepcopy(plan.get("sensors", [])),
            "nodes": [_pick(node, ("node_id", "model_id")) for node in spec.get("nodes", [])],
            "couplings": [_pick(edge, ("source", "target", "quantity", "unit", "method")) for edge in spec.get("couplings", [])],
            "known_bounds": deepcopy(spec.get("validity", {})), "assumptions": deepcopy(candidate.get("assumptions", [])),
            "engine": payload.get("execution_runtime", {}).get("engine", "unknown")})
    return {"schema": "ciw.system-review.v1", "inspection": overview, "simulations": entries}


def _safe_json(value):
    # Escaping '<' protects script end tags, including inside untrusted labels.
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).replace(
        "<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_system_review(session):
    """Return a self-contained HTML inspection surface with no remote assets."""
    return _HTML.replace("__RETAINED_SYSTEM_DATA__", _safe_json(review_data(session)))


def write_system_review(destination, session):
    """Atomically replace only a fully rendered review file."""
    destination = Path(destination)
    content = render_system_review(session)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
                prefix="." + destination.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src data:">
<title>NET · Retained system review</title><style>
:root{color-scheme:dark;--bg:#0d151c;--panel:#15222c;--border:#2b404d;--ink:#dfebef;--muted:#9aafb9;--cyan:#65d5d1;--amber:#ffcc80;--red:#ff9b9b;--purple:#c1a8ef}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}main{max-width:1380px;margin:auto;padding:32px}h1{font-size:29px;letter-spacing:-.03em;margin:6px 0}h2{font-size:18px;margin:0 0 16px}p{margin:8px 0;color:var(--muted)}small,.muted{color:var(--muted)}.kicker{font:12px ui-monospace,monospace;letter-spacing:.18em;color:var(--cyan)}.top,.bar{display:flex;justify-content:space-between;gap:20px;align-items:center;flex-wrap:wrap}.authority{padding:12px 16px;border:1px solid #755c38;color:var(--amber);font-size:13px}.grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:20px;margin-top:20px}.panel{background:var(--panel);border:1px solid var(--border);padding:22px;border-radius:10px;min-width:0}.controls{display:grid;grid-template-columns:1fr 1fr;gap:18px}label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}select{width:100%;color:var(--ink);background:#0e1921;border:1px solid var(--border);border-radius:5px;padding:11px;font-size:14px}input[type=range]{width:100%;accent-color:var(--cyan)}.time{font:15px ui-monospace,monospace;color:var(--cyan)}.plot{display:block;width:100%;height:auto;background:#101b23;border-radius:7px;border:1px solid var(--border)}.plot-title{display:flex;gap:12px;justify-content:space-between;margin:18px 0 9px}.legend{display:flex;gap:18px;color:var(--muted);font-size:12px;margin:12px 0}.a{color:var(--cyan)}.b{color:var(--purple)}.badge{display:inline-block;padding:3px 7px;font:11px ui-monospace,monospace;border-radius:3px;background:#233541;color:var(--muted)}.PASS,.current{background:#183c38;color:#9be2b5}.FAIL,.stale{background:#4a282c;color:var(--red)}.NOT_ASSESSED,.proposed{background:#413626;color:var(--amber)}.ids{font:11px/1.6 ui-monospace,monospace;overflow-wrap:anywhere;color:var(--muted)}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:15px 0}.metric{background:#101b23;padding:12px;border-radius:5px}.metric strong{display:block;font:19px ui-monospace,monospace}.metric label{margin:0}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;border-bottom:1px solid var(--border);padding:10px 8px;vertical-align:top}th{color:var(--muted);font-weight:500}td{overflow-wrap:anywhere}details{margin:14px 0}summary{cursor:pointer;color:var(--cyan)}pre{font:11px/1.5 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:340px;overflow:auto}ul{padding-left:20px;font-size:13px;color:var(--muted)}.graph{height:155px;width:100%;background:#101b23;border-radius:7px}.full{grid-column:1/-1}.notice{color:var(--amber);font-size:13px}button{background:#223843;color:var(--ink);border:1px solid var(--border);border-radius:5px;padding:7px 13px;cursor:pointer}.empty{padding:40px;color:var(--muted)}@media(max-width:850px){main{padding:20px}.grid{grid-template-columns:1fr}.controls{grid-template-columns:1fr}.panel{padding:17px}}@media print{body{background:white;color:black}.panel{background:white;border-color:#ccc}.controls,input,button{display:none}.grid{display:block}.panel{margin-top:15px}.plot,.graph{background:white}p,small,.muted{color:#444}}
</style></head><body><main>
<header class="top"><div><div class="kicker">NET / SCIENTIFIC SYSTEMS</div><h1>Retained system review</h1><p>Inspect configurations, state and numerical findings from one saved session.</p></div><div class="authority">Physical validation: <strong>NOT_ASSESSED</strong><br>Canonical admission: <strong>FALSE</strong></div></header>
<div class="grid"><section class="panel"><h2>Configuration and synchronized state</h2><div class="controls"><div><label for="primary">Primary occurrence</label><select id="primary"></select></div><div><label for="secondary">Compare occurrence</label><select id="secondary"></select></div></div>
<div class="bar" style="margin-top:18px"><label for="time">Retained sample time</label><output id="time-label" class="time"></output><button id="play" type="button">Play retained samples</button></div><input id="time" type="range" min="0" max="0" value="0" step="1" aria-label="Retained sample index"><p id="sample-note" class="muted"></p>
<div class="plot-title"><strong>Thermal cell averages</strong><small>K · position in m</small></div><svg id="thermal" class="plot" viewBox="0 0 760 235" role="img" aria-label="Retained thermal profile"></svg>
<div class="plot-title"><strong>Axial displacement</strong><small>µm · position in m</small></div><svg id="mechanical" class="plot" viewBox="0 0 760 235" role="img" aria-label="Retained displacement profile"></svg>
<div class="legend"><span class="a">— Primary</span><span class="b">— Comparison</span><span>● Synthetic sensor · ±1σ</span></div><p class="notice">Displayed sensors are synthetic model observations. The slider reads stored samples.</p></section>
<aside class="panel"><h2>Occurrence and current use</h2><div id="state-badges"></div><div id="metrics" class="metric-grid"></div><div id="identities" class="ids"></div><details open><summary>Configuration graph</summary><svg id="graph" class="graph" viewBox="0 0 430 155" role="img" aria-label="Declared thermal mechanical and sensor couplings"></svg><div id="edge-list" class="ids"></div></details><details><summary>Declared bounds and assumptions</summary><pre id="bounds"></pre><ul id="assumptions"></ul></details></aside>
<section class="panel full"><div class="bar"><h2>Retained numerical checks</h2><span class="muted">Separate from physical validation</span></div><table><thead><tr><th>Check</th><th>Status</th><th>Value</th><th>Limit</th><th>Unit</th></tr></thead><tbody id="checks"></tbody></table><p id="verification-note"></p></section>
<section class="panel"><h2>Configuration comparisons and studies</h2><div id="comparisons"></div><p class="notice">Reduction bounds apply to the declared parameters, initial state and recorded observation times.</p></section>
<section class="panel"><h2>Correction review and dependency use</h2><p id="correction-note"></p><div id="corrections"></div><details><summary>Stale artifact identities</summary><pre id="stale"></pre></details><p class="muted">An accepted correction changes current dependency eligibility. Historical records and numerical findings remain retained.</p></section>
<section class="panel full"><details><summary>Compact inspection JSON and identity links</summary><pre id="inspection"></pre></details><p class="muted">This file is standalone. No remote assets, provider calls or hardware commands are used.</p></section></div>
</main><script id="retained-system-data" type="application/json">__RETAINED_SYSTEM_DATA__</script><script>
"use strict";
const DATA=JSON.parse(document.getElementById("retained-system-data").textContent), O=DATA.inspection, S=DATA.simulations;
const $=id=>document.getElementById(id), NS="http://www.w3.org/2000/svg", primary=$("primary"),secondary=$("secondary"),slider=$("time");
const num=v=>typeof v==="number"&&Number.isFinite(v)?(Math.abs(v)>=1e4||Math.abs(v)>0&&Math.abs(v)<1e-3?v.toExponential(3):v.toPrecision(5).replace(/\.?0+$/,"")):"—";
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);if(className)e.className=className;return e}
function svg(tag,attrs,text){const e=document.createElementNS(NS,tag);for(const [key,value]of Object.entries(attrs))e.setAttribute(key,String(value));if(text!==undefined)e.textContent=String(text);return e}
function badge(text){return node("span",text,"badge "+(["PASS","FAIL","NOT_ASSESSED","current","stale","proposed"].includes(text)?text:""))}
function nearest(times,time){let i=0;while(i+1<times.length&&Math.abs(times[i+1]-time)<Math.abs(times[i]-time))i++;return i}
function current(id){return O.dependency_status.artifact_status[id]||{status:"unknown",stale_by:[]}}
function reportFor(id){return O.numerical_reports.filter(v=>v.candidate_result_id===id).at(-1)}
function option(value,label){const e=node("option",label);e.value=value;return e}
S.forEach((s,i)=>primary.append(option(i,s.configuration_id+" · "+s.engine+" · "+String(s.execution_id).slice(-8))));
secondary.append(option("","No comparison"));S.forEach((s,i)=>secondary.append(option(i,s.configuration_id+" · "+s.engine)));
if(S.length>1)secondary.value="1";
function chart(id,series,yscale){const out=$(id);out.replaceChildren();const valid=series.filter(s=>s.x.length&&s.y.length);if(!valid.length){out.append(svg("text",{x:25,y:90,fill:"#9aafb9"},"No retained profile"));return}
let xs=[],ys=[];valid.forEach(s=>{xs.push(...s.x);ys.push(...s.y.map(v=>v*yscale));s.sensors.forEach(v=>{xs.push(v.x);ys.push((v.y-v.std)*yscale,(v.y+v.std)*yscale)})});
let xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);if(xmax===xmin)xmax=xmin+1;let pad=(ymax-ymin)*.12||1;ymin-=pad;ymax+=pad;
const X=v=>70+(v-xmin)/(xmax-xmin)*660,Y=v=>193-(v*yscale-ymin)/(ymax-ymin)*167;
for(let i=0;i<=4;i++){let y=ymin+(ymax-ymin)*i/4,yp=193-i/4*167;out.append(svg("line",{x1:70,y1:yp,x2:730,y2:yp,stroke:"#263c49"}));out.append(svg("text",{x:59,y:yp+4,fill:"#9aafb9","text-anchor":"end","font-size":11},num(y)))}
for(let i=0;i<=4;i++){let x=xmin+(xmax-xmin)*i/4;out.append(svg("text",{x:X(x),y:218,fill:"#9aafb9","text-anchor":"middle","font-size":11},num(x)))}
valid.forEach(s=>{out.append(svg("polyline",{points:s.x.map((x,i)=>X(x)+","+Y(s.y[i])).join(" "),fill:"none",stroke:s.color,"stroke-width":2}));s.sensors.forEach(v=>{out.append(svg("line",{x1:X(v.x),x2:X(v.x),y1:Y(v.y-v.std),y2:Y(v.y+v.std),stroke:s.color,"stroke-width":2}));out.append(svg("circle",{cx:X(v.x),cy:Y(v.y),r:4,fill:s.color,stroke:"#101b23","stroke-width":1}))})})}
function series(s,index,key){const t=s.trajectory,thermal=key==="temperature_k",sensorKey=thermal?"sensor_temperature_k":"sensor_displacement_m";return{x:t[thermal?"temperature_cell_centers_m":"displacement_nodes_m"]||[],y:(t[key]||[])[index]||[],color:s===S[Number(primary.value)]?"#65d5d1":"#c1a8ef",sensors:s.sensors.map((v,j)=>({x:v.position_m,y:((t[sensorKey]||[])[index]||[])[j],std:v[thermal?"temperature_std_k":"displacement_std_m"]||0})).filter(v=>Number.isFinite(v.y))}}
function graph(s){const out=$("graph");out.replaceChildren();const nodes=s.nodes.slice(0,4),xy={};nodes.forEach((n,i)=>xy[n.node_id]=[65+i*140,75]);s.couplings.forEach((e,i)=>{const a=xy[e.source?.node],b=xy[e.target?.node];if(a&&b){out.append(svg("path",{d:"M"+a[0]+" "+a[1]+" Q"+(a[0]+b[0])/2+" "+(i===1?5:125)+" "+b[0]+" "+b[1],fill:"none",stroke:"#426675","stroke-width":1.5}));}});nodes.forEach(n=>{const[x,y]=xy[n.node_id];out.append(svg("rect",{x:x-55,y:y-20,width:110,height:40,rx:5,fill:"#203440",stroke:"#426675"}));out.append(svg("text",{x,y:y+4,"text-anchor":"middle",fill:"#dfebef","font-size":12},n.node_id))});$("edge-list").textContent=s.couplings.map(e=>e.source?.node+" → "+e.target?.node+" · "+e.quantity+" ["+e.unit+"]").join("\n")}
function update(){if(!S.length){$("sample-note").textContent="No retained simulations in this workspace.";$("play").disabled=true;return}const a=S[Number(primary.value)],time=a.trajectory.time_s||[];slider.max=Math.max(0,time.length-1);let ai=Math.min(Number(slider.value),Number(slider.max));slider.value=ai;let t=time[ai];$("time-label").textContent=num(t)+" s · sample "+(ai+1)+" / "+time.length;const b=secondary.value===""?null:S[Number(secondary.value)],bi=b?nearest(b.trajectory.time_s||[],t):0;
$("sample-note").textContent=b?"Primary: "+num(t)+" s. Comparison retained sample: "+num(b.trajectory.time_s?.[bi])+" s (nearest stored time).":"Primary stored trajectory only.";
chart("thermal",[series(a,ai,"temperature_k"),...(b?[series(b,bi,"temperature_k")]:[])],1);chart("mechanical",[series(a,ai,"displacement_m"),...(b?[series(b,bi,"displacement_m")]:[])],1e6);
const r=reportFor(a.result_id),status=current(a.result_id);$("state-badges").replaceChildren(badge(a.engine),node("span"," "),badge(status.status),node("span"," "),badge(r?.report.status||"NOT_ASSESSED"));
const m=$("metrics");m.replaceChildren();for(const[label,value]of[["Thermal cells",a.trajectory.temperature_cell_centers_m?.length||0],["Sensors",a.sensors.length]]){const e=node("div",undefined,"metric");e.append(node("label",label),node("strong",value));m.append(e)}
$("identities").textContent=["Configuration: "+a.configuration_id,"Configuration digest: "+a.configuration_digest,"Plan digest: "+a.plan_digest,"Candidate digest: "+a.candidate_digest,"Result: "+a.result_id,"Execution: "+a.execution_id,"State: "+(a.trajectory.snapshot_state_digests?.[ai]||"unknown"),"Frame: "+a.frame,"Stale by: "+(status.stale_by||[]).join(", ")].join("\n");graph(a);$("bounds").textContent=JSON.stringify(a.known_bounds,null,2);$("assumptions").replaceChildren(...a.assumptions.map(v=>node("li",v)));
const checks=$("checks");checks.replaceChildren();(r?.report.checks||[]).forEach(c=>{const row=node("tr");row.append(node("td",c.check_id));const cell=node("td");cell.append(badge(c.status));row.append(cell,node("td",num(c.value)),node("td",num(c.limit)),node("td",c.unit||"—"));checks.append(row)});$("verification-note").textContent=r?"Retained verification "+r.verification_id+" · current use: "+current(r.result_id).status:"No retained numerical verification for this occurrence."}
function reports(){const container=$("comparisons");if(!O.comparisons.length&&!O.temporal_studies.length)container.append(node("p","No retained comparison or convergence study."));[...O.comparisons,...O.temporal_studies].forEach(r=>{const d=node("details"),title=node("summary");title.append(node("span",r.operation_id+" "),badge(r.report.status||"NOT_ASSESSED"),node("span"," · "+current(r.result_id).status));d.append(title);const p=node("pre",JSON.stringify(r.report,null,2));d.append(p);container.append(d)});const deps=O.dependency_status,stale=Object.values(deps.artifact_status||{}).filter(s=>s.status==="stale");$("correction-note").textContent="Journal revision "+(deps.revision||0)+" · "+deps.corrections.length+" corrections · "+stale.length+" stale artifacts.";$("stale").textContent=JSON.stringify(stale,null,2);deps.corrections.forEach(c=>{const d=node("details"),title=node("summary");title.append(node("span",c.proposal.correction_id+" "),badge(c.status));d.append(title,node("pre",JSON.stringify(c,null,2)));$("corrections").append(d)});if(!deps.corrections.length)$("corrections").append(node("p","No proposed or reviewed source correction."));$("inspection").textContent=JSON.stringify(O,null,2)}
let timer=null;function stop(){if(timer!==null)clearInterval(timer);timer=null;$("play").textContent="Play retained samples"}
$("play").addEventListener("click",()=>{if(timer!==null){stop();return}$("play").textContent="Pause";timer=setInterval(()=>{if(Number(slider.value)>=Number(slider.max)){stop();return}slider.value=Number(slider.value)+1;update()},120)});
primary.addEventListener("change",()=>{stop();slider.value=0;update()});secondary.addEventListener("change",update);slider.addEventListener("input",()=>{stop();update()});window.addEventListener("pagehide",stop);reports();update();
</script></body></html>'''
