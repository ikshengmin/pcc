"""The scanner must not hand expensive iterable expressions to indexed zip lowering."""

import ast
import inspect

import pytest

from pcc.frontends.python import pipeline_import_scan as scan


@pytest.mark.parametrize("reader", [
    scan._without_inactive_implementation_imports,
    scan._literal_import_call_lines,
])
def test_scanner_zip_sources_are_bound_once_before_loop(reader):
    """The native indexed zip normalizer currently reuses argument ASTs.

    An inline splitlines call would therefore allocate a full line list for
    each indexed row instead of once at Python's iterable-evaluation boundary.
    Keep the two source-owned readers explicit until the generic lowering is
    corrected and qualified independently.
    """
    tree = ast.parse(inspect.getsource(reader))
    loops = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == "zip"
    ]
    assert len(loops) == 1
    assert all(isinstance(arg, ast.Name) for arg in loops[0].iter.args)


def test_implementation_mask_keeps_single_custom_splitlines_callback(monkeypatch):
    text = "import sys\nif sys.implementation.name != 'pcc':\n    import host_only\nimport retained\n"
    calls = []

    class Source(str):
        def splitlines(self, keepends=False):
            calls.append(keepends)
            return super().splitlines(keepends)

    source = Source(text)
    monkeypatch.setattr(scan, "_WITHOUT_IMPLEMENTATION_CACHE", {})
    masked = scan._source_import_discovery_text(text)
    monkeypatch.setattr(scan, "_source_import_discovery_text", lambda value: masked)
    assert scan._without_inactive_implementation_imports(source) == (
        "import sys\nif sys.implementation.name != 'pcc':\n    pass\nimport retained\n"
    )
    assert calls == [True]


def test_literal_call_lines_keeps_single_custom_splitlines_callback(monkeypatch):
    text = "mark('allowed')\n# mark('ignored')\n"
    calls = []

    class Source(str):
        def splitlines(self, keepends=False):
            calls.append(keepends)
            return super().splitlines(keepends)

    source = Source(text)
    masked = scan._source_import_discovery_text(text)
    monkeypatch.setattr(scan, "_source_import_discovery_text", lambda value: masked)
    assert scan._literal_import_call_lines(source, ["mark("], top_level_only=False) == [
        ("mark('allowed')", True, ["'allowed')"]),
        ("# mark('ignored')", True, []),
    ]
    assert calls == [False]
