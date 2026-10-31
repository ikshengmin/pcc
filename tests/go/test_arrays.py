"""Fixed-array value semantics; explicit missing dynamic-bounds panic contract."""

import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go, compile_go_packages
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units


def source(body, declarations=""):
    return "package main; " + declarations + "; func main(){" + body + "}"


@pytest.mark.parametrize("body,declarations,message", [
    ("var a [-1]int;_ = a", "", "nonnegative"),
    ("var a [true]int;_ = a", "", "integer constant"),
    ("var a [n]int;_ = a", "var n=3", "integer constant"),
    ("var a [4097]int;_ = a", "", "4096 elements"),
    ("var a [...]int;_ = a", "", "only valid in a composite"),
    ("_ = [2]int{1,2,3}", "", "out of bounds"),
    ("_ = [2]int{1:2,1:3}", "", "duplicate array literal index"),
    ("_ = [2]int{-1:3}", "", "nonnegative"),
    ("_ = [...]int{4096:1}", "", "4096 elements"),
    ("_ = [2]int{true:3}", "", "integer constant"),
    ("_ = [2]uint8{256}", "", "overflows uint8"),
    ("_ = [2][2]int{}", "", "nested arrays"),
    ("const a=[1]int{1};_ = a", "", "not a constant"),
    ("_ = [2]int{}[-1]", "", "nonnegative"),
    ("_ = [2]int{}[2]", "", "out of bounds"),
    ("_ = [0]int{}[0]", "", "out of bounds"),
    ("_ = [2]int{}[true]", "", "index must be an integer"),
    ("var a [2]int; var i int;_ = a[i]", "", "bounds-panic support"),
    ("var a [255]int; var i uint8;_ = a[i]", "", "bounds-panic support"),
    ("var a [2]int; var i uint64;_ = a[i&3]", "", "bounds-panic support"),
    ("var a [2]int; var i int64;_ = a[i>>63]", "", "bounds-panic support"),
    ("[2]int{}[0]=1", "", "non-addressable"),
    ("f()[0]=1", "func f()[2]int{return [2]int{}}", "non-addressable"),
    ("var a [2]int;var b [3]int;a=b;_=a", "", "cannot use"),
    ("var a [2]int;var b [2]int64;a=b;_=a", "", "cannot use"),
    ("var a [2]int;_=a==a", "", "unsupported comparison"),
    ("var a [2]int;_ = int(a)", "", "invalid conversion"),
    ("var a int;a=1", "", "declared and not used"),
    ("", "var a=f();func f()int{return 3}", "shared-runtime initialization"),
    ("", "var a=3;var b=a", "shared-runtime initialization"),
    ("", "var a=[1]int{f()};func f()int{return 3}", "shared-runtime initialization"),
    ("", "var a=a", "initialization cycle"),
    ("", "var init int", "reserved"),
    ("", "var a int;func a(){}", "duplicate package declaration"),
])
def test_array_or_initializer_boundaries_fail_as_go_diagnostics(body, declarations, message):
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(source(body, declarations))


def test_array_identity_lengths_aliases_and_static_package_storage():
    text = str(compile_go(source("var a [2]byte;var b [1+1]uint8;a=b;_=a", "var x [3]int64;var y=[2]uint64{1,2}")))
    assert "global [3 x i64] zeroinitializer" in text
    assert "global [2 x i64] [i64 1, i64 2]" in text
    assert "load [2 x i8]" in text and "store [2 x i8]" in text


def test_imported_package_variables_are_storage_not_inlined_constants():
    module = compile_go_packages({
        "main": [("main.go", 'package main;import "lib";func main(){data.Table[0]=9;data.Count++;_=data.Table[0]}')],
        "lib": [("lib.go", 'package data;var Table=[2]int{3,4};var Count int')],
    })
    text = str(module)
    assert "global [2 x i64]" in text and "store i64 9" in text


RAW_ARRAY_CASES = [
    ("MutateGlobal", (), 0xffff000011112222), ("ArgumentSnapshot", (), 0xfedcba9876543210),
    ("CopyLocal", (), 0xfedcba9876543210), ("CopyArgument", (), 0xfedcba9876543210),
    ("CopyResult", (), 0x8877665544332211), ("TemporaryIndex", (), 0x8877665544332211),
    ("Lookup", (255,), 0x8123456789abcdef), ("Lookup", (0,), 0),
    ("Masked", (-1,), 7), ("Shifted", (0xffffffffffffffff,), 7),
    ("Zero", (), 0), ("SparseResult", (), 281470681743365),
    ("Empty", (), 71), ("Boolean", (), 93), ("Parallel", (), 12861),
    ("Compound", (), 1212), ("Swap", (), 0xfedcba987654321b),
]


@pytest.mark.integration
@pytest.mark.parametrize("name,args,expected", RAW_ARRAY_CASES)
def test_original_array_helpers_return_independent_full_width_bytes(tmp_path, monkeypatch, name, args, expected):
    original = units(("arrays.go",))
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(original, observe_entry=(name, args))
    binary = tmp_path / "array-native"
    write_artifact(artifact, binary)
    actual = run(binary)
    raw = expected.to_bytes(8, "little")
    (tmp_path / "raw-array-observation.json").write_text(json.dumps({
        "owner": artifact.owner, "function": name, "arguments": args,
        "expected_hex": raw.hex(), "stdout_hex": actual.stdout.hex(),
        "returncode": actual.returncode, "stderr_hex": actual.stderr.hex(),
        "source_sha256": hashlib.sha256(original[0][1].encode()).hexdigest(),
        "executable_sha256": hashlib.sha256(artifact.executable).hexdigest(),
    }, indent=2) + "\n")
    assert actual == Execution(0, raw, b"")


@pytest.mark.integration
def test_original_array_program_main_executes(tmp_path, monkeypatch):
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(units(("arrays.go",)))
    binary = tmp_path / "array-main"
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b"", b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_receives_identical_original_array_program(tmp_path):
    original = units(("arrays.go",))
    reference = compile_reference(original, tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(reference) == Execution(0, b"", b"")
    assert (reference.parent / "arrays.go").read_bytes() == original[0][1].encode()


def test_original_array_program_emits_real_arm64_darwin_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(("arrays.go",))[0][1], target_triple="arm64-apple-darwin")
    assert emit_owned_object(str(module), "arm64-apple-darwin")[:4] == b"\xcf\xfa\xed\xfe"


def test_scalar_zero_initialization_retains_scalar_ir_constants():
    text = str(compile_go(source("var a int64;var b bool;_=a;_=b", "var x int64")))
    assert "store i64 0" in text and "store i1 0" in text
    assert "global i64 0" in text
    assert "i64 zeroinitializer" not in text
