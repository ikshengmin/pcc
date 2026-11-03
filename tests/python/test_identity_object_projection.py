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
    assert re.search(r"ptrtoint ptr [^\n]+ to i64", ir)
