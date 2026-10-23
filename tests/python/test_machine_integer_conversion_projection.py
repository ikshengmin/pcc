"""Already lowered integer arguments keep their machine representation."""

import re

import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import (
    _function,
    _pipeline_ir,
    _run_native_all_gc,
)


FUNCTIONS = '''from pcc import i64, u64
from pcc.extern import c_abi_export, extern, c_int64
read_backend = extern("pcc_gc_backend", (), c_int64)
def accept_signed(value: i64) -> i64:
    return value
def accept_unsigned(value: u64) -> u64:
    return value
def from_signed(value: i64) -> i64:
    return accept_signed(i64(value))
def from_unsigned(value: u64) -> u64:
    return accept_unsigned(u64(value))
def from_extern() -> i64:
    return accept_signed(i64(read_backend()))
@c_abi_export("conversion_from_abi")
def from_abi(value: int) -> i64:
    return accept_signed(i64(value))
def from_object(value) -> i64:
    return accept_signed(i64(value))
def exact_identity(value: int) -> int:
    return int(value)
'''


def _checks():
    source = '''class IntValue:
    def __int__(self):
        return 37
class BadInt:
    def __int__(self):
        raise ValueError("conversion failed")
class WrongInt:
    def __int__(self):
        return "wrong"
def main():
'''
    for value in (-9223372036854775808, -4611686018427387905,
                  -4611686018427387904, -1, 0, 1, 4611686018427387903,
                  4611686018427387904, 9223372036854775807):
        source += f'    assert from_signed({value}) == {value}\n'
        source += f'    assert from_abi({value}) == {value}\n'
    for value in (0, 1, 4611686018427387904, 9223372036854775808,
                  18446744073709551615):
        source += f'    assert from_unsigned({value}) == {value}\n'
    source += '''    assert from_extern() >= 0
    assert from_extern() <= 4
    assert from_object(IntValue()) == 37
    assert from_object("123") == 123
    assert from_object(12.75) == 12
    assert from_object(True) == 1
    assert from_object(False) == 0
    try:
        from_object(BadInt())
    except ValueError as error:
        assert str(error) == "conversion failed"
    else:
        raise AssertionError("lost conversion exception")
    huge = 1267650600228229401496703205376
    assert exact_identity(huge) == huge
    assert exact_identity(-huge) == -huge
    for value in (huge, -huge):
        try:
            from_object(value)
        except OverflowError:
            pass
        else:
            raise AssertionError("lost checked object narrowing")
    print("MACHINE_CONVERSION_PROJECTION_OK")
main()
'''
    return source


@pytest.mark.parametrize(
    'prologue', ('', 'from pcc.unsafe import null\n', '__pcc_runtime_port__ = True\n'),
    ids=('ordinary', 'unsafe', 'runtime'),
)
def test_machine_conversion_arguments_do_not_rebox(tmp_path, monkeypatch, prologue):
    text = _pipeline_ir(tmp_path, monkeypatch, prologue + FUNCTIONS)
    for name in ('from_signed', 'from_unsigned', 'from_extern'):
        body = _function(text, name)
        assert not re.search(r'\bcall\b[^\n]*@py_int_', body), body
    body = re.search(r'(?ms)^define [^\n]*@conversion_from_abi\([^\n]*\n.*?^}', text).group(0)
    assert not re.search(r'\bcall\b[^\n]*@py_int_', body), body
    object_body = _function(text, 'from_object')
    assert '@py_obj_as_int_object_args(' in object_body
    assert '@py_int_to_i64_lane(' in object_body


@pytest.mark.integration
def test_machine_conversion_boundaries_native_all_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(
        tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
        FUNCTIONS + _checks(), 'MACHINE_CONVERSION_PROJECTION_OK',
    )


@pytest.mark.integration
@pytest.mark.xfail(
    strict=True,
    reason="cbb95dc0a baseline accepts non-integer __int__ results; separate runtime defect",
)
def test_machine_conversion_rejects_non_integer_protocol_result(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    program = FUNCTIONS + """class WrongInt:
    def __int__(self):
        return "wrong"
def main():
    try:
        from_object(WrongInt())
    except TypeError:
        print("INVALID_INT_RESULT_REJECTED")
    else:
        raise AssertionError("accepted non-integer __int__ result")
main()
"""
    _run_native_all_gc(
        tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
        program, "INVALID_INT_RESULT_REJECTED",
    )
