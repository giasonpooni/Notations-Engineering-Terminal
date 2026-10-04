#!/usr/bin/env python3
"""Fail-closed native acceptance gate: real Julia export -> C -> Python and Rust.

Requires a provisioned NET checkout and explicit compiler/runtime paths. A
hand-lowered fixture is NEVER substituted here. Reports are build/test artifacts,
not a new session format or a substitute for CIW execution/result records.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
from ciw.adapters.oscillator_kernel import (OscillatorKernel, OPERATION, file_digest, operation)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def identity(value):
    return "sha256:" + sha256(canonical(value)).hexdigest()


def executable(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or not os.access(path,os.X_OK):
        raise ValueError("bind an existing absolute executable path: " + str(path))
    return path.resolve()


def run_command(command, directory, report, *, stdin=None, timeout=120):
    index = len(report["commands"])
    started = time.perf_counter()
    entry = {"argv":list(map(str,command)),"status":"failed","elapsed_s":None}
    report["commands"].append(entry)
    try:
        completed = subprocess.run(entry["argv"],input=stdin,capture_output=True,
            timeout=timeout,cwd=directory,check=False,
            env={**os.environ,"JULIA_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1"})
        for suffix,data in (("stdout",completed.stdout),("stderr",completed.stderr)):
            target = directory / f"command-{index:02d}.{suffix}"
            target.write_bytes(data)
            entry[suffix] = {"file":target.name,"sha256":file_digest(target)}
        entry["exit_code"] = completed.returncode
        if completed.returncode:
            raise RuntimeError(f"command {index} failed; see retained stdout/stderr")
        entry["status"] = "completed"
        return completed.stdout
    finally:
        entry["elapsed_s"] = time.perf_counter() - started


def read_rows(path, expected_rows):
    raw = path.read_bytes()
    if len(raw) > 1024*1024:
        raise ValueError("probe response exceeds size limit")
    lines = raw.decode("ascii").splitlines()
    if len(lines) != expected_rows:
        raise ValueError("probe response has wrong row count")
    result=[]
    for line in lines:
        fields=line.split("\t")
        if len(fields)!=2: raise ValueError("probe response has wrong width")
        values=list(map(float,fields))
        if not all(map(math.isfinite,values)): raise ValueError("nonfinite probe response")
        result.append(values)
    return result


def compare(actual, reference, *, atol=2e-12, rtol=2e-13):
    if len(actual)!=len(reference) or not actual:
        raise ValueError("comparison requires equal nonempty samples")
    maxima=[0.0]*len(actual[0])
    for left,right in zip(actual,reference):
        if len(left)!=len(right) or len(left)!=len(maxima): raise ValueError("comparison shape mismatch")
        for i,(a,b) in enumerate(zip(left,right)):
            if not math.isfinite(a) or not math.isfinite(b): raise ValueError("nonfinite comparison")
            error=abs(a-b)
            maxima[i]=max(maxima[i],error)
            if error > atol+rtol*abs(b):
                raise ValueError(f"comparison tolerance exceeded at component {i}: {error}")
    return {"outcome":"passed","rows":len(actual),"max_abs_error":maxima,"atol":atol,"rtol":rtol}


def integrate(kernel, source, *, steps_per_radian=512):
    """Host-owned test integrator. The export is RHS only, not an exported solver."""
    if type(steps_per_radian) is not int or not 1 <= steps_per_radian <= 4096:
        raise ValueError("invalid test-integrator resolution")
    model=source["model"]
    p=[model["gamma_s_inv"],model["omega_0_rad_s"]]
    state=[source["initial_state"]["q0_m"],source["initial_state"]["v0_m_s"]]
    times=source["time_s"]
    q,v=[state[0]],[state[1]]
    steps=0
    for left,right in zip(times,times[1:]):
        n=max(1,math.ceil((right-left)*max(1.0,p[1])*steps_per_radian))
        h=(right-left)/n
        for _ in range(n):
            k1=kernel.rhs(state,p)
            k2=kernel.rhs([state[i]+0.5*h*k1[i] for i in range(2)],p)
            k3=kernel.rhs([state[i]+0.5*h*k2[i] for i in range(2)],p)
            k4=kernel.rhs([state[i]+h*k3[i] for i in range(2)],p)
            state=[state[i]+h*(k1[i]+2*k2[i]+2*k3[i]+k4[i])/6 for i in range(2)]
        steps+=n; q.append(state[0]); v.append(state[1])
    energy=[0.5*model["mass_kg"]*(b*b+p[1]*p[1]*a*a) for a,b in zip(q,v)]
    return {"time_s":times,"q_m":q,"v_m_s":v,"energy_j":energy},steps


def trajectory_rows(trace):
    return list(map(list,zip(trace["q_m"],trace["v_m_s"],trace["energy_j"])))


def execute_gate(args, destination, report):
    if platform.system() != "Linux":
        raise ValueError("this first native build gate is Linux-only; other targets are unqualified")
    tools={name:executable(getattr(args,name)) for name in ("julia","cc","rustc")}
    for name,path in tools.items():
        version=run_command([path,"--version"],destination,report).decode().strip()
        report["tools"][name]={"executable_sha256":file_digest(path),"version":version}
    if report["tools"]["julia"]["version"].lower() != f"julia version {args.julia_version}":
        raise ValueError("Julia version differs from explicitly selected build profile")
    import inspect
    report["host_sources"]={"gate_sha256":file_digest(Path(__file__)),
        "adapter_sha256":file_digest(Path(inspect.getfile(OscillatorKernel)))}
    snapshot=destination/"source"
    snapshot.mkdir()
    for name in ("oscillator.jl","export.jl","abi.c.in","rust_binding.rs"):
        shutil.copyfile(ROOT/"runtimes/julia-oscillator-kernel"/name,snapshot/name)
    source_sha=file_digest(snapshot/"oscillator.jl")
    report["sources"]={path.name:file_digest(path) for path in sorted(snapshot.iterdir())}
    jl=[tools["julia"],"--startup-file=no","--history-file=no","--project=@stdlib","--threads=1",snapshot/"export.jl"]
    run_command(jl+["selftest"],destination,report)
    run_command(jl+["export",snapshot/"oscillator.jl",destination/"oscillator.c"],destination,report)
    report["gates"]["julia_export"]={"outcome":"passed","source_sha256":source_sha}
    flags=["-std=c11","-O2","-Wall","-Wextra","-Werror","-pedantic","-fno-fast-math",
           "-ffp-contract=off","-shared","-fPIC"]
    library=destination/"libciw_oscillator_kernel.so"
    target=run_command([tools["cc"],"-dumpmachine"],destination,report).decode().strip()
    run_command([tools["cc"],*flags,destination/"oscillator.c","-o",library,"-lm"],destination,report)
    build={"build_id":"build-"+uuid.uuid4().hex,"sources":report["sources"],
           "tools":report["tools"],"host_sources":report["host_sources"],"target":target,"flags":flags,
           "generated_c_sha256":file_digest(destination/"oscillator.c"),
           "library_sha256":file_digest(library),"model_source_sha256":source_sha}
    build["specification_digest"]=identity({k:v for k,v in build.items() if k!="build_id"})
    (destination/"build.json").write_bytes(canonical(build))
    kernel=OscillatorKernel(library,library_sha256=build["library_sha256"],source_sha256=source_sha)
    rust_flags=["--edition=2021","-L",f"native={destination}","-C",f"link-arg=-Wl,-rpath,{destination}"]
    run_command([tools["rustc"],*rust_flags,snapshot/"rust_binding.rs","-o",destination/"rust-probe"],destination,report)
    run_command([tools["rustc"],*rust_flags,"--test",snapshot/"rust_binding.rs","-o",destination/"rust-tests"],destination,report)
    run_command([destination/"rust-tests"],destination,report)
    rng=random.Random(7021)
    cases=[[1.0,-2.0,0.25,2.0],[2.0,3.0,0.0,2.0],[0.0,0.0,0.0,1.0]]
    for _ in range(1021):
        omega=rng.uniform(0.01,20)
        cases.append([rng.uniform(-1000,1000),rng.uniform(-1000,1000),rng.uniform(0,0.5*omega),omega])
    raw=("\n".join("\t".join(map(repr,row)) for row in cases)+"\n").encode("ascii")
    (destination/"cases.tsv").write_bytes(raw)
    run_command(jl+["reference",snapshot/"oscillator.jl",destination/"cases.tsv",destination/"julia.tsv"],destination,report)
    rust=run_command([destination/"rust-probe",source_sha[7:]],destination,report,stdin=raw)
    (destination/"rust.tsv").write_bytes(rust)
    py=[kernel.rhs(row[:2],row[2:]) for row in cases]
    (destination/"python.json").write_bytes(canonical(py))
    reference=read_rows(destination/"julia.tsv",len(cases))
    report["gates"]["python_vs_julia"]=compare(py,reference)
    report["gates"]["rust_vs_julia"]=compare(read_rows(destination/"rust.tsv",len(cases)),reference)
    from ciw import julia_oscillator as retained_julia
    report["host_sources"]["existing_julia_provider_sha256"]=file_digest(Path(retained_julia.__file__))
    fixtures=[]
    for name,q0,v0,gamma,omega,count in (("default",1.0,0.0,0.15,2*math.pi*0.8,768),
            ("mixed",-0.7,2.0,0.2,3.0,128),("undamped",1.0,-0.4,0.0,2.0,128),
            ("high-frequency",0.5,1.0,2.0,20.0,128)):
        source={"schema":retained_julia.SOURCE_SCHEMA,"operation_id":retained_julia.OPERATION,
            "experiment_id":"kernel-"+name,"claim_scope":retained_julia.AUTHORITY["claim_scope"],
            "model":{"gamma_s_inv":gamma,"omega_0_rad_s":omega,"mass_kg":1.0},
            "initial_state":{"q0_m":q0,"v0_m_s":v0},"time_s":[i/64 for i in range(count)],
            "solver":{"abstol":1e-10,"reltol":1e-10,"maxiters":1000000}}
        retained_julia.validate_source(canonical(source))
        trace,steps=integrate(kernel,source)
        oracle=retained_julia.analytic_oracle(source)
        comparison=compare(trajectory_rows(trace),trajectory_rows(oracle),atol=2e-8,rtol=2e-8)
        comparison.update(name=name,rk4_steps=steps)
        fixtures.append(comparison)
        (destination/f"{name}-trajectory.json").write_bytes(canonical({"source":source,"output":trace,"oracle":oracle}))
    report["gates"]["existing_analytic_oracle"]={"outcome":"passed","fixtures":fixtures}
    # Existing envelopes and schema dispatch, not a new kernel session manager.
    from ciw.adapters.protocol import InstrumentManifest
    from ciw.core.identities import evidence_id
    from ciw.operations.registry import OperationRegistry
    from ciw.operations.runner import execute, validate_execution
    from ciw.operations.schemas import validate_payload
    registry=OperationRegistry(); registry.register(operation(kernel))
    manifest = InstrumentManifest("oscillator-kernel-rk4-fixture.v1", role="record_only",
                                  units={"q": "m", "v": "m/s"}, frames=("oscillator-state",))
    run={"run_id":"run-"+uuid.uuid4().hex,
         "instrument":"oscillator-kernel-rk4-fixture.v1","time_s":trace["time_s"],
         "metadata":{"duration_s":trace["time_s"][-1]+1/64,"sample_count":len(trace["time_s"]),
                     "coordinate_frame":"oscillator-state","manifest":manifest.to_dict(),
                     "provenance":{"source":"native-kernel RK4 test trajectory"}},
         "channels":{"q":{"unit":"m","values":trace["q_m"]},"v":{"unit":"m/s","values":trace["v_m_s"]}}}
    run["evidence_id"] = evidence_id(run)
    selection={"revision":0,"channel":"q","interval_s":[0.0,run["metadata"]["duration_s"]]}
    parameters={"gamma_s_inv":source["model"]["gamma_s_inv"],"omega_0_rad_s":source["model"]["omega_0_rad_s"],
                "channel":selection["channel"],"interval_s":selection["interval_s"]}
    first,result=execute(registry,run,selection,"native-fixture.json",OPERATION,parameters)
    second,repeat=execute(registry,run,selection,"native-fixture.json",OPERATION,parameters)
    if result is None or repeat is None: raise ValueError("CIW operation was refused")
    if first["execution_id"]==second["execution_id"] or result["data"]!=repeat["data"]:
        raise ValueError("fresh occurrence or repeat comparison failed")
    validate_execution(first,run,0,{result["result_id"]:result})
    # Deny loading native code while the saved payload is validated.
    import ctypes
    original=ctypes.CDLL
    def denied(*a,**kw): raise AssertionError("offline validation attempted native load")
    ctypes.CDLL=denied
    try: validate_payload(OPERATION,result["data"],run,parameters,selection)
    finally: ctypes.CDLL=original
    (destination/"ciw-records.json").write_bytes(canonical({"run":run,"selection":selection,
        "executions":[first,second],"results":[result,repeat]}))
    report["gates"]["ciw_retention"]={"outcome":"passed","verification_status":result["verification_status"]}
    if args.julia_session is None:
        report["gates"]["retained_tsit5"]={"outcome":"not_run","reason":"supply a retained Julia oscillator session"}
        return False
    reference_path=Path(args.julia_session).resolve(strict=True)
    if reference_path.stat().st_size > retained_julia.MAX_BYTES: raise ValueError("retained Julia bundle exceeds bounds")
    raw_session=reference_path.read_bytes()
    bundle=json.loads(raw_session)
    raw_source=retained_julia.workflow._validate(bundle)
    retained_source=retained_julia.validate_source(raw_source)
    baseline=bundle["steps"][0]["result"]["data"]["output"]
    trace,steps=integrate(kernel,retained_source)
    result=compare(trajectory_rows(trace),trajectory_rows(baseline),atol=5e-8,rtol=5e-8)
    result.update(reference_session_sha256=file_digest(reference_path),
                  reference_execution_id=bundle["steps"][0]["execution_id"],rk4_steps=steps,
                  scope="comparison to supplied validated retained bytes; not a fresh Tsit5 execution")
    (destination/"retained-julia-session.json").write_bytes(raw_session)
    (destination/"retained-comparison-trajectory.json").write_bytes(canonical(trace))
    report["gates"]["retained_tsit5"]=result
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("julia","cc","rustc"):
        parser.add_argument("--"+name,required=True,help="explicit absolute executable path")
    parser.add_argument("--julia-version",default="1.10.12",help="explicit build-profile version; existing provider pin is unchanged")
    parser.add_argument("--julia-session",help="retained original Julia Tsit5 session; required for complete qualification")
    parser.add_argument("--output-dir",required=True,type=Path)
    args=parser.parse_args()
    destination=args.output_dir.expanduser().resolve()
    destination.mkdir(parents=True,exist_ok=False)
    report={"status":"failed","occurrence_id":"kernel-gate-"+uuid.uuid4().hex,"commands":[],"tools":{},
            "platform":platform.platform(),"gates":{name:{"outcome":"not_run"} for name in
                ("julia_export","python_vs_julia","rust_vs_julia","existing_analytic_oracle","ciw_retention","retained_tsit5")},
            "scope":"software build/interface/numerical comparisons; not physical validation or formal proof",
            "admission":"not_performed"}
    try:
        complete=execute_gate(args,destination,report)
        report["status"]="passed" if complete else "incomplete"
    except Exception as error:
        report["failure"]={"type":type(error).__name__,"message":str(error)}
    report["artifact_digests"]={p.relative_to(destination).as_posix():file_digest(p)
        for p in sorted(destination.rglob("*")) if p.is_file()}
    report["report_digest"]=identity(report)
    (destination/"gate-report.json").write_bytes(canonical(report))
    print(json.dumps(report,indent=2))
    return 0 if report["status"]=="passed" else 1

if __name__=="__main__":
    raise SystemExit(main())
