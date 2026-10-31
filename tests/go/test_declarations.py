"""Declaration groups retain source scope, typed repetition and iota context."""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go, compile_go_packages
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units


def program(declaration, body=""):
    return "package main;"+(declaration+";" if declaration else "")+"func main(){"+body+"}"


@pytest.mark.parametrize("declaration,body,message", [
    ("const (A)", "", "preceding ConstSpec"),
    ("const (A=1; B int)", "", "preceding ConstSpec"),
    ("const A", "", "preceding ConstSpec"),
    ("const (A,B=1,2; C)", "", "count mismatch"),
    ("const (A=1; B,C)", "", "count mismatch"),
    ("const (A uint8=255+iota; B)", "", "overflows uint8"),
    ("const (_ int8=128)", "", "overflows int8"),
    ("const (_=Unknown)", "", "undefined name"),
    ("const (_=iota; A=A)", "", "initialization cycle"),
    ("const (A=1; A=2)", "", "duplicate package declaration"),
    ("var (A)", "", "requires a type or initializer"),
    ("var (A=iota)", "", "iota is only defined"),
    ("", "_=iota", "iota is only defined"),
    ("", "const(A=1; A=2)", "redeclaration"),
    ("", "const(A=1; B); A=3;_=B", "cannot assign"),
    ("", "const (A=int64(1);B);var x int=A;_=x;_=B", "cannot use int64 as int"),
    ("", "const (_=iota;_=1<<4097)", "precision limit"),
    ("const (;A=1)", "", "expected 'name'"),
    ("const (A=1;;B)", "", "expected 'name'"),
])
def test_invalid_groups_and_iota_context_fail_before_lowering(declaration,body,message,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,"lower",lambda *args:pytest.fail("invalid declaration reached IR"))
    with pytest.raises(GoDiagnostic,match=message):
        compile_go(program(declaration,body))


def test_repetition_clones_expressions_instead_of_sharing_typed_ast():
    from pcc.frontends.go.syntax import GoParser
    source=program("const(A,B=iota,1<<iota;C,D;E,F)")
    parsed=GoParser().parse(source)
    assert [item.iota for item in parsed.constants]==[0,1,2]
    for index in range(2):
        assert len({id(item.exprs[index]) for item in parsed.constants})==3
    assert compile_go(source)


def test_group_does_not_create_a_new_local_scope_and_iota_resets():
    assert compile_go(program("", "const(a=iota;b);const c=iota;_=a;_=b;_=c"))
    with pytest.raises(GoDiagnostic,match="redeclaration"):
        compile_go(program("", "const(a=iota);var a int;_=a"))


def test_imported_group_constants_keep_nominal_typed_values():
    module=compile_go_packages({
        "main":[("main.go",'package main;import "lib";func main(){var x uint64=number.B;_=x}')],
        "lib":[("lib.go",'package number;const(A uint64=1<<63+iota;B)')],
    })
    from pcc.ir.compat import ir
    expected = ir.Constant(ir.IntType(64), (1 << 63) + 1)
    assert "store " + str(expected.type) + " " + str(expected) in str(module)


RAW_DECLARATIONS = [
    ("PackageIota",(),0xfedcba9876543214), ("PackagePairs",(),1224),
    ("DependencyContext",(),225), ("ResetIota",(),77), ("LocalGroup",(),1011226), ("Scoped",(),12205),
    ("VariableGroup",(),120), ("RepeatedBoolean",(),91),
    ("ArrayConstants",(),0xfedcba9876543214), ("TypedRepeat",(),-1292826),
    ("BlankChecked",(),2),
]


@pytest.mark.integration
@pytest.mark.parametrize("name,args,expected", RAW_DECLARATIONS)
def test_original_declaration_helpers_return_exact_full_width_bytes(tmp_path, monkeypatch, name, args, expected):
    original = units(("declarations.go",))
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(original, observe_entry=(name, args))
    binary = tmp_path / "declarations-native"
    write_artifact(artifact, binary)
    actual = run(binary)
    raw = (expected & ((1 << 64) - 1)).to_bytes(8, "little")
    (tmp_path / "raw-declarations-observation.json").write_text(json.dumps({
        "owner": artifact.owner, "function": name, "arguments": args,
        "expected_hex": raw.hex(), "stdout_hex": actual.stdout.hex(),
        "returncode": actual.returncode, "stderr_hex": actual.stderr.hex(),
        "source_sha256": hashlib.sha256(original[0][1].encode()).hexdigest(),
        "executable_sha256": hashlib.sha256(artifact.executable).hexdigest(),
    }, indent=2) + "\n")
    assert actual == Execution(0, raw, b"")


@pytest.mark.integration
def test_original_declarations_main_executes_owned_native_code(tmp_path, monkeypatch):
    with monkeypatch.context() as owned:
        forbid_external_compiler(owned)
        artifact = compile_owned(units(("declarations.go",)))
    binary = tmp_path / "declarations-main"
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b"", b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_receives_identical_complete_declarations_program(tmp_path):
    original = units(("declarations.go",))
    reference = compile_reference(original, tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(reference) == Execution(0, b"", b"")
    assert (reference.parent / "declarations.go").read_bytes() == original[0][1].encode()


def test_original_declaration_program_cross_emits_real_darwin_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(("declarations.go",))[0][1], target_triple="arm64-apple-darwin")
    assert emit_owned_object(str(module), "arm64-apple-darwin")[:4] == b"\xcf\xfa\xed\xfe"



@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("original,pcc_message,reference_message", [
    (program("var(A=iota)"), "iota is only defined", "cannot use iota outside constant declaration"),
    (program("const(_ int8=128)"), "overflows int8", "as int8 value in constant declaration.*overflows"),
])
def test_official_go_rejects_identical_invalid_constant_inputs(original,pcc_message,reference_message,tmp_path):
    with pytest.raises(GoDiagnostic,match=pcc_message):
        compile_go(original)
    with pytest.raises(AssertionError,match=reference_message):
        compile_reference((("invalid.go",original),),tmp_path/"reference",
                          compiler=os.environ["PCC_GO_REFERENCE"],cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (tmp_path/"reference/invalid.go").read_bytes()==original.encode()
    assert not (tmp_path/"reference/oracle").exists()


@pytest.mark.parametrize("kind", ["const", "var"])
def test_empty_declaration_group_is_not_erased_from_termination_rules(kind):
    original=program("func f()int{return 1;"+kind+"()}")
    with pytest.raises(GoDiagnostic,match="missing return"):
        compile_go(original)
    assert compile_go(program("func f()int{"+kind+"();return 1}"))
    with pytest.raises(GoDiagnostic,match="imports must precede"):
        compile_go('package main;'+kind+'();import "missing";func main(){}')


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("kind", ["const", "var"])
def test_official_go_rejects_same_nonterminating_empty_declaration_group(kind,tmp_path):
    original=program("func f()int{return 1;"+kind+"()}")
    with pytest.raises(GoDiagnostic,match="missing return"):
        compile_go(original)
    with pytest.raises(AssertionError,match="missing return"):
        compile_reference((("invalid.go",original),),tmp_path/"reference",
                          compiler=os.environ["PCC_GO_REFERENCE"],cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (tmp_path/"reference/invalid.go").read_bytes()==original.encode()
    assert not (tmp_path/"reference/oracle").exists()
