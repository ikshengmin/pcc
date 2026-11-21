"""Interaction is permitted for the real default codec, never arbitrary argument/extern dispatch."""
import re
from pathlib import Path
from pcc.frontends.python.pipeline import compile_python
from tests.owned_ir_validation import verify_ir_text
SRC="""def plain(s: str):
    return s.encode()
def discard(s: str):
    s.encode()
    return 1
def explicit(s: str, encoding: str):
    return s.encode(encoding)
def unknown(s: str):
    return s.encode(errors="surrogateescape")
"""
def _b(text,name):
    m=re.search(r'(?ms)^define [^\n]*?@user_scoped_encode_'+name+r'\([^\n]*\) [^\n]*\{\n(.*?)^\}',text)
    assert m,name
    return m[1]
def test_scoped_encoder_is_private_and_fallback_is_still_ordinary(tmp_path,monkeypatch):
    src=tmp_path/'scoped_encode.py';src.write_text(SRC)
    for enabled in [False,True]:
        monkeypatch.setenv('PCC_EXPERIMENT_GC3_LEXICAL','1' if enabled else '0')
        path=tmp_path/str(int(enabled));compile_python(str(src),str(path),backend='self',emit_llvm_only=True,python_library=True)
        t=path.read_text();verify_ir_text(t);a=_b(t,'plain')
        assert ('@pcc_exp2_lexical_leaf_slot(' in a)==enabled
        for n in ('explicit','unknown'):
            b=_b(t,n);assert '@pcc_exp2_lexical_leaf_slot(' not in b and '@py_str_encode_with_encoding(' in b
        assert '@pcc_gc_foreign_lease_acquire(' in a and '@py_str_encode_with_encoding(' in a
        if enabled:
            assert '@pcc_exp2_window_active(' in a
            assert 'exp2.encode.legacy' in a and '@pcc_gc_pin(' in a
            assert a.count('@py_str_encode_with_encoding(')==3 and '@pcc_exp2_lexical_leaf_slot(' in a
            drop = _b(t,'discard')
            # Both exclusive arms return one owner. Only *after* the join
            # can the ordinary expression-statement discard it.
            after = re.search(r'(?ms)^exp2.encode.merge[^:]*:(.*?)(?:^[A-Za-z_.][^:\n]*:|\Z)', drop)[1]
            assert re.search(r'call void \(ptr\) @pcc_gc_release(?:_known)?\(ptr %exp2.encode.result', after), after
