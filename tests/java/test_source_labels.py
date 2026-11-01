"""Owned labeled control flow, not bytecode label reconstruction.

JLS21 3.8/3.9 (label Identifiers), 14.7, 14.15, 14.16, 14.22 and 16.2.5, 16.2.10-13:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html

Target identity, label scope and DA are independent of spelling and runtime
constant folding. Existing owned assembly and unchanged JDK batch/observer
helpers compare full raw lanes for the exact same complete Java originals.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import JavaError, analyze_class_sources, compile_source
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_narrow_primitives import WIDTHS, descriptor, observer_source
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, record_process, tool_identity
from tests.java.test_source_scalars import NATIVE_REASON


def original(body, result="int", parameters="", helpers=""):
    return "class Labels{" + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


CASES = [
    ("block-exit", original("int x=0;done:{x=42;break done;}return x;"), "I", (), 42),
    ("nested-block-exit", original("int x=0;done:{{x=42;break done;}}return x;"), "I", (), 42),
    ("sibling-label-identity", original("int x=0;again:{x=20;break again;}again:{x+=22;break again;}return x;"), "I", (), 42),
    ("label-local-namespace", original("int done=42;done:{break done;}return done;"), "I", (), 42),
    ("label-method-namespace", original("same:{break same;}return same();", helpers="static int same(){return 42;}"), "I", (), 42),
    ("per-method-label-scope", original("done:{break done;}return helper();", helpers="static int helper(){done:{break done;}return 42;}"), "I", (), 42),
    ("empty-label", original("empty:;return 42;"), "I", (), 42),
    ("contextual-label-identifiers", original("int x=0;var:{x++;break var;}yield:{x++;break yield;}record:{x++;break record;}sealed:{x++;break sealed;}permits:{x++;break permits;}return x+37;"), "I", (), 42),
    ("contextual-continue-label", original("int x=0;var:for(int i=0;i<2;i++){x++;continue var;}return x+40;"), "I", (), 42),
    ("expression-label", original("int x=41;add:x++;return x;"), "I", (), 42),
    ("if-label", original("int x=0;done:if(flag){x=42;break done;}else{x=7;}return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("nested-label-break-outer", original("int x=0;outer:inner:{x=42;break outer;}return x;"), "I", (), 42),
    ("nested-label-break-inner", original("int x=0;outer:{inner:{x=20;break inner;}x+=22;}return x;"), "I", (), 42),
    ("outer-while-break", original("int x=0;outer:while(true){while(true){x=42;break outer;}}return x;"), "I", (), 42),
    ("outer-for-continue-update", original("int i=0;int n=0;outer:for(;i<4;i++){for(int j=0;j<3;j++){n++;continue outer;}}return i*10+n-2;"), "I", (), 42),
    ("outer-while-continue", original("int i=0;int n=0;outer:while(i<4){i++;for(int j=0;j<3;j++){n++;continue outer;}}return i*10+n-2;"), "I", (), 42),
    ("outer-do-continue-test", original("int i=0;int n=0;outer:do{i++;while(true){n++;continue outer;}}while(i<4);return i*10+n-2;"), "I", (), 42),
    ("inner-unlabeled-continue", original("int n=0;outer:for(int i=0;i<2;i++){for(int j=0;j<3;j++){n++;continue;}}return n+36;"), "I", (), 42),
    ("block-label-unlabeled-loop-continue", original("int n=0;for(int i=0;i<2;i++){block:{n++;continue;}}return n+40;"), "I", (), 42),
    ("inner-unlabeled-break", original("int n=0;outer:for(int i=0;i<2;i++){while(true){n++;break;}}return n+40;"), "I", (), 42),
    ("switch-to-outer-continue", original("int i=0;int n=0;outer:for(;i<4;i++){switch(i){case 0:case 2:n+=10;continue outer;default:n++;}}return i+n+16;"), "I", (), 42),
    ("switch-to-label-break", original("int n=0;done:{switch(1){case 1:n=42;break done;default:n=7;}}return n;"), "I", (), 42),
    ("switch-unlabeled-break-stays-local", original("int n=0;done:{switch(1){case 1:n=20;break;default:n=7;}n+=22;}return n;"), "I", (), 42),
    ("labeled-switch", original("int n=0;choice:switch(1){case 1:n=42;break choice;default:n=7;}return n;"), "I", (), 42),
    ("arrow-switch-to-loop", original("int i=0;int n=0;outer:for(;i<3;i++){switch(i){case 0->{n+=20;continue outer;}default->{n++;}}}return n+20;"), "I", (), 42),
    ("da-all-labeled-exits", original("int x;done:{if(flag){x=42;break done;}else{x=7;break done;}}return x;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("da-normal-and-break-exits", original("int x;done:{if(flag){x=7;break done;}x=42;}return x;", parameters="boolean flag"), "I", (("Z", 0),), 42),
    ("da-outer-do-continue", original("int x;int i=0;outer:do{x=42;i++;while(true){continue outer;}}while(i<2);return x;"), "I", (), 42),
    ("for-update-da-from-continue", original("int x;int n=0;outer:for(;;x++){x=41;if(n==1)break outer;n++;continue outer;}return x+1;"), "I", (), 42),
    ("return-through-label", original("answer:return 42;"), "I", (), 42),
    ("label-long-full-width", original("long x;done:{x=0x12345678abcdef01L;break done;}return x;", "long"), "J", (), 0x12345678abcdef01),
    ("label-narrow-wrap", original("byte x=127;done:{x++;break done;}return x;", "byte"), "B", (), -128),
    ("label-boolean", original("boolean x;done:{x=true;break done;}return x;", "boolean"), "Z", (), 1),
]

INVALID = [
    ("duplicate-enclosing-label", original("same:same:while(true){break same;}return 42;"), "duplicate enclosing"),
    ("duplicate-nested-block-label", original("same:{same:{break same;}}return 42;"), "duplicate enclosing"),
    ("undefined-break-label", original("break missing;"), "undefined enclosing"),
    ("undefined-continue-label", original("while(true){continue missing;}"), "undefined enclosing"),
    ("label-is-not-local", original("int done=42;break done;"), "undefined enclosing"),
    ("expired-label", original("done:;break done;"), "undefined enclosing"),
    ("other-method-label", original("done:{break done;}return helper();", helpers="static int helper(){break done;}"), "undefined enclosing"),
    ("continue-block-label", original("block:{while(true){continue block;}}return 42;"), "directly label a loop"),
    ("continue-switch-label", original("choice:switch(1){default:continue choice;}return 42;"), "directly label a loop"),
    ("continue-outer-label-chain", original("outer:inner:while(true){continue outer;}"), "directly label a loop"),
    ("unlabeled-break-not-label", original("done:{break;}return 42;"), "requires an enclosing"),
    ("unlabeled-continue-not-label", original("done:{continue;}return 42;"), "requires an enclosing"),
    ("label-local-declaration", original("done:int x=42;return x;"), "local declaration requires"),
    ("break-no-definite-assignment", original("int x;done:{if(flag)break done;x=42;}return x;", parameters="boolean flag"), "not definitely assigned"),
    ("continue-do-no-definite-assignment", original("int x;outer:do{if(flag)continue outer;x=42;}while(false);return x;", parameters="boolean flag"), "not definitely assigned"),
    ("continue-for-update-unassigned", original("int x;outer:for(;;x++){continue outer;}"), "not definitely assigned"),
    ("missing-return-after-label-break", original("done:{break done;}"), "missing return"),
    ("unreachable-after-label-return", original("done:return 42;return 0;"), "unreachable statement"),
    ("unreachable-after-label-continue", original("outer:while(true){continue outer;}return 42;"), "unreachable statement"),
]

# Accepted source whose execution does not terminate: reference TYPECHECK only.
TYPECHECK = [
    ("constant-false-break-is-reachable", original("outer:while(true){if(false)break outer;}return 42;")),
    ("infinite-labeled-continue", original("outer:while(true){continue outer;}")),
    ("continue-inner-label-chain", original("outer:inner:while(true){continue inner;}")),
]


@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[r[0] for r in CASES])
def test_labeled_original_owned_ir_object(name, text, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert result.emit_object().startswith(b"\x7fELF")


@pytest.mark.parametrize("name,text,message", INVALID, ids=[r[0] for r in INVALID])
def test_invalid_label_original_before_ir(name, text, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("invalid labeled source reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_source(text)
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text", TYPECHECK, ids=[r[0] for r in TYPECHECK])
def test_nonterminating_label_original_is_compile_only(name, text):
    assert compile_source(text).emit_object().startswith(b"\x7fELF")


def test_sibling_spelling_binds_distinct_typed_targets():
    text = next(row[1] for row in CASES if row[0] == "sibling-label-identity")
    model = analyze_class_sources({"Labels.java": text})
    program = model.programs[0]
    method = program.methods[0]
    first, second = [node for node in method.body.children if node.kind == "label"]
    assert first.value == second.value == "again" and first is not second
    assert first.children[0].children[-1].control_target is first
    assert second.children[0].children[-1].control_target is second


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[r[0] for r in CASES])
def test_labeled_original_native_result(name, text, lane, arguments, expected, tmp_path, monkeypatch):
    (tmp_path / "Labels.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert observe_scalar(result, tmp_path, monkeypatch, arguments) == expected


REFERENCE_REASON = NATIVE_REASON or (None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                                   "set PCC_JAVA_RAW_REFERENCE to an explicit existing JDK")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=REFERENCE_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_exact_labeled_originals_jdk_raw_and_rejections(tmp_path, monkeypatch, request):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in ("17", "21")
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    root = Path(__file__).parent
    inputs = tuple(root / name for name in ("test_source_labels.py", "NarrowReference.java", "owned_execution.py",
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
    for name, text, lane, arguments, expected in CASES:
        directory = tmp_path / name
        directory.mkdir()
        unit, observer = directory / "Labels.java", directory / "PccNarrowObserve.java"
        unit.write_text(text)
        observer.write_text(observer_source("Labels", lane, arguments))
        for p in (unit, observer): retained[p] = digest(p)
        rows.append(name + "\trun\tLabels.java")
    for intent, cases in (("reject", INVALID), ("typecheck", TYPECHECK)):
        for name, text, *_ in cases:
            directory = tmp_path / name
            directory.mkdir()
            unit = directory / "Labels.java"
            unit.write_text(text)
            retained[unit] = digest(unit)
            rows.append(name + "\t" + intent + "\tLabels.java")
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
    for name, *_ in INVALID:
        assert int((tmp_path / name / "compiler-status.txt").read_text()) != 0, name
    for name, _ in TYPECHECK:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "0", name
