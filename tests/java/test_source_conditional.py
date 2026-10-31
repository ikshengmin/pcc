"""Primitive ?: source typing/DA and selected-arm native execution."""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source
from pcc.frontends.java.artifact import method_symbol
from pcc.frontends.java.source_project import SourceProject
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_scalars import NATIVE_REASON

LANES = {"int": "I", "long": "J", "boolean": "Z"}


def original(result, body, helpers="", parameters=""):
    return "class Choice{" + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


CASES = [
    ("true-arm", "int", "return true?42:7;", "", 42),
    ("false-arm", "int", "return false?7:42;", "", 42),
    ("right-association", "int", "return false?7:true?42:8;", "", 42),
    ("nested-middle", "int", "return true?false?7:42:8;", "", 42),
    ("logical-precedence", "int", "return false||true?40+2:7;", "", 42),
    ("parenthesized-binary", "int", "return (false?1:14)*3;", "", 42),
    ("boolean-value", "boolean", "return false?false:true;", "", True),
    ("boolean-dynamic", "boolean", "return flag()?true:false;", "static boolean flag(){return true;}", True),
    ("int-wrap-before-widening", "long", "return true?2147483647+1:0L;", "", -2147483648),
    ("conditional-promotes-before-add", "long", "return (true?2147483647:0L)+1;", "", 2147483648),
    ("long-high-bits", "long", "return flag()?9223372036854775807L:-7;", "static boolean flag(){return true;}", 9223372036854775807),
    ("long-negative-arm", "long", "return flag()?7:-9223372036854775808L;", "static boolean flag(){return false;}", -9223372036854775808),
    ("byte-constant-narrowing", "int", "return (false?(byte)-1:127)+1;", "", 128),
    ("char-constant-narrowing", "int", "return false?(char)0:65535;", "", 65535),
    ("byte-short-common-type", "int", "return true?(byte)255:(short)1;", "", -1),
    ("constant-field", "int", "return VALUE;", "static final char VALUE=true?42:'x';", 42),
    ("final-constant-loop", "int", "final boolean b=true?true:false;while(b){return 42;}", "", 42),
    ("da-impossible-false-arm", "int", "int x;return true?42:x;", "", 42),
    ("da-impossible-true-arm", "int", "int x;return false?x:42;", "", 42),
    ("da-structural-true", "int", "int x;if(flag()?true:true)x=42;return x;", "static boolean flag(){return false;}", 42),
    ("da-structural-false", "int", "int x;if(flag()?false:false)return x;return 42;", "static boolean flag(){return true;}", 42),
    ("da-nested-short-circuit", "int", "int x;return (flag()&&false)?x:42;", "static boolean flag(){return true;}", 42),
    ("unchosen-false-arm-must-not-run", "int", "return flag()?42:never();", "static boolean flag(){return true;}static int never(){while(true){}}", 42),
    ("unchosen-true-arm-must-not-run", "int", "return flag()?never():42;", "static boolean flag(){return false;}static int never(){while(true){}}", 42),
    ("nested-unchosen-must-not-run", "long", "return flag()?(flag()?42L:never()):never();", "static boolean flag(){return true;}static long never(){while(true){}}", 42),
    ("overload-not-return-target", "long", "return pick(flag()?1:2);", "static boolean flag(){return true;}static int pick(int n){return 42;}static long pick(long n){return 7;}", 42),
    ("overload-promoted-long", "int", "return pick(flag()?1:2L);", "static boolean flag(){return false;}static int pick(int n){return 7;}static int pick(long n){return 42;}", 42),
    ("loop-condition-and-update", "int", "int n=0;for(int i=0;i<4;i+=i<2?1:2){n+=i<2?10:22;}return n;", "", 42),
]


@pytest.mark.parametrize("name,result,body,helpers,expected", CASES, ids=[case[0] for case in CASES])
def test_conditional_uses_owned_source_ir(name, result, body, helpers, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_source(original(result, body, helpers), descriptor="()" + LANES[result])
    assert compiled.emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,result,body,helpers,expected", CASES, ids=[case[0] for case in CASES])
def test_original_conditional_has_exact_native_result(name, result, body, helpers, expected, tmp_path, monkeypatch):
    text = original(result, body, helpers)
    (tmp_path / "Choice.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="()" + LANES[result])
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
    assert (tmp_path / "Choice.java").read_text() == text


OPERANDS = {
    "byte": ("(byte)x", 127), "short": ("(short)x", -129),
    "char": ("(char)x", 65407), "int": ("x", -129),
    "long": ("(long)x+4294967296L", 4294967167),
}
MATRIX = [(a, b) for a in OPERANDS for b in OPERANDS]


def matrix_source(a, b):
    return original("long", "return choose?" + OPERANDS[a][0] + ":" + OPERANDS[b][0] + ";", parameters="int x,boolean choose")


@pytest.mark.parametrize("a,b", MATRIX)
def test_standalone_numeric_conditional_type_matrix(a, b):
    program = SourceParser(matrix_source(a, b), "Choice.java").parse()
    SourceProject((program,)).analyze()
    node = program.methods[0].body.children[0].children[0]
    expected = a if a == b else "short" if {a, b} == {"byte", "short"} else "long" if "long" in (a, b) else "int"
    assert node.kind == "conditional" and node.dtype == expected


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("a,b", MATRIX)
@pytest.mark.parametrize("choose", [False, True])
def test_original_numeric_conditional_matrix_executes_exact_selected_value(a, b, choose, tmp_path, monkeypatch):
    text = matrix_source(a, b)
    (tmp_path / "Choice.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="(IZ)J")
    assert observe_scalar(compiled, tmp_path, monkeypatch, (("I", -129), ("Z", int(choose)))) == OPERANDS[a if choose else b][1]
    assert (tmp_path / "Choice.java").read_text() == text


@pytest.mark.parametrize("narrow,constant,expected", [
    ("byte", -128, "byte"), ("byte", 127, "byte"), ("byte", -129, "int"), ("byte", 128, "int"),
    ("short", -32768, "short"), ("short", 32767, "short"), ("short", -32769, "int"), ("short", 32768, "int"),
    ("char", 0, "char"), ("char", 65535, "char"), ("char", -1, "int"), ("char", 65536, "int"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_int_constant_narrowing_depends_on_its_representability_not_selected_branch(narrow, constant, expected, reverse):
    arms = ["(" + narrow + ")x", str(constant)]
    if reverse:
        arms.reverse()
    text = original("int", "return choose?" + arms[0] + ":" + arms[1] + ";", parameters="int x,boolean choose")
    program = SourceParser(text, "Choice.java").parse()
    SourceProject((program,)).analyze()
    assert program.methods[0].body.children[0].children[0].dtype == expected


INVALID = [
    ("return 1?42:7;", "", "condition requires boolean"),
    ("return true?42:0L;", "", "incompatible assignment"),
    ("return true?f():42;", "static void f(){}", "cannot have void"),
    ("return false?42:f();", "static void f(){}", "cannot have void"),
    ("return true?42:missing;", "", "undefined"),
    ("return true?42:missing();", "", "unresolved"),
    ("int x;return flag()?x:42;", "static boolean flag(){return true;}", "not definitely assigned"),
    ("int x;return flag()?42:x;", "static boolean flag(){return false;}", "not definitely assigned"),
    ("int x;if(flag()?true:false)x=42;return x;", "static boolean flag(){return true;}", "not definitely assigned"),
    ("while(flag()?true:true){}", "static boolean flag(){return true;}", "missing return"),
    ("final boolean b=true?true:flag();while(b){}", "static boolean flag(){return true;}", "missing return"),
    ("return true?1:2;", "static final char VALUE=true?42:f();static int f(){return 7;}", "incompatible assignment"),
]


@pytest.mark.parametrize("body,helpers,message", INVALID)
def test_invalid_conditional_source_is_checked_before_lowering(body, helpers, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid conditional reached IR"))
    with pytest.raises(JavaError, match=message):
        compile_source(original("int", body, helpers))


@pytest.mark.parametrize("body,message", [
    ("return true?1:false;", "reference/boxing"),
    ("return true?42:1/0;", "ArithmeticException"),
])
def test_conditional_does_not_hide_an_unsupported_operand(body, message):
    with pytest.raises(UnsupportedJava, match=message):
        compile_source(original("int", body))


def test_condition_is_evaluated_once_and_each_arm_has_its_own_cfg_block():
    text = original("int", "return condition()?yes():no();", "static boolean condition(){return true;}static int yes(){return 42;}static int no(){return 7;}")
    ir = str(compile_source(text).module)
    for name, descriptor in (("condition", "()Z"), ("yes", "()I"), ("no", "()I")):
        assert sum("call " in line and method_symbol("Choice", name, descriptor) in line for line in ir.splitlines()) == 1
    assert "conditional_yes:" in ir and "conditional_no:" in ir and "phi i32" in ir
