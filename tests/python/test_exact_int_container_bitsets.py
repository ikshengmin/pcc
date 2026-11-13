"""Python reference 6.3.2/6.7/6.10: subscription, bitwise ops and comparisons.

Container int annotations do not bound their values, even in the scaffold
projection used to build pcc1. Keep the original Windows find gate as the
integration qualification of the compiler that exposed this shape.
"""
from __future__ import annotations

import re

import pytest


PROGRAM = '''from pcc.extern import c_abi_typed_export

def overlap(left: dict[int, int], right: dict[str, int]) -> bool:
    return not left[0] & right["r"]

def sequence_checks(items: list[int], pair: tuple[int, int]) -> bool:
    copied = items[0]
    return (copied == pair[0] and items[0] & pair[1] == 0
            and ~items[0] == -pair[0] - 1)

def main() -> None:
    for bit in (0, 62, 63, 64, 127, 4095):
        high = 1 << bit
        left: dict[int, int] = {0: high}
        same: dict[str, int] = {"r": high}
        other: dict[str, int] = {"r": 1 << (bit + 1)}
        assert not overlap(left, same)
        assert overlap(left, other)
        assert sequence_checks([high], (high, 1 << (bit + 1)))
        left[0] = -high
        assert not overlap(left, same)
    print("CONTAINER_BITSETS_OK")

main()
'''


def test_container_bitsets_scaffold_ir_keeps_object_operands(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "container_bitsets.py"
    output = tmp_path / "container_bitsets.ll"
    source.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(source), str(output), emit_llvm_only=True)
    text = output.read_text(encoding="utf-8")
    match = re.search(
        r"define[^\n]*@[^\n(]*overlap\([^\n]*\).*?\{(.*?)\n\}",
        text, re.S,
    )
    assert match, text
    body = match.group(1)
    assert "@py_int_and" in body, body
    assert "@py_int_to_i64" not in body, body
    assert "@py_obj_to_i64" not in body, body


@pytest.fixture
def bitsets_owned_runtime(request, monkeypatch):
    from tests.python.owned_regression_support import explicit_owned_runtime

    return explicit_owned_runtime.__wrapped__(request, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_container_bitsets_owned_native_five_gc(
    python_program_compiler, request, bitsets_owned_runtime, tmp_path, capfd,
):
    from tests.python.owned_regression_support import assert_owned_program

    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM, "CONTAINER_BITSETS_OK\n", tmp_path,
        python_program_compiler, mode, bitsets_owned_runtime, capfd,
    )
