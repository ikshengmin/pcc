"""Overload sets mixing static and non-static member functions.

N4659 [over.match.funcs]/4-5, [over.match.best]/1, [over.call.func]/3,
[expr.ref]/4.3. https://timsong-cpp.github.io/cppwp/n4659/over.match.funcs#4
A static member's implicit object parameter matches any object without a
conversion, and its conversion is neither better nor worse than another
candidate's, so only the explicit arguments rank it. With an object
(`x.f(a)`, `p->f(a)`, or `*this` inside a member function), a selected
static member evaluates and discards the object. Without one (`C::f(a)`
outside C, or inside a static member function) a contrived object takes
part in ranking and selecting a non-static member is an error. Class
templates keep the explicit gap. TinyXML2's `XMLElement::DeleteAttribute`
(non-static by name, static by attribute; projects/tinyxml2-11.0.0, Zlib)
has this shape; the programs below are original.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.darwin_execution import (
    REFERENCE_UNAVAILABLE, UNAVAILABLE, reference_rejects, run_owned_cpp, run_reference_cpp,
)
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

PROGRAMS = (
    ('implicit-this-selects-both', (('s.cpp',
     'struct A{int v;};class E{public:int Delete(const char* n){return n[0]-1;}'
     'static int Delete(A* a){return a->v;}int Both(A* a){return Delete(a)+Delete("+");}};'
     'int cpp_test_entry(){E e;A a{0};return e.Both(&a);}'),)),
    ('object-and-qualified-calls', (('s.cpp',
     'struct S{int f(int x){return x+1;}static int f(long x){return int(x)+2;}};'
     'int cpp_test_entry(){S s;return s.f(40)+S::f(0L)-3+(&s)->f(0L);}'),)),
    ('static-member-function-context', (('s.cpp',
     'struct S{static int f(int x){return x;}int f(long x)const{return 1;}static int g(){return f(42);}};'
     'int cpp_test_entry(){return S::g();}'),)),
    ('discarded-object-is-evaluated', (('s.cpp',
     'int n;struct S{int f(int x){return x;}static int f(long x){return 42;}};S* p(S* s){n=1;return s;}'
     'int cpp_test_entry(){S s;int r=p(&s)->f(0L);return r+n-1;}'),)),
)
# Former gap rows of test_cpp_static_members (DEFERRED_STATIC_CASES), moved verbatim.
MOVED_DECLARATIONS = ('struct S{static int f(int);int f(long);};', 'struct S{int f(int);static int f(long);};')


@pytest.mark.parametrize('source', MOVED_DECLARATIONS)
def test_former_mixed_declaration_rows_declare_one_overload_set(source):
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse(source, 'moved.cpp')
    group = parser.scope.lookup('S').dtype.definition.scope.names['f']
    assert sorted(method.method_static for method in group.overloads) == [False, True]


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


INVALID = (
    ('struct S{int f(int x){return x;}static int f(long x){return 1;}};int g(){return S::f(1);}',
     'cpp.nonstatic-member-without-object'),
    ('struct S{int f(int x){return x;}static int f(long x){return 1;}static int h(){return f(1);}};',
     'cpp.nonstatic-member-without-object'),
    ('struct S{int f(int x);static int f(int x);};', 'cpp.static-nonstatic-overload'),
    # The static candidate's object conversion breaks no tie.
    ('struct S{int f(int x){return 1;}static int f(long x){return 2;}};int g(S* s){return s->f(1u);}',
     'cpp.ambiguous-overload'),
)


@pytest.mark.parametrize('source,code', INVALID)
def test_invalid_mixed_overload_uses_are_ordinary_errors(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, 'invalid.cpp')
    assert error.value.code == code


@pytest.mark.xdist_group(name='cpp-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_compiler_rejects_the_same_sources(tmp_path):
    for index, (source, _) in enumerate(INVALID):
        directory = tmp_path / str(index)
        directory.mkdir()
        rejected, stderr = reference_rejects((('invalid.cpp', source),), directory)
        assert rejected, (source, stderr)


def test_class_templates_keep_the_explicit_gap():
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(
            'template<class T>struct S{int f(int x){return x;}static int f(long x){return 1;}int g(){return f(1);}};'
            'int h(){S<int> s;return s.g();}', 'gap.cpp')
    assert error.value.code == 'cpp.unsupported.mixed-static-overloads'
