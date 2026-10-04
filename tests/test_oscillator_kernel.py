"""C ABI/host tests using a DECLARED HAND-LOWERED fixture, not Julia export evidence.

The mandatory Julia -> C -> Python/Rust gate is scripts/check_oscillator_kernel.py.
These tests never mark absent Julia or Rust execution as a successful native gate.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import ctypes
from decimal import Decimal, localcontext
import importlib.util
import math
from pathlib import Path
import random
import shutil
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
from ciw.adapters import oscillator_kernel as k

@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    if sys.platform != "linux":
        pytest.skip("this first C ABI fixture gate is Linux-only")
    cc = shutil.which("gcc")
    if cc is None:
        pytest.skip("GCC unavailable; C ABI fixture tests not exercised")
    directory = tmp_path_factory.mktemp("oscillator-c-fixture")
    template = (ROOT / "runtimes/julia-oscillator-kernel/abi.c.in").read_text()
    source_digest = k.file_digest(ROOT / "runtimes/julia-oscillator-kernel/oscillator.jl")
    # Deliberately hand-lowered test double; it does NOT establish Julia generation.
    c = template.replace("@SOURCE_SHA256@",source_digest[7:]).replace("@RHS_Q@","v")
    c = c.replace("@RHS_V@","(((-2.0 * gamma) * v) - ((omega * omega) * q))")
    source = directory / "fixture.c"
    c += "\nint fixture_get_rounding(void) { return fegetround(); }\n"
    c += "int fixture_downward(void) { return FE_DOWNWARD; }\n"
    c += "int fixture_set_rounding(int value) { return fesetround(value); }\n"
    source.write_text(c)
    library = directory / "libfixture.so"
    subprocess.run([cc,"-std=c11","-O2","-Wall","-Wextra","-Werror","-pedantic",
                    "-fno-fast-math","-ffp-contract=off","-shared","-fPIC",str(source),
                    "-o",str(library),"-lm"],check=True,capture_output=True,timeout=30)
    return k.OscillatorKernel(library, library_sha256=k.file_digest(library), source_sha256=source_digest)

@pytest.mark.parametrize("state,parameters,expected", [
    ([1.0,-2.0],[0.25,2.0],[-2.0,-3.0]),
    ([2.0,3.0],[0.0,2.0],[3.0,-8.0]),
    ([0.0,0.0],[0.0,1.0],[0.0,0.0]),
    ([-1.0,2.0],[0.5,1.0],[2.0,-1.0]),
    ([1e6,-1e6],[10.0,20.0],[-1e6,-380e6]),
])
def test_known_values(kernel,state,parameters,expected):
    assert kernel.rhs(state,parameters) == expected

@pytest.mark.parametrize("state,parameters", [
    ([True,0.0],[0.0,1.0]), (["1",0.0],[0.0,1.0]),
    ([float("nan"),0.0],[0.0,1.0]), ([0.0,float("inf")],[0.0,1.0]),
    ([0.0,0.0],[float("inf"),1.0]), ([0.0,0.0],[0.0,float("nan")]),
    ([0.0],[0.0,1.0]), ([0.0,0.0],[0.0]),
    ([0.0,0.0],[0.0,0.0]), ([0.0,0.0],[-0.1,1.0]),
    ([0.0,0.0],[0.6,1.0]), ([0.0,0.0],[0.0,21.0]),
    ([1e7,0.0],[0.0,1.0]), ([0.0,1e7],[0.0,1.0]),
    ([0.0,0.0],[False,1.0]), ([0.0,0.0],[0.0,True]),
])
def test_python_refusal(kernel,state,parameters):
    with pytest.raises(ValueError): kernel.rhs(state,parameters)

@pytest.mark.parametrize("which,expected", [("null",1),("size",2),("nan",3),("domain",4)])
def test_c_refusal_does_not_write(kernel,which,expected):
    array = ctypes.c_double * 2
    state, params, output = array(1,2),array(0.1,1),array(17,23)
    ns = 2
    if which == "null": state = None
    if which == "size": ns = 1
    if which == "nan": params[0] = math.nan
    if which == "domain": params[0] = -1
    assert kernel._rhs(state,ns,params,2,output,2) == expected
    assert list(output) == [17,23]


def test_aliasing_supported(kernel):
    array = ctypes.c_double * 2
    state, p = array(1,-2),array(0.25,2)
    assert kernel._rhs(state,2,p,2,state,2) == 0
    assert list(state) == [-2,-3]


def test_1000_cases_against_decimal(kernel):
    rng = random.Random(7021)
    with localcontext() as context:
        context.prec = 80
        for _ in range(1000):
            q,v = [rng.uniform(-1000,1000) for _ in range(2)]
            omega = rng.uniform(0.01,20)
            gamma = rng.uniform(0,0.5*omega)
            d = list(map(Decimal.from_float,[q,v,gamma,omega]))
            reference = float(-2*d[2]*d[1]-d[3]*d[3]*d[0])
            result = kernel.rhs([q,v],[gamma,omega])
            assert result[0] == v
            assert math.isclose(result[1],reference,rel_tol=2e-14,abs_tol=2e-10)


def test_concurrent_and_interleaved_calls(kernel):
    a = ([1.0,2.0],[0.2,3.0])
    b = ([-2.0,-1.0],[0.1,2.0])
    expected = [kernel.rhs(*args) for args in [a,b]*64]
    with ThreadPoolExecutor(max_workers=4) as executor:
        actual = list(executor.map(lambda args: kernel.rhs(*args), [a,b]*64))
    assert actual == expected


def test_identity_rejection_before_load(kernel):
    with pytest.raises(ValueError,match="digest mismatch"):
        k.OscillatorKernel(kernel._path,library_sha256="sha256:"+"0"*64,source_sha256=kernel.source_sha256)
    with pytest.raises(ValueError,match="source identity"):
        k.OscillatorKernel(kernel._path,library_sha256=kernel.library_sha256,source_sha256="sha256:"+"0"*64)


def example_run():
    from ciw.adapters.protocol import InstrumentManifest
    from ciw.core.identities import evidence_id
    manifest = InstrumentManifest("oscillator-kernel-test-fixture.v1", role="record_only",
                                  units={"q": "m", "v": "m/s"}, frames=("oscillator-state",))
    run = {"run_id":"run-kernel-fixture",
            "instrument":"oscillator-kernel-test-fixture.v1",
            "metadata":{"duration_s":1.0,"sample_count":4,"coordinate_frame":"oscillator-state",
                        "provenance":{"source":"synthetic test fixture"}, "manifest":manifest.to_dict()},"time_s":[0.0,0.25,0.5,0.75],
            "channels":{"q":{"unit":"m","values":[1.0,0.5,0.0,-0.5]},
                        "v":{"unit":"m/s","values":[0.0,-1.0,-2.0,-1.0]}}}
    run["evidence_id"] = evidence_id(run)
    return run


def example_parameters():
    return {"gamma_s_inv":0.25,"omega_0_rad_s":2.0,"channel":"q","interval_s":[0.0,0.5]}


def test_half_open_payload_offline(kernel,monkeypatch):
    run,p = example_run(),example_parameters()
    data = k.evaluate_run(kernel,run,p)
    assert data["sample_indices"] == [0,1]
    monkeypatch.setattr(ctypes,"CDLL",lambda *_: (_ for _ in ()).throw(AssertionError("native load")))
    k.validate_payload(k.OPERATION,data,run,p,p)
    assert data["verification"] == "not_verified"

@pytest.mark.parametrize("field,value", [
    ("time_s",[0.0,0.2]), ("sample_indices",[False,1]),
    ("dq_m_s",[0.0]), ("dv_m_s2",[math.nan,0]),
    ("library_sha256","bad"),("source_evidence_id","wrong"),
    ("verification","passed"),("origin","physical_measurement"),
])
def test_saved_payload_tampering(kernel,field,value):
    run,p = example_run(),example_parameters()
    data = k.evaluate_run(kernel,run,p)
    data[field] = value
    with pytest.raises(ValueError): k.validate_payload(k.OPERATION,data,run,p,p)


def test_wrong_units_and_unknown_parameter(kernel):
    run,p = example_run(),example_parameters()
    run["channels"]["q"]["unit"] = "cm"
    with pytest.raises(ValueError): k.evaluate_run(kernel,run,p)
    p["library"] = "/untrusted/path"
    with pytest.raises(ValueError): k.evaluate_run(kernel,example_run(),p)



def test_existing_runner_and_offline_restore(kernel,monkeypatch):
    from ciw.operations.registry import OperationRegistry
    from ciw.operations.runner import execute,validate_execution,check_seal
    from ciw.operations.schemas import validate_payload
    registry=OperationRegistry(); registry.register(k.operation(kernel))
    run,p=example_run(),example_parameters()
    original=deepcopy(run)
    selection={"revision":0,"channel":"q","interval_s":p["interval_s"]}
    first,a=execute(registry,run,selection,"fixture.json",k.OPERATION,p)
    second,b=execute(registry,run,selection,"fixture.json",k.OPERATION,p)
    assert first["status"]==second["status"]=="completed"
    assert a["data"]==b["data"] and a["result_id"]!=b["result_id"]
    assert first["execution_id"]!=second["execution_id"]
    assert a["verification_status"]=="not_verified" and a["verification_id"] is None
    assert run==original
    monkeypatch.setattr(ctypes,"CDLL",lambda *_: (_ for _ in ()).throw(AssertionError("native load")))
    validate_execution(first,run,0,{a["result_id"]:a})
    validate_payload(k.OPERATION,a["data"],run,p,selection)
    check_seal(a)
    bad=deepcopy(a); bad["data"]["dq_m_s"][0]+=1
    with pytest.raises(ValueError): check_seal(bad)


def test_existing_runner_retains_refusal(kernel):
    from ciw.operations.registry import OperationRegistry
    from ciw.operations.runner import execute,validate_execution
    registry=OperationRegistry(); registry.register(k.operation(kernel))
    run,p=example_run(),example_parameters()
    p["gamma_s_inv"]=2.0
    selection={"revision":0,"channel":"q","interval_s":p["interval_s"]}
    attempt,result=execute(registry,run,selection,"fixture.json",k.OPERATION,p)
    assert attempt["status"]=="refused" and result is None
    validate_execution(attempt,run,0,{})


def test_role_and_schema_cannot_be_rebound():
    from ciw.operations.schemas import validate_role,register_payload_validator
    validate_role(k.OPERATION,"backend")
    with pytest.raises(ValueError): validate_role(k.OPERATION,"analysis")
    with pytest.raises(ValueError): register_payload_validator(k.OPERATION,lambda *args:None)


def gate_module():
    spec=importlib.util.spec_from_file_location("kernel_gate_for_test",ROOT/"scripts/check_oscillator_kernel.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.mark.parametrize("q0,v0,gamma,omega",[(1.0,0.0,0.15,5.0),(-0.7,2.0,0.2,3.0),
                                              (1.0,-0.4,0.0,2.0),(0.5,1.0,2.0,20.0)])
def test_trajectory_against_independent_matrix_exponential(kernel,q0,v0,gamma,omega):
    import numpy as np
    source={"model":{"gamma_s_inv":gamma,"omega_0_rad_s":omega,"mass_kg":1.0},
            "initial_state":{"q0_m":q0,"v0_m_s":v0},"time_s":[i/64 for i in range(128)]}
    actual,steps=gate_module().integrate(kernel,source)
    matrix=np.array([[0.0,1.0],[-omega*omega,-2*gamma]])
    eigenvalues,vectors=np.linalg.eig(matrix)
    coefficients=np.linalg.solve(vectors,np.array([q0,v0]))
    reference=(np.exp(np.outer(source["time_s"],eigenvalues))*coefficients) @ vectors.T
    assert np.max(np.abs(reference.imag)) < 1e-12
    measured=np.column_stack((actual["q_m"],actual["v_m_s"]))
    np.testing.assert_allclose(measured,reference.real,atol=2e-8,rtol=2e-8)
    assert steps>0
    if gamma==0.0:
        assert max(abs(e-actual["energy_j"][0]) for e in actual["energy_j"]) < 1e-8


def test_rk4_timestep_convergence(kernel):
    source={"model":{"gamma_s_inv":0.0,"omega_0_rad_s":2.0,"mass_kg":1.0},
            "initial_state":{"q0_m":1.0,"v0_m_s":0.0},"time_s":[i/8 for i in range(49)]}
    gate=gate_module()
    a,_=gate.integrate(kernel,source,steps_per_radian=8)
    b,_=gate.integrate(kernel,source,steps_per_radian=16)
    reference=math.cos(2.0*source["time_s"][-1])
    ratio=abs(a["q_m"][-1]-reference)/abs(b["q_m"][-1]-reference)
    assert 12 < ratio < 20


def test_overflow_integer_and_wrong_frame(kernel):
    with pytest.raises(ValueError): kernel.rhs([10**400,0.0],[0.0,1.0])
    run=example_run(); run["metadata"]["coordinate_frame"]="unrelated"
    with pytest.raises(ValueError): k.evaluate_run(kernel,run,example_parameters())



def test_rounding_mode_refused_without_writes(kernel):
    get=kernel._library.fixture_get_rounding; get.argtypes=[]; get.restype=ctypes.c_int
    down=kernel._library.fixture_downward; down.argtypes=[]; down.restype=ctypes.c_int
    set_mode=kernel._library.fixture_set_rounding; set_mode.argtypes=[ctypes.c_int]; set_mode.restype=ctypes.c_int
    previous=get()
    array=ctypes.c_double*2
    out=array(17,23)
    try:
        assert set_mode(down())==0
        code=kernel._rhs(array(1,2),2,array(0.1,1),2,out,2)
    finally:
        assert set_mode(previous)==0
    assert code==6 and list(out)==[17,23]


def test_runtime_artifact_drift_is_retained_as_refusal(kernel,tmp_path):
    from ciw.operations.registry import OperationRegistry
    from ciw.operations.runner import execute,validate_execution
    path=tmp_path/"drift.so"; shutil.copyfile(kernel._path,path)
    bound=k.OscillatorKernel(path,library_sha256=kernel.library_sha256,source_sha256=kernel.source_sha256)
    registry=OperationRegistry(); registry.register(k.operation(bound))
    with path.open("ab") as stream: stream.write(b"drift")
    run,p=example_run(),example_parameters()
    selection={"revision":0,"channel":"q","interval_s":p["interval_s"]}
    attempt,result=execute(registry,run,selection,"fixture.json",k.OPERATION,p)
    assert result is None and attempt["runtime"] is None and attempt["status"]=="refused"
    validate_execution(attempt,run,0,{})


def test_comparison_rejects_changed_result():
    with pytest.raises(ValueError): gate_module().compare([[0.0,1.0]],[[0.0,2.0]])
    with pytest.raises(ValueError): gate_module().compare([[0.0,math.nan]],[[0.0,0.0]])
