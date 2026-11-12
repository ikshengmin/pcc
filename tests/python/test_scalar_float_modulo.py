"""Scalar float modulo shares Python's divisor-sign zero contract.

The host checks inspect the real ordinary-source lowering and reach the owned
IR verifier/object emitter. Only the integration case executes the Python
program and compares its output with CPython on all five collectors.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.owned_ir_validation import verify_ir_text


TARGET = "x86_64-unknown-linux-gnu"
SCALAR_SOURCE = """def scalar_modulo(left: float, right: float) -> float:
    remainder = left % right
    return remainder
"""
POSITIVE_ZERO = "0x0000000000000000"
NEGATIVE_ZERO = "0x8000000000000000"


@pytest.fixture
def _explicit_text_route(monkeypatch):
    # These source-level checks inspect the text route rather than inheriting
    # a runner's optional direct-indexed capture/emission setting.
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")


def _emit_scalar(scaffold):
    module = infer_module(parse_and_lift(
        SCALAR_SOURCE, "scalar_modulo.py", "scalar_modulo",
    ))
    codegen = L1CodeGen(module, ir_scaffold_mode=scaffold)
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    codegen.module.triple = TARGET
    codegen._target_triple = TARGET
    text = str(codegen.generate(module))
    parsed = verify_ir_text(text)
    function = next(
        function for function in parsed.functions
        if function.name == "user_scalar_modulo_scalar_modulo"
    )
    get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    instructions = [instruction for block in function.blocks
                    for instruction in block.instructions]
    calls = [instruction for instruction in instructions if instruction.kind == "call"]
    call = _one(calls, lambda data: data[2] == "fmod",
                "typed local must reach the scalar fmod lowering")
    assert all(data[2] != "py_obj_mod" for data in (call.data for call in calls))
    assert call.data[1].is_fp and call.data[1].width == 64
    return text, function, instructions, call.data


def _one(instructions, predicate, message):
    matches = [instruction for instruction in instructions if predicate(instruction.data)]
    assert len(matches) == 1, message
    return matches[0]


def _reaches_return(function, instructions, value):
    # Follow the ordinary local and return-slot stores/loads, not fresh names.
    aliases = {value}
    while True:
        old = set(aliases)
        slots = {instruction.data[3] for instruction in instructions
                 if instruction.kind == "store" and instruction.data[1] in aliases}
        aliases.update(instruction.data[0] for instruction in instructions
                       if instruction.kind == "load" and instruction.data[3] in slots)
        if aliases == old:
            break
    return any(block.terminator.kind == "ret" and block.terminator.data[1] in aliases
               for block in function.blocks)


@pytest.mark.parametrize("scaffold", ("on", "off"))
def test_scalar_modulo_emits_divisor_signed_zero_without_replacing_nan(scaffold, _explicit_text_route):
    text, function, instructions, call = _emit_scalar(scaffold)
    remainder = call[0]
    right = call[4][1][1]
    comparisons = [instruction for instruction in instructions if instruction.kind == "fcmp"]
    selections = [instruction for instruction in instructions if instruction.kind == "select"]
    divisor_negative = _one(
        comparisons, lambda data: data[0] == "olt" and data[3:] == (right, POSITIVE_ZERO),
        "scalar modulo must inspect the divisor sign",
    )
    zero_sign = _one(
        selections,
        lambda data: data[2:] == (divisor_negative.data[1], NEGATIVE_ZERO, POSITIVE_ZERO),
        "scalar modulo must replace fmod's zero with divisor-signed zero",
    )
    remainder_is_zero = _one(
        comparisons, lambda data: data[0] == "oeq" and data[3:] == (remainder, POSITIVE_ZERO),
        "zero correction must use ordered equality so NaN remains NaN",
    )
    adjusted = _one(
        selections,
        lambda data: data[2:] == (remainder_is_zero.data[1], zero_sign.data[0], remainder),
        "only an actual zero remainder may be replaced",
    )
    corrected = _one(
        [instruction for instruction in instructions if instruction.kind == "fbinop"],
        lambda data: data[0] == "fadd" and data[3:] == (remainder, right),
        "nonzero remainder sign correction must stay unchanged",
    )
    remainder_nonzero = _one(
        comparisons, lambda data: data[0] == "one" and data[3:] == (remainder, POSITIVE_ZERO),
        "nonzero correction must exclude zero and NaN remainders",
    )
    remainder_negative = _one(
        comparisons, lambda data: data[0] == "olt" and data[3:] == (remainder, POSITIVE_ZERO),
        "nonzero correction must inspect the remainder sign",
    )
    binary_ops = [instruction for instruction in instructions if instruction.kind == "binop"]
    sign_difference = _one(
        binary_ops,
        lambda data: data[0] == "xor" and data[3:] == (remainder_negative.data[1], divisor_negative.data[1]),
        "nonzero correction must compare the remainder and divisor signs",
    )
    need_fix = _one(
        binary_ops,
        lambda data: data[0] == "and" and data[3:] == (remainder_nonzero.data[1], sign_difference.data[1]),
        "nonzero correction requires both a nonzero remainder and differing signs",
    )
    final = _one(
        selections, lambda data: data[2:] == (need_fix.data[1], corrected.data[1], adjusted.data[0]),
        "the returned correction must consume the zero adjustment",
    )
    assert _reaches_return(function, instructions, final.data[0])
    data = emit_owned_object(text, TARGET)
    parse_relocatable(data)


@pytest.mark.parametrize("scaffold", ("on", "off"))
def test_scalar_modulo_retains_zero_division_error_edge(scaffold, _explicit_text_route):
    _text, function, instructions, call = _emit_scalar(scaffold)
    right = call[4][1][1]
    divisor_is_zero = _one(
        [instruction for instruction in instructions if instruction.kind == "fcmp"],
        lambda data: data[0] == "oeq" and data[3:] == (right, POSITIVE_ZERO),
        "both signed zero divisors must retain the real error guard",
    )
    branch = _one(
        [block.terminator for block in function.blocks if block.terminator.kind == "br_cond"],
        lambda data: data[0] == divisor_is_zero.data[1],
        "the divisor check must still branch to an error block",
    )
    error = next(block for block in function.blocks if block.name == branch.data[1])
    calls = [instruction for instruction in error.instructions if instruction.kind == "call"]
    exception = _one(calls, lambda data: data[2] == "py_exc_new", "zero divisor must construct an exception")
    assert exception.data[4][0][1] == "9"
    raised = _one(calls, lambda data: data[2] == "py_raise", "zero divisor must raise the exception")
    assert raised.data[4][0][1] == exception.data[0]
    normal = function.block_map[branch.data[2]]
    assert any(instruction.kind == "call" and instruction.data == call
               for instruction in normal.instructions)
    assert error.terminator.kind == "br" and error.terminator.data[0] != normal.name


PROGRAM = SCALAR_SOURCE + """
def main():
    nan = float("nan")
    inf = float("inf")
    for left, right in [
        (4.0, -2.0), (-4.0, 2.0), (0.0, -2.0), (-0.0, 2.0),
        (4.0, 2.0), (-4.0, -2.0), (5.0, -2.0), (-5.0, 2.0),
        (5.0, 2.0), (-5.0, -2.0), (1.0, inf), (-1.0, inf),
        (1.0, -inf), (-1.0, -inf), (inf, 2.0), (-inf, 2.0),
        (nan, 2.0), (2.0, nan), (2.0, 0.0), (2.0, -0.0),
    ]:
        try:
            print(repr(scalar_modulo(left, right)))
        except ZeroDivisionError:
            print("ZeroDivisionError")


main()
"""


@pytest.mark.integration
@pytest.mark.parametrize("scaffold", ("on", "off"))
def test_scalar_modulo_matches_cpython_on_five_collectors(
    tmp_path, pcc_runtime_archive, python_program_compiler, scaffold,
):
    source = tmp_path / "scalar_float_modulo.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=30,
    )
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "scalar_float_modulo"
    python_program_compiler(
        str(source), str(binary), backend="self", ir_scaffold_mode=scaffold,
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert result.returncode == 0, (backend, result.stderr)
        assert result.stdout == expected.stdout, (backend, result.stdout, expected.stdout)
