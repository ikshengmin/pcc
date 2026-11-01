"""Primitive-array semantic model, NOT PCC native allocation/execution.

JLS21 10.1–10.7, 15.10, 15.21.3, 15.26 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-10.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.26
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html

All originals are checked as complete sources, including uncalled methods.
A separate opt-in JDK lane below compiles these exact sources and executes the
positive run methods; it is not a native differential or Java production owner.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_types import ArrayType
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, record_process, tool_identity


def original(body, helpers=""):
    return "class ArrayExample{static int run(){" + body + "}" + helpers + "}"


PROGRAMS = [
    ("allocated-zero", original("int[] a=new int[3];return a.length+a[1]+39;")),
    ("initializer", original("int[] a={7,35,};return a[0]+a[1];")),
    ("component-store", original("int[] a=new int[2];a[0]=7;a[1]=35;return a[0]+a[1];")),
    ("final-reference-not-components", original("final int[] a={41};a[0]++;return a[0];")),
    ("compound-narrows-byte", original("byte[] a={127};a[0]+=171;return a[0];")),
    ("short-char-components", original("short[] a={-1};char[] b={'*'};b[0]+=1;return a[0]+b[0];")),
    ("boolean-components", original("boolean[] a={false};a[0]|=true;return a[0]?42:0;")),
    ("long-component", original("long[] a={9223372036854775807L};a[0]++;return (int)(a[0]>>>63)+41;")),
    ("parameter-return-alias", original("int[] a={1};int[] b=id(a);b[0]=42;return a[0];", "static int[] id(int[] value){return value;}")),
    ("reference-assignment", original("int[] a=null;int[] b={42};a=b;return a[0];")),
    ("null-conditional", original("int[] a=true?new int[]{42}:null;return a!=null?a[0]:0;")),
    ("identity-not-content", original("int[] a={42};int[] b={42};return a==b?0:42;")),
    ("nullable-overload", original("return choose(null);", "static int choose(int[] a){return a==null?42:0;}static int choose(int a){return 0;}")),
    ("distinct-primitive-overloads", original("return choose(new int[]{42});", "static int choose(int[] a){return a[0];}static int choose(long[] a){return 0;}")),
    ("post-name-declarations", original("byte a[]={42};return id(a)[0];", "static byte id(byte a[])[]{return a;}")),
    ("index-promotion", original("int[] a={42};return a[(char)0];")),
    ("length-of-call", original("return values().length+40;", "static int[] values(){return new int[]{1,2};}")),
    ("loop-array-dependency", original("int[] a={1,2,3,4};int sum=0;for(int i=0;i<a.length;i++){sum+=a[i];}return sum+32;")),
    ("empty-array", original("int[] a={};return a.length+42;")),
    ("parenthesized-place", original("int[] a={0};(a[0])=42;return a[0];")),
    ("source-order", original("int[] a={0};int[] log={0};array(a,log)[index(log)]=value(log);return log[0]==123&&a[0]==42?42:0;",
      "static int[] array(int[] a,int[] log){log[0]=log[0]*10+1;return a;}"
      "static int index(int[] log){log[0]=log[0]*10+2;return 0;}"
      "static int value(int[] log){log[0]=log[0]*10+3;return 42;}")),
]

# Valid sources with intentionally abrupt operations; reference TYPECHECK only.
VALID_ONLY = [
    ("negative-size-runtime", original("int[] a=new int[-1];return a.length;")),
    ("null-access-runtime", original("int[] a=null;return a[0];")),
    ("bounds-runtime", original("int[] a={42};return a[1];")),
    ("instance-array-parameter", "class ArrayExample{int sum(int[] a){return a[0]+a.length;}ArrayExample(int[] a){a[0]=42;}}"),
]

INVALID = [
    ("long-dimension", original("int[] a=new int[0L];return 42;"), "promote to int"),
    ("boolean-dimension", original("int[] a=new int[true];return 42;"), "promote to int"),
    ("long-index", original("int[] a={42};return a[0L];"), "promote to int"),
    ("boolean-index", original("int[] a={42};return a[false];"), "promote to int"),
    ("primitive-invariant", original("int[] a={42};long[] b=a;return 42;"), "incompatible array"),
    ("initializer-range", original("byte[] a={128};return 42;"), "incompatible assignment"),
    ("initializer-long", original("int[] a={42L};return 42;"), "incompatible assignment"),
    ("initializer-boolean", original("int[] a={true};return 42;"), "integer value required"),
    ("store-range", original("byte[] a={0};a[0]=128;return 42;"), "incompatible assignment"),
    ("store-boolean", original("int[] a={0};a[0]=true;return 42;"), "integer value required"),
    ("length-final", original("int[] a={0};a.length=42;return 42;"), "length is final"),
    ("unassigned-read", original("int[] a;return a.length;"), "not definitely assigned"),
    ("unassigned-store", original("int[] a;a[0]=42;return 42;"), "not definitely assigned"),
    ("final-rebind", original("final int[] a={0};a=new int[]{42};return 42;"), "final local"),
    ("incomparable-arrays", original("int[] a={};long[] b={};return a==b?42:0;"), "incomparable array"),
    ("null-to-int", original("int a=null;return a;"), "integer value required"),
    ("missing-array-return", "class ArrayExample{static int[] values(){}}", "missing return"),
    ("bare-array-return", "class ArrayExample{static int[] values(){return;}}", "must return a value"),
    ("ambiguous-null", original("return f(null);", "static int f(int[] a){return 42;}static int f(long[] a){return 42;}"), "ambiguous"),
    ("length-not-constant", original("int[] a={0};switch(0){case a.length:return 1;default:return 42;}"), "constant expression"),
    ("scalar-not-array", original("int a=42;return a[0];"), "array reference"),
    ("boolean-increment", original("boolean[] a={true};a[0]++;return 42;"), "integer component"),
]


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY, ids=[x[0] for x in PROGRAMS + VALID_ONLY])
def test_complete_original_array_sources_have_checked_model_only(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("array model invoked lowering"))
    inputs = {"ArrayExample.java": text}
    project = analyze_class_sources(inputs)
    assert inputs == {"ArrayExample.java": text}
    assert not project.models["ArrayExample"].runtime_registered


@pytest.mark.parametrize("name,text,message", INVALID, ids=[x[0] for x in INVALID])
def test_invalid_array_sources_fail_without_capability_misclassification(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


def test_array_signatures_are_nominal_component_descriptors_not_pointer_width():
    text = "class ArrayExample{static int[] id(int[] a){return a;}static long[] id(long[] a){return a;}static boolean[] flags(boolean a[]){return a;}static byte bytes(byte[] a)[]{return a;}}"
    methods = analyze_class_sources({"ArrayExample.java": text}).classes["ArrayExample"].methods
    assert [m.descriptor for m in methods] == ["([I)[I", "([J)[J", "([Z)[Z", "([B)[B"]
    assert methods[0].parameter_types == (ArrayType("int"),)
    assert methods[0].parameter_types != methods[1].parameter_types


def test_read_and_store_plans_retain_jls_abrupt_completion_order_once():
    text = "class ArrayExample{static void update(int[] a,int i,int n){a[i]=n;a[i]+=n;int x=a[i];int y=a.length;}}"
    statements = analyze_class_sources({"ArrayExample.java": text}).classes["ArrayExample"].methods[0].body.children
    simple, compound, read, length = statements
    assert simple.runtime_steps == ("array-reference", "index", "rhs", "null-check", "bounds-check", "store-component")
    assert compound.runtime_steps == ("array-reference", "index", "null-check", "bounds-check", "read-component", "rhs", "operation", "narrow", "store-component")
    assert simple.children[0].kind == compound.children[0].kind == "array_place"
    assert read.children[0].runtime_steps == ("array-reference", "index", "null-check", "bounds-check", "read-component")
    assert length.children[0].dtype == "int" and length.children[0].constant is None


@pytest.mark.parametrize("text", [
    "class ArrayExample{int[] field;}",
    "class ArrayExample{static int[][] f(){return null;}}",
    original("int[][] a=new int[2][3];return 42;"),
    original("int[] a=(int[])null;return 42;"),
])
def test_array_runtime_and_wider_shape_boundaries_remain_explicit(text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code in {"java.unsupported.array-storage", "java.unsupported.array-model-shape"}


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[x[0] for x in PROGRAMS])
def test_model_does_not_grant_native_array_compilation(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("managed array reached scalar IR"))
    with pytest.raises(UnsupportedJava):
        compile_source(text, filename="ArrayExample.java")


@pytest.mark.parametrize("body", ("return values().length+40;", "return values()[0];"))
def test_native_postfix_arrays_are_capability_failures_before_ir(body, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("managed array reached scalar IR"))
    text = original(body, "static int[] values(){return new int[]{42,0};}")
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text, filename="ArrayExample.java")
    assert caught.value.code == "java.unsupported.array-storage"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                     "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-array-model-reference")
def test_exact_array_model_originals_against_selected_jdk_only(tmp_path, request):
    """JDK runtime positives + compile rejection; PCC runtime remains gated."""
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    release = (java.parent.parent / "release").read_text()
    import re
    version = re.search(r'^JAVA_VERSION="([^"]+)"', release, re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    driver = Path(__file__).with_name("ArrayModelReference.java")
    before = tool_identity(java)
    source_inputs = {str(path): digest(path) for path in (Path(__file__), driver)}
    originals, observers, rows = [], [], []
    for name, text in PROGRAMS + VALID_ONLY + [(n, t) for n, t, _ in INVALID]:
        directory = tmp_path / name
        directory.mkdir()
        unit = directory / "ArrayExample.java"
        unit.write_bytes(text.encode("utf-8"))
        originals.append((unit, text))
        kind = "run" if name in dict(PROGRAMS) else "typecheck" if name in dict(VALID_ONLY) else "reject"
        if kind == "run":
            observer = directory / "PccArrayObserve.java"
            body = "public final class PccArrayObserve{public static int value(){return ArrayExample.run();}}"
            observer.write_bytes(body.encode("utf-8"))
            observers.append((observer, body))
        rows.append(name + "\t" + kind)
    (tmp_path / "cases.tsv").write_text("\n".join(rows) + "\n")
    owner = {"reference_owner": "explicit existing JDK; development-only array semantic reference",
             "version": version.group(1), "source_level": level, "target_level": level,
             "contract": "current platform; not --release compatibility; PCC array runtime NOT executed",
             "tools_before": before, "inputs_before": source_inputs,
             "originals": [{"path": str(path.relative_to(tmp_path)), "sha256": digest(path)} for path, _ in originals],
             "observers": [{"path": str(path.relative_to(tmp_path)), "sha256": digest(path)} for path, _ in observers]}
    (tmp_path / "owner-before.json").write_text(json.dumps(owner, indent=2) + "\n")

    def finish_identity():
        after = tool_identity(java)
        unchanged = all(path.read_bytes() == text.encode("utf-8") for path, text in originals + observers)
        inputs_after = {str(path): digest(path) for path in (Path(__file__), driver)}
        (tmp_path / "owner-post.json").write_text(json.dumps({**owner, "tools_after": after,
            "inputs_after": inputs_after, "originals_and_observers_unchanged": unchanged}, indent=2) + "\n")
        assert before == after and source_inputs == inputs_after and unchanged

    request.addfinalizer(finish_identity)
    observed_version = record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")
    assert observed_version[0] == 0
    modules = record_process((str(java), *JVM_LIMITS, "--list-modules"), tmp_path, "modules")
    assert modules[0] == 0 and any(row.split(b"@")[0] == b"jdk.compiler" for row in modules[1].splitlines())
    # Source-file launch implicitly asks for --release symbol metadata. Compile
    # this separate observer via the selected official module instead, using
    # the same explicit current-platform source/target contract as the fixtures.
    driver_classes = tmp_path / "driver-classes"
    driver_classes.mkdir()
    compiler = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
                               "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
                               "-classpath", str(driver_classes), "-d", str(driver_classes), str(driver)),
                              tmp_path, "driver-compile", 20)
    assert compiler[0] == 0, compiler
    result = record_process((str(java), *JVM_LIMITS, "-cp", str(driver_classes), "ArrayModelReference",
                             str(tmp_path), level), tmp_path, "reference-batch", 60)
    assert result[0] == 0, result
    for name, _ in PROGRAMS:
        assert (tmp_path / name / "runtime-value.txt").read_text() == "42"
    for name, _ in PROGRAMS + VALID_ONLY:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "0"
    for name, _, _ in INVALID:
        assert int((tmp_path / name / "compiler-status.txt").read_text()) != 0
