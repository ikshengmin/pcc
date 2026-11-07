"""Static final String constant variables on the native source route.

JLS21 4.12.4 (constant variables), 13.1 (constant inlining), 15.29 (constant
expressions), 6.4.1 (java.lang.String shadowing):
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.12.4
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.29

A String constant variable is folded to source UTF-16 units and needs no
String object, field storage or class initialization. Every value read still
needs managed String materialization and stays an explicit gap; so do other
reference fields, mixed primitive concatenation and nonconstant initializers.
"""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from scripts.ci_java_primes_project import PRODUCTION, load_inputs
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_scalars import NATIVE_REASON


PRIMES_LINE = '    static final String NUMBER_TOO_SMALL = "%d is smaller than the minimum (%d)";'


def original(fields, body="return 42;", helpers=""):
    return "class Constants{" + fields + helpers + "static int run(){" + body + "}}"


CASES = [
    ("primes-original-field", PRIMES_LINE, "%d is smaller than the minimum (%d)"),
    ("empty", 'static final String EMPTY="";', ""),
    ("private-modifiers", 'private static final String HIDDEN="x";', "x"),
    ("final-static-order", 'final static String ORDER="y";', "y"),
    ("declarator-list", 'static final String A="a",B=A+"b";', "ab"),
    ("qualified-forward", 'static final String A=Constants.B+"!",B="b";', "b!"),
    ("parenthesized", 'static final String P=("p"+("q"));', "pq"),
    ("constant-condition", 'static final boolean YES=true;static final String C=YES?"t":"f";', "t"),
    ("condition-selects-second", 'static final String C=1>2?"t":"f";', "f"),
    ("supplementary-units", 'static final String S="\\uD83D\\uDE00";', "\U0001F600"),
]


def units(text):
    encoded = text.encode("utf-16-le")
    return tuple(int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2))


@pytest.mark.parametrize("name,fields,value", CASES, ids=[row[0] for row in CASES])
def test_string_constant_fields_fold_without_storage(name, fields, value, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_source(original(fields))
    assert compiled.emit_object().startswith(b"\x7fELF")
    assert " global " not in str(compiled.module)


def test_folded_units_are_the_source_value():
    from pcc.frontends.java.source_project import SourceProject
    from pcc.frontends.java.source_syntax import SourceParser
    program = SourceParser(original('static final String A="a\\u0000",B=A+"\\uD83D\\uDE00";'), "Constants.java").parse()
    SourceProject((program,)).analyze()
    fields = {field.token.text: field.string_constant.units for field in program.fields}
    assert fields == {"A": units("a\0"), "B": units("a\0\U0001F600")}


def test_primes_original_line_is_unchanged():
    _, files = load_inputs()
    text = files[PRODUCTION[0]].decode("utf-8")
    assert text.splitlines()[30] == PRIMES_LINE


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,fields,value", CASES, ids=[row[0] for row in CASES])
def test_string_constant_fields_keep_exact_native_result(name, fields, value, tmp_path, monkeypatch):
    text = original(fields)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42


@pytest.mark.parametrize("text,code,message", [
    (original('static final String M="m";', "return M==M?1:0;"),
     "java.unsupported.string-literals", "materialization"),
    (original('static final String M="m";', "return Constants.M==M?1:0;"),
     "java.unsupported.string-literals", "materialization"),
    # null is not a constant expression; the native parser rejects it first.
    (original('static final String M=null;'), "java.unsupported.object-expressions", "unsupported Java expression"),
    (original('static final String M="a"+1;'), "java.unsupported.static-initialization", "constant-expression subset"),
    (original('static final String M="a",N=M+1;'), "java.unsupported.static-initialization", "constant-expression subset"),
    (original('static final int I=1;static final String M="a"+I;'), "java.unsupported.string-literals", "conversion"),
    (original('static final String A=Constants.B,B=Constants.A;'), "java.unsupported.cyclic-initialization", "cyclic"),
    (original('static String M="m";'), "java.unsupported.declared-result-type", "source method results"),
    (original('static final String[] M={"m"};'), "java.unsupported.declared-result-type", "source method results"),
    (original('static final String M;'), "java.unsupported.static-initialization", "blank final"),
])
def test_string_constant_boundaries_precede_ir(text, code, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("String boundary reached IR"))
    with pytest.raises(UnsupportedJava, match=message) as caught:
        compile_source(text)
    assert caught.value.code == code


def test_forward_string_reference_is_illegal(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("illegal forward reached IR"))
    with pytest.raises(JavaError, match="illegal forward"):
        compile_source(original('static final String A=B,B="b";'))


def test_supplied_string_class_shadows_java_lang_string(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("shadowed String reached IR"))
    sources = {"p/String.java": "package p;class String{}",
               "p/Constants.java": "package p;" + original('static final String M="m";')}
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(sources, entry_class="p.Constants", entry="run", descriptor="()I")
    assert caught.value.code == "java.unsupported.object-expressions"
