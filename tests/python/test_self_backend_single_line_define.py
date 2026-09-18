"""The IR splitter must accept a function that is not laid out line-by-line.

``_iter_function_defs`` assumed the opening brace ends a line and the closing
brace owns one -- the layout every module this backend emits happens to use.
A function written on one line, which is valid IR and what
``tests/test_runtime_archive_consumers.py`` emits
(``define i32 @cache_member() {{ ret i32 42 }}``), left the scanner in its
header state to the end of the file and raised
``BackendUnavailable: self backend saw unterminated function body``.  That took
21 of that file's 23 cases down, at HEAD, for anything that emitted an object
through ``pcc.tools.ir_to_obj``.

Brace depth is counted outside quoted strings, so a body holding a type
literal or a ``c"{}"`` constant is measured rather than guessed at, and only
the line that opens the body is scanned so the common layout keeps its
line-at-a-time cost.
"""

from __future__ import annotations

import pytest

from pcc.backend.self_backend_parse import _iter_function_defs


_SHAPES = [
    ("one line", 'define i32 @f() { ret i32 42 }', 1),
    ("brace ends the line", 'define i32 @g() {\nentry:\n  ret i32 1\n}', 1),
    (
        "both layouts in one module",
        'define i32 @f() { ret i32 42 }\ndefine i32 @g() {\nentry:\n  ret i32 1\n}',
        2,
    ),
    (
        "preceded by module-level text",
        'target triple = "arm64-apple-macosx"\ndefine i32 @f() { ret i32 42 }',
        1,
    ),
    (
        "type literal in a multi-line body",
        'define i32 @h() {\nentry:\n  %s = alloca {i32, i32}\n  ret i32 0\n}',
        1,
    ),
    (
        "body opens mid-line and continues",
        'define i32 @k() { %s = alloca {i32, i32}\n  ret i32 0\n}',
        1,
    ),
    ("header wraps, body on one line", 'define i32 @m(\n  i32 %a) { ret i32 %a }', 1),
    (
        "header wraps and body wraps",
        'define i32 @n(\n  i32 %a) {\nentry:\n  ret i32 %a\n}',
        1,
    ),
    (
        "braces inside a string constant",
        'define i32 @q() { ret i32 0 }\n@s = constant [3 x i8] c"{}\\00"',
        1,
    ),
]


@pytest.mark.parametrize("label,text,count", _SHAPES, ids=[s[0] for s in _SHAPES])
def test_splitter_accepts_every_brace_layout(label, text, count):
    assert len(_iter_function_defs(text)) == count


def test_multi_line_body_text_is_unchanged():
    """The common layout must come through exactly as before."""
    header, body = _iter_function_defs(
        'define i32 @g() {\nentry:\n  ret i32 1\n}'
    )[0]
    assert header == "define i32 @g() {"
    assert body == "entry:\n  ret i32 1"


def test_single_line_body_is_the_text_between_the_braces():
    header, body = _iter_function_defs('define i32 @f() { ret i32 42 }')[0]
    assert header == "define i32 @f() {"
    assert body.strip() == "ret i32 42"


def test_unterminated_body_is_still_rejected():
    from pcc.backend import BackendUnavailable

    with pytest.raises(BackendUnavailable):
        _iter_function_defs("define i32 @f() {\nentry:\n  ret i32 1\n")
