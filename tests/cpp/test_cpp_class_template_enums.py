"""Original tests of unnamed unscoped member enums of class templates.

N4659 [dcl.enum]/2-5, [class.mem], [temp.dep.constexpr]/2 (an enumerator of
the current instantiation is value-dependent when its initializer is),
[temp.inst]/1 and [basic.lookup.qual]. Each specialization builds its own
enum with the ordinary owner (values, implicit successors, fixed or selected
underlying type); member bodies, array bounds, member access and
`S<N>::K` name that specialization's enumerators. Named and scoped member
enums and enum object declarators stay explicit capability boundaries.
"""
import os
import shutil
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic
from pcc.frontends.cpp.driver import preprocess_cpp

TARGET = "x86_64-unknown-linux-gnu"

PROGRAMS = {
    "value-dependent-enumerator-in-body":
        "template<unsigned long N> struct P{enum{PER=(4*1024)/N}; int per(){return PER;}};"
        "int main(){P<128> p;return p.per()+10;}",
    "tinyxml2-items-per-block-shape":
        "template<int ITEM_SIZE> class Pool{public:enum{ITEMS_PER_BLOCK=(4*1024)/ITEM_SIZE};};"
        "int main(){return Pool<128>::ITEMS_PER_BLOCK+10;}",
    "enumerator-in-loop-bound":
        "template<int N> struct P{enum{K=N*2}; int f(){int t=0;for(int i=0;i<K-1;++i)t+=2;return t;}};"
        "int main(){P<11> p;return p.f();}",
    "successor-and-earlier-enumerator":
        "template<int N> struct P{enum{A=N,B,C=B*2}; int f(){return A+B+C;}};int main(){P<6> p;return p.f()+15;}",
    "member-array-bound":
        "template<int N> struct P{enum{K=N+1}; int a[K]; int f(){return sizeof(a);}};int main(){P<4> p;return p.f()+22;}",
    "qualified-through-template-id": "template<int N> struct P{enum{K=N*2};};int main(){return P<21>::K;}",
    "object-member-access": "template<int N> struct P{enum{K=N*2};};int main(){P<21> p;return p.K;}",
    "specializations-own-their-values": "template<int N> struct P{enum{K=N};};int main(){return P<40>::K+P<2>::K;}",
    "fixed-underlying-type":
        "template<int N> struct P{enum:unsigned char{K=N}; int f(){return K;}};int main(){P<42> p;return p.f();}",
    "nondependent-enumerator":
        "template<int N> struct P{enum{K=42}; int f(){return K;}};int main(){P<1> p;return p.f();}",
}
INVALID = {
    "duplicate-enumerator": ("template<int N> struct P{enum{K=1,K=2};};", "cpp.redeclaration"),
    # g++ diagnoses this nondependent initializer only on instantiation;
    # [temp.res]/8 also permits the earlier definition-point diagnosis.
    "non-integral-initializer": ("template<int N> struct P{enum{K=1.5};};int main(){return P<1>::K;}",
                                 "cpp.enum-initializer-type"),
    "non-integral-base": ("template<int N> struct P{enum:double{K};};", "cpp.enum-underlying-type"),
    "value-outside-fixed-base":
        ("template<int N> struct P{enum:unsigned char{K=N}; int f(){return K;}};int main(){P<300> p;return p.f();}",
         "cpp.enum-value-range"),
    "private-enumerator-outside": ("template<int N> class P{enum{K=N};};int main(){return P<1>::K;}",
                                   "cpp.member-access"),
}
GAPS = {
    "named-member-enum": ("template<int N> struct P{enum E{K=N};};", "cpp.unsupported.enum-templates"),
    "scoped-member-enum": ("template<int N> struct P{enum class E{K};};", "cpp.unsupported.enum-templates"),
    "enum-object-declarator": ("template<int N> struct P{enum{K=N} k;};", "cpp.unsupported.enum-declarators"),
}


def lower(source):
    from pcc.frontends.c.evaluator.c_evaluator import _compile_preprocessed_translation_unit_artifact
    text = preprocess_cpp(source, target_triple=TARGET, source_name="original.cpp")
    return _compile_preprocessed_translation_unit_artifact("original.cpp", text, target_triple=TARGET)["ir_text"]


@pytest.mark.parametrize("name", PROGRAMS)
def test_class_template_enum_originals_lower_to_ir(name):
    assert "define" in lower(PROGRAMS[name])


def test_each_specialization_folds_its_own_enumerator_value():
    ir = lower("template<int N> struct P{enum{K=N*3}; int f(){return K;}};"
               "int a(){P<5> p;return p.f();}int b(){P<7> p;return p.f();}")
    bodies = {name: block for block in ir.split("define ")
              for name in ("@_ZN1PILi5EE1fEv(", "@_ZN1PILi7EE1fEv(") if name in block.split("\n", 1)[0]}
    assert set(bodies) == {"@_ZN1PILi5EE1fEv(", "@_ZN1PILi7EE1fEv("}
    assert "i32 15" in bodies["@_ZN1PILi5EE1fEv("] and "i32 21" in bodies["@_ZN1PILi7EE1fEv("]


@pytest.mark.parametrize("name", INVALID)
def test_invalid_class_template_enum_originals_are_ordinary_errors(name):
    source, code = INVALID[name]
    with pytest.raises(CppDiagnostic) as caught:
        lower(source)
    assert caught.value.code == code


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_class_template_enums_keep_an_explicit_capability_code(name):
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
def test_class_template_enum_originals_execute_against_explicit_cpp17_reference(name, tmp_path, monkeypatch):
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
