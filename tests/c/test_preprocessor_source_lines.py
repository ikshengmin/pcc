"""Translation phases 1-3 in the owned preprocessor's line scanner (C17 5.1.1.2).

Line splicing removes each original backslash-newline once; a block comment
becomes one space plus its newlines and a line comment one space; literals keep
their contents. Every resulting line keeps the physical line it starts on, which
__LINE__ and diagnostics report.
"""
import pytest

from pcc.frontends.c.preprocessor import _source_line_records, preprocess


def records(source):
    return [(line.text, line.physical_line) for line in _source_line_records(source)]


def test_splices_keep_the_starting_physical_line():
    assert records("a \\\nb\nc\\\n\\\nd\ne") == [("a b", 1), ("cd", 3), ("e", 6)]


def test_splicing_is_a_single_pass():
    # Removing the original backslash-newline leaves a new backslash before a
    # newline; it is not spliced again.
    assert records("a\\\\\n\nb") == [("a\\", 1), ("b", 3)]


def test_comments_keep_line_structure_and_literals_keep_contents():
    source = 'a /* one\ntwo */ b // tail\n"//not" \'/*\' c\r\nd'
    assert records(source) == [("a  ", 1), (" b  ", 2), ('"//not" \'/*\' c', 3), ("d", 4)]


def test_unterminated_block_comment_is_an_error():
    with pytest.raises(RuntimeError, match="unterminated comment"):
        records("int x; /* open")


def test_line_numbers_survive_splices_and_comments():
    source = "/* a\n b */ int first = __LINE__;\nint \\\nsecond = __LINE__;\nint third = __LINE__;\n"
    out = str(preprocess(source, target_triple="x86_64-unknown-linux-gnu"))
    assert "first = 2" in out and "second = 3" in out and "third = 5" in out
