"""Owned scalar assignment expressions and prefix/postfix values.

JLS21 5.2/5.6, 14.8/14.14, 15.7, 15.14.2-3, 15.15.1-2,
15.26 and 16.1.2-10:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-5.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html

Each complete original is used unchanged for owned parsing/IR/native execution
and the existing actual JDK compiler/independent binary-output observer. Assignment
is not constant propagation; RHS effects and branch-specific DA are explicit.
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
    return "class Mutations{" + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


CASES = [
    ("assignment-chain", original("int a;int b;return (a=b=21)+a;"), "I", (), 42),
    ("nested-call-arguments", original("int x=1;return check(x++,++x,x=7,x);", helpers="static int check(int a,int b,int c,int d){if(a==1&&b==3&&c==7&&d==7)return 42;return 0;}"), "I", (), 42),
    ("eight-argument-effects", original("int x=0;return check(++x,x++,x=5,++x,x--,x,++x,x+=35);", helpers="static int check(int a,int b,int c,int d,int e,int f,int g,int h){if(a==1&&b==1&&c==5&&d==6&&e==6&&f==5&&g==6&&h==41)return 42;return 0;}"), "I", (), 42),
    ("operand-left-to-right", original("int x=3;int y=x++*10 + ++x;return y+x+2;"), "I", (), 42),
    ("prefix-decrement", original("int x=44;return --x - --x +41;"), "I", (), 42),
    ("postfix-decrement", original("int x=42;int y=x--;return y+x-41;"), "I", (), 42),
    ("compound-saves-old", original("int x=1;return (x+=(x=4)*6)+17;"), "I", (), 42),
    ("statement-compound-saves-old", original("int x=1;x+=(x=4)*6;return x+17;"), "I", (), 42),
    ("assignment-result-byte", original("byte b=0;return b=127;", "byte"), "B", (), 127),
    ("postfix-byte-old-and-wrap", original("byte b=127;int old=b++;if(old==127&&b==-128)return 42;return 0;"), "I", (), 42),
    ("prefix-byte-wrap", original("byte b=127;return ++b;", "byte"), "B", (), -128),
    ("prefix-short-wrap", original("short s=32767;return ++s;", "short"), "S", (), -32768),
    ("prefix-char-wrap", original("char c=65535;return ++c;", "char"), "C", (), 0),
    ("postfix-char-old", original("char c=0;char old=c--;if(c!=65535)return 1;return old;", "char"), "C", (), 0),
    ("postfix-long-old-and-wrap", original("long n=9223372036854775807L;long old=n++;if(n!=-9223372036854775808L)return 0L;return old;", "long"), "J", (), 9223372036854775807),
    ("compound-long-to-byte", original("byte b=41;return b+=0x100000001L;", "byte"), "B", (), 42),
    ("compound-shift-mask", original("int x=1;return (x<<=33L)+40;"), "I", (), 42),
    ("compound-unsigned-shift", original("int x=-1;return x>>>=1;"), "I", (), 2147483647),
    ("compound-negative-divisor", original("long x=-9223372036854775808L;return x/=-1L;", "long"), "J", (), -9223372036854775808),
    ("compound-boolean-not-short-circuit", original("boolean b=false;int x=0;boolean r=(b&=(x=42)==42);if(!r)return x;return 0;"), "I", (), 42),
    ("boolean-assignment-value", original("boolean b;return b=(flag?true:false);", "boolean", "boolean flag"), "Z", (("Z", 0),), 0),
    ("and-skips-store", original("int x=42;boolean b=false&&(x=7)>0;return x;"), "I", (), 42),
    ("or-skips-store", original("int x=42;boolean b=true||(x=7)>0;return x;"), "I", (), 42),
    ("and-rhs-da", original("int x;if(flag&&(x=42)>0)return x;return 7;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("or-false-da", original("int x;if(flag||(x=42)<0)return 7;else return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("not-reverses-da", original("int x;if(!(flag&&(x=42)>0))return 7;return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("argument-da-sequence", original("int x;return add(x=21,x);", helpers="static int add(int a,int b){return a+b;}"), "I", (), 42),
    ("binary-da-sequence", original("int x;return (x=21)+x;"), "I", (), 42),
    ("conditional-both-arms-da", original("int x;int y=flag?(x=21):(x=7);return x+y;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("conditional-selects-one", original("int x=0;int y=flag?++x:--x;return x*20+y+21;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("conditional-middle-assignment", original("int x=0;return flag?x=42:7;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("conditional-boolean-da", original("int x;if(flag?((x=42)>0):false)return x;return 7;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("nested-conditional-effects", original("int x=0;int y=flag?(other?++x:--x):(x=7);return x+y+40;", parameters="boolean flag,boolean other"), "I", (("Z", 1),("Z", 1)), 42),
    ("while-condition-assignment", original("int x;while((x=42)<0){}return x;"), "I", (), 42),
    ("do-condition-assignment", original("int x;do{}while((x=42)<0);return x;"), "I", (), 42),
    ("for-condition-assignment", original("int n;int i=0;for(;(n=i++)<3;){}return n+39;"), "I", (), 42),
    ("for-update-prefix", original("int n=0;for(int i=0;i<3;n+=++i){}return n+36;"), "I", (), 42),
    ("labeled-continue-update", original("int n=0;outer:for(int i=0;i<3;n+=++i){switch(i){default:continue outer;}}return n+36;"), "I", (), 42),
    ("do-continue-test-effects", original("int n=0;outer:do{continue outer;}while(++n<3);return n+39;"), "I", (), 42),
    ("switch-selector-assignment", original("int x;switch(x=42){case 42:break;default:return 0;}return x;"), "I", (), 42),
    ("prefix-statement", original("int x=41;++x;return x;"), "I", (), 42),
    ("parenthesized-write-statement", original("int x;(x)=42;return x;"), "I", (), 42),
    ("parenthesized-postfix-statement", original("int x=41;(x)++;return x;"), "I", (), 42),
    ("parenthesized-variable", original("int x=41;return ++(x);"), "I", (), 42),
    ("parenthesized-assignment-target", original("int x;return ((x)=42);"), "I", (), 42),
    ("prefix-overload-type", original("byte x=41;return pick(++x);", helpers="static int pick(byte x){return x;}static int pick(int x){return 0;}"), "I", (), 42),
    ("postfix-overload-type", original("short x=42;return pick(x++);", helpers="static int pick(short x){return x;}static int pick(int x){return 0;}"), "I", (), 42),
    ("parameter-mutation", original("return (x=21)+x++;", parameters="int x"), "I", (("I", 9),), 42),
    ("assignment-not-constant-loop", original("boolean b;int n=0;while(b=(++n<3)){}return n+39;"), "I", (), 42),
    ("self-initializer-write", original("int x=(x=42);return x;"), "I", (), 42),
    ("grouped-chain-targets", original("int x;int y;return ((x)=((y)=21))+x;"), "I", (), 42),
    ("nested-compound-old-values", original("int x=2;int y=(x+=(x*=3));return x+y+26;"), "I", (), 42),
    ("postfix-in-compound-rhs", original("int x=3;int y=(x+=x++);return x+y+30;"), "I", (), 42),
    ("prefix-in-compound-rhs", original("int x=3;int y=(x+=++x);return x+y+28;"), "I", (), 42),
    ("boolean-compound-saves-old", original("boolean b=false;boolean r=(b|=(b=true));return r&&b?42:0;"), "I", (), 42),
    ("eager-bitwise-assignment-flow", original("int x;boolean b=false&((x=42)>0);return b?0:x;"), "I", (), 42),
    ("conditional-false-arm-flow", original("int x;int y=flag?(x=7):(x=21);return x+y;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("short-circuit-in-eager-operand", original("int x=0;boolean b=(flag&&(x=7)>0)|((x+=35)>0);return b?x:0;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("for-expression-list-flow", original("int i;int total;for(i=0,total=36;i<3;i++,total+=i){}return total;"), "I", (), 42),
    ("for-update-order", original("int i;int sum;for(i=0,sum=0;i<3;sum+=i,i++){}return sum+39;"), "I", (), 42),
    ("for-init-list-once", original("int x=0;for(x++,x*=42;x<0;x++){}return x;"), "I", (), 42),
    ("for-update-list-continue", original("int i;int total;outer:for(i=0,total=36;i<3;i++,total+=i){continue outer;}return total;"), "I", (), 42),
    ("for-update-list-break", original("int i;int total;for(i=0,total=42;true;i++,total+=100){break;}return i+total;"), "I", (), 42),
    ("for-update-prefix-and-call", original("int i=0;int total=0;for(;i<3;++i,total+=plus(i)){}return total+33;", helpers="static int plus(int n){return n+1;}"), "I", (), 42),
    ("for-update-call-item", original("int i;int total;for(i=0,total=36;i<3;ignore(i++),total+=i){}return total;", helpers="static void ignore(int n){}"), "I", (), 42),
    ("for-init-call-items", original("int x;for(ignore(x=21),ignore(x+=21);x<0;){}return x;", helpers="static void ignore(int n){}"), "I", (), 42),
    ("long-chain-result", original("long a;long b;return a=b=0x123456789abcdef0L;", "long"), "J", (), 1311768467463790320),
    ("assignment-result-overload", original("byte b;return pick(b=42);", helpers="static int pick(byte n){return n;}static int pick(int n){return 0;}"), "I", (), 42),
]


INVALID = [
    ("unassigned-postfix", original("int x;return x++;"), "not definitely assigned"),
    ("unassigned-prefix", original("int x;return ++x;"), "not definitely assigned"),
    ("unassigned-compound-before-rhs", original("int x;return x+=(x=42);"), "not definitely assigned"),
    ("unassigned-rhs", original("int x;return x=x+42;"), "not definitely assigned"),
    ("earlier-call-argument", original("int x;return add(x,x=42);", helpers="static int add(int a,int b){return a+b;}"), "not definitely assigned"),
    ("earlier-binary-operand", original("int x;return x+(x=42);"), "not definitely assigned"),
    ("and-after-not-assigned", original("int x;boolean b=flag&&(x=42)>0;return x;", parameters="boolean flag"), "not definitely assigned"),
    ("or-after-not-assigned", original("int x;boolean b=flag||(x=42)>0;return x;", parameters="boolean flag"), "not definitely assigned"),
    ("conditional-single-arm", original("int x;int y=flag?(x=42):0;return x;", parameters="boolean flag"), "not definitely assigned"),
    ("boolean-assignment-loses-correlation", original("int x;boolean b;if(b=flag&&(x=42)>0)return x;return 0;", parameters="boolean flag"), "not definitely assigned"),
    ("boolean-cast-loses-correlation", original("int x;if((boolean)(flag&&(x=42)>0))return x;return 0;", parameters="boolean flag"), "not definitely assigned"),
    ("final-prefix", original("final int x=41;return ++x;"), "cannot assign an initialized final"),
    ("final-postfix", original("final int x=42;return x++;"), "cannot assign an initialized final"),
    ("final-nested-write", original("final int x=1;return x=42;"), "cannot assign an initialized final"),
    ("final-parameter", original("return x=42;", parameters="final int x"), "cannot assign an initialized final"),
    ("boolean-prefix", original("boolean b=true;return ++b;", "boolean"), "integer variable"),
    ("nonvariable-literal", original("return ++42;"), "requires a variable"),
    ("nonvariable-call", original("return f()++;", helpers="static int f(){return 42;}"), "requires a variable"),
    ("nonvariable-assignment-result", original("int x=0;return ++(x=41);"), "requires a variable"),
    ("nonvariable-conditional", original("int a=0;int b=1;return (flag?a:b)=42;", parameters="boolean flag"), "requires a variable"),
    ("narrow-nonconstant-assignment-result", original("int x;byte b=(x=1);return b;"), "incompatible assignment"),
    ("long-assignment-to-int", original("int x;return x=42L;"), "incompatible assignment"),
    ("integer-assignment-to-boolean", original("boolean b;return b=1;", "boolean"), "boolean value"),
    ("assigned-final-is-not-constant", original("int x;final int k=(x=42);switch(42){case k:return 42;default:return 0;}"), "constant expression"),
    ("assigned-true-not-constant-reachability", original("boolean b;while(b=true){}"), "missing return"),
    ("assignment-value-not-statement", original("int x=41;(x=42);return x;"), "only assignment"),
    ("conditional-third-not-assignment", original("int x=0;return flag?7:x=42;", parameters="boolean flag"), "requires a variable"),
    ("for-init-earlier-read", original("int x;int y;for(y=x,x=42;false;){}return y;"), "not definitely assigned"),
    ("for-update-earlier-read", original("int x;for(int i=0;i<3;i+=x,x=42){}return 0;"), "not definitely assigned"),
    ("for-update-not-guaranteed", original("int x;for(int i=0;i<3;i++,x=42){}return x;"), "not definitely assigned"),
    ("for-init-value-not-statement", original("int x=0;for(x=1,x+1;x<3;x++){}return x;"), "only assignment"),
    ("for-update-value-not-statement", original("for(int x=0;x<3;x++,x+1){}return 0;"), "only assignment"),
    ("for-update-declaration", original("for(int x=0;x<3;x++,int y=0){}return 0;"), "local declaration"),
    ("for-init-list-not-declaration", original("int x=0;for(x=1,int y=0;x<3;x++){}return x;"), "local declaration"),
    ("comma-not-general-operator", original("int x=0;return (x=21,x=42);"), "expected"),
    ("assignment-chain-narrowing", original("int x;byte b;return b=x=42;"), "incompatible assignment"),
    ("postfix-result-not-variable", original("int x=1;return (x++)=42;"), "requires a variable"),
]


TYPECHECK = []


@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[r[0] for r in CASES])
def test_assignment_original_owned_ir_object(name, text, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert result.emit_object().startswith(b"\x7fELF")


@pytest.mark.parametrize("name,text,message", INVALID, ids=[r[0] for r in INVALID])
def test_invalid_assignment_original_before_ir(name, text, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("invalid assignment source reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_source(text)
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("text", [
    "class Mutations{int x;int f(){return x=42;}}",
    "class Mutations{int x;int f(){return ++this.x;}}",
    original("int[] a={41};return ++a[0];"),
    original("int[] a={0};return a[0]=42;"),
    "class Mutations{static int[] run(){int[] a;return a=new int[1];}}",
])
def test_model_nonlocal_expression_writes_remain_explicit(text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"Mutations.java": text})
    assert caught.value.code == "java.unsupported.assignment-expression-target"


@pytest.mark.parametrize("text", [
    original("int x=41;return ++x;"),
    original("int x;return (x=21)+x;"),
    original("int x;(x)=42;return x;"),
    original("int x=41;(x)++;return x;"),
    original("int x;if(flag?((x=42)==42):false)return x;return 0;", parameters="boolean flag"),
])
def test_scalar_expression_mutation_is_shared_by_class_analysis(text):
    model = analyze_class_sources({"Mutations.java": text})
    assert model.programs[0].methods[0].result == "int"


def test_grouped_assignment_target_is_typed_without_an_unassigned_read():
    text = original("int x;return ((x)=42);")
    method = analyze_class_sources({"Mutations.java": text}).programs[0].methods[0]
    mutation = method.body.children[1].children[0].children[0]
    target = mutation.children[0]
    assert mutation.kind == "mutation" and mutation.constant is None
    assert target.kind == "group" and target.children[0].kind == "name"
    assert target.dtype == target.children[0].dtype == mutation.dtype == "int"
    assert target.symbol is target.children[0].symbol is mutation.symbol is method.locals[0]


def test_for_lists_keep_sequential_flow_and_one_enclosing_scope():
    text = original("int i;int total;for(i=0,total=36;i<3;i++,total+=i){}return total;")
    method = analyze_class_sources({"Mutations.java": text}).programs[0].methods[0]
    loop = method.body.children[2]
    initial, condition, update, body = loop.children
    assert initial.kind == update.kind == "sequence"
    assert [node.symbol.name for node in initial.children] == ["i", "total"]
    assert [node.symbol.name for node in update.children] == ["i", "total"]
    assert condition.children[0].symbol is update.children[0].symbol
    assert update.children[1].children[0].symbol is initial.children[0].symbol
    assert method.body.children[3].children[0].symbol is initial.children[1].symbol


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[r[0] for r in CASES])
def test_assignment_original_native_result(name, text, lane, arguments, expected, tmp_path, monkeypatch):
    (tmp_path / "Mutations.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert observe_scalar(result, tmp_path, monkeypatch, arguments) == expected


JDK_REASON = None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else "set PCC_JAVA_RAW_REFERENCE to an explicit existing JDK"
REFERENCE_REASON = NATIVE_REASON or JDK_REASON


def compile_reference_originals(tmp_path, request, *, runtime):
    """Separate outcome lanes share the same source/tool/compiler binding."""
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in ("17", "21")
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    root = Path(__file__).parent
    inputs = tuple(root / name for name in ("test_source_assignment_expressions.py", "NarrowReference.java", "owned_execution.py",
                                           "test_source_narrow_primitives.py", "test_source_raw_reference.py"))
    before = {"tools": tool_identity(java), "inputs": {str(p): digest(p) for p in inputs}}
    retained = {}
    (tmp_path / "owner-before.json").write_text(json.dumps({**before, "version": version.group(1),
        "source_level": level, "target_level": level, "platform_contract": "current selected JDK platform; not --release compatibility",
        "production_dependency": False}, indent=2))
    def finish():
        after = {"tools": tool_identity(java), "inputs": {str(p): digest(p) for p in inputs}}
        unchanged = all(digest(p) == h for p, h in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**after, "original_observer_hashes": {str(p): h for p, h in retained.items()},
                                                             "unchanged": unchanged}, indent=2))
        assert before == after and unchanged
    request.addfinalizer(finish)
    assert record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")[0] == 0
    modules = record_process((str(java), *JVM_LIMITS, "--list-modules"), tmp_path, "modules")
    assert modules[0] == 0 and any(line.split(b"@")[0] == b"jdk.compiler" for line in modules[1].splitlines())
    rows = []
    for name, text, lane, arguments, expected in (CASES if runtime else ()):
        directory = tmp_path / name
        directory.mkdir()
        unit, observer = directory / "Mutations.java", directory / "PccNarrowObserve.java"
        unit.write_text(text)
        observer.write_text(observer_source("Mutations", lane, arguments))
        for p in (unit, observer): retained[p] = digest(p)
        rows.append(name + "\trun\tMutations.java")
    for intent, cases in (() if runtime else (("reject", INVALID), ("typecheck", TYPECHECK))):
        for name, text, *_ in cases:
            directory = tmp_path / name
            directory.mkdir()
            unit = directory / "Mutations.java"
            unit.write_text(text)
            retained[unit] = digest(unit)
            rows.append(name + "\t" + intent + "\tMutations.java")
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
    batch = record_process((str(java), *JVM_LIMITS, "-cp", str(driver_classes), "NarrowReference", str(tmp_path), level),
                           tmp_path, "batch-compile", 30)
    assert batch[0] == 0, batch
    return java


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=REFERENCE_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_exact_assignment_originals_jdk_raw_runtime(tmp_path, monkeypatch, request):
    java = compile_reference_originals(tmp_path, request, runtime=True)
    # Every original is compiled first; each real JVM run precedes owned assertions.
    for name, text, lane, arguments, expected in CASES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", name
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"), "PccNarrowObserve"), directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            result = compile_source(text, descriptor=descriptor(lane, arguments))
        observe_scalar(result, native, monkeypatch, arguments)
        actual = record_process((str(native / "observed"),), native, "raw-native")
        expected_bytes = int(expected).to_bytes(WIDTHS[lane], "little", signed=lane not in ("Z", "C"))
        (directory / "comparison.json").write_text(json.dumps({"case": name, "comparison": "raw_process_streams",
            "source_sha256": hashlib.sha256(text.encode()).hexdigest(), "descriptor": descriptor(lane, arguments),
            "arguments": arguments, "observer_sha256": digest(directory / "PccNarrowObserve.java"),
            "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()}}, indent=2))
        assert actual == reference == (0, expected_bytes, b""), name


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_exact_assignment_invalid_originals_jdk_strict_rejection(tmp_path, request):
    # No waiver: the retained boolean-cast DA original remains a strict
    # rejection assertion, despite the observed selected-JDK acceptance.
    compile_reference_originals(tmp_path, request, runtime=False)
    for name, *_ in INVALID:
        assert int((tmp_path / name / "compiler-status.txt").read_text()) != 0, name
    for name, _ in TYPECHECK:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "0", name
