"""Algebra laws and native Session wiring, NOT numerical/physical qualification.

The explicit Python providers below are small routing fixtures. Existing built-in
numerics and native workcell campaigns are exercised separately.
"""
from copy import deepcopy
from unittest.mock import patch
import pytest

from ciw import workflow_algebra as a
from ciw.adapters.protocol import InstrumentManifest
from ciw.control_contracts import keys, number
from ciw.control_plane import CapabilityRegistry, Port
from ciw.core.identities import content_identity
from ciw.instruments import make_demo_run
from ciw.operations.registry import Operation
from ciw.operations.runner import seal
from ciw.operations.schemas import register_payload_validator
from ciw.session import Session

SEED = 'test.workflow.seed.v1'
DOUBLE = 'test.workflow.double.v1'
VALUE = a.wire('test.workflow.value.v1', unit='m', frame='fixture')
RUNTIME = {'provider':'test.workflow', 'scope':'synthetic-routing-fixture'}
_registered = False


@pytest.fixture
def context():
    global _registered
    if not _registered:
        def validator(op, data, run, parameters, selection):
            keys(data, {'schema', 'unit', 'frame', 'value'})
            assert data['schema'] == VALUE['port']['schema']
            number(data['value'])
        for name in (SEED, DOUBLE):
            register_payload_validator(name, validator, role="analysis")
        _registered = True
    registry = CapabilityRegistry()
    manifest = InstrumentManifest(instrument_id='test.workflow', version='1', role='operation_provider',
        inputs=('run.v1',), outputs=(VALUE['port']['schema'],), units={}, frames=(),
        sampling={'mode':'synthetic_fixture'}, normalization={},
        supported_operations=(SEED, DOUBLE), determinism={}, tolerance_policy={}, calibration_requirements={})
    registry.advertise(manifest, runtime=RUNTIME, capabilities={SEED:['fixture.seed'], DOUBLE:['fixture.double']},
        inputs={DOUBLE:{'input':VALUE['port']}},
        outputs={op:{'value':{'type':VALUE['port'], 'path':['data']}} for op in (SEED, DOUBLE)})
    calls = []
    def execute(op, params):
        calls.append((op, deepcopy(params)))
        value = params['value'] if op == SEED else params['input']['value'] * 2
        return {**VALUE['port'], 'unit':params.get('emit_unit','m'), 'value':value}
    for op in (SEED, DOUBLE):
        registry.bind(Operation(op, 'analysis', lambda r,p,o=op:execute(o,p), lambda:deepcopy(RUNTIME)))
    declarations = {op:a.declare(registry, op, effect=a.effects(), permissions=('fixture.execute',)) for op in (SEED, DOUBLE)}
    return registry, declarations, a.grant(permissions=('fixture.execute',)), calls


def nf(expr, context):
    return a.normal_form(expr, *context[:3])


def compile(expr, context):
    return a.compile_graph(expr, *context[:3], experiment_id='typed-demo', model_id='routing-fixture.v1')


def source(name='source', value=3.):
    return a.call(name, SEED, {'value':value})


def transform(name='transform'):
    return a.call(name, DOUBLE)


def test_associativity_identity_and_no_compile_execution(context):
    f,g,h = source(),transform('double'),transform('again')
    terms = [a.sequence(a.sequence(f,g),h), a.sequence(f,a.sequence(g,h)),
             a.sequence(a.identity(),f,a.identity([VALUE]),g,h,a.identity([VALUE]))]
    normals = [nf(t, context) for t in terms]
    assert normals[0] == normals[1] == normals[2]
    graphs = [compile(t,context)['experiment'] for t in terms]
    assert graphs[0] == graphs[1] == graphs[2]
    assert context[3] == []


def test_tensor_associativity_and_units(context):
    f,g,h = source('a'),source('b'),source('c')
    assert nf(a.parallel(a.parallel(f,g),h),context) == nf(a.parallel(f,a.parallel(g,h)),context)
    assert nf(a.parallel(a.identity(),f,a.identity()),context) == nf(f,context)


def test_interchange_and_symmetry_involution(context):
    f,g,h,k = source('a'),source('b'),transform('c'),transform('d')
    left = a.sequence(a.parallel(f,g),a.parallel(h,k))
    right = a.parallel(a.sequence(f,h),a.sequence(g,k))
    assert nf(left,context) == nf(right,context)
    swap = a.permute([VALUE,VALUE],[1,0])
    assert nf(a.sequence(a.parallel(f,g),swap,swap),context) == nf(a.parallel(f,g),context)
    assert nf(a.sequence(a.parallel(f,g),swap),context)['outputs'][0]['source']['node_id'] == 'b'


def test_original_session_transports_exact_outputs_after_permutation(tmp_path,context):
    registry, declarations, rights, calls = context
    expr = a.sequence(a.parallel(source('a',3.),source('b',5.)),
                      a.permute([VALUE,VALUE],[1,0]),a.parallel(transform('c'),transform('d')))
    compiled = compile(expr,context)
    session = Session(make_demo_run(),tmp_path,operations=registry.operations)
    report = a.run_compiled(session,compiled,registry,declarations,rights)
    assert report['status'] == 'completed' and len(session.executions) == 4
    assert report['nodes']['c']['result']['data']['value'] == 10.
    assert report['nodes']['d']['result']['data']['value'] == 6.
    assert len({r['execution_id'] for r in session.results.values()}) == 4
    assert all(r['verification_id'] is None for r in session.results.values())
    assert len(calls) == 4


def test_open_identity_is_symbolic_and_never_fake_execution(context):
    assert nf(a.identity([VALUE]),context) == {'inputs':[VALUE], 'outputs':[{'type':VALUE,'source':{'external':0}}], 'nodes':[]}
    with pytest.raises(ValueError,match='Open workflow'):compile(transform(),context)
    with pytest.raises(ValueError,match='no executable'):compile(a.identity(),context)
    assert not context[3]


@pytest.mark.parametrize('field,changed', [('schema','wrong.v1'),('unit','cm'),('frame','other'),('clock','other-clock'),('meaning','velocity')])
def test_incompatible_wire_refinements_refuse(context,field,changed):
    registry, declarations, _, _ = context
    if field in ('schema','unit','frame'):
        declarations[DOUBLE]['inputs']['input']['port'][field] = changed
    else:
        declarations[DOUBLE]['inputs']['input'][field] = changed
    with pytest.raises(ValueError):compile(a.sequence(source(),transform()),context)
    assert not context[3]


def test_explicit_refinement_conversion_must_be_an_operator_contract(context):
    registry, declarations, _, _ = context
    declarations[SEED]['outputs']['value']['clock'] = 'clock-a'
    declarations[DOUBLE]['inputs']['input']['clock'] = 'clock-a'
    declarations[DOUBLE]['outputs']['value']['clock'] = 'clock-b'
    declared = compile(a.sequence(source(),transform()),context)
    assert declared['normal_form']['outputs'][0]['type']['clock'] == 'clock-b'
    # A declared mapping is not acquired evidence of synchronization.
    assert declared['authority']['verification_id'] is None


def test_parallel_wires_are_partitioned_not_implicitly_copied(context):
    with pytest.raises(ValueError,match='interface mismatch'):
        compile(a.sequence(source(),a.parallel(transform('a'),transform('b'))),context)
    with pytest.raises(ValueError,match='literal parameter'):
        compile(a.sequence(source(),a.call('b',DOUBLE,{'input':{}})),context)


@pytest.mark.parametrize('left,right', [
    (a.effects(writes=('scene',)),a.effects(reads=('scene/material',))),
    (a.effects(reads=('scene/material',)),a.effects(writes=('scene',))),
    (a.effects(writes=('scene/mesh',)),a.effects(writes=('scene/mesh',))),
    (a.effects(unknown=True),a.effects()),
])
def test_unordered_effect_conflicts(context,left,right):
    registry,declarations,_,_ = context
    declarations[SEED]['effects'] = left
    declarations[DOUBLE]['effects'] = right
    # Two open calls can be checked structurally before source closure.
    with pytest.raises(ValueError,match='effect conflict'):
        nf(a.parallel(source(),transform()),context)
    # Data-dependence serializes the very same resources.
    assert compile(a.sequence(source(),transform()),context)['experiment']


@pytest.mark.parametrize('left,right', [
    (a.effects(reads=('scene',)),a.effects(reads=('scene/material',))),
    (a.effects(writes=('cell/a',)),a.effects(writes=('cell/b',))),
    (a.effects(writes=('scene/a',)),a.effects(reads=('scene/ab',))),
])
def test_independent_or_read_shared_resources(context,left,right):
    context[1][SEED]['effects']=left;context[1][DOUBLE]['effects']=right
    assert len(nf(a.parallel(source(),transform()),context)['nodes']) == 2


def test_default_unknown_effect_is_not_silently_pure(context):
    registry,declarations,_,_=context
    declarations[SEED]=a.declare(registry,SEED)
    with pytest.raises(ValueError,match='effect conflict'):compile(a.parallel(source('a'),source('b')),context)


def test_authority_is_a_set_not_a_rank(context):
    context[2]['permissions']=['release']
    with pytest.raises(ValueError,match='permission'):compile(source(),context)
    assert not context[3]


def test_scope_reference_is_explicit_and_exact(context):
    ref=content_identity({'qualification':'synthetic-routing-only'})
    context[1][SEED]['qualifications']={'routing-fixture.v1':ref}
    with pytest.raises(ValueError,match='qualification'):compile(source(),context)
    context[2]['qualifications']={'routing-fixture.v1':content_identity('other')}
    with pytest.raises(ValueError,match='qualification'):compile(source(),context)
    context[2]['qualifications']['routing-fixture.v1']=ref
    assert compile(source(),context)['authority']['authorizes_execution'] is False


def test_contract_drift_rejected_and_compile_does_not_probe_runtime(context):
    registry,declarations,_,_=context
    with patch.object(registry.operations,'get',side_effect=AssertionError('runtime probe')):
        compile(source(),context)
    declarations[SEED]['contract_digest']=content_identity('different')
    with pytest.raises(ValueError,match='contract drift'):compile(source(),context)


def test_saved_receipt_never_regrants_live_permissions(tmp_path,context):
    registry,declarations,rights,calls=context
    compiled=compile(source(),context)
    session=Session(make_demo_run(),tmp_path,operations=registry.operations)
    with pytest.raises(ValueError):a.run_compiled(session,compiled,registry,declarations,a.grant())
    assert not calls and not session.executions


def test_offline_inspection_no_native_or_registry_loading(context):
    value=compile(a.sequence(source(),transform()),context)
    before=deepcopy(value)
    with patch('subprocess.Popen',side_effect=AssertionError('process')):
        assert a.inspect_compilation(value)['fresh_execution'] is False
    assert value==before and not context[3]


@pytest.mark.parametrize('field',['normal_form','experiment','authority','compiler_sha256'])
def test_resealed_compilation_cannot_contradict_its_expression(context,field):
    value=compile(source(),context)
    if field=='normal_form':value[field]['outputs']=[]
    elif field=='experiment':value[field]['nodes'][0]['parameters']['value']=42;seal(value[field])
    elif field=='authority':value[field]['authorizes_execution']=True
    else:value[field]=content_identity('other')
    seal(value)
    with pytest.raises(ValueError):a.inspect_compilation(value)


@pytest.mark.parametrize('expr', [
    {'kind':'trace'}, {'kind':'feedback'}, {'kind':'choice'}, {'kind':'repeat'},
    {'kind':'call','node_id':'x','operation_id':SEED,'parameters':{},'shell':'run'},
    a.permute([VALUE,VALUE],[0,0]),a.permute([VALUE],[False]),
    a.sequence(source(),source()), {'kind':'sequence','items':'wrong'},
])
def test_unknown_unsafe_or_non_linear_syntax_refuses(expr,context):
    with pytest.raises(ValueError):nf(expr,context)
    assert not context[3]


def test_depth_total_terms_and_expansion_bounds(context):
    expr=source()
    for _ in range(11):expr=a.sequence(expr)
    with pytest.raises(ValueError,match='budget'):nf(expr,context)
    expr=a.parallel(*(a.parallel(*(a.identity() for _ in range(64))) for _ in range(5)))
    with pytest.raises(ValueError,match='budget'):nf(expr,context)
    expr=a.parallel(*(source(str(i)) for i in range(64)))
    assert len(compile(expr,context)['experiment']['nodes'])==64
    with pytest.raises(ValueError):nf(a.parallel(expr,source('65')),context)
    cycle=a.sequence();cycle['items'].append(cycle)
    with pytest.raises(ValueError,match='budget'):nf(cycle,context)


@pytest.mark.parametrize('resource',['/host','../escape','a/../b','a//b','a*','a\\b'])
def test_effects_do_not_accept_filesystem_paths_or_wildcards(resource):
    with pytest.raises(ValueError):a.effects(writes=(resource,))


def test_compilation_detaches_source_and_grants(context):
    expr=source();value=compile(expr,context)
    expr['parameters']['value']=99;context[2]['permissions'].clear()
    assert value['expression']['parameters']['value']==3.
    assert value['grant_snapshot']['permissions']==['fixture.execute']


def test_static_success_does_not_hide_runtime_contract_failure(tmp_path,context):
    expr=a.sequence(a.call('source',SEED,{'value':3.,'emit_unit':'cm'}),transform())
    compiled=compile(expr,context)
    registry,declarations,rights,calls=context
    session=Session(make_demo_run(),tmp_path,operations=registry.operations)
    report=a.run_compiled(session,compiled,registry,declarations,rights)
    assert report['nodes']['source']['status']=='output_rejected'
    assert report['nodes']['transform']['status']=='blocked'
    assert len(session.executions)==len(session.results)==1
    assert len(calls)==1  # Static well-typedness is not evidence that a provider honored its contract.


@pytest.mark.parametrize('construct',[lambda:a.effects(reads='world'),lambda:a.effects(writes='world'),lambda:a.grant(permissions='release')])
def test_names_are_not_silently_split_into_characters(construct):
    with pytest.raises(ValueError):construct()
