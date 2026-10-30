"""Host IR proof for unpacked locals in scopes without a Python ``def``.

These checks do not build or execute a native compiler or runtime.
"""

import pytest

from pcc.backend.self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect as arm64_indirect,
)
from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_x86_64_linux import _aggregate_returned_indirect as x86_indirect
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


@pytest.mark.parametrize("secondary_module", [True, False], ids=["module-init", "program-main"])
@pytest.mark.parametrize("triple, target, indirect", [
    ("x86_64-unknown-linux-gnu", "x86_64-linux", x86_indirect),
    ("aarch64-unknown-linux-gnu", "aarch64-linux", arm64_indirect),
], ids=["x86_64", "aarch64"])
def test_nested_dict_comprehension_registers_unpacked_method_receiver(
    secondary_module, triple, target, indirect,
):
    source = '''
pool = {"rbx": {8: "rbx", 4: "ebx"}, "r12": {8: "r12"}}
bases = {alias: base for base, aliases in pool.items()
         for alias in aliases.values()}
'''
    module = infer_module(parse_and_lift(source, "unpack_roots.py", "unpack_roots"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._skip_program_main = secondary_module
    codegen._target_triple = triple
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(module))
    function = "_pcc_py_module_top_unpack_roots" if secondary_module else "main"
    registry = codegen._fn_gc_root_slot_registry[function]
    assert {name for name, _slot in registry} >= {"base", "aliases", "alias"}
    assert len({id(slot) for _name, slot in registry}) == len(registry)
    assert "@pcc_gc_root_move(" in text
    assert "@pcc_gc_root_copy_lease(" in text
    prepared = prepare_module_for_target(
        'target triple = "' + triple + '"\n' + text,
        aggregate_returned_indirect=indirect,
    )
    assert prepared.triple == triple
    # The optional precise-map analyzer supports x86-64 Linux, but not
    # AArch64 Linux. Check the real lifetimes and cleanup joins on its
    # supported target; both targets run the verifier and slot preparation.
    if target == "x86_64-linux":
        plans = build_stack_map_plans(
            prepared.functions, prepared.globals_, target=target,
        )
        assert len(plans) == len(prepared.functions)
