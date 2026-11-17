"""CB Rt31 is ZR. Reject illegal SP spelling, without claiming native execution."""
import inspect
import textwrap
import pytest
from pcc.backend import arm64_encode as enc
from pcc.backend import self_backend_aarch64_darwin_mem as direct
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend.self_backend_aarch64_fragments import AArch64EmissionFragments
from pcc.backend.self_backend_aarch64_semantic_encode import encode_semantic_instruction
from pcc.backend.self_backend_value_arena import CompilerInt4

BAD=[(op,reg) for op in ('cbz','cbnz') for reg in ('sp','wsp')]


def reject(action):
    try:action()
    except enc.EncodeError as error:
        assert str(error)=='bad emitted zero-branch register'
    else:
        raise AssertionError('illegal CB SP operand rejection oracle')


def typed_operands(reg):
    return [CompilerInt4(sem.TOKEN_REGISTER,sem.semantic_register_id(reg),0,0),
            CompilerInt4(sem.TOKEN_SYMBOL,0,0,0)]


@pytest.mark.parametrize('op,reg',BAD)
@pytest.mark.parametrize('route',['encoder','plain_fragment','typed_fragment','semantic'])
def test_illegal_sp_rejected_and_fragment_preimage_unchanged(op,reg,route):
    if route=='encoder':
        reject(lambda:enc.encode_emitted_branch_base_parts(op,reg));return
    if route=='semantic':
        reject(lambda:encode_semantic_instruction(sem.semantic_opcode_id(op),typed_operands(reg)));return
    f=AArch64EmissionFragments(capture_semantics=route=='typed_fragment')
    try:
        span=f.new_fragment();f.append_label(span,'L_existing')
        owners=[f.records,f.spans.nodes,f.spans.spans]
        if f.semantics is not None:owners += [f.semantics.headers,f.semantics.tokens]
        before=[(len(a),list(a._values)) for a in owners]
        symbols=(list(f.symbol_names),dict(f.symbol_ids))
        reject(lambda:f.append_branch(span,op,'L_new',reg))
        assert [(len(a),list(a._values)) for a in owners]==before, 'branch failure prefix oracle'
        assert (list(f.symbol_names),dict(f.symbol_ids))==symbols
    finally:f.close()


@pytest.mark.parametrize('op',['cbz','cbnz'])
@pytest.mark.parametrize('reg,base,rt',[('xzr',0xb4000000,31),('wzr',0x34000000,31),
    ('x0',0xb4000000,0),('w0',0x34000000,0),('fp',0xb4000000,29),('lr',0xb4000000,30)])
def test_legal_aliases_and_zero_registers_keep_exact_words(op,reg,base,rt):
    expected=base|rt|(0x01000000 if op=='cbnz' else 0)
    assert enc.encode_emitted_branch_base_parts(op,reg)==expected
    assert encode_semantic_instruction(sem.semantic_opcode_id(op),typed_operands(reg)).first==expected
    for capture in (False,True):
        f=AArch64EmissionFragments(capture_semantics=capture)
        try:
            span=f.new_fragment();f.append_branch(span,op,'L_target',reg)
            f.start_cursor(span);rid=f.next_record_id()
            assert f.records.get4_unchecked(rid).first==expected
        finally:f.close()
    # Independent field check, not a second call to the register encoder.
    assert expected&0x7e000000==0x34000000 and expected&31==rt
    if rt==31:
        decoded_taken=((expected>>24)&1)==0
        assert decoded_taken==(op=='cbz')


@pytest.mark.parametrize('op,reg',BAD)
def test_direct_capture_preserves_existing_intern_before_error_behavior(op,reg):
    direct.begin_direct_instruction_capture(capture_semantics=True)
    try:
        records=direct.borrow_direct_instruction_records()
        semantics=direct.borrow_direct_instruction_semantics()
        names=direct.borrow_direct_instruction_symbol_names()
        before=[(len(a),list(a._values)) for a in (records,semantics.headers,semantics.tokens)]
        reject(lambda:direct.emitted_branch_line(op,'L_rejected',reg))
        assert [(len(a),list(a._values)) for a in (records,semantics.headers,semantics.tokens)]==before
        # The producer interns before invoking the encoder. Do not silently
        # strengthen this different transport's rollback contract.
        assert names==['L_rejected']
        direct.emitted_branch_line('cbz','L_rejected','xzr')
        assert names==['L_rejected'] and len(records)==4
    finally:direct.end_direct_instruction_capture()


def test_missing_sp_guard_mutant_killed_by_rejection_oracle(monkeypatch):
    for op,reg in BAD:reject(lambda:enc.encode_emitted_branch_base_parts(op,reg))
    original=enc.encode_emitted_branch_base_parts
    source=textwrap.dedent(inspect.getsource(original))
    anchor='register in ("sp", "wsp") or '
    assert source.count(anchor)==1
    namespace=dict(original.__globals__)
    exec(compile(source.replace(anchor,''),'<missing-cb-sp-guard>','exec'),namespace)
    monkeypatch.setattr(enc,'encode_emitted_branch_base_parts',namespace[original.__name__])
    with pytest.raises(AssertionError,match=r'^illegal CB SP operand rejection oracle(?:\n|$)'):
        reject(lambda:enc.encode_emitted_branch_base_parts('cbz','sp'))
