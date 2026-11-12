"""Original tests of single non-virtual bases of non-polymorphic classes.

N4659 [class.derived], [class.member.lookup], [class.access.base]/1-5,
[class.protected], [conv.ptr]/3, [dcl.init.ref]/4-5, [over.ics.rank]/4.4 and
[expr.sizeof], [class.base.init]/2,13 and [class.dtor]/9; Itanium C++ ABI 2.4
(base at offset 0, empty-base optimization, no tail-padding reuse of a POD
base). User-provided derived constructors construct the base first and
destructors destroy it last. Virtual and multiple bases and implicit derived
special members stay explicit capability boundaries; polymorphic bases are
covered by test_cpp_polymorphic_inheritance.py.
"""
import os
import shutil
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic
from pcc.frontends.cpp.driver import preprocess_cpp

TARGET = "x86_64-unknown-linux-gnu"

PROGRAMS = {
    "inherited-data-and-method":
        "struct B{int x;int get()const{return x;}};struct D:B{int y;};"
        "int main(){D d;d.x=40;d.y=2;return d.get()+d.y;}",
    "unqualified-base-members-in-derived":
        "struct B{int x;int twice(){return x*2;}};struct D:B{int y;int sum(){return twice()+y;}};"
        "int main(){D d;d.x=20;d.y=2;return d.sum();}",
    "protected-base-member":
        "class B{protected:int x;};class D:public B{public:int set(){x=42;return x;}};"
        "int main(){D d;return d.set();}",
    "private-inheritance-inside":
        "struct B{int x;};class D:B{public:int put(){x=42;return x;}};int main(){D d;return d.put();}",
    "pointer-conversions":
        "struct B{int x;};struct D:B{int y;};int f(B*b){return b->x;}int g(const B*b){return b->x;}"
        "int main(){D d;d.x=21;d.y=0;B*b=&d;const D*c=&d;return f(&d)+g(c)+b->x-21;}",
    "reference-binding":
        "struct B{int x;};struct D:B{int y;};int main(){D d;d.x=42;d.y=1;B&r=d;const B&c=d;return r.x+c.x-42;}",
    "three-level-chain":
        "struct A{int a;};struct B:A{int b;};struct C:B{int c;};"
        "int main(){C v;v.a=40;v.b=1;v.c=1;A*p=&v;B*q=&v;return p->a+q->b+v.c;}",
    "name-hiding":
        "struct B{int x;int base_x(){return x;}};struct D:B{int x;};"
        "int main(){D d;d.x=2;B*b=&d;b->x=40;return d.x+d.base_x();}",
    "overload-base-over-void":
        "struct B{int x;};struct D:B{};int f(B*){return 42;}int f(void*){return 0;}int main(){D d;return f(&d);}",
    "overload-nearest-base":
        "struct A{int a;};struct B:A{};struct D:B{};int f(A*){return 0;}int f(B*){return 42;}"
        "int main(){D d;return f(&d);}",
    "empty-base-optimization":
        "struct E{};struct D:E{int y;};struct F:E{};int main(){return sizeof(D)*10+sizeof(F);}",
    "pod-base-tail-padding-kept":
        "struct B{int i;char c;};struct D:B{char d;};int main(){return sizeof(D)+30;}",
    "non-pod-base-without-tail-padding":
        "class B{int x;public:int y;};struct D:B{int z;};int main(){return sizeof(D)+30;}",
    "non-pod-base-ending-at-its-size":
        "class B{int x;char c;public:int y;};struct D:B{char d;};int main(){return sizeof(D)+26;}",
    "derived-without-members-of-non-pod-base":
        "class B{int x;char c;public:int get(){return 42;}};struct D:B{};int main(){D d;return d.get()+sizeof(D)-8;}",
    "base-static-member-function":
        "struct B{static int k(){return 42;}};struct D:B{};int main(){return D::k();}",
    # [class.base.init]/13 and [class.dtor]/9: base first in, last out.
    "base-default-constructor":
        "struct B{int x;B(){x=40;}};struct D:B{int y;D(){y=2;}};int main(){D d;return d.x+d.y;}",
    "defaulted-derived-with-trivial-base":
        "struct B{int x;};struct D:B{int y=2;D()=default;};int main(){D d;d.x=40;return d.x+d.y;}",
    "base-mem-initializer":
        "struct B{int x;B(int v){x=v;}};struct D:B{int y;D(int a):B(a),y(2){}};int main(){D d(40);return d.x+d.y;}",
    "destruction-order":
        "int log=0;struct B{int x;B(){x=1;}~B(){log=log*10+2;}};struct D:B{D(){}~D(){log=log*10+1;}};"
        "int main(){{D d;}return log;}",
    "constructor-chain":
        "struct A{int a;A(){a=10;}};struct B:A{int b;B(){b=30;}};struct C:B{int c;C(){c=2;}};"
        "int main(){C v;return v.a+v.b+v.c;}",
    "empty-base-constructor":
        "int n=0;struct E{E(){n=42;}};struct D:E{int y;D(){y=0;}};int main(){D d;return n+d.y;}",
    "protected-base-destructor":
        "int n=0;struct B{protected:~B(){n=42;}public:B(){}};struct D:B{D(){}~D(){}};int main(){{D d;}return n;}",
    "base-copy-from-derived":
        "struct B{int x;B(int v){x=v;}B(const B&o){x=o.x+1;}};struct D:B{D(int v):B(v){}D(const D&o):B(o){}};"
        "int main(){D a(41);D b(a);return b.x;}",
}
EXPECTED = {
    "empty-base-optimization": 41,
    "destruction-order": 12,
}
INVALID = {
    "private-member-of-base": ("class B{int x;};struct D:B{int f(){return x;}};", "cpp.member-access"),
    "private-inheritance-outside": ("struct B{int x;};class D:B{};int f(){D d;return d.x;}", "cpp.member-access"),
    "private-base-conversion": ("struct B{int x;};class D:B{};int f(D*d){B*b=d;return 0;}", "cpp.inaccessible-base"),
    "protected-through-base-object":
        ("class B{protected:int x;};struct D:B{int f(B*b){return b->x;}};", "cpp.member-access"),
    "dropping-const": ("struct B{int x;};struct D:B{};int f(const D*d){B*b=d;return 0;}", "cpp.invalid-conversion"),
    "base-is-not-a-class": ("int B;struct D:B{};", "cpp.invalid-base"),
    "incomplete-base": ("struct B;struct D:B{};", "cpp.incomplete-class"),
    "private-base-constructor": ("class B{B(){}};struct D:B{D(){}};", "cpp.member-access"),
    "duplicate-base-initializer": ("struct B{B(int){}};struct D:B{D():B(1),B(2){}};", "cpp.duplicate-member-initializer"),
}
GAPS = {
    "virtual-base": "struct B{};struct D:virtual B{};",
    "multiple-bases": "struct A{};struct B{};struct D:A,B{};",
    "polymorphic-derived": "struct B{int x;};struct D:B{virtual int f(){return 1;}};",
    "value-initialized-constructorless-base": "struct B{int x;};struct D:B{D():B(){}};",
    "base-default-member-initializer": "struct B{int x=40;};struct D:B{D(){}};",
    "tail-padding-reuse": "class B{int x;char c;public:int get(){return x;}};struct D:B{char d;};",
    "empty-base-at-member-address": "struct E{};struct D:E{E e;int y;};",
    "qualified-member-access": "struct B{int x;};struct D:B{int x;};int f(){D d;return d.B::x;}",
}


def lower(source):
    from pcc.frontends.c.evaluator.c_evaluator import _compile_preprocessed_translation_unit_artifact
    text = preprocess_cpp(source, target_triple=TARGET, source_name="original.cpp")
    return _compile_preprocessed_translation_unit_artifact("original.cpp", text, target_triple=TARGET)["ir_text"]


@pytest.mark.parametrize("name", PROGRAMS)
def test_inheritance_originals_lower_to_ir(name):
    assert "define" in lower(PROGRAMS[name])


def test_nonempty_base_is_the_leading_field_and_empty_base_has_no_storage():
    ir = lower("struct B{int x;char c;};struct D:B{char d;};struct E{};struct F:E{int y;};"
               "int f(D*d,F*g){return d->d+g->y;}")
    types = {line.split(" = type ")[0].rsplit("_", 1)[-1]: line.split(" = type ")[1]
             for line in ir.splitlines() if " = type {" in line and "__pcc_cpp_class_1" in line}
    assert types["1D"].startswith("{ %") and types["1D"].endswith("__pcc_cpp_class_1B, i8 }")
    assert types["1F"] == "{ i32 }" and types["1E"] == "{ i8 }"


@pytest.mark.parametrize("name", INVALID)
def test_invalid_inheritance_originals_are_ordinary_errors(name):
    source, code = INVALID[name]
    with pytest.raises(CppDiagnostic) as caught:
        lower(source)
    assert caught.value.code == code


# Implicit special members of a derived class are not synthesized.
SYNTHESIS_GAPS = {
    "implicit-derived-constructor": ("struct B{int x;B(){x=1;}};struct D:B{};int f(){D d;return d.x;}",
                                     "cpp.unsupported.constructor-subobjects"),
    "implicit-derived-destructor": ("struct B{B(){}~B(){}};struct D:B{D(){}};",
                                    "cpp.unsupported.destructor-subobjects"),
}


@pytest.mark.parametrize("name", SYNTHESIS_GAPS)
def test_implicit_derived_special_members_stay_explicit_gaps(name):
    source, code = SYNTHESIS_GAPS[name]
    with pytest.raises(CppDiagnostic) as caught:
        lower(source)
    assert caught.value.code == code


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_inheritance_keeps_an_explicit_capability_code(name):
    with pytest.raises(CppDiagnostic) as caught:
        lower(GAPS[name])
    assert caught.value.code == "cpp.unsupported.base-classes"


def oracle():
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    return compiler


@pytest.mark.integration
@pytest.mark.parametrize("name", PROGRAMS)
def test_inheritance_originals_execute_against_explicit_cpp17_reference(name, tmp_path, monkeypatch):
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
@pytest.mark.parametrize("name", [*INVALID, *GAPS, *SYNTHESIS_GAPS])
def test_explicit_reference_rejects_invalid_and_accepts_gap_originals(name, tmp_path):
    source = (INVALID[name][0] if name in INVALID else SYNTHESIS_GAPS[name][0] if name in SYNTHESIS_GAPS
              else GAPS[name])
    unit = tmp_path / "original.cpp"
    unit.write_text(source)
    result = subprocess.run([oracle(), "-std=c++17", "-pedantic-errors", "-fsyntax-only", str(unit)],
                            capture_output=True, timeout=30)
    assert (result.returncode != 0) == (name in INVALID), result.stderr
