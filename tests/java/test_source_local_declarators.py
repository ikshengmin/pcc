"""Grouped local declarators on the owned source-to-native path.

JLS21 6.3, 14.4.1-2, 14.14.1 and 16.2.4:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.4
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html#jls-16.2.4

Whole original sources are shared unchanged by owned native execution and the
selected development JDK. Array-declarator controls are semantic-model only;
their native managed-storage boundary is not relaxed by this grammar change.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import (
    JavaError,
    UnsupportedJava,
    analyze_class_sources,
    compile_source,
)
from pcc.frontends.java.source_types import ArrayType
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_narrow_primitives import (
    WIDTHS,
    descriptor,
    observer_source,
)
from tests.java.test_source_raw_reference import (
    JVM_LIMITS,
    digest,
    record_process,
    tool_identity,
)
from tests.java.test_source_scalars import NATIVE_REASON


def original(body, result="int", parameters="", helpers=""):
    return "class Declared{" + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


CASES = [
    ("int-pair", original("int a=7,b=35;return a+b;"), "I", (), 42),
    ("dependent-triple", original("int a=7,b=a*5,c=a+b;return c;"), "I", (), 42),
    ("initializer-effects", original("int n=0;int a=++n,b=n++,c=n+=40;return a==1&&b==1&&n==42?c:0;"), "I", (), 42),
    ("uninitialized-following-assigned", original("int a,b=42;a=b;return a;"), "I", (), 42),
    ("all-uninitialized", original("int a,b,c;a=7;b=35;c=a+b;return c;"), "I", (), 42),
    ("self-initializer-assignment", original("int a=(a=21),b=a;return a+b;"), "I", (), 42),
    ("later-initializer-assigns-earlier", original("int a,b=(a=21);return a+b;"), "I", (), 42),
    ("conditional-initializer-flow", original("int a,b=flag?(a=21):(a=7);return a+b;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("left-to-right-call-arguments", original("int a=21,b=add(a++,a-1);return b;", helpers="static int add(int a,int b){return a+b;}"), "I", (), 42),
    ("earlier-name-resolves-field", original("int a=later,later=35;return a+later;", helpers="static final int later=7;"), "I", (), 42),
    ("local-shadow-starts-at-declarator", original("int a=Declared.a,b=a;return a+b;", helpers="static final int a=21;"), "I", (), 42),
    ("byte-declarators", original("byte a=127,b=-128,c=42;return a+b+c+1;"), "I", (), 42),
    ("short-declarators", original("short a=-32768,b=32767,c=43;return a+b+c;"), "I", (), 42),
    ("char-declarators", original("char a=65535,b=42;return b;", "char"), "C", (), 42),
    ("long-declarators", original("long a=0x123456789abcdef0L,b=a;return b;", "long"), "J", (), 1311768467463790320),
    ("boolean-declarators", original("boolean a=false,b=!a,c=b;return c;", "boolean"), "Z", (), 1),
    ("final-chain-narrowing", original("final int a=7,b=a+35;byte answer=b;return answer;"), "I", (), 42),
    ("final-chain-case-label", original("final int a=7,b=a+35;switch(42){case b:return 42;default:return 0;}"), "I", (), 42),
    ("final-chain-reachability", original("final boolean a=false,b=!a;int x;if(a)return x;while(b){return 42;}"), "I", (), 42),
    ("final-runtime-initializers", original("int n=0;final int a=f(++n),b=f(++n);return n==2?a+b+36:0;", helpers="static int f(int n){return n*2;}"), "I", (), 42),
    ("for-two-local-declarators", original("int sum=0;for(int i=0,j=3;i<j;i++,j--){sum+=i+j;}return sum+36;"), "I", (), 42),
    ("for-dependent-initializers", original("int sum=0;for(int i=1,j=i+2;i<j;i++){sum+=j;}return sum+36;"), "I", (), 42),
    ("for-final-declarators", original("for(final int a=7,b=a+35;b>0;){return b;}"), "I", (), 42),
    ("for-grouped-local-scope-reuse", original("int sum=0;for(int i=0,j=2;i<j;i++){sum+=21;}int i=0,j=0;return sum+i+j;"), "I", (), 42),
    ("continue-keeps-all-loop-locals", original("int sum=0;outer:for(int i=0,j=0;i<3;i++,j+=i){sum=j;continue outer;}return sum+39;"), "I", (), 42),
    ("nested-declaration-groups", original("int answer=0;{int a=7,b=35;answer=a+b;}{int a=1,b=1;answer+=a-b;}return answer;"), "I", (), 42),
    ("switch-block-declaration-group", original("int n=0;switch(1){case 1:int a=7,b=35;n=a+b;break;default:n=0;}return n;"), "I", (), 42),
    ("do-loop-declarations-recreated", original("int n=0,total=0;do{int a=++n,b=a;total+=b;}while(n<3);return total+36;"), "I", (), 42),
    ("parameter-initializers", original("int a=x,b=a+35;return b;", parameters="int x"), "I", (("I", 7),), 42),
    ("narrow-overload-binding", original("byte a=41,b=++a;return pick(b);", helpers="static int pick(byte x){return x;}static int pick(int x){return 0;}"), "I", (), 42),
]


INVALID = [
    ("duplicate-declarator", original("int a=7,a=35;return a;"), "already in scope"),
    ("duplicate-parameter", original("int a=7,x=35;return a+x;", parameters="int x"), "already in scope"),
    ("read-unassigned-earlier", original("int a,b=a;return b;"), "not definitely assigned"),
    ("self-read", original("int a=7,b=b;return a+b;"), "not definitely assigned"),
    ("self-read-hides-field", original("int a=a,b=0;return a+b;", helpers="static final int a=42;"), "not definitely assigned"),
    ("later-name-not-in-scope", original("int a=b,b=42;return a;"), "undefined local"),
    ("later-assignment-not-in-scope", original("int a=(b=42),b=0;return a;"), "undefined local"),
    ("short-circuit-does-not-assign-earlier", original("int a;boolean b=flag&&((a=42)>0),c=b;return a;", parameters="boolean flag"), "not definitely assigned"),
    ("narrow-each-initializer", original("byte a=42,b=128;return a;"), "incompatible assignment"),
    ("nonconstant-int-not-narrowed", original("int n=42;byte a=1,b=n;return a+b;"), "incompatible assignment"),
    ("boolean-each-initializer", original("boolean a=true,b=42;return 0;"), "boolean value"),
    ("final-second-reassignment", original("final int a=7,b=35;b=42;return a+b;"), "final local"),
    ("final-later-initializer-write", original("final int a=7,b=(a=35);return b;"), "final local"),
    ("runtime-final-not-constant", original("final int a=f(),b=a;switch(42){case b:return 42;default:return 0;}", helpers="static int f(){return 42;}"), "constant expression"),
    ("for-group-ends-at-loop", original("for(int a=0,b=2;a<b;a++){}return b;"), "undefined local"),
    ("for-body-cannot-redeclare", original("for(int a=0,b=2;a<b;a++){int b=7;}return 0;"), "already in scope"),
    ("for-update-not-declaration", original("for(int a=0,b=2;a<b;int c=0,d=1){}return 0;"), "local declaration"),
    ("statement-list-cannot-be-declaration", original("int a=0;for(a++,int b=0,c=1;a<c;a++){}return a;"), "local declaration"),
    ("if-body-not-declaration", original("if(true)int a=7,b=35;return 42;"), "local declaration"),
    ("label-body-not-declaration", original("label:int a=7,b=35;return 42;"), "local declaration"),
    ("trailing-comma", original("int a=42,;return a;"), "identifier"),
    ("type-cannot-repeat", original("int a=7,int b=35;return a+b;"), "identifier"),
    ("for-declaration-followed-by-expression", original("for(int a=0,a++;a<3;a++){}return 0;"), "expected"),
]


@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_grouped_declarators_compile_owned_without_external_owner(name, text, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert result.emit_object().startswith(b"\x7fELF")


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_declarator_originals_fail_before_ir(name, text, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("invalid declaration reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_source(text)
    assert type(caught.value) is JavaError and caught.value.code is None


def test_declarators_retain_distinct_bindings_and_enclosing_scope():
    text = original("int first=7,second=first+35;return second;")
    method = analyze_class_sources({"Declared.java": text}).programs[0].methods[0]
    group, result = method.body.children
    first, second = group.children
    assert group.kind == "sequence" and group.value == "local_declarations"
    assert first.kind == second.kind == "var"
    assert first.symbol is not second.symbol
    assert second.children[0].children[0].symbol is first.symbol
    assert result.children[0].symbol is second.symbol
    assert [binding.slot for binding in method.locals] == [0, 1]


@pytest.mark.parametrize("body,types", [
    ("int a[]={42},b=7;return a[0]+b;", [ArrayType("int"), "int"]),
    ("int[] a={42},b=a;return b[0];", [ArrayType("int"), ArrayType("int")]),
    ("int a=42,b[]={a};return b[0];", ["int", ArrayType("int")]),
])
def test_array_brackets_belong_to_their_declarator_model_only(body, types, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    text = original(body)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("managed array reached native IR"))
    method = analyze_class_sources({"Declared.java": text}).programs[0].methods[0]
    assert [binding.dtype for binding in method.locals] == types
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text)
    assert caught.value.code == "java.unsupported.array-storage"


def test_grouped_blank_final_retains_explicit_du_boundary():
    text = original("final int a=7,b;b=35;return a+b;")
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text)
    assert caught.value.code == "java.unsupported.blank-final-local"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_grouped_declarator_originals_execute_native(name, text, lane, arguments, expected, tmp_path, monkeypatch):
    unit = tmp_path / "Declared.java"
    unit.write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        result = compile_source(text, filename=unit.name, descriptor=descriptor(lane, arguments))
    assert observe_scalar(result, tmp_path, monkeypatch, arguments) == expected
    assert unit.read_bytes() == text.encode("utf-8")


JDK_REASON = None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else "select an existing JDK with PCC_JAVA_RAW_REFERENCE"


def compile_original_reference_batch(tmp_path, request, *, runtime):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    root = Path(__file__).parent
    inputs = tuple(root / name for name in (
        "test_source_local_declarators.py", "NarrowReference.java", "owned_execution.py",
        "test_source_narrow_primitives.py", "test_source_raw_reference.py"))
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    owner = {**before, "version": version.group(1), "source_level": level, "target_level": level,
             "platform_contract": "selected current JDK platform, not --release compatibility",
             "production_dependency": False}
    (tmp_path / "owner-before.json").write_text(json.dumps(owner, indent=2))

    def finish_identity():
        after = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
        unchanged = all(digest(path) == expected for path, expected in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**after,
            "original_observer_hashes": {str(path): value for path, value in retained.items()},
            "unchanged": unchanged}, indent=2))
        assert before == after and unchanged

    request.addfinalizer(finish_identity)
    assert record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")[0] == 0
    rows = []
    for name, text, *rest in (CASES if runtime else INVALID):
        directory = tmp_path / name
        directory.mkdir()
        unit = directory / "Declared.java"
        unit.write_bytes(text.encode("utf-8"))
        retained[unit] = digest(unit)
        if runtime:
            lane, arguments, _ = rest
            observer = directory / "PccNarrowObserve.java"
            observer.write_text(observer_source("Declared", lane, arguments), encoding="utf-8")
            retained[observer] = digest(observer)
        rows.append(name + ("\trun\t" if runtime else "\treject\t") + unit.name)
    (tmp_path / "cases.tsv").write_text("\n".join(rows) + "\n")
    driver = tmp_path / "NarrowReference.java"
    driver.write_bytes(inputs[1].read_bytes())
    retained[driver] = digest(driver)
    driver_classes = tmp_path / "driver-classes"
    driver_classes.mkdir()
    compiler = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
        "-classpath", str(driver_classes), "-d", str(driver_classes), str(driver)), tmp_path, "driver-compile", 20)
    assert compiler[0] == 0, compiler
    batch = record_process((str(java), *JVM_LIMITS, "-cp", str(driver_classes), "NarrowReference",
        str(tmp_path), level), tmp_path, "batch-compile", 30)
    assert batch[0] == 0, batch
    return java


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_declarator_originals_match_raw_jdk_execution(tmp_path, monkeypatch, request):
    java = compile_original_reference_batch(tmp_path, request, runtime=True)
    for name, text, lane, arguments, expected in CASES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", name
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"),
            "PccNarrowObserve"), directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            compilation = compile_source(text, descriptor=descriptor(lane, arguments))
        observe_scalar(compilation, native, monkeypatch, arguments)
        actual = record_process((str(native / "observed"),), native, "raw-native")
        expected_bytes = int(expected).to_bytes(WIDTHS[lane], "little", signed=lane not in {"Z", "C"})
        (directory / "comparison.json").write_text(json.dumps({
            "case": name, "comparison": "raw_process_streams",
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "descriptor": descriptor(lane, arguments), "arguments": arguments,
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
            "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
        }, indent=2))
        assert actual == reference == (0, expected_bytes, b""), name


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_invalid_declarator_originals_are_rejected_by_jdk(tmp_path, request):
    compile_original_reference_batch(tmp_path, request, runtime=False)
    for name, *_ in INVALID:
        assert int((tmp_path / name / "compiler-status.txt").read_text()) != 0, name
