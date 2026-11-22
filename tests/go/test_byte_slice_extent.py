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


def selected_cpu_package():
    import json

    from pcc.frontends.go.packages import parse_package
    from scripts.ci_go_math_bits_project import sha256
    from scripts.go_std_closure import FIXTURE, load_linux_normal_plan

    pin = json.loads(load_linux_normal_plan()[0])
    files = pin["packages"]["internal/cpu"]["files"]["go"]
    units = []
    for name in files:
        name = "src/internal/cpu/" + name
        raw = (FIXTURE / name).read_bytes()
        assert pin["files"][name] == [len(raw), sha256(raw)]
        units.append((name, raw.decode()))
    return parse_package(units, require_main=False)


def test_exact_append_bytes_prefix_preserves_capacity_and_backing_owner():
    from pcc.frontends.go.managed_objects import FixedByteExtent, byte_backing_plan
    from pcc.frontends.go.slice_proof import append_within_extent, byte_append_summary

    program = selected_cpu_package()
    summary = byte_append_summary(program, "appendBytes")
    assert summary.bytes_per_word == 4 and summary.shifts == (0, 8, 16, 24)
    name_fn = next(fn for fn in program.functions if fn.token.text == "Name")
    counts = [
        len(stmt.exprs[0].args) - 1 for stmt in name_fn.body
        if stmt.kind == "=" and stmt.exprs[0].kind == "call" and stmt.exprs[0].value == "appendBytes"
    ]
    assert counts == [4, 4, 4]
    owner = byte_backing_plan()
    extent = FixedByteExtent(0, 48, owner)
    observed = []
    for count in counts:
        extent = append_within_extent(extent, summary, count)
        observed.append(extent.length)
        assert extent.backing is owner and extent.capacity == 48
    assert observed == [16, 32, 48]
    with pytest.raises(ValueError, match="capacity"):
        append_within_extent(extent, summary, 1)


@pytest.mark.parametrize("extra", (
    "func append(){}",
    "var byte int",
))
def test_shadowed_builtins_cannot_prove_in_place_growth(extra):
    from pcc.frontends.go.slice_proof import byte_append_summary
    from pcc.frontends.go.syntax import GoParser

    code = ("package p;func f(b []byte,args ...uint32)[]byte{"
            "for _,v:=range args{b=append(b,byte(v))};return b};" + extra)
    program = GoParser().parse(code, "shadow.go", require_main=False)
    assert byte_append_summary(program, "f") is None


def test_retained_or_replaced_slice_is_not_a_caller_backing_proof():
    from pcc.frontends.go.slice_proof import byte_append_summary
    from pcc.frontends.go.syntax import GoParser

    prefix = "package p;var saved []byte;func f(b []byte,args ...uint32)[]byte{"
    step = "for _,v:=range args{b=append(b,byte(v))};"
    for rest in ("saved=b;return b}", "return saved}"):
        program = GoParser().parse(prefix + step + rest, "escape.go", require_main=False)
        assert byte_append_summary(program, "f") is None


def test_original_caller_keeps_backing_local_and_requires_an_owned_string_copy():
    from pcc.frontends.go.managed_objects import FixedByteExtent, byte_backing_plan
    from pcc.frontends.go.slice_proof import byte_span_caller_proof

    program = selected_cpu_package()
    extent = FixedByteExtent(0, 48, byte_backing_plan())
    proof = byte_span_caller_proof(program, "Name", "data", extent)
    assert proof.append_lengths == (16, 32, 48)
    assert proof.views == 2 and proof.string_copies == 1
    assert byte_span_caller_proof(program, "Name", "data", FixedByteExtent(0, 64, extent.backing)) is None


@pytest.mark.parametrize("use", (
    "consume(data)",
    "_ = &data[0]",
    "alias := data; _ = alias",
    "if true { data := 2; _ = data }",
    "for true { data=appendWords(data,1) }",
    "data=data[:0]; data=appendWords(data,1)",
    "Again: data=appendWords(data,1); goto Again",
))
def test_unproved_caller_uses_cannot_authorize_fixed_stack_backing(use):
    from pcc.frontends.go.managed_objects import FixedByteExtent, byte_backing_plan
    from pcc.frontends.go.slice_proof import byte_span_caller_proof
    from pcc.frontends.go.syntax import GoParser

    code = (
        "package p;func appendWords(b []byte,args ...uint32)[]byte{"
        "for _,v:=range args{b=append(b,byte(v))};return b};"
        f"func f()string{{data:=make([]byte,0,48);{use};return string(data)}}"
    )
    program = GoParser().parse(code, "unproved.go", require_main=False)
    assert byte_span_caller_proof(program, "f", "data", FixedByteExtent(0, 48, byte_backing_plan())) is None


def test_legacy_octal_capacity_cannot_be_reinterpreted_as_decimal_proof():
    from pcc.frontends.go.managed_objects import FixedByteExtent, byte_backing_plan
    from pcc.frontends.go.slice_proof import byte_span_caller_proof
    from pcc.frontends.go.syntax import GoParser

    code = "package p;func f() int { data:=make([]byte,0,010);return cap(data) }"
    program = GoParser().parse(code, "octal.go", require_main=False)
    assert byte_span_caller_proof(program, "f", "data", FixedByteExtent(0, 10, byte_backing_plan())) is None
