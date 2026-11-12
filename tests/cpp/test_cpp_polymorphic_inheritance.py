"""Original tests of overriding through a single polymorphic non-virtual base.

N4659 [class.virtual]/2,4,7-8 (overriders, final, covariant returns),
[class.abstract]/2-3, [class.cdtor]/4, [class.base.init]/13, [class.dtor]/9,14
and [class.mem] (virt-specifiers); Itanium C++ ABI 2.4 (a dynamic single
base is the primary base: offset 0, shared table pointer) and 2.5.2 (the
primary base's entries first, overriders in place, then the new virtual
functions in declaration order). Implicit or defaulted virtual destructors,
implicit constructors, slicing copies, a nonempty non-dynamic base under a
new table pointer and tail-padding reuse stay explicit capability boundaries.
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
        "struct B{B(){} virtual int f(){return 1;}};struct D:B{D(){} int f(){return 42;}};"
        "int main(){D d;B*b=&d;return b->f();}",
    "base-member-dispatches-to-overrider":
        "struct B{B(){} virtual int f(){return 1;} int call(){return f();}};"
        "struct D:B{D(){} int f() override{return 42;}};int main(){D d;return d.call();}",
    "reference-dispatch":
        "struct B{B(){} virtual int f()const{return 1;}};struct D:B{D(){} int f()const override{return 42;}};"
        "int main(){D d;const B&r=d;return r.f();}",
    # [class.cdtor]/4: the base constructor sees the base's overrider.
    "construction-dispatch":
        "int seen=0;struct B{B(){seen=f();} virtual int f(){return 40;}};"
        "struct D:B{D(){seen+=f();} int f() override{return 2;}};int main(){D d;return seen;}",
    "member-initializer-sees-own-table":
        "struct B{B(){} virtual int f(){return 1;}};struct D:B{int v;D():v(f()){} int f(){return 42;}};"
        "int main(){D d;return d.v;}",
    "new-virtual-in-derived":
        "struct B{B(){} virtual int f(){return 40;}};struct D:B{D(){} virtual int g(){return 2;}};"
        "struct E:D{E(){} int g(){return 1;}};int main(){E e;D*d=&e;B*b=&e;return b->f()+d->g()+1;}",
    "inherited-final-overrider":
        "struct A{A(){} virtual int f(){return 1;}};struct B:A{B(){}};struct C:B{C(){} int f(){return 42;}};"
        "int main(){C c;A*a=&c;B*b=&c;return a->f()+b->f()-42;}",
    "abstract-interface":
        "struct Shape{virtual int area()const=0;virtual ~Shape(){}};"
        "struct Square:Shape{int s;Square(int v){s=v;} int area()const override{return s*s;} ~Square(){}};"
        "int twice(const Shape*x){return 2*x->area();}int main(){Square q(3);return twice(&q)+24;}",
    "qualified-base-call":
        "struct B{B(){} virtual int f(){return 40;}};struct D:B{D(){} int f(){return B::f()+2;}};"
        "int main(){D d;B&b=d;return b.f();}",
    "covariant-return":
        "struct B{B(){} virtual B*self(){return this;} virtual int id(){return 1;}};"
        "struct D:B{D(){} D*self() override{return this;} int id(){return 42;}};"
        "int main(){D d;B*b=&d;return b->self()->id();}",
    "final-overrider":
        "struct B{B(){} virtual int f(){return 1;}};struct D:B{D(){} int f() final{return 42;}};"
        "int main(){D d;B*b=&d;return b->f();}",
    "virtual-destructor-order":
        "int log=0;struct B{B(){} virtual ~B(){log=log*10+2;}};struct D:B{D(){} ~D(){log=log*10+1;}};"
        "int main(){{D d;}return log;}",
    "primary-base-layout":
        "struct B{B(){} virtual int f(){return 0;}};struct D:B{int x;D(){}};int main(){return sizeof(D)*2+10;}",
    "empty-base-under-new-table-pointer":
        "struct E{int k(){return 2;}};struct D:E{D(){} virtual int f(){return 40;}};"
        "int main(){D d;E*e=&d;return d.f()+e->k()+sizeof(D)-8;}",
}
EXPECTED = {"virtual-destructor-order": 12}
INVALID = {
    "override-without-overridden-member":
        ("struct B{B(){} virtual int f(){return 0;}};struct D:B{D(){} int f(int) override{return 1;}};",
         "cpp.invalid-override"),
    "destructor-override-without-virtual-base":
        ("struct B{B(){} virtual int f(){return 0;} ~B(){}};struct D:B{D(){} ~D() override{}};",
         "cpp.invalid-override"),
    "overriding-final-member":
        ("struct B{B(){} virtual int f() final{return 0;}};struct D:B{D(){} int f(){return 1;}};",
         "cpp.override-final"),
    "overriding-final-destructor":
        ("struct B{B(){} virtual ~B() final{}};struct D:B{D(){} ~D(){}};", "cpp.override-final"),
    "different-return-type":
        ("struct B{B(){} virtual int f(){return 0;}};struct D:B{D(){} long f(){return 1;}};",
         "cpp.invalid-override-return"),
    "covariant-unrelated-class":
        ("struct X{};struct B{B(){} virtual B*f(){return this;}};struct D:B{D(){} X*f(){return 0;}};",
         "cpp.invalid-override-return"),
    "covariant-adds-const":
        ("struct B{B(){} virtual B*f(){return this;}};struct D:B{D(){} const D*f(){return this;}};",
         "cpp.invalid-override-return"),
    "covariant-private-base":
        ("struct R{};struct S:private R{};struct B{B(){} virtual R*f(){return 0;}};"
         "struct D:B{D(){} S*f(){return 0;}};", "cpp.invalid-override-return"),
    "static-member-overriding":
        ("struct B{B(){} virtual int f(){return 0;}};struct D:B{D(){} static int f(){return 1;}};",
         "cpp.invalid-virtual"),
    "object-of-abstract-derived":
        ("struct B{virtual int f()=0;};struct D:B{D(){}};int g(){D d;return 0;}", "cpp.abstract-class"),
}
GAPS = {
    "implicit-virtual-destructor":
        ("struct B{B(){} virtual ~B(){}};struct D:B{D(){}};", "cpp.unsupported.virtual-functions"),
    "defaulted-virtual-destructor":
        ("struct B{B(){} virtual ~B(){}};struct D:B{D(){} ~D()=default;};", "cpp.unsupported.virtual-functions"),
    "implicit-derived-constructor":
        ("struct B{B(){} virtual int f(){return 1;}};struct D:B{};", "cpp.unsupported.virtual-functions"),
    "nonempty-non-dynamic-base":
        ("struct B{int x;};struct D:B{D(){} virtual int f(){return 1;}};", "cpp.unsupported.base-classes"),
    "dynamic-base-tail-padding":
        ("struct B{B(){} virtual int f(){return 1;} int x;};struct D:B{int y;D(){}};",
         "cpp.unsupported.base-classes"),
    "slicing-copy":
        ("struct B{B(){} virtual int f(){return 1;}};struct D:B{D(){}};int g(){D d;B b=d;return b.f();}",
         "cpp.unsupported.class-initialization"),
}


def lower(source):
    from pcc.frontends.c.evaluator.c_evaluator import _compile_preprocessed_translation_unit_artifact
    text = preprocess_cpp(source, target_triple=TARGET, source_name="original.cpp")
    return _compile_preprocessed_translation_unit_artifact("original.cpp", text, target_triple=TARGET)["ir_text"]


@pytest.mark.parametrize("name", PROGRAMS)
def test_polymorphic_inheritance_originals_lower_to_ir(name):
    assert "define" in lower(PROGRAMS[name])


TABLES = ("struct B{B(){} virtual int f(){return 1;} virtual int g(){return 2;} virtual ~B(){}};"
          "struct D:B{int x;D(){} int g(){return 3;} virtual int h(){return 4;} ~D(){}};"
          "void kill(B*b){b->~B();}int call(D*d){return d->h()+d->g();}")


def table_object(ir, tag):
    line, = [line for line in ir.splitlines() if line.startswith("@") and "vtable_object" in line
             and line.split(" =")[0].endswith("_" + tag)]
    return re.findall(r"ptr (@\w+|null)", line.split(" global ", 1)[1])


def function_body(ir, name):
    return re.search(r"define [^\n]*@%s\([^\n]*\{\n(.*?)\n\}" % re.escape(name), ir, re.S).group(1)


def test_derived_table_extends_the_primary_base_table_in_place():
    ir = lower(TABLES)
    record = [line for line in ir.splitlines() if " = type {" in line and line.split(" =")[0].endswith("class_1D")]
    assert record and record[0].split(" = type ")[1].endswith("__pcc_cpp_class_1B, i32 }")
    assert table_object(ir, "1B") == ["@_ZN1B1fEv", "@_ZN1B1gEv", "@_ZN1BD1Ev", "null"]
    assert table_object(ir, "1D") == ["@_ZN1B1fEv", "@_ZN1D1gEv", "@_ZN1DD1Ev", "null", "@_ZN1D1hEv"]


def test_construction_and_destruction_store_the_class_table():
    ir = lower(TABLES)
    base_table = "@__pcc_internal_original_cpp___pcc_cpp_vtable_object___pcc_cpp_class_1B"
    derived_table = "@__pcc_internal_original_cpp___pcc_cpp_vtable_object___pcc_cpp_class_1D"
    constructor = function_body(ir, "_ZN1DC1Ev")
    assert constructor.index("@_ZN1BC1Ev") < constructor.index("store ptr " + derived_table)
    assert "store ptr " + base_table in function_body(ir, "_ZN1BD1Ev")
    assert "store ptr " + derived_table in function_body(ir, "_ZN1DD1Ev")


def test_unqualified_calls_and_explicit_destruction_load_the_final_overrider():
    ir = lower(TABLES)
    kill = function_body(ir, "_Z4killP1B")
    assert "@_ZN1BD1Ev" not in kill and re.search(r"call void \(ptr\) %\.\d+\(", kill)
    call = function_body(ir, "_Z4callP1D")
    assert "@_ZN1D1hEv" not in call and "@_ZN1D1gEv" not in call
    assert call.count("vtable___pcc_cpp_class_1D") == 2


@pytest.mark.parametrize("name", INVALID)
def test_invalid_overriding_originals_are_ordinary_errors(name):
    source, code = INVALID[name]
    with pytest.raises(CppDiagnostic) as caught:
        lower(source)
    assert caught.value.code == code


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_polymorphic_shapes_keep_an_explicit_capability_code(name):
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
def test_polymorphic_originals_execute_against_explicit_cpp17_reference(name, tmp_path, monkeypatch):
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
