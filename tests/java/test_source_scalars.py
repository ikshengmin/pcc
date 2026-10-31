"""Original Java scalar semantics; exact byte observers are independent of lowering."""
import json
import platform
import sys

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


NATIVE_REASON = None if sys.platform == "linux" and platform.machine() == "x86_64" else "owned scalar observer requires Linux x86_64"
DESCRIPTOR = {"int": "I", "long": "J", "boolean": "Z"}


def source(result, body, helpers=""):
    return "public class Scalar {" + helpers + "public static " + result + " run(){" + body + "}}"


CASES = [
    ("int-add-wrap-before-long-return", "long", "return add(2147483647,1);", "static long add(int a,int b){return a+b;}", -2147483648),
    ("cast-before-multiply-preserves-long-square", "long", "return square(-2147483648);", "static long square(int y){return (long)y*y;}", 4611686018427387904),
    ("boolean-variable-not", "boolean", "return flip(false);", "static boolean flip(boolean x){return !x;}", 1),
    ("mixed-width-signed-compare", "boolean", "return less(-1,0xffffffffL);", "static boolean less(int a,long b){return a<b;}", 1),
    ("mixed-width-bitwise-sign-extension", "long", "return bits(0x123456789abcdef0L,-1);", "static long bits(long a,int b){return a&b;}", 1311768467463790320),
    ("long-literal-selects-long-overload", "long", "return pick(1L);", "static int pick(int x){return 42;}static long pick(long x){return 7L;}", 7),
    ("long-max", "long", "return 9223372036854775807L;", "", 9223372036854775807),
    ("long-min", "long", "return -9223372036854775808L;", "", -9223372036854775808),
    ("long-add-wrap", "long", "return add(9223372036854775807L,1L);", "static long add(long a,long b){return a+b;}", -9223372036854775808),
    ("long-sub-wrap", "long", "return sub(-9223372036854775808L,1L);", "static long sub(long a,long b){return a-b;}", 9223372036854775807),
    ("long-neg-min", "long", "return neg(-9223372036854775808L);", "static long neg(long a){return -a;}", -9223372036854775808),
    ("long-mul-wrap", "long", "return mul(0x100000001L,0x100000001L);", "static long mul(long a,long b){return a*b;}", 8589934593),
    ("long-logical-mask64", "long", "return shift(-1L,64);", "static long shift(long a,int b){return a>>>b;}", -1),
    ("long-logical-negative-count", "long", "return shift(-1L,-1L);", "static long shift(long a,long b){return a>>>b;}", 1),
    ("long-left-mask65", "long", "return shift(1L,65);", "static long shift(long a,int b){return a<<b;}", 2),
    ("int-long-shift-count", "int", "return shift(1,4294967329L);", "static int shift(int a,long b){return a<<b;}", 2),
    ("long-arithmetic-shift", "long", "return shift(-8L,66L);", "static long shift(long a,long b){return a>>b;}", -2),
    ("compound-long-narrows-int", "int", "int x=1;x+=0x100000001L;return x;", "", 2),
    ("compound-int-long-shift", "int", "int x=-1;x>>>=32L;return x;", "", -1),
    ("widen-negative-int", "long", "return widen(-2147483648);", "static long widen(int x){return x;}", -2147483648),
    ("long-cast-int", "int", "return narrow(0xfedcba9876543210L);", "static int narrow(long x){return (int)x;}", 1985229328),
    ("long-cast-byte", "int", "return narrow(0x100000080L);", "static int narrow(long x){return (byte)x;}", -128),
    ("long-cast-char", "int", "return narrow(-1L);", "static int narrow(long x){return (char)x;}", 65535),
    ("long-return-boolean", "boolean", "return isMin(-9223372036854775808L);", "static boolean isMin(long x){return x==-9223372036854775808L;}", 1),
    ("boolean-short-circuit", "boolean", "boolean x=true||spin();return x;", "static boolean spin(){while(true){}}", 1),
    ("boolean-compound", "boolean", "boolean x=true;x^=true;x|=false;x&=true;return x;", "", 0),
    ("overload-int-over-long", "int", "return pick(1);", "static int pick(int x){return 42;}static long pick(long x){return 7L;}", 42),
    ("overload-cast-byte-promotes", "int", "return pick((byte)1);", "static int pick(int x){return 42;}static long pick(long x){return 7L;}", 42),
    ("overload-boolean", "int", "return pick(true);", "static int pick(int x){return 7;}static int pick(boolean x){return 42;}", 42),
    ("invocation-widens-int", "long", "return onlyLong(-1);", "static long onlyLong(long x){return x;}", -1),
    ("long-for-boolean-local", "long", "long sum=0;for(long i=0;i<100;i++){sum+=i*i;}boolean ok=sum==328350L;if(ok)return sum;return -1;", "", 328350),
    ("long-suffix-changes-hex-type", "long", "return 0xffffffffL;", "", 4294967295),
    ("unsuffixed-hex-widens-signed", "long", "return 0xffffffff;", "", -1),
    ("boolean-identity-cast", "boolean", "return (boolean)(true & false);", "", 0),
    ("overload-return-context-ignored", "long", "return pick(1);", "static int pick(int x){return -1;}static long pick(long x){return 42L;}", -1),
    ("boolean-da", "boolean", "boolean x;if(false)return x;x=true;return x;", "", 1),
]


@pytest.mark.parametrize("name,result,body,helpers,expected", CASES, ids=[case[0] for case in CASES])
def test_scalar_source_uses_owned_ir_and_object(name, result, body, helpers, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    original = source(result, body, helpers)
    compilation = compile_source(original, descriptor="()" + DESCRIPTOR[result])
    assert compilation.emit_object().startswith(b"\x7fELF")
    assert "nsw" not in str(compilation.module) and "nuw" not in str(compilation.module)
    assert str(compilation.module) == str(compile_source(original, descriptor="()" + DESCRIPTOR[result]).module)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,result,body,helpers,expected", CASES, ids=[case[0] for case in CASES])
def test_original_scalar_source_has_exact_native_result(name, result, body, helpers, expected, tmp_path, monkeypatch):
    original = source(result, body, helpers)
    (tmp_path / "Scalar.java").write_text(original)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compilation = compile_source(original, descriptor="()" + DESCRIPTOR[result])
    assert observe_scalar(compilation, tmp_path, monkeypatch) == expected
    assert (tmp_path / "Scalar.java").read_text() == original


@pytest.mark.parametrize("result,body,helpers,message", [
    ("int", "return 1L;", "", "incompatible assignment"),
    ("boolean", "return 1;", "", "boolean value required"),
    ("long", "return true;", "", "integer value required"),
    ("int", "boolean b=1;return 0;", "", "boolean value required"),
    ("int", "long x=false;return 0;", "", "integer value required"),
    ("int", "int x=1L;return x;", "", "incompatible assignment"),
    ("int", "return f(1L);", "static int f(int x){return x;}", "unresolved same-class"),
    ("int", "return f(1,1);", "static int f(int a,long b){return 1;}static int f(long a,int b){return 2;}", "ambiguous primitive"),
    ("int", "return f(1);", "static int f(boolean x){return 1;}", "unresolved same-class"),
    ("int", "return 1<<true;", "", "integer value required"),
    ("int", "return true & 1;", "", "integer value required"),
    ("int", "return true+1L;", "", "integer value required"),
    ("boolean", "return (boolean)1L;", "", "boolean casts require"),
    ("int", "return (int)true;", "", "integer value required"),
    ("boolean", "boolean b=true;b++;return b;", "", "increment/decrement requires"),
    ("boolean", "boolean b=true;b+=1;return b;", "", "compound arithmetic"),
    ("int", "return 42;", "static int f(long x){return 1;}static long f(long x){return 2L;}", "duplicate method signature"),
    ("long", "return 9223372036854775808L;", "", "literal exceeds long range"),
    ("long", "return -(9223372036854775808L);", "", "literal exceeds long range"),
    ("long", "return 0xffffffffffffffff;", "", "literal exceeds int range"),
    ("long", "return 0x10000000000000000L;", "", "literal exceeds long range"),
    ("long", "return 1_L;", "", "invalid long literal"),
    ("int", "return 42;", "static void wait(long timeout){}", "inherited java.lang.Object instance"),
    ("int", "return 42;", "static void wait(long timeout,int nanos){}", "inherited java.lang.Object instance"),
])
def test_invalid_scalar_source_never_reaches_lowering(result, body, helpers, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid scalar source reached lowering")
    monkeypatch.setattr(SourceLowerer, "lower", forbidden)
    with pytest.raises(JavaError, match=message):
        compile_source(source(result, body, helpers), descriptor="()" + DESCRIPTOR[result])


@pytest.mark.parametrize("body", ["return 1L/0L;", "long x=1L;x%=2L;return x;"])
def test_long_division_does_not_replace_arithmetic_exception_with_trap(body):
    with pytest.raises(UnsupportedJava, match="ArithmeticException"):
        compile_source(source("long", body), descriptor="()J")


def test_cross_class_overload_identity_contains_actual_parameter_types():
    original = {"Main.java": "package p;class Main{static long run(){return Lib.f(true,1,2L);}}",
                "Lib.java": "package p;class Lib{static long f(boolean b,int x,long y){if(b)return x+y;return 0;}static int f(boolean b,long x,int y){return 7;}}"}
    compilation = compile_sources(original, entry_class="p.Main", descriptor="()J")
    assert ("p/Lib", "f", "(ZIJ)J") in compilation.emitted_class_methods
    assert ("p/Lib", "f", "(ZJI)I") in compilation.emitted_class_methods
    assert compilation.emit_object().startswith(b"\x7fELF")


MIXED_SOURCE = """public class Mixed {
 public static long mix(long a,int b,boolean c,long d,int e,boolean f,long g,int h){
  long result=a-b-d-e-g-h;
  if(c)result+=17;
  if(f)result-=9;
  return result;
 }
}"""
MIXED_ARGUMENTS = (("J", -9223372036854775000), ("I", -123456789), ("Z", 1),
                   ("J", 0x123456789abcdef0), ("I", -17), ("Z", 1),
                   ("J", -9876543210123), ("I", 127))
# Independent mathematical reduction at the declared signed-long width.
MIXED_BITS = (-9223372036854775000 + 123456789 - 0x123456789abcdef0 + 17 + 9876543210123 - 127 + 17 - 9) & ((1 << 64) - 1)
MIXED_EXPECTED = MIXED_BITS if MIXED_BITS < (1 << 63) else MIXED_BITS - (1 << 64)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_independent_assembly_passes_mixed_width_register_and_stack_arguments(tmp_path, monkeypatch):
    (tmp_path / "Mixed.java").write_text(MIXED_SOURCE)
    (tmp_path / "arguments.json").write_text(json.dumps(MIXED_ARGUMENTS))
    compilation = compile_source(MIXED_SOURCE, entry="mix", descriptor="(JIZJIZJI)J")
    assert observe_scalar(compilation, tmp_path, monkeypatch, MIXED_ARGUMENTS) == MIXED_EXPECTED


def test_long_boolean_cross_object_is_not_mac_device_execution(monkeypatch):
    from pcc.backend import macho_spec
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_source(MIXED_SOURCE, entry="mix", descriptor="(JIZJIZJI)J", target="arm64-apple-darwin")
    parsed = macho_spec.parse_object(compiled.emit_object())
    assert parsed.header["cputype"] == macho_spec.CPU_TYPE_ARM64
    assert "_" + compiled.entry_symbol in {symbol["name"] for symbol in parsed.symbols()}


ACCESS_OVERLOAD_SOURCE = {
    "Main.java": "package app;import lib.Helpers;class Main{static long run(){return Helpers.pick(42);}}",
    "Helpers.java": "package lib;public class Helpers{private static int pick(int x){return 7;}public static long pick(long x){return x;}}",
}


def test_inaccessible_narrow_overload_does_not_hide_accessible_widening_overload():
    compilation = compile_sources(ACCESS_OVERLOAD_SOURCE, entry_class="app.Main", descriptor="()J")
    text = str(compilation.module)
    assert "sext i32 42 to i64" in text
    assert compilation.emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_accessible_cross_class_overload_executes_its_original_source(tmp_path, monkeypatch):
    (tmp_path / "original-project.json").write_text(json.dumps(ACCESS_OVERLOAD_SOURCE, sort_keys=True))
    compilation = compile_sources(ACCESS_OVERLOAD_SOURCE, entry_class="app.Main", descriptor="()J")
    assert observe_scalar(compilation, tmp_path, monkeypatch) == 42


def test_long_parameter_limit_counts_two_slots_without_a_bytecode_intermediate():
    valid = ",".join("long x" + str(i) for i in range(127)) + ",int last"
    original = source("int", "return 42;", "static void many(" + valid + "){}")
    compilation = compile_source(original)
    assert ("Scalar", "many", "(" + "J" * 127 + "I)V") in compilation.emitted_class_methods
    invalid = ",".join("long x" + str(i) for i in range(128))
    with pytest.raises(JavaError, match="too many static method parameters"):
        compile_source(source("int", "return 42;", "static void many(" + invalid + "){}"))
