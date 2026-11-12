"""Original tests of virtual members and polymorphic bases of class templates.

N4659 [temp.dep]/3, [class.virtual]/2,4,7-8, [class.abstract], [class.cdtor]/4,
[class.base.init]/13, [class.dtor]/9 and [temp.inst]/1 (virtual members of a
specialization are instantiated with its definition); Itanium C++ ABI 2.4
and 2.5.2. Each specialization declares its own overriders and owns its own
table built by virtual_functions.py, so every final overrider is ODR-used.
A nondependent signature's override rules are checked at the definition, a
dependent one by each specialization. Construction of a base that needs
destruction uses the same closed-construction proof as ordinary classes.
Defaulted and implicit virtual destructors, virtual operators and base
members named inside member bodies stay explicit capability boundaries.
"""
import os
import re
import shutil
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic
from pcc.frontends.cpp.driver import preprocess_cpp

TARGET = "x86_64-unknown-linux-gnu"

PROGRAMS = {
    "override-through-base-pointer":
        "struct B{B(){} virtual int f(){return 1;}};template<int N> struct D:B{D(){} int f() override{return N;}};"
        "int main(){D<42> d;B*b=&d;return b->f();}",
    "specializations-own-their-tables":
        "struct B{B(){} virtual int f(){return 0;}};template<int N> struct D:B{D(){} int f() override{return N;}};"
        "int call(B*b){return b->f();}int main(){D<40> a;D<2> c;return call(&a)+call(&c);}",
    "pure-interface":
        "struct I{virtual int size()const=0;};template<int N> struct S:I{S(){} int size()const override{return N*2;}};"
        "int main(){S<21> s;const I*i=&s;return i->size();}",
    "implicit-overrider-without-keyword":
        "struct B{B(){} virtual void set(){}};template<int N> struct D:B{int v;D(){v=0;} void set(){v=N;}"
        " int get(){return v;}};int main(){D<42> d;B*b=&d;b->set();return d.get();}",
    "virtual-base-destructor-order":
        "struct B{int*p;B(){} virtual ~B(){*p=*p*10+2;}};template<int N> struct D:B{int*q;D(){} ~D(){*q=*q*10+N;}};"
        "int main(){int log=0;{D<1> d;d.p=&log;d.q=&log;}return log+30;}",
    "new-virtual-in-template":
        "struct B{B(){} virtual int f(){return 40;}};template<int N> struct D:B{D(){} virtual int g(){return N;}"
        " int twice(){return g()*2;}};int main(){D<1> d;B*b=&d;return b->f()+d.twice();}",
    "dependent-signature-override":
        "struct B{B(){} virtual long f(){return 0;}};template<class T> struct D:B{D(){} T f() override{return 42;}};"
        "int main(){D<long> d;B*b=&d;return (int)b->f();}",
    "virtual-template-without-base":
        "template<int N> struct D{D(){} virtual int f(){return N;}};int main(){D<42> d;return d.f();}",
    "construction-dispatch":
        "struct B{int seen;B(){seen=f();} virtual int f(){return 40;}};template<int N> struct D:B{D(){} int f() override{return N;}};"
        "int main(){D<2> d;B*b=&d;return d.seen+b->f();}",
}
EXPECTED = {"virtual-base-destructor-order": 42}
INVALID = {
    "override-without-overridden-member":
        ("struct B{B(){} virtual int f(){return 1;}};template<int N> struct D:B{D(){} int g() override{return 1;}};"
         "int main(){D<1> d;return 0;}", "cpp.invalid-override"),
    "overriding-final-member":
        ("struct B{B(){} virtual int f() final{return 1;}};template<int N> struct D:B{D(){} int f(){return 2;}};"
         "int main(){D<1> d;return 0;}", "cpp.override-final"),
    "different-return-type":
        ("struct B{B(){} virtual int f(){return 1;}};template<int N> struct D:B{D(){} long f(){return 2;}};"
         "int main(){D<1> d;return 0;}", "cpp.invalid-override-return"),
    "static-overrider":
        ("struct B{B(){} virtual int f(){return 1;}};template<int N> struct D:B{D(){} static int f(){return 2;}};"
         "int main(){D<1> d;return 0;}", "cpp.invalid-virtual"),
    "virtual-constructor": ("template<int N> struct D{virtual D(){}};", "cpp.invalid-virtual"),
    "virtual-data-member": ("template<int N> struct D{virtual int x;};", "cpp.invalid-virtual"),
    "dependent-override-without-target":
        ("struct B{B(){} virtual int f(){return 1;}};template<class T> struct D:B{D(){} T g() override{return 1;}};"
         "int main(){D<int> d;return 0;}", "cpp.invalid-override"),
    "object-of-abstract-specialization":
        ("struct I{virtual int f()=0;};template<int N> struct D:I{D(){}};int main(){D<1> d;return 0;}",
         "cpp.abstract-class"),
    "destructor-override-without-virtual-base":
        ("struct B{B(){} virtual int f(){return 1;}};template<int N> struct D:B{D(){} ~D() override{}};"
         "int main(){D<1> d;return 0;}", "cpp.invalid-override"),
}
GAPS = {
    "implicit-virtual-destructor":
        ("struct B{B(){} virtual ~B(){}};template<int N> struct D:B{D(){}};int main(){D<1> d;return 0;}",
         "cpp.unsupported.virtual-functions"),
    "defaulted-virtual-destructor":
        ("template<int N> struct D{D(){} virtual ~D()=default;};", "cpp.unsupported.member-templates"),
    "virtual-operator":
        ("template<int N> struct D{D(){} virtual int operator[](int i){return i;}};int main(){D<1> d;return 0;}",
         "cpp.unsupported.virtual-functions"),
    "unproven-construction-with-base-destructor":
        ("struct B{B(){} virtual ~B(){}};int g(){return 1;}"
         "template<int N> struct D:B{int v;D(){v=g();} ~D(){}};int main(){D<1> d;return d.v;}",
         "cpp.unsupported.destructor-subobjects"),
}


def lower(source):
    from pcc.frontends.c.evaluator.c_evaluator import _compile_preprocessed_translation_unit_artifact
    text = preprocess_cpp(source, target_triple=TARGET, source_name="original.cpp")
    return _compile_preprocessed_translation_unit_artifact("original.cpp", text, target_triple=TARGET)["ir_text"]


@pytest.mark.parametrize("name", PROGRAMS)
def test_virtual_class_template_originals_lower_to_ir(name):
    assert "define" in lower(PROGRAMS[name])


def test_each_specialization_owns_a_table_of_its_final_overriders():
    ir = lower(PROGRAMS["specializations-own-their-tables"])
    tables = [line for line in ir.splitlines() if line.startswith("@") and "vtable_object" in line]
    assert len(tables) == 3  # B, D<40> and D<2>.
    entries = [re.findall(r"ptr (@\w+|null)", line.split(" global ", 1)[1]) for line in tables]
    overriders = sorted(entry[0] for entry in entries)
    assert overriders[0] == "@_ZN1B1fEv"
    assert all(name.startswith("@_ZN1DILi") and name.endswith("EE1fEv") for name in overriders[1:])
    assert overriders[1] != overriders[2]


@pytest.mark.parametrize("name", INVALID)
def test_invalid_virtual_class_template_originals_are_ordinary_errors(name):
    source, code = INVALID[name]
    with pytest.raises(CppDiagnostic) as caught:
        lower(source)
    assert caught.value.code == code


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_virtual_class_template_shapes_keep_an_explicit_capability_code(name):
    source, code = GAPS[name]
    with pytest.raises(CppDiagnostic) as caught:
        lower(source)
    assert caught.value.code == code


def oracle():
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    return compiler


@pytest.mark.integration
@pytest.mark.parametrize("name", PROGRAMS)
def test_virtual_class_template_originals_execute_against_explicit_cpp17_reference(name, tmp_path, monkeypatch):
    from tests.cpp.owned_execution import Execution
    from tests.cpp.test_cpp_class_execution import record_execution
    from tests.cpp.test_cpp_cerrno_ctime_headers import owned_executable
    source = PROGRAMS[name]
    expected = Execution(EXPECTED.get(name, 42), b"", b"")
    owned = tmp_path / "owned"
    owned.write_bytes(owned_executable(source, monkeypatch))
    owned.chmod(0o700)
    record_execution(owned, expected)
    unit = tmp_path / "original.cpp"
    unit.write_text(source)
    reference = tmp_path / "oracle"
    result = subprocess.run([oracle(), "-std=c++17", "-pedantic-errors", "-O0", str(unit), "-o", str(reference)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    record_execution(reference, expected)


@pytest.mark.integration
@pytest.mark.parametrize("name", [*INVALID, *GAPS])
def test_explicit_reference_rejects_invalid_and_accepts_gap_originals(name, tmp_path):
    source = INVALID[name][0] if name in INVALID else GAPS[name][0]
    unit = tmp_path / "original.cpp"
    unit.write_text(source)
    result = subprocess.run([oracle(), "-std=c++17", "-pedantic-errors", "-fsyntax-only", str(unit)],
                            capture_output=True, timeout=30)
    assert (result.returncode != 0) == (name in INVALID), result.stderr
