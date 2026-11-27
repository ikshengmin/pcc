"""Exact marker-absent shortcut preserves split/join results and identities."""
import sys

import pytest

from pcc.frontends.python import pipeline_import_scan as scan


SEPARATORS = ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d",
              "\x1e", "\x85", "\u2028", "\u2029")


@pytest.mark.parametrize("sep", SEPARATORS)
def test_marker_absent_exact_split_join_identity(sep):
    # Construct exact str values dynamically rather than relying on literal
    # interning. Preserve original one-line and multi-line join identity.
    for source in (
        "".join(("ordinary", "中文")),
        "".join(("ordinary", "中文", sep)),
        sep.join(("ordinary", "中文")),
        sep.join(("ordinary", "中文", "")),
        sep.join(("", "ordinary", "", "中文", "")),
        sep.join(("ordinary", "\ud800", "")),
    ):
        expected = "".join(source.splitlines(keepends=True))
        result = scan._without_attribute_error_handler_imports(source)
        assert result == expected == source
        assert (result is source) == (expected is source)


def test_marker_absent_skips_original_per_line_strip_calls():
    source = "\n".join("    import ordinary_%d" % i for i in range(128))
    counts = {"strip": 0, "lstrip": 0}
    prior = sys.getprofile()
    def profile(frame, event, arg):
        if event == "c_call" and getattr(arg, "__name__", "") in counts:
            counts[arg.__name__] += 1
    sys.setprofile(profile)
    try:
        actual = scan._without_attribute_error_handler_imports(source)
    finally:
        sys.setprofile(prior)
    expected = "".join(source.splitlines(keepends=True))
    assert actual == expected
    assert (actual is source) == (expected is source)
    assert counts == {"strip": 0, "lstrip": 0}


@pytest.mark.parametrize("source", [
    "except\tAttributeError:\n    import ordinary\n",
    "except  AttributeError:\r\n    import ordinary\r\n",
    "except\u00a0AttributeError:\n    import ordinary\n",
    "except \\\nAttributeError:\n    import ordinary\n",
], ids=["tab", "double-space", "unicode-space", "continued"])
def test_nonliteral_handler_spellings_keep_legacy_text(source):
    # The original scanner only recognizes literal startswith marker text;
    # this shortcut neither broadens nor narrows that existing boundary.
    assert "except AttributeError" not in source
    assert scan._without_attribute_error_handler_imports(source) == source


def test_literal_handler_prefix_variants_remain_original():
    for header in ("except AttributeError:", "except AttributeError as e:",
                   "except AttributeErrorProxy:", "  except AttributeError:"):
        indent = "    " if not header.startswith("  ") else "      "
        text = header + "\n" + indent + "import legacy\n" + "import current\n"
        assert scan._without_attribute_error_handler_imports(text) == (
            header + "\n\nimport current\n"
        )
