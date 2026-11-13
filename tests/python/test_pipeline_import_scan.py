"""Facade and behavior contract for textual pipeline import discovery."""

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_import_scan


def test_pipeline_reexports_import_scanners_by_identity():
    assert (
        pipeline._source_module_scope_lines
        is pipeline_import_scan._source_module_scope_lines
    )
    assert (
        pipeline._iter_source_import_specs
        is pipeline_import_scan._iter_source_import_specs
    )
    assert (
        pipeline._iter_source_import_from_specs
        is pipeline_import_scan._iter_source_import_from_specs
    )
    assert (
        pipeline._iter_source_importlib_literal_specs
        is pipeline_import_scan._iter_source_importlib_literal_specs
    )
    assert (
        pipeline._iter_source_importlib_resource_literal_specs
        is pipeline_import_scan._iter_source_importlib_resource_literal_specs
    )
    assert (
        pipeline._source_import_discovery_text
        is pipeline_import_scan._source_import_discovery_text
    )
    assert (
        pipeline._without_type_checking_imports
        is pipeline_import_scan._without_type_checking_imports
    )


def test_module_scope_scan_keeps_control_flow_and_masks_function_suites():
    source = (
        "if enabled:\n"
        "    import eager\n"
        "def deferred():\n"
        "    import lazy\n"
        "import final\n"
    )
    rows = pipeline_import_scan._source_module_scope_lines(source)
    assert rows == [
        ("if enabled:", True),
        ("    import eager", True),
        ("def deferred():", False),
        ("    import lazy", False),
        ("import final", True),
    ]
    assert pipeline_import_scan._iter_source_import_specs(
        source,
        top_level_only=True,
    ) == ["eager", "final"]


def test_literal_dynamic_resource_and_multiline_from_imports_are_preserved():
    source = (
        "importlib.import_module('pkg.dynamic')\n"
        "importlib.resources.files(\n"
        "    \"pkg.assets\",\n"
        ")\n"
        "from pkg.api import (\n"
        "    first,\n"
        "    second as renamed,\n"
        ")\n"
    )
    assert pipeline_import_scan._iter_source_importlib_literal_specs(
        source,
        top_level_only=True,
    ) == ["pkg.dynamic"]
    assert pipeline_import_scan._iter_source_importlib_resource_literal_specs(
        source,
        top_level_only=True,
    ) == ["pkg.assets"]
    assert pipeline_import_scan._iter_source_import_from_specs(
        source,
        top_level_only=True,
    ) == [("pkg.api", ["first", "second"])]


def test_attribute_error_fallback_imports_remain_in_runtime_source_only():
    source = (
        "try:\n"
        "    modern()\n"
        "except AttributeError:\n"
        "    import legacy\n"
        "import current\n"
    )
    filtered = pipeline_import_scan._without_attribute_error_handler_imports(source)
    assert "import legacy" not in filtered
    assert "import current" in filtered


@pytest.mark.parametrize("source", [
    "",
    "import ordinary\nvalue = 1\n",
    "import ordinary\r\nvalue = 1",
    "name = '中文'\u2028import ordinary\n",
    "try:\n    action()\nexcept ValueError:\n    import ordinary\n",
], ids=["empty", "multiline", "crlf_no_final_newline", "unicode", "other_handler"])
def test_attribute_error_filter_preserves_text_without_handler_marker(source):
    # Common baseline/candidate semantics do not require allocation reuse.
    assert pipeline_import_scan._without_attribute_error_handler_imports(source) == source


@pytest.mark.parametrize("source, expected", [
    (
        "try:\n    action()\nexcept AttributeError as error:\n    from legacy import old\nimport current\n",
        "try:\n    action()\nexcept AttributeError as error:\n\nimport current\n",
    ),
    (
        "try:\r\n\taction()\r\nexcept AttributeError:\r\n\timport legacy\r\nimport current",
        "try:\r\n\taction()\r\nexcept AttributeError:\r\n\nimport current",
    ),
    (
        "text = 'except AttributeError'\nimport ordinary\n",
        "text = 'except AttributeError'\nimport ordinary\n",
    ),
    (
        "try:\n    action()\nexcept AttributeError: # existing scanner boundary\n    import ordinary\n",
        "try:\n    action()\nexcept AttributeError: # existing scanner boundary\n    import ordinary\n",
    ),
], ids=["alias_handler", "tab_crlf", "marker_in_literal", "existing_comment_boundary"])
def test_attribute_error_prefilter_preserves_full_scanner_results(source, expected):
    assert pipeline_import_scan._without_attribute_error_handler_imports(source) == expected


def test_attribute_error_prefilter_preserves_custom_source_callbacks():
    events = []

    class Source(str):
        def __contains__(self, value):
            raise AssertionError("custom source must not run the prefix check")

        def splitlines(self, keepends=False):
            events.append(("splitlines", keepends))
            return ["except AttributeError:\n", "    import legacy\n", "import current\n"]

    source = Source("no handler in the underlying text")
    assert pipeline_import_scan._without_attribute_error_handler_imports(source) == (
        "except AttributeError:\n\nimport current\n"
    )
    assert events == [("splitlines", True)]


def test_attribute_error_prefilter_preserves_custom_source_failure():
    class Source(str):
        def splitlines(self, keepends=False):
            assert keepends is True
            raise ValueError("source split failure")

    with pytest.raises(ValueError, match="^source split failure$"):
        pipeline_import_scan._without_attribute_error_handler_imports(Source("import ordinary\n"))


@pytest.mark.parametrize("separator", [
    "\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029",
])
def test_attribute_error_prefilter_keeps_all_splitlines_separators(separator):
    source = separator.join(("import ordinary", "text = '中文'", ""))
    assert pipeline_import_scan._without_attribute_error_handler_imports(source) == source

    # Preserve the full scanner's existing newline replacement policy too.
    source = separator.join((
        "except AttributeError:", "    import legacy", "import current", "",
    ))
    replacement = "\n" if separator.endswith("\n") else ""
    expected = "except AttributeError:" + separator + replacement + "import current" + separator
    assert pipeline_import_scan._without_attribute_error_handler_imports(source) == expected


def test_attribute_error_prefilter_keeps_duck_typed_source_callbacks():
    events = []

    class Source:
        def __contains__(self, item):
            raise AssertionError("only exact strings may use the prefix check")

        def splitlines(self, keepends=False):
            events.append(keepends)
            return ["except AttributeError:\n", "    import legacy\n", "import current"]

    assert pipeline_import_scan._without_attribute_error_handler_imports(Source()) == (
        "except AttributeError:\n\nimport current"
    )
    assert events == [True]


def test_attribute_error_prefilter_keeps_missing_splitlines_failure():
    with pytest.raises(AttributeError, match="splitlines"):
        pipeline_import_scan._without_attribute_error_handler_imports(object())


def test_attribute_error_filter_preserves_duck_typed_source_failure():
    events = []
    failure = ValueError("duck source split failure")

    class Source:
        def __contains__(self, item):
            raise AssertionError("custom containment must not run")

        def splitlines(self, keepends=False):
            events.append(keepends)
            raise failure

    with pytest.raises(ValueError) as caught:
        pipeline_import_scan._without_attribute_error_handler_imports(Source())
    assert caught.value is failure
    assert events == [True]


@pytest.mark.parametrize("top_level_only", [False, True], ids=["all", "module"])
@pytest.mark.parametrize("caller", ["top_level", "package"])
@pytest.mark.parametrize("has_handler", [False, True], ids=["absent", "present"])
def test_attribute_error_filter_preserves_real_discovery_targets(
    tmp_path, top_level_only, caller, has_handler,
):
    from pcc.frontends.python import pipeline_dependency_closure as closure

    assert closure._without_attribute_error_handler_imports is (
        pipeline_import_scan._without_attribute_error_handler_imports
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("current", "legacy", "lazy"):
        (package / (name + ".py")).write_text("VALUE = 42\n", encoding="utf-8")
    prefix = "pkg." if caller == "top_level" else "."
    source = "from " + prefix + "current import VALUE\n"
    if has_handler:
        source += (
            "try:\n    modern()\nexcept AttributeError:\n"
            + "    from " + prefix + "legacy import VALUE\n"
        )
    source += "def deferred():\n    from " + prefix + "lazy import VALUE\n"
    entry = package / "entry.py"
    entry.write_text(source, encoding="utf-8")
    original = {path: path.read_bytes() for path in package.iterdir()}
    if caller == "top_level":
        targets = closure._top_level_import_targets(
            str(tmp_path), source, top_level_only=top_level_only,
        )
        expected = {"pkg", "pkg.current"}
    else:
        targets = closure._package_import_targets(
            str(entry), "pkg.entry", root_dir=str(tmp_path),
            top_level_only=top_level_only,
        )
        expected = {"pkg.current"}
    if not top_level_only:
        expected.add("pkg.lazy")
    assert {name for _path, name in targets} == expected
    expected_paths = {
        str(package / ("__init__.py" if name == "pkg" else name.split(".")[-1] + ".py"))
        for name in expected
    }
    assert {path for path, _name in targets} == expected_paths
    # Discovery alone never rewrites the program consumed by code generation.
    assert {path: path.read_bytes() for path in package.iterdir()} == original


def test_discovery_masks_strings_comments_and_type_checking_suites():
    source = (
        "import typing as t\n"
        "message = 'import fictional'  # import ignored\n"
        "if t.TYPE_CHECKING:\n"
        "    import annotations_only\n"
        "import runtime_module\n"
    )
    masked = pipeline_import_scan._source_import_discovery_text(source)
    assert "fictional" not in masked
    assert "import ignored" not in masked

    runtime_source = pipeline_import_scan._without_type_checking_imports(source)
    assert "import annotations_only" not in runtime_source
    assert "import runtime_module" in runtime_source


def test_literal_calls_ignore_inline_strings_and_comments_without_losing_calls():
    source = (
        'text = "importlib.import_module(\'phantom\')"; '
        "importlib.import_module('actual')\n"
        'text2 = "importlib.resources.files(\'phantom_resource\')"; '
        "importlib.resources.files('actual_resource')\n"
        "# importlib.import_module('comment')\n"
        "importlib.import_module(\"importlib.resources.files('nested_phantom')\")\n"
        "def deferred():\n"
        "    importlib.import_module('lazy')\n"
    )
    assert pipeline_import_scan._iter_source_importlib_literal_specs(
        source, top_level_only=True,
    ) == ["actual", "importlib.resources.files('nested_phantom')"]
    assert pipeline_import_scan._iter_source_importlib_resource_literal_specs(
        source, top_level_only=True,
    ) == ["actual_resource"]
    assert pipeline_import_scan._iter_source_importlib_literal_specs(
        source, top_level_only=False,
    ) == ["actual", "importlib.resources.files('nested_phantom')", "lazy"]


def test_literal_discovery_keeps_fstring_expression_calls_only():
    source = (
        'value = f"importlib.import_module(\'phantom\') '
        '{importlib.import_module(\'actual\').VALUE}"\n'
        'resource = f"{importlib.resources.files(\'actual_resource\')}"\n'
        'escaped = f"{{importlib.import_module(\'escaped_phantom\')}}"\n'
        'formatted = f"{42:importlib.import_module(\'format_phantom\')}"\n'
        'def deferred():\n'
        '    return f"{importlib.import_module(\'lazy\').VALUE}"\n'
    )
    assert pipeline_import_scan._iter_source_importlib_literal_specs(
        source, top_level_only=True,
    ) == ["actual"]
    assert pipeline_import_scan._iter_source_importlib_resource_literal_specs(
        source, top_level_only=True,
    ) == ["actual_resource"]
    assert pipeline_import_scan._iter_source_importlib_literal_specs(
        source, top_level_only=False,
    ) == ["actual", "lazy"]
