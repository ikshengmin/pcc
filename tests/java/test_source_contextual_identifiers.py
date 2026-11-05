"""JLS21 3.8/3.9 contextual identifiers in existing value/member domains.

https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.8
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.9

Record/sealed-class declarations, restricted type identifiers and yield remain
separate boundaries. New source/native/raw-JDK obligations are locally UNRUN.
"""
import hashlib
import json
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_sources
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, observer_source, record_process
from tests.java.test_source_scalars import NATIVE_REASON
from tests.java.test_source_static_imports import prepare_reference
from tests.java.test_source_unicode_escapes import JDK_REASON


NAMES = ("record", "sealed", "permits")
RESTRICTED_TYPES = (*NAMES, "var", "yield")


def original(body, members=""):
    return {"ContextNames.java": "public class ContextNames{" + members + "public static int run(){" + body + "}}"}


CASES = [
    ("parameter-local-" + name, original("return take(40);",
       "static int take(int " + name + "){int result=" + name + "+2;return result;}"), "ContextNames")
    for name in NAMES
] + [
    ("inferred-mutation-" + name, original("var " + name + "=40;" + name + "++;++" + name + ";return " + name + ";"), "ContextNames")
    for name in NAMES
] + [
    ("method-names", original("return permits();",
       "static int record(){return 20;}static int sealed(){return 22;}"
       "static int permits(){return record()+ContextNames.sealed();}"), "ContextNames"),
    ("bare-record-call-statement", original("record();return 42;", "static void record(){}"), "ContextNames"),
    ("existing-constant-field-names", original("return record+ContextNames.sealed;",
       "static final int record=40,sealed=2;"), "ContextNames"),
    ("ordered-declarators", original("int record=40,sealed=record+1,permits=sealed+1;return permits;"), "ContextNames"),
    ("basic-for", original("int sealed=0;for(int record=0;record<3;record++){sealed+=14;}return sealed;"), "ContextNames"),
    ("labels-and-values", original("int record=40;record:{record++;break record;}return record+1;"), "ContextNames"),
    ("escaped-name", original(r"int r\u0065cord=42;return record;"), "ContextNames"),
    ("existing-var-value-control", original("int var=40;var++;return var+1;"), "ContextNames"),
    ("single-static-member-import", {
        "app/Main.java": "package app;import static provider.Names.record;public class Main{public static int run(){return record();}}",
        "provider/Names.java": "package provider;public class Names{public static int record(){return 42;}}",
    }, "app.Main"),
]


@pytest.mark.parametrize("name,sources,entry", CASES, ids=[row[0] for row in CASES])
def test_contextual_identifiers_preserve_original_sources_and_owned_ir(name, sources, entry, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    originals = dict(sources)
    forward = compile_sources(sources, entry_class=entry)
    backward = compile_sources(dict(reversed(tuple(sources.items()))), entry_class=entry)
    assert str(forward.module) == str(backward.module)
    assert forward.emitted_class_methods == backward.emitted_class_methods
    assert forward.emit_object().startswith(b"\x7fELF")
    assert sources == originals


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources,entry", CASES, ids=[row[0] for row in CASES])
def test_contextual_identifiers_execute_owned_native_bytes(name, sources, entry, tmp_path, monkeypatch):
    originals = dict(sources)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class=entry)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42
    assert (tmp_path / "observed.stdout.bin").read_bytes() == b"\x2a\0\0\0"
    assert (tmp_path / "observed.stderr.bin").read_bytes() == b""
    assert sources == originals


@pytest.mark.parametrize("name", NAMES)
def test_contextual_reference_locals_keep_declarations_and_bindings(name):
    source = "class Reference{Reference run(Reference input){Reference " + name + "=input;return " + name + ";}}"
    model = analyze_class_sources({"Reference.java": source})
    method, = model.models["Reference"].source.methods
    local, returned = method.body.children
    assert local.kind == "var" and local.token.text == name
    assert returned.children[0].symbol is local.symbol


def test_contextual_receiver_fields_and_methods_remain_nonlowering_models():
    source = "class Model{int record;int sealed(){int permits=record;return this.record+permits;}}"
    model = analyze_class_sources({"Model.java": source}).models["Model"]
    assert model.source.fields[0].token.text == "record"
    assert model.source.methods[0].token.text == "sealed"
    assert not model.runtime_registered
    grouped = analyze_class_sources({"Model.java": "class Model{int run(int record){return (record)+1;}}"})
    returned, = grouped.models["Model"].source.methods[0].body.children
    assert returned.children[0].kind == "binary"
    assert returned.children[0].children[0].kind == "group"


@pytest.mark.parametrize("name", NAMES)
def test_contextual_cast_operands_keep_the_reference_conversion_boundary(name):
    source = "class Reference{Reference run(Reference " + name + "){return (Reference)" + name + ";}}"
    with pytest.raises(UnsupportedJava, match="reference casts") as caught:
        analyze_class_sources({"Reference.java": source})
    assert caught.value.code == "java.unsupported.object-expressions"


@pytest.mark.parametrize("source,code", [
    ("class C{static int run(){record Local(int value){}return 42;}}", "local-types"),
    ("class C{static record Member(int value){}}", "member-types"),
    ("class C{static int run(){return switch(0){default->{yield 42;}};}}", "switch-expressions"),
])
def test_contextual_value_names_do_not_admit_record_or_switch_domains(source, code):
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources({"C.java": source}, entry_class="C")
    assert caught.value.code == "java.unsupported." + code


@pytest.mark.parametrize("name", RESTRICTED_TYPES)
@pytest.mark.parametrize("template", [
    "class NAME{}",
    "import p.NAME;class C{}",
    "import static p.NAME.value;class C{}",
    "class C{p.NAME run(){return null;}}",
])
def test_contextual_value_names_never_become_type_identifiers(name, template):
    source = template.replace("NAME", name)
    with pytest.raises(JavaError) as caught:
        SourceParser(source, "Original.java", class_model=True).parse()
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name", ("class", "true", "null", "_", "yield"))
def test_reserved_names_and_unexpanded_yield_remain_rejected(name):
    with pytest.raises(JavaError) as caught:
        compile_sources(original("int " + name + "=42;return 42;"), entry_class="ContextNames")
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-contextual-identifiers-reference")
@pytest.mark.parametrize("name,sources,entry", CASES, ids=[row[0] for row in CASES])
def test_contextual_originals_match_jdk_raw_processes(name, sources, entry, tmp_path, request, monkeypatch):
    native = tmp_path / "native"
    native.mkdir()
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class=entry)
    assert observe_scalar(compiled, native, monkeypatch) == 42
    actual = record_process((str(native / "observed"),), native, "raw-native")
    java, classes, compiler = prepare_reference(sources, tmp_path, request,
                                                observer=observer_source(entry, "I", ()))
    assert compiler[0] == 0, compiler
    reference = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccRawObserve"), tmp_path, "raw-reference")
    (tmp_path / "comparison.json").write_text(json.dumps({
        "case": name, "test_source_sha256": digest(Path(__file__)),
        "source_inputs": {path: hashlib.sha256(text.encode()).hexdigest() for path, text in sources.items()},
        "source_rewritten": False, "comparison": "raw_process_streams",
        "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
        "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
        "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
    }, indent=2) + "\n")
    assert actual == reference == (0, b"\x2a\0\0\0", b"")
