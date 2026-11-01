"""Native byte/short/char declarations, distinct Java values and PCC ABI lanes.

JLS21 5.1.2/5.1.3/5.2/5.3/5.6, 15.12/15.14/15.25/15.26.2:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-5.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html

No JVM native ABI is asserted. Java values are normalized i32 internally;
PCC's native integer ABI exposes byte=i8, short/char=i16. Independent assembly
supplies native arguments, and a separate JDK observer supplies raw reference
bytes from exactly the same original Java program and call selection.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, record_process, tool_identity
from tests.java.test_source_scalars import NATIVE_REASON


LANES = {"boolean": "Z", "byte": "B", "short": "S", "char": "C", "int": "I", "long": "J"}
WIDTHS = {"Z": 1, "B": 1, "S": 2, "C": 2, "I": 4, "J": 8}


def original(result, body, helpers="", parameters=""):
    return "class Narrow{" + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


# Each row is a complete original source and an explicitly selected ABI call.
CASES = [
    ("byte-local-wrap", original("int", "byte b=127;b++;return b;"), "I", (), -128),
    ("short-local-wrap", original("int", "short s=-32768;s--;return s;"), "I", (), 32767),
    ("char-local-wrap", original("int", "char c=65535;c++;return c;"), "I", (), 0),
    ("byte-long-compound", original("int", "byte b=127;b+=0x100000001L;return b;"), "I", (), -128),
    ("short-long-compound", original("int", "short s=1;s*=0x100008001L;return s;"), "I", (), -32767),
    ("char-long-compound", original("long", "char c=65535;c+=0x100000002L;return c;"), "J", (), 1),
    ("byte-logical-shift", original("int", "byte b=-128;b>>>=1;return b;"), "I", (), -64),
    ("char-arithmetic-shift", original("int", "char c=32768;c>>=1;return c;"), "I", (), 16384),
    ("byte-normalize-simple-store", original("int", "byte b=1;b=id((byte)-128);return b;", "static byte id(byte x){return x;}"), "I", (), -128),
    ("byte-return-call-widen-long", original("long", "return id((byte)-128);", "static byte id(byte x){return x;}"), "J", (), -128),
    ("short-return-call-widen-long", original("long", "return id((short)-32768);", "static short id(short x){return x;}"), "J", (), -32768),
    ("char-return-call-widen-long", original("long", "return id((char)65535);", "static char id(char x){return x;}"), "J", (), 65535),
    ("return-constant-narrow", original("byte", "return 127;"), "B", (), 127),
    ("direct-byte-negative", original("byte", "return x;", parameters="byte x"), "B", (("B", -128),), -128),
    ("direct-short-negative", original("short", "return x;", parameters="short x"), "S", (("S", -32768),), -32768),
    ("direct-char-high-bit", original("char", "return x;", parameters="char x"), "C", (("C", 65535),), 65535),
    ("byte-parameter-to-int", original("int", "return x;", parameters="byte x"), "I", (("B", -1),), -1),
    ("short-parameter-to-long", original("long", "return x;", parameters="short x"), "J", (("S", -1),), -1),
    ("char-parameter-to-long", original("long", "return x;", parameters="char x"), "J", (("C", 65535),), 65535),
    ("conditional-byte-store", original("byte", "byte x=-128;byte y=127;byte z=flag?x:y;return z;", parameters="boolean flag"), "B", (("Z", 1),), -128),
    ("conditional-byte-short", original("short", "byte x=-128;short y=32767;return flag?x:y;", parameters="boolean flag"), "S", (("Z", 0),), 32767),
    ("conditional-char-constant", original("char", "char x=65535;return flag?x:42;", parameters="boolean flag"), "C", (("Z", 1),), 65535),
    ("overload-byte-most-specific", original("int", "return f((byte)1);", "static int f(byte x){return 42;}static int f(short x){return 7;}static int f(int x){return 8;}"), "I", (), 42),
    ("overload-short-most-specific", original("int", "return f((short)1);", "static int f(short x){return 42;}static int f(int x){return 8;}"), "I", (), 42),
    ("overload-char-not-short", original("int", "return f((char)65535);", "static int f(short x){return 7;}static int f(char x){return 42;}static int f(int x){return 8;}"), "I", (), 42),
    ("invocation-no-constant-narrowing", original("int", "return f(1);", "static int f(byte x){return 7;}static int f(int x){return 42;}"), "I", (), 42),
    ("byte-to-short-widening", original("short", "byte x=-128;return f(x);", "static short f(short x){return x;}"), "S", (), -128),
    ("constant-local-narrow-context", original("char", "final int x=65535;char y=x;return y;"), "C", (), 65535),
    ("narrow-branch-definite-assignment", original("int", "short x;if(flag)x=-32768;else x=42;return x;", parameters="boolean flag"), "I", (("Z", 1),), -32768),
    ("byte-for-wrap", original("int", "int n=0;for(byte i=126;i!= -126;i++){n++;}return n;"), "I", (), 4),
    ("char-switch-selector", original("int", "switch(c){case 65535:return 42;default:return 0;}", parameters="char c"), "I", (("C", 65535),), 42),
    ("cast-char-to-byte", original("byte", "return (byte)c;", parameters="char c"), "B", (("C", 65535),), -1),
    ("byte-compound-division", original("byte", "b/=-1;return b;", parameters="byte b"), "B", (("B", -128),), -128),
    ("eight-lanes-independent-entry", original("int", "if(a!=-128)return 1;if(b!=-32768)return 2;if(c!=65535)return 3;if(d!=-9223372036854775808L)return 4;if(e!=-2147483648)return 5;if(f!=127)return 6;if(g!=32767)return 7;if(h!=32768)return 8;return 42;", parameters="byte a,short b,char c,long d,int e,byte f,short g,char h"), "I", (("B", -128), ("S", -32768), ("C", 65535), ("J", -9223372036854775808), ("I", -2147483648), ("B", 127), ("S", 32767), ("C", 32768)), 42),
    ("eight-lanes-owned-call", original("int", "return f((byte)-128,(short)-32768,(char)65535,-9223372036854775808L,-2147483648,(byte)127,(short)32767,(char)32768);", "static int f(byte a,short b,char c,long d,int e,byte f,short g,char h){if(a!=-128)return 1;if(b!=-32768)return 2;if(c!=65535)return 3;if(d!=-9223372036854775808L)return 4;if(e!=-2147483648)return 5;if(f!=127)return 6;if(g!=32767)return 7;if(h!=32768)return 8;return 42;}"), "I", (), 42),
    # These three exact formerly unsupported sources migrate without rewriting.
    ("original-byte-result", "class Diagnostic{static byte run(){return 42;}}", "B", (), 42),
    ("original-short-parameter", "class Diagnostic{static int run(short a){return a;}}", "I", (("S", -32768),), -32768),
    ("original-final-short-local", "class Diagnostic{static int run(){final short value=42;return value;}}", "I", (), 42),
]

INVALID = [
    ("constant-byte-overflow", original("int", "byte b=128;return b;"), "incompatible assignment"),
    ("constant-short-overflow", original("int", "short s=32768;return s;"), "incompatible assignment"),
    ("constant-char-negative", original("int", "char c=-1;return c;"), "incompatible assignment"),
    ("constant-char-overflow", original("int", "char c=65536;return c;"), "incompatible assignment"),
    ("long-constant-not-narrowed", original("byte", "return 1L;"), "incompatible assignment"),
    ("runtime-int-not-narrowed", original("byte", "return x;", parameters="int x"), "incompatible assignment"),
    ("byte-to-char-not-widening", original("char", "byte b=1;return b;"), "incompatible assignment"),
    ("char-to-short-not-widening", original("short", "char c=1;return c;"), "incompatible assignment"),
    ("call-does-not-narrow-constant", original("int", "return f(1);", "static int f(byte b){return b;}"), "unresolved same-class"),
    ("short-argument-not-byte", original("int", "return f((short)1);", "static int f(byte b){return b;}"), "unresolved same-class"),
    ("ambiguous-narrow-overloads", original("int", "return f((byte)1,(byte)2);", "static int f(short x,byte y){return 1;}static int f(byte x,short y){return 2;}"), "ambiguous"),
    ("constant-return-overflow", original("byte", "return 128;"), "incompatible assignment"),
    ("final-narrow-reassignment", original("int", "final byte b=1;b++;return b;"), "final local"),
    ("unassigned-narrow", original("int", "short s;return s;"), "not definitely assigned"),
    ("narrow-does-not-make-boolean", original("int", "byte b=1;if(b)return 42;return 0;"), "condition requires boolean"),
]


def descriptor(lane, arguments):
    return "(" + "".join(kind for kind, _ in arguments) + ")" + lane


@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_narrow_original_has_owned_ir_and_object(name, text, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert result.emit_object().startswith(b"\x7fELF")
    assert "nsw" not in str(result.module) and "nuw" not in str(result.module)


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_narrow_original_fails_before_ir(name, text, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("invalid narrow source reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_source(text)
    assert type(caught.value) is JavaError and caught.value.code is None


def test_narrow_canonical_values_are_separate_from_abi_and_calls():
    text = original("long", "return f(b,s,c);", "static char f(byte b,short s,char c){c+=b+s;return c;}", "byte b,short s,char c")
    ir_text = str(compile_source(text, descriptor="(BSC)J").module)
    assert "i8" in ir_text and "i16" in ir_text and "alloca i32" in ir_text
    assert "sext i8" in ir_text and "sext i16" in ir_text and "zext i16" in ir_text
    assert "trunc i32" in ir_text and "call i16" in ir_text
    assert "sext i32" in ir_text  # canonical nonnegative char -> long is exact


def test_narrow_overload_identity_crosses_source_units_without_reencoding():
    sources = {"Main.java": "class Main{static int run(){return Other.pick((byte)1)+Other.pick((char)2);}}",
               "Other.java": "class Other{static int pick(byte x){return 20;}static int pick(char x){return 22;}}"}
    result = compile_sources(sources, entry_class="Main")
    assert ("Other", "pick", "(B)I") in result.emitted_class_methods
    assert ("Other", "pick", "(C)I") in result.emitted_class_methods


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_original_narrow_native_result(name, text, lane, arguments, expected, tmp_path, monkeypatch):
    (tmp_path / "Original.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert observe_scalar(result, tmp_path, monkeypatch, arguments) == expected


def observer_source(owner, lane, arguments):
    spelling = {"B": "byte", "S": "short", "C": "char"}
    values = [("true" if value else "false") if kind == "Z" else
              "(" + spelling[kind] + ")" + str(value) if kind in spelling else
              str(value) + ("L" if kind == "J" else "") for kind, value in arguments]
    call = owner + ".run(" + ",".join(values) + ")"
    value = "(" + call + "?1L:0L)" if lane == "Z" else call
    return ("public final class PccNarrowObserve{public static void main(String[] args){long value=" + value +
            ";for(int i=0;i<" + str(WIDTHS[lane]) + ";i++)System.out.write((int)(value >>> (8*i))&255);System.out.flush();}}")


def test_narrow_reference_observer_is_independent_width_and_one_call():
    for lane in ("B", "S", "C"):
        text = observer_source("Original", lane, (("B", -128), ("S", -32768), ("C", 65535)))
        assert text.count("Original.run(") == 1
        assert "(byte)-128,(short)-32768,(char)65535" in text
        assert "i<" + str(WIDTHS[lane]) in text


REFERENCE_REASON = NATIVE_REASON or (None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                                   "set PCC_JAVA_RAW_REFERENCE to an explicit existing JDK")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=REFERENCE_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_exact_narrow_originals_match_jdk_raw_processes_and_rejections(tmp_path, monkeypatch, request):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in ("17", "21")
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    inputs = (Path(__file__), Path(__file__).with_name("NarrowReference.java"), Path(__file__).with_name("owned_execution.py"))
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    (tmp_path / "owner-before.json").write_text(json.dumps({**before, "version": version.group(1),
        "source_level": level, "target_level": level, "platform_contract": "current selected JDK platform; not --release compatibility",
        "production_dependency": False}, indent=2))

    def finish():
        after = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
        unchanged = all(digest(path) == checksum for path, checksum in retained.items())
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
        owner = re.search(r"class (\w+)", text).group(1)
        unit = directory / (owner + ".java")
        unit.write_text(text)
        observer = directory / "PccNarrowObserve.java"
        observer.write_text(observer_source(owner, lane, arguments))
        for path in (unit, observer): retained[path] = digest(path)
        rows.append(name + "\trun\t" + unit.name)
    for name, text, message in INVALID:
        directory = tmp_path / name
        directory.mkdir()
        unit = directory / "Narrow.java"
        unit.write_text(text)
        retained[unit] = digest(unit)
        rows.append(name + "\treject\tNarrow.java")
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
    # All originals are reference-compiled before any expected-value assertion.
    for name, text, lane, arguments, expected in CASES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", name
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"), "PccNarrowObserve"),
                                   directory, "reference-run")
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
    for name, text, message in INVALID:
        assert int((tmp_path / name / "compiler-status.txt").read_text()) != 0, name
