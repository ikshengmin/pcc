"""Finite negative proof and opaque-input compatibility for import discovery."""

import pytest

from pcc.frontends.python import pipeline_import_scan as scan


_HELPERS = (
    ("_iter_source_importlib_literal_specs", ["importlib.import_module("]),
    ("_iter_source_importlib_resource_literal_specs", [
        "importlib.resources." + name + "(" for name in (
            "files", "contents", "is_resource", "open_binary", "open_text",
            "path", "read_binary", "read_text",
        )
    ]),
)

_NEGATIVE_SOURCES = (
    "",
    "value = 42\n",
    "import os\nfrom pkg import item\n",
    "text = 'importlib'; other = 'importlib_resource'\n",
    "def method():\n    return f'{1 + 2}: {str(4)}'\n",
    "values = [" + ", ".join(repr("ordinary-value-" + str(i)) for i in range(96)) + "]\n",
)


@pytest.mark.parametrize("helper,markers", _HELPERS, ids=["literal", "resource"])
@pytest.mark.parametrize("top_level_only", [False, True], ids=["all", "module"])
@pytest.mark.parametrize("source", _NEGATIVE_SOURCES, ids=["empty", "simple", "imports", "lookalike", "fstring", "many_literals"])
def test_exact_negative_skips_line_and_literal_allocations(monkeypatch, helper, markers, top_level_only, source):
    # The unchanged raw scanner independently proves there are no calls.
    rows = scan._literal_import_call_lines(source, markers, top_level_only=top_level_only)
    assert all(not calls for _line, _active, calls in rows)

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("exact negative must not allocate per-line scan rows")

    monkeypatch.setattr(scan, "_literal_import_call_lines", unexpected_scan)
    assert getattr(scan, helper)(source, top_level_only=top_level_only) == []


@pytest.mark.parametrize("helper,markers", _HELPERS, ids=["literal", "resource"])
@pytest.mark.parametrize("top_level_only", [False, True], ids=["all", "module"])
def test_string_subclass_preserves_first_error_and_callbacks(monkeypatch, helper, markers, top_level_only):
    events = []

    class Source(str):
        def __contains__(self, value):
            events.append(("contains", value))
            raise AssertionError("contains must not precede the old splitlines error")

        def splitlines(self, *args, **kwargs):
            events.append(("splitlines", args, kwargs))
            raise ValueError("original splitlines failure")

    monkeypatch.setattr(scan, "_DISCOVERY_TEXT_CACHE", {})
    with pytest.raises(ValueError, match="^original splitlines failure$"):
        getattr(scan, helper)(Source("value = 1\n"), top_level_only=top_level_only)
    assert events == [("splitlines", (), {})]


@pytest.mark.parametrize("helper,markers", _HELPERS, ids=["literal", "resource"])
def test_custom_truth_retains_original_scanner_route(monkeypatch, helper, markers):
    events = []

    class Truth:
        def __bool__(self):
            events.append("bool")
            raise ValueError("original truth failure")

    # An exact string without the marker must still reach the original truth
    # conversion when top_level_only is not an ordinary bool.
    with pytest.raises(ValueError, match="^original truth failure$"):
        getattr(scan, helper)("value = 1\n", top_level_only=Truth())
    assert events == ["bool"]


@pytest.mark.parametrize("helper,markers", _HELPERS, ids=["literal", "resource"])
def test_non_string_preserves_original_splitlines_error(monkeypatch, helper, markers):
    monkeypatch.setattr(scan, "_DISCOVERY_TEXT_CACHE", {})
    with pytest.raises(AttributeError, match="splitlines"):
        getattr(scan, helper)(object(), top_level_only=True)
