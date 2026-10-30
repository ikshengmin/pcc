"""Exact IR text/formatting contracts; host helper and source-extracted branch."""
import ast
import copy
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline_targets as targets


def _reference(records):
    return str("\n\n".join(f"; ---- module: {name} ----\n{text}" for name, text in records))


@pytest.mark.parametrize("records", [
    [], (), [("one", "")], [("a", "alpha\n"), ("b", "beta")],
    [("模块", "λ😀\n"), ("é", "")], [("\0\n", "\r\n\0")],
    [("a", "shared payload"), ("b", "shared payload")],
])
def test_ordinary_output_bytes_and_module_order(records):
    assert targets._ordinary_module_ir_batch(records) is True
    actual = targets.combine_module_ir_texts(records)
    assert type(actual) is str
    assert actual.encode("utf-8") == _reference(records).encode("utf-8")


def _case(kind):
    events = []
    records = [("first", "payload"), ("second", "tail")]

    class Formatter:
        def __init__(self, label, value):
            self.label = label
            self.value = value

        def __format__(self, specification):
            events.append(("format", self.label, specification))
            if kind == "error_" + self.label:
                raise LookupError(self.label, "format failed")
            if kind == "mutate_" + self.label:
                records.append(("appended", "late payload"))
            return self.value

    class Text(str):
        def __format__(self, specification):
            events.append(("subclass.format", specification))
            return "formatted:" + str(self)

    class Records(list):
        def __iter__(self):
            events.append("records.iter")
            return super().__iter__()

    class Pair(tuple):
        def __iter__(self):
            events.append("pair.iter")
            return super().__iter__()

    if kind in ("name", "both", "error_name", "mutate_name"):
        records[1] = (Formatter("name", "formatted module"), records[1][1])
    if kind in ("text", "both", "error_text", "mutate_text"):
        records[1] = (records[1][0], Formatter("text", "formatted payload"))
    if kind == "text_subclass":
        records[1] = ("second", Text("payload"))
    elif kind == "pair_subclass":
        records[1] = Pair(records[1])
    elif kind == "pair_list":
        records[1] = list(records[1])
    elif kind == "record_one":
        records[1] = ("one",)
    elif kind == "record_three":
        records[1] = ("one", "two", "three")
    elif kind == "records_subclass":
        records = Records(records)
    if kind in ("generator", "error_iterator"):
        source = records

        def generate():
            events.append("generator.begin")
            yield source[0]
            events.append("generator.next")
            if kind == "error_iterator":
                raise RuntimeError("iterator failed")
            yield source[1]
            events.append("generator.end")

        records = generate()
    return records, events


def _observe(function, kind):
    records, events = _case(kind)
    try:
        outcome = ("return", function(records))
    except Exception as error:
        outcome = ("error", type(error), error.args)
    return outcome, events


@pytest.mark.parametrize("kind", ["name", "text", "both", "text_subclass", "pair_subclass",
                                  "pair_list", "records_subclass", "generator"])
def test_opaque_inputs_decline_before_callbacks_and_keep_original_formatting(kind):
    records, events = _case(kind)
    assert targets._ordinary_module_ir_batch(records) is False
    assert events == []
    assert _observe(targets.combine_module_ir_texts, kind) == _observe(_reference, kind)


@pytest.mark.parametrize("kind", ["error_name", "error_text", "error_iterator", "record_one", "record_three"])
def test_original_exception_type_args_and_callback_order(kind):
    assert _observe(targets.combine_module_ir_texts, kind) == _observe(_reference, kind)


@pytest.mark.parametrize("kind", ["mutate_name", "mutate_text"])
def test_format_callback_mutation_is_seen_by_original_single_iteration(kind):
    assert _observe(targets.combine_module_ir_texts, kind) == _observe(_reference, kind)


@pytest.fixture(scope="module")
def emit_branch_codes():
    # Compile only the real emit-only branch with injected file/profile hooks.
    # No compiler invocation or native/backend execution occurs in this test.
    source = Path(targets.__file__).with_name("pipeline.py").read_text()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "compile_python_multi")
    branches = [node for node in function.body if isinstance(node, ast.If)
                and isinstance(node.test, ast.Name) and node.test.id == "emit_llvm_only"
                and any(isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name)
                        and item.targets[0].id == "combined" for item in node.body)]
    assert len(branches) == 1
    assignment = branches[0].body[0]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Call)
    assert isinstance(assignment.value.func, ast.Attribute)
    assert assignment.value.func.attr == "combine_module_ir_texts"
    result = {}
    for name in ("candidate", "reference"):
        body = copy.deepcopy(branches[0].body)
        if name == "reference":
            body[0].value = ast.parse(
                'str("\\n\\n".join(f"; ---- module: {name} ----\\n{text}" for name, text in module_ir_texts))',
                mode="eval",
            ).body
        wrapper = ast.FunctionDef(
            name="emit", args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=arg) for arg in
                ("module_ir_texts", "out_path", "verbose", "profile", "total_start")],
                vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
            body=body, decorator_list=[], returns=None, type_comment=None, type_params=[])
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        result[name] = compile(module, "<actual-emit-only-branch-" + name + ">", "exec", dont_inherit=True)
    return result


@pytest.mark.parametrize("failure", ["none", "error_name", "error_text", "path", "logger", "writer"])
def test_actual_emit_only_branch_preserves_single_write_and_error_order(emit_branch_codes, failure):
    def observe(code):
        records, events = _case(failure)

        class OutputPath:
            def __str__(self):
                events.append("path.str")
                if failure == "path":
                    raise ValueError("path failed")
                return "output.ll"

        def log(verbose, message):
            events.append(("log", verbose, message))
            if failure == "logger":
                raise RuntimeError("logger failed")

        def write(path, text):
            events.append(("write", path, text))
            if failure == "writer":
                raise OSError("write failed")

        def begin(profile):
            events.append("profile.begin")
            return 11

        def end(profile, phase, start):
            events.append(("profile.end", phase, start))

        namespace = {"_pipeline_targets": targets, "_log": log, "_write_utf8_text_file": write,
                     "_profile_begin": begin, "_profile_end": end}
        exec(code, namespace)
        try:
            result = ("return", namespace["emit"](records, OutputPath(), True, None, 7))
        except Exception as error:
            result = ("error", type(error), error.args)
        return result, events

    assert observe(emit_branch_codes["candidate"]) == observe(emit_branch_codes["reference"])
