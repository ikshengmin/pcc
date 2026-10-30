"""Fixture-free owned-IR checks for authoritative call operand producers.

No native object, runtime archive, subprocess, or emitted program is built or
executed here. These are compiler protocol checks, not moving-GC qualification.
"""
from __future__ import annotations

import re
from dataclasses import replace

import pytest

from pcc.frontends.python import type_infer
from pcc.frontends.python import parser as reference_parser
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.hoist_free_names import compute_free_names
from pcc.frontends.python.py_ast import BinOp, DynType, IntLit, IntType, Name, SetType
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.pipeline_ast_wire import (
    _py_ast_from_wire, _py_ast_node_replace, _py_ast_to_wire,
)
from pcc.ir.compat import ir
from tests.python.root_copy_ir import root_copy_calls


class UnborrowedAttributeCodegen(L1CodeGen):
    """The former attribute receiver policy, as a narrow negative control."""

    def _emit_slot_call_attribute(self, expr, label):
        previous = getattr(self, "_unborrowed_attribute_label", None)
        self._unborrowed_attribute_label = label + ".receiver"
        try:
            return super()._emit_slot_call_attribute(expr, label)
        finally:
            self._unborrowed_attribute_label = previous

    def _emit_slot_call_operand(self, expr, label, borrow_stable=False):
        if label == getattr(self, "_unborrowed_attribute_label", None):
            borrow_stable = False
        return super()._emit_slot_call_operand(expr, label, borrow_stable)


def _attribute_emit(source, *, before=False):
    """Ordinary Python through production lowering, not SlotProbeCodegen."""
    from tests.owned_ir_validation import verify_ir_text

    typed = type_infer.infer_module(parse_and_lift(source, "attribute_borrow.py", "attribute_borrow"))
    generator = (UnborrowedAttributeCodegen if before else L1CodeGen)(typed, ir_scaffold_mode="on")
    generator._strict_no_libpython = True
    generator._prefer_native_callable_values = True
    text = str(generator.generate(typed))
    verified = verify_ir_text(text)
    bodies = re.findall(r"^define [^\n]*@user_attribute_borrow_probe[^\n]*\{.*?^}",
                        text, re.M | re.S)
    assert bodies and all("strict.nolib.stub:" not in body and "@py_cpy_" not in body for body in bodies)
    return "\n".join(bodies), verified


def _assert_attribute_parameter_not_cleared(body, name="value"):
    for helper in ("pcc_gc_store_root", "py_cleanup_one_root_preserving_exception",
                   "py_cleanup_two_roots_preserving_exception"):
        assert not any(name + ".addr" in call for call in _calls(body, helper)), helper


def test_attribute_receiver_reuses_parameter_with_live_lookup_and_leases():
    source = "def probe(value):\n    return value.data + 1\n"
    before, _ = _attribute_emit(source, before=True)
    after, parsed = _attribute_emit(source)
    assert len(_calls(before, "py_obj_getattr")) == len(_calls(after, "py_obj_getattr")) == 1
    assert not _calls(before, "pcc_gc_borrowed_lease_acquire")
    assert len(_calls(after, "pcc_gc_borrowed_lease_acquire")) == 1
    assert "value.addr" in _calls(after, "pcc_gc_borrowed_lease_acquire")[0]
    copies = root_copy_calls(before, local=True, borrowed=True)
    assert len(copies) == 1 and copies[0].source.startswith("%value.addr")
    assert not root_copy_calls(after, local=True, borrowed=True)
    assert not _calls(after, "pcc_gc_root_copy_borrowed_lease")
    assert _calls(after, "pcc_gc_foreign_lease_release")
    _assert_attribute_parameter_not_cleared(after)
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from tests.python.root_slot_contract import RootSlotContract
    function = next(fn for fn in parsed.functions if fn.name == "user_attribute_borrow_probe")
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    RootSlotContract(blocks, parsed.globals_).assert_frame_exits()


@pytest.mark.parametrize("source", [
    "def probe(value, other):\n    value = other\n    return value.data + 1\n",
    "def probe(value, values):\n    for value in values:\n        result = value.data + 1\n    return 0\n",
    "def probe(value):\n    def change(other):\n        nonlocal value\n        value = other\n    return value.data + 1\n",
    "def probe(value):\n    yield value.data + 1\n",
    "def probe(value):\n    alias = value\n    return alias.data + 1\n",
    "shared = None\ndef probe():\n    return shared.data + 1\n",
])
def test_attribute_unstable_receivers_keep_the_original_owning_policy(source):
    before, _ = _attribute_emit(source, before=True)
    after, _ = _attribute_emit(source)
    assert _calls(after, "py_obj_getattr")
    assert not _calls(after, "pcc_gc_borrowed_lease_acquire")
    for helper in ("pcc_gc_root_copy_lease", "pcc_gc_root_copy_borrowed_lease",
                   "pcc_gc_frame_enter_lifo", "pcc_gc_frame_leave_lifo", "py_obj_getattr"):
        assert len(_calls(before, helper)) == len(_calls(after, helper)), helper
    for borrowed in (False, True):
        assert len(root_copy_calls(before, local=True, borrowed=borrowed)) == len(
            root_copy_calls(after, local=True, borrowed=borrowed)
        )


def test_attribute_exception_edges_release_lease_without_clearing_parameter():
    source = """def probe(value):
    try:
        return value.data + 1
    except AttributeError:
        return 42
"""
    before, _ = _attribute_emit(source, before=True)
    after, parsed = _attribute_emit(source)
    assert len(_calls(before, "py_obj_getattr")) == len(_calls(after, "py_obj_getattr")) == 1
    assert len(_calls(after, "pcc_gc_borrowed_lease_acquire")) == 1
    assert _calls(after, "py_cleanup_one_lease_preserving_exception")
    assert _calls(after, "pcc_gc_foreign_lease_release")
    # The builtin handler uses its existing inline/tag match, not necessarily
    # the generic py_exc_matches helper. Its selected cleanup remains intact.
    assert len(_calls(before, "py_clear_exception")) == len(_calls(after, "py_clear_exception"))
    _assert_attribute_parameter_not_cleared(after)
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from tests.python.root_slot_contract import RootSlotContract
    function = next(fn for fn in parsed.functions if fn.name == "user_attribute_borrow_probe")
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    RootSlotContract(blocks, parsed.globals_).assert_frame_exits()


def test_attribute_alias_keeps_its_owner_and_each_dynamic_lookup():
    source = """def probe(value):
    alias = value
    first = value.data + 1
    return first + alias.data
"""
    before, _ = _attribute_emit(source, before=True)
    after, _ = _attribute_emit(source)
    assert len(_calls(before, "py_obj_getattr")) == len(_calls(after, "py_obj_getattr")) == 2
    assert len(_calls(after, "pcc_gc_borrowed_lease_acquire")) == 1
    assert any(copy.source.startswith("%alias.addr") for copy in
               root_copy_calls(after, local=True, borrowed=False))
    _assert_attribute_parameter_not_cleared(after)


def test_attribute_descriptor_and_getattribute_remain_live():
    source = """events = []
class Descriptor:
    @property
    def data(self):
        events.append('property')
        return 4
class Override:
    def __getattribute__(self, name):
        if name == 'data':
            events.append('override')
            return 8
        raise AttributeError(name)
def probe(value):
    return value.data + 1
"""
    namespace = {}
    exec(source, namespace)
    assert namespace["probe"](namespace["Descriptor"]()) == 5
    assert namespace["probe"](namespace["Override"]()) == 9
    assert namespace["events"] == ["property", "override"]
    before, _ = _attribute_emit(source, before=True)
    after, _ = _attribute_emit(source)
    assert len(_calls(before, "py_obj_getattr")) == len(_calls(after, "py_obj_getattr")) == 1
    assert len(_calls(after, "pcc_gc_borrowed_lease_acquire")) == 1


def test_actual_packed_relocation_reader_elides_one_receiver_owner():
    """Real linker callee + real local exports; not a Stage1/native benchmark."""
    from pathlib import Path
    from pcc.frontends.python import py_ast
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.pipeline_closed_world import (
        _closed_world_boxed_int_functions, _closed_world_function_object_exports,
    )
    from tests.owned_ir_validation import verify_ir_text

    root = Path(__file__).resolve().parents[2]
    names = ("pcc.backend.native_object", "pcc.backend.macho_obj",
             "pcc.backend.macho_spec", "pcc.backend.self_backend_value_arena")
    paths = [str(root.joinpath(*name.split('.')).with_suffix('.py')) for name in names]
    parsed, exports, derived = build_closed_world_context(paths, names)
    external = {name: values for name, values in exports.items() if name != names[0]}
    typed = type_infer.infer_module(parsed[0], external_exports=external, derived_class_map=derived)
    functions = ("_packed_relocation_fields_at", "_relocation_fields_at", "_native_payload_reads_available")
    classes = ("PackedNativeObject", "PackedNativeSection", "PackedNativeSymbol", "NativeObjectError")
    selected = replace(typed, body=tuple(node for node in typed.body if (
        isinstance(node, (py_ast.Import, py_ast.ImportFrom, py_ast.Assign))
        or isinstance(node, py_ast.FuncDef) and node.name in functions
        or isinstance(node, py_ast.ClassDef) and node.name in classes
    )))
    bodies = []
    for implementation in (UnborrowedAttributeCodegen, L1CodeGen):
        generator = implementation(selected, ir_scaffold_mode="on")
        generator._strict_no_libpython = True
        generator._prefer_native_callable_values = True
        generator._module_source_path = paths[0]
        generator._skip_program_main = True
        generator._sibling_module_inits = names
        generator._native_module_exports = external
        generator._native_function_object_exports = _closed_world_function_object_exports(exports, names[0])
        generator._native_boxed_int_functions = _closed_world_boxed_int_functions(exports, names[0])
        text = str(generator._generate_initial_module())
        verify_ir_text(text)
        found = re.findall(r"^define [^\n]*@user_pcc_backend_native_object__packed_relocation_fields_at\([^\n]*\).*?^}", text, re.M | re.S)
        assert len(found) == 1 and "strict.nolib.stub:" not in found[0] and "@py_cpy_" not in found[0]
        bodies.append(found[0])
    before, after = bodies
    assert len(_calls(before, "py_obj_getattr")) == len(_calls(after, "py_obj_getattr")) == 2
    assert len(root_copy_calls(before, local=True, borrowed=True)) == 2
    assert len(root_copy_calls(after, local=True, borrowed=True)) == 1
    borrowed = _calls(after, "pcc_gc_borrowed_lease_acquire")
    assert len(borrowed) == 1 and "section.addr" in borrowed[0]
    assert not _calls(before, "pcc_gc_borrowed_lease_acquire")
    # Precise stack-map mode uses the mapped entry for the same owning slot.
    # Count both exact ABI spellings, not declarations or similarly named calls.
    for helpers in (
        ("pcc_gc_frame_enter_lifo", "pcc_gc_frame_enter_lifo_mapped"),
        ("pcc_gc_store_root",),
    ):
        before_count = sum(len(_calls(before, helper)) for helper in helpers)
        after_count = sum(len(_calls(after, helper)) for helper in helpers)
        assert after_count == before_count - 1, helpers
    _assert_attribute_parameter_not_cleared(after, "section")


class SlotProbeCodegen(L1CodeGen):
    def _emit_call(self, expr):
        if isinstance(expr.func, Name):
            name = expr.func.ident
            if name == "slot_operand_probe":
                slot = self._emit_slot_call_operand(expr.args[0], "probe.operand")
                return self._take_slot_call_root(slot)
            if name == "slot_args_probe":
                slot = self._emit_slot_call_args_tuple(expr.args, "probe.args")
                return self._take_slot_call_root(slot)
            if name == "slot_kwargs_probe":
                _args, kwargs = self._slot_call_split_operands(expr)
                slot = self._emit_slot_call_kwargs_object(kwargs, None, expr.span, "probe.kwargs")
                return self._take_slot_call_root(slot)
            if name == "slot_cpy_probe":
                self._cpy_env_flags[expr.args[0].ident] = True
                return self._emit_slot_call_operand(expr.args[0], "probe.cpy")
            if name == "slot_raw_erased_probe":
                argument = replace(expr.args[0], ty=DynType(name="dyn"))
                return self._emit_slot_call_operand(argument, "probe.raw.erased")
            if name == "slot_stale_probe":
                slot = self._new_slot_call_root("probe.stale")
                value = self.builder.call(self.runtime["py_list_new"], [ir.Constant(ir.IntType(64), 0)])
                self.builder.call(self.runtime["py_err_occurred"], [])
                self._publish_slot_call_owned(slot, value, label="stale test result")
        return super()._emit_call(expr)


def _emit(source):
    module = type_infer.infer_module(parse_and_lift(source, "slot_operand.py", "slot_operand"))
    codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    return str(codegen.generate(module))


def _probe_function(text):
    # Callable/cache metadata is a separate emitted owner. These checks
    # describe the user probe's operand contract, not whole-module counts.
    body = re.search(r"^define [^\n]*@user_slot_operand_probe\([^\n]*\).*?^}", text, re.M | re.S)
    assert body is not None
    return body.group(0)


def _calls(text, name):
    return re.findall(r"[^\n]*\bcall\b[^\n]*@" + re.escape(name) + r"\([^\n]*", text)


def _with_slot_origins(text, call):
    for result, source in re.findall(r"(%[^ ]+) = bitcast ptr ([^ ]+) to ptr", text):
        call = call.replace(result + ")", source + ")").replace(result + ",", source + ",")
    return call


def test_parameter_operand_copies_authoritative_borrowed_root():
    text = _emit("""def probe(value):
    return slot_operand_probe(value)
""")
    text = _probe_function(text)
    copies = root_copy_calls(text, local=True, borrowed=True)
    assert len(copies) == 1
    assert copies[0].source.startswith("%value.addr")
    assert "probe.operand" in copies[0].destination
    assert not _calls(text, "pcc_gc_root_copy_lease")
    assert not _calls(text, "pcc_gc_root_copy_borrowed_lease")
    # This probe returns the copied owner directly; it has no raw-argument
    # consumer and therefore no address transaction to acquire or retire.
    assert not _calls(text, "pcc_gc_foreign_lease_acquire")
    assert not _calls(text, "pcc_gc_foreign_lease_release")
    rebound = _emit("""def probe(value):
    value = [1]
    return slot_operand_probe(value)
""")
    rebound = _probe_function(rebound)
    assert any(copy.source.startswith(("%value.addr", "%value.for.obj.addr")) for copy in
               root_copy_calls(rebound, local=True, borrowed=False))
    assert not root_copy_calls(rebound, local=True, borrowed=True)
    assert not _calls(rebound, "pcc_gc_root_copy_borrowed_lease")


def test_global_and_class_operands_copy_existing_slots():
    text = _emit("""shared = [1]
class Box:
    pass
def probe():
    first = slot_operand_probe(shared)
    return slot_operand_probe(Box)
""")
    text = _probe_function(text)
    copies = _calls(text, "pcc_gc_root_copy_lease")
    assert len(copies) == 2
    assert any("shared" in _with_slot_origins(text, call) for call in copies)
    assert any("Box" in _with_slot_origins(text, call) for call in copies)


def test_generator_splat_publishes_call_before_cleanup():
    text = _emit("""def items():
    yield (1, 2)
def probe():
    return slot_args_probe(*items())
""")
    assert _calls(text, "py_list_extend")
    assert _calls(text, "py_tuple_from_list")
    # The precise returned owner is immediately stored. The old call-ret
    # retain/root/unregister route cannot appear between return and store.
    match = re.search(r"(%[^ ]+) = call ptr[^\n]*@user_[^\n(]*_items\([^\n]*\n([^\n]+)", text)
    assert match is not None, [line for line in text.splitlines() if "call" in line and "items" in line]
    assert "store ptr " + match.group(1) in match.group(2)
    assert "probe.args.item" in match.group(2)


def test_nested_literal_operands_use_slot_producers():
    text = _emit("""def probe():
    return slot_operand_probe(([1, 2], {'answer': 3}, {4, 5}))
""")
    assert _calls(text, "py_list_append")
    assert _calls(text, "py_dict_set")
    assert _calls(text, "py_set_add")
    constructors = _calls(_probe_function(text), "py_tuple_from_owned_roots")
    assert len(constructors) == 1 and "i64 3" in constructors[0]
    assert not _calls(_probe_function(text), "py_tuple_set_item")
    assert not _calls(_probe_function(text), "py_tuple_from_list")
    assert not root_copy_calls(text, borrowed=True)
    assert "call.slot.argument.lease" in text
    # An explicit sequence argument is evaluated completely before set()
    # hashes its first member. In particular, a raising a.__hash__ must not
    # prevent effect() from running. Keep the independent review's exact
    # list reduction, and cover the equivalent tuple constructor too.
    for sequence in ("[a, effect()]", "(a, effect())"):
        explicit = _emit("""def effect():
    print("effect")
    return 2
def probe(a):
    return slot_operand_probe(set(""" + sequence + "))\n")
        constructors = _calls(explicit, "py_set_from_iterable")
        assert len(constructors) == 1
        effect = re.search(r"[^\n]*\bcall\b[^\n]*@user_[^\n(]*_effect\([^\n]*", explicit)
        assert effect is not None
        assert effect.start() < explicit.index(constructors[0])
        assert not _calls(explicit, "py_set_add")
        ordinary = _emit("""def effect():
    print("effect")
    return 2
def probe(a):
    return set(""" + sequence + ")\n")
        constructors = _calls(ordinary, "py_set_from_iterable")
        assert len(constructors) == 1
        effect = re.search(r"[^\n]*\bcall\b[^\n]*@user_[^\n(]*_effect\([^\n]*", ordinary)
        assert effect is not None
        assert effect.start() < ordinary.index(constructors[0])
        assert not _calls(ordinary, "py_set_add")
    source = "literal = {1, 2}\nexplicit = set([1, 2])\n"
    lifted = (
        parse_and_lift(source, "set_literal.py", "set_literal"),
        reference_parser.parse(source, "set_literal.py"),
    )
    for module in lifted:
        typed = type_infer.infer_module(module)
        restored = _py_ast_from_wire(_py_ast_to_wire(typed))
        assert restored.body[0].value.is_set_literal
        assert isinstance(restored.body[0].value.ty, SetType)
        assert not restored.body[1].value.is_set_literal
        literal = restored.body[0].value
        assert replace(literal, ty=DynType(name="dyn")).is_set_literal
        assert _py_ast_node_replace(literal, {"ty": DynType(name="dyn")}).is_set_literal
        # Older wire inputs have no provenance and must remain ordinary calls.
        old_wire = _py_ast_to_wire(restored.body[1].value)
        del old_wire["fields"]["is_set_literal"]
        assert not _py_ast_from_wire(old_wire).is_set_literal
    shadowed = _emit("""def set(value):
    return 99
def probe():
    first = slot_operand_probe({1, 2})
    return ({3, 4}, first)
""")
    assert len(_calls(shadowed, "py_set_add")) == 4
    probe = re.search(r"^define [^\n]*@user_[^\n(]*_probe\([^\n]*\) \{(.*?)^\}", shadowed, re.M | re.S)
    assert probe is not None
    shadow_calls = re.findall(r"\bcall\b[^\n]*@user_[^\n(]*_set\(", probe.group(1))
    assert not shadow_calls
    unsafe_shadow = _emit("""from pcc.unsafe import null as set
def probe():
    return slot_operand_probe({1, 2})
""")
    assert len(_calls(unsafe_shadow, "py_set_add")) == 2
    for prefix in ("def set(value):\n    return 99\n", "from pcc.unsafe import null as set\n"):
        source = prefix + "def probe():\n    return {1, 2}\n"
        for module in (
            parse_and_lift(source, "set_shadow.py", "set_shadow"),
            reference_parser.parse(source, "set_shadow.py"),
        ):
            typed = type_infer.infer_module(module)
            literal = typed.body[-1].body[0].value
            assert literal.is_set_literal
            assert isinstance(literal.ty, SetType)
    for display, expected in (
        ("{value}", ("value",)),
        ("set([value])", ("set", "value")),
        ("len(value)", ("len", "value")),
    ):
        module = parse_and_lift(
            "def inner():\n    return " + display + "\n", "set_capture.py", "set_capture",
        )
        captured = compute_free_names(
            module.body[0], (), None, ("set", "len", "value"), (), (), {}, False, {},
        )
        assert captured == expected
    early_hash_error = _emit("""class First:
    def __hash__(self):
        raise ValueError("hash first")
def effect():
    print("effect")
    return 2
def probe():
    a = First()
    return slot_operand_probe(set([a, effect()]))
""")
    constructor = _calls(early_hash_error, "py_set_from_iterable")
    assert len(constructor) == 1
    effect = re.search(r"[^\n]*\bcall\b[^\n]*@user_[^\n(]*_effect\([^\n]*", early_hash_error)
    assert effect is not None
    assert effect.start() < early_hash_error.index(constructor[0])
    assert not _calls(early_hash_error, "py_set_add")


def test_keyword_mapping_merges_remain_owned_through_move():
    text = _emit("""def probe(mapping):
    return slot_kwargs_probe(first=1, **mapping, last=2)
""")
    text = _probe_function(text)
    # The first explicit group is already the private output. The mapping
    # and trailing explicit group still merge, each moving its completed
    # owner back into that oldest root before temporaries are retired.
    merges = _calls(text, "py_call_merge_kwargs_unique")
    moves = _calls(text, "pcc_gc_root_move")
    assert len(merges) == 2
    assert len(moves) == len(merges)
    assert len(_calls(text, "py_dict_new")) == 2
    borrowed = root_copy_calls(text, local=True, borrowed=True)
    assert len(borrowed) == 1
    assert borrowed[0].source.startswith("%mapping.addr")
    assert text.index(borrowed[0].line) < text.index(merges[0])


def test_cpython_parameter_never_enters_native_slot_copy():
    with pytest.raises(L1CodegenError, match="CPython name requires an output-slot bridge"):
        _emit("""def probe(value):
    return slot_cpy_probe(value)
""")


def test_stale_owned_result_fails_closed():
    with pytest.raises(L1CodegenError, match="lacks an immediate owned-result handoff"):
        _emit("""def probe():
    return slot_stale_probe()
""")


def test_module_operand_leaves_root_before_nonparking_take():
    text = _emit("slot_operand_probe([1])\n")
    take = _calls(text, "pcc_gc_take_pinned_slot")[-1]
    prefix = text[:text.index(take)]
    assert (_calls(prefix, "pcc_gc_frame_leave_lifo")
            or _calls(prefix, "pcc_gc_frame_leave_lifo_mapped"))
    assert prefix.rfind("@pcc_gc_frame_leave_lifo") > prefix.rfind("@pcc_py_gc_minor_graph_unlock")


def test_constructor_call_uses_slot_dispatch():
    text = _emit("""class Meta(type):
    def __call__(cls, value):
        return value
class Plain:
    def __init__(self, value):
        self.value = value
class Custom(metaclass=Meta):
    pass
class Inherited(Custom):
    pass
def probe(parts):
    first = Plain(*parts)
    second = Custom(*parts)
    third = Custom(3)
    fourth = Inherited(4)
    return slot_operand_probe((Plain(1), Custom(2), first, second, third, fourth))
""")
    assert len(_calls(text, "py_obj_call_slots")) >= 6
    assert not _calls(text, "py_obj_call_method_kwargs")
    assert "ctor.unpack.result" in text


def test_classmethod_actual_cls_uses_slot_dispatch():
    text = _emit("""class Base:
    def __init__(self, value):
        self.value = value
    @classmethod
    def build(cls, parts):
        return cls(*parts)
class Meta(type):
    def __call__(cls, value):
        return value
class Child(Base, metaclass=Meta):
    pass
def probe():
    direct = Child(11)
    return Child.build((17,))
""")
    assert len(_calls(text, "py_obj_call_slots")) >= 2
    borrowed = root_copy_calls(text, local=True, borrowed=True)
    assert any(copy.source.startswith("%cls.addr") for copy in borrowed)
    assert not _calls(text, "py_obj_call_method_kwargs")


def test_raw_pointer_call_result_cannot_enter_managed_sink():
    prefix = """from pcc.extern import c_ptr
from pcc.unsafe import int_to_ptr
__pcc_runtime_port__ = True
def address() -> c_ptr:
    return int_to_ptr(4096)
"""
    with pytest.raises(L1CodegenError, match="raw pointer cannot be a slot-call operand"):
        _emit(prefix + "def probe():\n    return slot_operand_probe(address())\n")
    # Losing the call-site type must not erase the callee's raw ABI proof.
    with pytest.raises(L1CodegenError, match="raw-pointer ABI call cannot publish"):
        _emit(prefix + "def probe():\n    return slot_raw_erased_probe(address())\n")



def test_literal_integer_tree_publishes_every_result_before_cleanup():
    text = _emit("""def probe():
    return slot_operand_probe((-(1 << 100), ~((1 << 100) + 3),
                               +18446744073709551615, 3 ** 5,
                               (7 * 9 - 4) // 2, (19 % 7) >> 1,
                               (17 & 9) | (3 ^ 1)))
""")
    for name in ("shl", "neg", "xor", "add", "pow", "mul", "sub",
                 "floordiv", "mod", "shr", "and", "or"):
        calls = _calls(text, "py_int_" + name)
        assert calls, name
        for call in calls:
            result = re.search(r"(%[^ ]+) = call ptr", call)
            assert result is not None, call
            following = text[text.index(call) + len(call):].splitlines()[1]
            assert "store ptr " + result.group(1) in following, (call, following)
            assert ".operand" in following, following
    # Literals outside the tagged lane are static immortal objects
    # (1c0a63ab5), never parsed from text at run time.
    assert not _calls(text, "py_int_from_cstr")
    assert not _calls(text, "py_int_to_i64_lane")
    assert not _calls(text, "py_obj_invert")
    assert not _calls(text, "py_obj_neg")


def test_literal_integer_constructor_and_keyword_sinks():
    text = _emit("""class Row:
    def __init__(self, value, other=0):
        self.value = value
        self.other = other
def probe(value):
    return Row(*(value,), **{'other': -(1 << 100)})
""")
    assert _calls(text, "py_obj_call_slots")
    assert _calls(text, "py_int_shl")
    assert _calls(text, "py_int_neg")
    nested = _emit("""def probe():
    return slot_kwargs_probe(first=[-(1 << 100)], second={'value': ~(3 + 4)})
""")
    assert _calls(nested, "py_int_neg")
    assert _calls(nested, "py_int_xor")
    assert _calls(nested, "py_dict_set")


def test_literal_integer_operator_errors_use_rooted_values():
    for expression, helper in (("7 // 0", "py_int_floordiv"), ("7 % 0", "py_int_mod")):
        text = _emit("def probe():\n    return slot_operand_probe(" + expression + ")\n")
        assert _calls(text, helper)
        assert re.search(r"(%[^ ]+) = load ptr, ptr %[^\n]+\n"
                         r"[^\n]*icmp eq ptr \1, null", text)
        assert "divres_null" in text
        assert "div.zero" in text
    for expression in ("1 << -1", "1 >> -(1 << 100)"):
        text = _emit("def probe():\n    return slot_operand_probe(" + expression + ")\n")
        assert _calls(text, "py_int_cmp")
        assert re.search(r"sext i32 %[^ ]+ to i64", text)
        assert "shift.neg" in text
        assert not _calls(text, "py_int_to_i64_lane")


def test_literal_unary_bool_plus_produces_integer():
    text = _emit("def probe():\n    return slot_operand_probe(+True)\n")
    text = _probe_function(text)
    # Identity-copying the bool would preserve the wrong runtime type.
    assert _calls(text, "py_int_add")
    assert not root_copy_calls(text, borrowed=False)
    for expression, helper in (("True & False", "py_obj_and"),
                               ("True | False", "py_obj_or"),
                               ("True ^ False", "py_obj_xor"),
                               ("(True | False) + 1", "py_obj_or"),
                               ("~(True & False)", "py_obj_and")):
        source = "def probe():\n    return slot_operand_probe(" + expression + ")\n"
        module = type_infer.infer_module(parse_and_lift(source, "slot_operand.py", "slot_operand"))
        operand = module.body[0].body[0].value.args[0]
        codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
        assert codegen._slot_call_literal_integer_kind(operand) == 0
        with pytest.raises(L1CodegenError, match="integer producer requires literal provenance"):
            codegen._emit_slot_call_literal_integer(operand, "unproven.bool.tree")
        text = _probe_function(_emit(source))
        produced = re.search(r"(%[^ ]+) = call ptr[^\n]*@" + helper + r"\([^\n]*\n([^\n]+)", text)
        assert produced is not None
        assert produced.group(2).lstrip().startswith("store ptr " + produced.group(1) + ",")
        assert not _calls(text, "py_int_to_i64_lane")


@pytest.mark.parametrize("source", (
    "def probe(value: int):\n    return slot_operand_probe(-value)\n",
    "def value() -> int:\n    return 4\ndef probe():\n    return slot_operand_probe(-value())\n",
    "class Value:\n    def __neg__(self):\n        return 4\ndef probe(value: Value):\n    return slot_operand_probe(-value)\n",
    "def probe(value):\n    return slot_operand_probe(-value)\n",
))
def test_generic_unary_uses_owned_dispatch_without_literal_integer_proof(source):
    module = type_infer.infer_module(parse_and_lift(source, "slot_operand.py", "slot_operand"))
    expression = module.body[-1].body[0].value.args[0]
    codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
    # An annotation, callback return annotation or user __neg__ does not
    # establish an exact integer. The primitive producer still rejects it.
    assert codegen._slot_call_literal_integer_kind(expression) == 0
    with pytest.raises(L1CodegenError, match="integer producer requires literal provenance"):
        codegen._emit_slot_call_literal_integer(expression, "unproven.integer")

    text = _probe_function(_emit(source))
    produced = re.search(r"(%[^ ]+) = call ptr[^\n]*@py_obj_neg\([^\n]*\n([^\n]+)", text)
    assert produced is not None
    assert "store ptr " + produced.group(1) in produced.group(2)
    assert "probe.operand" in produced.group(2)
    assert _calls(text, "pcc_gc_foreign_lease_acquire")
    assert _calls(text, "pcc_gc_foreign_lease_release")
    assert not _calls(text, "py_int_neg")
    assert not _calls(text, "py_int_to_i64_lane")


@pytest.mark.parametrize("expression,helper,expected", (
    ("2 ** -1", None, 0.5),
    ("(2 ** -1) + 1", "py_obj_add", 1.5),
    ("1 + (2 ** -1)", "py_obj_add", 1.5),
    ("-(2 ** -1)", "py_obj_neg", -0.5),
))
def test_numeric_slot_negative_power_preserves_boxed_result(expression, helper, expected):
    from tests.python.test_dynamic_power_comparison_boundary import assert_rooted_power
    from tests.owned_ir_validation import verify_ir_text

    source = "def probe():\n    return slot_operand_probe(" + expression + ")\n"
    module = type_infer.infer_module(parse_and_lift(source, "slot_operand.py", "slot_operand"))
    operand = module.body[0].body[0].value.args[0]
    codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
    # A negative power is still not a proven integer tree. The generic
    # object-boundary producer now publishes the runtime's actual float
    # owner, so the former blanket compilation rejection is obsolete.
    assert codegen._slot_call_literal_integer_kind(operand) == 0
    with pytest.raises(L1CodegenError, match="integer producer requires literal provenance"):
        codegen._emit_slot_call_literal_integer(operand, "unproven.negative.power")

    text = _emit(source)
    verify_ir_text(text)
    body = _probe_function(text)
    assert len(assert_rooted_power(body)) == 1
    if helper is not None:
        produced = re.search(r"(%[^ ]+) = call ptr[^\n]*@" + helper
                             + r"\([^\n]*\n([^\n]+)", body)
        assert produced is not None
        assert produced.group(2).lstrip().startswith("store ptr " + produced.group(1) + ",")
    assert not _calls(body, "py_int_add")
    # This is the Python reference for the exact emitted source, not native
    # execution. The runtime source-body model separately checks 2 ** -1.
    namespace = {"slot_operand_probe": lambda value: value}
    exec(source, namespace)
    result = namespace["probe"]()
    assert type(result) is float and result == expected


def test_numeric_slot_direct_name_copies_authoritative_source_root():
    # Direct names copy their independently authoritative source root;
    # their numeric annotation is not a proof of an exact runtime kind.
    direct = _emit("def probe(value: int):\n    return slot_operand_probe(value)\n")
    direct = _probe_function(direct)
    owned = re.search(r"%value\.owned[^ ]* = alloca i1", direct) is not None
    copies = root_copy_calls(direct, local=True, borrowed=not owned)
    assert any(copy.source.startswith("%value.addr") for copy in copies)
    assert not root_copy_calls(direct, borrowed=owned)


def test_non_integer_binop_publishes_before_operand_cleanup():
    # Use runtime strings: two string literals fold before concat lowering.
    # Runtime object dispatch publishes its actual owner before releasing
    # either operand. The independent stale-result test remains a rejection.
    text = _emit("""def probe(left: str, right: str):
    return slot_operand_probe(left + right)
""")
    match = re.search(r"(%[^ ]+) = call ptr[^\n]*@py_obj_add\([^\n]*\n([^\n]+)", text)
    assert match is not None
    assert "store ptr " + match.group(1) in match.group(2)
    assert _calls(text, "pcc_gc_foreign_lease_acquire")
    assert _calls(text, "pcc_gc_foreign_lease_release")


def test_literal_integer_producer_does_not_admit_machine_arithmetic():
    ordinary = _emit("""from pcc import i64, u64
def signed(value: i64) -> i64:
    return value + 1
def unsigned(value: u64) -> u64:
    return value << 1
""")
    assert "add i64" in ordinary
    assert "shl i64" in ordinary
    assert not _calls(ordinary, "py_int_add")
    assert not _calls(ordinary, "py_int_shl")
    # Even entirely literal children must not override explicit lane types.
    module = type_infer.infer_module(parse_and_lift("", "machine.py", "machine"))
    codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
    for name in ("pcc.i64", "pcc.u64"):
        machine = IntType(name=name)
        literal = IntLit(span=None, ty=machine, value=1)
        expr = BinOp(span=None, ty=machine, op="+", lhs=literal, rhs=literal)
        assert codegen._slot_call_literal_integer_kind(expr) == 0
    from inspect import Parameter, signature
    from pcc.frontends.python.codegen.host_contract import L1_CODEGEN_HOST_METHODS
    from pcc.frontends.python.codegen._l1_codegen_static_methods import L1_CODEGEN_STATIC_METHODS
    from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports

    static = {entry["name"]: entry for entry in L1_CODEGEN_STATIC_METHODS}
    exports = _default_native_module_exports("pcc.frontends.python.codegen.layer1")
    native = {entry["name"]: entry for entry in exports["pcc.frontends.python.codegen.layer1"]["L1CodeGen"]["methods"]}
    for name, parameters in (
        ("_slot_call_literal_integer_kind", ("self", "expr")),
        ("_emit_slot_call_literal_integer", ("self", "expr", "label")),
    ):
        assert name in L1_CODEGEN_HOST_METHODS
        assert static[name] == native[name]
        assert tuple(item["name"] for item in static[name]["call_sig"]) == parameters
        assert all(item["kind"] == "pos" and not item["has_default"]
                   for item in static[name]["call_sig"])
        actual = signature(getattr(L1CodeGen, name)).parameters
        assert tuple(actual) == parameters
        assert all(item.kind == Parameter.POSITIONAL_OR_KEYWORD
                   and item.default == Parameter.empty for item in actual.values())


def test_original_integer_constructors_pipeline_ir_only(tmp_path, monkeypatch):
    from test_ordinary_int_object_boundaries import CONSTRUCTORS
    from pcc.frontends.python.pipeline import compile_python
    from tests.owned_ir_validation import verify_ir_text

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "ordinary_int_constructors.py"
    output = source.with_suffix(".ll")
    source.write_text(CONSTRUCTORS)
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   target_triple="x86_64-unknown-linux-gnu")
    text = output.read_text()
    verify_ir_text(text)
    assert _calls(text, "py_obj_call_slots")
    assert _calls(text, "py_int_shl")
    assert _calls(text, "py_int_neg")
    for cls in ("Box", "Row"):
        signature = re.search(r"^define [^\n]*@user_[^\n]*_" + cls + r"___init__\([^\n]*", text, re.M)
        assert signature is not None
        assert "i64" not in signature.group(0), signature.group(0)



def test_literal_bool_integer_leaves_preserve_kernel_preconditions():
    for expression, helper in (
        ("True << 0", "py_int_shl"),
        ("False >> 0", "py_int_shr"),
        ("1 << True", "py_int_shl"),
        ("4 >> True", "py_int_shr"),
        ("True ** False", "py_int_pow"),
        ("2 ** True", "py_int_pow"),
        ("+True", "py_int_add"),
        ("-False", "py_int_neg"),
        ("~(+True)", "py_int_xor"),
        ("(True << 0) + 1", "py_int_add"),
    ):
        text = _emit("def probe():\n    return slot_operand_probe(" + expression + ")\n")
        text = _probe_function(text)
        assert _calls(text, helper), expression
        # Primitive shift/power kernels require actual int operands, not
        # bool headers which only some bigint conversion paths understand.
        assert not _calls(text, "py_bool_from_bit"), expression
        assert not _calls(text, "py_bool_from_i1"), expression
        assert re.search(r"inttoptr i64 (?:1|3) to ptr", text), expression
    bare = _emit("def probe():\n    return slot_operand_probe(True)\n")
    bare = _probe_function(bare)
    assert _calls(bare, "py_bool_from_bit")
    assert not _calls(bare, "py_int_add")
    for expression, helper in (("True & False", "py_obj_and"),
                               ("True | False", "py_obj_or"),
                               ("True ^ False", "py_obj_xor"),
                               ("(True | False) + 1", "py_obj_or")):
        # These stay bool-valued through their actual generic bitwise call;
        # they never enter the literal-only integer primitive above.
        text = _probe_function(_emit("def probe():\n    return slot_operand_probe(" + expression + ")\n"))
        produced = re.search(r"(%[^ ]+) = call ptr[^\n]*@" + helper + r"\([^\n]*\n([^\n]+)", text)
        assert produced is not None
        assert produced.group(2).lstrip().startswith("store ptr " + produced.group(1) + ",")
    for expression in ("~True", "(~False) + 1"):
        # Direct bool inversion still requires the qualified 3.15 warning.
        with pytest.raises(L1CodegenError, match="proven literal-derived integer tree"):
            _emit("def probe():\n    return slot_operand_probe(" + expression + ")\n")
