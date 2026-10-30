import re
import pytest
from pcc.frontends.python.pipeline import compile_python

def emit(tmp_path,expression,method='fullmatch'):
    source=tmp_path/'method_slots.py'
    source.write_text('import re\ndef receiver(s: str): return re.compile(s)\ndef text(s: str): return s + "ab"\ndef start(s: str): return 0\ndef end(s: str): return 99\ndef boom(s: str): raise ValueError("arg")\ndef run(s: str):\n    '+expression+'\n')
    out=tmp_path/'method.ll'
    compile_python(str(source),str(out),emit_llvm_only=True,python_library=True,libpython_mode='off',ir_scaffold_mode='on',backend='self')
    content=out.read_text();body=re.search(r'^define[^\n]*@user_method_slots_run\(.*?^}',content,re.M|re.S).group()
    assert 'strict.nolib.stub' not in body
    return body

@pytest.mark.parametrize('statement',[
    'return receiver(s).fullmatch(text(s), start(s), end(s))',
    'result = receiver(s).fullmatch(text(s), start(s), end(s))',
    'receiver(s).fullmatch(text(s), start(s), end(s))',
])
def test_dynamic_receiver_and_all_arguments_use_registered_roots(tmp_path,statement):
    body=emit(tmp_path,statement)
    order=[body.index('@user_method_slots_'+n+'(') for n in ('receiver','text','start','end')]
    assert order==sorted(order)
    for name in ('receiver','text','start','end'):
        assert re.search(r'(%[-.\w]+) = call ptr[^\n]*@user_method_slots_'+name+r'\([^\n]*\)\n\s*store ptr \1, ptr',body), name
    assert 'method.fullmatch.receiver'  in body and 'method.fullmatch.method' in body
    assert '@py_obj_call_loaded_method(' in body and '@py_obj_call_method(' not in body
    result=re.search(r'(%[-.\w]+) = call ptr[^\n]*@py_obj_call_loaded_method\([^\n]*\)\n\s*store ptr \1, ptr',body)
    assert result
    assert '@py_cleanup_two_roots_preserving_exception' in body
    assert '@pcc_gc_take_pinned_slot' in body

def test_later_argument_error_has_receiver_method_and_argument_cleanup(tmp_path):
    body=emit(tmp_path,'return receiver(s).fullmatch(text(s), boom(s), end(s))')
    assert '@user_method_slots_boom(' in body
    assert 'method.fullmatch.receiver' in body and 'method.fullmatch.method' in body
    assert '@py_cleanup_two_roots_preserving_exception' in body

def test_keywords_use_shared_slot_call_and_source_order(tmp_path):
    body=emit(tmp_path,'return receiver(s).other(end=end(s), text=text(s), start=start(s))')
    order=[body.index('@user_method_slots_'+n+'(') for n in ('receiver','end','text','start')]
    assert order==sorted(order)
    assert '@py_obj_call_slots(' in body
    assert '@py_obj_call(' not in body

def test_unrelated_dynamic_method_gets_same_generic_fix(tmp_path):
    body=emit(tmp_path,'return receiver(s).arbitrary(text(s))')
    assert '@py_obj_call_loaded_method(' in body
    assert 'method.arbitrary.result' in body
