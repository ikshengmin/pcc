"""Host IR checks for generic iterator owners outside a Python function.

No native executable or runtime is built or run by these tests.
"""

import re
import textwrap

import pytest

from pcc.backend.self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect as arm64_indirect,
)
from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_x86_64_linux import (
    _aggregate_returned_indirect as x86_indirect,
)
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


_TARGETS = [
    pytest.param("x86_64-unknown-linux-gnu", x86_indirect, id="x86_64"),
    pytest.param("aarch64-unknown-linux-gnu", arm64_indirect, id="aarch64"),
]


def _check_iterator_owner(source, scope, triple, indirect, prefix):
    if scope == "function":
        source = "def probe():\n" + textwrap.indent(source, "    ")
    module = infer_module(parse_and_lift(source, "iterator_roots.py", "iterator_roots"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._skip_program_main = scope == "module-init"
    codegen._target_triple = triple
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(module))
    owners = [
        (function, slot)
        for function, entries in codegen._fn_gc_root_slot_registry.items()
        for name, slot in entries
        if name.startswith(prefix + ".owner")
    ]
    assert len(owners) == 1, owners
    function, slot = owners[0]
    match = re.search(
        r"^define [^\n]*@" + re.escape(function) + r"\([^\n]*\).*?^}",
        text, re.M | re.S,
    )
    assert match is not None
    body = match.group(0)
    # Resolve representation-only casts so the checks name the same physical
    # owner at registration, publication, reload and normal/error retirement.
    for result, source in re.findall(r"(%[^ ]+) = bitcast ptr ([^ ]+) to ptr", body):
        body = body.replace(result + ")", source + ")").replace(result + ",", source + ",")
    reference = str(slot)
    calls = [line for line in body.splitlines() if "call " in line and reference in line]
    assert sum("@pcc_gc_frame_enter(" in line for line in calls) == 1, calls
    assert any("@pcc_gc_store_root_take(" in line for line in calls), calls
    assert any("@pcc_gc_load_ptr(" in line for line in calls), calls
    assert sum("@pcc_gc_store_root(" in line and "ptr null" in line for line in calls) >= 2, calls
    assert any("@pcc_gc_frame_leave(" in line for line in calls), calls
    prepared = prepare_module_for_target(
        'target triple = "' + triple + '"\n' + text,
        aggregate_returned_indirect=indirect,
    )
    assert prepared.triple == triple
    if triple.startswith("x86_64"):
        # The existing analyzer supports x86-64 Linux, not AArch64 Linux.
        # Both targets above use the verifier and target slot preparation.
        plans = build_stack_map_plans(
            prepared.functions, prepared.globals_, target="x86_64-linux",
        )
        assert len(plans) == len(prepared.functions)


@pytest.mark.parametrize("triple, indirect", _TARGETS)
@pytest.mark.parametrize("scope", ["module-init", "program-main", "function"])
def test_comprehension_iterator_has_an_authoritative_root(scope, triple, indirect):
    _check_iterator_owner(
        'result = [item.upper() for item in iter(["first", "second"])]\n',
        scope, triple, indirect, "comp.iter",
    )


@pytest.mark.parametrize("triple, indirect", _TARGETS)
@pytest.mark.parametrize("scope", ["module-init", "program-main", "function"])
def test_for_iterator_has_an_authoritative_root(scope, triple, indirect):
    _check_iterator_owner(
        'for item in iter(["first", "second"]):\n'
        '    print(item)\n'
        '    if item == "second":\n'
        '        break\n',
        scope, triple, indirect, "for.obj.iter",
    )


@pytest.mark.parametrize("triple, indirect", _TARGETS)
def test_native_lambda_comprehension_iterator_is_rooted(triple, indirect):
    _check_iterator_owner(
        'operation = lambda: [item.upper() for item in iter(["first", "second"])]\n'
        'result = operation()\n',
        "native-lambda", triple, indirect, "comp.iter",
    )
