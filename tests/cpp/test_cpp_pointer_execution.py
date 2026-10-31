"""Host-owned builtin pointer execution, separate from the G++17 oracle."""

import os
import platform
import re
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import (
    Execution,
    assert_exact,
    compile_host_cpp,
    compile_reference_cpp,
    run_executable,
    write_host_executable,
)
from tests.cpp.test_cpp_grouped_names import owned_only
from tests.cpp.test_cpp_pointers import (
    FORMER_UNSUPPORTED_ARRAY_SOURCE, FORMER_UNSUPPORTED_COMPOUND_SOURCE, NEGATIVE_CASES,
)


CASES = (
    ("previously-unsupported-pointer-compound-assignment",
     FORMER_UNSUPPORTED_COMPOUND_SOURCE + "int cpp_test_entry(){return f()+39;}", 42),
    ("previously-unsupported-array-declaration",
     FORMER_UNSUPPORTED_ARRAY_SOURCE + "int cpp_test_entry(){return f()+42;}", 42),
    ("fixed-array-elements-and-pointer-decay",
     "int cpp_test_entry(){int a[2];a[0]=17;a[1]=25;int*p=a;return p[0]+p[1];}", 42),
    ("address-dereference-mutation-return-call",
     "int *identity(int *p){return p;}int cpp_test_entry(){int x=4;int *p=&x;"
     "*identity(p)=37;return *p+x;}", 74),
    ("const-pointee-qualification-preserves-alias",
     "const int *view(int *p){return p;}int read(const int *p){return *p;}"
     "int cpp_test_entry(){int x=5;const int *p=view(&x);x=42;return read(p);}", 42),
    ("const-pointer-and-scalar-parameter-signatures",
     "int read(int *const);int read(int *p){return *p;}int add(const int);"
     "int add(const int x){return x+1;}int cpp_test_entry(){int x=40;"
     "int *const p=&x;*p=41;return add(read(p));}", 42),
    ("typedef-and-using-const-at-each-level",
     "typedef int (*P);using CP=const int *;using C=const int;"
     "int cpp_test_entry(){int x=14;const P p=&x;CP q=p;C y=3;C *r=&y;"
     "return *p+*q+*r;}", 31),
    ("pointer-reassignment-and-nested-shadowing",
     "int cpp_test_entry(){int x=7;int y=11;int *p=&x;"
     "{int x=5;int *q=&x;*q=9;*p=*p+*q;}p=&y;*p=*p+2;return x+y;}", 29),
    ("pointer-to-bool-initialization-call-return",
     "bool truth(int *p){return p;}int take(bool x){return x;}"
     "int cpp_test_entry(){int x=9;int *p=&x;int *q=nullptr;bool yes=p;"
     "bool no=q;return int(yes)+int(no)*2+take(p)*4+int(truth(p))*8"
     "+int(truth(q))*16;}", 13),
    ("pointer-contextual-bool-and-short-circuit",
     "int cpp_test_entry(){int x=0;int *p=&x;int *q=nullptr;"
     "q&&(*p=100);p||(*p=200);if(p)*p=*p+3;while(q)*p=90;"
     "return x+int(!q)*10+int(!p)*100;}", 13),
    ("nullptr-direct-bool-not-copy-initialization",
     "int cpp_test_entry(){bool b(nullptr);if(nullptr)return 99;"
     "return int(b)+int(bool(nullptr))+int(nullptr||true)+int(nullptr==0)*2"
     "+int(nullptr==nullptr)*4;}", 7),
    ("null-pointer-literals-assignment-and-return",
     "int *empty(){return 0;}int cpp_test_entry(){int *p=0x0ULL;"
     "int *q=empty();p=nullptr;return int(p==q)+int(q==0u)*2"
     "+int(0L==p)*4+int(p!=nullptr)*8;}", 7),
    ("void-pointer-and-composite-const-equality",
     "void *erase(int *p){return p;}int cpp_test_entry(){int x=2;const int *p=&x;"
     "void *v=erase(&x);const void *c=p;return int(v==p)+int(c==v)*2"
     "+int(v!=nullptr)*4;}", 7),
    ("exact-pointer-over-qualification-and-bool",
     "int tag(int *p){return 11;}int tag(const int *p){return 22;}"
     "int tag(void *p){return 33;}int tag(bool p){return 44;}"
     "int cpp_test_entry(){int x=1;const int y=2;return tag(&x)+tag(&y);}", 33),
    ("qualification-exact-rank-over-void-pointer",
     "int tag(const int *p){return 11;}int tag(void *p){return 22;}"
     "int tag(bool p){return 33;}int cpp_test_entry(){int x=1;return tag(&x);}", 11),
    ("void-pointer-conversion-over-bool",
     "int tag(void *p){return 11;}int tag(bool p){return 22;}"
     "int cpp_test_entry(){int x=1;return tag(&x);}", 11),
    ("void-pointer-qualification-tie-break",
     "int tag(void *p){return 11;}int tag(const void *p){return 22;}"
     "int cpp_test_entry(){int x=1;const int y=2;return tag(&x)+tag(&y);}", 33),
    ("literal-zero-arithmetic-vs-nullptr-overloads",
     "int tag(int x){return 11;}int tag(int *p){return 22;}"
     "int cpp_test_entry(){return tag(0)+tag(nullptr);}", 33),
    ("unsigned-char-pointee-load-and-store",
     "unsigned char *identity(unsigned char *p){return p;}"
     "int cpp_test_entry(){unsigned char x=250;unsigned char *p=identity(&x);"
     "*p=*p+3;return *p;}", 253),
    ("floating-pointee-load-and-store",
     "double *identity(double *p){return p;}int cpp_test_entry(){double x=1.5;"
     "double *p=identity(&x);*p=*p+2.25;return int(*p*100.0);}", 375),
    ("bool-pointee-value-and-abi",
     "bool *identity(bool *p){return p;}int cpp_test_entry(){bool x=false;"
     "bool *p=identity(&x);*p=2;return int(*p)+int(x)*2;}", 3),
    ("address-of-indirection-and-parenthesized-declarators",
     "int *((identity))(int (*p)){return &*p;}int cpp_test_entry(){int x=19;"
     "int (*p)(&x);return *identity(p)+int(&*p==p);}", 20),
    ("two-pointer-parameters-repeated-type-mangling",
     "int sum(const int *a,const int *b){return *a+*b;}"
     "int cpp_test_entry(){const int x=20;int y=22;return sum(&x,&y);}", 42),
    ("const-arithmetic-value-removes-only-top-qualification",
     "int tag(int x){return 11;}int tag(long x){return 22;}"
     "const int value(){return 4;}const void nothing(){}"
     "int cpp_test_entry(){const int x=5;const long y=7;int(x+1);nothing();"
     "return tag(x)+tag(y)+value()+x+y;}", 49),
    ("assignment-rhs-precedes-effectful-lhs",
     "int *left(int *p){*p=1;return p;}int right(int *p){return *p;}"
     "int cpp_test_entry(){int x=2;*left(&x)=right(&x);return x;}", 2),
    ("nested-assignment-orders-each-side-once",
     "int *left(int *p,int *log){*log=*log*10+1;return p;}"
     "int right(int *log){*log=*log*10+2;return 7;}"
     "int cpp_test_entry(){int x=0;int y=0;int log=0;"
     "*left(&x,&log)=*left(&y,&log)=right(&log);return log*100+x*10+y;}", 21177),
    ("assignment-rhs-reseats-lhs-pointer",
     "int cpp_test_entry(){int x=2;int y=9;int *p=&x;"
     "*p=*(p=&y);return x*100+y;}", 209),
    ("assignment-result-value-and-return",
     "int set(int *p){return *p=42;}int cpp_test_entry(){int x=0;"
     "int result=set(&x);return result+x;}", 84),
)


@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_pointer_host_owned_artifact_is_deterministic(owned_only, name, source, value):
    units = (("pointer.cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("source,code", NEGATIVE_CASES)
def test_cpp_pointer_rejection_precedes_object_emission(owned_only, tmp_path, monkeypatch, source, code):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid pointer semantics reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    binary = tmp_path / "invalid-pointer"
    with pytest.raises(CppDiagnostic) as error:
        artifact = compile_host_cpp((("invalid-pointer.cpp", source),))
        write_host_executable(artifact, binary)
    assert error.value.code == code
    assert not binary.exists()


def execute_and_compare(tmp_path, monkeypatch, units, value):
    assert sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64"), (
        "this execution adapter requires Linux x86_64; not a skipped qualification"
    )
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned C++ pointer compile launched an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units)
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    assert_exact(run_executable(binary), expected)
    assert_exact(run_executable(binary), expected)
    oracle = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    assert_exact(run_executable(oracle), expected)


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_pointer_owned_execution_matches_explicit_cpp17_reference(
    tmp_path, monkeypatch, name, source, value,
):
    execute_and_compare(tmp_path, monkeypatch, (("pointer.cpp", source),), value)


@pytest.mark.integration
def test_cpp_pointer_separate_translation_units_execute_with_const_signature_identity(tmp_path, monkeypatch):
    units = (
        ("helper.cpp", "int *id(int *const p){return p;}"
         "int sum(const int *a,const int *b){return *a+*b;}"),
        ("entry.cpp", "int *id(int *);int sum(const int *,const int *);"
         "int cpp_test_entry(){int x=20;int y=22;return sum(id(&x),&y);}"),
    )
    execute_and_compare(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_cpp_nullptr_negation_owned_execution_follows_contextual_bool(tmp_path, monkeypatch):
    # N4659 [expr.unary.op]/9 and [conv]/4: ! contextually converts to bool,
    # whose validity is direct-initialization, not implicit copy-init. G++14.2
    # -pedantic-errors rejects !nullptr despite accepting if(nullptr) and
    # nullptr||false. Keep this discrepancy separate from the shared oracle
    # corpus rather than copying the reference compiler's diagnostic.
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned nullptr compile launched an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp((("nullptr.cpp", "int cpp_test_entry(){return int(!nullptr);}"),))
    binary = tmp_path / "owned-nullptr"
    write_host_executable(artifact, binary)
    assert_exact(run_executable(binary), Execution(1, struct.pack("<i", 1), b""))


@pytest.mark.integration
def test_cpp_pointer_mangled_exports_match_explicit_gpp17_assembly(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import parse_relocatable

    source = (
        "int f(int *a,int *b){return *a+*b;}"
        "int f(const int *a,const int *b){return *a+*b;}"
        "int g(int *a,const int *b,int *c,const int *d){return *a+*b+*c+*d;}"
        "int h(void *a,const void *b,void *c,const void *d){return a==b;}"
        "int cpp_test_entry(){int x=3;return f(&x,&x)+g(&x,&x,&x,&x);}")
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned ABI compile launched an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        owned = compile_host_cpp((("pointer-abi.cpp", source),))
    unit, output = tmp_path / "pointer-abi.cpp", tmp_path / "reference.s"
    unit.write_text(source, encoding="utf-8")
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit G++17 ABI oracle unavailable"
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-O0",
                             "-S", str(unit), "-o", str(output)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr

    def exports(data):
        return {symbol.name for symbol in parse_relocatable(data).symbols
                if symbol.binding == 1 and symbol.section_index and symbol.name.startswith("_Z")}

    reference_exports = set(re.findall(r"^\s*\.globl\s+(_Z\w+)", output.read_text(), re.MULTILINE))
    assert reference_exports
    assert exports(owned.objects[0]) == reference_exports
