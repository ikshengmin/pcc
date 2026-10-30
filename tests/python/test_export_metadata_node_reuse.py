"""Finite host-only contracts for adjacent owned export metadata traversal."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_closed_world as cw
from pcc.frontends.python.py_lift import parse_and_lift


NAMES = ["pkg.own", "pkg.own.sub", "pkg.other", "pkg.other.deep", "pcc.ir.ir"]
PATHS = ["/tmp/pkg/own.py"]


def _module(source):
    return parse_and_lift(source, PATHS[0], "pkg.own")


def _exports():
    return {"pkg.own": {name: {"kind": "function", "owning_module": "pkg.own"}
                        for name in ("fn", "sub", "local")}}


def _reference_filtered_mark_nodes(module):
    # Literal original collector order, filtered only after full traversal.
    from pcc.frontends.python.py_ast import Attr, Import, ImportFrom, Type
    pending = [module.body]
    seen = set()
    out = []
    while pending:
        node = pending.pop()
        if node is None or isinstance(node, Type):
            continue
        if isinstance(node, (tuple, list)):
            pending.extend(node)
            continue
        marker = id(node)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(node)
        for field_name in cw._py_ast_field_names(node):
            if field_name in ("annotation", "return_ty", "ty", "span"):
                continue
            pending.append(cw._py_ast_field_value(node, field_name, None))
    return [node for node in out if isinstance(node, (Import, ImportFrom, Attr))]


def _compare(module):
    # Assert exact identities/order, not merely equality of final deduped rows.
    expected_nodes = _reference_filtered_mark_nodes(module)
    node_rows = []
    try:
        cw._closed_world_module_dependencies_with_nodes(
            [module], ["pkg.own"], PATHS, NAMES, node_rows)
        assert len(node_rows) == 1 and len(node_rows[0]) == len(expected_nodes)
        assert all(a is b for a, b in zip(node_rows[0], expected_nodes))
    finally:
        for row in node_rows:
            row.clear()
        node_rows.clear()
    before = _exports()
    # Preserve the first, subset-only context marking pass on both sides.
    cw._mark_closed_world_function_object_exports([module], ["pkg.own"], PATHS, before)
    baseline = deepcopy(before)
    candidate = deepcopy(before)
    expected_dependencies = cw._closed_world_module_dependencies(
        [module], ["pkg.own"], PATHS, NAMES)
    expected_uses = cw._mark_closed_world_function_object_exports(
        [module], ["pkg.own"], PATHS, baseline, known_module_names=NAMES)
    actual = cw._closed_world_owned_export_metadata(
        [module], ["pkg.own"], PATHS, NAMES, candidate)
    assert actual is not None
    assert actual == (expected_dependencies, expected_uses)
    assert candidate == baseline
    return actual, candidate, before


@pytest.mark.parametrize("source", [
    "value = 1\n",
    "import pkg.other as p\nx = p.run\n",
    "from . import other\nx = other.run\n",
    "from pkg.other import *\n",
    "def f():\n    import pkg.other.deep as p\n    return p.run\n",
    "from pcc.ir.compat import ir\n",
    "import pkg.other as p\nimport pkg.other as p\nx = p.deep.run\n",
    "class C:\n    def f(self):\n        from pkg.other import run\n        return run\n",
], ids=["empty", "import", "relative", "star", "nested", "compat", "duplicates", "method"])
def test_owned_metadata_matches_both_original_passes(source):
    _compare(_module(source))
    if source == "value = 1\n":
        # Three bounded structural walker fixtures, not source-program or
        # native-runtime claims. Mutation is fixture construction only.
        from pcc.frontends.python.py_ast import ClassType, FuncType, IntType, Type
        base = _module("import pkg.other as p\nx = p.run\n")
        scalar = IntType("int")
        record = ClassType("Record", "pkg", fields=(("x", scalar),))
        callback = FuncType("callback", (record,), scalar)
        logical_base_type = type("Type", (), {})()
        logical_base_type.name = "plain native-projection name"
        assert not isinstance(logical_base_type, Type)
        assert cw._closed_world_is_node(logical_base_type, Type)
        _compare(replace(base, body=(Type("base"), scalar, record, callback,
                                    logical_base_type) + base.body))
        _compare(replace(base, body=(base.body[0], base.body[0]) + base.body[1:]))
        cyclic = _module("def recursive():\n    import pkg.other as p\n    return p.run\n")
        function = cyclic.body[0]
        object.__setattr__(function, "body", (function,) + function.body)
        _compare(cyclic)


@pytest.mark.parametrize("source, first_name, expected_dependencies", [
    ("from pkg.own import sub\nx = sub.fn\n", "sub", ("pkg.own.sub",)),
    ("import pkg.own as a\nx = a.sub.fn\n", "fn", ()),
], ids=["from_collision", "attr_collision"])
def test_subset_full_identity_collision_keeps_union_and_second_uses(source, first_name, expected_dependencies):
    (dependencies, uses), exports, first = _compare(_module(source))
    assert first["pkg.own"][first_name]["needs_object"] is True
    assert exports["pkg.own"][first_name]["needs_object"] is True
    assert ("pkg.own.sub", "fn") in uses
    assert ("pkg.own", first_name) not in uses
    assert dependencies == (("pkg.own", expected_dependencies),)


@pytest.mark.parametrize("shape", ["multiple", "tuple_names", "list_subclass", "mutable_body"])
def test_uncertain_shape_falls_back_before_nodes(shape):
    module = _module("x = 1\n")
    modules, names, paths, all_names = [module], ["pkg.own"], list(PATHS), list(NAMES)
    if shape == "multiple":
        modules.append(module)
    elif shape == "tuple_names":
        all_names = tuple(all_names)
    elif shape == "list_subclass":
        class BadList(list):
            def __iter__(self):
                raise AssertionError("custom iterator must not be entered")
        all_names = BadList(all_names)
    else:
        modules = [replace(module, body=list(module.body))]
    assert cw._closed_world_owned_export_metadata(modules, names, paths, all_names, _exports()) is None


@pytest.mark.parametrize("name", [
    "_py_ast_field_names", "_py_ast_field_value", "_closed_world_is_node",
    "_resolve_ast_import_from_module", "_package_parts_for_module", "_join_dotted_parts",
])
def test_replaced_reader_falls_back_without_calling_it(monkeypatch, name):
    module = _module("import pkg.other\n")
    def unexpected(*args, **kwargs):
        raise AssertionError("reader must not run before fallback")
    monkeypatch.setattr(cw, name, unexpected)
    assert cw._closed_world_owned_export_metadata([module], ["pkg.own"], PATHS, NAMES, _exports()) is None


@pytest.mark.parametrize("failure", [None, "dependency", "mark"])
def test_extra_node_lists_are_cleared_on_success_and_exception(monkeypatch, failure):
    module = _module("import pkg.other as p\nx = p.run\n")
    held = []
    original_dependencies = cw._closed_world_module_dependencies_with_nodes
    original_mark = cw._mark_closed_world_function_object_exports_with_nodes
    def dependencies(*args):
        rows = args[-1]
        result = original_dependencies(*args)
        held.append(rows)
        held.extend(rows)
        assert rows and rows[0]
        if failure == "dependency":
            raise RuntimeError("dependency sentinel")
        return result
    def mark(*args):
        assert held and held[0]
        if failure == "mark":
            raise RuntimeError("mark sentinel")
        return original_mark(*args)
    monkeypatch.setattr(cw, "_closed_world_module_dependencies_with_nodes", dependencies)
    monkeypatch.setattr(cw, "_mark_closed_world_function_object_exports_with_nodes", mark)
    if failure is None:
        assert cw._closed_world_owned_export_metadata([module], ["pkg.own"], PATHS, NAMES, _exports()) is not None
    else:
        with pytest.raises(RuntimeError, match=failure + " sentinel"):
            cw._closed_world_owned_export_metadata([module], ["pkg.own"], PATHS, NAMES, _exports())
    assert held and all(row == [] for row in held)


@pytest.mark.parametrize("name", [
    "build_closed_world_context", "_write_py_ast_wire", "_closed_world_reexport_edges",
    "_closed_world_module_dependencies", "_mark_closed_world_function_object_exports",
    "_python_frontend_worker_timing_enabled",
])
def test_facade_custom_callback_uses_original_worker_route(monkeypatch, name):
    sentinel = object()
    def custom(*args, **kwargs):
        return sentinel
    captured = []
    def worker(manifest, **kwargs):
        captured.append(kwargs)
        return 0
    monkeypatch.setattr(pipeline, name, custom)
    monkeypatch.setattr(pipeline._pipeline_frontend_worker_execution, "run_export_worker", worker)
    assert pipeline._run_python_multi_export_worker({}) == 0
    assert captured[0]["owned_export_metadata"] is None
    key = {"build_closed_world_context": "build_closed_world_context",
           "_write_py_ast_wire": "write_ast_wire",
           "_closed_world_reexport_edges": "closed_world_reexport_edges",
           "_closed_world_module_dependencies": "closed_world_module_dependencies",
           "_mark_closed_world_function_object_exports": "mark_function_object_exports",
           "_python_frontend_worker_timing_enabled": "worker_timing_enabled"}[name]
    assert captured[0][key] is custom


@pytest.mark.parametrize("source", [
    "from pkg.own import sub\ndef local():\n    return sub.fn\n",
    "import pkg.own as a\ndef fn():\n    return a.sub.fn\n",
], ids=["from_worker", "attr_worker"])
def test_actual_export_worker_preserves_complete_ast_and_metadata_bytes(tmp_path, monkeypatch, source):
    # This calls only the host frontend export worker: parse/lift/metadata/wire.
    # No backend, linker, executable, or runtime provisioning is involved.
    src = tmp_path / "own.py"
    src.write_text(source, encoding="utf-8")
    outputs = []
    original = cw._closed_world_owned_export_metadata
    calls = []
    for side in ("baseline", "candidate"):
        directory = tmp_path / side
        directory.mkdir()
        (directory / "ast").mkdir()
        manifest = {"result_path": str(directory / "result.tsv"), "ir_dir": str(directory),
                    "ast_dir": str(directory / "ast"), "src_paths": [str(src), str(src)],
                    "module_names": ["pkg.own", "pkg.own.sub"], "assigned_indices": [0]}
        with monkeypatch.context() as patch:
            if side == "baseline":
                patch.setattr(cw, "_closed_world_owned_export_metadata", lambda *args: None)
            else:
                def measured(*args):
                    result = original(*args)
                    assert result is not None, "owned path must not silently fall back"
                    calls.append(True)
                    return result
                patch.setattr(cw, "_closed_world_owned_export_metadata", measured)
            assert pipeline._run_python_multi_export_worker(manifest) == 0
        outputs.append(tuple((directory / p).read_bytes() for p in (
            "ast/module_0.json", "exports_result.tsv.json", "reexports_result.tsv.json")))
        row = (directory / "result.tsv").read_text().split("\t")
        assert row[0] == "EXPORT"
        assert Path(row[1]).name == "exports_result.tsv.json"
        assert Path(row[2].strip()).name == "reexports_result.tsv.json"
    assert calls == [True]
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("broken_field", ["level", "names"])
def test_malformed_import_keeps_dependency_first_error(broken_field):
    module = _module("from . import other\n")
    stmt = module.body[0]
    bad = replace(stmt, **{broken_field: "bad" if broken_field == "level" else (("other",),)})
    module = replace(module, body=(bad,))
    errors = []
    for optimized in (False, True):
        with pytest.raises(Exception) as error:
            if optimized:
                cw._closed_world_owned_export_metadata([module], ["pkg.own"], PATHS, NAMES, _exports())
            else:
                cw._closed_world_module_dependencies([module], ["pkg.own"], PATHS, NAMES)
        errors.append((type(error.value), str(error.value)))
    assert errors[0] == errors[1]
