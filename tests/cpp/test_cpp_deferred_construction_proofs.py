"""Constructor calls before the constructor's out-of-line definition.

N4659 [class.base.init], [except.ctor]/3, [basic.def.odr]/4.
A constructor of a class with members that need destruction is called only
when its body is a closed construction chain: no unknown call can leave a
partially constructed object needing cleanup that PCC cannot emit yet.
When the call comes before the constructor's definition (TinyXML2's
`XMLText(XMLDocument* doc) : XMLNode(doc)`, with `XMLNode::XMLNode` defined
in tinyxml2.cpp; projects/tinyxml2-11.0.0, Zlib), the obligation moves to
that body, as for class template instances: the body proves or rejects
it, and a translation unit that never binds it fails at the call. The
programs below are original.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.darwin_execution import REFERENCE_UNAVAILABLE, UNAVAILABLE, run_owned_cpp, run_reference_cpp
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

PROGRAMS = (
    # The XMLNode/XMLText shape: protected explicit base constructor defined
    # after the derived class's inline mem-initializer.
    ('base-defined-after-derived', (('s.cpp',
     'struct P{int v;P():v(0){}~P(){}};'
     'class B{protected:explicit B(int n);~B(){}P p;int v;public:int get()const{return v;}};'
     'class D:public B{public:explicit D(int n):B(n){}~D(){}};'
     'B::B(int n):p(),v(n){}int cpp_test_entry(){D d(42);return d.get();}'),)),
    ('object-constructed-before-definition', (('s.cpp',
     'struct P{int v;P():v(2){}~P(){}};struct S{S(int);~S(){}P p;int v;};'
     'int f(){S s(40);return s.v+s.p.v;}S::S(int n):p(),v(n){}int cpp_test_entry(){return f();}'),)),
    ('out-of-class-derived-before-base', (('s.cpp',
     'struct P{int v;P():v(1){}~P(){}};'
     'class B{protected:B(int n);~B(){}P p;int v;public:int get()const{return v+p.v;}};'
     'class D:public B{public:D();~D(){}};D::D():B(41){}B::B(int n):p(),v(n){}'
     'int cpp_test_entry(){D d;return d.get();}'),)),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_programs_compile_to_stable_linux_artifacts(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
@pytest.mark.xdist_group(name='cpp-darwin-native')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_programs_run_natively_with_owned_compile_assemble_and_link(name, units, tmp_path, monkeypatch):
    assert run_owned_cpp(units, tmp_path, monkeypatch) == 42


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
@pytest.mark.xdist_group(name='cpp-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_compiler_returns_the_same_value(name, units, tmp_path):
    assert run_reference_cpp(units, tmp_path) == 42


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_programs_execute_against_real_cpp17_reference_on_linux(name, units, tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.parametrize('source,coordinate,message', [
    # Never defined in this unit: no proof of its construction effects.
    ('struct P{P(){}~P(){}};struct S{S(int);~S(){}P p;};int f(){S s(1);return 0;}',
     'gap.cpp:1:61:', 'requires a bound closed-construction proof'),
    # Defined later with an unknown call: the body rejects the obligation.
    ('int g();struct P{P(){}~P(){}};struct S{S(int);~S(){}P p;int v;};int f(){S s(1);return s.v;}'
     'S::S(int n):p(),v(g()){}', 'gap.cpp:1:92:', 'partial member exception cleanup'),
])
def test_unproven_construction_stays_explicit(source, coordinate, message):
    with pytest.raises(CppDiagnostic, match=message) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, 'gap.cpp')
    assert error.value.code == 'cpp.unsupported.destructor-subobjects'
    assert str(error.value).startswith(coordinate)
