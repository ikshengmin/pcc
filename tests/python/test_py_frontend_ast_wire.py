from __future__ import annotations

import json
import os
import subprocess
import textwrap

import pytest

from pcc.frontends.python.py_ast_contract import PY_AST_FIELD_NAME_OVERRIDES


NATIVE_WIRE_CONTRACT_PROGRAM = '''import json
from pcc.frontends.python.py_ast_contract import PY_AST_FIELD_NAME_OVERRIDES
from pcc.frontends.python.pipeline_ast_wire import (
    _PY_AST_FIELD_NAME_OVERRIDES, _py_ast_to_wire, _py_ast_from_wire,
    _decode_py_ast_wire, _py_ast_v2_payload,
)
from pcc.frontends.python.py_ast import DynType, SetType, ClassType, ValueArrayType, SourceSpan
from pcc.frontends.python.py_lift import parse_and_lift
def main():
    assert _PY_AST_FIELD_NAME_OVERRIDES is PY_AST_FIELD_NAME_OVERRIDES
    assert PY_AST_FIELD_NAME_OVERRIDES["Name"] == ("span", "ty", "ident")
    assert len(PY_AST_FIELD_NAME_OVERRIDES) == 66
    set_type = SetType(name="set", elem=DynType(name="dyn"))
    assert _py_ast_from_wire(_py_ast_to_wire(set_type)) == set_type
    element = ClassType(name="Point", module="m", fields=(), bases=(),
                        properties=(), valueclass=True)
    array_type = ValueArrayType(name="pcc.array", elem=element, length=3)
    assert _py_ast_from_wire(_py_ast_to_wire(array_type)) == array_type
    source = "class Item:\\n    token = 23\\n    def method(self, value=token):\\n        return value\\n"
    module = parse_and_lift(source, "wire.py", "wire")
    recovered = _py_ast_from_wire(json.loads(json.dumps(_py_ast_to_wire(module))))
    assert recovered == module
    assert recovered.body[0].body[1].args[1].default.ident == "token"
    compact = _py_ast_v2_payload(module)
    assert compact["files"] == ["wire.py"]
    assert _decode_py_ast_wire(json.dumps(compact)) == module
    spans = (SourceSpan("例.py", -1, 0, 1, 2), SourceSpan("<builtins>", 0, 0, 0, 0))
    compact_spans = _py_ast_v2_payload(spans)
    assert compact_spans["files"] == ["例.py", "<builtins>"]
    assert _decode_py_ast_wire(json.dumps(compact_spans)) == spans
    escaped = _decode_py_ast_wire(__HOST_ESCAPE_WIRE__)
    assert [ord(character) for character in escaped.docstring] == __ESCAPE_POINTS__
    assert json.dumps(escaped.docstring) == __HOST_ESCAPE_TEXT__
    assert _py_ast_from_wire(_py_ast_to_wire(escaped)) == escaped
    assert _decode_py_ast_wire(json.dumps(_py_ast_v2_payload(escaped))) == escaped
    print("AST_WIRE_CONTRACT_OK")
main()
'''


def test_pipeline_facade_reexports_ast_wire_codec() -> None:
    from pcc.frontends.python import pipeline, pipeline_ast_wire

    assert pipeline._py_ast_field_names is pipeline_ast_wire._py_ast_field_names
    assert pipeline._py_ast_to_wire is pipeline_ast_wire._py_ast_to_wire
    assert pipeline._py_ast_from_wire is pipeline_ast_wire._py_ast_from_wire
    assert pipeline._write_py_ast_wire is pipeline_ast_wire._write_py_ast_wire
    assert pipeline._read_py_ast_wire is pipeline_ast_wire._read_py_ast_wire


def test_ast_wire_field_names_use_the_stable_table_and_ignore_primitive_leaves() -> None:
    from pcc.frontends.python import pipeline_ast_wire, py_ast

    node = py_ast.Name(
        span=py_ast.SourceSpan("probe.py", 1, 0, 1, 1),
        ty=py_ast.IntType("int"),
        ident="value",
    )
    assert pipeline_ast_wire._py_ast_field_names(node) == (
        "span",
        "ty",
        "ident",
    )
    assert pipeline_ast_wire._py_ast_field_names("leaf") == ()


def test_py_ast_wire_roundtrip_preserves_full_lifted_ast() -> None:
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.pipeline import _py_ast_from_wire, _py_ast_to_wire

    source = textwrap.dedent(
        r'''
        import json

        class Box:
            label: str = "line1\nline2"

            def __init__(self, value: int = 7) -> None:
                self.value = value

            def render(self) -> str:
                quote = 'a"b'
                slash = "a\\b"
                raw = b"abc"
                return self.label + quote + slash + str(raw)

        def main() -> None:
            box = Box()
            print(json.dumps({"x": box.render()}))
        '''
    ).lstrip()
    ast_mod = parse_and_lift(source, "wire_probe.py", "wire_probe")
    wire_text = json.dumps(_py_ast_to_wire(ast_mod))
    recovered = _py_ast_from_wire(json.loads(wire_text))
    assert recovered == ast_mod


def test_parallel_self_codegen_enables_ast_wire_sidecars(tmp_path, monkeypatch) -> None:
    from pcc.frontends.python.pipeline import compile_python_multi

    entry = tmp_path / "entry.py"
    lib = tmp_path / "lib.py"
    out_ll = tmp_path / "out.ll"
    entry.write_text(
        textwrap.dedent(
            """
            def main() -> None:
                print(1)

            if __name__ == "__main__":
                main()
            """
        ).lstrip()
    , encoding="utf-8")
    lib.write_text(
        textwrap.dedent(
            """
            def helper() -> int:
                return 42
            """
        ).lstrip()
    , encoding="utf-8")
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "2")
    monkeypatch.setenv("PCC_PY_FRONTEND_AST_WIRE", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    profile = {}
    compile_python_multi(
        [str(entry), str(lib)],
        str(out_ll),
        module_names=["entry", "lib"],
        entry_module="entry",
        backend="self",
        emit_llvm_only=True,
        profile=profile,
    )
    assert out_ll.exists()
    counters = profile.get("counters", {})
    assert counters.get("multi_frontend_ast_wire_enabled") == 1
    assert counters.get("multi_frontend_chunks") == 2


def test_py_ast_wire_is_opt_in_by_default(monkeypatch) -> None:
    from pcc.frontends.python.pipeline import _python_frontend_ast_wire_enabled

    monkeypatch.delenv("PCC_PY_FRONTEND_AST_WIRE", raising=False)
    assert _python_frontend_ast_wire_enabled() is False

    monkeypatch.setenv("PCC_PY_FRONTEND_AST_WIRE", "1")
    assert _python_frontend_ast_wire_enabled() is True


def test_py_ast_wire_normalizes_legacy_classtype_null_fields() -> None:
    from pcc.frontends.python.pipeline import _py_ast_from_wire, _py_ast_to_wire
    from pcc.frontends.python.py_ast import ClassType

    cls = ClassType("PointerType", "", (), (), None, None)
    recovered = _py_ast_from_wire(_py_ast_to_wire(cls))
    assert recovered.fields == ()
    assert recovered.bases == ()
    assert recovered.properties == ()
    assert recovered.valueclass is False


def test_py_ast_wire_roundtrips_first_class_set_type() -> None:
    from pcc.frontends.python.pipeline import _py_ast_from_wire, _py_ast_to_wire
    from pcc.frontends.python.py_ast import DynType, SetType

    set_ty = SetType(name="set", elem=DynType(name="dyn"))

    assert _py_ast_from_wire(_py_ast_to_wire(set_ty)) == set_ty


def test_py_ast_wire_roundtrips_value_array_type() -> None:
    # A module annotating pcc.array[...] failed in the frontend worker with
    # "unknown py_ast wire node kind ValueArrayType".
    from pcc.frontends.python.pipeline import _py_ast_from_wire, _py_ast_to_wire
    from pcc.frontends.python.py_ast import ClassType, ValueArrayType

    elem = ClassType(name="Point", module="m", fields=(), bases=(), properties=(),
                     valueclass=True)
    array_ty = ValueArrayType(name="pcc.array", elem=elem, length=3)

    assert _py_ast_from_wire(_py_ast_to_wire(array_ty)) == array_ty


def test_stdlib_ast_lifter_preserves_interleaved_call_operand_order() -> None:
    from pcc.frontends.python.parser import parse
    from pcc.frontends.python.py_ast import Call, ExprStmt

    module = parse(
        "target(named=value, *items, **mapping)\n",
        "operand_order_probe.py",
    )
    stmt = module.body[0]
    assert isinstance(stmt, ExprStmt)
    assert isinstance(stmt.expr, Call)
    assert stmt.expr.operand_order == (
        ("kw", 0),
        ("arg", 0),
        ("kw", 1),
    )


@pytest.mark.integration
def test_native_ast_wire_uses_shared_contract_all_gcs(
    tmp_path, pcc_runtime_archive, python_program_compiler,
) -> None:
    source = tmp_path / "wire_contract.py"
    binary = tmp_path / "wire_contract"
    # The host produces canonical short escapes. A provider-only roundtrip
    # would miss a decoder that only accepts its own Unicode escape spelling.
    value = "\b\f\t\n\r\"\\\x00\x1f"
    wire = json.dumps({
        "schema": "pcc.frontends.python.py_ast.v1",
        "module": {
            "__pcc_py_ast_v1__": "Module",
            "fields": {"name": "escapes", "body": [], "docstring": value},
        },
    })
    program = NATIVE_WIRE_CONTRACT_PROGRAM.replace("__HOST_ESCAPE_WIRE__", repr(wire))
    program = program.replace("__HOST_ESCAPE_TEXT__", repr(json.dumps(value)))
    program = program.replace("__ESCAPE_POINTS__", "[8, 12, 9, 10, 13, 34, 92, 0, 31]")
    source.write_text(program)
    python_program_compiler(
        str(source), str(binary), backend="self",
        runtime_archive=str(pcc_runtime_archive),
    )
    for gc in (0,):
        environment = dict(os.environ, PCC_GC_BACKEND=str(gc))
        environment.pop("LC_ALL", None)
        result = subprocess.run(
            [str(binary)], env=environment, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (gc, result.stderr)
        assert result.stdout == "AST_WIRE_CONTRACT_OK\n"
        assert result.stderr == ""


def test_private_positional_ast_wire_roundtrip_and_v1_compatibility(tmp_path):
    from pcc.frontends.python import pipeline_ast_wire as wire
    from pcc.frontends.python.py_lift import parse_and_lift

    source = (
        "class Box:\n"
        "    def method(self, value: int = 7, *, label=None):\n"
        "        return (value, label, b'a\\x00b', 'é')\n"
        "def call(*items, **kwargs):\n"
        "    return Box().method(*items, **kwargs)\n"
    )
    module = parse_and_lift(source, "例\\probe.py", "wire")
    legacy = tmp_path / "legacy.json"
    compact = tmp_path / "compact.json"
    wire._write_py_ast_wire(str(legacy), module)
    wire._write_py_ast_wire_v2(str(compact), module)
    v2 = json.loads(compact.read_text(encoding="utf-8"))
    assert v2["schema"] == wire._PY_AST_WIRE_SCHEMA_V2
    assert v2["files"] == ["例\\probe.py"]
    assert json.loads(legacy.read_text(encoding="utf-8"))["schema"] == wire._PY_AST_WIRE_SCHEMA
    assert wire._read_py_ast_wire(str(legacy)) == module
    assert wire._read_py_ast_wire(str(compact)) == module
    assert wire._py_ast_to_wire(wire._read_py_ast_wire(str(compact))) == wire._py_ast_to_wire(module)
    first = compact.read_bytes()
    wire._write_py_ast_wire_v2(str(compact), module)
    assert compact.read_bytes() == first


def test_positional_ast_wire_preserves_opaque_fields_and_source_locations():
    from pcc.frontends.python import pipeline_ast_wire as wire, py_ast as ast

    opaque = {"n": ("node", None), "s": b"\x00\xff", "b": True, "d": {"nested": 3},
              "__pcc_py_ast_v1__": ["opaque"]}
    first = ast.SourceSpan("first.py", -1, 0, 4, 7)
    second = ast.SourceSpan("<builtins>", 0, 0, 0, 0)
    module = ast.Module("m", (
        ast.ExprStmt(first, ast.Name(first, ast.DynType("dyn"), "x")),
        ast.ExprStmt(second, ast.Name(second, ast.DynType("dyn"), "y")),
    ), opaque)
    payload = wire._py_ast_v2_payload(module)
    assert payload["files"] == ["first.py", "<builtins>"]
    restored = wire._decode_py_ast_wire(json.dumps(payload))
    assert restored == wire._py_ast_from_wire(wire._py_ast_to_wire(module))
    assert restored.docstring["n"] == ("node", None)
    assert restored.docstring["s"] == b"\x00\xff"
    assert restored.body[0].span == first
    assert restored.body[1].span == second


@pytest.mark.parametrize("module,files", [
    ({"unknown": []}, []),
    ({"n": ["Module"], "d": {}}, []),
    ({"n": []}, []),
    ({"n": ["unknown"]}, []),
    ({"n": ["Module", "m", [], None, "extra"]}, []),
    ({"n": ["Module", "m", [], {"bad": 1}]}, []),
    ({"s": [0, 1, 2, 3, 4]}, []),
    ({"s": [-1, 1, 2, 3, 4]}, ["f"]),
    ({"s": [True, 1, 2, 3, 4]}, ["f"]),
    ({"s": [0, 1, "2", 3, 4]}, ["f"]),
    ({"s": [0, 1, 2, 3]}, ["f"]),
    ({"n": ["SourceSpan", "f", 1, 2, 3, 4]}, ["f"]),
    ({"b": [256]}, []),
    ({"b": [True]}, []),
    ({"b": "bad"}, []),
    ({"d": []}, []),
    (None, ["duplicate", "duplicate"]),
    (None, [1]),
    (None, {}),
])
def test_positional_ast_wire_rejects_malformed_records(module, files):
    from pcc.frontends.python import pipeline_ast_wire as wire
    from pcc.frontends.python.pipeline_modes import PyPipelineError

    payload = {"schema": wire._PY_AST_WIRE_SCHEMA_V2, "files": files, "module": module}
    with pytest.raises(PyPipelineError):
        wire._decode_py_ast_wire(json.dumps(payload))


@pytest.mark.parametrize("change", ["missing_files", "missing_module", "extra"])
def test_positional_ast_wire_rejects_malformed_envelope(change):
    from pcc.frontends.python import pipeline_ast_wire as wire
    from pcc.frontends.python.pipeline_modes import PyPipelineError

    payload = {"schema": wire._PY_AST_WIRE_SCHEMA_V2, "files": [], "module": None}
    if change == "extra":
        payload["extra"] = 1
    else:
        payload.pop(change.removeprefix("missing_"))
    with pytest.raises(PyPipelineError):
        wire._decode_py_ast_wire(json.dumps(payload))


def test_positional_ast_wire_validates_before_opening_destination(tmp_path):
    from pcc.frontends.python import pipeline_ast_wire as wire, py_ast as ast
    from pcc.frontends.python.pipeline_modes import PyPipelineError

    path = tmp_path / "ast.json"
    path.write_bytes(b"prior complete sidecar")
    with pytest.raises(PyPipelineError):
        wire._write_py_ast_wire_v2(str(path), ast.SourceSpan("f", "bad", 0, 0, 0))
    assert path.read_bytes() == b"prior complete sidecar"


@pytest.mark.parametrize("checkpoint", [False, True])
def test_ordinary_owned_export_selects_private_ast_wire_but_checkpoint_keeps_v1(monkeypatch, checkpoint):
    from pcc.frontends.python import pipeline, pipeline_ast_wire as wire

    if checkpoint:
        monkeypatch.setenv("PCC_STAGE1_CHECKPOINT_STAGE", "1")
    else:
        monkeypatch.delenv("PCC_STAGE1_CHECKPOINT_STAGE", raising=False)
    captured = {}

    def worker(manifest, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(pipeline._pipeline_frontend_worker_execution, "run_export_worker", worker)
    assert pipeline._run_python_multi_export_worker({}) == 0
    chosen = wire._write_py_ast_wire if checkpoint else wire._write_py_ast_wire_v2
    assert captured["write_ast_wire"] is chosen
    assert pipeline._write_py_ast_wire is wire._write_py_ast_wire


def test_positional_ast_wire_layout_is_version_pinned():
    import hashlib
    from pcc.frontends.python.py_ast_contract import PY_AST_FIELD_NAME_OVERRIDES

    # Any positional field-order change requires a new wire schema version.
    layout = json.dumps(PY_AST_FIELD_NAME_OVERRIDES, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(layout.encode()).hexdigest() == (
        "32cd8342c47b8d8ea41dacb06cebe7a19fc73e22bba90d8e1be0811c465b5242"
    )


def test_parallel_codegen_v1_and_private_v2_sidecars_emit_identical_ir(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python_multi

    entry = tmp_path / "entry.py"
    helper = tmp_path / "helper.py"
    entry.write_text("from helper import value\ndef main():\n    print(value(3))\n", encoding="utf-8")
    helper.write_text("def value(x: int) -> int:\n    return x + 4\n", encoding="utf-8")
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "2")
    monkeypatch.setenv("PCC_PY_FRONTEND_AST_WIRE", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.delenv("PCC_STAGE1_CHECKPOINT_DIR", raising=False)
    outputs = []
    for legacy in (True, False):
        with monkeypatch.context() as patch:
            if legacy:
                patch.setenv("PCC_STAGE1_CHECKPOINT_STAGE", "1")
            else:
                patch.delenv("PCC_STAGE1_CHECKPOINT_STAGE", raising=False)
            path = tmp_path / ("legacy.ll" if legacy else "compact.ll")
            profile = {}
            compile_python_multi(
                [str(entry), str(helper)], str(path),
                module_names=["entry", "helper"], entry_module="entry",
                backend="self", emit_llvm_only=True, profile=profile,
            )
            assert profile.get("counters", {}).get("multi_frontend_ast_wire_enabled") == 1
            outputs.append(path.read_bytes())
    assert outputs[0] == outputs[1]


def test_compact_native_reader_contract_model(monkeypatch):
    """Model the native immutable-byte ABI, not the public host dispatch."""
    import struct
    import sys
    from types import SimpleNamespace
    from pcc.frontends.python import pipeline_ast_wire as wire, py_ast

    def document(value, files=()):
        return json.dumps({"schema": wire._PY_AST_WIRE_SCHEMA_V2,
                           "files": list(files), "module": value})

    # Hand-encoded canonical tape of IntType("int", 64, True).
    text = document({"n": ["IntType", "int", 64, True]})
    values = ['"IntType"', '"int"', '64', 'true']
    records = [(0, 1), (1 << 29, 6)]
    for kind, token in zip((3, 3, 4, 5), values):
        start = text.index(token)
        records.append(((kind << 29) | start, start + len(token)))
    tape = struct.pack("<8I", 844383056, 1, len(text), 6, 0, 1, 0, 0)
    tape += b"".join(struct.pack("<II", *r) for r in records)
    expected = wire._decode_py_ast_wire(text)
    monkeypatch.setattr(wire, "sys", SimpleNamespace(implementation=SimpleNamespace(name="pcc")))
    monkeypatch.setattr(wire, "_owned_ast_tape_scan", lambda value: tape)
    monkeypatch.setattr(wire, "_owned_ast_tape_u32",
                        lambda data, offset: struct.unpack_from("<I", data, offset)[0])
    monkeypatch.setattr(wire, "_owned_ast_tape_record",
                        lambda data, index: struct.unpack_from("<q", data, 32+8*index)[0])
    monkeypatch.setattr(wire, "_owned_ast_tape_text_len", len)
    monkeypatch.setattr(wire, "_owned_ast_tape_token",
                        lambda value, start, end, kind: json.loads(value[start:end]))
    def unexpected_fallback(*args):
        raise AssertionError("validated canonical tape must not fall back")
    monkeypatch.setattr(wire, "_decode_py_ast_wire_legacy", unexpected_fallback)
    assert wire._decode_py_ast_wire(text) == expected
    def broken_constructor(*args):
        raise RuntimeError("constructor fault")
    monkeypatch.setattr(py_ast, "IntType", broken_constructor)
    with pytest.raises(RuntimeError, match="constructor fault"):
        wire._decode_py_ast_wire(text)


@pytest.mark.parametrize("kind", sorted(PY_AST_FIELD_NAME_OVERRIDES))
@pytest.mark.parametrize("null_fields", [False, True])
def test_validated_positional_constructor_matches_named_field_contract(kind, null_fields):
    from pcc.frontends.python import pipeline_ast_wire as wire
    from pcc.frontends.python.pipeline_modes import PyPipelineError

    names = wire._PY_AST_FIELD_NAME_OVERRIDES[kind]
    values = [None if null_fields else "field-" + str(i) for i in range(len(names))]
    row = [kind] + values
    named = wire._PyAstV2Fields(row, wire._py_ast_v2_field_positions()[kind])
    if kind in ("Expr", "Stmt"):
        # The schema contains abstract kinds not owned by the constructor.
        # Their existing explicit error, not an inferred dataclass, remains.
        with pytest.raises(PyPipelineError, match="unknown py_ast wire node kind"):
            wire._py_ast_node_from_wire(kind, named)
        with pytest.raises(PyPipelineError, match="unknown py_ast wire node kind"):
            wire._py_ast_node_from_wire(kind, row, True)
    else:
        assert wire._py_ast_node_from_wire(kind, row, True) == wire._py_ast_node_from_wire(kind, named)


def test_v2_positional_rows_are_used_only_for_canonical_layout(monkeypatch):
    from pcc.frontends.python import pipeline_ast_wire as wire, py_ast

    original = wire._py_ast_node_from_wire
    calls = []
    def observed(kind, fields, positional=False):
        calls.append((kind, type(fields), positional))
        return original(kind, fields, positional)
    monkeypatch.setattr(wire, "_py_ast_node_from_wire", observed)
    node = py_ast.Name(None, py_ast.DynType("dyn"), "identifier")
    text = json.dumps(wire._py_ast_v2_payload(node))
    assert wire._decode_py_ast_wire(text) == node
    assert calls and all(kind is list and positional for _, kind, positional in calls)

    calls.clear()
    legacy = wire._py_ast_to_wire(node)
    assert wire._py_ast_from_wire(legacy) == node
    assert calls and all(kind is dict and not positional for _, kind, positional in calls)

    calls.clear()
    schema = wire._PY_AST_FIELD_NAME_OVERRIDES
    monkeypatch.setitem(schema, "Name", ("span", "ident", "ty"))
    assert wire._decode_py_ast_wire(json.dumps(wire._py_ast_v2_payload(node))) == node
    assert calls and all(kind is wire._PyAstV2Fields and not positional for _, kind, positional in calls)


def test_positional_validation_preserves_child_error_before_constructor(monkeypatch):
    from pcc.frontends.python import pipeline_ast_wire as wire
    from pcc.frontends.python.pipeline_modes import PyPipelineError

    def should_not_construct(*args):
        pytest.fail("constructor entered before invalid nested field was rejected")
    monkeypatch.setattr(wire, "_py_ast_node_from_wire", should_not_construct)
    text = json.dumps({"schema": wire._PY_AST_WIRE_SCHEMA_V2, "files": [],
                       "module": {"n": ["Name", None, None, {"unknown": 1}]}})
    with pytest.raises(PyPipelineError, match="unknown positional py_ast wire tag"):
        wire._decode_py_ast_wire(text)


def test_compact_native_generic_row_uses_shared_positional_constructor(monkeypatch):
    """Exercise the ordinary typed generic tape materializer through its ABI model."""
    import struct
    from types import SimpleNamespace
    from pcc.frontends.python import pipeline_ast_wire as wire, py_ast

    text = json.dumps({"schema": wire._PY_AST_WIRE_SCHEMA_V2, "files": [],
                       "module": {"n": ["Name", None, None, "ident"]}})
    records = [(0, 1), (1 << 29, 6)]
    offset = text.index('"n"')
    for kind, token in ((3, '"Name"'), (5, 'null'), (5, 'null'), (3, '"ident"')):
        start = text.index(token, offset)
        records.append(((kind << 29) | start, start + len(token)))
        offset = start + len(token)
    # Each token record carries the authenticated original byte range; this
    # fixture models the already decoded tape ABI, not a native scanner.
    tape = struct.pack("<8I", 844383056, 1, len(text), 6, 0, 1, 0, 0)
    tape += b"".join(struct.pack("<II", *r) for r in records)
    monkeypatch.setattr(wire, "sys", SimpleNamespace(implementation=SimpleNamespace(name="pcc")))
    monkeypatch.setattr(wire, "_owned_ast_tape_scan", lambda value: tape)
    monkeypatch.setattr(wire, "_owned_ast_tape_u32",
                        lambda data, offset: struct.unpack_from("<I", data, offset)[0])
    monkeypatch.setattr(wire, "_owned_ast_tape_record",
                        lambda data, index: struct.unpack_from("<q", data, 32+8*index)[0])
    monkeypatch.setattr(wire, "_owned_ast_tape_text_len", len)
    monkeypatch.setattr(wire, "_owned_ast_tape_token",
                        lambda value, start, end, kind: json.loads(value[start:end]))
    def forbidden_fallback(*args):
        pytest.fail("valid compact generic tape must not fall back")
    monkeypatch.setattr(wire, "_decode_py_ast_wire_legacy", forbidden_fallback)
    original = wire._py_ast_node_from_wire
    calls = []
    def observed(kind, fields, positional=False):
        calls.append((kind, fields, positional))
        return original(kind, fields, positional)
    monkeypatch.setattr(wire, "_py_ast_node_from_wire", observed)
    assert wire._decode_py_ast_wire(text) == py_ast.Name(None, None, "ident")
    assert calls == [("Name", ["Name", None, None, "ident"], True)]
