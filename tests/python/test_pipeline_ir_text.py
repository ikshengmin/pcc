"""Pure LLVM text contracts shared by both pipeline split paths."""

from __future__ import annotations

from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_ir_text


def test_private_symbol_map_and_reference_rewrite_preserve_quoted_text():
    functions = [
        ("helper", "define internal void @helper() {", "", True),
        ("public", "define void @public() {", "", False),
    ]
    rename_map = pipeline_ir_text.private_symbol_rename_map(
        ["@state = private global i64 0"],
        functions,
        "__pccsplit_mod_",
    )
    source = 'call void @helper()\n%v = load i64, ptr @state\n@text = "@helper"'

    assert rename_map == {
        "state": "__pccsplit_mod_state",
        "helper": "__pccsplit_mod_helper",
    }
    assert pipeline_ir_text.rename_llvm_global_refs(source, rename_map) == (
        "call void @__pccsplit_mod_helper()\n"
        "%v = load i64, ptr @__pccsplit_mod_state\n"
        '@text = "@helper"'
    )


def test_quote_scan_does_not_search_for_escapes_beyond_the_candidate_quote():
    class BoundedText(str):
        def find(self, sub, start=0, end=None):
            if sub == "\\":
                quote = str.find(self, '"', start)
                assert quote >= 0 and end is not None and end <= quote
            if end is None:
                return str.find(self, sub, start)
            return str.find(self, sub, start, end)

    # Later literals and malformed input must not cause a tail-wide escape search.
    text = BoundedText('"" "short" "q\\"r" @x "unfinished @x')
    assert pipeline_ir_text.rename_llvm_global_refs(text, {"x": "y"}) == (
        '"" "short" "q\\"r" @y "unfinished @x'
    )


def test_quote_scanner_matches_a_character_oracle():
    from itertools import product

    def literal_end(text, opening):
        pos = opening + 1
        while pos < len(text):
            if text[pos] == '"':
                return pos + 1
            if text[pos] == "\\":
                pos += 1
            pos += 1
        return len(text)

    # Every placement of quotes and runs of slashes through length six,
    # with and without a closing suffix; opening is not at index zero.
    for length in range(7):
        for body in product(('x', '"', "\\"), repeat=length):
            for suffix in ("", '"tail'):
                text = 'prefix"' + "".join(body) + suffix
                assert pipeline_ir_text._skip_llvm_quoted(text, 6) == literal_end(text, 6)

    # Quoted global names stay unchanged, even with an odd trailing escape
    # that leaves the quote open; an even run lets the following symbol rename.
    for length in range(7):
        prefix = '@"q@x' + "\\" * length + '" '
        expected = "@x" if length % 2 else "@y"
        assert pipeline_ir_text.rename_llvm_global_refs(prefix + "@x", {"x": "y"}) == (
            prefix + expected
        )


def test_definition_lines_become_external_declarations():
    assert pipeline_ir_text.function_declaration_from_define_line(
        "define internal i64 @work(i64 %x) {"
    ) == "declare i64 @work(i64 %x)"
    assert pipeline_ir_text.global_declaration_from_definition_line(
        "@items = private constant [2 x i8] c\"x\\00\""
    ) == "@items = external constant [2 x i8]"


def test_pipeline_facade_reexports_shared_ir_text_helpers():
    assert (
        pipeline._defined_function_name_from_line
        is pipeline_ir_text.defined_function_name_from_line
    )
    assert (
        pipeline._rename_llvm_global_refs
        is pipeline_ir_text.rename_llvm_global_refs
    )
    assert pipeline._find_substring is pipeline_ir_text.find_substring
