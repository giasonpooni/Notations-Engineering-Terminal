"""Retained inspection, standalone rendering, escaping and CLI export tests."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ciw.session import Session, read_json
from ciw.system_cli import _retain_specification, main, summary
from ciw.system_spec import demo_spec
from ciw.system_view import inspection_summary, render_system_review, review_data, write_system_review
from ciw import system_workflow as workflow, system_study


@pytest.fixture
def retained(tmp_path):
    specs = [demo_spec(cells=8), demo_spec(cells=4)]
    session = Session(workflow.source_run(specs), tmp_path / "retained")
    sources = [_retain_specification(session, spec) for spec in specs]
    results = [workflow.run_specification(session, spec, source_id=source["source_id"])
               for spec, source in zip(specs, sources)]
    workflow.execute(session, workflow.COMPARE, {"left": results[0]["candidate"], "right": results[1]["candidate"],
        "source_result_id": results[0]["candidate"]["result_id"],
        "source_result_ids": [result["candidate"]["result_id"] for result in results]})
    return session, results, sources


def _embedded_data(html):
    match = re.search(r'<script id="retained-system-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match.group(1))


def test_compact_inspection_preserves_identity_findings_without_histories(retained):
    session, results, _ = retained
    compact = summary(session)
    full = summary(session, full=True)
    assert compact["detail"] == "compact" and full["detail"] == "full"
    assert len(compact["configurations"]) == len(compact["simulations"]) == 2
    report = compact["comparisons"][0]["report"]
    assert report["retained_report_digest"] == full["comparisons"][0]["report"]["record_digest"]
    assert report["schema"] == "ciw.system-report-projection.v1"
    assert report["retained_report_schema"] == full["comparisons"][0]["report"]["schema"]
    assert report["commutation"]["max_temperature_error_k"] >= 0
    assert "reduced_temperature_k" not in report["commutation"]
    assert "time_s" not in report["commutation"]
    assert "reduced_temperature_k" in full["comparisons"][0]["report"]["commutation"]
    assert "simulation" not in compact["simulations"][0]
    assert "trajectory" in full["simulations"][0]["simulation"]
    assert compact["numerical_reports"][0]["verification_id"] == results[0]["verification"]["data"]["verification_id"]
    assert all("details" not in check for check in compact["numerical_reports"][0]["report"]["checks"])
    assert compact["configurations"][0]["state_schema"]
    assert compact["configurations"][0]["known_bounds"]
    assert compact["physical_validation_status"] == "not_assessed"
    assert compact["canonical_admission"] is False
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 4


def test_views_never_invoke_compiler_solver_verifier_or_worker(retained):
    session, _, _ = retained
    before = deepcopy(session.results)
    with patch("ciw.system_spec.compile_spec", side_effect=AssertionError("view compiler")), \
         patch("ciw.system_models.simulate", side_effect=AssertionError("view solver")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("view verifier")), \
         patch("ciw.system_models.compare_configurations", side_effect=AssertionError("view comparison")), \
         patch("ciw.system_execution.execute_worker", side_effect=AssertionError("view worker")), \
         patch("ciw.system_study.run_temporal_study", side_effect=AssertionError("view study")):
        compact = inspection_summary(session)
        html = render_system_review(session)
    assert compact["simulations"]
    assert _embedded_data(html)["inspection"] == compact
    assert session.results == before


def test_review_uses_exact_retained_thermal_mechanical_and_sensor_samples(retained):
    session, results, _ = retained
    data = review_data(session)
    plotted = data["simulations"][0]
    original = results[0]["candidate"]["data"]["simulation"]["trajectory"]
    for name in ("time_s", "temperature_k", "displacement_m", "sensor_temperature_k",
                 "sensor_displacement_m", "snapshot_state_digests"):
        assert plotted["trajectory"][name] == original[name]
    assert plotted["candidate_digest"] == results[0]["candidate"]["data"]["simulation"]["record_digest"]
    assert "strain" not in plotted["trajectory"] and "stress_pa" not in plotted["trajectory"]
    plotted["trajectory"]["temperature_k"][0][0] = 0
    assert original["temperature_k"][0][0] > 0


def test_review_contains_controls_graph_explicit_authority_and_no_external_assets(retained):
    html = render_system_review(retained[0])
    for marker in ('id="primary"', 'id="secondary"', 'id="time"', 'id="thermal"', 'id="mechanical"',
                   'id="graph"', 'id="corrections"', 'id="checks"', 'id="inspection"'):
        assert marker in html
    assert "Physical validation:" in html and "NOT_ASSESSED" in html
    assert "Canonical admission:" in html and "FALSE" in html
    assert "Synthetic sensor" in html and "nearest stored time" in html
    assert "connect-src 'none'" in html and "default-src 'none'" in html
    assert not re.search(r'<(?:script|link|img|iframe)[^>]+(?:src|href)\s*=', html, re.I)
    assert "fetch(" not in html and "XMLHttpRequest" not in html
    assert "innerHTML" not in html and "eval(" not in html


def test_untrusted_labels_cannot_close_script_or_create_html(retained):
    session = retained[0]
    evil = '</script><script>globalThis.injected=true</script><img src=x onerror=alert(1)> & <b>name</b>'
    # Rendering is defensive even when presented an object outside the validated
    # workspace ingress. The renderer never grants authority to these labels.
    fake_results = deepcopy(session.results)
    for record in fake_results.values():
        if record["operation_id"] == workflow.RUN:
            record["parameters"]["plan"]["data"]["specification"]["configuration_id"] = evil
    fake = SimpleNamespace(results=fake_results, run=session.run, executions=session.executions,
                           dependency_status=session.dependency_status)
    html = render_system_review(fake)
    assert evil not in html
    assert "\\u003c/script\\u003e" in html
    assert _embedded_data(html)["simulations"][0]["configuration_id"] == evil
    assert html.count("<script") == 2


def test_unknown_additional_results_are_catalogued_safely(retained):
    session = retained[0]
    fake_results = deepcopy(session.results)
    fake_results["extension"] = {"result_id": "extension", "execution_id": "another-execution",
        "operation_id": "future.instrument.v9", "data": {"schema": "future.schema.v9", "opaque": [[1, 2]]}}
    fake = SimpleNamespace(results=fake_results, run=session.run, executions=session.executions,
                           dependency_status=session.dependency_status)
    compact = inspection_summary(fake)
    assert compact["other_results"][0]["schema"] == "future.schema.v9"
    assert compact["other_results"][0]["current_use"]["status"] == "unknown"
    assert "opaque" not in compact["other_results"][0]
    assert _embedded_data(render_system_review(fake))["inspection"]["other_results"]


def test_empty_retained_session_can_be_reviewed(tmp_path):
    session = Session(workflow.source_run([demo_spec()]), tmp_path)
    data = _embedded_data(render_system_review(session))
    assert data["simulations"] == [] and data["inspection"]["numerical_reports"] == []


def test_atomic_review_write_preserves_existing_file_on_replace_failure(tmp_path, retained):
    destination = tmp_path / "review.html"
    destination.write_text("old review", encoding="utf-8")
    with patch("ciw.system_view.os.replace", side_effect=OSError("replace refused")):
        with pytest.raises(OSError, match="replace refused"):
            write_system_review(destination, retained[0])
    assert destination.read_text(encoding="utf-8") == "old review"
    assert not list(tmp_path.glob(".review.html.*.tmp"))
    assert write_system_review(destination, retained[0]) == destination
    assert destination.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert b"\r\n" not in destination.read_bytes()


def test_cli_review_and_full_inspection_preserve_input_workspace(tmp_path, capsys, retained):
    session = retained[0]
    workspace = session.save_workspace(tmp_path / "original.json")
    before = workspace.read_bytes()
    output = tmp_path / "export" / "review.html"
    with patch("ciw.system_models.simulate", side_effect=AssertionError("inspection solver")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("inspection verifier")), \
         patch("ciw.system_execution.execute_worker", side_effect=AssertionError("inspection worker")):
        assert main(["review", str(workspace), "--output", str(output)]) == 0
        assert json.loads(capsys.readouterr().out)["review"] == str(output)
        assert main(["inspect", str(workspace)]) == 0
        compact = json.loads(capsys.readouterr().out)
        assert main(["inspect", str(workspace), "--full"]) == 0
        full = json.loads(capsys.readouterr().out)
    assert compact["detail"] == "compact" and full["detail"] == "full"
    assert "simulation" in full["simulations"][0]
    assert _embedded_data(output.read_text(encoding="utf-8"))["schema"] == "ciw.system-review.v1"
    assert workspace.read_bytes() == before


def test_cli_run_retains_exact_linked_specification_source(tmp_path, capsys):
    from ciw.session import write_json
    path = write_json(tmp_path / "spec.json", demo_spec(cells=4))
    destination = tmp_path / "run"
    assert main(["run", str(path), "--output-dir", str(destination)]) == 0
    capsys.readouterr()
    session = Session.from_workspace(destination / "workspace.json", tmp_path / "restored")
    plan = next(r for r in session.results.values() if r["operation_id"] == workflow.COMPILE)
    assert plan["parameters"]["source_id"] == plan["parameters"]["source"]["source_id"]
    source = session.workbench.get_source(plan["parameters"]["source_id"])
    assert source["kind"] == "system-specification"
    assert summary(session)["configurations"][0]["source_evidence_id"] == source["evidence_id"]


def test_temporal_study_compact_findings_preserve_startup_scope(retained):
    session, results, _ = retained
    report = workflow.execute(session, workflow.STUDY, {"plan": results[0]["plan"],
        "source_result_id": results[0]["plan"]["result_id"]})
    compact = summary(session)["temporal_studies"][0]["report"]
    full = summary(session, full=True)["temporal_studies"][0]["report"]
    assert compact["retained_report_digest"] == report["data"]["report"]["record_digest"]
    assert compact["samples_scope"] == "thermal_cell_averages_at_base_clock_samples_including_startup"
    assert "error_by_time_k" not in compact["studies"][0]
    assert "error_by_time_k" in full["studies"][0]
    assert compact["studies"][0]["startup_error_k"] >= 0
    assert compact["validity"]["mechanical_and_sensor_accuracy"] == "not_assessed"


def test_cli_study_retains_failure_and_uses_exact_compiled_occurrence(tmp_path, capsys):
    spec = demo_spec(cells=8)
    spec["tolerances"]["analytic_temperature_K"] = 1e-12
    session = Session(workflow.source_run([spec]), tmp_path / "source")
    plan = workflow.execute(session, workflow.COMPILE, {"specification": spec})
    workspace = session.save_workspace(tmp_path / "study-input.json")
    before = workspace.read_bytes()
    destination = tmp_path / "study"
    assert main(["study", str(workspace), "--output-dir", str(destination),
                 "--step-s", "5", "--step-s", "2.5"]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["temporal_studies"] == 1
    restored = Session.from_workspace(destination / "workspace.json", tmp_path / "study-restored")
    study = next(r for r in restored.results.values() if r["operation_id"] == workflow.STUDY)
    assert study["parameters"]["source_result_id"] == plan["result_id"]
    assert study["data"]["report"]["status"] == "FAIL"
    assert study["data"]["report"]["canonical_admission"] is False
    assert workspace.read_bytes() == before
    assert (destination / "review.html").exists()


def test_review_displays_real_accepted_correction_staleness_without_rewriting(retained):
    session, results, sources = retained
    replacement = _retain_specification(session, demo_spec(cells=8, force_n=6))
    before = deepcopy(session.results)
    proposal = session.correction_journal.propose({"old_source_id": sources[0]["source_id"],
        "new_source_id": replacement["source_id"], "kind": "configuration",
        "reason": "Revise the declared illustrative load."}, session._artifact_graph())
    session.correction_journal.review({"correction_id": proposal["correction_id"], "decision": "accept",
        "expected_revision": session.correction_journal.revision, "reviewer": "Declared view test operator",
        "reason": "Use the replacement configuration."}, session._artifact_graph())
    data = _embedded_data(render_system_review(session))
    inspection = data["inspection"]
    assert inspection["dependency_status"]["corrections"][0]["status"] == "accepted"
    assert inspection["simulations"][0]["current_use"]["status"] == "stale"
    assert inspection["simulations"][1]["current_use"]["status"] == "current"
    assert inspection["numerical_reports"][0]["current_use"]["status"] == "stale"
    assert inspection["numerical_reports"][0]["report"]["status"] == results[0]["verification"]["data"]["report"]["status"]
    assert session.results == before


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for static UI script checks")
def test_standalone_script_time_and_configuration_controls_read_retained_samples(tmp_path, retained):
    """Exercise the executable script with a small DOM harness; no browser claim."""
    destination = write_system_review(tmp_path / "review.html", retained[0])
    harness = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert");
const html=fs.readFileSync(process.argv[1],"utf8"),elements={};
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.listeners={};this.value="";this.max="0";this._text=""}
 set textContent(value){this._text=String(value);this.children=[]}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join("")}
 append(...children){this.children.push(...children);if(this.tag==="select"&&this.children.length===1)this.value=this.children[0].value}
 replaceChildren(...children){this.children=[];this._text="";this.append(...children)}
 setAttribute(key,value){this.attributes[key]=String(value)}
 addEventListener(key,listener){this.listeners[key]=listener}
}
for(const match of html.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"[^>]*>/g))elements[match[2]]=new Element(match[1]);
elements["retained-system-data"].textContent=html.match(/<script id="retained-system-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const document={getElementById:id=>elements[id],createElement:tag=>new Element(tag),createElementNS:(_ns,tag)=>new Element(tag)};
const context={document,window:{addEventListener(){}},setInterval(){return 1},clearInterval(){},console,fetch(){throw new Error("network forbidden")}};
vm.createContext(context);
const script=html.match(/<script>\r?\n([\s\S]*?)<\/script>/)[1];vm.runInContext(script,context,{timeout:3000});
assert(elements.checks.children.length>0);assert(elements.graph.children.length>0);
const initial=elements.thermal.children.find(e=>e.tag==="polyline").attributes.points;
elements.time.value="10";elements.time.listeners.input();
assert.notStrictEqual(elements.thermal.children.find(e=>e.tag==="polyline").attributes.points,initial);
assert(elements["time-label"].textContent.includes("sample 11"));
elements.primary.value="1";elements.primary.listeners.change();
assert.strictEqual(String(elements.time.value),"0");
assert(elements.identities.textContent.includes("polymer-free-4-cells"));
elements.secondary.value="";elements.secondary.listeners.change();
assert(elements["sample-note"].textContent.includes("Primary stored trajectory only"));
assert.strictEqual(context.injected,undefined);console.log("retained UI controls passed");
'''
    reply = subprocess.run([shutil.which("node"), "-e", harness, str(destination)], check=False,
                           capture_output=True, text=True, timeout=8)
    assert reply.returncode == 0, reply.stderr
    assert reply.stdout.strip() == "retained UI controls passed"
