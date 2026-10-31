"""Stable source capability codes, distinct from malformed Java diagnostics.

JLS21 3.5/3.8/3.10 (tokens), 7.5 (imports), 8.1.1/8.4.3
(modifiers), 14 (statements), 15.25 (conditionals), and 16 (DA/DU):
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html

Codes report the first unavailable domain, not that all remaining source is
valid. Metadata qualification is not native object/runtime qualification.
Existing unchanged raw-reference programs exercise the native source path.
"""
import ast
import hashlib
import json
import runpy
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources
from pcc.frontends.java.source_syntax import SourceParser, Token, tokenize


def original(body):
    return "class Diagnostic{static int run(){" + body + "}}"


# Exact input and old diagnostic prose are retained separately from the code.
CAPABILITIES = [
    (original('return "text";'), "string-literals", "unsupported Java source token"),
    ("@Deprecated class Diagnostic{}", "annotations", "unsupported Java source token"),
    (original("int π=1;return π;"), "unicode-tokens", "unsupported Java source token"),
    (original("return 42; // \\u000a comment\n"), "unicode-escapes", "Unicode-escape translation"),
    ("abstract class Diagnostic{}", "abstract-classes", "unsupported top-level class modifiers"),
    ("class Diagnostic extends Object{}", "class-supertypes-generics", "inheritance, generics"),
    ("class Diagnostic<T>{}", "class-supertypes-generics", "inheritance, generics"),
    ("class Diagnostic{static <T> int run(){return 42;}}", "generic-declarations", "source method results"),
    ("class Diagnostic{static class Nested{}}", "member-types", "source method results"),
    ("class Diagnostic{static interface Nested{}}", "member-types", "source method results"),
    ("class Diagnostic{static enum Nested{VALUE}}", "member-types", "source method results"),
    ("class Diagnostic{static record Nested(){}}", "member-types", "source method results"),
    ("import static p.C.f; class Diagnostic{}", "static-imports", "static imports"),
    ("import p.*; class Diagnostic{}", "on-demand-imports", "on-demand imports"),
    ("import p.C; class Diagnostic{}", "source-dependency", "requires supplied source for p.C"),
    ("class Diagnostic{int run(){return 42;}}", "instance-methods", "only ordinary static source methods"),
    ("class Diagnostic{static synchronized int run(){return 42;}}", "special-method-modifiers", "only ordinary static source methods"),
    ("class Diagnostic{static native int run();}", "special-method-modifiers", "only ordinary static source methods"),
    ("class Diagnostic{static byte run(){return 42;}}", "declared-result-type", "source method results"),
    ("class Diagnostic{static Object run(){return null;}}", "declared-result-type", "source method results"),
    ("class Diagnostic{static int run(short a){return a;}}", "declared-parameter-type", "source parameters require"),
    ("class Diagnostic{static int[] values;}", "array-storage", "array fields require"),
    ("class Diagnostic{static int value;}", "static-initialization", "mutable static fields"),
    ("class Constants{static { int value=42; }static int run(){return 42;}}", "static-initialization", "source method results"),
    ("class Diagnostic{static final int value;}", "static-initialization", "blank final fields"),
    ("class Diagnostic{static final int value=f();static int f(){return 42;}}", "static-initialization", "nonconstant static initialization"),
    ("class Diagnostic{static final int value=Diagnostic.value;}", "cyclic-initialization", "cyclic field initialization"),
    (original("final int value;return 42;"), "blank-final-local", "blank final locals"),
    (original("final short value=42;return value;"), "declared-local-type", "final local declarations"),
    (original("final var value=42;return value;"), "declared-local-type", "final local declarations"),
    (original("float value=1;return 42;"), "declared-local-type", "unsupported Java expression"),
    (original("return 1/0;"), "arithmetic-exception", "ArithmeticException"),
    (original("return true?true:1;"), "reference-conditional", "reference/boxing conditional"),
    (original("return new Object();"), "object-expressions", "unsupported Java expression"),
    (original("return null;"), "object-expressions", "unsupported Java expression"),
    (original("return switch(1){default->42;};"), "switch-expressions", "unsupported Java expression"),
    (original("throw null;"), "runtime-statements", "unsupported Java expression"),
    (original("assert true;return 42;"), "assert-statements", "unsupported Java expression"),
    (original("class Local{}return 42;"), "local-types", "unsupported Java expression"),
    (original("return Diagnostic.Member.f();"), "member-types", "member types require"),
    (original("return missing.Package.f();"), "implicit-type-metadata", "complete implicit-type metadata"),
    ("package java.lang; class Object{}", "bootstrap-object", "bootstrap Object"),
]


@pytest.mark.parametrize("source,suffix,message", CAPABILITIES, ids=[row[1] + str(i) for i, row in enumerate(CAPABILITIES)])
def test_source_capability_has_explicit_code(source, suffix, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("capability reached lowering"))
    with pytest.raises(UnsupportedJava, match=message) as caught:
        compile_source(source, filename="Original.java")
    assert caught.value.code == "java.unsupported." + suffix
    assert caught.value.args == (str(caught.value),)
    assert caught.value.code not in str(caught.value)


@pytest.mark.parametrize("source,suffix,message", [
    ("class Diagnostic{final int x;}", "final-instance-fields", "final instance fields require"),
    ("class Diagnostic{static {}}", "static-initialization", "static initializer execution"),
    ("class Diagnostic{int hashCode(){return 42;}}", "object-inherited-methods", "Object override/final/access semantics"),
])
def test_semantic_model_capability_is_not_runtime_support(source, suffix, message):
    with pytest.raises(UnsupportedJava, match=message) as caught:
        analyze_class_sources({"Original.java": source})
    assert caught.value.code == "java.unsupported." + suffix


INVALID = [
    original("return #;"), original("return `;"), original("return ; +;"),
    original("return );"), original("return else;"), original("final return 42;"),
    "private class Diagnostic{}", "static class Diagnostic{}", "native class Diagnostic{}",
    "abstract final class Diagnostic{}", "class Diagnostic ;",
    "class Diagnostic{static abstract int run();}",
    "class Diagnostic{static return run(){return 42;}}",
    "class Diagnostic{static int run(void x){return 42;}}",
]


@pytest.mark.parametrize("source", INVALID)
def test_known_malformed_input_is_not_an_unimplemented_feature(source):
    with pytest.raises(JavaError) as caught:
        compile_source(source)
    assert type(caught.value) is JavaError
    assert caught.value.code is None
    if source == "class Diagnostic ;":
        assert str(caught.value) == "<java>:1:18: inheritance, generics and interfaces require the shared Java runtime"


def test_message_location_and_legacy_compatibility_contract():
    with pytest.raises(UnsupportedJava) as caught:
        Token("x", "Original.java", 7, 9).error("same text", code="java.unsupported.blank-final-local")
    assert str(caught.value) == "Original.java:7:9: same text"
    assert caught.value.args == ("Original.java:7:9: same text",)
    legacy = UnsupportedJava("frozen class-file message")
    assert legacy.code is None and legacy.args == ("frozen class-file message",)


def test_source_target_and_budget_codes(monkeypatch):
    from pcc.frontends.java import source, source_class_model
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(original("return 42;"), target="unknown-target")
    assert caught.value.code == "java.unsupported.target-abi"
    with pytest.raises(UnsupportedJava) as caught:
        tokenize(" " * 1_048_577, "Large.java")
    assert caught.value.code == "java.unsupported.source-size-limit"
    with pytest.raises(UnsupportedJava) as caught:
        tokenize(";" * 100_001, "Tokens.java")
    assert caught.value.code == "java.unsupported.token-limit"
    def recurse(*args):
        raise RecursionError("injected bounded parser exhaustion")
    monkeypatch.setattr(SourceParser, "parse", recurse)
    for invoke in (lambda: compile_source("class C{}"),
                   lambda: compile_sources({"C.java": "class C{}"}, entry_class="C"),
                   lambda: analyze_class_sources({"C.java": "class C{}"})):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.nesting-limit"


def test_all_source_capability_sites_and_ledger_are_bound():
    root = Path(__file__).resolve().parents[2]
    directory = root / "pcc/frontends/java"
    ledger = json.loads((root / "specs/features/java.json").read_text())
    checker = runpy.run_path(str(root / "scripts/language_feature_ledger.py"))
    checker["validate_ledger"](ledger)
    checker["audit_diagnostics"](ledger, root)
    records = {row["code"]: row for row in ledger["diagnostics"]["codes"]}
    found = set()
    for path in [*directory.glob("source*.py"), directory / "artifact.py"]:
        tree = ast.parse(path.read_bytes())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "UnsupportedJava":
                assert any(keyword.arg == "code" for keyword in node.keywords), path.name
            if isinstance(node.func, ast.Attribute) and node.func.attr == "error":
                assert len(node.args) == 1, "no boolean unsupported flags or implicit message classification"
            for keyword in node.keywords:
                if keyword.arg != "code" or not isinstance(keyword.value, ast.Constant):
                    continue
                code = keyword.value.value
                assert code in records, (path.name, code)
                # A code can be shared across source modules; source_sites
                # lists every caller, rather than attributing all to one file.
                identity = {"path": path.relative_to(root).as_posix(),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                assert identity in records[code]["source_sites"]
                found.add(code)
    assert found == set(records)
    assert ledger["diagnostics"]["message_only"] == []
    assert ledger["diagnostics"]["inventory_complete"] is False  # Compatibility is frozen separately.


def test_project_and_local_limits_have_capability_codes():
    # Synthetic budget pressure, not claims about valid large-program semantics.
    sources = {str(index): " " * 1_048_576 for index in range(9)}
    for invoke in (lambda: compile_sources(sources, entry_class="C"),
                   lambda: analyze_class_sources(sources)):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.project-size-limit"
    from pcc.frontends.java.source_sema import SourceAnalyzer
    program = SourceParser(original("return 42;"), "Original.java").parse()
    analyzer = SourceAnalyzer(program)
    analyzer.method = program.methods[0]
    analyzer.method.locals = [None] * 4096
    analyzer.scopes = [{}]
    with pytest.raises(UnsupportedJava) as caught:
        analyzer.declare(Token("extra", "Original.java", 1, 1))
    assert caught.value.code == "java.unsupported.local-variable-limit"


def test_artifact_startup_capabilities_preserve_messages(tmp_path):
    long_result = compile_source("class Diagnostic{static long run(){return 42;}}", descriptor="()J")
    with pytest.raises(UnsupportedJava, match="executable entry must have descriptor") as caught:
        long_result.write_executable(tmp_path / "not-emitted")
    assert caught.value.code == "java.unsupported.entry-descriptor"
    darwin = compile_source(original("return 42;"), target="arm64-apple-darwin")
    with pytest.raises(UnsupportedJava, match="Java executable startup") as caught:
        darwin.write_executable(tmp_path / "not-emitted")
    assert caught.value.code == "java.unsupported.executable-target"
    assert not (tmp_path / "not-emitted").exists()
