"""Named result slots and multiple values are not first-class Go tuples."""

import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go, compile_go_packages, function_symbol
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units


def source(declarations, body=""):
    return "package main; " + (declarations + ";" if declarations else "") + "func main(){" + body + "}"


PAIR = "func pair()(int64,int64){return 3,4}"


@pytest.mark.parametrize("declarations,body,message", [
    ("func f()(x int64,x int64){return}", "", "duplicate parameter or result"),
    ("func f(x int64)(x int64){return}", "", "duplicate parameter or result"),
    ("func f()(int64,int64){return}", "", "explicit result"),
    ("func f()(int64,int64){return 1}", "", "return requires 2"),
    ("func f()int64{return 1,2}", "", "return requires 1"),
    ("func f(){};func g(){return f()}", "", "void function cannot"),
    ("func f()(x int64){x:=2;return}", "", "at least one new"),
    ("func f()(x int64){var x int64;return}", "", "redeclaration"),
    ("func f()(x int64){{x:=int64(2);_=x;return}}", "", "shadowed at bare return"),
    ("func f()(x int64){{const x=2;return}}", "", "shadowed at bare return"),
    ("func f()(x int64){if x:=2;x>0{return};return}", "", "shadowed at bare return"),
    ("func f()(x int64){for x:=0;x<1;x++{return};return}", "", "shadowed at bare return"),
    ("func f()(x int64,y uint64){return true,1}", "", "cannot use"),
    (PAIR, "_ = pair()", "assignment count mismatch"),
    (PAIR, "a:=pair();_=a", "assignment count mismatch"),
    (PAIR, "a,b,c:=1,pair();_=a;_=b;_=c", "single-value context"),
    (PAIR, "const a,b=pair();_=a;_=b", "not a constant"),
    (PAIR, "_ = int(pair())", "invalid conversion"),
    (PAIR, "_ = pair()+pair()", "require integers"),
    (PAIR, "if pair(){}", "condition must be bool"),
    (PAIR, "_ = [1]int64{pair()}", "cannot use"),
    (PAIR+";func f(a int64){}", "f(pair())", "argument count mismatch"),
    (PAIR+";func f(a,b,c int64){}", "f(1,pair())", "single-value context"),
    (PAIR+";func f()(int64,int64,int64){return 1,pair()}", "", "single-value context"),
    (PAIR+";func f()(int64,uint64){return pair()}", "", "cannot use int64 as uint64"),
    ("func f()(x int64){x=1}", "", "missing return"),
    ("func f()(int64,x int64){return 1,2}", "", "unsupported type"),
    ("func f()(x int64,int64){return 1,2}", "", "expected 'name'"),
])
def test_result_lists_reject_invalid_value_and_scope_rules(declarations, body, message):
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(source(declarations, body))


def test_result_count_limit_is_explicit_before_ir():
    with pytest.raises(GoDiagnostic, match="256 results"):
        compile_go(source("func f()(" + ",".join(["int64"] * 257) + "){for{}}"))


def test_multi_result_ir_is_a_private_public_ir_aggregate_not_external_abi():
    module = compile_go(source("func f()(int8,uint64){return -1,7}", "a,b:=f();_=a;_=b"))
    dtype = module.globals[function_symbol("f")].function_type.return_type
    assert [item.width for item in dtype.elements] == [8, 64]
    text = str(module)
    assert "define " + str(dtype) in text
    assert "extractvalue " + str(dtype) in text
    assert "multiple_results" in text


def test_imported_multi_results_keep_package_function_identity():
    module = compile_go_packages({
        "main": [("main.go", 'package main;import "lib";func main(){a,b:=results.Pair();_=a;_=b}')],
        "lib": [("lib.go", 'package results;func Pair()(a int64,b uint64){a=-7;b=11;return}')],
    })
    dtype = module.globals[function_symbol("Pair", "lib")].function_type.return_type
    assert [item.width for item in dtype.elements] == [64, 64]
    assert "extractvalue " + str(dtype) in str(module)


RAW_RESULTS = [
    ("Named", (), 9), ("Blank", (), 0), ("BlankExplicit", (), 33),
    ("ZeroResult", (), 0), ("First", (), 0xfedcba9876543210),
    ("Second", (), -128), ("Third", (), 0x8123456789abcdef),
    ("ArrayResult", (), 0x8877665544332211), ("ScalarAlongArray", (), 0xffeeddccbbaa9988),
    ("ExpandedArguments", (), 0xfedcba9876543210), ("Once", (), 134),
    ("Indexed", (), 234), ("ReturnOrder", (), 121122), ("Declarations", (), 2344),
    ("ShadowExplicit", (), 23), ("Sum", (10,), 45), ("Parenthesized", (), 17),
]


@pytest.mark.integration
@pytest.mark.parametrize("name,args,expected", RAW_RESULTS)
def test_original_result_helpers_return_exact_full_width_bytes(tmp_path, monkeypatch, name, args, expected):
    original = units(("results.go",))
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(original, observe_entry=(name, args))
    binary = tmp_path / "results-native"
    write_artifact(artifact, binary)
    actual = run(binary)
    raw = (expected & ((1 << 64) - 1)).to_bytes(8, "little")
    (tmp_path / "raw-results-observation.json").write_text(json.dumps({
        "owner": artifact.owner, "function": name, "arguments": args,
        "expected_hex": raw.hex(), "stdout_hex": actual.stdout.hex(),
        "returncode": actual.returncode, "stderr_hex": actual.stderr.hex(),
        "source_sha256": hashlib.sha256(original[0][1].encode()).hexdigest(),
        "executable_sha256": hashlib.sha256(artifact.executable).hexdigest(),
    }, indent=2) + "\n")
    assert actual == Execution(0, raw, b"")


@pytest.mark.integration
def test_original_results_main_executes_owned_native_code(tmp_path, monkeypatch):
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(units(("results.go",)))
    binary = tmp_path / "results-main"
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b"", b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_receives_identical_complete_results_program(tmp_path):
    original = units(("results.go",))
    reference = compile_reference(original, tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(reference) == Execution(0, b"", b"")
    assert (reference.parent / "results.go").read_bytes() == original[0][1].encode()


def test_original_result_program_cross_emits_real_darwin_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(("results.go",))[0][1], target_triple="arm64-apple-darwin")
    assert emit_owned_object(str(module), "arm64-apple-darwin")[:4] == b"\xcf\xfa\xed\xfe"


BARE_SHADOW_SOURCES = [
    source("func f()(x int64){{x:=int64(2);_=x;return}}"),
    source("func f()(x int64){{const x=2;return}}"),
]


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("original", BARE_SHADOW_SOURCES)
def test_official_go_rejects_the_same_bare_return_shadow(original, tmp_path):
    with pytest.raises(GoDiagnostic, match="shadowed at bare return"):
        compile_go(original)
    with pytest.raises(AssertionError, match="result parameter x not in scope at return"):
        compile_reference((("shadow.go", original),), tmp_path / "reference",
                          compiler=os.environ["PCC_GO_REFERENCE"], cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (tmp_path / "reference/shadow.go").read_bytes() == original.encode()
    assert not (tmp_path / "reference/oracle").exists()
