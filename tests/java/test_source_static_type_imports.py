"""Single-static member-type imports from complete supplied source units.

JLS21 6.3,6.4.1,6.5.2,6.6.1,7.5.1,7.5.3,8.5 and 13.1:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html#jls-7.5.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.4.1
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.5

These source, owned-native and same-original JDK obligations do not grant
on-demand imports, inherited types, managed execution or project acceptance.
"""
import hashlib
import json

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_sources
from pcc.frontends.java.source_project import SourceProject
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_project import unit
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, observer_source, record_process
from tests.java.test_source_scalars import NATIVE_REASON
from tests.java.test_source_static_imports import prepare_reference
from tests.java.test_source_static_member_classes import JDK_REASON, app


IMPORT = "import static provider.Outer.Inner;"
TYPE = "public static class Inner{public static int answer(){return 42;}}"


def project(imports=IMPORT, body="return Inner.answer();", declarations=TYPE, *, members=""):
    sources = app(imports, body, declarations)
    if members:
        sources["app/Main.java"] = unit("app", "Main", members + "public static int run(){" + body + "}",
                                         imports=imports)
    return sources


CASES = [
    ("public-member-type", project()),
    ("member-constant", project(body="return Inner.VALUE;",
                               declarations="public static class Inner{public static final int VALUE=42;}")),
    ("repeated-static-type", project(imports=IMPORT + IMPORT)),
    ("same-ordinary-then-static-type", project(imports="import provider.Outer.Inner;" + IMPORT)),
    ("same-static-then-ordinary-type", project(imports=IMPORT + "import provider.Outer.Inner;")),
    ("deep-canonical-owner", project(imports="import static provider.Outer.Middle.Inner;",
                                     declarations="public static class Middle{" + TYPE + "}")),
    ("imported-type-qualifies-deeper-type", project(body="return Inner.Deep.answer();",
        declarations="public static class Inner{public static class Deep{public static int answer(){return 42;}}}")),
    ("compilation-unit-scope-reaches-member", project(body="return Reader.read();",
        members="static class Reader{static int read(){return Inner.answer();}}")),
    ("lexical-type-shadows-static-import", project(declarations=TYPE.replace("42", "7"),
        members="static class Inner{static int answer(){return 42;}}")),
    ("nested-lexical-type-shadows-static-import", project(body="return Reader.read();",
        declarations=TYPE.replace("42", "7"), members="static class Reader{"
        "static int read(){return Inner.answer();}static class Inner{static int answer(){return 42;}}}")),
    ("method-name-does-not-obscure-imported-type", project(body="return Inner.answer()+Inner();",
        declarations=TYPE.replace("42", "40"), members="static int Inner(){return 2;}")),
    ("one-import-contributes-type-and-method", project(body="return Inner.answer()+Inner();",
        declarations=TYPE.replace("42", "40") + "public static int Inner(){return 2;}")),
    ("one-import-contributes-three-namespaces", project(body="return Inner+Inner();",
        declarations=TYPE + "public static final int Inner=40;public static int Inner(){return 2;}")),
    ("private-type-does-not-block-public-field", project(body="return Inner;",
        declarations="private static class Inner{}public static final int Inner=42;")),
    ("private-type-does-not-block-public-method", project(body="return Inner();",
        declarations="private static class Inner{}public static int Inner(){return 42;}")),
    ("private-field-does-not-obscure-public-type", project(
        declarations=TYPE + "private static final int Inner=7;")),
    ("private-method-does-not-obscure-public-type", project(
        declarations=TYPE + "private static int Inner(){return 7;}")),
    ("static-type-shadows-other-unit-same-package-type", {
        **project(), "app/Inner.java": unit("app", "Inner", "public static int answer(){return 7;}"),
    }),
    ("inaccessible-type-does-not-conflict-with-ordinary-import", {
        **project(imports=IMPORT + "import other.Inner;", body="return Inner.answer()+Inner();",
                  declarations="private static class Inner{}public static int Inner(){return 2;}"),
        "other/Inner.java": unit("other", "Inner", "public static int answer(){return 40;}"),
    }),
    ("self-public-type-import", {
        "app/Main.java": unit("app", "Main", "public static int run(){return Inner.answer();}" + TYPE,
                              imports="import static app.Main.Inner;"),
    }),
    ("unused-import-still-retains-all-methods", project(body="return 42;")),
    ("distinct-static-types-unused", {
        **project(imports=IMPORT + "import static second.Other.Inner;", body="return 42;"),
        "second/Other.java": unit("second", "Other", TYPE),
    }),
    ("distinct-types-with-accessible-value-names-unused", {
        **project(imports=IMPORT + "import static second.Other.Inner;", body="return 42;",
                  declarations=TYPE + "public static int Inner(){return 2;}"),
        "second/Other.java": unit("second", "Other", TYPE + "public static final int Inner=40;"),
    }),
    ("distinct-types-do-not-obscure-value-namespaces", {
        **project(imports=IMPORT + "import static second.Other.Inner;", body="return Inner+Inner();",
                  declarations=TYPE + "public static int Inner(){return 2;}"),
        "second/Other.java": unit("second", "Other", TYPE + "public static final int Inner=40;"),
    }),
    ("lexical-member-shadows-distinct-static-types", {
        **project(imports=IMPORT + "import static second.Other.Inner;",
                  members="static class Inner{static int answer(){return 42;}}"),
        "second/Other.java": unit("second", "Other", TYPE.replace("42", "7")),
    }),
]

# Retain the three exact previous capability-boundary inputs, including the
# unused import and its same-name scalar declarations, without rewriting them.
CASES += [
    ("former-boundary-" + name, app(IMPORT, "return 42;", "public static class Inner{}" + suffix))
    for name, suffix in (("type", ""), ("field", "public static final int Inner=7;"),
                         ("method", "public static int Inner(){return 7;}"))
]
CASES += [
    ("same-package-" + (modifier or "default") + "-type", {
        "app/Main.java": unit("app", "Main", "public static int run(){return Inner.answer();}",
                              imports="import static app.Outer.Inner;"),
        "app/Outer.java": unit("app", "Outer", modifier + " static class Inner{"
                               "public static int answer(){return 42;}}"),
    })
    for modifier in ("", "protected", "public")
]


def parsed_project(sources):
    return SourceProject([SourceParser(text, filename).parse() for filename, text in sources.items()])


@pytest.mark.parametrize("name,sources", CASES, ids=[row[0] for row in CASES])
def test_static_type_originals_retain_all_declarations_and_owned_ir(name, sources, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    before = dict(sources)
    forward = compile_sources(sources, entry_class="app.Main")
    backward = compile_sources(dict(reversed(tuple(sources.items()))), entry_class="app.Main")
    assert sources == before
    assert str(forward.module) == str(backward.module)
    required = {(program.name, method.token.text, method.descriptor)
                for program in parsed_project(sources).programs for method in program.methods}
    assert set(forward.emitted_class_methods) == required
    assert forward.emitted_class_methods == backward.emitted_class_methods
    assert "bc_" not in str(forward.module)
    assert forward.emit_object().startswith(b"\x7fELF")


def test_static_type_import_preserves_canonical_binary_and_namespace_identity():
    sources = project(imports=IMPORT + "import provider.Outer.Inner;" + IMPORT,
        declarations=TYPE + "public static final int Inner=40;public static int Inner(){return 2;}",
        body="return Inner+Inner();", members="static class Reader{static int unused(){return 7;}}")
    source = parsed_project(sources)
    target = source.classes["provider/Outer$Inner"]
    assert source.imports["app/Main"]["Inner"] is target
    assert source.static_types["app/Main"]["Inner"]["provider/Outer$Inner"] is target
    assert source.canonical_classes["provider/Outer/Inner"] is target
    assert source.imports["app/Main$Reader"] is source.imports["app/Main"]
    assert source.static_types["app/Main$Reader"] is source.static_types["app/Main"]
    provider = source.analyzers["provider/Outer"]
    assert source.static_fields["app/Main"]["Inner"]["provider/Outer"] is provider.fields["Inner"]
    assert source.static_methods["app/Main"]["Inner"]["provider/Outer", ()] is provider.methods["Inner", ()]
    tokens, repeated = source.classes["app/Main"].static_imports
    assert tuple(token.text for token in tokens) == ("provider", "Outer", "Inner")
    assert tokens[0].filename == "app/Main.java" and tokens[0].line == 1
    assert tokens[0].column < repeated[0].column


INVALID = [
    ("ambiguous-static-types-at-use", {
        **project(imports=IMPORT + "import static second.Other.Inner;"),
        "second/Other.java": unit("second", "Other", TYPE),
    }, "ambiguous single-static imported type"),
    ("conflicting-ordinary-static-types-unused", {
        **project(imports="import other.Inner;" + IMPORT, body="return 42;"),
        "other/Inner.java": unit("other", "Inner", ""),
    }, "conflicting single-static type import"),
    ("conflicting-static-ordinary-types-unused", {
        **project(imports=IMPORT + "import other.Inner;", body="return 42;"),
        "other/Inner.java": unit("other", "Inner", ""),
    }, "conflicting single-static type import"),
    ("same-package-type-does-not-resolve-static-import-ambiguity", {
        **project(imports=IMPORT + "import static second.Other.Inner;"),
        "second/Other.java": unit("second", "Other", TYPE),
        "app/Inner.java": unit("app", "Inner", "public static int answer(){return 42;}"),
    }, "ambiguous single-static imported type"),
    ("conflicting-top-level-type-unused", project(imports="import static provider.Outer.Main;", body="return 42;",
        declarations="public static class Main{}"), "conflicting single-static type import"),
    ("private-self-type-import", {
        "app/Main.java": unit("app", "Main", "public static int run(){return 42;}private static class Inner{}",
                              imports="import static app.Main.Inner;"),
    }, "no accessible static member"),
    ("public-type-in-hidden-owner", app(IMPORT, "return 42;", TYPE, public=False), "source class is not accessible"),
    ("public-type-in-hidden-intermediate-owner", project(imports="import static provider.Outer.Middle.Inner;",
        body="return 42;", declarations="private static class Middle{" + TYPE + "}"), "source class is not accessible"),
    ("local-obscures-imported-type", project(body="int Inner=42;return Inner.answer();"), "cannot be dereferenced"),
    ("field-obscures-imported-type", project(members="static final int Inner=42;"), "cannot be dereferenced"),
    ("imported-field-obscures-imported-type", project(declarations=TYPE + "public static final int Inner=42;"),
        "cannot be dereferenced"),
    ("inaccessible-type-not-rescued-by-public-method", project(
        declarations=TYPE.replace("public static class", "private static class") + "public static int Inner(){return 42;}"),
        "unresolved source class qualifier"),
    ("uncalled-imported-type-body-still-checked", project(body="return 42;",
        declarations="public static class Inner{public static int unused(){int n;return n;}}"), "not definitely assigned"),
]
INVALID += [
    ("cross-package-" + (modifier or "default") + "-type-unused", project(body="return 42;",
        declarations=modifier + " static class Inner{}"), "no accessible static member")
    for modifier in ("private", "protected", "")
]


@pytest.mark.parametrize("name,sources,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_static_type_originals_fail_before_lowering(name, sources, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("invalid import reached lowering"))
    before = dict(sources)
    for inputs in (sources, dict(reversed(tuple(sources.items())))):
        with pytest.raises(JavaError, match=message) as caught:
            compile_sources(inputs, entry_class="app.Main")
        assert type(caught.value) is JavaError and caught.value.code is None
    assert sources == before


@pytest.mark.parametrize("imports,code", [
    ("import static absent.Outer.Inner;", "source-dependency"),
    ("import static provider.Outer$Middle.Inner;", "source-dependency"),
    ("import provider.Outer.*;", "on-demand-imports"),
    ("import static provider.Outer.*;", "static-imports"),
])
def test_static_type_support_preserves_missing_dependency_and_demand_boundaries(imports, code):
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(project(imports=imports, body="return 42;"), entry_class="app.Main")
    assert caught.value.code == "java.unsupported." + code


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources", CASES, ids=[row[0] for row in CASES])
def test_same_static_type_originals_execute_owned_native_bytes(name, sources, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class="app.Main")
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42
    assert (tmp_path / "observed.stdout.bin").read_bytes() == b"\x2a\x00\x00\x00"
    assert (tmp_path / "observed.stderr.bin").read_bytes() == b""


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-static-type-import-reference")
@pytest.mark.parametrize("name,sources", CASES, ids=[row[0] for row in CASES])
def test_same_static_type_originals_match_jdk_raw_processes(name, sources, tmp_path, request, monkeypatch):
    native = tmp_path / "native"
    native.mkdir()
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class="app.Main")
    assert observe_scalar(compiled, native, monkeypatch) == 42
    actual = record_process((str(native / "observed"),), native, "raw-native")
    java, classes, compiler = prepare_reference(sources, tmp_path, request,
        observer=observer_source("app.Main", "I", ()))
    assert compiler[0] == 0, compiler
    reference = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccRawObserve"), tmp_path, "raw-reference")
    (tmp_path / "comparison.json").write_text(json.dumps({
        "case": name, "source_inputs": {path: hashlib.sha256(text.encode()).hexdigest() for path, text in sources.items()},
        "source_rewritten": False, "comparison": "raw_process_streams",
        "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
        "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
        "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
    }, indent=2) + "\n")
    assert actual == reference == (0, b"\x2a\x00\x00\x00", b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-type-import-reference")
@pytest.mark.parametrize("name,sources,message", INVALID, ids=[row[0] for row in INVALID])
def test_same_invalid_static_type_originals_have_jdk_rejections(name, sources, message, tmp_path, request):
    _, _, compiler = prepare_reference(sources, tmp_path, request)
    assert compiler[0] == 1, compiler
    assert b".java:" in compiler[2]
