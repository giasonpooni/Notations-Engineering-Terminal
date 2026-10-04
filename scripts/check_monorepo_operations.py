"""Qualify public composition, budget, BIM and bounded CPU execution imports.

Independent package suites and original NET integrations run outside the source
tree. Current source wheels and historical execution bindings remain distinct.
Optional BIM integrations retain their original, explicitly unqualified skips.
No live service, device, GPU, prover or recorded Windows worker is executed.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import venv
import xml.etree.ElementTree as ET

if __package__:
    from .check_monorepo import DEPENDENCIES, _copy_source, _environment, _git, _IMPORT_PROBE, _junit, _run, _wheel
    from .check_monorepo_inference import FLOWSTATE_REVISION, _flowstate, _worktree
    from .check_monorepo_budget import qualify_budget
    from .monorepo import ROOT, load_manifest, provider_worktrees, verify_imports, verify_terminal_source
else:
    from check_monorepo import DEPENDENCIES, _copy_source, _environment, _git, _IMPORT_PROBE, _junit, _run, _wheel
    from check_monorepo_inference import FLOWSTATE_REVISION, _flowstate, _worktree
    from check_monorepo_budget import qualify_budget
    from monorepo import ROOT, load_manifest, provider_worktrees, verify_imports, verify_terminal_source

PACKAGE_ROLES = frozenset({"sra", "ywir", "cse"})
IDENTIFIED_ROLES = frozenset({"mcur", "tbrt", "oit", "gsie", "cbsr", "fdir", "set", "sidt", "edspt", "ywir"})
RUNTIME_ROLES = IDENTIFIED_ROLES | {"sra", "scr", "cse"}
SCOPE = ("Public independently installed packages, synthetic eleven-provider identified-design "
         "recomputation, scalar BIM quantity conditioning, schematic eligibility and bounded CPU integer "
         "heat execution only. No physical validation, independent verification, canonical state "
         "admission, acquisition, provider billing, freight or device actuation, GPU/prover qualification, "
         "native-host worker qualification, or attached browser qualification.")
BIM_SKIP_REASONS = frozenset({
    "public IFC corpus not fetched", "companion JSPT clone is not importable",
    "companion PLSR clone is not importable", "optional usd-core runtime is not installed",
    "optional OpenUSD runtime not installed", "ifcopenshell extra is not installed",
})
BIM_SDK_BRANCH_SKIPS = {
    "tests.test_openusd_combined_stage.CombinedUsdStageWithoutRuntimeTests.test_missing_usd_core_fails_closed": "usd-core is installed in this environment",
    "tests.test_ifcopenshell_adapter.IfcOpenShellAdapterTests.test_missing_runtime_fails_closed": "ifcopenshell is installed in this environment",
}
JSPT_COMPANION_REVISION = "7399ab03087b27683620b4c57f97b2ac14546c7f"
BIM_IMPLEMENTED_COMPANION_CASES = (
    "tests/test_jspt_consume.py::JsptConsumeTests::test_derived_view_uses_jspt",
    "tests/test_jspt_consume.py::JsptConsumeTests::test_gat_chart_uses_jspt_grams",
    "tests/test_jspt_pin.py::JsptAgreementTests::test_chart_push_is_consistent_with_the_pushforward",
    "tests/test_jspt_pin.py::JsptAgreementTests::test_full_view_covariance_matches_jspt_bitwise",
    "tests/test_jspt_pin.py::JsptAgreementTests::test_local_algebra_is_the_same_expression_the_owner_computes",
    "tests/test_survey_bind.py::SurveyBindChartTests::test_bind_uses_jspt_grams",
)
_BINDING_NAMES = frozenset({"CIW_CSE_REPO", "CIW_DECLARED_STACK_ROOT", "CIW_SCR_ENGINE",
    "CIW_DECLARED_FIXTURE_DIR", "CIW_IDENTIFIED_DESIGN_STACK_ROOT", "CIW_CALIBRATED_STACK_ROOT",
    "CIW_WINDOW_FIXTURE_DIR", "GAT_IFC_VALIDATION_ROOT", "GAT_JSPT_REPO", "GAT_PLSR_REPO",
    "JSPT_REPO", "PLSR_REPO", "SCR_PROVIDER_HOST", "SCR_NATIVE_REQUIRED", "SCR_JULIA_WORKER",
    "SCR_REACTION_WORKER", "SCR_INTERVAL_WORKER", "RUN_LIVE_TESTS"})


def _run_bound(arguments, *, cwd: Path, log: Path, bindings=None, timeout=600) -> str:
    environment = _environment()
    for name in list(environment):
        if name in _BINDING_NAMES or name.startswith(("GAT_", "CIW_", "SCR_")):
            environment.pop(name)
    environment.update({name: str(value) for name, value in (bindings or {}).items()})
    command = [str(argument) for argument in arguments]
    display = list(command)
    if "-c" in display:
        index = display.index("-c") + 1
        if "\n" in display[index]:
            display[index] = "<inline-sha256:" + sha256(command[index].encode()).hexdigest() + ">"
    with log.open("ab") as stream:
        stream.write(("\n$ " + " ".join(display) + "\n").encode())
        result = subprocess.run(command, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
        stream.write(result.stdout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(display[:4])}\n"
                           + result.stdout.decode(errors="replace")[-8000:])
    return result.stdout.decode()


def _python(path: Path, temporary: Path, log: Path, *, numerical=True) -> Path:
    venv.EnvBuilder(with_pip=True).create(path)
    python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = ["setuptools>=77", "wheel", "hatchling>=1.27"]
    requirements += DEPENDENCIES if numerical else ["pytest==9.0.2"]
    _run([python, "-I", "-m", "pip", "install", *requirements], cwd=temporary, log=log)
    return python


def _bim_junit(path: Path, *, sdk=False) -> dict:
    tree = ET.parse(path).getroot()
    cases = list(tree.iter("testcase"))
    skipped = []
    if not cases:
        raise AssertionError("BIM's original suite must not be empty")
    for case in cases:
        if case.find("failure") is not None or case.find("error") is not None:
            raise AssertionError("The original BIM suite contains a failure/error")
        skip = case.find("skipped")
        if skip is not None:
            reason = skip.get("message", "")
            name = case.get("classname", "") + "." + case.get("name", "")
            branch = sdk and BIM_SDK_BRANCH_SKIPS.get(name) == reason
            allowed = {"companion PLSR clone is not importable", "companion JSPT clone is not importable"} if sdk else BIM_SKIP_REASONS
            if not branch and reason not in allowed:
                raise AssertionError("Unexpected BIM skip: " + ET.tostring(case, encoding="unicode"))
            skipped.append({"name": name, "reason": reason,
                            "qualification": "opposite_configuration_tested_in_base_suite" if branch else "not_provisioned"})
    if any(int(suite.get(name, "0")) for suite in tree.iter("testsuite") for name in ("errors", "failures")):
        raise AssertionError("BIM collection errors cannot be treated as optional skips")
    return {"tests": len(cases), "passed": len(cases) - len(skipped), "failures": 0,
            "errors": 0, "skipped": len(skipped), "unqualified_checks": skipped,
            "junit": str(path), "junit_sha256": sha256(path.read_bytes()).hexdigest()}


def _test(python: Path, suite: Path, output: Path, name: str, log: Path, *, tests="tests",
          bindings=None, extra=(), timeout=600, bim=False) -> dict:
    junit = output / (name + "-tests.xml")
    _run_bound([python, "-I", "-m", "pytest", "-q", *extra, "--junitxml", junit, tests],
               cwd=suite, log=log, bindings=bindings, timeout=timeout)
    result = _bim_junit(junit) if bim else _junit(junit)
    result.setdefault("passed", result["tests"])
    return result


def _bim_companion(python: Path, suite: Path, temporary: Path, output: Path, log: Path) -> dict:
    """Qualify only the six original interfaces implemented by the old pin."""
    with provider_worktrees(ROOT,revisions="runtime",roles=["jspt"],
                            overrides={"jspt":JSPT_COMPANION_REVISION}) as sources:
        build = temporary / "bim-companion-build"; _copy_source(sources["jspt"],build)
        wheel = _wheel(python,build,temporary / "bim-companion-wheel",log)
        _run([python,"-I","-m","pip","install","--no-deps",wheel["path"]],cwd=temporary,log=log)
        probe = json.loads(_run_bound([python,"-I","-c",_IMPORT_PROBE,
            json.dumps({"sensitivity":{key:wheel[key] for key in ("distribution","version")}})],cwd=temporary,log=log))
        junit = output / "cse-implemented-jspt-tests.xml"
        _run_bound([python,"-m","pytest","-q","--junitxml",junit,*BIM_IMPLEMENTED_COMPANION_CASES],
                   cwd=suite,log=log)
        tests = _junit(junit)
        if tests["tests"] != 6:
            raise AssertionError("Only the six original implemented BIM/JSPT cases are qualified")
        return {**tests,"runtime_revision":JSPT_COMPANION_REVISION,"installed_package":probe,
            "wheel":{key:value for key,value in wheel.items() if key != "path"},
            "selected_original_cases":BIM_IMPLEMENTED_COMPANION_CASES,
            "excluded_unavailable_interface":"tests/test_grams_guest_pin.py::GramsGuestPinTests::test_gat_reads_jspt_grams_receipt",
            "scope":"Existing pinned public covariance and quantity interfaces only; no grams guest or whole companion-suite qualification"}


_SNAPSHOT = r'''
from hashlib import sha256
import json, pathlib, runpy, sys
from ciw import calibrated_observable as calibrated, identified_design as design
from ciw.bim_quantity import workflow as bim
from ciw.telemetry import canonical

config = json.loads(pathlib.Path(sys.argv[1]).read_text())
providers = {role: pathlib.Path(path) for role, path in config['providers'].items()}
root = pathlib.Path(config['fixtures'])
calibrated_providers = {role:providers[role] for role in calibrated.ROLES}
design_providers = {role:providers[role] for role in design.ROLES}
upstream = calibrated.create_session((root / 'calibrated-observable/source.json').read_bytes(), calibrated_providers)
original = design.create_session((root / 'identified-design/source.json').read_bytes(), upstream, design_providers)
replay = design.replay_session(original, design_providers)
fresh = replay['session']
assert replay['replay_receipt']['numerical_match'] is True
assert replay['replay_receipt']['admission'] == 'not_performed'
assert original['verification']['independent'] is fresh['verification']['independent'] is False
assert original['session_id'] != fresh['session_id']
assert original['verification']['verification_id'] != fresh['verification']['verification_id']
assert [s['numerical_result_id'] for s in original['steps']] == [s['numerical_result_id'] for s in fresh['steps']]
assert len({s['execution_id'] for session in (original,fresh) for s in session['steps']}) == 2 * len(design.OPERATIONS)
assert len({s['result_id'] for session in (original,fresh) for s in session['steps']}) == 2 * len(design.OPERATIONS)
assert original['decision']['state_admission'] == original['decision']['acquisition'] == 'not_performed'
data = {s['runtime_ref']: s['result'].get('data', s['result']) for s in original['steps']}
assert data['sidt']['numerical_result']['parameter_covariance']['status'] == 'unknown'
assert data['sidt']['numerical_result']['parameter_covariance']['matrix'] is None
assert data['edspt']['advisory_only'] is True
assert data['edspt']['selected_candidate_id'] == 'sensor:tank-2:precise'
assert data['ywir']['admitted'] is True and data['ywir']['advisory_token_cap'] == 16
paths = {}
out = pathlib.Path(config['output'])
for name, bundle in [('identified-original',original),('identified-replay',fresh)]:
    paths[name] = str(design.save_session(bundle,out/name))
receipt = out / 'identified-replay-receipt.json'
receipt.write_bytes(canonical(replay['replay_receipt']))
paths['identified-replay-receipt'] = str(receipt)
bim_source = runpy.run_path(str(root / 'bim-quantity/make_source.py'))['source']()
quantity = bim.create_session(canonical(bim_source), {'cse':providers['cse']})
quantity_replay = bim.replay_session(quantity, {'cse':providers['cse']})
for name,bundle in [('bim-original',quantity),('bim-replay',quantity_replay['session'])]:
    path = out / (name+'.json'); path.write_bytes(canonical(bundle)); paths[name]=str(path)
assert quantity['verification']['independent'] is False
assert quantity['steps'][0]['result']['data']['geometry_authority'] == 'QUANTITY_ONLY'
assert quantity['steps'][0]['numerical_result_id'] == quantity_replay['session']['steps'][0]['numerical_result_id']
assert quantity['steps'][0]['execution_id'] != quantity_replay['session']['steps'][0]['execution_id']
report = {'independent_verification':False,'admission':'not_performed','acquisition':'not_performed',
 'numerical_replay_equal':True,'identified_runtimes':original['runtimes'],'bim_runtimes':quantity['runtimes'],
 'identified_inspection':design.inspect_session(original),'fresh_occurrence_identities':True,
 'artifacts':{name:{'path':path,'sha256':sha256(pathlib.Path(path).read_bytes()).hexdigest()} for name,path in paths.items()}}
pathlib.Path(config['report']).write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
'''


def _qualify(args, report: dict, output: Path, log: Path) -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("Public SRA, YWIR, BIM and FlowState require Python 3.12 or newer")
    report["imports"] = verify_imports(ROOT)
    all_modules = {module["role"]: module for module in load_manifest(ROOT)["modules"]}
    if not RUNTIME_ROLES <= set(all_modules):
        raise ValueError("All public composition/identified-design provider imports must be registered")
    report["sources"] = {role: all_modules[role] for role in sorted(PACKAGE_ROLES | {"scr"})}
    with tempfile.TemporaryDirectory(prefix="notations-operations-") as directory, ExitStack() as stack:
        temporary = Path(directory)
        sources = stack.enter_context(provider_worktrees(ROOT, revisions="import", roles=sorted(PACKAGE_ROLES | {"scr"})))
        providers = stack.enter_context(provider_worktrees(ROOT, revisions="runtime", roles=sorted(RUNTIME_ROLES)))
        parents = {path.parent for path in providers.values()}
        if len(parents) != 1:
            raise ValueError("Original integration suites require sibling role checkouts")
        provider_root, = parents
        flowstate = _flowstate(args.flowstate_root, temporary, log)
        providers["fsrt"] = stack.enter_context(_worktree(flowstate, FLOWSTATE_REVISION,
                                                  provider_root / "fsrt", temporary, log))
        python = _python(temporary / "environment", temporary, log)
        root_source = temporary / "terminal-wheel-source"
        root_source.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copyfile(ROOT / name, root_source / name)
        shutil.copytree(ROOT / "src", root_source / "src")
        wheels = {"ciw": _wheel(python, root_source, temporary / "terminal-wheel", log)}
        suites = {}
        for role in ("sra", "cse"):
            source = temporary / (role + "-wheel-source")
            _copy_source(sources[role], source)
            wheels[all_modules[role]["python_import"]] = _wheel(python, source, temporary / (role + "-wheel"), log)
            suite = temporary / (role + "-suite")
            _copy_source(sources[role], suite)
            suites[role] = suite
        _run([python, "-I", "-m", "pip", "install", "--no-deps", *[wheel["path"] for wheel in wheels.values()]],
             cwd=temporary, log=log)
        expected = {name:{key:wheel[key] for key in ("distribution","version")} for name,wheel in wheels.items()}
        report["installed_packages"] = json.loads(_run_bound([python,"-I","-c",_IMPORT_PROBE,json.dumps(expected)],
                                                             cwd=temporary,log=log))
        report["wheels"] = {name:{key:value for key,value in wheel.items() if key != "path"}
                             for name,wheel in wheels.items()}
        report["provider_tests"] = {}
        report["provider_examples"] = {}
        # SRA's unchanged tests import their sibling conftest directly; retain
        # original prepend mode and the declared non-live test partition.
        report["provider_tests"]["sra"] = _test(python,suites["sra"],output,"sra",log)
        report["sra_live_kernels_qualified"] = False
        report["sra_test_partition"] = "Original pyproject.toml selection: -m 'not live'"
        examples = {}
        for name in ("quickstart.py", "unknown_plant.py", "field_record.py"):
            path = suites["sra"] / "examples" / name
            raw = _run_bound([python,"-I",path],cwd=suites["sra"],log=log)
            target = output / ("sra-"+path.stem+".txt"); target.write_text(raw)
            examples[name] = {"source_sha256":sha256(path.read_bytes()).hexdigest(),
                              "output":str(target),"output_sha256":sha256(target.read_bytes()).hexdigest()}
        report["provider_examples"]["sra"] = examples
        # BIM's original artifact-path tests assert repository-relative source
        # layout, which its wheel deliberately does not package. Keep that
        # original source suite separate from genuine installed-wheel execution.
        junit = output / "cse-tests.xml"
        _run_bound([python,"-m","pytest","-q","--junitxml",junit,"tests"],
                   cwd=suites["cse"],log=log,timeout=1800)
        report["provider_tests"]["cse"] = _bim_junit(junit)
        report["bim_suite_scope"] = ("Unchanged original repository suite copied outside the monorepo, "
            "using its original source-root behavior. Its artifact-path tests require repository validation files. "
            "Independent wheel identity and packaged milestone execution are checked separately. "
            "Optional skipped providers are unqualified.")
        raw = _run_bound([python,"-I","-m","gat.demo",output / "bim-wheel-demo"],
                         cwd=temporary,log=log)
        demo_log = output / "bim-wheel-demo.txt"; demo_log.write_text(raw)
        report["bim_installed_wheel_demo"] = {"module":"gat.demo","passed":True,
            "output":str(demo_log),"output_sha256":sha256(demo_log.read_bytes()).hexdigest(),
            "fixture_scope":"Synthetic packaged BIM milestone, invariants, rollback, IFC roundtrip and determinism"}
        report["budget"] = qualify_budget(python,sources["ywir"],providers["ywir"],temporary / "budget",output,log)

        # Public SDK tests are a separate configuration: the base suite above
        # already checked both SDK-absence refusals. PLSR is private. Public
        # JSPT's available source lacks sensitivity.grams_guest named by current
        # BIM's optional suite; installing it makes that unchanged check fail.
        # Keep the original companion skips and record the unresolved contract.
        sdk_python = _python(temporary / "bim-sdk-environment",temporary,log)
        sdk_dependencies = ["usd-core>=26.8,<27","cryptography>=40","ifcopenshell>=0.8"]
        _run([sdk_python,"-I","-m","pip","install",*sdk_dependencies],cwd=temporary,log=log)
        _run([sdk_python,"-I","-m","pip","install","--no-deps",wheels["gat"]["path"]],
             cwd=temporary,log=log)
        sdk_probe = r'''import importlib.metadata,json,sys
from pathlib import Path
import gat,pxr,ifcopenshell
prefix=Path(sys.prefix).resolve()
modules={name:module for name,module in [('gat',gat),('pxr',pxr),('ifcopenshell',ifcopenshell)]}
assert all(Path(module.__file__).resolve().is_relative_to(prefix) for module in modules.values())
print(json.dumps({'modules':{name:str(Path(module.__file__).resolve()) for name,module in modules.items()},
 'distributions':{name:importlib.metadata.version(name) for name in ['gat-bim','usd-core','cryptography','ifcopenshell']}}))'''
        sdk_packages = json.loads(_run_bound([sdk_python,"-I","-c",sdk_probe],cwd=temporary,log=log))
        corpus = temporary / "public-bim-ifc-corpus"
        _run_bound([sdk_python,"-I",suites["cse"] / "validation/fetch_ifc_corpus.py",corpus],
                   cwd=temporary,log=log,timeout=600)
        corpus_manifest = suites["cse"] / "validation/ifc-corpus-v1.json"
        corpus_records = []
        for model in json.loads(corpus_manifest.read_text())["models"]:
            if not model["ci"]:
                continue
            payload = (corpus / model["destination"]).read_bytes()
            if len(payload) != model["size_bytes"] or sha256(payload).hexdigest() != model["sha256"]:
                raise AssertionError("Original public IFC fixture bytes changed after fetch validation")
            corpus_records.append({key:model[key] for key in ("id","url","sha256","size_bytes")})
        sdk_junit = output / "cse-public-sdk-tests.xml"
        _run_bound([sdk_python,"-m","pytest","-q","--junitxml",sdk_junit,"tests"],
                   cwd=suites["cse"],log=log,timeout=1800,bindings={"GAT_IFC_VALIDATION_ROOT":corpus})
        report["bim_public_sdk_tests"] = {**_bim_junit(sdk_junit,sdk=True),
            "installed_packages":sdk_packages,"dependencies":sdk_dependencies,
            "jspt_companion_qualified":False,
            "unresolved_public_companion_contract":"Current BIM optional suite expects sensitivity.grams_guest absent from available public JSPT source; combined installation remains unqualified",
            "private_plsr_qualified":False,"public_ifc_corpus_qualified":True,
            "public_ifc_corpus":corpus_records,"corpus_manifest_sha256":sha256(corpus_manifest.read_bytes()).hexdigest(),
            "corpus_scope":"Original commit-pinned public IFC compatibility fixtures and scoped lowerings; no physical validation or construction approval"}
        report["bim_implemented_companion_tests"] = _bim_companion(sdk_python,suites["cse"],temporary,output,log)

        compute = temporary / "compute-source"
        _copy_source(sources["scr"],compute)
        release_python = _python(temporary / "release-environment",temporary,log,numerical=False)
        report["compute_releases"] = {}
        for name, module in (("provenance_pool","evidence"),("canonical_state","core")):
            derived = temporary / (name+"-derived")
            _run_bound([release_python,"-I",compute / "release" / name / "build.py","--out",derived],
                       cwd=compute,log=log,timeout=1800)
            wheel = _wheel(release_python,derived,temporary / (name+"-wheel"),log)
            _run([release_python,"-I","-m","pip","install","--no-deps",wheel["path"]],cwd=temporary,log=log)
            probe = json.loads(_run_bound([release_python,"-I","-c",_IMPORT_PROBE,
                json.dumps({module:{key:wheel[key] for key in ("distribution","version")}})],cwd=temporary,log=log))
            report["compute_releases"][name] = {"wheel":{key:value for key,value in wheel.items() if key != "path"},
                                                  "installed_package":probe,"original_deriver_passed":True}
        cargo = str(args.cargo.expanduser().absolute()) if args.cargo else shutil.which("cargo")
        if cargo is None:
            raise RuntimeError("The bounded original SCR CPU gate requires Cargo/Rust; native tests cannot be skipped")
        report["cargo_version"] = _run_bound([cargo,"--version"],cwd=temporary,log=log).strip()
        manifest = providers["scr"] / "crates/Cargo.toml"
        target = temporary / "rust-target"
        cargo_tests = _run_bound([cargo,"test","--locked","--offline","--manifest-path",manifest,"--target-dir",target],
                                 cwd=temporary,log=log,timeout=900)
        summaries = [tuple(map(int,row)) for row in re.findall(
            r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;",cargo_tests)]
        if not summaries or sum(row[0] for row in summaries) < 1 or any(sum(row[1:]) for row in summaries):
            raise AssertionError("Original bounded Rust workspace tests must be nonempty with no failures or ignored tests")
        _run_bound([cargo,"build","--release","--locked","--offline","--manifest-path",manifest,
                    "--target-dir",target,"-p","execution-cli"],cwd=temporary,log=log,timeout=900)
        engine = target / "release" / ("execution-cli.exe" if os.name == "nt" else "execution-cli")
        report["cpu_engine"] = {"source_revision":all_modules["scr"]["runtime_revision"],
                               "binary_sha256":sha256(engine.read_bytes()).hexdigest(),
                               "rust_workspace_tests_passed":True,"native_host_workers_qualified":False,
                               "gpu_or_prover_qualified":False,
                               "original_rust_tests":sum(row[0] for row in summaries),"ignored_tests":0}
        suite = temporary / "terminal-integrations"
        (suite / "tests").mkdir(parents=True)
        for name in ("test_declared_workloads.py","test_bim_quantity.py","test_identified_design.py",
                     "test_spatial_view.py","test_spatial_transport.py"):
            shutil.copyfile(ROOT / "tests" / name,suite / "tests" / name)
        shutil.copytree(ROOT / "examples",suite / "examples")
        bindings = {"CIW_DECLARED_STACK_ROOT":provider_root,"CIW_SCR_ENGINE":engine,
                    "CIW_CSE_REPO":providers["cse"],"CIW_IDENTIFIED_DESIGN_STACK_ROOT":provider_root,
                    "CIW_CALIBRATED_STACK_ROOT":provider_root,"CIW_DECLARED_FIXTURE_DIR":output / "declared-sessions"}
        report["terminal_integration_tests"] = _test(python,suite,output,"terminal-operations",log,
             extra=("--import-mode=importlib",),bindings=bindings,timeout=2400)
        report["unchanged_integration_suites"] = {name:sha256((ROOT / "tests" / name).read_bytes()).hexdigest()
             for name in ("test_declared_workloads.py","test_bim_quantity.py","test_identified_design.py",
                          "test_spatial_view.py","test_spatial_transport.py")}
        sessions = output / ("sessions-"+report["verification_id"].split(":",1)[1]); sessions.mkdir()
        config = temporary / "snapshot.json"
        snapshot = output / "composition.json"
        config.write_text(json.dumps({"providers":{role:str(path) for role,path in providers.items()},
                  "fixtures":str(suite / "examples"),"output":str(sessions),"report":str(snapshot)}))
        _run_bound([python,"-I","-c",_SNAPSHOT,config],cwd=temporary,log=log,timeout=900)
        report["composition"] = json.loads(snapshot.read_text())
        report["post_execution_imports"] = verify_imports(ROOT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flowstate-root",type=Path,help="Standalone public FlowState history containing the exact NET pin")
    parser.add_argument("--cargo",type=Path,help="Explicit trusted Cargo executable for the original bounded CPU build")
    parser.add_argument("--output-dir",type=Path,default=ROOT / "results/monorepo-operations")
    args = parser.parse_args(argv)
    output = args.output_dir.expanduser().resolve(); output.mkdir(parents=True,exist_ok=True)
    log = output / "commands.log"; log.write_text("")
    report = {"schema":"notations.monorepo-operations-gate.v1","status":"running",
              "verification_id":"verification:"+uuid.uuid4().hex,"created_at":datetime.now(timezone.utc).isoformat(),
              "terminal_revision":_git(ROOT,"rev-parse","HEAD"),"python_version":sys.version,
              "minimum_python":"3.12","claim_scope":SCOPE,"independent_verification":False,
              "admission":"not_performed","dependencies":DEPENDENCIES,"log":str(log)}
    try:
        report["terminal_source"] = verify_terminal_source(root=ROOT)
        if report["terminal_source"]["revision"] != report["terminal_revision"]:
            raise ValueError("Terminal revision changed before qualification")
        _qualify(args,report,output,log)
        if verify_terminal_source(root=ROOT) != report["terminal_source"]:
            raise ValueError("Terminal source identity changed during qualification")
    except Exception as error:
        report.update(status="failed",error={"type":type(error).__name__,"message":str(error)})
        print("Public operations gate failed:",error,file=sys.stderr)
    else:
        report["status"] = "passed"
        print("Public operations gate passed: independent packages, bounded CPU execution and exact eleven-provider replay")
    finally:
        (output / "report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
