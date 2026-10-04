"""Optional FIR instrument binding; SCR owns the numerical kernel.

No native library is loaded by importing this module or inspecting a workspace.
The original CIW Session/OperationRegistry own dispatch and occurrence records.
"""
from __future__ import annotations
from copy import deepcopy
import math
from pathlib import Path
import platform
import tempfile

from .control_contracts import bytes_ref, content_ref, detached, keys, load, number, save_new, text
from .core.identities import content_identity, validate_evidence_identity
from .dsp_abi import Library, CONTRACT_SHA256
from .instruments import validate_run
from .operations.registry import Operation

OPERATION = "signal.filter.fir.v1"
_REGISTERED = False


def _vector(value, low, high):
    if type(value) is not list or not low <= len(value) <= high:
        raise ValueError("DSP buffer exceeds declared length")
    return [number(v) for v in value]


def inputs(run, parameters):
    """Select retained samples and their exact predecessor history, not new math."""
    validate_run(run); validate_evidence_identity(run)
    keys(parameters, {"channel", "interval_s", "taps", "initial_history", "clock_id"})
    text(parameters['clock_id'])
    taps = _vector(parameters['taps'], 1, 64)
    history = _vector(parameters['initial_history'], len(taps)-1, len(taps)-1)
    channel = parameters['channel']
    if type(channel) is not str or channel not in run['channels']:
        raise ValueError("Unknown DSP channel")
    interval = _vector(parameters['interval_s'], 2, 2)
    if not 0 <= interval[0] < interval[1] <= run['metadata']['duration_s']:
        raise ValueError("Invalid DSP selection interval")
    fs = number(run['metadata']['sample_rate_hz'])
    if fs <= 0: raise ValueError("A declared positive sample rate is required")
    times = run['time_s']; dt=1/fs
    if any(not math.isclose(b-a,dt,rel_tol=1e-8,abs_tol=dt*1e-10) for a,b in zip(times,times[1:])):
        raise ValueError("DSP v1 refuses gaps/irregular sampling; resampling must be explicit")
    indices = [i for i,t in enumerate(times) if interval[0] <= t < interval[1]]
    if not 1 <= len(indices) <= 4096: raise ValueError("DSP selection requires 1..4096 samples")
    first, last = indices[0], indices[-1]+1
    values = run['channels'][channel]['values']
    # Initial history explicitly refers to the samples before this recording.
    # Later selections use the required retained source predecessors, never a
    # hidden reset at a chunk boundary. Null predecessors refuse, not zero-fill.
    needed = len(taps)-1
    prior = (history + values[max(0,first-needed):first])[-needed:] if needed else []
    prior = _vector(prior, needed, needed)
    selected = _vector(values[first:last], 1, 4096)
    return taps, selected, prior, indices


def _metadata(run, parameters, prior, indices):
    channel = parameters['channel']
    return {'schema':'ciw.dsp-filtered.v1', 'method':'causal_fir_f64',
            'contract_sha256':CONTRACT_SHA256, 'source_evidence_id':run['evidence_id'],
            'channel':channel, 'unit':run['channels'][channel]['unit'],
            'frame':run['metadata']['coordinate_frame'], 'clock_id':parameters['clock_id'],
            'sample_rate_hz':run['metadata']['sample_rate_hz'],
            'source_indices':indices, 'time_s':[run['time_s'][i] for i in indices],
            'coefficients_sha256':content_identity(parameters['taps']),
            'initial_history_sha256':content_identity(parameters['initial_history']),
            'history_used':prior, 'phase':'causal_uncompensated',
            'group_delay':'not_estimated', 'missing_policy':'refuse_required_samples',
            'uncertainty_propagation':'not_performed', 'state_admission':'not_performed'}


def validate_payload(operation, data, run, parameters, selection):
    if operation != OPERATION: raise ValueError("Wrong DSP operation")
    taps, selected, prior, indices = inputs(run, parameters)
    expected = _metadata(run, parameters, prior, indices)
    keys(data, set(expected)|{'values','final_history'})
    # Preserve JSON types: False must not impersonate sample index/time zero.
    if content_identity({k:data[k] for k in expected}) != content_identity(expected):
        raise ValueError("DSP output source/clock/units/contract mismatch")
    _vector(data['values'], len(selected), len(selected))
    state = _vector(data['final_history'], len(taps)-1, len(taps)-1)
    expected_history = (prior+selected)[-(len(taps)-1):] if len(taps)>1 else []
    if state != expected_history: raise ValueError("DSP final input history mismatch")
    # Structural/source validation only: no hidden native recomputation and no
    # claim of numerical verification simply because an artifact is sealed.


def register_schemas():
    global _REGISTERED
    if not _REGISTERED:
        from .operations.schemas import register_payload_validator
        register_payload_validator(OPERATION, validate_payload, role="analysis")
        _REGISTERED = True


class FirBinding:
    def __init__(self, library: Path, *, expected_sha256: str):
        content_ref(expected_sha256)
        self.native = Library(library, expected_sha256=expected_sha256)
        self.identity = {'provider':'scr.dsp-fir', 'abi_version':1,
            'library_sha256':expected_sha256, 'contract_sha256':CONTRACT_SHA256,
            'adapter_sha256':bytes_ref(Path(__file__).read_bytes()),
            'python_binding_sha256':bytes_ref(Path(__file__).with_name('dsp_abi.py').read_bytes()),
            'platform':platform.platform(), 'scope':'operator-trusted native library; not build attestation or physical qualification'}

    def runtime_identity(self):
        if bytes_ref(self.native.path.read_bytes()) != self.native.digest:
            raise ValueError("Bound DSP library changed")
        return deepcopy(self.identity)

    def execute(self, run, parameters):
        taps, selected, prior, indices = inputs(run, parameters)
        self.runtime_identity()
        output, final_history = self.native.fir(taps, selected, prior)
        self.runtime_identity()
        return {**_metadata(run,parameters,prior,indices), 'values':output, 'final_history':final_history}

    def operation(self):
        register_schemas()
        return Operation(OPERATION,'analysis',self.execute,self.runtime_identity)


def registry(binding):
    from .adapters.protocol import InstrumentManifest
    from .control_plane import CapabilityRegistry
    manifest = InstrumentManifest(instrument_id='org.notationsystems.scr.dsp-fir',version='1',role='operation_provider',
        inputs=('run.v1',),outputs=('ciw.dsp-filtered.v1',),units={},frames=(),
        sampling={'mode':'uniform_retained', 'gaps':'refuse'},normalization={'coefficients':'dimensionless_declared'},
        supported_operations=(OPERATION,),determinism={'claim':'qualified_configuration_only'},
        tolerance_policy={'policy':'explicit_comparison'},calibration_requirements={'status':'not_established'})
    result=CapabilityRegistry()
    result.advertise(manifest,runtime=binding.runtime_identity(),capabilities={OPERATION:['signal.condition','signal.filter.fir']})
    result.bind(binding.operation())
    return result


def inspect(path):
    from .session import Session
    register_schemas()
    value = load(Path(path))
    with tempfile.TemporaryDirectory(prefix='net-dsp-inspect-') as directory:
        root=Path(directory); save_new(root/'workspace.json',value)
        session=Session.from_workspace(root/'workspace.json',root/'reopened')
        return {'schema':'ciw.dsp-inspection.v1','fresh_execution':False,
                'validation':'structural_and_source_binding_only', 'numerical_verification':'not_performed',
                'executions':deepcopy(session.executions),'results':deepcopy(session.results)}


def observations(result, *, semantics):
    from .control_contracts import observation, record
    data=result['data']
    if result['operation_id'] != OPERATION: raise ValueError("Not a FIR result")
    return record('observation-stream', observations=[observation(
        identity={'model_id':'dsp-fir/'+data['coefficients_sha256'],'entity_id':data['channel'],'execution_id':result['execution_id']},
        clock={'id':data['clock_id'],'time_s':stamp}, frame=data['frame'],quantity='filtered.'+data['channel'],
        value=value,unit=data['unit'], provenance={'provider':'scr.dsp-fir','sources':[result['record_digest'],data['source_evidence_id']], 'semantics':semantics})
        for stamp,value in zip(data['time_s'],data['values'])])
