"""Native Java floating primitives, not C casts or host-float evaluation.

JLS21 3.10.2, 4.2.3-4, 5.1.2-3, 5.6, 15.4, 15.7, 15.17-18,
15.20-21, 15.25 and 15.26.2:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.10.2
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.2.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-5.html#jls-5.1.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.4

Both constant and nonconstant paths execute unchanged originals. Raw bits are
observed independently at the native ABI; NaN cases compare Java predicates,
not unspecified NaN payloads. The selected development JDK is reference-only.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_project import SourceProject
from pcc.frontends.java.source_syntax import SourceParser, tokenize
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_narrow_primitives import descriptor
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, record_process, tool_identity
from tests.java.test_source_scalars import NATIVE_REASON


WIDTHS = {"Z": 1, "B": 1, "S": 2, "C": 2, "I": 4, "J": 8, "F": 4, "D": 8}
IDENTITIES = "static double d(double x){return x;}static float f(float x){return x;}"


def original(body, result="int", parameters="", helpers=""):
    return "strictfp class Floating{" + IDENTITIES + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


CASES = [
    ("decimal-leading-dot", original("return .5f;", "float"), "F", (), 0x3f000000),
    ("decimal-trailing-dot", original("return 42.;", "double"), "D", (), 0x4045000000000000),
    ("decimal-underscores", original("return 4_2e+0_0D;", "double"), "D", (), 0x4045000000000000),
    ("hex-float", original("return 0x1.5p5f;", "float"), "F", (), 0x42280000),
    ("hex-leading-dot", original("return 0x.8p1;", "double"), "D", (), 0x3ff0000000000000),
    ("hex-integer-f-suffix-is-digit", original("return 0xff+0x1d;"), "I", (), 284),
    ("float-decimal-midpoint-even", original("return 1.000000059604644775390625f;", "float"), "F", (), 0x3f800000),
    ("float-decimal-avoid-double-rounding", original("return 1.0000000596046447753906250000000000000000000001f;", "float"), "F", (), 0x3f800001),
    ("double-decimal-midpoint-even", original("return 1.00000000000000011102230246251565404236316680908203125;", "double"), "D", (), 0x3ff0000000000000),
    ("double-decimal-above-midpoint", original("return 1.00000000000000011102230246251565404236316680908203125000000001;", "double"), "D", (), 0x3ff0000000000001),
    ("float-minimum-subnormal", original("return 0x1p-149f;", "float"), "F", (), 1),
    ("double-minimum-subnormal", original("return 0x1p-1074;", "double"), "D", (), 1),
    ("float-maximum-finite", original("return 0x1.fffffep127f;", "float"), "F", (), 0x7f7fffff),
    ("double-maximum-finite", original("return 0x1.fffffffffffffp1023;", "double"), "D", (), 0x7fefffffffffffff),
    ("zero-with-enormous-exponent", original("return 0e999999999999999999999999;", "double"), "D", (), 0),
    ("long-to-float-constant-round-once", original("return (float)4611686293305294849L;", "float"), "F", (), 0x5e800001),
    ("long-to-float-runtime-round-once", original("return x;", "float", "long x"), "F", (("J", 4611686293305294849),), 0x5e800001),
    ("float-to-double-exact", original("return f(0x1.000002p0f);", "double"), "D", (), 0x3ff0000020000000),
    ("double-to-float-round", original("return (float)d(0x1.0000010000001p0);", "float"), "F", (), 0x3f800001),
    ("int-to-float-runtime", original("return x;", "float", "int x"), "F", (("I", 16777217),), 0x4b800000),
    ("char-to-double-unsigned", original("return x;", "double", "char x"), "D", (("C", 65535),), 0x40efffe000000000),
    ("direct-float-negative-zero", original("return x;", "float", "float x"), "F", (("F", 0x80000000),), 0x80000000),
    ("direct-double-negative-zero", original("return x;", "double", "double x"), "D", (("D", 0x8000000000000000),), 0x8000000000000000),
    ("mixed-independent-register-banks", original("return a+b+c+e;", "double", "double a,int b,float c,long e"), "D", (("D", 0x4024000000000000), ("I", 20), ("F", 0x40e00000), ("J", 5)), 0x4045000000000000),
    ("ninth-floating-argument-on-stack", original("return i;", "double", "double a,double b,double c,double d,double e,double f,double g,double h,double i"), "D", (("D", 0),) * 8 + (("D", 0x4045000000000000),), 0x4045000000000000),
    ("nonconstant-division-positive-zero", original("return d(1.0)/d(0.0);", "double"), "D", (), 0x7ff0000000000000),
    ("nonconstant-division-negative-zero", original("return f(1f)/f(-0f);", "float"), "F", (), 0xff800000),
    ("double-gradual-underflow", original("return d(0x1p-1022)/d(2.0);", "double"), "D", (), 0x0008000000000000),
    ("float-gradual-underflow", original("return f(0x1p-126f)/f(2f);", "float"), "F", (), 0x00400000),
    ("runtime-double-overflow", original("return d(0x1p1023)*d(2.0);", "double"), "D", (), 0x7ff0000000000000),
    ("runtime-float-overflow", original("return f(0x1p127f)*f(2f);", "float"), "F", (), 0x7f800000),
    ("float-rounded-at-each-operation", original("float a=f(16777216f),b=f(1f);return (a+b)-a;", "float"), "F", (), 0),
    ("no-multiply-add-contraction", original("double a=d(0x1.0000000000001p0),b=d(0x1.fffffffffffffp-1);return a*b-1.0;", "double"), "D", (), 0),
    ("no-reassociation", original("double a=d(1e16),b=d(-1e16),c=d(1.0);return (a+b)+c;", "double"), "D", (), 0x3ff0000000000000),
    ("two-sum-error-term", original("double a=d(1e16),b=d(1.0),s=a+b,v=s-a;return (a-(s-v))+(b-v);", "double"), "D", (), 0x3ff0000000000000),
    ("compensated-two-increments", original("double sum=d(1e16),c=0.;for(int i=0;i<2;i++){double y=1.-c,t=sum+y;c=(t-sum)-y;sum=t;}return sum;", "double"), "D", (), 0x4341c37937e08001),
    ("floating-overload-is-more-specific", original("return pick(1);", helpers="static int pick(float x){return 42;}static int pick(double x){return 0;}"), "I", (), 42),
    ("long-overload-before-float", original("return pick(1);", helpers="static int pick(long x){return 42;}static int pick(float x){return 0;}"), "I", (), 42),
    ("float-compound-double-narrow", original("float x=f(1f);x+=d(0x1.0000001p-24);return x;", "float"), "F", (), 0x3f800001),
    ("compound-old-value-before-rhs", original("double x=d(20.);return x+=(x=d(22.));", "double"), "D", (), 0x4045000000000000),
    ("float-postfix-old-value", original("float x=f(41f),y=x++;return x+y;", "float"), "F", (), 0x42a60000),
    ("float-prefix-new-value", original("float x=f(41f);return ++x;", "float"), "F", (), 0x42280000),
    ("int-compound-double-saturates", original("int x=1;x+=d(1e100);return x;"), "I", (), 2147483647),
    ("byte-compound-double-two-stage", original("byte x=1;x+=d(1e100);return x;", "byte"), "B", (), -1),
    ("constant-field-stores-converted-float", original("return K;", "float", helpers="static final float K=16777217;"), "F", (), 0x4b800000),
    ("constant-local-stores-converted-float", original("final float x=16777217;return x==16777217?42:0;"), "I", (), 42),
    ("conditional-constant-promotion", original("return true?16777217:0f;", "float"), "F", (), 0x4b800000),
    ("conditional-runtime-promotion", original("return flag?16777217:0f;", "float", "boolean flag"), "F", (("Z", 1),), 0x4b800000),
    ("conditional-double-no-narrow", original("return flag?f(1.5f):d(2.5);", "double", "boolean flag"), "D", (("Z", 1),), 0x3ff8000000000000),
    ("nan-constant-reachability", original("while(0.0/0.0!=0.0/0.0){return 42;}"), "I", (), 42),
    ("float-declaration-diagnostic-original", "class Diagnostic{static int run(float a){return 42;}}", "I", (("F", 0),), 42),
    ("float-local-diagnostic-original", "class Diagnostic{static int run(){float value=1;return 42;}}", "I", (), 42),
]

# Paired originals exercise the actual same Java operation through constant
# evaluation and through nonconstant calls, including both signs of zero.
for name, expression, bits in [
    ("negate-zero", "-(ZERO)", 0x8000000000000000),
    ("negative-plus-negative-zero", "-ZERO + -ZERO", 0x8000000000000000),
    ("mixed-zero-addition", "ZERO + -ZERO", 0),
    ("negative-minus-positive-zero", "-ZERO - ZERO", 0x8000000000000000),
    ("negative-underflow", "-TINY/2.0", 0x8000000000000000),
    ("negative-zero-multiply", "-ZERO*2.0", 0x8000000000000000),
]:
    for runtime in (False, True):
        text = expression.replace("ZERO", "d(0.0)" if runtime else "0.0").replace("TINY", "d(0x1p-1074)" if runtime else "0x1p-1074")
        CASES.append((name + ("-runtime" if runtime else "-constant"), original("return " + text + ";", "double"), "D", (), bits))

for operator, expected in [("==", 0), ("!=", 1), ("<", 0), ("<=", 0), (">", 0), (">=", 0)]:
    for runtime in (False, True):
        nan = "(d(0.0)/d(0.0))" if runtime else "(0.0/0.0)"
        CASES.append(("nan-" + {"==": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}[operator]
                      + ("-runtime" if runtime else "-constant"), original("return " + nan + operator + "1.0;", "boolean"), "Z", (), expected))

for target, lane, positive_narrow, negative_narrow in [
    ("int", "I", 2147483647, -2147483648),
    ("long", "J", 9223372036854775807, -9223372036854775808),
    ("byte", "B", -1, 0), ("short", "S", -1, 0), ("char", "C", 65535, 0),
]:
    for name, expression, expected in [("nan", "0.0/0.0", 0), ("positive-infinity", "1.0/0.0", positive_narrow),
                                       ("negative-infinity", "-1.0/0.0", negative_narrow),
                                       ("finite-positive-overflow", "1e100", positive_narrow),
                                       ("finite-negative-overflow", "-1e100", negative_narrow),
                                       ("negative-zero", "-0.0", 0), ("fraction-truncates", "42.99", 42)]:
        for runtime in (False, True):
            value = "d(" + expression + ")" if runtime else "(" + expression + ")"
            CASES.append((target + "-cast-" + name + ("-runtime" if runtime else "-constant"),
                          original("return (" + target + ")" + value + ";", target), lane, (), expected))
    for name, expression, expected in [("nan", "0f/0f", 0), ("positive-infinity", "1f/0f", positive_narrow),
                                       ("negative-infinity", "-1f/0f", negative_narrow),
                                       ("finite-positive-overflow", "1e30f", positive_narrow),
                                       ("finite-negative-overflow", "-1e30f", negative_narrow),
                                       ("fraction-truncates", "42.99f", 42)]:
        for runtime in (False, True):
            value = "f(" + expression + ")" if runtime else "(" + expression + ")"
            CASES.append((target + "-from-float-" + name + ("-runtime" if runtime else "-constant"),
                          original("return (" + target + ")" + value + ";", target), lane, (), expected))

for target, lane, expression, expected in [
    ("byte", "B", "128.99", -128), ("short", "S", "65535.99", -1), ("char", "C", "65536.99", 0),
    ("int", "I", "2147483647.5", 2147483647), ("int", "I", "-2147483648.5", -2147483648),
    ("byte", "B", "2147483647.5", -1), ("char", "C", "-2147483648.5", 0),
]:
    CASES.append(("finite-two-stage-" + target + "-" + expression.replace(".", "_"),
                  original("return (" + target + ")d(" + expression + ");", target), lane, (), expected))

for name, expression in [("zero-times-infinity", "ZERO*INF"), ("infinity-minus-infinity", "INF-INF"),
                          ("infinity-divided-by-infinity", "INF/INF"), ("opposite-infinities", "INF + -INF")]:
    for runtime in (False, True):
        value = expression.replace("ZERO", "d(0.0)" if runtime else "0.0").replace("INF", "d(1.0/0.0)" if runtime else "(1.0/0.0)")
        CASES.append((name + ("-runtime" if runtime else "-constant"),
                      original("double n=" + value + ";return n!=n;", "boolean"), "Z", (), 1))

CASES.extend([
    ("finite-divided-by-infinity-negative-zero", original("return d(-42.0)/d(1.0/0.0);", "double"), "D", (), 0x8000000000000000),
    ("float-negative-underflow", original("return -f(0x1p-149f)/f(2f);", "float"), "F", (), 0x80000000),
    ("float-positive-overflow-narrowing", original("return (float)d(1e300);", "float"), "F", (), 0x7f800000),
    ("float-negative-zero-narrowing", original("return (float)d(-0.0);", "float"), "F", (), 0x80000000),
    ("nonconstant-compound-division-by-zero", original("float x=f(1f);x/=f(-0f);return x;", "float"), "F", (), 0xff800000),
    ("integral-compound-division-promotes-to-double", original("int x=1;x/=d(0.0);return x;"), "I", (), 2147483647),
])

for name, expression, expected in [
    ("below-positive-long-boundary", "0x1.fffffffffffffp62", 9223372036854774784),
    ("at-positive-long-boundary", "0x1p63", 9223372036854775807),
    ("above-positive-long-boundary", "0x1.0000000000001p63", 9223372036854775807),
    ("at-negative-long-boundary", "-0x1p63", -9223372036854775808),
    ("below-negative-long-boundary", "-0x1.0000000000001p63", -9223372036854775808),
]:
    CASES.append((name, original("return (long)d(" + expression + ");", "long"), "J", (), expected))


INVALID = [
    ("double-to-float-assignment", original("float x=1.0;return 0;"), "incompatible assignment"),
    ("float-to-long-assignment", original("long x=1f;return 0;"), "incompatible assignment"),
    ("float-constant-to-byte", original("byte x=1f;return x;"), "incompatible assignment"),
    ("boolean-to-float", original("float x=true;return 0;"), "integer value required"),
    ("float-to-boolean-cast", original("return (boolean)1f;", "boolean"), "boolean casts"),
    ("float-bitwise", original("return 1f&2f;", "float"), "integer value required"),
    ("float-shift", original("return 1f<<2;", "float"), "integer value required"),
    ("floating-shift-distance", original("return 1<<2f;"), "integer value required"),
    ("float-complement", original("return ~1f;", "float"), "integer value required"),
    ("float-compound-bitwise", original("float x=1f;x&=2;return 0;"), "integer variable"),
    ("float-compound-shift", original("float x=1f;x<<=2;return 0;"), "integer variable"),
    ("float-condition", original("if(1f)return 42;return 0;"), "requires boolean"),
    ("float-switch-selector", original("switch(1f){default:return 42;}"), "switch selector"),
    ("float-switch-label", original("switch(1){case 1f:return 42;default:return 0;}"), "incompatible assignment"),
    ("float-unassigned", original("float x;return (int)x;"), "not definitely assigned"),
    ("float-final-assignment", original("final float x=1f;x++;return 0;"), "final local"),
    ("invocation-cannot-narrow", original("return pick(1.0);", helpers="static int pick(float x){return 42;}"), "unresolved same-class"),
    ("ambiguous-floating-overload", original("return pick(1,1);", helpers="static int pick(float x,double y){return 1;}static int pick(double x,float y){return 2;}"), "ambiguous"),
]
for name, literal in [("float-overflow", "0x1p128f"), ("double-overflow", "0x1p1024"),
                      ("float-underflow", "0x1p-150f"), ("double-underflow", "0x1p-1075"),
                      ("decimal-float-overflow", "1e39f"), ("decimal-double-underflow", "1e-4000"),
                      ("huge-positive-exponent", "1e99999999999999"), ("huge-negative-exponent", "1e-99999999999999")]:
    INVALID.append((name, original("return " + literal + ";", "double"), "rounds to infinity or zero"))
for name, literal in [("missing-exponent", "1e"), ("missing-hex-exponent", "0x1.2"),
                      ("underscore-before-dot", "1_.0"), ("underscore-after-dot", "1._0"),
                      ("underscore-after-exponent-sign", "1e+_2"), ("suffix-underscore", "1_f")]:
    INVALID.append((name, original("return " + literal + ";", "double"), ""))
INVALID.append(("double-parameters-use-two-units", original("return 0;", parameters=",".join(
    "double p" + str(index) for index in range(128))), "too many static method parameters"))


@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_floating_originals_have_owned_ir_and_object(name, text, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert result.emit_object().startswith(b"\x7fELF")
    assert not re.search(r"\b(fast|reassoc|contract|nnan|ninf|nsz|afn)\b", str(result.module))


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_floating_originals_are_not_capability_gaps(name, text, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid numeric source reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_source(text)
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("body", ["return 5.0%2.0;", "double x=5.0;x%=2.0;return x;", "return d(5.0)%d(2.0);"])
def test_remainder_is_explicit_and_never_synthesized_from_rounded_division(body, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("floating remainder reached IR"))
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(original(body, "double"), descriptor="()D")
    assert caught.value.code == "java.unsupported.floating-remainder"


def test_fp_to_integral_ir_sanitizes_before_conversion_and_narrows_afterward():
    result = compile_source(original("return (byte)x;", "byte", "double x"), descriptor="(D)B")
    text = str(result.module)
    assert "fcmp uno double" in text and "fcmp ole double" in text and "fcmp oge double" in text
    assert text.index("select i1") < text.index("fptosi double") < text.index("trunc i32")
    assert " to i32" in text and "fptosi double" in text and " to i8" in text


def test_float_double_descriptors_overloads_and_model_remain_typed():
    source = original("final float x=16777217;return x==16777217?42:0;")
    project = SourceProject((SourceParser(source, "Floating.java").parse(),)).analyze()
    assert {method.descriptor for method in project.programs[0].methods} == {"(D)D", "(F)F", "()I"}
    model = analyze_class_sources({"Floating.java": source})
    assert model.programs[0].methods[-1].body.children[-1].children[0].constant == 42


def test_floating_lexemes_preserve_integer_tokens_and_separate_operators():
    tokens = ".5 1. 1.e+2 1f 1D 1e-2 0x1.fp+2f 0X.8P-1D 0xff 0x1d 0b101 077 1L 1+2 1e2-3"
    assert [token.text for token in tokenize(tokens, "Tokens.java")] == [
        ".5", "1.", "1.e+2", "1f", "1D", "1e-2", "0x1.fp+2f", "0X.8P-1D",
        "0xff", "0x1d", "0b101", "077", "1L", "1", "+", "2", "1e2", "-", "3", "<eof>"]


def test_shared_model_float_fields_do_not_claim_native_heap_support():
    text = "class Floating{float f=1f;double d=2.;void add(){f+=d;d++;}static int run(){return 42;}}"
    project = analyze_class_sources({"Floating.java": text})
    model = project.models["Floating"]
    assert [(slot.field.dtype, slot.size, slot.offset) for slot in model.fields] == [("float", 4, 0), ("double", 8, 8)]
    assert model.runtime_registered is False
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text)
    assert caught.value.code == "java.unsupported.instance-methods"


def test_floating_arrays_keep_their_separate_model_and_native_boundary():
    text = original("float[] values;return 42;")
    for invoke, code in [(lambda: analyze_class_sources({"Floating.java": text}), "java.unsupported.array-model-shape"),
                         (lambda: compile_source(text), "java.unsupported.array-storage")]:
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == code


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_floating_originals_execute_native_raw_lanes(name, text, lane, arguments, expected, tmp_path, monkeypatch):
    unit = tmp_path / "original.java"
    unit.write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        result = compile_source(text, descriptor=descriptor(lane, arguments))
    assert observe_scalar(result, tmp_path, monkeypatch, arguments) == expected
    assert unit.read_bytes() == text.encode("utf-8")


def observer_source(owner, lane, arguments):
    values = []
    for kind, value in arguments:
        if kind in {"F", "D"}:
            conversion = "Float.intBitsToFloat" if kind == "F" else "Double.longBitsToDouble"
            values.append(conversion + "(0x" + format(value, "x") + ("" if kind == "F" else "L") + ")")
        elif kind == "Z":
            values.append("true" if value else "false")
        else:
            cast = {"B": "(byte)", "S": "(short)", "C": "(char)"}.get(kind, "")
            values.append(cast + str(value) + ("L" if kind == "J" else ""))
    call = owner + ".run(" + ",".join(values) + ")"
    value = ("Float.floatToRawIntBits(" + call + ")" if lane == "F" else
             "Double.doubleToRawLongBits(" + call + ")" if lane == "D" else
             "(" + call + "?1L:0L)" if lane == "Z" else call)
    return ("public final class PccNarrowObserve{public static void main(String[] args){long value=" + value
            + ";for(int i=0;i<" + str(WIDTHS[lane]) + ";i++)System.out.write((int)(value >>> (8*i))&255);System.out.flush();}}")


JDK_REASON = None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else "select an existing JDK with PCC_JAVA_RAW_REFERENCE"


def compile_reference_batch(tmp_path, request, *, runtime, cases):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    root = Path(__file__).parent
    inputs = tuple(root / name for name in ("test_source_floating.py", "NarrowReference.java", "owned_execution.py",
                                            "test_source_narrow_primitives.py", "test_source_raw_reference.py"))
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    (tmp_path / "owner-before.json").write_text(json.dumps({**before, "version": version.group(1),
        "source_level": level, "target_level": level, "production_dependency": False,
        "platform_contract": "selected current JDK platform, not --release compatibility"}, indent=2))

    def finish_identity():
        after = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
        unchanged = all(digest(path) == value for path, value in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**after, "unchanged": unchanged,
            "original_observer_hashes": {str(path): value for path, value in retained.items()}}, indent=2))
        assert before == after and unchanged

    request.addfinalizer(finish_identity)
    assert record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")[0] == 0
    rows = []
    for name, text, *rest in cases:
        directory = tmp_path / name
        directory.mkdir()
        owner = re.search(r"class (\w+)", text).group(1)
        unit = directory / (owner + ".java")
        unit.write_bytes(text.encode("utf-8"))
        retained[unit] = digest(unit)
        if runtime:
            lane, arguments, _ = rest
            observer = directory / "PccNarrowObserve.java"
            observer.write_text(observer_source(owner, lane, arguments), encoding="utf-8")
            retained[observer] = digest(observer)
        rows.append(name + ("\trun\t" if runtime else "\treject\t") + unit.name)
    (tmp_path / "cases.tsv").write_text("\n".join(rows) + "\n")
    driver = tmp_path / "NarrowReference.java"
    driver.write_bytes(inputs[1].read_bytes())
    retained[driver] = digest(driver)
    driver_classes = tmp_path / "driver-classes"
    driver_classes.mkdir()
    compilation = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
        "-classpath", str(driver_classes), "-d", str(driver_classes), str(driver)), tmp_path, "driver-compile", 20)
    assert compilation[0] == 0, compilation
    batch = record_process((str(java), *JVM_LIMITS, "-cp", str(driver_classes), "NarrowReference",
        str(tmp_path), level), tmp_path, "batch-compile", 30)
    assert batch[0] == 0, batch
    return java


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
@pytest.mark.parametrize("shard", range(8))
def test_identical_floating_originals_match_raw_jdk_execution(shard, tmp_path, monkeypatch, request):
    # Exhaustive disjoint shards retain the existing per-test resource budget;
    # no case is skipped and no process/watchdog limit is widened.
    cases = CASES[shard::8]
    java = compile_reference_batch(tmp_path, request, runtime=True, cases=cases)
    for name, text, lane, arguments, expected in cases:
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
        expected_bytes = int(expected).to_bytes(WIDTHS[lane], "little", signed=lane in {"B", "S", "I", "J"})
        (directory / "comparison.json").write_text(json.dumps({"case": name, "comparison": "raw_process_streams",
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
def test_identical_invalid_floating_originals_are_rejected_by_jdk(tmp_path, request):
    compile_reference_batch(tmp_path, request, runtime=False, cases=INVALID)
    for name, *_ in INVALID:
        assert int((tmp_path / name / "compiler-status.txt").read_text()) != 0, name
