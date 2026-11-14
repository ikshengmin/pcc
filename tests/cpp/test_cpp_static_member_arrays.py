"""Static data member arrays of scalar elements.

N4659 [class.static.data]/2, [basic.link]/10, [dcl.init.aggr], [basic.start.static].
https://timsong-cpp.github.io/cppwp/n4659/class.static.data
A static data member may be an array, declared in the class with its bound.
Its one namespace-scope definition may omit that major bound and initializes
it with an explicit braced list (or `{...}` without `=`) whose elements are
static constants: integers and enumerators, null pointers, addresses of
namespace objects and ordinary string literals. Omitted elements, and a
definition without an initializer, are zero-initialized; a const array needs
an initializer. TinyXML2's `static const char* _errorNames[XML_ERROR_COUNT];`
(projects/tinyxml2-11.0.0, Zlib, tinyxml2.h:1959, defined at tinyxml2.cpp:2160)
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
    # The XMLDocument::_errorNames shape: a private array of string pointers
    # bounded by an enumerator, read through a static member function.
    ('error-name-table', (('s.cpp',
     'enum Code{OK,MISSING,BROKEN,CODE_COUNT};'
     'class Doc{public:static const char* Name(Code c){return names_[c];}'
     'private:static const char* names_[CODE_COUNT];};'
     'const char* Doc::names_[CODE_COUNT]={"OK","MISSING","BROKEN"};'
     "int cpp_test_entry(){return Doc::Name(MISSING)[1]-'I'+Doc::Name(BROKEN)[0]-'B'"
     "+Doc::Name(OK)[2]+42;}"),)),
    ('omitted-bound-and-zero-fill', (('s.cpp',
     'struct T{static int v[4];};int T::v[]={40,2};'
     'int cpp_test_entry(){return T::v[0]+T::v[1]+T::v[2]+T::v[3]+int(sizeof(T::v)/sizeof(T::v[0]))-4;}'),)),
    ('zero-initialized-and-written', (('s.cpp',
     'class Counter{static long hits[3];public:static void bump(int i){hits[i]+=7;}'
     'static long total(){return hits[0]+hits[1]+hits[2];}};long Counter::hits[3];'
     'int cpp_test_entry(){for(int i=0;i<3;++i){Counter::bump(0);Counter::bump(2);}return int(Counter::total());}'),)),
    ('nested-arrays-of-addresses', (('s.cpp',
     'int base=40;struct P{static int* slots[2][2];};int* P::slots[2][2]={{&base,nullptr},{0}};'
     'int cpp_test_entry(){return *P::slots[0][0]+2+(P::slots[1][1]==nullptr?0:100)+(P::slots[0][1]?100:0);}'),)),
    ('const-elements-direct-list-and-object-access', (('s.cpp',
     'struct K{static const unsigned char bytes[3];};const unsigned char K::bytes[3]{20,20,2};'
     'int cpp_test_entry(){K k;K* p=&k;return k.bytes[0]+p->bytes[1]+K::bytes[2];}'),)),
    ('separate-units', (
     ('define.cpp', 'struct S{static short t[2];};short S::t[2]={40,2};'),
     ('use.cpp', 'struct S{static short t[2];};int cpp_test_entry(){return S::t[0]+S::t[1];}'))),
    # Former unsupported row of test_cpp_static_data_members, completed with an entry.
    ('declaration-without-odr-use', (('s.cpp', 'struct S{static int a[2];};int cpp_test_entry(){return 42;}'),)),
)


def test_definition_is_one_external_initialized_array():
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    unit = parser.parse('enum E{N=2};struct S{static const char* n[N];};const char* S::n[]={"a"};', 'd.cpp')
    symbol = parser.scope.lookup('S').dtype.definition.scope.names['n']
    assert symbol.c_name == '_ZN1S1nE' and symbol.defined and symbol.dtype.bound == 2
    declaration, definition = [item for item in unit.ext if getattr(item, 'name', None) == '_ZN1S1nE']
    assert (declaration.storage, definition.storage) == (['extern'], [])
    assert len(definition.init.exprs) == 1


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
    ('struct S{static int a[3];};int S::a[3]={1,2,3,4};', 'cpp.too-many-initializers'),
    ('struct S{static int a[3];};int S::a[4]={1};', 'cpp.conflicting-object-type'),
    ('struct S{static int a[3];};long S::a[]={1};', 'cpp.conflicting-object-type'),
    ('struct S{static const int a[3];};const int S::a[3];', 'cpp.const-initializer'),
    ('struct S{static int a[2];};int S::a[2]={1};int S::a[2]={2};', 'cpp.redefinition'),
    ('class S{static int a[2];};int S::a[2]={1};int f(){return S::a[0];}', 'cpp.member-access'),
    ('struct S{static char* n[1];};char* S::n[1]={"x"};', 'cpp.invalid-conversion'),
)


@pytest.mark.parametrize('source,code', INVALID)
def test_invalid_static_member_arrays_are_ordinary_errors(source, code):
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


@pytest.mark.parametrize('source,code', (
    ('struct S{static int a[];};int S::a[2]={1};', 'cpp.unsupported.static-data-members'),
    ('struct T{int x;};struct S{static T t[2];};', 'cpp.unsupported.class-array-lifetime'),
    ('struct S{static char s[3];};char S::s[3]="ab";', 'cpp.unsupported.static-initialization-analysis'),
    ('struct S{static int a[2][2];};int S::a[2][2]={1,2,3,4};', 'cpp.unsupported.array-brace-elision'),
    ('int f();struct S{static int a[2];};int S::a[2]={f(),1};', 'cpp.unsupported.dynamic-initialization'),
))
def test_remaining_array_shapes_stay_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, 'gap.cpp')
    assert error.value.code == code
