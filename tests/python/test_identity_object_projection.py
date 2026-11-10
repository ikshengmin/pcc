"""Language reference 6.10.3: identity consumes objects, not integer values."""

import re

import pytest

from pcc.frontends.python.pipeline import compile_python


@pytest.mark.parametrize("operator", ["is", "is not"])
def test_raw_integer_scaffold_preserves_container_operand_identity(tmp_path, operator):
    source = tmp_path / "identity.py"
    source.write_text('''from pcc.extern import c_int64, extern
unused = extern("identity_unused_external", (), c_int64)
def values():
    first = (1 << 120) + 19
    keep = [first]
    return keep[0] ''' + operator + ''' first
def main():
    print(values())
main()
''')
    output = tmp_path / "identity.ll"
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", ir_scaffold_mode="on")
    ir = output.read_text()
    assert re.search(r"\bcall\b[^\n]*@py_list_getitem\(", ir)
    # No conversion to a fixed-width lane may intervene between the owning
    # list lookup and the identity consumer. The native bigint-publication
    # regression executes this original shape under every collector.
    assert not re.search(r"\bcall\b[^\n]*@py_int_to_i64_lane\(", ir)
    # Object identity stays in the pointer lane so machine-lifetime cleanup
    # can retire dead operand registers/spills before a conservative GC stop.
    assert re.search(r"%is\.[^\n]* = icmp eq ptr [^\n]+", ir)
    assert not re.search(r"%is\.[lr][^\n]* = ptrtoint", ir)


def test_function_root_groups_identity_does_not_leave_integer_address_copies(tmp_path):
    # The unchanged native all-GC fixture exercises closure(first, second)
    # is second, drops both owners, then requires both finalizer events.
    # Before direct pointer comparison, the address-integer copies could
    # retain second after its pointer owners had been cleared.
    from tests.python.test_function_root_groups import PROGRAM

    source = tmp_path / "function_groups.py"
    source.write_text(PROGRAM)
    output = tmp_path / "function_groups.ll"
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", ir_scaffold_mode="on")
    ir = output.read_text()
    assert re.search(r"icmp eq ptr %closure_ret[^\n]+, %second[^\n]+", ir)
    assert not re.search(r"%is\.[lr][^\n]* = ptrtoint", ir)
