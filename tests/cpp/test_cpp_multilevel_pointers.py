"""N4659 [conv.qual], [conv.ptr], [expr.type], [over.ics.rank], [expr.unary.op].

Deep pointer qualification is not a license for const insertion through a
writable intermediate pointer. Reuse the existing C AST/lvalue ABI only after
checking the complete qualification decomposition.
"""
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_owned_execution import owned_only

ORIGINALS = (
    ('uninitialized-unused', 'int f(){int **p;return 0;}', 'int f();int cpp_test_entry(){return f();}', 0),
    ('address-of-pointer', 'int f(){int *p=nullptr;&p;return 0;}', 'int f();int cpp_test_entry(){return f();}', 0),
    ('grouped-parameter', 'int function(int (**value));', 'int cpp_test_entry(){return 42;}', 42),
    ('grouped-alias', 'typedef int (**Pointer);', 'int cpp_test_entry(){return 42;}', 42),
)
CASES = (
    ('original-c-return', 'extern "C" int**same(int**p){return p;}'
     'int cpp_test_entry(){int value=42;int*p=&value;return **same(&p);}'),
    ('write-through-two-levels', 'int cpp_test_entry(){int x=1;int*p=&x;int**q=&p;**q=42;return x;}'),
    ('write-new-pointer', 'int cpp_test_entry(){int x=1;int y=42;int*p=&x;int**q=&p;*q=&y;return **q;}'),
    ('safe-deep-const', 'int read(const int*const*p){return **p;}'
     'int cpp_test_entry(){int x=42;int*p=&x;return read(&p);}'),
    ('three-levels', 'int read(const int*const*const*p){return ***p;}'
     'int cpp_test_entry(){int x=42;int*p=&x;int**q=&p;return read(&q);}'),
    ('qualification-overload-order', 'int pick(int**){return 42;}int pick(const int*const*){return 1;}'
     'int cpp_test_entry(){int x=0;int*p=&x;return pick(&p);}'),
    ('composite-deep-const', 'int cpp_test_entry(){int x=42;int*p=&x;const int*q=&x;'
     'int**a=&p;const int**b=&q;if(a==b)return 1;if(a!=b)return x;return 2;}'),
    ('pointer-object-to-void', 'int cpp_test_entry(){int*p=nullptr;int**q=&p;void*v=q;if(v==q)return 42;return 1;}'),
    ('const-pointer-parameter', 'int get(int**const q){return **q;}'
     'int cpp_test_entry(){int x=42;int*p=&x;return get(&p);}'),
)


@pytest.mark.parametrize('name,source',CASES,ids=[x[0] for x in CASES])
def test_deep_pointer_rule_originals_have_byte_stable_owned_artifacts(name,source,owned_only):
    units=((name+'.cpp',source),)
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source',CASES,ids=[x[0] for x in CASES])
def test_deep_pointer_rule_originals_match_real_cpp17_reference(name,source,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,((name+'.cpp',source),),42)


@pytest.mark.parametrize('name,original,entry,result',ORIGINALS,ids=[x[0] for x in ORIGINALS])
def test_original_formerly_rejected_source_is_preserved(name,original,entry,result,owned_only):
    units=((name+'.cpp',original),('entry.cpp',entry))
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,original,entry,result',ORIGINALS,ids=[x[0] for x in ORIGINALS])
def test_original_formerly_rejected_source_matches_real_cpp17(name,original,entry,result,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,((name+'.cpp',original),('entry.cpp',entry)),result)


@pytest.mark.parametrize('source,code',[
    ('int f(){int*p=nullptr;const int**q=&p;return 0;}', 'cpp.invalid-conversion'),
    ('int f(){const int*p=nullptr;int*const*q=&p;return 0;}', 'cpp.invalid-conversion'),
    ('int f(){int*p=nullptr;int**q=&p;const int***r=&q;return 0;}', 'cpp.invalid-conversion'),
    ('int f(){int x=1;int*p=&x;const int*const*q=&p;**q=2;return x;}', 'cpp.const-assignment'),
    ('int f(){int x=1;int*p=&x;int*const*q=&p;*q=&x;return x;}', 'cpp.const-assignment'),
    ('int use(const int**);int f(){int*p=nullptr;return use(&p);}', 'cpp.no-viable-overload'),
    ('int f(){int*p=nullptr;int**q=&p;long**r=q;return 0;}', 'cpp.invalid-conversion'),
    ('int f(){int*p=nullptr;int*const*q=&p;void*v=q;return 0;}', 'cpp.invalid-conversion'),
])
def test_unsafe_qualification_is_rejected_before_emission(source,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code==code
