"""Host-only checks for parameter-target rebinding and entry storage.

These tests parse or emit IR. They never build or execute a native program.
"""

import re
from types import SimpleNamespace

import pytest

from pcc.frontends.python import parser
from pcc.frontends.python.codegen import cpy_call_lowering
from pcc.frontends.python.codegen.cpy_call_lowering import (
    CpyCallLoweringMixin,
    _name_is_stored_in,
)
from pcc.frontends.python.py_ast import FuncDef
from tests.python.root_copy_ir import root_copy_calls
from tests.python.test_slot_call_operand_roots import _emit


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("value = other", True),
        ("value += other", True),
        ("value, other = replacement", True),
        ("(value,), other = replacement", True),
        ("[value, other] = replacement", True),
        ("[other, [value]] = replacement", True),
        ("(other, [value, tail]) = replacement", True),
        ("for (value,), other in replacement:\n    pass", True),
        ("for [other, [value]] in replacement:\n    pass", True),
        ("value.field, other = replacement", False),
        ("value[0], other = replacement", False),
        ("other[value] = replacement", False),
        # Keep the enclosing scan conservative: nested bodies may contain
        # nonlocal writes, and the same scan guards the own-kwargs shortcut.
        ("def inner():\n    value = other", True),
        ("def inner():\n    nonlocal value\n    value = other", True),
        ("class Inner:\n    value = other", True),
        ("fn = lambda value: value", False),
    ],
)
def test_store_scan_preserves_target_and_scope_policy(statement, expected):
    source = "def probe(value, other, replacement):\n" + "\n".join(
        "    " + line for line in statement.splitlines()
    ) + "\n"
    module = parser.parse(source, "parameter_scan.py")
    function = module.body[0]
    assert isinstance(function, FuncDef)
    assert _name_is_stored_in(function.body, "value") is expected


def test_target_walker_does_not_enter_a_nested_scope():
    module = parser.parse("def inner():\n    value = other\n", "parameter_scan.py")
    assert not cpy_call_lowering._target_stores_name(module.body[0], "value")


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("(kwargs,), other = replacement", False),
        ("[other, [kwargs]] = replacement", False),
        ("kwargs.field = replacement", True),
        ("kwargs['key'] = replacement", True),
    ],
)
def test_own_kwargs_shortcut_rejects_recursive_name_rebinding(statement, expected):
    module = parser.parse(
        "def probe(replacement, **kwargs):\n    " + statement +
        "\n    return kwargs\n", "parameter_scan.py",
    )
    function = module.body[0]
    host = SimpleNamespace(current_func_def=function)
    kwargs_expr = function.body[-1].value
    accepted = CpyCallLoweringMixin._cpy_kw_mapping_is_own_kwargs_param(host, kwargs_expr)
    assert accepted is expected


@pytest.mark.parametrize("method", [False, True], ids=["function", "method"])
@pytest.mark.parametrize(
    "target",
    ["(value,), other", "[value, other]", "[other, [value]]"],
    ids=["nested-tuple", "list", "nested-list"],
)
def test_conditional_parameter_unpack_uses_entry_owned_storage(target, method, tmp_path):
    source = ("def probe(value, take, replacement):\n"
              "    if take:\n"
              "        " + target + " = replacement\n"
              "    return slot_args_probe(value)\n")
    symbol = "user_slot_operand_probe"
    if method:
        source = "class Holder:\n" + "\n".join(
            "    " + line.replace("def probe(value,", "def probe(self, value,")
            for line in source.splitlines()
        ) + "\n"
        symbol = "user_slot_operand_Holder_probe"
    emitted = _emit(source)
    match = re.search(r"^define[^\n]*@" + symbol + r"\([^\n]*\{\n(.*?)^\}",
                      emitted, re.M | re.S)
    assert match is not None
    body = match[1]
    (tmp_path / "parameter-unpack.ll").write_text(body)
    assert "strict.nolib.stub" not in body
    split_slots = re.findall(r"^  (%value\.unpack\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert not split_slots
    slots = re.findall(r"^  (%value\.for\.obj\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert len(slots) == 1
    slot = re.escape(slots[0])
    assert re.search(r"@pcc_gc_frame_enter(?:_mapped)?\(ptr @\.pcc\.gc\.frame\.map\.1, ptr " + slot + r"\)", body)
    assert re.search(r"@pcc_gc_store_root_take\(ptr " + slot + r", ptr %value\.for\.pre\.retain", body)
    assert re.search(r"@pcc_gc_root_move\(ptr " + slot + r", ptr ", body)
    assert any(copy.source == slots[0] for copy in
               root_copy_calls(body, local=True, borrowed=False))
    assert re.search(r"@pcc_gc_store_root\(ptr " + slot + r", ptr null\)", body)
    # The synthetic recursion-activation preamble can branch before the
    # user's entry block. The owner must precede the user's condition, not
    # that activation failure edge which never enters the function body.
    entry_start = body.index("\nentry:\n")
    first_body_branch = body.index("br i1 ", entry_start)
    initialized_at = body.index("@pcc_gc_store_root_take(ptr " + slots[0])
    assert entry_start < initialized_at < first_body_branch


@pytest.mark.parametrize("statement", [
    "first, [second] = replacement\nthird += replacement",
    "for [first, (second,)] in replacement:\n    third = replacement",
    "first.field = replacement\nsecond[0] = replacement",
    "def nested():\n    nonlocal first\n    first = replacement\nclass Nested:\n    second = replacement",
    "x = lambda first: first\ny = (second := replacement)",
    "try:\n    first = replacement\nexcept Exception:\n    second = replacement\nfinally:\n    third = replacement",
])
def test_owned_multi_parameter_scan_matches_legacy_queries(statement):
    source = "def probe(first, second, third, replacement):\n" + "\n".join(
        "    " + line for line in statement.splitlines()) + "\n"
    body = parser.parse(source, "parameter_batch.py").body[0].body
    names = ["first", "second", "third", "replacement"]
    expected = {name for name in names if _name_is_stored_in(body, name)}
    batch = getattr(cpy_call_lowering, "_owned_stored_parameter_names", None)
    assert batch is not None
    assert set(batch(body, names)) == expected
    duplicate = batch(body, names + names)
    assert len(duplicate) == len(expected) and set(duplicate) == expected


def test_owned_multi_parameter_scan_declines_custom_and_incomplete_nodes_without_getters():
    from pcc.frontends.python.py_ast import Assign, Name
    batch = getattr(cpy_call_lowering, "_owned_stored_parameter_names", None)
    assert batch is not None
    events = []

    class Foreign:
        @property
        def body(self):
            events.append("foreign body")
            return ()

    class ForeignName(Name):
        @property
        def value(self):
            events.append("foreign name")
            return None

    assert batch((Foreign(),), ["first", "second"]) is None
    assert batch((ForeignName(None, None, "first"),), ["first", "second"]) is None
    assert events == []
    # The legacy scan owns the exact error/short-circuit behavior here.
    assert batch((object.__new__(Assign),), ["first", "second"]) is None


def test_rebound_owner_batches_multiple_parameters_preserving_promotion_order(monkeypatch):
    from pcc.frontends.python.codegen import ownership_lowering, for_loop_lowering
    from pcc.frontends.python.py_ast import DynType
    from pcc.ir.compat import ir
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    module = parser.parse(
        "def probe(first, second, third):\n    second = first\n    first = third\n",
        "parameter_batch.py",
    )
    body = module.body[0]
    events = []
    ptr = ir.IntType(8).as_pointer()
    typ = DynType("dyn")
    host = L1CodeGen(module)
    assert host._owned_frozen_scope_input is False
    host._owned_frozen_scope_input = True
    host._current_param_names = {"first", "second", "third"}
    host.env = {name: (object(), ptr, typ) for name in ("first", "second", "third")}
    host.current_func_def = body
    host._is_object = lambda ty: events.append("eligible") or True
    def promote(owner, name, bind_ty):
        events.append(name)
    monkeypatch.setattr(for_loop_lowering, "_for_prepare_owned_object_target", promote)
    def no_single(*args):
        raise AssertionError("canonical multi-parameter owner must scan once")
    monkeypatch.setattr(cpy_call_lowering, "_name_is_stored_in", no_single)
    ownership_lowering.prepare_rebound_object_parameters(host, body, set())
    assert events == ["eligible", "first", "eligible", "second", "eligible"]
    assert host._current_param_names == {"third"}


def test_rebound_owner_custom_fallback_keeps_legacy_getter_and_promotion_order(monkeypatch):
    from pcc.frontends.python.codegen import ownership_lowering, for_loop_lowering
    from pcc.frontends.python.py_ast import DynType
    from pcc.ir.compat import ir
    canonical = parser.parse(
        "def probe(first, second):\n    first = second\n    second = first\n",
        "parameter_batch.py",
    ).body[0].body
    events = []

    class Foreign:
        @property
        def body(self):
            events.append("foreign body")
            return canonical

    ptr = ir.IntType(8).as_pointer()
    typ = DynType("dyn")
    host = SimpleNamespace(_freestanding_module=False, _module_has_c_abi_export=False,
                           _current_param_names={"first", "second"},
                           env={name: (object(), ptr, typ) for name in ("first", "second")})
    host._is_object = lambda ty: events.append("eligible") or True
    monkeypatch.setattr(for_loop_lowering, "_for_prepare_owned_object_target",
                        lambda owner, name, bind_ty: events.append(name))
    ownership_lowering.prepare_rebound_object_parameters(host, SimpleNamespace(body=(Foreign(),)), set())
    assert events == ["eligible", "foreign body", "first", "eligible", "foreign body", "second"]
    assert not host._current_param_names


def test_rebound_owner_single_parameter_stays_on_legacy_query(monkeypatch):
    from pcc.frontends.python.codegen import ownership_lowering, for_loop_lowering
    from pcc.frontends.python.py_ast import DynType
    from pcc.ir.compat import ir
    ptr = ir.IntType(8).as_pointer()
    typ = DynType("dyn")
    host = SimpleNamespace(_freestanding_module=False, _module_has_c_abi_export=False,
                           _current_param_names={"value"}, env={"value": (object(), ptr, typ)})
    host._is_object = lambda ty: True
    calls = []
    monkeypatch.setattr(cpy_call_lowering, "_owned_stored_parameter_names",
                        lambda *args: pytest.fail("single query must not allocate batch"))
    monkeypatch.setattr(cpy_call_lowering, "_name_is_stored_in",
                        lambda body, name: calls.append(name) or True)
    monkeypatch.setattr(for_loop_lowering, "_for_prepare_owned_object_target", lambda *args: None)
    ownership_lowering.prepare_rebound_object_parameters(host, SimpleNamespace(body=()), set())
    assert calls == ["value"]
    assert not host._current_param_names

def test_generic_rebound_owner_observes_later_eligible_callback_mutation(monkeypatch):
    from pcc.frontends.python.codegen import ownership_lowering, for_loop_lowering
    from pcc.frontends.python.py_ast import DynType
    from pcc.ir.compat import ir
    first = parser.parse("def probe(first, second):\n    first = second\n", "mutation.py").body[0]
    second = parser.parse("def probe(first, second):\n    second = first\n", "mutation.py").body[0]
    fd = SimpleNamespace(body=first.body)
    ptr = ir.IntType(8).as_pointer()
    events = []

    class Host(SimpleNamespace):
        def __getattr__(self, name):
            if name == "_owned_frozen_scope_input":
                pytest.fail("generic host must not observe private owner marker lookup")
            raise AttributeError(name)

    host = Host(_freestanding_module=False, _module_has_c_abi_export=False,
                _current_param_names={"first", "second"},
                env={name: (object(), ptr, DynType("dyn")) for name in ("first", "second")})
    def eligible(ty):
        events.append("eligible")
        if events.count("eligible") == 2:
            fd.body = second.body
        return True
    host._is_object = eligible
    monkeypatch.setattr(for_loop_lowering, "_for_prepare_owned_object_target",
                        lambda owner, name, bind_ty: events.append(name))
    ownership_lowering.prepare_rebound_object_parameters(host, fd, set())
    assert events == ["eligible", "first", "eligible", "second"]
    assert not host._current_param_names


def test_public_codegen_owner_keeps_original_scan_for_observed_canonical_fields(monkeypatch):
    from pcc.frontends.python.codegen import ownership_lowering, for_loop_lowering
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_ast import DynType, Return
    from pcc.ir.compat import ir
    module = parser.parse("def probe(first, second):\n    return first\n", "observer.py")
    fd = module.body[0]
    host = L1CodeGen(module)
    host.current_func_def = fd
    host._current_param_names = {"first", "second"}
    ptr = ir.IntType(8).as_pointer()
    host.env = {name: (object(), ptr, DynType("dyn")) for name in ("first", "second")}
    original = Return.__getattribute__
    events = []
    def observed(node, name):
        if name == "value":
            events.append("value")
        return original(node, name)
    monkeypatch.setattr(Return, "__getattribute__", observed)
    monkeypatch.setattr(for_loop_lowering, "_for_prepare_owned_object_target",
                        lambda *args: pytest.fail("return is not a store"))
    ownership_lowering.prepare_rebound_object_parameters(host, fd, set())
    assert events == ["value", "value"]


@pytest.mark.parametrize("changed_callback", [
    None, "_read_py_ast_wire", "build_closed_world_context",
    "_contextual_host_params_for_module", "_module_imports_pcc_native_extension",
    "_copy_native_module_exports", "_closed_world_function_object_exports",
    "_read_native_exports_wire", "_read_native_exports_wire_for_module",
    "_read_python_frontend_worker_manifest", "_module_uses_default_native_exports",
    "_run_python_multi_export_worker", "_run_python_multi_summary_worker",
    "_python_frontend_worker_executable", "_python_frontend_worker_timing_enabled",
    "_log", "_ir_needs_libpython", "_safe_exception_text",
    "_write_python_frontend_worker_error", "PyPipelineError",
])
def test_real_worker_wrapper_owns_only_canonical_scope_callbacks(monkeypatch, changed_callback):
    from pcc.frontends.python import pipeline
    from pcc.frontends.python import pipeline_frontend_worker_execution
    calls = []
    if changed_callback is not None:
        monkeypatch.setattr(pipeline, changed_callback, lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_frontend_worker_execution, "run_codegen_worker",
                        lambda path, **kwargs: calls.append(kwargs) or 17)
    assert pipeline.run_python_multi_codegen_worker("unused") == 17
    assert calls[0]["owned_frozen_scope_input"] is (changed_callback is None)


def test_callback_injectable_worker_scope_opt_in_defaults_to_false():
    import inspect
    from pcc.frontends.python import pipeline_frontend_worker_execution
    parameter = inspect.signature(pipeline_frontend_worker_execution.run_codegen_worker).parameters
    assert parameter["owned_frozen_scope_input"].default is False

@pytest.mark.parametrize("route", ["injected", "ordinary", "error", "direct"])
def test_actual_worker_scope_marker_is_one_generation_and_only_ordinary(monkeypatch, tmp_path, route):
    from pcc.frontends.python import pipeline, pipeline_frontend_workers as workers
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.codegen import cpy_call_lowering
    for name in ("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
                 "PCC_DIRECT_INDEXED_SIDECAR", "PCC_TEXT_INDEXED_KERNEL_EMIT"):
        monkeypatch.setenv(name, "0")
    source = tmp_path / "probe.py"
    source.write_text("def probe(first: str, second: str) -> str:\n    first = second\n    return first\n")
    manifest = tmp_path / "worker.manifest"
    workers.write_worker_manifest(
        str(manifest), str(tmp_path / "result.tsv"), str(tmp_path), "", "",
        [str(source)], ["probe"], [0], entry_module="probe", sibling_inits=(),
        ir_scaffold_mode="on", verbose=False,
    )
    if route == "injected":
        original_context = pipeline.build_closed_world_context
        monkeypatch.setattr(pipeline, "build_closed_world_context",
                            lambda *args, **kwargs: original_context(*args, **kwargs))
    original_generate = L1CodeGen._generate_initial_module
    owners = []
    markers = []
    def traced_generate(owner):
        owners.append(owner)
        markers.append(owner._owned_frozen_scope_input)
        if route == "error":
            raise RuntimeError("intentional generation error")
        return original_generate(owner)
    monkeypatch.setattr(L1CodeGen, "_generate_initial_module", traced_generate)
    original_batch = cpy_call_lowering._owned_stored_parameter_names
    scans = []
    def traced_batch(body, names):
        scans.append(tuple(names))
        return original_batch(body, names)
    monkeypatch.setattr(cpy_call_lowering, "_owned_stored_parameter_names", traced_batch)
    if route == "direct":
        from pcc.frontends.python import pipeline_frontend_worker_execution
        real_worker = pipeline_frontend_worker_execution.run_codegen_worker
        captured = []
        monkeypatch.setattr(pipeline_frontend_worker_execution, "run_codegen_worker",
                            lambda path, **kwargs: captured.append(kwargs) or 0)
        assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0
        kwargs = captured[0]
        assert kwargs.pop("owned_frozen_scope_input") is True
        status = real_worker(str(manifest), **kwargs)
    else:
        status = pipeline.run_python_multi_codegen_worker(str(manifest))
    assert status == (1 if route == "error" else 0), (tmp_path / "result.tsv").read_text()
    assert markers == [route in ("ordinary", "error")]
    assert owners[0]._owned_frozen_scope_input is False
    assert (("first", "second") in scans) is (route == "ordinary")
    if route != "error":
        assert "strict.nolib.stub:" not in (tmp_path / "module_0.ll").read_text()
