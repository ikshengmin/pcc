"""static_cast in class template member bodies: N4659 [expr.static.cast]/13, [temp.dep.expr]/3.

https://timsong-cpp.github.io/cppwp/n4659/expr.static.cast
A member Pattern keeps the typed target and operand; instantiation applies
the ordinary static_cast rules to the substituted types, including the cv
void* to object pointer conversion (TinyXML2 MemPoolT::Free,
`static_cast<Item*>( mem )`, projects/tinyxml2-11.0.0, Zlib). Original tests.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

PROGRAMS = (
    ('void-pointer-to-member-class-pointer',
     'template<int N>struct Pool{union Item{Item*next;char data[N];};Item slots[2];Item*root;'
     'void Free(void*mem){Item*item=static_cast<Item*>( mem );item->next=root;root=item;}'
     'int Count(){int n=0;for(Item*i=root;i;i=i->next)n=n+1;return n;}};'
     'int cpp_test_entry(){Pool<8> p;p.root=nullptr;p.Free(&p.slots[0]);p.Free(&p.slots[1]);return p.Count()*21;}'),
    ('dependent-arithmetic-cast',
     'template<class T>struct Scale{T value;long Widen(){return static_cast<long>(value)*2;}};'
     'int cpp_test_entry(){Scale<short> s;s.value=21;return (int)s.Widen();}'),
)


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_template_static_cast_programs_compile_to_stable_artifacts(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_template_static_cast_programs_execute_against_real_cpp17_reference(name, source, tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair
    execute_row_pair(tmp_path, monkeypatch, ((name + '.cpp', source),))


def test_invalid_substituted_static_cast_fails_at_instantiation():
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(
            'template<class T>struct S{const void*p;T*get(){return static_cast<T*>(p);}};'
            'int*f(S<int>*s){return s->get();}')
    assert error.value.code == 'cpp.invalid-conversion'
