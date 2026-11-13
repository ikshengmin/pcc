"""Non-inline static data members of non-template classes.

N4659 [class.static.data], [basic.lookup.unqual]/13, [expr.ref]/4.1,
[basic.start.static], [conv.array].
https://timsong-cpp.github.io/cppwp/n4659/class.static.data
The in-class declaration declares one external object `C::m` (Itanium
`_ZN1C1mE`); one namespace-scope definition `T C::m = init;` defines it with
its initializer looked up and access-checked in the class scope. `x.m` and
`p->m` evaluate the object expression and name the static object. A
`const char*` static object may be initialized by a string literal. TinyXML2's
`XMLUtil::writeBoolTrue`/`writeBoolFalse` (projects/tinyxml2-11.0.0, Zlib)
have this shape; the programs below are original.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.darwin_execution import (
    REFERENCE_UNAVAILABLE, UNAVAILABLE, reference_rejects, run_owned_cpp, run_reference_cpp,
)
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

PROGRAMS = (
    ('basic-definition', (('s.cpp', 'struct S{static int v;};int S::v=42;int cpp_test_entry(){return S::v;}'),)),
    ('zero-initialized-definition', (('s.cpp',
     'struct S{static int v;static int get(){return v;}void set(int n){v=n;}};int S::v;'
     'int cpp_test_entry(){if(S::get()!=0)return 1;S s;s.set(42);return S::get();}'),)),
    ('private-in-member-definition', (('s.cpp',
     'class S{static int v;public:static int get();};int S::v=42;int S::get(){return v;}'
     'int cpp_test_entry(){return S::get();}'),)),
    ('class-scope-initializer', (('s.cpp',
     'struct S{enum{K=40};static int v;};int S::v=K+2;int cpp_test_entry(){return S::v;}'),)),
    # The TinyXML2 XMLUtil shape: private string pointers, defined with
    # literals, read and replaced through static member functions.
    ('string-literal-pointers', (('s.cpp',
     'class U{public:static void Set(const char* t,const char* f);'
     'static const char* Get(bool v){return v?t_:f_;}'
     'private:static const char* t_;static const char* f_;};'
     'const char* U::t_="true";const char* U::f_="false";'
     'void U::Set(const char* t,const char* f){t_=t;f_=f;}'
     "int cpp_test_entry(){int a=U::Get(true)[0]-'t'+U::Get(false)[4]-'e';U::Set(\"yes\",\"no\");"
     "return a+U::Get(true)[0]-'y'+U::Get(false)[1]-'o'+42;}"),)),
    ('object-expression-is-evaluated', (('s.cpp',
     'struct S{static int v;};int S::v=40;int n;S* f(S* q){n=1;return q;}'
     'int cpp_test_entry(){S s;s.v+=1;int x=f(&s)->v;return x+n;}'),)),
    ('address-constant', (('s.cpp',
     'struct S{static int v;};int*p=&S::v;int S::v=40;int cpp_test_entry(){*p+=2;return S::v;}'),)),
    ('separate-units', (
     ('define.cpp', 'struct S{static int v;static const char* s;};int S::v=40;const char* S::s="ab";'),
     ('use.cpp', "struct S{static int v;static const char* s;};int cpp_test_entry(){return S::v+S::s[1]-'b'+2;}"))),
    ('namespace-string-pointer', (('s.cpp', 'const char* p="*";int cpp_test_entry(){return p[0];}'),)),
    # Former unsupported row (below), completed with an entry.
    ('declaration-without-odr-use', (('s.cpp', 'struct S{static int value;};int cpp_test_entry(){return 42;}'),)),
)
# Formerly pinned as cpp.unsupported.static-data-members by
# test_cpp_class_semantics (and its execution twin) and test_cpp_static_members.
MOVED_DECLARATION = 'struct S{static int value;};'


def test_former_declaration_row_declares_an_external_object():
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    unit = parser.parse(MOVED_DECLARATION, 'moved.cpp')
    symbol = parser.scope.lookup('S').dtype.definition.scope.names['value']
    assert symbol.static_data_member and symbol.c_name == '_ZN1S5valueE' and not symbol.defined
    declaration, = [item for item in unit.ext if getattr(item, 'name', None) == '_ZN1S5valueE']
    assert declaration.storage == ['extern'] and declaration.init is None


def test_definition_is_one_external_initialized_object():
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    unit = parser.parse('namespace n{struct S{static const char* p;};}const char* n::S::p="x";', 'd.cpp')
    definitions = [item for item in unit.ext if getattr(item, 'name', None) == '_ZN1n1S1pE']
    assert [item.storage for item in definitions] == [['extern'], []]
    assert definitions[1].init is not None


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
    ('struct S{static int v;};int S::v=1;int S::v=2;', 'cpp.redefinition'),
    ('struct S{static int v;};long S::v=1;', 'cpp.conflicting-object-type'),
    ('struct S{static int v;};int S::w=1;', 'cpp.no-matching-member'),
    ('struct S{static int v;};static int S::v=1;', 'cpp.invalid-member-storage'),
    ('struct S{extern int v;};', 'cpp.invalid-member-storage'),
    ('class S{static int v;};int S::v=42;int f(){return S::v;}', 'cpp.member-access'),
    ('class S{static int v;};int S::v=42;int f(){S s;return s.v;}', 'cpp.member-access'),
    ('struct S{static int v;int v;};', 'cpp.redeclaration'),
    ('struct S{static char* p;};char* S::p="x";', 'cpp.invalid-conversion'),
)


@pytest.mark.parametrize('source,code', INVALID)
def test_invalid_static_data_members_are_ordinary_errors(source, code):
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
    ('struct S{static const int K=42;};', 'cpp.unsupported.static-data-members'),
    ('struct S{static int a[2];};', 'cpp.unsupported.static-data-members'),
    ('struct T{int x;};struct S{static T t;};', 'cpp.unsupported.static-data-members'),
    ('namespace{struct S{static int v;};}', 'cpp.unsupported.static-data-members'),
    ('struct S{static int v, w;};', 'cpp.unsupported.init-declarator-list'),
    ('struct S{static int v;};int f();int S::v=f();', 'cpp.unsupported.dynamic-initialization'),
))
def test_remaining_static_member_shapes_stay_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, 'gap.cpp')
    assert error.value.code == code


def test_odr_use_without_a_definition_fails_at_link_like_a_real_toolchain(owned_only):
    from pcc.backend.elf_x86_64 import ElfError
    with pytest.raises(ElfError, match='undefined static ELF symbols: _ZN1S1vE'):
        compile_host_cpp((('s.cpp', 'struct S{static int v;};int cpp_test_entry(){return S::v;}'),))
