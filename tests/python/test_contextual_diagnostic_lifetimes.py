"""Bound contextual diagnostic retention without changing fallback counts."""
from dataclasses import fields, is_dataclass
from types import SimpleNamespace
import weakref

import pytest

from pcc.frontends.python import pipeline_context


@pytest.mark.parametrize("separator", [
    "\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029",
])
def test_fallback_counter_preserves_splitlines_boundaries(separator):
    text = separator.join([
        "declare ptr @py_cpy_import(ptr)",
        "call ptr @py_cpy_a(); call ptr @py_cpy_b()",
        "@py_cpy_reversed call ptr elsewhere",
        "call ",
        "@py_cpy_on_another_line",
        "",
    ])
    assert pipeline_context.count_py_cpy_fallback_calls(text) == 2


def test_fallback_counter_does_not_materialize_a_line_list():
    class NoLineList(str):
        def splitlines(self, *args, **kwargs):
            raise AssertionError("whole-IR line materialization")

    text = NoLineList("declare @py_cpy_a\n" * 1000 + "call @py_cpy_b call @py_cpy_c\n")
    assert pipeline_context.count_py_cpy_fallback_calls(text) == 1
    assert pipeline_context.count_py_cpy_fallback_calls("") == 0
    assert pipeline_context.count_py_cpy_fallback_calls("call @py_cpy_a\x1f@py_cpy_b call ") == 1


@pytest.mark.parametrize("first_fails", [False, True])
def test_contextual_loop_releases_completed_codegen(first_fails, tmp_path, monkeypatch):
    from pcc.frontends.python import type_infer
    from pcc.frontends.python.codegen import layer1

    references = []

    class Codegen:
        def __init__(self, module, **kwargs):
            assert all(reference() is None for reference in references)
            self.ast_module = module
            self._native_module_exports = {}
            references.append(weakref.ref(self))

        def generate(self, module):
            if first_fails and module.name == "first":
                raise ValueError("preserved failure")
            return "declare ptr @py_cpy_import(ptr)\n"

    def count_after_release(text):
        assert all(reference() is None for reference in references)
        return 0

    monkeypatch.setattr(layer1, "L1CodeGen", Codegen)
    monkeypatch.setattr(type_infer, "infer_module", lambda module, **kwargs: module)
    monkeypatch.setattr(pipeline_context, "build_closed_world_context", lambda *args, **kwargs: (
        [SimpleNamespace(name="first"), SimpleNamespace(name="second")], {}, {},
    ))
    monkeypatch.setattr(pipeline_context, "_contextual_host_params_for_module", lambda *args: None)
    monkeypatch.setattr(pipeline_context, "count_py_cpy_fallback_calls", count_after_release)
    result = pipeline_context.compile_contextual_per_module_fallback_counts(
        ["first.py", "second.py"], ["first", "second"], ["first", "second"],
        ir_scaffold_mode="on", emit_ir_dir=str(tmp_path),
    )
    assert result == {"first": -1 if first_fails else 0, "second": 0}
    assert all(reference() is None for reference in references)
    if first_fails:
        assert (tmp_path / "first.error.txt").read_text() == "ValueError: preserved failure\n"
    else:
        assert (tmp_path / "first.ll").read_text() == "declare ptr @py_cpy_import(ptr)\n"


def _observe_raw_context_lifetimes(monkeypatch, paths):
    from pcc.frontends.python import py_parse

    original_parse = py_parse.parse
    original_export_scan = pipeline_context.raw_pointer_annotation_names
    references = []
    parsed_paths = []
    exported_modules = []

    def assert_released():
        assert all(reference() is None for reference in references), (
            "completed parser tree remained live"
        )

    def observe_nodes(root):
        pending = [root]
        seen = set()
        while pending:
            node = pending.pop()
            if isinstance(node, (tuple, list)):
                pending.extend(node)
                continue
            if not is_dataclass(node) or type(node).__module__ != py_parse.__name__:
                continue
            if id(node) in seen:
                continue
            seen.add(id(node))
            references.append(weakref.ref(node))
            pending.extend(getattr(node, field.name) for field in fields(node))

    def parse(source, filename="<input>"):
        if filename in paths:
            assert_released()
            parsed_paths.append(filename)
        module = original_parse(source, filename=filename)
        if filename in paths:
            observe_nodes(module)
        return module

    def scan_exports(module):
        assert_released()
        exported_modules.append(module.name)
        return original_export_scan(module)

    monkeypatch.setattr(py_parse, "parse", parse)
    monkeypatch.setattr(pipeline_context, "raw_pointer_annotation_names", scan_exports)
    return references, parsed_paths, exported_modules, assert_released


@pytest.mark.parametrize(
    "lift_indices", [None, [], [0]], ids=["full", "shallow", "mixed"],
)
def test_context_releases_real_parser_nodes_before_exports_and_next_parse(
    lift_indices, tmp_path, monkeypatch,
):
    from pcc.frontends.python import py_ast

    provider = tmp_path / "provider.py"
    provider.write_text(
        '"""Provider documentation."""\n'
        "TOKEN = [1, 2]\n"
        "def make():\n"
        "    return 7\n"
        "def publish(value: int = 3, label: str = 'hello', items = [1, 2], "
        "sentinel = TOKEN, factory = make):\n"
        "    global TOKEN\n"
        "    return value\n"
        "class Base:\n"
        "    def __init__(self, value: int = 4):\n"
        "        self.value: int = value\n"
        "    def method(self, label: str = 'method'):\n"
        "        return label\n"
        "def outer():\n"
        "    value = 3\n"
        "    def inner():\n"
        "        nonlocal value\n"
        "        return value\n"
        "    return inner\n",
        encoding="utf-8",
    )
    consumer = tmp_path / "consumer.py"
    consumer.write_text(
        "from .provider import publish, Base\n"
        "class Child(Base):\n"
        "    pass\n"
        "def caller():\n"
        "    return publish()\n",
        encoding="utf-8",
    )
    paths = [str(provider), str(consumer)]
    names = ["sample.provider", "sample.consumer"]
    expected = pipeline_context.build_closed_world_context(
        paths, names, lift_indices=lift_indices,
    )
    references, parsed_paths, exported_modules, assert_released = (
        _observe_raw_context_lifetimes(monkeypatch, paths)
    )

    result = pipeline_context.build_closed_world_context(
        paths, names, lift_indices=lift_indices,
    )

    assert_released()
    assert len(references) > len(paths)
    assert parsed_paths == paths
    assert exported_modules == names
    assert result == expected
    modules, exports, derived = result
    function = next(
        statement for statement in modules[0].body
        if isinstance(statement, py_ast.FuncDef) and statement.name == "publish"
    )
    published = exports[names[0]]["publish"]
    assert exports[names[1]]["publish"] is published
    assert published["needs_object"] is True
    assert published["box_int_abi"] is True
    assert len(function.args) == len(published["call_sig"]) == 5
    for argument, exported in zip(function.args, published["call_sig"]):
        assert exported["default"] is argument.default
        assert exported["has_default"] is True
    assert function.args[0].default.value == 3
    assert function.args[1].default.value == "hello"
    assert tuple(item.value for item in function.args[2].default.elems) == (1, 2)
    assert published["call_sig"][3]["default_native_global"] == {
        "owning_module": names[0], "name": "TOKEN",
    }
    assert published["call_sig"][4]["default_native_func"] == {
        "owning_module": names[0], "name": "make",
    }
    base = next(
        statement for statement in modules[0].body
        if isinstance(statement, py_ast.ClassDef) and statement.name == "Base"
    )
    method = next(
        statement for statement in base.body
        if isinstance(statement, py_ast.FuncDef) and statement.name == "method"
    )
    method_export = next(
        item for item in exports[names[0]]["Base"]["methods"]
        if item["name"] == "method"
    )
    assert method_export["call_sig"][1]["default"] is method.args[1].default
    assert method.args[1].default.value == "method"
    assert exports[names[0]]["Base"]["field_names"] == ("value",)
    assert exports[names[1]]["Child"]["field_names"] == ("value",)
    assert derived["Base"] == (names[1], "Child")


@pytest.mark.parametrize("failure", ["parse", "lift"])
def test_context_real_frontend_failure_preserves_error_and_stops(
    failure, tmp_path, monkeypatch,
):
    from pcc.frontends.python import py_lift, py_parse

    first = tmp_path / "first.py"
    first.write_text("value = 1\n", encoding="utf-8")
    broken = tmp_path / "broken.py"
    source = (
        "def broken(:\n"
        if failure == "parse" else
        "def broken[T](value: T):\n    return value\n"
    )
    broken.write_text(source, encoding="utf-8")
    never = tmp_path / "never.py"
    never.write_text("value = 3\n", encoding="utf-8")
    paths = [str(first), str(broken), str(never)]
    names = ["first", "broken", "never"]
    if failure == "parse":
        with pytest.raises(py_parse.ParseError) as original_error:
            py_parse.parse(source, filename=str(broken))
        original_message = str(original_error.value)
        expected_message = (
            "parse failed for broken in " + str(broken)
            + ": ParseError: " + original_message
        )
    else:
        raw = py_parse.parse(source, filename=str(broken))
        with pytest.raises(py_lift.LiftError) as original_error:
            py_lift.lift_module(raw, str(broken), "broken")
        expected_message = str(original_error.value)
    _references, parsed_paths, exported_modules, _assert_released = (
        _observe_raw_context_lifetimes(monkeypatch, paths)
    )

    with pytest.raises(py_lift.LiftError) as caught:
        pipeline_context.build_closed_world_context(paths, names)

    assert str(caught.value) == expected_message
    assert caught.value.__cause__ is None
    assert parsed_paths == paths[:2]
    assert exported_modules == names[:1]
    if failure == "parse":
        assert isinstance(caught.value.__context__, py_parse.ParseError)
        assert str(caught.value.__context__) == original_message
    else:
        assert isinstance(caught.value.__context__, py_lift.LiftError)
        assert "PCC-PY-TYPE-PARAMS-UNIMPLEMENTED" in str(caught.value.__context__)


@pytest.mark.parametrize("phase", ["finish", "export"])
def test_context_propagates_phase_failure_after_real_parse_and_lift(
    phase, tmp_path, monkeypatch,
):
    from pcc.frontends.python import py_lift

    source = tmp_path / "provider.py"
    source.write_text("def value(item: int = 3):\n    return item\n", encoding="utf-8")
    paths = [str(source)]
    references, parsed_paths, _exported_modules, assert_released = (
        _observe_raw_context_lifetimes(monkeypatch, paths)
    )
    error = ValueError(phase + " failure after real parse and lift")
    original_finish = py_lift.finish_lifted_module

    def fail_finish(module):
        original_finish(module)
        # The first observed node is the raw module root. Its children may
        # be consumed by lifting; only the root's release order matters here.
        assert references and references[0]() is not None
        raise error

    def fail_export(module):
        assert_released()
        raise error

    if phase == "finish":
        monkeypatch.setattr(py_lift, "finish_lifted_module", fail_finish)
    else:
        monkeypatch.setattr(pipeline_context, "raw_pointer_annotation_names", fail_export)
    with pytest.raises(ValueError) as caught:
        pipeline_context.build_closed_world_context(paths, ["provider"])

    assert caught.value is error
    assert parsed_paths == paths
    assert len(references) > 1
    if phase == "export":
        assert_released()
