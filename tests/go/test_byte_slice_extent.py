"""Checked constant byte-slice extent is not backing-storage admission."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go


def original(arguments):
    return f"package main;func main(){{b:=make([]byte,{arguments});_=b}}"


@pytest.mark.parametrize("arguments,want", (("0, 3*4*4", (0, 48)), ("3", (3, 3)), ("1.0, 5.0", (1, 5))))
def test_fixed_byte_slice_extent_is_retained_at_the_real_storage_boundary(arguments, want):
    with pytest.raises(UnsupportedGo, match="slice backing") as caught:
        compile_go(original(arguments), "span.go")
    assert caught.value.code == "go.unsupported.slice-storage"
    extent = caught.value.byte_span_extent
    assert (extent.length, extent.capacity) == want
    assert extent.backing.storage == "unresolved"


@pytest.mark.parametrize("arguments,message", (
    ("0,-1", "size must be a nonnegative int"),
    ("false", "size must be an integer"),
    ("5,3", "length cannot exceed capacity"),
    ("0,4,5", "requires a type, length"),
))
def test_bad_make_sizes_are_source_errors_before_the_storage_gate(arguments, message):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original(arguments), "span.go")
    assert not isinstance(caught.value, UnsupportedGo)


def test_dynamic_length_does_not_claim_a_static_extent():
    code = "package main;func count()int{return 1};func main(){_=make([]byte,count(),48)}"
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(code, "dynamic.go")
    assert caught.value.code == "go.unsupported.slice-storage"
    assert caught.value.byte_span_extent is None


def test_unchanged_cpu_name_make_has_the_selected_constant_extent():
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    from scripts.go_std_closure import FIXTURE

    path = "src/internal/cpu/cpu_x86.go"
    unit = GoParser().parse((FIXTURE / path).read_text(), path, require_main=False)
    fn = next(fn for fn in unit.functions if fn.token.text == "Name")
    allocate = next(stmt.exprs[0] for stmt in fn.body if stmt.kind == ":=" and stmt.names[0].text == "data")
    with pytest.raises(UnsupportedGo) as caught:
        Analyzer(unit, require_main=False).expression(allocate)
    assert (caught.value.filename, caught.value.line) == (path, 246)
    extent = caught.value.byte_span_extent
    assert (extent.length, extent.capacity) == (0, 48)
