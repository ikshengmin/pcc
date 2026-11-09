"""Compilation units with zero or several top-level classes.

JLS 7.3: CompilationUnit is [PackageDeclaration] {ImportDeclaration}
{TypeDeclaration}, so a unit may declare no type; JLS 7.4.1 uses such a
package-info.java for package documentation and annotations. Such a unit
contributes no class, and imports in it still need resolution against the
project, an explicit gap. JLS 7.6: several top-level classes share their
unit's package and imports. The unchanged Commons Numbers Primes
package-info.java is the project input.
"""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from pcc.frontends.java.source_class_model import analyze_class_sources
from pcc.frontends.java.source_syntax import SourceParser
from scripts.ci_java_primes_project import PRODUCTION, load_inputs
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_scalars import NATIVE_REASON


PACKAGE_INFO = PRODUCTION[2]
TWO_CLASSES = "class A{static int run(){return B.v()+38;}}class B{static int v(){return 4;}}"
ENTRY = "package org.apache.commons.numbers.primes;final class Answer{static int run(){return 42;}}"
TYPELESS = (
    ("empty", ""),
    ("comment-only", "/* nothing */\n// still nothing\n"),
    ("package-only", "package a.b;"),
    ("documented-package", "/** Package documentation. */\npackage a.b;\n"),
)


def upstream_package_info():
    _, files = load_inputs()
    return files[PACKAGE_INFO].decode("utf-8")


def test_upstream_package_info_declares_only_its_package():
    text = upstream_package_info()
    assert PACKAGE_INFO.endswith("/package-info.java")
    assert text.rstrip().splitlines()[-1] == "package org.apache.commons.numbers.primes;"
    assert SourceParser(text, PACKAGE_INFO).parse() is None


@pytest.mark.parametrize("name,text", TYPELESS, ids=[row[0] for row in TYPELESS])
def test_typeless_units_contribute_no_class(name, text):
    assert SourceParser(text, name + ".java").parse() is None
    assert SourceParser(text, name + ".java", class_model=True).parse() is None


def test_project_with_upstream_package_info_compiles_its_classes(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    sources = {"Answer.java": ENTRY, PACKAGE_INFO: upstream_package_info()}
    compiled = compile_sources(sources, entry_class="org.apache.commons.numbers.primes.Answer")
    assert compiled.emit_object().startswith(b"\x7fELF")
    model = analyze_class_sources(sources)
    assert [program.name for program in model.programs] == ["org/apache/commons/numbers/primes/Answer"]


def test_a_unit_or_project_without_classes_has_no_entry():
    with pytest.raises(JavaError, match="source unit declares no class"):
        compile_source("package a;")
    with pytest.raises(JavaError, match="source entry class not found"):
        compile_sources({"package-info.java": "package a;"}, entry_class="a.C")


def test_imports_in_a_typeless_unit_stay_an_explicit_gap():
    with pytest.raises(UnsupportedJava, match="without type declarations") as caught:
        SourceParser("package a;import b.C;", "package-info.java").parse()
    assert caught.value.code == "java.unsupported.source-dependency"


def test_top_level_classes_of_one_unit_share_its_package_and_imports():
    first, second = SourceParser("package p;import q.R;" + TWO_CLASSES, "Two.java").parse_unit()
    assert (first.name, second.name) == ("p/A", "p/B")
    assert first.imports == second.imports and first.imports[0][-1].text == "R"


def test_single_class_parse_rejects_a_unit_with_several_classes():
    with pytest.raises(ValueError, match="2 top-level classes"):
        SourceParser(TWO_CLASSES, "Two.java").parse()


def test_classes_of_one_unit_call_each_other(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    assert compile_source(TWO_CLASSES).emit_object().startswith(b"\x7fELF")
    assert compile_sources({"Two.java": TWO_CLASSES}, entry_class="A").emit_object().startswith(b"\x7fELF")
    model = analyze_class_sources({"Two.java": TWO_CLASSES})
    assert sorted(program.name for program in model.programs) == ["A", "B"]


def test_single_source_entry_is_its_first_class():
    with pytest.raises(JavaError, match="source entry method not found"):
        compile_source("class B{static int v(){return 4;}}" + "class A{static int run(){return 42;}}")


def test_duplicate_classes_in_one_unit_are_invalid():
    with pytest.raises(JavaError, match="duplicate source class A") as caught:
        compile_source("class A{}class A{}")
    assert not isinstance(caught.value, UnsupportedJava)


@pytest.mark.parametrize("text", ("package a", "package a;;class", "package a; int x;"))
def test_malformed_units_remain_invalid(text):
    with pytest.raises(JavaError) as caught:
        SourceParser(text, "Bad.java").parse()
    assert not isinstance(caught.value, UnsupportedJava)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_classes_of_one_unit_keep_exact_native_result(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(TWO_CLASSES)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_project_with_upstream_package_info_keeps_exact_native_result(tmp_path, monkeypatch):
    sources = {"Answer.java": ENTRY, PACKAGE_INFO: upstream_package_info()}
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class="org.apache.commons.numbers.primes.Answer")
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42
