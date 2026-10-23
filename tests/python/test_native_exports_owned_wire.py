"""The legacy JSON reader consumes only its private decoded containers."""

import copy
import json

import pytest

from pcc.frontends.python import pipeline_exports as exports


def _default(kind, **fields):
    return {exports._EXPORT_DEFAULT_WIRE_KEY: kind, **fields}


def _text(**fields):
    return json.dumps({"schema": exports._NATIVE_EXPORT_WIRE_SCHEMA, **fields})


def _dict_ids(value):
    found = set()
    if isinstance(value, dict):
        found.add(id(value))
        for item in value.values():
            found.update(_dict_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_dict_ids(item))
    return found


@pytest.mark.parametrize("include_uses", [False, True])
def test_legacy_decoder_reuses_json_owned_dictionaries(monkeypatch, include_uses):
    text = _text(
        native_exports={
            "provider": {
                "function": {
                    "kind": "function",
                    "args": [{"name": "value", "annotation": ["int", {}]}],
                    "empty": {},
                }
            }
        },
        derived_class_map={"Child": ["provider", {"name": "Base"}]},
        function_object_uses=[["provider", {"name": "function"}]],
    )
    original_loads = json.loads
    captured = []

    def load_private_tree(document):
        payload = original_loads(document)
        fields = [payload["native_exports"], payload["derived_class_map"]]
        if include_uses:
            fields.append(payload["function_object_uses"])
        captured.append((payload, _dict_ids(fields)))
        return payload

    monkeypatch.setattr(exports.json, "loads", load_private_tree)
    result = exports._decode_native_exports_wire(text, include_uses)
    assert len(captured) == 1
    payload, original_dict_ids = captured[0]
    assert _dict_ids(result) == original_dict_ids, "JSON-owned dictionaries were rebuilt"
    assert result[0] is payload["native_exports"], "JSON export dictionaries were cloned"
    assert result[1] is payload["derived_class_map"]
    function = result[0]["provider"]["function"]
    assert function is payload["native_exports"]["provider"]["function"]
    assert isinstance(function["args"], tuple)
    assert function["args"][0]["annotation"] == ("int", {})
    assert result[1]["Child"] == ("provider", {"name": "Base"})
    assert list(function) == ["kind", "args", "empty"]
    if include_uses:
        assert result[2] == (("provider", {"name": "function"}),)
        assert result[2][0][1] is payload["function_object_uses"][0][1]
    else:
        assert len(result) == 2
        assert isinstance(payload["function_object_uses"], list)


@pytest.mark.parametrize(
    "wire",
    [
        _default("absent"),
        _default("complex"),
        _default("unknown", ignored=[_default("int", value="invalid")]),
        _default("none"),
        _default("bool", value=True),
        _default("int", value=1 << 130),
        _default("float", value=-1.25),
        _default("str", value="hello\u2603"),
        _default("bytes", value=[0, 127, 255]),
        _default("name", ident="factory"),
        _default("attr", obj=_default("name", ident="module"), name="factory"),
        _default("attr", obj=_default("complex"), name="ignored"),
        _default("unary", op="-", operand=_default("int", value=7)),
        _default("unary", op="-", operand=_default("complex")),
        _default("tuple", elems=[_default("int", value=1), _default("none")]),
        _default("list", elems=[_default("str", value="entry")]),
        _default(
            "dict",
            pairs=[[_default("str", value="key"), _default("int", value=9)]],
        ),
        _default("tuple"),
        _default("list"),
        _default("dict"),
        _default("dataclass-factory", factory=_default("name", ident="list")),
        _default("dataclass-factory", factory=_default("complex")),
    ],
)
def test_owned_default_decoding_matches_shared_decoder(wire):
    container = {"provider": {"args": [{"default": wire}]}}
    expected = exports._native_export_from_wire(container)
    actual, derived = exports._decode_native_exports_wire(_text(native_exports=container))
    assert actual == expected
    assert derived == {}


def test_shared_decoder_still_copies_shared_input_without_mutation():
    child = {"values": [1, {"default": _default("int", value=2)}]}
    wire = {"first": child, "second": child, "rows": [child]}
    before = copy.deepcopy(wire)
    decoded = exports._native_export_from_wire(wire)
    assert wire == before
    assert wire["first"] is wire["second"] is wire["rows"][0]
    assert decoded is not wire
    assert decoded["first"] is not child
    assert decoded["first"] is not decoded["second"]
    assert decoded["rows"][0] is not child
    assert isinstance(decoded["first"]["values"], tuple)
    assert isinstance(wire["first"]["values"], list)


@pytest.mark.parametrize("value", [{}, [], None, 17, "literal", [1, {"items": []}]])
def test_legacy_decoder_preserves_permissive_field_values(value):
    text = _text(native_exports=value, derived_class_map=value, function_object_uses=value)
    expected = exports._native_export_from_wire(value)
    assert exports._decode_native_exports_wire(text, True) == (expected, expected, expected)


def test_legacy_decoder_preserves_missing_fields_and_ignored_uses():
    assert exports._decode_native_exports_wire(_text(), True) == ({}, {}, ())
    text = _text(function_object_uses=[_default("int", value="invalid")])
    assert exports._decode_native_exports_wire(text) == ({}, {})
    with pytest.raises(ValueError, match="invalid"):
        exports._decode_native_exports_wire(text, True)


@pytest.mark.parametrize(
    "wire",
    [
        _default("int", value="invalid"),
        _default("bytes", value=[256]),
        _default("dict", pairs=[[]]),
        _default("tuple", elems=None),
    ],
)
def test_owned_default_errors_match_shared_decoder(wire):
    value = {"converted_first": [{"values": [1]}], "bad_default": wire}
    with pytest.raises(Exception) as expected:
        exports._native_export_from_wire(value)
    with pytest.raises(type(expected.value)) as actual:
        exports._decode_native_exports_wire(_text(native_exports=value))
    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize(
    "first_field", ["native_exports", "derived_class_map", "function_object_uses"]
)
def test_legacy_decoder_preserves_field_error_order(first_field):
    fields = {}
    for name in ("native_exports", "derived_class_map", "function_object_uses"):
        fields[name] = {"bad": _default("int", value=name)}
    names = list(fields)
    for name in names[:names.index(first_field)]:
        fields[name] = {}
    with pytest.raises(ValueError, match=first_field):
        exports._decode_native_exports_wire(_text(**fields), True)


@pytest.mark.parametrize("text", ["{", "null", "[]", "{}", '{"schema":"wrong"}'])
def test_legacy_decoder_preserves_json_and_schema_errors(text):
    with pytest.raises((ValueError, AttributeError)):
        exports._decode_native_exports_wire(text, True)


def test_indexed_reader_does_not_use_legacy_owned_decoder(tmp_path, monkeypatch):
    path = tmp_path / "exports.wire"
    native = {"provider": {"function": {"args": ({"name": "value"},)}}}
    exports._write_native_exports_wire(
        str(path),
        native,
        {},
        ("provider.function",),
        module_dependencies={"provider": ()},
        unique_class_preload_index={
            "types": (), "base_keys": (), "roots": {"provider": ((), ())}
        },
    )

    def unexpected(_value):
        pytest.fail("indexed input entered the legacy owned decoder")

    monkeypatch.setattr(exports, "_native_export_from_owned_wire", unexpected)
    assert exports._read_native_exports_wire(str(path), True) == (
        native, {}, ("provider.function",)
    )
    restored, derived, preload, indexed = exports._read_native_exports_wire_for_module(
        str(path), "provider"
    )
    assert restored == native
    assert derived == {}
    assert preload == {"types": (), "base_keys": (), "drop_keys": (), "set_keys": ()}
    assert indexed is True
