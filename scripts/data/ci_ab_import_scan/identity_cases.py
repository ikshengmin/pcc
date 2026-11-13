"""Unpublished candidate-only identity acceptance; not common B/C semantics."""

import pytest

from pcc.frontends.python import pipeline_import_scan


@pytest.mark.parametrize("source", [
    "",
    "import ordinary\nvalue = 1\n",
    "import ordinary\r\nvalue = 1",
    "name = '中文'\u2028import ordinary\n",
    "try:\n    action()\nexcept ValueError:\n    import ordinary\n",
], ids=["empty", "multiline", "crlf_no_final_newline", "unicode", "other_handler"])
def test_attribute_error_filter_reuses_text_without_handler_marker(source):
    # The unchanged exact-str arm must retain the existing text instead of
    # allocating one line object per line and another whole source string.
    assert pipeline_import_scan._without_attribute_error_handler_imports(source) is source


@pytest.mark.parametrize("separator", [
    "\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029",
])
def test_attribute_error_prefilter_keeps_all_splitlines_separators(separator):
    source = separator.join(("import ordinary", "text = '中文'", ""))
    assert pipeline_import_scan._without_attribute_error_handler_imports(source) is source

