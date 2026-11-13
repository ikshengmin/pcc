"""Protected base constructors selected by a derived class's mem-initializer.

N4659 [class.access.base]/5, [class.protected], [class.base.init]/2.
https://timsong-cpp.github.io/cppwp/n4659/class.access.base#5
A mem-initializer `D(...) : B(args)` constructs the base subobject of `*this`,
an object of the derived class, so a protected (or, through a private base,
inherited) constructor of B is accessible in D's constructor, as a protected
member named through `this` is. Creating a separate B object in a member of D,
or anywhere outside B, still needs B's constructor to be accessible there.
TinyXML2's `XMLText(XMLDocument* doc) : XMLNode(doc)` (projects/tinyxml2-11.0.0,
Zlib, tinyxml2.h:1019 with the class head at 993) has this shape; the programs
below are original.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.darwin_execution import (
    REFERENCE_UNAVAILABLE, UNAVAILABLE, reference_rejects, run_owned_cpp, run_reference_cpp,
)
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

PROGRAMS = (
    ('inline-mem-initializer', (('s.cpp',
     'class B{protected:int v;B(int n):v(n){}};class D:public B{public:D(int n):B(n+2){}'
     'int get()const{return v;}};int cpp_test_entry(){D d(40);return d.get();}'),)),
    ('out-of-class-definitions', (('s.cpp',
     'class B{protected:int v;explicit B(int*p);};B::B(int*p):v(*p){}'
     'class D:public B{public:explicit D(int*p);int get(){return v;}};D::D(int*p):B(p){}'
     'int cpp_test_entry(){int n=42;D d(&n);return d.get();}'),)),
    ('private-base', (('s.cpp',
     'class B{protected:int v;B(int n):v(n){}};class D:B{public:D():B(42){}int get(){return v;}};'
     'int cpp_test_entry(){D d;return d.get();}'),)),
    # The XMLNode/XMLText shape: protected explicit constructor and virtual
    # destructor in a polymorphic base, a further member, an override.
    ('polymorphic-protected-constructor-and-destructor', (('s.cpp',
     'class B{protected:long v;explicit B(long n):v(n){}virtual ~B(){}public:virtual int get()const{return int(v);}};'
     'class D:public B{public:explicit D(int n):B(n),w(2){}virtual ~D(){}int get()const override{return int(v)+w;}'
     'private:int w;};int cpp_test_entry(){D d(40);B* b=&d;return b->get();}'),)),
    ('protected-through-two-levels', (('s.cpp',
     'class A{protected:int a;A(int n):a(n){}};class B:public A{protected:B(int n):A(n){}};'
     'class D:public B{public:D():B(42){}int get(){return a;}};int cpp_test_entry(){D d;return d.get();}'),)),
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


INVALID = (
    # A private constructor stays private to the derived class.
    'class B{B(int){}};class D:public B{public:D():B(1){}};',
    # A separate B object is not a base subobject of *this ([class.protected]).
    'class B{protected:B(int){}};class D:public B{public:void f(){B b(1);}};',
    'class B{protected:B(int){}};int f(){B b(1);return 0;}',
)


@pytest.mark.parametrize('source', INVALID)
def test_inaccessible_constructors_are_ordinary_errors(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, 'invalid.cpp')
    assert error.value.code == 'cpp.member-access'


@pytest.mark.xdist_group(name='cpp-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_compiler_rejects_the_same_sources(tmp_path):
    for index, source in enumerate(INVALID):
        directory = tmp_path / str(index)
        directory.mkdir()
        rejected, stderr = reference_rejects((('invalid.cpp', source),), directory)
        assert rejected, (source, stderr)
