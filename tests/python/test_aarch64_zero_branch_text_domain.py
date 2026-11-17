"""Text CB register-domain rejection, including actual public relaxation.

The tiny far fixtures lower the limit only inside tests. Production displacement
limits and the existing full-size relaxation tests are unchanged. Host only.
"""
import inspect
import textwrap
import pytest
from pcc.backend import arm64_encode as e
from pcc.backend.self_backend_value_arena import CompilerIntArena

BAD=[(op,reg) for op in ('cbz','cbnz') for reg in ('sp','wsp')]


def reject(action):
    try:action()
    except e.EncodeError as error:
        assert type(error) is e.EncodeError and str(error)=='bad emitted zero-branch register'
    else:raise AssertionError('illegal text CB SP rejection oracle')


def near(op,reg):
    reject(lambda:e.assemble_text_lines([op+' '+reg+', L_end','L_end:','ret']))


def far(patch,op,reg):
    patch.setattr(e,'_CONDITIONAL_BRANCH_LIMIT',2)
    calls=[];original=e._inverted_skip_word
    def inverted(line):
        calls.append(line)
        return original(line)
    patch.setattr(e,'_inverted_skip_word',inverted)
    reject(lambda:e.assemble_text_lines([op+' '+reg+', L_end']+['nop']*8+['L_end:','ret']))
    assert calls==[op+' '+reg+', L_end'], 'actual public relaxation entry oracle'


@pytest.mark.parametrize('op,reg',BAD)
@pytest.mark.parametrize('upper',[False,True])
def test_public_near_rejects_sp_spelling(op,reg,upper):
    near(op.upper() if upper else op,reg.upper() if upper else reg)


@pytest.mark.parametrize('op,reg',BAD)
def test_public_far_relaxation_rejects_sp(monkeypatch,op,reg):
    far(monkeypatch,op,reg)


def direct(op,reg,known):
    records=CompilerIntArena();ids={'L_old':0};names=['L_old']
    try:
        records.append4(7,0xd503201f,0,-1)
        before=(len(records),list(records._values),dict(ids),list(names))
        result=e.append_emitted_instruction_record('  '+op+' '+reg+', L_end',8,0,0,
            {'L_end':16} if known else None,records,ids,names)
        assert result==e.EMITTED_INSTRUCTION_FALLBACK, 'direct illegal CB fallback oracle'
        assert (len(records),list(records._values),ids,names)==before, 'direct fallback no mutation oracle'
    finally:records.close()


@pytest.mark.parametrize('op,reg',BAD)
@pytest.mark.parametrize('known',[False,True])
def test_direct_fallback_preserves_records_and_symbols(op,reg,known):direct(op,reg,known)


@pytest.mark.parametrize('op',['cbz','cbnz'])
@pytest.mark.parametrize('reg,base,rt',[('xzr',0xb4000000,31),('wzr',0x34000000,31),
    ('x0',0xb4000000,0),('w0',0x34000000,0),('fp',0xb4000000,29),('lr',0xb4000000,30)])
def test_legal_text_fixed_words_and_inverted_skip(op,reg,base,rt):
    result=e.assemble_text_lines([op+' '+reg+', L_end','L_end:','ret'])
    expected=base|rt|32|(0x01000000 if op=='cbnz' else 0)
    assert int.from_bytes(result.code[:4],'little')==expected, 'legal CB fixed word oracle'
    inverted=(base|rt|64)^(0x01000000 if op=='cbz' else 0)
    assert e._inverted_skip_word(op+' '+reg+', L_end')==inverted
    assert expected&31==rt and expected&0x7e000000==0x34000000


@pytest.mark.parametrize('kind',['direct','near','far'])
def test_missing_text_guard_mutants_have_specific_oracles(monkeypatch,kind):
    if kind=='direct':
        fn=e.append_emitted_instruction_record
        old='line[register_start:register_end] in ("sp", "wsp") or '
        new=''
        oracle=lambda patch:direct('cbz','sp',False)
        message=r'^direct illegal CB fallback oracle(?:\n|$)'
    else:
        fn=e._encode_one if kind=='near' else e._inverted_skip_word
        expression='ops[0]' if kind=='near' else 'operands[0]'
        old='if '+expression+'.strip().lower().rstrip(",") in ("sp", "wsp"):'
        new='if False:'
        oracle=(lambda patch:near('cbz','sp')) if kind=='near' else (lambda patch:far(patch,'cbz','sp'))
        message=r'^illegal text CB SP rejection oracle(?:\n|$)'
    with pytest.MonkeyPatch.context() as positive:oracle(positive)
    source=textwrap.dedent(inspect.getsource(fn));assert source.count(old)==1
    namespace=dict(fn.__globals__)
    exec(compile(source.replace(old,new),'<missing-text-cb-guard>','exec'),namespace)
    monkeypatch.setattr(e,fn.__name__,namespace[fn.__name__])
    with pytest.raises(AssertionError,match=message):
        with pytest.MonkeyPatch.context() as changed:oracle(changed)
