"""Fixed byte pads occupy storage but do not act as observable fields."""
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler


ORIGINAL = """package main
const CacheLinePadSize = 64
type CacheLinePad struct { _ [CacheLinePadSize]byte }
type Record struct { L uint8; _ [4]byte; _ [2]byte; R int }
var order int
func four() [4]byte { order = order*10+1; return [4]byte{99} }
func two() [2]byte { order = order*10+2; return [2]byte{22} }
func right() int { order = order*10+3; return 41 }
func main() {
    pad := CacheLinePad{}
    if pad != (CacheLinePad{[64]byte{1}}) { panic("padding equality") }
    a := Record{1, four(), two(), right()}
    b := Record{L: 1, R: 41}
    if order != 123 || a != b || a.L != 1 || a.R != 41 { panic("field or evaluation order") }
    b.R = 40
    if a == b { panic("real field missing from equality") }
}
"""


def test_fixed_byte_fields_occupy_slots_without_padding_comparison():
    ir = str(compile_go(ORIGINAL, "padding.go"))
    assert "{ i8, [4 x i8], [2 x i8], i64 }" in ir
    assert "[64 x i8]" in ir
    # Only the one explicitly provided literal element is stored, not 64
    # padding comparisons or any observable field of CacheLinePad.
    assert ir.count("getelementptr [64 x i8]") == 1


@pytest.mark.parametrize("body", ("_ = x._", "_ = R{_:[4]byte{}, N:1}"))
def test_padding_has_no_selector_or_key(body):
    code = f"package main;type R struct{{_ [4]byte;N int}};func main(){{var x R;_=x;{body}}}"
    with pytest.raises(GoDiagnostic, match="blank field") as caught:
        compile_go(code, "invalid.go")
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_fixed_byte_padding_native_differential(tmp_path, monkeypatch):
    units = (("padding.go", ORIGINAL),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units)
    native = tmp_path / "owned"
    write_artifact(artifact, native)
    official = compile_reference(units, tmp_path / "official", compiler=os.environ["PCC_GO_REFERENCE"],
                                 cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(native) == run(official) == Execution(0, b"", b"")


NAMED_PADDING = """package main
type CacheLinePad struct { _ [16]byte }
type NestedPad struct { _ CacheLinePad; _ [4]byte }
type CPU struct { _ NestedPad; HasAES bool; _ CacheLinePad }
var X86 CPU
var called int
func pad() NestedPad { called++; return NestedPad{} }
func main() {
    X86.HasAES = true
    want := CPU{pad(), true, CacheLinePad{}}
    if X86 != want || !X86.HasAES || called != 1 { panic("named padding or field order") }
    want.HasAES = false
    if X86 == want { panic("real field missing from equality") }
}
"""


def test_named_padding_records_preserve_nested_storage_and_real_fields():
    ir = str(compile_go(NAMED_PADDING, "named_padding.go"))
    assert "{ { { [16 x i8] }, [4 x i8] }, i1, { [16 x i8] } }" in ir


@pytest.mark.parametrize("padding", [
    "type Pad struct { Data [8]byte }",
    "type Pad struct { _ [0]byte }",
    "type Pad struct { _ [8]uint16 }",
    "type Pad struct {}",
    "type Pad struct { _ *int }",
])
def test_blank_named_records_still_reject_non_padding_shapes(padding):
    source = "package main;" + padding + "; type R struct{_ Pad; X int};func main(){}"
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source, "nonpadding.go")
    assert caught.value.code == "go.unsupported.blank-struct-fields"


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_nested_named_padding_native_differential(tmp_path, monkeypatch):
    originals = (("named_padding.go", NAMED_PADDING),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / "owned"
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / "official",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (reference.parent / "named_padding.go").read_bytes() == NAMED_PADDING.encode()
    assert run(binary) == run(reference) == Execution(0, b"", b"")
