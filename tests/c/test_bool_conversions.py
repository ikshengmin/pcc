"""C _Bool conversions preserve truth values rather than signed i1 values."""

import pytest

from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
from pcc.frontends.c.parse.c_parser import CParser


def compile_ir(source):
    generator = CCodeGenerator()
    generator.set_target_text("x86_64-unknown-linux-gnu", "")
    generator.generate_code(CParser().parse(source))
    return str(generator.module)


@pytest.mark.parametrize("source_type", ("float", "double"))
@pytest.mark.parametrize("conversion", ("return value;", "return (_Bool)value;"))
def test_float_to_bool_uses_unordered_nonzero_comparison(source_type, conversion):
    text = compile_ir("_Bool truth(" + source_type + " value){" + conversion + "}")
    assert "fcmp une " + source_type in text
    assert "fptosi" not in text
    assert "fptoui" not in text


@pytest.mark.parametrize("target_type,width", (("char", 8), ("short", 16), ("int", 32), ("long long", 64)))
@pytest.mark.parametrize("source", ("value", "identity(value)", "(_Bool)2"))
def test_bool_to_signed_integer_is_zero_extended(target_type, width, source):
    text = compile_ir(
        "_Bool identity(_Bool x){return x;} "
        + target_type + " widen(_Bool value){return " + source + ";}"
    )
    assert "zext i1" in text and "to i" + str(width) in text
    assert "sext i1" not in text


@pytest.mark.parametrize("target_type", ("float", "double"))
@pytest.mark.parametrize("source", ("value", "identity(value)", "(_Bool)2"))
def test_bool_to_float_is_unsigned_conversion(target_type, source):
    text = compile_ir(
        "_Bool identity(_Bool x){return x;} "
        + target_type + " widen(_Bool value){return " + source + ";}"
    )
    assert "uitofp i1" in text and "to " + target_type in text
    assert "sitofp i1" not in text


def test_bool_integer_promotion_remains_signed_int():
    text = compile_ir("int less(_Bool value){return -1 < value;}")
    assert "zext i1" in text
    assert "icmp slt i32" in text
    assert "icmp ult" not in text


def test_explicit_float_to_signed_and_unsigned_keep_distinct_conversions():
    text = compile_ir(
        "int signed_value(double x){return (int)x;}"
        "unsigned int unsigned_value(double x){return (unsigned int)x;}"
    )
    assert "fptosi double" in text
    assert "fptoui double" in text
