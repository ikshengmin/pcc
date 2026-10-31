"""Initialized final source variables, not a substitute for blank-final DU rules."""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source
from pcc.frontends.java.artifact import method_symbol
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_scalars import NATIVE_REASON


def original(body, helpers="", result="int"):
    return "class FinalLocals{" + helpers + "static " + result + " run(){" + body + "}}"


CASES = [
    ("constant-int", "final int value=42;return value;", "", "int", 42),
    ("constant-long-wrap", "final long value=9223372036854775807L+1;return value;", "", "long", -9223372036854775808),
    ("constant-chain", "final int first=40;final int second=first+2;return second;", "", "int", 42),
    ("constant-char-promotes", "final int value='A';return value-23;", "", "int", 42),
    ("constant-boolean-da", "final boolean never=false;int x;if(never)return x;return 42;", "", "int", 42),
    ("constant-true-loop", "final boolean always=true;while(always){return 42;}", "", "int", 42),
    ("final-local-from-runtime-call", "final int value=f(7);return value+value+value;", "static int f(int x){return x*2;}", "int", 42),
    ("final-parameter", "return f(40);", "static int f(final int x){return x+2;}", "int", 42),
    ("final-long-parameter", "return f(9223372036854775807L);", "static long f(final long x){return x+1;}", "long", -9223372036854775808),
    ("final-boolean-parameter", "return f(false);", "static int f(final boolean b){if(b)return 7;return 42;}", "int", 42),
    ("final-for-initializer", "for(final int n=3;n>0;){return n*14;}", "", "int", 42),
    ("final-local-reinitialized-each-loop-entry", "int result=0;for(int i=0;i<4;i++){final int x=i+1;result+=x;}return result;", "", "int", 10),
    ("nested-final-shadow-field", "final int VALUE=42;return VALUE;", "static final int VALUE=7;", "int", 42),
    ("final-call-nonconstant-boolean", "final boolean b=f();if(b)return 42;return 7;", "static boolean f(){return true;}", "int", 42),
]


@pytest.mark.parametrize("name,body,helpers,result,expected", CASES, ids=[x[0] for x in CASES])
def test_final_source_uses_direct_owned_ir(name, body, helpers, result, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_source(original(body, helpers, result), descriptor="()J" if result == "long" else "()I")
    assert compiled.emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,body,helpers,result,expected", CASES, ids=[x[0] for x in CASES])
def test_original_final_source_has_exact_native_result(name, body, helpers, result, expected, tmp_path, monkeypatch):
    text = original(body, helpers, result)
    (tmp_path / "FinalLocals.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="()J" if result == "long" else "()I")
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
    assert (tmp_path / "FinalLocals.java").read_text() == text


@pytest.mark.parametrize("body,helpers,message", [
    ("final int x=1;x=2;return x;", "", "cannot assign an initialized final"),
    ("final int x=1;x+=2;return x;", "", "cannot assign an initialized final"),
    ("final int x=1;x++;return x;", "", "cannot assign an initialized final"),
    ("final int x=1;x--;return x;", "", "cannot assign an initialized final"),
    ("final boolean x=true;x^=false;return 42;", "", "cannot assign an initialized final"),
    ("final int x=f();x=2;return x;", "static int f(){return 1;}", "cannot assign an initialized final"),
    ("final int x=1;if(false)x=2;return x;", "", "cannot assign an initialized final"),
    ("for(final int i=0;i<4;i++){}return 42;", "", "cannot assign an initialized final"),
    ("return f(1);", "static int f(final int x){x=2;return x;}", "final parameter"),
    ("return f(1);", "static int f(final long x){x++;return 42;}", "final parameter"),
    ("return f(true);", "static int f(final boolean x){x&=false;return 42;}", "final parameter"),
    ("final int x=x;return x;", "", "not definitely assigned"),
    ("final int VALUE=VALUE;return VALUE;", "static final int VALUE=42;", "not definitely assigned"),
    ("final int x=1;{int x=2;}return x;", "", "already in scope"),
    ("final boolean never=false;while(never){}return 42;", "", "unreachable loop body"),
    ("final boolean b=f();if(b)return 42;", "static boolean f(){return true;}", "missing return"),
    ("return f(true);", "static int f(final boolean b){if(b)return 42;}", "missing return"),
    ("final final int x=42;return x;", "", "duplicate final local"),
    ("return f(42);", "static int f(final final int x){return x;}", "duplicate final parameter"),
])
def test_invalid_final_source_fails_before_lowering(body, helpers, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid final source reached IR"))
    with pytest.raises(JavaError, match=message):
        compile_source(original(body, helpers))


def test_blank_final_local_remains_explicit_until_definite_unassignment_is_implemented():
    text = original("final int x;if(true)x=42;else x=7;return x;")
    with pytest.raises(UnsupportedJava, match="blank final locals"):
        compile_source(text)


def test_final_does_not_change_method_signature_or_create_overloads():
    text = original("return f(42);", "static int f(int x){return x;}static int f(final int x){return x;}")
    with pytest.raises(JavaError, match="duplicate method signature"):
        compile_source(text)


def test_nonconstant_final_initializer_is_evaluated_once_not_folded_or_duplicated():
    text = original("final int x=f(7);return x+x+x;", "static int f(int x){return x*2;}")
    ir = str(compile_source(text).module)
    calls = [line for line in ir.splitlines() if "call i32" in line and method_symbol("FinalLocals", "f", "(I)I") in line]
    assert len(calls) == 1
    assert sum("load i32" in line for line in ir.splitlines()) >= 3


def test_final_constant_false_local_participates_in_short_circuit_definite_assignment():
    text = original("final boolean never=false;int x;if(never && x>0)return 7;return 42;")
    assert compile_source(text).emit_object().startswith(b"\x7fELF")
