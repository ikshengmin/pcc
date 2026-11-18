"""Go's postfix slice grammar and proven, non-allocating byte-string views."""
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go
from pcc.frontends.go.syntax import GoParser
from tests.go.owned_execution import Execution, compile_owned, run, write_artifact
from tests.go.test_go_enry_tokenizer_project import DEFAULT_BUILD, load_inputs
from tests.go.test_execution import forbid_external_compiler


def test_parse_complete_pinned_go_enry_default_files_without_rewriting():
    _lock, files = load_inputs()
    for names in DEFAULT_BUILD.values():
        for name in names:
            program = GoParser().parse(files[name].decode("utf-8"), Path(name).name, require_main=False)
            assert program.functions or program.constants
    # This is parser coverage only. The separate unchanged project gate
    # still requires typed analysis, all package providers and emitted IR.


@pytest.mark.parametrize(("expression","message"), [
    ('"abc"[1:0]', "ascending order"),
    ('"abc"[:4]', "out of bounds"),
    ('"abc"[-1:2]', "nonnegative"),
    ('"abc"[:1.2]', "integer"),
    ('"abc"[true:]', "integer"),
    ('"abc"[:18446744073709551616]', "representable"),
    ('"abc"[0:1:3]', "three-index slicing is not valid for strings"),
    ('"abc"[::3]', "requires both high and maximum"),
    ('"abc"[:1:]', "requires both high and maximum"),
    ('"abc"[0,1:2]', "expected"),
    ('"abc"[0:1,2]', "expected"),
    ('int(1)[:]', "cannot slice"),
    ('[2]int{1,2}[:]', "unaddressable array"),
    ('[2]int{1,2}[0:1:2]', "unaddressable array"),
])
def test_slice_bounds_and_grammar_remain_strict(expression,message):
    with pytest.raises(GoDiagnostic, match=message):
        compile_go('package main; func main(){_='+expression+'}')


@pytest.mark.parametrize(("declarations","code"), [
    ('func F() string { return ("abc"[:1])[2:] }', 'go.unsupported.string-bounds-panic'),
    ('func F() string { return ("abc"[:1])[:2] }', 'go.unsupported.string-bounds-panic'),
    ('func F(s string, i int) string { return s[:i] }', 'go.unsupported.string-bounds-panic'),
    ('func F(i uint8) string { return "abc"[2:i & 3] }', 'go.unsupported.string-bounds-panic'),
    ('func F(){var a [3]int; _=a[0:1:3]}', 'go.unsupported.slice-storage'),
    ('func F(){var a [3]int; _=a[:]}', 'go.unsupported.slice-storage'),
    ('func F(){var a [3]int; F(a...)}', 'go.unsupported.slice-storage'),
])
def test_unproven_or_unowned_slices_fail_closed(declarations,code):
    with pytest.raises(GoDiagnostic) as exc:
        compile_go('package main;'+declarations+';func main(){}')
    assert exc.value.code == code


def test_even_a_literal_string_slice_is_not_a_go_constant():
    with pytest.raises(GoDiagnostic,match="constant"):
        compile_go('package main;const C="abc"[1:];func main(){}')


NATIVE = r'''package main
var calls int
type Word string
func makeWord() Word { calls++; return "first" }
func checked(s Word) Word { return s[:] }
func masked(i uint8) string { return "abcd"[i & 3:3] }
func prefix(i uint8) string { return "abcd"[:i & 3] }
func Result() int {
    if checked(makeWord()) != "first" { return 1 }
    if makeWord()[:0] != "" { return 2 }
    if makeWord()[0:] != "first" { return 3 }
    if "é"[1:2] != "\xa9" { return 4 }
    if "\x00\xffz"[0:2] != "\x00\xff" { return 5 }
    if "abcd"[1:][1:2] != "c" {return 6}
    if prefix(6) != "ab" || masked(5) != "bc" { return 7 }
    if calls != 3 { return 8 }
    return 42
}
func main() {}
'''


@pytest.mark.integration
def test_owned_native_slices_preserve_raw_bytes_descriptor_and_operand_evaluation(tmp_path,monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((("slices.go",NATIVE),), observe_result=True)
    binary = tmp_path/"slice-native"
    write_artifact(artifact,binary)
    assert run(binary) == Execution(0,(42).to_bytes(8,"little"),b"")
