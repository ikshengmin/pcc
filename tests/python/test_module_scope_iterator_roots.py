"""Host IR checks for generic iterator owners outside a Python function.

No native executable or runtime is built or run by these tests.
"""

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
    from tests.owned_ir_validation import verify_ir_text
    from tests.python.iterator_root_contract import assert_iterator_owner, parsed_root_contract

    parsed = verify_ir_text('target triple = "' + triple + '"\n' + text)
    body = next(fn for fn in parsed.functions if fn.name == function)
    roots = parsed_root_contract(parsed, body)
    assert_iterator_owner(roots, str(slot).lstrip("%"), private=prefix == "for.obj.iter")
    if scope in ("function", "native-lambda"):
        # Ordinary function/lambda activations have no process-lifetime
        # globals to retain, so all regular and lexical frames must retire.
        roots.assert_frame_exits(exact_abi=True)
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


@pytest.mark.parametrize("triple, indirect", _TARGETS)
def test_legacy_native_lambda_comprehension_retires_regular_frames(tmp_path, monkeypatch, triple, indirect):
    # The compiler's established provider uses the legacy adapter for native
    # valueclass captures. Exercise that actual contextual lowering, not a
    # fake adapter or a CPython fallback.
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from tests.owned_ir_validation import verify_ir_text
    from tests.python.iterator_root_contract import assert_iterator_owner, parsed_root_contract

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    source = tmp_path / "legacy_lambda.py"
    source.write_text('''from pcc.ir.compat import ir
def valueclass(cls):
    return cls
@valueclass
class Pair:
    first: int
    second: int
class Left:
    def __init__(self):
        pass
class UserFunctionLoweringMixin:
    def __init__(self, builder):
        self.builder = builder
    def callback(self, target, second, pair: Pair):
        return lambda: [self.builder.call(target, [pair.first, second], name="probe") for item in iter(["a", "bb"])]
class L1CodeGen(Left, UserFunctionLoweringMixin):
    def __init__(self, builder):
        self.builder = builder
''')
    modules, exports, derived = build_closed_world_context(
        [str(source)], ["pcc.frontends.python.codegen.user_function_lowering"],
    )
    typed = infer_module(modules[0], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._native_module_exports = exports
    codegen._target_triple = triple
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = 'target triple = "' + triple + '"\n' + str(codegen.generate(typed))
    parsed = verify_ir_text(text)
    adapters = [fn for fn in parsed.functions if "__native_lambda_" in fn.name]
    assert len(adapters) == 1
    roots = parsed_root_contract(parsed, adapters[0])
    assert any("pair.cap" in name for name in roots.allocas), "legacy valueclass capture required"
    owner = next(name for name in roots.allocas if name.startswith("comp.iter.root."))
    assert_iterator_owner(roots, owner, private=False)
    assert not any(ins.kind == "call" and ins.data[2].startswith("py_cpy_")
                   for _, _, ins in roots.rows)
    roots.assert_frame_exits(exact_abi=True)
    prepared = prepare_module_for_target(text, aggregate_returned_indirect=indirect)
    assert prepared.triple == triple
    if triple.startswith("x86_64"):
        assert len(build_stack_map_plans(
            prepared.functions, prepared.globals_, target="x86_64-linux",
        )) == len(prepared.functions)
