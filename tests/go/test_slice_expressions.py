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
    ('func choose(s string) string { return s };func F(s string, i int) string { return choose(s)[:i] }', 'go.unsupported.string-bounds-panic'),
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


BORROWED_DYNAMIC = r'''package main
var baseCalls int
func offset(n int) int { baseCalls++; return n }
func split(s string, i int) bool { h,t := s[:i], s[i:]; return h == "A\x00\xc3" && t == "\xa9\xffZ" }
func middle(s string, lo uint8, hi int) string { return s[offset(int(lo)):offset(hi)] }
func main() {
    if !split("A\x00é\xffZ", 3) { panic("byte-split") }
    if middle("ABCDE", 1, 3) != "BC" || baseCalls != 2 { panic("order") }
    if middle("", 0, 0) != "" || baseCalls != 4 { panic("empty") }
}
'''


def test_borrowed_dynamic_string_slice_checks_before_pointer_access():
    ir = str(compile_go(BORROWED_DYNAMIC, "borrowed.go"))
    assert "string_slice_check_low" in ir and "string_slice_ready" in ir
    assert "icmp ule i64" in ir
    assert "__pcc_go_builtin_integer" in ir


def test_dynamic_string_slice_keeps_darwin_terminal_panic_boundary():
    with pytest.raises(GoDiagnostic, match="owned Linux terminal panic") as caught:
        compile_go(BORROWED_DYNAMIC, "borrowed.go", target_triple="arm64-apple-darwin")
    assert caught.value.code == "go.unsupported.string-bounds-panic"


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_borrowed_dynamic_string_valid_and_panic_lines_match_go(tmp_path, monkeypatch):
    import os
    from tests.go.owned_execution import compile_reference

    sources = [
        ("valid", BORROWED_DYNAMIC, None),
        ("negative-high", 'func cut(s string,n int)string{return s[:n]}', 'cut("abc",-1)'),
        ("large-high", 'func cut(s string,n uint64)string{return s[:n]}', 'cut("abc",18446744073709551615)'),
        ("negative-low", 'func cut(s string,n int)string{return s[n:]}', 'cut("abc",-1)'),
        ("large-low", 'func cut(s string,n uint64)string{return s[n:]}', 'cut("abc",5)'),
        ("reversed", 'func cut(s string,l,h int)string{return s[l:h]}', 'cut("abc",2,1)'),
        ("high-first", 'func cut(s string,l,h int)string{return s[l:h]}', 'cut("abc",-1,5)'),
    ]
    for kind, body, call in sources:
        source = body if call is None else "package main;" + body + ";func main(){_=" + call + "}"
        units = (("slice.go", source),)
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(units)
        binary = tmp_path / (kind + "-owned")
        write_artifact(artifact, binary)
        reference = compile_reference(units, tmp_path / kind,
                                      compiler=os.environ["PCC_GO_REFERENCE"],
                                      cache=os.environ["PCC_GO_REFERENCE_CACHE"])
        assert (reference.parent / "slice.go").read_bytes() == source.encode()
        actual, oracle = run(binary), run(reference)
        if call is None:
            assert actual == oracle == Execution(0, b"", b"")
        else:
            assert actual.returncode == oracle.returncode == 2
            assert actual.stdout == oracle.stdout == b""
            assert actual.stderr.rstrip(b"\n") == oracle.stderr.splitlines()[0]


INDEX_NATIVE = r'''package main
var calls int
func indexByte(s string, c byte) int {
    for i := 0; i < len(s); i++ {
        if s[i] == c { return i }
    }
    return -1
}
func pos(i int) int { calls++; return i }
func read(s string, i int) byte { return s[pos(i)] }
func main() {
    if indexByte("a\x00é\xff", 0xff) != 4 || indexByte("", 0) != -1 ||
        indexByte("abc", 'b') != 1 || indexByte("abc", 'z') != -1 ||
        read("é", 1) != 0xa9 || calls != 1 { panic("raw byte index") }
}
'''


def test_named_string_index_checks_unsigned_bound_before_load():
    ir = str(compile_go(INDEX_NATIVE, "index_native.go"))
    assert "string_index_ready" in ir and "string_index_bad" in ir and "icmp ult i64" in ir
    for constant in ("-1", "uint64(18446744073709551615)"):
        source = "package main;func read(s string)byte{return s[" + constant + "]};func main(){}"
        with pytest.raises(GoDiagnostic, match="nonnegative and representable as int"):
            compile_go(source)
    with pytest.raises(GoDiagnostic, match="owned Linux terminal panic"):
        compile_go(INDEX_NATIVE, "index_native.go", target_triple="arm64-apple-darwin")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_dynamic_string_index_native_reference_success_and_panics(tmp_path, monkeypatch):
    import os
    from tests.go.owned_execution import compile_reference

    for label, source in [
        ("loop", INDEX_NATIVE),
        ("negative", 'package main;func read(s string,n int)byte{return s[n]};func main(){_=read("abc",-1)}'),
        ("equal", 'package main;func read(s string,n int)byte{return s[n]};func main(){_=read("abc",3)}'),
        ("unsigned", 'package main;func read(s string,n uint64)byte{return s[n]};func main(){_=read("abc",18446744073709551615)}'),
        ("empty", 'package main;func read(s string,n int)byte{return s[n]};func main(){_=read("",0)}'),
    ]:
        units = (("byteindex.go", source),)
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(units)
        native = tmp_path / (label + "-owned")
        write_artifact(artifact, native)
        reference = compile_reference(units, tmp_path / label, compiler=os.environ["PCC_GO_REFERENCE"],
                                      cache=os.environ["PCC_GO_REFERENCE_CACHE"])
        assert (reference.parent / "byteindex.go").read_bytes() == source.encode()
        actual, oracle = run(native), run(reference)
        if label == "loop":
            assert actual == oracle == Execution(0, b"", b"")
        else:
            assert (actual.returncode, actual.stdout) == (oracle.returncode, oracle.stdout) == (2, b"")
            assert actual.stderr.rstrip(b"\n") == oracle.stderr.splitlines()[0]
