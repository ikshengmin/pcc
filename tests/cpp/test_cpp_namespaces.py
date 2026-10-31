"""C++17 namespace lookup rules; host-owned lane, not full language qualification.

PCC-authored cases derive from N4659 rules, not copied compiler tests/source.
The integration lane labels G++ explicitly as a development reference.
"""

import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import PointerType, mangle_function
from tests.cpp.owned_execution import (
    Execution,
    assert_exact,
    compile_host_cpp,
    compile_reference_cpp,
    run_executable,
    write_host_executable,
)
from tests.cpp.test_cpp_grouped_names import owned_only


CASES = (
    ("nested-reopen-alias-qualified-type",
     (("entry.cpp", "namespace A::B {using T=int;T f(T x){return x+1;}}"
       "namespace A {namespace B {T g(T x){return f(x)+2;}}}namespace Alias=A::B;"
       "int cpp_test_entry(){Alias::T x=39;return Alias::g(x);}"),), 42),
    ("qualified-global-bypasses-local-hiding",
     (("entry.cpp", "int f(){return 4;}namespace A {int f(){return 8;}}"
       "int cpp_test_entry(){int f=1;int A=2;return ::f()+A::f()+f+A;}"),), 15),
    ("namespace-main-is-ordinary-function",
     (("entry.cpp", "namespace A {long main(int x){return x+1;}}"
       "int cpp_test_entry(){return A::main(41);}"),), 42),
    ("directive-common-ancestor-does-not-hide-closer",
     (("entry.cpp", "namespace A {int f(){return 90;}}namespace B {int f(){return 42;}"
       "namespace C {using namespace A;int g(){return f();}}}"
       "int cpp_test_entry(){return B::C::g();}"),), 42),
    ("directive-transitive-overloads-not-qualified-hiding",
     (("entry.cpp", "namespace A {int f(int x){return 10;}}"
       "namespace B {using namespace A;int f(long x){return 20;}}"
       "using namespace B;int cpp_test_entry(){return f(1)+B::f(1);}"),), 30),
    ("qualified-direct-stops-directives",
     (("entry.cpp", "namespace A {int f(int x){return 90;}}"
       "namespace B {using namespace A;int f(long x){return 42;}}"
       "int cpp_test_entry(){return B::f(1);}"),), 42),
    ("using-declaration-snapshot-vs-directive-live",
     (("entry.cpp", "namespace A {int f(long x){return 10;}}using A::f;"
       "namespace A {int f(int x){return 20;}}"
       "int cpp_test_entry(){int x=f(1);{using namespace A;x=x+f(1);}return x;}"),), 30),
    ("using-declaration-list-and-namespace-only-directive",
     (("entry.cpp", "namespace A {using T=int;int f(T x){return x+1;}}"
       "int cpp_test_entry(){int A=1;using namespace A;using ::A::T,::A::f;"
       "T x=40;return f(x)+A;}"),), 42),
    ("inline-overloads-merge-even-with-direct-member",
     (("entry.cpp", "namespace A {inline namespace V {int f(int x){return 10;}}"
       "int f(long x){return 20;}}int cpp_test_entry(){return A::f(1)+A::f(1L);}"),), 30),
    ("inline-reopen-with-omitted-inline",
     (("entry.cpp", "namespace A {inline namespace V {namespace N {using T=int;}}}"
       "namespace A {namespace N {T f(){return 42;}}}"
       "int cpp_test_entry(){return A::V::N::f();}"),), 42),
    ("anonymous-reopen-shared-within-tu",
     (("entry.cpp", "namespace {int f(){return 40;}}namespace {int g(){return f()+2;}}"
       "int cpp_test_entry(){return g();}"),), 42),
    ("anonymous-internal-linkage-across-tu",
     (("helper.cpp", "namespace {int same(){return 10;}}int helper(){return same();}"),
      ("entry.cpp", "namespace {int same(){return 32;}}int helper();"
       "int cpp_test_entry(){return helper()+same();}")), 42),
    ("qualified-call-across-tu",
     (("helper.cpp", "namespace A::B {int f(int x){return x+1;}}"),
      ("entry.cpp", "namespace A::B {int f(int);}"
       "int cpp_test_entry(){return A::B::f(41);}")), 42),
    ("namespace-alias-identity-and-repeat",
     (("entry.cpp", "namespace A {int f(){return 42;}}namespace B=A;namespace C=B;namespace B=C;"
       "int cpp_test_entry(){return C::f();}"),), 42),
    ("diamond-and-cycle-deduplicate-entity",
     (("entry.cpp", "namespace A {int f(){return 42;}}"
       "namespace B {using namespace A;}namespace C {using namespace A;}"
       "namespace A {using namespace B;}namespace D {using namespace B;using namespace C;}"
       "int cpp_test_entry(){return D::f();}"),), 42),
    ("block-using-does-not-escape",
     (("entry.cpp", "int f(){return 1;}namespace A {int f(){return 41;}}"
       "int cpp_test_entry(){int x=0;{using A::f;x=f();}return x+f();}"),), 42),
    ("block-namespace-alias-is-lexical",
     (("entry.cpp", "namespace A {int f(){return 10;}}namespace B {int f(){return 32;}}"
       "int cpp_test_entry(){namespace N=A;int x=N::f();{namespace N=B;x=x+N::f();}return x;}"),), 42),
    ("qualified-alias-functional-conversion",
     (("entry.cpp", "namespace A {using T=int;}int cpp_test_entry(){return A::T(42.5);}"),), 42),
    ("namespace-pointer-substitutions",
     (("entry.cpp", "namespace A::B {int f(int *x,int *y){return *x+*y;}}"
       "int cpp_test_entry(){int x=21;return A::B::f(&x,&x);}"),), 42),
)


@pytest.mark.parametrize("name,units,value", CASES, ids=[case[0] for case in CASES])
def test_namespace_owned_compilation(owned_only, name, units, value):
    artifact = compile_host_cpp(units)
    repeated = compile_host_cpp(units)
    assert artifact.ir == repeated.ir
    assert artifact.objects == repeated.objects
    assert artifact.executable == repeated.executable
    assert artifact.owner == "host-python/pcc-cpp/pcc-self/pcc-elf"
    assert artifact.executable[:4] == b"\x7fELF"


def test_namespace_graph_retains_identity_and_internal_linkage():
    parser = CppParser()
    ast = parser.parse("namespace A {inline namespace V {int f(){return 1;}}}"
                       "namespace A {namespace V {int g(){return f();}}}"
                       "namespace {int local(){return 2;}}", "namespace.cpp")
    a = parser.scope.lookup("A").namespace_scope
    v = a.lookup("V").namespace_scope
    assert v.parent is a and a.parent is parser.scope
    assert a.lookup_qualified("f").overloads[0].owner is v
    assert v.namespace_path() == ("A", "V")
    assert ast.ext[0].decl.name == "_ZN1A1V1fEv"
    assert ast.ext[2].decl.name == "_ZN12_GLOBAL__N_15localEv"
    assert ast.ext[2].decl.storage == ["static"]


def test_namespace_prefixes_count_before_pointer_type_substitutions():
    assert mangle_function("f", (PointerType("int"), PointerType("int")), ("A", "B")) == "_ZN1A1B1fEPiS1_"
    assert mangle_function("f", ("int",), ("std",)) == "_ZSt1fi"
    assert mangle_function("f", (PointerType("int"), PointerType("int")), ("std", "V")) == "_ZNSt1V1fEPiS0_"


NEGATIVE_CASES = (
    ("namespace A {using T=int;}namespace B {using T=long;}using namespace A;using namespace B;T f();", "cpp.ambiguous-name"),
    ("namespace A {int f(int);}namespace B {int f(int);}using namespace A;using namespace B;int g(){return f(1);}", "cpp.ambiguous-overload"),
    ("namespace A {int f(int);}using A::f;int f(int);", "cpp.using-declaration-conflict"),
    ("namespace A {int f(int);}int f(int);using A::f;", "cpp.using-declaration-conflict"),
    ("namespace A {using T=int;}template<class T>int f(T x){using A::T;return 1;}", "cpp.template-parameter-shadowing"),
    ("namespace A {using T=int;}using A::T;using T=long;", "cpp.redeclaration"),
    ("namespace A {}using A;", "cpp.invalid-using-declaration"),
    ("namespace A {}using ::A;", "cpp.using-namespace-name"),
    ("namespace Alias=Missing;", "cpp.not-a-namespace"),
    ("using namespace Missing;", "cpp.not-a-namespace"),
    ("namespace A {}namespace Alias=A;namespace Alias {}", "cpp.namespace-conflict"),
    ("using A=int;namespace A {}", "cpp.namespace-conflict"),
    ("namespace A {}inline namespace A {}", "cpp.namespace-inline-mismatch"),
    ("inline namespace A::B {}", "cpp.invalid-namespace-definition"),
    ("namespace ::A {}", "cpp.invalid-namespace-definition"),
    ("int f(){namespace A {}return 0;}", "cpp.namespace-scope"),
    ("namespace A {}int f(){return A::missing();}", "cpp.undeclared"),
    ("namespace A {}int f(){return A;}", "cpp.not-a-value"),
    ("namespace A {}int f(){{namespace N=A;}return N::missing();}", "cpp.not-a-namespace"),
    ("int f(){return Missing::f();}", "cpp.not-a-namespace"),
    ("namespace A {int f(){return later();}int later();}", "cpp.undeclared"),
    ("namespace A {int f(int);}int g(){ {using A::f;}return f(1);}", "cpp.undeclared"),
    ("namespace A {template<class T>T f(T x){return x;}}", "cpp.unsupported.namespace-templates"),
    ("template<class T>int f(T x){return T::g(x);}", "cpp.unsupported.dependent-lookup"),
    ("namespace A {struct S {int x;};}", "cpp.unsupported.classes"),
    ("namespace A {int f();}int A::f(){return 1;}", "cpp.unsupported.qualified-names"),
    ("namespace A {int f(){return 1;}}int g(){return A : : f();}", "cpp.invalid-qualified-name"),
)


@pytest.mark.parametrize("source,code", NEGATIVE_CASES)
def test_namespace_diagnostics(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, "namespace-negative.cpp")
    assert error.value.code == code
    assert error.value.coord.file == "namespace-negative.cpp"


@pytest.mark.integration
@pytest.mark.parametrize("name,units,value", CASES, ids=[case[0] for case in CASES])
def test_namespace_owned_execution_and_explicit_cpp17_reference(tmp_path, monkeypatch, name, units, value):
    assert sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64")
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit development oracle unavailable"
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned C++ compile attempted an external process")
        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units)
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    reference = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    for owner, executable in (("owned", binary), ("reference", reference)):
        actual = run_executable(executable)
        assert_exact(actual, expected)
        executable.with_suffix(".stdout.bin").write_bytes(actual.stdout)
        executable.with_suffix(".stderr.bin").write_bytes(actual.stderr)
        executable.with_suffix(".execution.json").write_text(json.dumps({
            "owner": owner, "returncode": actual.returncode,
            "stdout_hex": actual.stdout.hex(), "stderr_hex": actual.stderr.hex(),
            "binary": str(executable),
            "binary_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "source_sha256": {filename: hashlib.sha256(source.encode()).hexdigest()
                              for filename, source in units},
        }, indent=2))
    for index, (ir, obj) in enumerate(zip(artifact.ir, artifact.objects)):
        (tmp_path / ("owned-" + str(index) + ".ll")).write_text(ir)
        (tmp_path / ("owned-" + str(index) + ".o")).write_bytes(obj)
