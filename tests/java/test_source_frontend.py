"""Original Java source -> typed AST -> PCC IR -> owned native observations.

No javac oracle or class-file translation is used by the source compiler.
Independent literal .class comparisons are compatibility checks only.
"""
import platform
import subprocess
import sys

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source
from tests.java.owned_execution import observe_i32


NATIVE_REASON = None if sys.platform == "linux" and platform.machine() == "x86_64" else "owned Java source observer requires Linux x86_64"


def source(body, helpers="", name="Example"):
    return "public class " + name + " { " + helpers + " public static int run(){" + body + "} }"


def forbid_bytecode_or_external_owner(monkeypatch):
    import pcc.frontends.java as java
    from pcc.frontends.java import bytecode, classfile, compiler
    def forbidden(*args, **kwargs):
        raise AssertionError("Java source attempted a bytecode/JVM/external-tool route")
    for module, name in ((java, "compile_class"), (java, "parse_class"), (compiler, "compile_class"),
                         (compiler, "parse_class"), (classfile, "parse_class"), (bytecode, "decode"),
                         (bytecode, "verify_method"), (subprocess, "Popen")):
        monkeypatch.setattr(module, name, forbidden)


def test_source_entry_uses_direct_public_ir_and_owned_object_without_bytecode(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compilation = compile_source(source("return 42;"), filename="Example.java")
    assert compilation.class_name == "Example"
    assert compilation.entry == ("run", "()I")
    assert "ret i32 42" in str(compilation.module)
    assert "bc_" not in str(compilation.module)
    assert compilation.emit_object()[:4] == b"\x7fELF"


def test_original_eight_argument_source_keeps_java_signature_and_export(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    original = ("public class Interop { public static int mix(int a,int b,int c,int d,"
                "int e,int f,int g,int h) { return a-b-c-d-e-f-g-h; } }")
    compilation = compile_source(original, filename="Interop.java", entry="mix", descriptor="(IIIIIIII)I")
    assert compilation.entry_symbol == "pcc_java_496e7465726f70_6d6978_2849494949494949492949"
    text = str(compilation.module)
    assert text.count("sub i32") == 7 and "nsw" not in text and "nuw" not in text
    assert compilation.emit_object()[:4] == b"\x7fELF"


@pytest.mark.parametrize("target", [None, "", "i386-unknown-linux-gnu", "x86_64-unknown-linux-gnux32",
                                  "aarch64-unknown-linux-gnu_ilp32", "arm64-apple-darwin-bogus",
                                  "x86_64-pc-windows-msvc", 'x86_64-unknown-linux-gnu"\nsource_filename="x'])
def test_source_target_admission_precedes_public_ir(target, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid target reached source lowering")
    monkeypatch.setattr(SourceLowerer, "lower", forbidden)
    with pytest.raises(UnsupportedJava, match="target/ABI"):
        compile_source(source("return 42;"), target=target)


def test_source_arm64_darwin_cross_emission_is_an_owned_object_not_device_execution(monkeypatch):
    from pcc.backend import macho_spec
    forbid_bytecode_or_external_owner(monkeypatch)
    compilation = compile_source(source("return add(40,2);", "static int add(int a,int b){return a+b;}"),
                                 target="arm64-apple-darwin")
    parsed = macho_spec.parse_object(compilation.emit_object())
    assert parsed.header["cputype"] == macho_spec.CPU_TYPE_ARM64
    assert parsed.header["filetype"] == macho_spec.MH_OBJECT
    assert "_" + compilation.entry_symbol in {symbol["name"] for symbol in parsed.symbols()}


@pytest.mark.parametrize("body,helpers,message", [
    ("int x;return x;", "", "not definitely assigned"),
    ("int x=x;return x;", "", "not definitely assigned"),
    ("int x; if(f(1)>0){x=2;} return x;", "static int f(int a){return a;}", "not definitely assigned"),
    ("int x;while(f(1)>0){x=2;}return x;", "static int f(int a){return a;}", "not definitely assigned"),
    ("int x=1;{int x=2;}return x;", "", "already in scope"),
    ("return f(1);", "static int f(int a){int a=2;return a;}", "already in scope"),
    ("return f(1,2);", "static int f(int a,int a){return a;}", "already in scope"),
    ("{int x=1;}return x;", "", "undefined local"),
    ("for(int i=0;i<2;i++){}return i;", "", "undefined local"),
    ("if(1)return 1;return 0;", "", "requires boolean"),
    ("return true;", "", "integer value required"),
    ("return 1L;", "", "incompatible assignment conversion"),
    ("return 1<2;", "", "integer value required"),
    ("return 1 + false;", "", "integer value required"),
    ("return f();", "static void f(){return;}", "integer value required"),
    ("return;", "", "must return a value"),
    ("int x=1;", "", "missing return"),
    ("if(true)return 1;", "", "missing return"),
    ("return 1;int x=2;", "", "unreachable statement"),
    ("while(false){}return 1;", "", "unreachable loop body"),
    ("break;return 1;", "", "enclosing loop"),
    ("continue;return 1;", "", "enclosing loop"),
    ("return f();", "", "unresolved same-class"),
    ("return 1;", "static int f(){return 1;}static void f(){return;}", "duplicate method signature"),
    ("return 2147483648;", "", "literal exceeds"),
    ("return -(2147483648);", "", "literal exceeds"),
    ("return 0x100000000;", "", "literal exceeds"),
    ("return 09;", "", "invalid int literal"),
    ("return 1_;", "", "invalid int literal"),
    ("return 0x_1;", "", "invalid int literal"),
])
def test_source_semantic_errors_are_rejected_before_ir(body, helpers, message, monkeypatch):
    from pcc.frontends.java import source_lowering
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid source reached IR lowering")
    monkeypatch.setattr(source_lowering.SourceLowerer, "lower", forbidden)
    with pytest.raises(JavaError, match=message):
        compile_source(source(body, helpers), filename="Bad.java")


@pytest.mark.parametrize("original,message", [
    (source("return 1/0;"), "ArithmeticException"),
    (source("int x=1;x%=2;return x;"), "ArithmeticException"),
    ("import p.Library;" + source("return 1;"), "dependency resolution"),
    ("class C extends D {}", "shared Java runtime"),
    ("class C {static int value;}", "fields"),
    ("class C {int run(){return 1;}}", "static source methods"),
    ("class C {static synchronized int run(){return 1;}}", "static source methods"),
    (source("return new Object();"), "unsupported Java"),
    (source("return 1; // \\u000a comment\n"), "Unicode-escape"),
    (source("int π=1;return π;"), "unsupported Java source token"),
])
def test_source_runtime_and_lexical_boundaries_fail_explicitly(original, message):
    with pytest.raises(UnsupportedJava, match=message):
        compile_source(original)


@pytest.mark.parametrize("literal,expected", [
    ("-2147483648", -2147483648), ("0xffffffff", -1), ("0x80000000", -2147483648),
    ("0b11111111111111111111111111111111", -1), ("037777777777", -1),
    ("2_147_483_647", 2147483647), ("4__2", 42), ("0_52", 42),
])
def test_java_integer_literal_lexical_values(literal, expected):
    assert "ret i32 " + str(expected) in str(compile_source(source("return " + literal + ";")).module)


def test_package_identity_and_all_source_methods_are_analyzed():
    compilation = compile_source("package sample.tools;" + source("return f();", "static int f(){return 42;}"))
    assert compilation.class_name == "sample/tools/Example"
    assert compilation.admitted_methods == (("f", "()I"), ("run", "()I"))
    with pytest.raises(JavaError, match="not definitely assigned"):
        compile_source(source("return 42;", "static int unused(){int x;return x;}"))


@pytest.mark.parametrize("name", ["hashCode", "getClass", "toString", "clone", "finalize", "notify", "notifyAll", "wait"])
def test_implicit_object_instance_methods_cannot_be_hidden_by_static_methods(name):
    with pytest.raises(JavaError, match="inherited java.lang.Object instance"):
        compile_source(source("return " + name + "();", "static int " + name + "(){return 42;}"))
    # A distinct int-parameter overload does not share Object's signature.
    compile_source(source("return " + name + "(42);", "static int " + name + "(int x){return x;}"))


@pytest.mark.parametrize("body", [
    "int x;if(false)return x;return 42;",
    "int x;if(false && x>0)return 1;return 42;",
    "int x;if(true || x>0)return 42;return 1;",
    "int x;if(!(true))return x;return 42;",
    "int x;if(a>0 || true)x=42;return x;",
    "int x;if(a>0 && false)return x;return 42;",
    "int x;while(true){if(false)break;x=42;break;}return x;",
])
def test_boolean_truth_specific_definite_assignment_is_distinct_from_reachability(body):
    compile_source(source("return f(1);", "static int f(int a){" + body + "}"))


@pytest.mark.parametrize("body,message", [
    ("if(false){int x;return x;}return 42;", "not definitely assigned"),
    ("int x;if(a>0)return x;return 42;", "not definitely assigned"),
    ("int x;if(a>0 || false)x=42;return x;", "not definitely assigned"),
    ("while(a>0 || true){}", "missing return"),
])
def test_vacuous_da_does_not_initialize_new_locals_or_change_loop_reachability(body, message):
    with pytest.raises(JavaError, match=message):
        compile_source(source("return f(1);", "static int f(int a){" + body + "}"))


NATIVE = [
    (source("return 42;"), 42),
    (source("return add(2147483647,1);", "static int add(int a,int b){return a+b;}"), -2147483648),
    (source("return shift(-1,32);", "static int shift(int a,int b){return a>>>b;}"), -1),
    (source("return shift(-1,-1);", "static int shift(int a,int b){return a>>>b;}"), 1),
    (source("return narrow(255);", "static int narrow(int a){return (byte)a;}"), -1),
    (source("return narrow(-1);", "static int narrow(int a){return (char)a;}"), 65535),
    (source("int sum=0;for(int i=0;i<100;i++){sum+=i*i;}return sum;"), 328350),
    (source("int x=0;int sum=0;while(x<10){x++;if(x==3)continue;if(x==8)break;sum+=x;}return sum;"), 25),
    (source("int x;if(1<2){x=42;}else{x=7;}return x;"), 42),
    (source("int x;while(true){x=42;break;}return x;"), 42),
    (source("return f(1);", "static int f(int a){int x;if(a>0 || true)x=42;return x;}"), 42),
    (source("int x;if(false && x>0)return 1;if(true || x>0)return 42;return 3;"), 42),
    (source("return fib(10);", "static int fib(int n){if(n<2)return n;return fib(n-1)+fib(n-2);}"), 55),
    (source("if(false && spin()>0)return 1;if(true || spin()>0)return 42;return 3;", "static int spin(){while(true){}}"), 42),
    (source("return mix(-2000000000,1,2,4,8,16,32,64);",
            "static int mix(int a,int b,int c,int d,int e,int f,int g,int h){return a-b-c-d-e-f-g-h;}"), -2000000127),
]


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("original,expected", NATIVE)
def test_original_java_source_executes_full_i32_without_bytecode(tmp_path, monkeypatch, original, expected):
    assert sys.platform == "linux" and platform.machine() == "x86_64"
    (tmp_path / "Example.java").write_text(original)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compilation = compile_source(original, filename="Example.java")
        assert compilation.emit_object()[:4] == b"\x7fELF"
    assert observe_i32(compilation, tmp_path, monkeypatch) == expected
    assert (tmp_path / "Example.java").read_text() == original


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_source_and_separate_class_experiment_agree_on_full_i32_semantics(tmp_path, monkeypatch):
    from pcc.frontends.java import compile_class
    from tests.java.class_builder import ClassBuilder, u2
    original = source("return add(2147483647,1);", "static int add(int a,int b){return a+b;}")
    builder = ClassBuilder("Example")
    number, call = builder.integer(2147483647), builder.method_ref("add", "(II)I")
    data = (builder.method("run", b"\x13" + u2(number) + b"\x04\xb8" + u2(call) + b"\xac")
            .method("add", b"\x1a\x1b\x60\xac", "(II)I").build())
    source_dir, class_dir = tmp_path / "source", tmp_path / "class"
    source_dir.mkdir()
    class_dir.mkdir()
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        owned_source = compile_source(original)
    owned_class = compile_class(data)
    assert owned_source.entry_symbol == owned_class.entry_symbol
    assert observe_i32(owned_source, source_dir, monkeypatch) == -2147483648
    assert observe_i32(owned_class, class_dir, monkeypatch) == -2147483648
