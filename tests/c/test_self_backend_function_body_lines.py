from __future__ import annotations

import ast
import inspect
import textwrap
import weakref

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_parse as parser
from pcc.backend.self_backend_parse import (
    _iter_function_defs,
    parse_self_backend_module,
)


_TWO_FUNCTION_IR = textwrap.dedent(
    """
    target triple = "arm64-apple-macosx13.0.0"

    define i64 @first(
        i64 %value
    ) {
    entry:

      ; retained comment line
      %next = add i64 %value, 1
      ret i64 %next
    }

    define void @second() {
    entry:
      ret void
    }
    """
)


def test_function_definition_scan_preserves_multiline_body_text() -> None:
    definitions = _iter_function_defs(_TWO_FUNCTION_IR)

    assert len(definitions) == 2
    first_header, first_body = definitions[0]
    second_header, second_body = definitions[1]
    assert first_header.startswith("define i64 @first(")
    assert second_header == "define void @second() {"
    assert isinstance(first_body, str)
    assert isinstance(second_body, str)
    assert first_body.splitlines() == [
        "entry:",
        "",
        "  ; retained comment line",
        "  %next = add i64 %value, 1",
        "  ret i64 %next",
    ]
    assert second_body.splitlines() == ["entry:", "  ret void"]


def test_function_body_handoff_filters_empty_and_comment_lines() -> None:
    module = parse_self_backend_module(_TWO_FUNCTION_IR)
    first = module.functions[0]
    kernel = first.indexed_kernel

    assert kernel.block_names == ["entry"]
    assert kernel.block_fact(0).second == 1


def test_transferred_body_lines_preserve_parser_output() -> None:
    module = parse_self_backend_module(_TWO_FUNCTION_IR)

    assert [function.name for function in module.functions] == ["first", "second"]
    assert [len(function.indexed_kernel.block_names) for function in module.functions] == [
        1,
        1,
    ]


def test_function_definition_scan_keeps_unterminated_diagnostic() -> None:
    with pytest.raises(BackendUnavailable, match="unterminated function body"):
        _iter_function_defs("define void @broken() {\nentry:\n  ret void\n")


def _restore_retained_parse_inputs(tree):
    """Restore only the old private-list traversal and local seed lifetime."""
    function, = tree.body
    assert isinstance(function, ast.FunctionDef) and function.name == "_parse_functions"
    starts = [index for index, node in enumerate(function.body)
              if isinstance(node, ast.Assign) and ast.unparse(node) == "definitions = _iter_function_defs(ir_text)"]
    assert len(starts) == 1
    index = starts[0]
    scan, reverse, loop = function.body[index:index + 3]
    assert ast.unparse(reverse) == "definitions.reverse()"
    assert isinstance(loop, ast.While) and ast.unparse(loop.test) == "definitions" and not loop.orelse
    take = loop.body[0]
    assert ast.unparse(take) == "header_text, body_text = definitions.pop()"
    releases = {"indexed_seed = None", "header_text = ''", "body_text = ''"}
    assert sum(ast.unparse(node) in releases for node in loop.body) == 3
    body = [node for node in loop.body[1:] if ast.unparse(node) not in releases]
    function.body[index:index + 3] = [ast.For(
        target=take.targets[0], iter=scan.value, body=body, orelse=[], type_comment=None,
    )]
    return ast.fix_missing_locations(tree)


def _retaining_parse_functions():
    tree = _restore_retained_parse_inputs(ast.parse(inspect.getsource(parser._parse_functions)))
    namespace = dict(vars(parser))
    exec(compile(tree, "<original-parser-input-lifetimes>", "exec"), namespace)
    return namespace["_parse_functions"]


_LIFETIME_IR = (
    _TWO_FUNCTION_IR,
    'target triple = "arm64-apple-macosx13.0.0"\n'
    'define i32 @first() { ret i32 42 }\ndefine void @last() { ret void }\n',
    'target triple = "arm64-apple-macosx13.0.0"\n'
    'define i64 @"quoted.name"(\n i64 %"value"\n) {\nentry:\n ret i64 %"value"\n}\n',
    'target triple = "arm64-apple-macosx13.0.0"\n'
    '@slot = global i64 9\ndeclare i64 @callee(i64)\n'
    'define i64 @first(i1 %cond) {\nentry:\n br i1 %cond, label %yes, label %no\n'
    'yes:\n %value = load i64, ptr @slot\n br label %join\n'
    'no:\n br label %join\njoin:\n %selected = phi i64 [ %value, %yes ], [ 2, %no ]\n'
    ' %result = call i64 @callee(i64 %selected)\n ret i64 %result\n}\n'
    'define void @last() {\nentry:\n ret void\n}\n',
)


@pytest.mark.parametrize("text", _LIFETIME_IR, ids=["multiline", "single-line", "quoted", "cfg-call-global"])
def test_retired_parser_inputs_preserve_verified_indexed_bytes(tmp_path, monkeypatch, text):
    from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
    from pcc.backend.self_backend_verify import verify_parsed_module

    original = _retaining_parse_functions()
    actual = parse_self_backend_module(text)
    verify_parsed_module(actual)
    actual_path = tmp_path / "actual.pidx"
    encode_indexed_module_file(str(actual_path), actual)
    with monkeypatch.context() as control:
        control.setattr(parser, "_parse_functions", original)
        expected = parse_self_backend_module(text)
        verify_parsed_module(expected)
    expected_path = tmp_path / "expected.pidx"
    encode_indexed_module_file(str(expected_path), expected)
    assert [function.name for function in actual.functions] == [function.name for function in expected.functions]
    assert actual_path.read_bytes() == expected_path.read_bytes()


@pytest.mark.parametrize("text,message", [
    ('define i32 @"bad-name"() { ret i32 1 }\ndefine void @late() {\nentry:\n ret void\n',
     "unterminated function body"),
    ('define i32 @"bad-first"() { ret i32 1 }\ndefine void @"bad-second"() { ret void }\n',
     "bad-first"),
    ('define i32 @first() {\nentry:\n %bad = unsupported i32 1\n ret i32 %bad\n}\n',
     "self backend"),
], ids=["complete-scan-first", "symbol-order", "instruction-failure"])
def test_retired_parser_inputs_preserve_failure_type_and_priority(monkeypatch, text, message):
    text = 'target triple = "arm64-apple-macosx13.0.0"\n' + text
    original = _retaining_parse_functions()
    with pytest.raises(BackendUnavailable, match=message) as actual:
        parse_self_backend_module(text)
    with monkeypatch.context() as control:
        control.setattr(parser, "_parse_functions", original)
        with pytest.raises(BackendUnavailable) as expected:
            parse_self_backend_module(text)
    assert type(actual.value) is type(expected.value)
    assert str(actual.value) == str(expected.value)


def test_parser_retires_consumed_definitions_and_frozen_seed(monkeypatch):
    scan, parse_blocks = parser._iter_function_defs, parser._parse_blocks
    definitions, seeds, visits = [], [], []

    class TrackedSeed(parser.IndexedFunctionSeed):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seeds.append(weakref.ref(self))

    def scanned(text):
        result = scan(text)
        definitions.append(result)
        return result

    def blocks(name, *args, **kwargs):
        if visits:
            assert seeds[0]() is None
        visits.append(name)
        assert len(definitions[0]) == 2 - len(visits)
        return parse_blocks(name, *args, **kwargs)

    monkeypatch.setattr(parser, "IndexedFunctionSeed", TrackedSeed)
    monkeypatch.setattr(parser, "_iter_function_defs", scanned)
    monkeypatch.setattr(parser, "_parse_blocks", blocks)
    module = parse_self_backend_module(_TWO_FUNCTION_IR)
    assert visits == ["first", "second"] and definitions == [[]]
    assert len(seeds) == 2 and all(reference() is None for reference in seeds)
    assert all(function.indexed_seed is None for function in module.functions)
    assert all(function.indexed_kernel.indexed_call_plane is None for function in module.functions)
    # The first kernel remains readable after retiring both construction seeds.
    first, second = (function.indexed_kernel for function in module.functions)
    assert first.block_names == second.block_names == ["entry"]
    assert first.block_fact(0).second == 1 and second.block_fact(0).second == 0
