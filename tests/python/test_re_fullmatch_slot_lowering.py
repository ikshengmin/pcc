import re
from pcc.frontends.python.pipeline import compile_python

def emit(tmp_path,call):
    src=tmp_path/'fullmatch_slots.py'
    src.write_text('import re\nfrom re import fullmatch\ndef p(s: str): return s + "(a|ab)"\ndef t(s: str): return s + "ab"\ndef f(s: str): return 2\ndef boom(s: str): raise ValueError("second")\ndef run(s: str):\n    return '+call+'\n')
    out=tmp_path/'fullmatch.ll'
    compile_python(str(src),str(out),emit_llvm_only=True,python_library=True,libpython_mode='off',ir_scaffold_mode='on',backend='self')
    text=out.read_text();body=re.search(r'^define[^\n]*@user_fullmatch_slots_run\(.*?^}',text,re.M|re.S).group()
    assert 'strict.nolib.stub' not in body
    return body

def test_dynamic_operands_are_rooted_and_result_published_before_error_check(tmp_path):
    body=emit(tmp_path,'re.fullmatch(p(s), t(s))')
    assert '@py_re_fullmatch(' in body
    first = re.search(r'(%[-.\w]+) = call ptr[^\n]*@user_fullmatch_slots_p\([^\n]*\)', body)
    assert first is not None
    later = body.index('@user_fullmatch_slots_t(')
    assert re.search(r'store ptr ' + re.escape(first.group(1)) + r', ptr %[-.\w]+', body[first.end():later])
    # Every returned regex result is immediately stored into a registered root.
    call=re.search(r'(%[-.\w]+) = call ptr[^\n]*@py_re_fullmatch\([^\n]*\)\n\s*store ptr \1, ptr (%[-.\w]+)',body)
    assert call
    assert 're.fullmatch.argument' in body and 'call.slot.argument.lease' in body
    assert '@py_cleanup_two_roots_preserving_exception' in body
    assert '@pcc_gc_take_pinned_slot' in body

def test_second_argument_exception_has_prior_operand_cleanup(tmp_path):
    body=emit(tmp_path,'re.fullmatch(p(s), boom(s))')
    assert '@user_fullmatch_slots_boom' in body
    assert '@py_cleanup_two_roots_preserving_exception' in body
    assert 're.fullmatch.argument' in body

def test_keyword_evaluation_order_and_flags_conversion(tmp_path):
    body=emit(tmp_path,'re.fullmatch(flags=f(s), string=t(s), pattern=p(s))')
    order=[body.index('@user_fullmatch_slots_'+name+'(') for name in ('f','t','p')]
    assert order==sorted(order)
    assert body.index('@py_index_i64_checked(')>order[-1]
    assert '@py_re_fullmatch_flags(' in body

def test_imported_fullmatch_uses_same_transaction(tmp_path):
    body=emit(tmp_path,'fullmatch(p(s), string=t(s), flags=re.I | re.S)')
    assert '@py_re_fullmatch_flags(' in body
    assert 're.fullmatch.argument' in body
    assert '@py_cpy_' not in body


def test_dynamic_compile_arguments_and_return_use_owned_transaction(tmp_path):
    body = emit(tmp_path, 're.compile(p(s), f(s))')
    first = re.search(r'(%[-.\w]+) = call ptr[^\n]*@user_fullmatch_slots_p\([^\n]*\)', body)
    assert first is not None
    later = body.index('@user_fullmatch_slots_f(')
    assert re.search(r'store ptr ' + re.escape(first.group(1)) + r', ptr %[-.\w]+', body[first.end():later])
    assert re.search(r'(%[-.\w]+) = call ptr[^\n]*@py_re_compile_obj\([^\n]*\)\n\s*store ptr \1, ptr %[-.\w]+', body)
    assert '@py_cleanup_two_roots_preserving_exception' in body
    assert '@pcc_gc_take_pinned_slot' in body


def test_compile_keyword_source_order_and_assignment_result_are_owned(tmp_path):
    source = tmp_path / 'compile_slots.py'
    source.write_text('import re\ndef p(s: str): return s + "a"\ndef f(s: str): return 2\ndef run(s: str):\n    compiled = re.compile(flags=f(s), pattern=p(s))\n    return compiled\n')
    output = tmp_path / 'compile.ll'
    compile_python(str(source), str(output), emit_llvm_only=True, python_library=True,
                   libpython_mode='off', ir_scaffold_mode='on', backend='self')
    body = re.search(r'^define[^\n]*@user_compile_slots_run\(.*?^}', output.read_text(), re.M | re.S).group()
    assert 'strict.nolib.stub' not in body
    assert body.index('@user_compile_slots_f(') < body.index('@user_compile_slots_p(')
    assert re.search(r'(%[-.\w]+) = call ptr[^\n]*@py_re_compile_obj\([^\n]*\)\n\s*store ptr \1, ptr %[-.\w]+', body)
    assert 'call.slot.argument.lease' in body
