"""Go fixed-width integer identities, exact constants and conversion contracts."""

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go
from pcc.frontends.go.sema import Analyzer
from pcc.frontends.go.syntax import GoParser
from pcc.frontends.go.types import INTEGER_TYPES, integer_bounds


def program(body, helpers=""):
    return "package main; " + (helpers + "; " if helpers else "") + "func main(){" + body + "}"


@pytest.mark.parametrize("dtype", INTEGER_TYPES)
def test_each_integer_type_has_exact_constant_bounds_and_public_ir_width(dtype):
    minimum, maximum = integer_bounds(dtype)
    bits, signed = INTEGER_TYPES[dtype]
    module = compile_go(program(f"var lo {dtype}={minimum};var hi {dtype}={maximum};_ = lo;_ = hi"))
    assert f"alloca i{bits}" in str(module)
    for invalid in (minimum - 1, maximum + 1):
        with pytest.raises(GoDiagnostic, match="overflows " + dtype):
            compile_go(program(f"_ = {dtype}({invalid})"))


@pytest.mark.parametrize("dtype", INTEGER_TYPES)
def test_constant_arithmetic_does_not_wrap_even_when_runtime_arithmetic_does(dtype):
    minimum, maximum = integer_bounds(dtype)
    with pytest.raises(GoDiagnostic, match="overflows " + dtype):
        compile_go(program(f"const x {dtype}={maximum}; _ = x + 1"))
    with pytest.raises(GoDiagnostic, match="overflows " + dtype):
        compile_go(program(f"const x {dtype}={minimum}; _ = x - 1"))


@pytest.mark.parametrize("dtype", [name for name, (_, signed) in INTEGER_TYPES.items() if not signed])
def test_unsigned_complement_uses_the_semantic_width(dtype):
    ast = GoParser().parse(program(f"const mask = ^{dtype}(0); _ = mask"))
    Analyzer(ast).analyze()
    assert ast.functions[0].body[0].exprs[0].constant == integer_bounds(dtype)[1]


@pytest.mark.parametrize("source,target,opcode", [
    ("int8", "uint64", "sext i8"), ("uint8", "int64", "zext i8"),
    ("uint32", "int64", "zext i32"), ("int32", "uint64", "sext i32"),
    ("uint64", "int8", "trunc i64"), ("int64", "uint16", "trunc i64"),
])
def test_runtime_conversion_extends_from_source_signedness_then_truncates(source, target, opcode):
    text = str(compile_go(program("", f"func f(x {source}) {target} {{return {target}(x)}}")))
    assert opcode in text


@pytest.mark.parametrize("left,right", [("int", "int64"), ("uint", "uint64"), ("uint", "uintptr"),
                                        ("uint8", "int8"), ("int32", "int64")])
def test_same_representation_does_not_erase_named_type_identity(left, right):
    with pytest.raises(GoDiagnostic, match="cannot use|mismatched operand"):
        compile_go(program(f"var a {left};var b {right};a=b;_ = a"))


def test_byte_and_rune_are_aliases_not_new_named_types():
    compile_go(program("var a byte; var b uint8; a=b; _=a; var r rune; var s int32; r=s; _=r"))


@pytest.mark.parametrize("body,helpers,message", [
    ("_ = f(1)", "func f(n uint) uint8{return 256 >> n}", "overflows uint8"),
    ("_ = f(1)", "func f(n uint) uint8{return uint8(-1 << n)}", "overflows uint8"),
    ("_ = f(1)", "func f(n uint) bool{return (18446744073709551615 >> n)>0}", "overflows int"),
    ("_ = 1 << 18446744073709551616", "", "shift count overflows uint64"),
    ("_ = f(1,2)", "func f(x uint64,n int) uint64{return x<<n}", "negative-count panic support"),
    ("_ = f(1,2)", "func f(x,y uint64) uint64{return x/y}", "division-by-zero panic support"),
])
def test_integer_unsupported_or_invalid_boundaries_stay_diagnostic(body, helpers, message):
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(program(body, helpers))


def test_dynamic_unsigned_shift_is_clamped_before_the_ir_shift():
    text = str(compile_go(program("", "func f(x uint8,n uint64) uint8{return x<<n}")))
    assert "icmp ult i64" in text
    assert text.index("select i1") < text.index("shl i8")
    assert "trunc i64" in text


def test_target_word_model_is_64_bit_and_unsupported_32_bit_targets_fail_closed():
    for target in ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu", "arm64-apple-darwin"):
        text = str(compile_go(program("var a int;var b uint;var p uintptr;_=a;_=b;_=p"), target_triple=target))
        assert text.count("alloca i64") == 3
    for target in ("i386-unknown-linux-gnu", "armv7-unknown-linux-gnueabihf", "wasm32-unknown-unknown"):
        with pytest.raises(GoDiagnostic, match="64-bit target"):
            compile_go(program(""), target_triple=target)


def test_giant_decimal_literal_gets_frontend_precision_diagnostic_not_host_int_error():
    with pytest.raises(GoDiagnostic, match="precision limit"):
        compile_go(program("_ = " + "9" * 5000))


@pytest.mark.parametrize("target", [
    "x86_64-unknown-linux-gnux32", "aarch64-unknown-linux-gnu_ilp32",
    "aarch64-unknown-linux-ilp32", "arm64-apple-darwin-ilp32",
    "x86_64", "x86_64-unknown-imaginary", "x86_64--linux-gnu",
    "x86_64-unknown-linux-gnu-extra", "x86_64-unknown-linux-unknownabi",
    'x86_64-unknown-linux-gnu\"\nsource_filename = \"injected',
    "x86_64-pc-windows-msvc", "aarch64-unknown-freebsd", None,
])
def test_unsupported_or_malformed_target_never_reaches_ir(target, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    def forbidden(*args, **kwargs):
        raise AssertionError("unadmitted Go target reached IR")
    monkeypatch.setattr(Lowerer, "lower", forbidden)
    with pytest.raises(GoDiagnostic, match="64-bit target"):
        compile_go(program(""), target_triple=target)


@pytest.mark.parametrize("target", [
    "x86_64-linux", "x86_64-linux-gnu", "x86_64-unknown-linux-musl",
    "amd64-unknown-linux-gnu", "aarch64-linux-gnu", "arm64-unknown-linux-musl",
    "arm64-apple-darwin25.0", "aarch64-apple-macosx15.0",
])
def test_explicit_64_bit_target_aliases_keep_original_target_identity(target):
    module = compile_go(program("var p uintptr;_ = p"), target_triple=target)
    assert module.triple == target
    assert "alloca i64" in str(module)


@pytest.mark.parametrize("dtype,operation,extension,opcode", [
    ("int8", "*", "sext", "mul"), ("uint8", "*", "zext", "mul"),
    ("int16", "/", "sext", "sdiv"), ("uint16", "%", "zext", "urem"),
])
def test_narrow_arithmetic_legalizes_without_changing_its_go_result_type(dtype, operation, extension, opcode):
    width = INTEGER_TYPES[dtype][0]
    text = str(compile_go(program("", f"func f(x {dtype}) {dtype} {{return x {operation} 3}}")))
    assert f"{extension} i{width}" in text
    assert f"{opcode} i32" in text
    assert "trunc i32" in text


RAW_CASES = [
    ("inc8", (127,), 1, -128), ("inc16", (32767,), 2, -32768),
    ("inc32", (2147483647,), 4, -2147483648),
    ("inc64", (9223372036854775807,), 8, -9223372036854775808),
    ("incU8", (255,), 1, 0), ("incU16", (65535,), 2, 0),
    ("incU32", (4294967295,), 4, 0), ("incU64", (18446744073709551615,), 8, 0),
    ("divU64", (18446744073709551615,), 8, 6148914691236517205),
    ("remU64", (18446744073709551614,), 8, 2),
    ("divI8", (-128,), 1, -128), ("remI16", (-32768,), 2, 0),
    ("signedWide", (-1,), 8, 18446744073709551615),
    ("unsignedWide", (255,), 8, 255), ("narrow", (511,), 1, -1),
    ("narrowU", (-1,), 2, 65535), ("invert", (0,), 2, 65535),
    ("left8", (255, 7), 1, 128), ("left8", (255, 8), 1, 0),
    ("left8", (255, 18446744073709551615), 1, 0),
    ("right8", (-128, 18446744073709551615), 1, -1),
    ("rightU8", (255, 7), 1, 1), ("rightU8", (255, 8), 1, 0),
    ("left64", (1, 63), 8, 9223372036854775808), ("left64", (1, 64), 8, 0),
    ("context", (7,), 1, 128), ("context", (8,), 1, 0),
    ("convertedContext", (8,), 1, 0), ("mul8", (-128,), 1, 0),
    ("mulU8", (200,), 1, 88), ("quotient8", (-29,), 1, -4),
    ("remainder8", (-29,), 1, -5), ("quotientU8", (255,), 1, 85),
    ("remainderU16", (65535,), 2, 1),
]


@pytest.mark.integration
@pytest.mark.parametrize("name,arguments,width,expected", RAW_CASES)
def test_original_integer_helpers_return_exact_raw_abi_bytes(tmp_path, monkeypatch, name, arguments, width, expected):
    import hashlib
    import json
    import platform
    import sys
    from tests.go.owned_execution import Execution, compile_owned, run, write_artifact
    from tests.go.test_execution import forbid_external_compiler, units

    assert sys.platform == "linux" and platform.machine() == "x86_64"
    original = units(("integers.go",))
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(original, observe_entry=(name, arguments))
    binary = tmp_path / "raw-integer"
    write_artifact(artifact, binary)
    raw = (expected & ((1 << (8 * width)) - 1)).to_bytes(width, "little")
    actual = run(binary)
    (tmp_path / "raw-observation.json").write_text(json.dumps({
        "owner": artifact.owner, "function": name, "arguments": arguments,
        "width_bytes": width, "expected_hex": raw.hex(),
        "returncode": actual.returncode, "stdout_hex": actual.stdout.hex(),
        "stderr_hex": actual.stderr.hex(),
        "source_sha256": hashlib.sha256(original[0][1].encode("utf-8")).hexdigest(),
        "executable_sha256": hashlib.sha256(artifact.executable).hexdigest(),
    }, indent=2) + "\n")
    assert actual == Execution(0, raw, b"")
