"""Original tests of a nondependent single base of a class template.

N4659 [temp.dep]/3 (a nondependent base is bound at the definition),
[class.derived], [class.base.init]/13, [class.dtor]/9, [conv.ptr]/3 and
[class.access.base]; Itanium C++ ABI 2.4 (base at offset 0, empty-base
optimization). Every specialization shares the base: layout, implicit and
declared construction, destruction and conversions reuse inheritance.py.
Naming base members inside member bodies, mem-initializers naming the base,
dependent, template-id and polymorphic bases, and base construction that
needs a destruction proof stay explicit capability boundaries.
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
        "struct B{int x;int get()const{return x;}};template<int N> struct D:B{int y;D(){y=N;} int own(){return y;}};"
        "int main(){D<2> d;d.x=40;return d.get()+d.own();}",
    "base-default-constructor":
        "struct B{int x;B(){x=40;}};template<int N> struct D:B{int y;D(){y=N;}};int main(){D<2> d;return d.x+d.y;}",
    "type-parameter-member-with-base":
        "struct B{int x;};template<class T> struct D:B{T y;};int f(B*b){return b->x;}"
        "int main(){D<long> d;d.x=42;d.y=0;return f(&d);}",
    "specializations-share-the-base":
        "struct B{int x;};template<int N> struct D:B{int y[N];};int f(B*b){return b->x;}"
        "int main(){D<1> a;D<3> c;a.x=40;c.x=2;B&r=c;return f(&a)+r.x+sizeof(D<3>)-16;}",
    "private-base-public-member":
        "struct B{int x;};template<int N> class D:B{public:int y;};int main(){D<1> d;d.y=42;return d.y;}",
    "empty-base":
        "struct E{int k()const{return 2;}};template<int N> struct D:E{int y;};"
        "int main(){D<1> d;d.y=40;return d.y+d.k()+sizeof(D<1>)-4;}",
}
INVALID = {
    "private-base-member-outside": ("struct B{int x;};template<int N> class D:B{};int f(){D<1> d;return d.x;}",
                                    "cpp.member-access"),
    "incomplete-base": ("struct B;template<int N> struct D:B{};", "cpp.incomplete-class"),
    "base-is-not-a-class": ("int B;template<int N> struct D:B{};", "cpp.invalid-base"),
}
GAPS = {
    "base-member-in-member-body":
        ("struct B{int x;};template<int N> struct D:B{int f(){return x;}};", "cpp.unsupported.class-templates"),
    "base-member-hides-an-outer-name":
        ("int x=3;struct B{int x;};template<int N> struct D:B{int f(){return x;}};",
         "cpp.unsupported.class-templates"),
    "base-member-through-this":
        ("struct B{int x;};template<int N> struct D:B{int f(){return this->x;}};", "cpp.unsupported.class-templates"),
    "dependent-base": ("template<class T> struct D:T{};", "cpp.unsupported.class-templates"),
    "template-id-base": ("template<int N> struct A{int x;};template<int N> struct D:A<N>{};",
                         "cpp.unsupported.base-classes"),
    "polymorphic-base": ("struct B{B(){} virtual int f(){return 1;}};template<int N> struct D:B{};",
                         "cpp.unsupported.class-templates"),
    "base-mem-initializer": ("struct B{int x;B(int v){x=v;}};template<int N> struct D:B{D():B(N){}};",
                             "cpp.unsupported.class-templates"),
    "base-with-destructor":
        ("struct B{B(){} ~B(){}};template<int N> struct D:B{D(){} ~D(){}};int main(){{D<1> d;}return 0;}",
         "cpp.unsupported.destructor-subobjects"),
}


def lower(source):
    from pcc.frontends.c.evaluator.c_evaluator import _compile_preprocessed_translation_unit_artifact
    text = preprocess_cpp(source, target_triple=TARGET, source_name="original.cpp")
    return _compile_preprocessed_translation_unit_artifact("original.cpp", text, target_triple=TARGET)["ir_text"]


@pytest.mark.parametrize("name", PROGRAMS)
def test_class_template_base_originals_lower_to_ir(name):
    assert "define" in lower(PROGRAMS[name])


def test_specialization_record_leads_with_the_shared_base():
    ir = lower("struct B{int x;char c;};template<int N> struct D:B{int y[N];};int f(D<2>*d){return d->y[1];}")
    record, = [line for line in ir.splitlines() if " = type {" in line and "class_template" in line]
    assert record.split(" = type ")[1].startswith("{ %") and "__pcc_cpp_class_1B, [2 x i32] }" in record


@pytest.mark.parametrize("name", INVALID)
def test_invalid_class_template_base_originals_are_ordinary_errors(name):
    source, code = INVALID[name]
    with pytest.raises(CppDiagnostic) as caught:
        lower(source)
    assert caught.value.code == code


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_class_template_bases_keep_an_explicit_capability_code(name):
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
def test_class_template_base_originals_execute_against_explicit_cpp17_reference(name, tmp_path, monkeypatch):
    from tests.cpp.owned_execution import Execution
    from tests.cpp.test_cpp_class_execution import record_execution
    from tests.cpp.test_cpp_cerrno_ctime_headers import owned_executable
    source = PROGRAMS[name]
    expected = Execution(42, b"", b"")
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
