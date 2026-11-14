"""Indexed export readers consume private JSON dictionaries without copying."""

import json
import os
import subprocess
import sys

import pytest

from pcc.frontends.python import pipeline_exports as exports


def _rows():
    return [
        ("D", {"Base": ["dep", "Child"]}),
        ("F", [["root", {"name": "entry"}]]),
        ("P", {"root": ["dep"], "contextual": ["dep"]}),
        ("T", [["class", {"fields": [["value", ["int", 64, True]]]}]]),
        ("G", [["Base", 0]]),
        ("C", ["contextual"]),
        ("H", {"dep": {"extra": {"kind": "constant", "value": 7}},
               "host": {"provided": {"kind": "constant", "value": 8}}}),
        ("U\troot", [["root.Local"], [["dep.Other", 0]]]),
        ("U\tdep", [[], []]),
        ("U\tcontextual", [[], []]),
        ("U\thost", [[], []]),
        ("U\tunused", [[], []]),
        ("M\troot", {"entry": {"kind": "function", "args": [
            {"name": "value", "annotation": ["int", 64, True],
             "default": {exports._EXPORT_DEFAULT_WIRE_KEY: "int", "value": 23}},
        ]}}),
        ("M\tdep", {"Child": {"kind": "class", "field_names": ["value"]}}),
        ("M\tcontextual", {"context": {"kind": "constant", "value": 6}}),
        ("M\thost", {"unselected": {"kind": "constant", "value": 9}}),
        ("M\tunused", {"unused": {"kind": "constant", "value": 10}}),
    ]


def _document(rows):
    return exports._NATIVE_EXPORT_INDEXED_SCHEMA + "\n" + "".join(
        tag + "\t" + json.dumps(value) + "\n" for tag, value in rows
    )


@pytest.mark.parametrize("root", ["", "root", "contextual"])
def test_indexed_reader_reuses_each_selected_json_dictionary(monkeypatch, root):
    rows = _rows()
    text = _document(rows)
    with monkeypatch.context() as borrowed:
        borrowed.setattr(exports, "_native_export_from_owned_wire", exports._native_export_from_wire)
        expected = exports._read_indexed_native_exports_wire(text, root)

    original_loads = json.loads
    captured = {}

    def load_private_row(document):
        value = original_loads(document)
        captured[document] = value
        return value

    monkeypatch.setattr(exports.json, "loads", load_private_row)
    actual = exports._read_indexed_native_exports_wire(text, root)
    assert actual == expected
    native, derived, uses, preload = actual
    raw = {tag: json.dumps(value) for tag, value in rows}
    assert derived is captured[raw["D"]]
    assert uses[0][1] is captured[raw["F"]][0][1]
    selected = (
        ("root", "dep", "contextual", "host", "unused") if not root
        else ("root", "dep") if root == "root"
        else ("dep", "contextual", "host")
    )
    assert tuple(native) == selected
    for name in selected:
        if root == "contextual" and name == "host":
            assert native[name] is captured[raw["H"]]["host"]
        else:
            assert native[name] is captured[raw["M\t" + name]]
    if root:
        assert preload["types"][0][1] is captured[raw["T"]][0][1]
        assert raw["M\tunused"] not in captured
    if root != "contextual":
        assert raw["H"] not in captured
    else:
        assert native["dep"]["extra"] is captured[raw["H"]]["dep"]["extra"]
        assert "unselected" not in native["host"]
    if not root or root == "root":
        default = native["root"]["entry"]["args"][0]["default"]
        assert exports._export_default_to_wire(default) == {
            exports._EXPORT_DEFAULT_WIRE_KEY: "int", "value": 23,
        }
        assert isinstance(native["root"]["entry"]["args"], tuple)


@pytest.mark.parametrize("root", ["", "root", "contextual"])
@pytest.mark.parametrize("tag,bad_value", [
    ("D", []), ("F", {}), ("P", []), ("T", {}), ("G", {}), ("C", {}),
    ("H", []), ("M\troot", []), ("M\tdep", []), ("U\troot", [[], [], []]),
    ("M\troot", {"bad": {exports._EXPORT_DEFAULT_WIRE_KEY: "int", "value": "invalid"}}),
    ("T", [{"bad": {exports._EXPORT_DEFAULT_WIRE_KEY: "bytes", "value": [256]}}]),
])
def test_owned_indexed_errors_and_lazy_validation_match_borrowed_decoder(
    monkeypatch, root, tag, bad_value,
):
    text = _document([(name, bad_value if name == tag else value) for name, value in _rows()])

    def outcome():
        try:
            return ("value", exports._read_indexed_native_exports_wire(text, root))
        except Exception as exc:
            cause = exc.__cause__
            return ("error", type(exc), str(exc), type(cause), str(cause))

    with monkeypatch.context() as borrowed:
        borrowed.setattr(exports, "_native_export_from_owned_wire", exports._native_export_from_wire)
        expected = outcome()
    assert outcome() == expected


@pytest.mark.parametrize("metadata_tag", ["D", "F", "P", "T", "G", "C", "H"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("error_type", [MemoryError, RecursionError, ValueError])
def test_indexed_metadata_conversion_failure_preserves_destination(
    tmp_path, monkeypatch, metadata_tag, existing, error_type,
):
    # The existing writer converts all metadata before opening the output.
    # This protects that boundary only; later U/M writes are not atomic.
    output = tmp_path / "exports.indexed"
    previous = b"previous complete export\n"
    if existing:
        output.write_bytes(previous)
    error = error_type("metadata conversion failed: " + metadata_tag)
    original_convert = exports._native_export_to_wire
    tags = ("D", "F", "P", "T", "G", "C", "H")
    converted_tags = []
    depth = 0

    def fail_selected_conversion(value):
        nonlocal depth
        if depth == 0:
            tag = tags[len(converted_tags)]
            converted_tags.append(tag)
            if tag == metadata_tag:
                raise error
        depth += 1
        try:
            return original_convert(value)
        finally:
            depth -= 1

    monkeypatch.setattr(exports, "_native_export_to_wire", fail_selected_conversion)
    with pytest.raises(error_type) as caught:
        exports._write_native_exports_wire(
            str(output), {"root": {}}, {}, module_dependencies={"root": ()},
            unique_class_preload_index={
                "types": (), "base_keys": (), "roots": {"root": ((), ())},
            },
        )
    assert caught.value is error
    assert converted_tags == list(tags[:tags.index(metadata_tag) + 1])
    if existing:
        assert output.read_bytes() == previous
    else:
        assert not output.exists()


_NATIVE_PROGRAM = '''import json
import sys
import gc
from pcc.frontends.python.pipeline_exports import (
    _native_export_from_owned_wire, _native_export_from_wire,
    _read_native_exports_wire, _read_native_exports_wire_for_module,
    _write_native_exports_wire,
)

def main():
    private = json.loads('{"nested": {"items": [1, {"value": 23}]}}')
    nested = private["nested"]
    leaf = nested["items"][1]
    consumed = _native_export_from_owned_wire(private)
    assert consumed is private
    assert consumed["nested"] is nested
    assert consumed["nested"]["items"][1] is leaf
    assert consumed["nested"]["items"] == (1, {"value": 23})
    shared = {"nested": {"items": [1, 2]}}
    copied = _native_export_from_wire(shared)
    assert copied is not shared
    assert copied["nested"] is not shared["nested"]
    assert isinstance(shared["nested"]["items"], list)
    native = {
        "root": {"entry": {"kind": "function", "param_types": (("int", 64, True),)}},
        "dep": {"Child": {"kind": "class", "field_names": ("value",)}},
        "unused": {"unused": {"kind": "constant", "value": 17}},
    }
    derived = {"Base": ("dep", "Child")}
    preload = {"types": (("class", "Child"),), "base_keys": (("Base", 0),),
               "roots": {"root": ((), ()), "dep": ((), ()), "unused": ((), ())}}
    path = sys.argv[1]
    _write_native_exports_wire(
        path, native, derived, (("root", "entry"),),
        module_dependencies={"root": ("dep",)}, unique_class_preload_index=preload,
    )
    count = 0
    while count < 8:
        full, classes, uses = _read_native_exports_wire(path, True)
        assert full == native
        assert classes == derived
        assert uses == (("root", "entry"),)
        selected, classes, view, indexed = _read_native_exports_wire_for_module(path, "root")
        assert indexed
        assert tuple(selected) == ("root", "dep")
        assert selected["dep"] == native["dep"]
        assert classes == derived
        assert view == {"types": preload["types"], "base_keys": preload["base_keys"],
                        "drop_keys": (), "set_keys": ()}
        gc.collect()
        assert selected["root"]["entry"]["param_types"] == (("int", 64, True),)
        count += 1
    print("INDEXED_EXPORT_OWNERS_OK")

main()
'''


@pytest.mark.integration
def test_native_indexed_export_lifecycle_all_collectors(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "indexed_export_owners.py"
    source.write_text(_NATIVE_PROGRAM, encoding="utf-8")
    reference_path = tmp_path / "reference.indexed"
    reference = subprocess.run(
        [sys.executable, str(source), str(reference_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stdout == "INDEXED_EXPORT_OWNERS_OK\n"
    binary = tmp_path / "indexed_export_owners"
    python_program_compiler(
        str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive),
    )
    for gc in range(5):
        output = tmp_path / ("gc" + str(gc) + ".indexed")
        result = subprocess.run(
            [str(binary), str(output)], env=dict(os.environ, PCC_GC_BACKEND=str(gc)),
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (gc, result.stdout, result.stderr)
        assert result.stdout == reference.stdout
        assert result.stderr == ""
        assert output.read_bytes() == reference_path.read_bytes()
