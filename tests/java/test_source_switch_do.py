"""Original JLS21 switch/do source cases; independent native full-width results.

https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.11
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html#jls-16.2.9
No JVM bytecode construction, reference runtime or compiler is a production owner.
"""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source
from pcc.frontends.java.artifact import method_symbol
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_scalars import NATIVE_REASON


def original(body, helpers="", result="int", parameters=""):
    return "class Control{" + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


# Every row's original text is used unchanged by native and separate JDK lanes.
CASES = [
    ("do-false-once", "int n=0;do{n=42;}while(false);return n;", "", "I", 42),
    ("do-repeat", "int n=0;do{n+=7;}while(n<42);return n;", "", "I", 42),
    ("do-unbraced", "int n=0;do n+=14;while(n<42);return n;", "", "I", 42),
    ("do-empty", "do;while(false);return 42;", "", "I", 42),
    ("do-continue-to-test", "int n=0;int value=0;do{n++;if(n<3)continue;value=42;}while(n<3);return value;", "", "I", 42),
    ("do-continue-to-short-circuit-test", "int n=0;do{n++;continue;}while(n<3&&flag());return n+39;", "static boolean flag(){return true;}", "I", 42),
    ("do-break-skips-test", "do{break;}while(never());return 42;", "static boolean never(){while(true){}}", "I", 42),
    ("do-return-skips-test", "do{return 42;}while(never());", "static boolean never(){while(true){}}", "I", 42),
    ("do-da-after-body", "int value;do{value=42;}while(false);return value;", "", "I", 42),
    ("do-da-before-test", "int value;do{value=42;}while(value<42);return value;", "", "I", 42),
    ("do-da-before-continue", "int value;do{value=42;continue;}while(value<42);return value;", "", "I", 42),
    ("do-vacuous-dead-test", "int value;do{break;}while(value>0);return 42;", "", "I", 42),
    ("do-constant-true-break", "do{if(true)break;}while(true);return 42;", "", "I", 42),
    ("do-nested-break", "int n=0;int value=0;do{do{value+=14;break;}while(true);n++;}while(n<3);return value;", "", "I", 42),
    ("do-inside-for", "int value=0;for(int i=0;i<3;i++){int n=0;do{n++;if(n<2)continue;value+=14;}while(n<2);}return value;", "", "I", 42),
    ("do-outer-binding", "int n=0;do{int value=42;n=value;}while(n<42);return n;", "", "I", 42),
    ("do-long", "long n=9223372036854775806L;do{n++;}while(false);return n;", "", "J", 9223372036854775807),
    ("do-boolean", "boolean b=false;do{b=true;}while(false);return b;", "", "Z", 1),
    ("switch-match", "switch(2){case 1:return 7;case 2:return 42;default:return 8;}", "", "I", 42),
    ("switch-default", "switch(9){case 1:return 7;default:return 42;}", "", "I", 42),
    ("switch-no-match", "int value=42;switch(9){case 1:value=7;}return value;", "", "I", 42),
    ("switch-fallthrough", "int value=0;switch(1){case 1:value+=17;case 2:value+=25;break;default:value=7;}return value;", "", "I", 42),
    ("switch-default-middle", "int value=0;switch(9){case 1:value=7;break;default:value=17;case 2:value+=25;}return value;", "", "I", 42),
    ("switch-stacked-labels", "switch(2){case 1:case 2:case 3:return 42;default:return 7;}", "", "I", 42),
    ("switch-comma-labels", "switch(3){case 1,2,3:return 42;default:return 7;}", "", "I", 42),
    ("switch-empty", "switch(value()){}return 42;", "static int value(){return 1;}", "I", 42),
    ("switch-trailing-label", "switch(3){case 1:return 7;case 3:}return 42;", "", "I", 42),
    ("switch-only-default-label", "switch(3){default:}return 42;", "", "I", 42),
    ("switch-char", "switch('\uffff'){case 65535:return 42;default:return 7;}", "", "I", 42),
    ("switch-byte", "switch((byte)255){case -1:return 42;default:return 7;}", "", "I", 42),
    ("switch-short", "switch((short)32768){case -32768:return 42;default:return 7;}", "", "I", 42),
    ("switch-constant-field", "switch(42){case VALUE:return 42;default:return 7;}", "static final char VALUE=42;", "I", 42),
    ("switch-constant-local", "final int n=6*7;switch(42){case n:return 42;default:return 7;}", "", "I", 42),
    ("switch-nested-assigned-constant-label", "switch(1){case 1:final int n=2;switch(2){case n:return 42;default:return 7;}default:return 8;}", "", "I", 42),
    ("switch-da-all-exits", "int value;switch(1){case 1:value=42;break;default:value=7;}return value;", "", "I", 42),
    ("switch-local-assigned-after-label", "switch(2){case 1:int value;break;case 2:value=42;return value;default:return 7;}return 8;", "", "I", 42),
    ("switch-block-local-scopes", "int value=0;switch(2){case 1:{int n=7;value=n;break;}case 2:{int n=42;value=n;break;}}return value;", "", "I", 42),
    ("switch-nested", "switch(1){case 1:switch(2){case 2:return 42;default:return 7;}default:return 8;}", "", "I", 42),
    ("switch-break-not-loop", "int value=0;for(int n=0;n<3;n++){switch(n){case 0:value+=14;break;default:value+=14;}}return value;", "", "I", 42),
    ("switch-continue-enclosing-loop", "int value=0;for(int n=0;n<4;n++){switch(n){case 1:continue;default:value+=14;}}return value;", "", "I", 42),
    ("switch-in-do-continue", "int n=0;int value=0;do{n++;switch(n){case 1:continue;default:value+=21;}}while(n<3);return value;", "", "I", 42),
    ("do-in-switch-break", "int value=0;switch(1){case 1:do{value=42;break;}while(true);break;default:value=7;}return value;", "", "I", 42),
    ("switch-arrow-blocks", "switch(2){case 1->{return 7;}case 2->{return 42;}default->{return 8;}}", "", "I", 42),
    ("switch-arrow-no-fallthrough", "int value=0;switch(1){case 1->value=42;default->value=7;}return value;", "", "I", 42),
    ("switch-arrow-comma", "int value=0;switch(3){case 1,2,3->value=42;default->value=7;}return value;", "", "I", 42),
    ("switch-arrow-negative", "int value=0;switch(-1){case -1->value=42;default->value=7;}return value;", "", "I", 42),
    ("switch-arrow-expression-call", "switch(1){case 1->noop();default->never();}return 42;", "static void noop(){}static void never(){while(true){}}", "I", 42),
    ("switch-arrow-da", "int value;switch(1){case 1->value=42;default->value=7;}return value;", "", "I", 42),
    ("switch-arrow-block-break", "int value=0;switch(1){case 1->{value=42;break;}default->value=7;}return value;", "", "I", 42),
    ("switch-arrow-no-match", "int value=42;switch(7){case 1->value=7;}return value;", "", "I", 42),
    ("switch-arrow-default-middle", "int value=0;switch(1){default->value=7;case 1->value=42;}return value;", "", "I", 42),
    ("switch-return-long", "switch(1){case 1:return -9223372036854775808L;default:return 7L;}", "", "J", -9223372036854775808),
    ("switch-return-boolean", "switch(1){case 1:return true;default:return false;}", "", "Z", 1),
]


@pytest.mark.parametrize("name,body,helpers,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_switch_do_source_uses_owned_parse_sema_ir(name, body, helpers, lane, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    result = {"I": "int", "J": "long", "Z": "boolean"}[lane]
    assert compile_source(original(body, helpers, result), descriptor="()" + lane).emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,body,helpers,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_original_switch_do_has_exact_full_width_native_result(name, body, helpers, lane, expected, tmp_path, monkeypatch):
    text = original(body, helpers, {"I": "int", "J": "long", "Z": "boolean"}[lane])
    (tmp_path / "Control.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="()" + lane)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
    assert (tmp_path / "Control.java").read_text() == text


DISPATCH = [(-2147483648, -700000001), (-1, 1800000042), (0, 42), (1, 42),
            (42, 700000001), (2147483647, 1700000042), (7, -2000000001)]
DISPATCH_BODY = "switch(x){case -2147483648:return -700000001;case -1:return 1800000042;case 0:case 1:return 42;case 42:return 700000001;case 2147483647:return 1700000042;default:return -2000000001;}"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("value,expected", DISPATCH)
def test_original_switch_dispatch_observes_all_32_bits(value, expected, tmp_path, monkeypatch):
    text = original(DISPATCH_BODY, parameters="int x")
    (tmp_path / "Control.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="(I)I")
    assert observe_scalar(compiled, tmp_path, monkeypatch, (("I", value),)) == expected


INVALID = [
    # Exact original from the failed JDK comparison, not a rewritten surrogate.
    ("switch(2){case 1:final int n=2;break;case n:return 42;default:return 7;}return 8;", "not definitely assigned"),
    ("switch(2){case 1:final int n=2;case n:return 42;default:return 7;}", "not definitely assigned"),
    ("do{}while(1);return 42;", "condition requires boolean"),
    ("do{int n=1;}while(n>0);return 42;", "undefined"),
    ("do int n=1;while(false);return 42;", "local declaration requires"),
    ("do{return 42;}while(false);return 7;", "unreachable"),
    ("int n;do{continue;}while(n>0);return 42;", "not definitely assigned"),
    ("int n;do{if(true)break;n=42;}while(false);return n;", "not definitely assigned"),
    ("int n;do{if(true)continue;n=42;}while(n>0);return n;", "not definitely assigned"),
    ("do{}while(true);return 42;", "unreachable"),
    ("do{}while(false);continue;return 42;", "enclosing loop"),
    ("do{}while(false);break;return 42;", "enclosing loop"),
    ("switch(true){default:return 42;}", "switch selector"),
    ("switch(1L){default:return 42;}", "switch selector"),
    ("switch(1){case true:return 42;default:return 7;}", "integer value required"),
    ("switch(1){case 1L:return 42;default:return 7;}", "incompatible assignment"),
    ("switch((byte)0){case 128:return 42;default:return 7;}", "incompatible assignment"),
    ("switch((char)0){case -1:return 42;default:return 7;}", "incompatible assignment"),
    ("switch(1){case 1:return 42;case 0+1:return 7;}", "duplicate case"),
    ("switch(97){case 'a':return 42;case 97:return 7;}", "duplicate case"),
    ("switch(1){default:return 42;default:return 7;}", "duplicate default"),
    ("int n=1;switch(1){case n:return 42;default:return 7;}", "constant expression"),
    ("switch(1){case 1:int n=42;break;case 2:int n=7;break;}return 42;", "already in scope"),
    ("switch(1){case 1:int n=42;case 2:return n;default:return 7;}", "not definitely assigned"),
    ("int n;switch(1){case 1:n=42;}return n;", "not definitely assigned"),
    ("int n;switch(1){case 1:n=42;break;default:}return n;", "not definitely assigned"),
    ("int n;switch(1){case 1->n=42;}return n;", "not definitely assigned"),
    ("switch(1){case 1:return 42;}", "missing return"),
    ("switch(1){default:return 42;case 1:}", "missing return"),
    ("switch(1){case 1:break;return 7;}return 42;", "unreachable"),
    ("switch(1){case 1:continue;}return 42;", "enclosing loop"),
    ("case 1:return 42;", "directly enclosing switch"),
    ("switch(1){return 42;}", "requires a case/default"),
    ("switch(1){case 1:{case 2:return 42;}}return 7;", "directly enclosing switch"),
    ("switch(1){case 1:return 42;default->{return 7;}}", "cannot mix"),
    ("switch(1){case 1->42;}return 7;", "expression statement"),
    ("switch(1){case 1->return 42;}return 7;", "unsupported Java expression"),
]


@pytest.mark.parametrize("body,message", INVALID)
def test_invalid_switch_do_is_rejected_before_ir(body, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid control flow reached IR"))
    with pytest.raises(JavaError, match=message):
        compile_source(original(body))


@pytest.mark.parametrize("body,message", [
    ("return switch(1){case 1->42;default->7;};", "unsupported Java expression"),
    ("switch(1){case 1/0:return 42;default:return 7;}", "ArithmeticException"),
    ("switch(1){case null:return 42;default:return 7;}", "unsupported Java expression"),
])
def test_switch_runtime_and_expression_dependencies_are_explicit(body, message):
    with pytest.raises(UnsupportedJava, match=message):
        compile_source(original(body))


def test_switch_selector_call_occurs_once_in_public_ir():
    compiled = compile_source(original("switch(select()){case 1:return 42;default:return 7;}", "static int select(){return 1;}"))
    symbol = method_symbol("Control", "select", "()I")
    assert sum("call " in line and symbol in line for line in str(compiled.module).splitlines()) == 1
    assert "switch i32" in str(compiled.module)
