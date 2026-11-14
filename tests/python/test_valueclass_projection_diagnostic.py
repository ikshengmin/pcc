"""A missing valueclass owner reports context without admitting the value."""

from types import SimpleNamespace

import pytest

from pcc.ir.compat import ir
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.codegen.type_abi_lowering import TypeAbiLoweringMixin


MESSAGE = (
    "boxed valueclass projection requires an authoritative object root before evaluation"
)
PAYLOAD = ir.LiteralStructType((ir.IntType(64), ir.IntType(64)))


class _UnrootedValue:
    type = ir.IntType(8).as_pointer()

    def __str__(self):
        raise AssertionError("diagnostic formatted an unrooted value")

    def __repr__(self):
        raise AssertionError("diagnostic inspected an unrooted value")


def _host(*, context=True, payload_type=PAYLOAD, paths=(), source=None):
    events = []
    projected = object()

    def project(root, ty):
        events.append((root, ty))
        return projected

    host = SimpleNamespace(
        _valueclass_payload_ir_type=lambda ty: payload_type,
        _valueclass_payload_pointer_field_paths=lambda ty: paths,
        _valueclass_payload_source=lambda value: source,
        _emit_valueclass_payload_from_root=project,
    )
    if context:
        host.current_function = SimpleNamespace(name="user_demo_Holder___init__")
        host.builder = SimpleNamespace(_block=SimpleNamespace(name="ternary_false.17"))
    return host, events, projected


@pytest.mark.parametrize("context", [False, True])
def test_missing_object_root_keeps_guard_and_reports_compiler_context(context):
    host, events, _ = _host(context=context)
    ty = SimpleNamespace(name="Pair")
    with pytest.raises(L1CodegenError) as caught:
        TypeAbiLoweringMixin._emit_object_to_valueclass_payload(
            host, _UnrootedValue(), ty,
        )
    suffix = (
        "; function='user_demo_Holder___init__'; block='ternary_false.17'; target='Pair'"
        if context else "; function='<unknown>'; block='<unknown>'; target='Pair'"
    )
    assert str(caught.value) == MESSAGE + suffix
    assert events == []


def test_supplied_object_source_still_delegates_to_rooted_projection():
    class NoDiagnosticContext(SimpleNamespace):
        @property
        def current_function(self):
            raise AssertionError("success path read function context")

        @property
        def builder(self):
            raise AssertionError("success path read block context")

    class NoTargetName:
        @property
        def name(self):
            raise AssertionError("success path read target context")

    host, events, projected = _host(context=False)
    host = NoDiagnosticContext(**vars(host))
    ty, root = NoTargetName(), object()
    actual = TypeAbiLoweringMixin._emit_object_to_valueclass_payload(
        host, _UnrootedValue(), ty, source_root=root,
    )
    assert actual is projected
    assert events == [(root, ty)]


@pytest.mark.parametrize("field", ["function", "block", "target"])
def test_non_string_context_is_not_formatted(field):
    host, events, _ = _host()
    ty = SimpleNamespace(name="Pair")
    owner = {
        "function": host.current_function,
        "block": host.builder._block,
        "target": ty,
    }[field]
    owner.name = _UnrootedValue()
    with pytest.raises(L1CodegenError) as caught:
        TypeAbiLoweringMixin._emit_object_to_valueclass_payload(
            host, _UnrootedValue(), ty,
        )
    assert str(caught.value).startswith(MESSAGE + "; function=")
    assert "; " + field + "='<unknown>'" in str(caught.value)
    assert events == []


@pytest.mark.parametrize("field", ["function", "block", "target"])
def test_long_context_is_bounded_and_control_characters_are_escaped(field):
    host, events, _ = _host()
    ty = SimpleNamespace(name="Pair")
    owner = {
        "function": host.current_function,
        "block": host.builder._block,
        "target": ty,
    }[field]
    name = "injected\n\r\t\x00\x1b'\\" + "x" * 2000
    owner.name = name
    with pytest.raises(L1CodegenError) as caught:
        TypeAbiLoweringMixin._emit_object_to_valueclass_payload(
            host, _UnrootedValue(), ty,
        )
    diagnostic = str(caught.value)
    assert "; " + field + "=" + repr(name[:160]) in diagnostic
    assert all(character not in diagnostic for character in "\n\r\t\x00\x1b")
    assert "x" * 161 not in diagnostic
    assert len(diagnostic) < len(MESSAGE) + 600
    assert events == []


def test_string_subclass_context_cannot_override_slice_or_repr():
    class UnformattableName(str):
        def __getitem__(self, key):
            raise AssertionError("diagnostic sliced a string subclass")

        def __repr__(self):
            raise AssertionError("diagnostic formatted a string subclass")

    host, events, _ = _host()
    ty = SimpleNamespace(name=UnformattableName("Pair"))
    with pytest.raises(L1CodegenError) as caught:
        TypeAbiLoweringMixin._emit_object_to_valueclass_payload(
            host, _UnrootedValue(), ty,
        )
    assert "; target='<unknown>'" in str(caught.value)
    assert events == []


@pytest.mark.parametrize("field", ["function", "block", "target"])
def test_unreadable_context_cannot_mask_ownership_failure(field):
    class Unreadable:
        @property
        def name(self):
            raise ValueError("diagnostic context unavailable")

    host, events, _ = _host()
    ty = SimpleNamespace(name="Pair")
    if field == "function":
        host.current_function = Unreadable()
    elif field == "block":
        host.builder._block = Unreadable()
    else:
        ty = Unreadable()
    with pytest.raises(L1CodegenError) as caught:
        TypeAbiLoweringMixin._emit_object_to_valueclass_payload(
            host, _UnrootedValue(), ty,
        )
    assert str(caught.value) == MESSAGE
    assert events == []


@pytest.mark.parametrize("paths,source", [((), None), (((0,),), object())])
def test_existing_payload_with_required_provenance_is_unchanged(paths, source):
    host, events, _ = _host(paths=paths, source=source)
    value, ty = SimpleNamespace(type=PAYLOAD), SimpleNamespace(name="Pair")
    assert TypeAbiLoweringMixin._emit_object_to_valueclass_payload(host, value, ty) is value
    assert events == []


def test_managed_payload_without_producer_slot_keeps_original_guard():
    host, events, _ = _host(paths=((0,),))
    value, ty = SimpleNamespace(type=PAYLOAD), SimpleNamespace(name="Pair")
    with pytest.raises(L1CodegenError, match="^managed valueclass conversion requires its producer-owned payload slot$"):
        TypeAbiLoweringMixin._emit_object_to_valueclass_payload(host, value, ty)
    assert events == []


@pytest.mark.parametrize("payload_type,value_type", [
    (None, ir.IntType(8).as_pointer()),
    (PAYLOAD, ir.IntType(64)),
])
def test_non_projection_shapes_keep_no_result(payload_type, value_type):
    host, events, _ = _host(payload_type=payload_type)
    value, ty = SimpleNamespace(type=value_type), SimpleNamespace(name="Pair")
    assert TypeAbiLoweringMixin._emit_object_to_valueclass_payload(host, value, ty) is None
    assert events == []
