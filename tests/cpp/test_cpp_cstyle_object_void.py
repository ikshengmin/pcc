"""N4659 [expr.cast], [conv.ptr], [conv.qual]: narrow standard conversion.

Other valid C-style cast combinations remain explicitly unimplemented. No
named static_cast admission changes. Execute exactly these originals on both
owned PCC x86 and G++ C++17, including the side-effecting pointer producer.
"""
import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference

CASES = (
    ('scalar', 'int cpp_test_entry(){int x=42;void*p=(void*)&x;void*q=&x;if(p!=q)return 1;return x;}'),
    ('add-const', 'int cpp_test_entry(){int x=42;const void*p=(const void*)&x;const void*q=&x;if(p!=q)return 1;return x;}'),
    ('retain-const', 'int cpp_test_entry(){const int x=42;const void*p=(const void*)&x;const void*q=&x;if(p!=q)return 1;return x;}'),
    ('top-level-const', 'int cpp_test_entry(){int x=42;int*const p=&x;void*q=(void*)p;void*r=&x;if(q!=r)return 1;return x;}'),
    ('class', 'struct S{int x;};int cpp_test_entry(){S s{42};void*p=(void*)&s;void*q=&s;if(p!=q)return 1;return s.x;}'),
    ('incomplete', 'struct S;void*f(S*p){return (void*)p;}int cpp_test_entry(){if(f(nullptr)==nullptr)return 42;return 1;}'),
    ('pointer-object', 'int cpp_test_entry(){const int x=42;const int*p=&x;void*q=(void*)&p;void*r=&p;if(q!=r)return 1;return *p;}'),
    ('once', 'int*get(int*p,int*n){*n=*n+1;return p;}int cpp_test_entry(){int x=42;int n=0;void*p=(void*)get(&x,&n);void*q=&x;if(p!=q)return 1;if(n!=1)return 2;return x;}'),
)
DEFERRED = (
    ('remove-const', 'void*f(const int*p){return (void*)p;}', 'cpp.unsupported.c-style-cast'),
    ('reverse', 'int*f(void*p){return (int*)p;}', 'cpp.unsupported.c-style-cast'),
    ('integer', 'void*f(){return (void*)0;}', 'cpp.unsupported.c-style-cast'),
    ('function-pointer', 'using F=int();void*f(F*p){return (void*)p;}', 'cpp.unsupported.function-pointers'),
    ('void-identity', 'void*f(void*p){return (void*)p;}', 'cpp.unsupported.c-style-cast'),
    ('named-contract', 'void*f(int*p){return static_cast<void*>(p);}', 'cpp.unsupported.static-cast'),
    ('dependent', 'template<class T>void*f(T*p){return (void*)p;}', 'cpp.unsupported.pointer-templates'),
    ('nondependent-pattern', 'template<class T>void*f(int*p){return (void*)p;}', 'cpp.unsupported.pointer-templates'),
)

@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_cstyle_object_void_parses(name, source):
    CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, name+'.cpp')

@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_cstyle_other_owners_unchanged(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, name+'.cpp')
    assert error.value.code == code

@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_cstyle_object_void_executes_originals(name, source, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, ((name+'.cpp',source),))

@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_casts_are_valid_cpp17(name, source, code, tmp_path):
    result=syntax_reference(tmp_path,name,source)
    assert result.returncode == 0, result.stderr
