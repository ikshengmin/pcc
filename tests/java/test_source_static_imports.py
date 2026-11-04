"""Single-static imports from complete supplied source units, not a classpath.

JLS21 6.4.1,6.5.6.1,6.6.1,7.5.3,15.12.1-2:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html#jls-7.5.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.4.1
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.12.1
All source, native and same-original JDK obligations are authored for CI, UNRUN.
No source rewriting, missing-library stand-in or managed-runtime acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import (
    JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources,
)
from pcc.frontends.java.source_project import OBJECT_INHERITED_METHOD_NAMES, SourceProject
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_project import unit
from tests.java.test_source_raw_reference import (
    JVM_LIMITS, digest, observer_source, record_process, tool_identity,
)
from tests.java.test_source_scalars import NATIVE_REASON
from tests.java.test_source_unicode_escapes import JDK_REASON


def project(imports, body, providers, *, members="", result="int"):
    return {
        "app/Main.java": unit("app", "Main", members + "public static " + result
                              + " run(){" + body + "}", imports=imports),
        **{name.replace(".", "/") + ".java": unit(*name.rsplit(".", 1), declarations)
           for name, declarations in providers.items()},
    }


FIELD = {"provider.Values": "public static final int VALUE=42;"}
TWO_FIELDS = {"provider.Values": "public static final int VALUE=7;",
              "other.Values": "public static final int VALUE=9;"}
AMBIGUOUS_FIELDS = "import static provider.Values.VALUE;import static other.Values.VALUE;"
INHERITED_NAMES = ("clone", "equals", "finalize", "getClass", "hashCode", "notify", "notifyAll", "toString", "wait")
CASES = [
    ("constant-field", project("import static provider.Values.VALUE;", "return VALUE;", FIELD), "I", 42),
    ("constant-in-initializer", project("import static provider.Values.VALUE;", "return COPY;", FIELD,
                                      members="static final int COPY=VALUE;"), "I", 42),
    ("constant-switch", project("import static provider.Values.VALUE;",
                               "switch(42){case VALUE:return VALUE;default:return 0;}", FIELD), "I", 42),
    ("same-name-field-and-method", project("import static provider.Values.answer;",
                                         "return answer+answer();",
                                         {"provider.Values": "public static final int answer=40;"
                                                             "public static int answer(){return 2;}"}), "I", 42),
    ("duplicate-imports", project("import static provider.Values.VALUE;import static provider.Values.VALUE;"
                                  "import static provider.Values.f;import static provider.Values.f;",
                                  "return VALUE+f(2);",
                                  {"provider.Values": "public static final int VALUE=40;"
                                                      "public static int f(int n){return n;}"}), "I", 42),
    ("cross-owner-overloads", project("import static provider.A.f;import static other.B.f;",
                                      "return f(1)+f(1L);",
                                      {"provider.A": "public static int f(int n){return 40;}",
                                       "other.B": "public static int f(long n){return 2;}"}), "I", 42),
    ("all-imported-overloads", project("import static provider.Values.f;", "return f(1)+f(1L);",
                                      {"provider.Values": "public static int f(int n){return 40;}"
                                                          "public static int f(long n){return 2;}"}), "I", 42),
    ("inaccessible-narrow-overload", project("import static provider.Values.f;", "return f(1);",
                                            {"provider.Values": "private static int f(int n){return 7;}"
                                                                "public static int f(long n){return 42;}"}), "I", 42),
    ("unused-field-conflict", project(AMBIGUOUS_FIELDS, "return 42;", TWO_FIELDS), "I", 42),
    ("member-shadows-field-conflict", project(AMBIGUOUS_FIELDS, "return VALUE;", TWO_FIELDS,
                                            members="static final int VALUE=42;"), "I", 42),
    ("local-shadows-field-conflict", project(AMBIGUOUS_FIELDS, "int VALUE=42;return VALUE;", TWO_FIELDS), "I", 42),
    ("parameter-shadows-field-conflict", project(AMBIGUOUS_FIELDS, "return take(42);", TWO_FIELDS,
                                               members="static int take(int VALUE){return VALUE;}"), "I", 42),
    ("own-method-shadows-import", project("import static provider.Values.f;", "return f();",
                                         {"provider.Values": "public static int f(int n){return 7;}"},
                                         members="static int f(){return 42;}"), "I", 42),
    ("local-value-does-not-shadow-method", project("import static provider.Values.f;",
                                                  "int f=40;return f(f);",
                                                  {"provider.Values": "public static int f(int n){return n+2;}"}), "I", 42),
    ("method-does-not-obscure-type", project("import provider.Values;import static provider.Factory.Values;",
                                            "return Values.VALUE+Values();",
                                            {"provider.Values": "public static final int VALUE=40;",
                                             "provider.Factory": "public static int Values(){return 2;}"}), "I", 42),
    ("same-package-protected", project("import static app.Values.f;", "return f();",
                                       {"app.Values": "protected static int f(){return 42;}"}), "I", 42),
    ("same-package-default", project("import static app.Values.VALUE;", "return VALUE;",
                                     {"app.Values": "static final int VALUE=42;"}), "I", 42),
    ("public-self-import", project("import static app.Main.answer;", "return answer();", {},
                                   members="public static int answer(){return 42;}"), "I", 42),
    ("qualified-inherited-name-control", project("import provider.Values;import static provider.Values.hashCode;",
                                                "return Values.hashCode(1);",
                                                {"provider.Values": "public static int hashCode(int n){return 42;}"}), "I", 42),
    ("own-inherited-name-overload-control", project("import static provider.Values.hashCode;", "return hashCode(1);",
                                                   {"provider.Values": "public static int hashCode(int n){return 7;}"},
                                                   members="static int hashCode(int n){return 42;}"), "I", 42),
    ("long-constant-width", project("import static provider.Values.MIN;", "return MIN;",
                                    {"provider.Values": "public static final long MIN=-9223372036854775808L;"},
                                    result="long"), "J", -9223372036854775808),
]


INVALID = [
    ("missing-unused-member", project("import static provider.Values.missing;", "return 42;", FIELD),
     "no accessible static member", "cannot find symbol"),
    ("private-unused-member", project("import static provider.Values.VALUE;", "return 42;",
                                      {"provider.Values": "private static final int VALUE=42;"}),
     "no accessible static member", "cannot find symbol|private access"),
    ("private-self-import", project("import static app.Main.answer;", "return 42;", {},
                                   members="private static int answer(){return 42;}"),
     "no accessible static member", "cannot find symbol|private access"),
    ("package-member-cross-package", project("import static provider.Values.VALUE;", "return 42;",
                                            {"provider.Values": "static final int VALUE=42;"}),
     "no accessible static member", "cannot find symbol|cannot be accessed"),
    ("protected-member-cross-package", project("import static provider.Values.f;", "return 42;",
                                              {"provider.Values": "protected static int f(){return 42;}"}),
     "no accessible static member", "cannot find symbol|protected access"),
    ("ambiguous-field", project(AMBIGUOUS_FIELDS, "return VALUE;", TWO_FIELDS),
     "ambiguous single-static imported field", "reference to VALUE is ambiguous"),
    ("ambiguous-same-signature", project("import static provider.A.f;import static other.B.f;", "return f(1);",
                                         {"provider.A": "public static int f(int n){return 42;}",
                                          "other.B": "public static int f(int n){return 7;}"}),
     "ambiguous primitive method", "reference to f is ambiguous"),
    ("ambiguous-incomparable-overloads", project("import static provider.A.f;import static other.B.f;",
                                                "return f(1,1);",
                                                {"provider.A": "public static int f(int a,long b){return 42;}",
                                                 "other.B": "public static int f(long a,int b){return 7;}"}),
     "ambiguous primitive method", "reference to f is ambiguous"),
    ("own-wrong-arity-still-shadows", project("import static provider.Values.f;", "return f(1);",
                                            {"provider.Values": "public static int f(int n){return 42;}"},
                                            members="static int f(){return 7;}"),
     "unresolved same-class static method", "cannot be applied"),
    ("own-wrong-type-still-shadows", project("import static provider.Values.f;", "return f(1);",
                                           {"provider.Values": "public static int f(int n){return 42;}"},
                                           members="static int f(boolean n){return 7;}"),
     "unresolved same-class static method", "incompatible types|cannot be applied"),
    ("unassigned-local-shadows-import", project("import static provider.Values.VALUE;",
                                               "int VALUE;return VALUE;", FIELD),
     "not definitely assigned", "might not have been initialized"),
    ("own-forward-field-shadows-import", project("import static provider.Values.VALUE;", "return COPY;", FIELD,
                                                members="static final int COPY=VALUE;static final int VALUE=42;"),
     "illegal forward", "illegal forward reference"),
    ("field-obscures-type", project("import provider.Values;import static provider.Other.Values;",
                                    "return Values.VALUE;",
                                    {"provider.Values": "public static final int VALUE=42;",
                                     "provider.Other": "public static final int Values=7;"}),
     "cannot be dereferenced", "int cannot be dereferenced"),
    ("field-import-does-not-import-method", project("import static provider.Values.VALUE;", "return VALUE();", FIELD),
     "unresolved same-class static method", "cannot find symbol"),
    ("method-import-does-not-import-type", project("import static provider.Values.f;", "return Values.f();",
                                                  {"provider.Values": "public static int f(){return 42;}"}),
     "unresolved source class qualifier", "cannot find symbol"),
    ("method-import-does-not-import-field", project("import static provider.Values.f;", "return f;",
                                                   {"provider.Values": "public static int f(){return 42;}"}),
     "undefined local variable or constant field", "cannot find symbol"),
]
INVALID += [
    ("final-import-write-" + str(index), project("import static provider.Values.VALUE;", body, FIELD),
     "cannot assign a final", r"cannot assign a value to (?:static )?final variable VALUE\b")
    for index, body in enumerate(("VALUE=1;return VALUE;", "VALUE+=1;return VALUE;",
                                  "VALUE++;return VALUE;", "return ++VALUE;", "return (VALUE=1);"))
]
INVALID.append((
    "inaccessible-owner", {
        "app/Main.java": unit("app", "Main", "public static int run(){return 42;}",
                              imports="import static provider.Values.VALUE;"),
        "provider/Values.java": unit("provider", "Values", "public static final int VALUE=42;", False),
    }, "source class is not accessible", "cannot be accessed",
))


@pytest.mark.parametrize("name,sources,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_exact_static_import_originals_keep_source_identity_and_direct_ir(name, sources, lane, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    original = dict(sources)
    forward = compile_sources(sources, entry_class="app.Main", descriptor="()" + lane)
    backward = compile_sources(dict(reversed(tuple(sources.items()))), entry_class="app.Main", descriptor="()" + lane)
    assert sources == original and str(forward.module) == str(backward.module)
    assert forward.emitted_class_methods == backward.emitted_class_methods
    assert forward.emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_exact_static_import_originals_execute_owned_native_bytes(name, sources, lane, expected, tmp_path, monkeypatch):
    original = dict(sources)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class="app.Main", descriptor="()" + lane)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
    assert (tmp_path / "observed.stdout.bin").read_bytes() == expected.to_bytes(8 if lane == "J" else 4, "little", signed=True)
    assert (tmp_path / "observed.stderr.bin").read_bytes() == b""
    assert sources == original


@pytest.mark.parametrize("name,sources,message,jdk_message", INVALID, ids=[row[0] for row in INVALID])
def test_exact_invalid_static_imports_fail_before_lowering(name, sources, message, jdk_message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("invalid import reached lowering"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_sources(sources, entry_class="app.Main")
    assert type(caught.value) is JavaError and caught.value.code is None


def test_static_import_tables_preserve_declaration_identity_and_all_overloads():
    sources = project("import static provider.Values.f;import static provider.Values.f;"
                      "import static other.Values.f;", "return 42;",
                      {"provider.Values": "public static int f(int n){return 42;}"
                                          "public static int f(long n){return 7;}",
                       "other.Values": "public static int f(int n){return 3;}"})
    programs = [SourceParser(text, filename).parse() for filename, text in sources.items()]
    analyzed = SourceProject(programs).analyze()
    table = analyzed.static_methods["app/Main"]["f"]
    assert set(table) == {("provider/Values", ("int",)), ("provider/Values", ("long",)), ("other/Values", ("int",))}
    for (owner, parameters), method in table.items():
        assert method is analyzed.analyzers[owner].methods["f", parameters]
    assert [tuple(token.text for token in tokens) for tokens in programs[0].static_imports] == [
        ("provider", "Values", "f"), ("provider", "Values", "f"), ("other", "Values", "f"),
    ]


@pytest.mark.parametrize("member", ["public int value;", "public int value(){return 42;}"])
def test_model_instance_members_are_not_static_import_candidates(member):
    sources = project("import static provider.Values.value;", "return 42;", {"provider.Values": member})
    with pytest.raises(JavaError, match="no accessible static member") as caught:
        analyze_class_sources(sources)
    assert type(caught.value) is JavaError


def test_imported_model_string_calls_retain_callers_missing_runtime_contract(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    sources = project("import static provider.Values.take;", "return take(null);",
                      {"provider.Values": "public static int take(String value){return 42;}"})
    originals = dict(sources)
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("unresolved String contract reached native IR"))
    model = analyze_class_sources(sources)
    obligation, = model.models["app/Main"].compiler_context_obligations
    assert obligation.type_name == "java/lang/String"
    assert set(obligation.requirements) == {"owned-bootstrap-type-definition", "shared-owning-root-and-safepoint-ABI"}
    assert not model.models["app/Main"].runtime_registered and not model.semantic_context_complete
    # Native project parsing uses sorted diagnostic filenames, so the exact
    # caller's null token is encountered before the provider's String type.
    for inputs in (sources, dict(reversed(tuple(sources.items())))):
        with pytest.raises(UnsupportedJava) as caught:
            compile_sources(inputs, entry_class="app.Main")
        assert caught.value.code == "java.unsupported.object-expressions"
        assert str(caught.value).startswith("app/Main.java:1:" + str(sources["app/Main.java"].index("null") + 1) + ":")
    # The unmodified provider has its own later boundary when checked alone;
    # this is not substituted for the complete project's first diagnostic.
    with pytest.raises(UnsupportedJava) as provider:
        compile_source(sources["provider/Values.java"], filename="provider/Values.java")
    assert provider.value.code == "java.unsupported.declared-parameter-type"
    assert str(provider.value).startswith("provider/Values.java:1:" + str(sources["provider/Values.java"].index("String") + 1) + ":")
    assert sources == originals


@pytest.mark.parametrize("imports,code", [
    ("import static absent.Values.VALUE;", "source-dependency"),
    ("import static provider.Values.*;", "static-imports"),
    ("import provider.*;", "on-demand-imports"),
])
def test_unavailable_import_domains_are_not_satisfied_by_host_metadata(imports, code):
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(project(imports, "return 42;", FIELD), entry_class="app.Main")
    assert caught.value.code == "java.unsupported." + code


def test_import_owner_is_canonical_and_cannot_use_another_import_alias():
    sources = project("import provider.Values;import static Values.VALUE;", "return 42;", FIELD)
    with pytest.raises(JavaError, match="unnamed package") as caught:
        compile_sources(sources, entry_class="app.Main")
    assert type(caught.value) is JavaError


def test_unnamed_package_static_owner_is_not_importable():
    sources = {"Main.java": "import static Values.VALUE;class Main{static int run(){return 42;}}",
               "Values.java": "class Values{public static final int VALUE=42;}"}
    with pytest.raises(JavaError, match="unnamed package"):
        compile_sources(sources, entry_class="Main")


def test_single_source_entrypoint_uses_the_same_self_import_rules():
    text = "package app;import static app.Main.answer;class Main{static int answer(){return 42;}static int run(){return answer();}}"
    assert "ret i32 42" in str(compile_source(text).module)


def inherited_name_project(name):
    return project("import static provider.Values." + name + ";", "return " + name + "(1);",
                   {"provider.Values": "public static int " + name + "(int n){return 42;}"})


@pytest.mark.parametrize("name", INHERITED_NAMES)
def test_inherited_object_names_cannot_be_bypassed_by_imported_overloads(name, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("inherited lookup reached lowering"))
    assert OBJECT_INHERITED_METHOD_NAMES == frozenset(INHERITED_NAMES)
    for invoke in (compile_sources, analyze_class_sources):
        with pytest.raises(UnsupportedJava) as caught:
            options = {"entry_class": "app.Main"} if invoke is compile_sources else {}
            invoke(inherited_name_project(name), **options)
        assert caught.value.code == "java.unsupported.object-inherited-methods"


def prepare_reference(sources, directory, request, *, observer=None):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    release = (java.parent.parent / "release").read_text()
    version = re.search(r'^JAVA_VERSION="([^"]+)"', release, re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    before = tool_identity(java)
    originals = []
    for filename, text in sources.items():
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = text.encode("utf-8")
        path.write_bytes(raw)
        originals.append((path, raw))
    if observer is not None:
        path = directory / "PccRawObserve.java"
        raw = observer.encode("utf-8")
        path.write_bytes(raw)
        originals.append((path, raw))
    source_hash = digest(Path(__file__))
    identities = {str(path.relative_to(directory)): hashlib.sha256(raw).hexdigest() for path, raw in originals}
    owner = {"owner": "explicit existing JDK, never the PCC production owner", "version": version.group(1),
             "source_level": level, "target_level": level, "tools_before": before,
             "test_source_sha256": source_hash, "original_and_observer_sha256": identities,
             "source_rewritten": False, "managed_runtime_qualified": False}
    (directory / "reference-owner-before.json").write_text(json.dumps(owner, indent=2) + "\n")

    def finish():
        after = tool_identity(java)
        unchanged = all(path.read_bytes() == raw for path, raw in originals)
        (directory / "reference-owner-after.json").write_text(json.dumps({
            **owner, "tools_after": after, "originals_and_observer_unchanged": unchanged,
        }, indent=2) + "\n")
        assert after == before and unchanged and digest(Path(__file__)) == source_hash

    request.addfinalizer(finish)
    assert record_process((str(java), *JVM_LIMITS, "--version"), directory, "version")[0] == 0
    classes = directory / "classes"
    classes.mkdir()
    actual = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
                             "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
                             "-classpath", str(classes), "-d", str(classes),
                             *(str(path) for path, _ in originals)), directory, "compiler", 20)
    return java, classes, actual


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-static-import-reference")
@pytest.mark.parametrize("name,sources,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_exact_static_import_originals_match_jdk_raw_processes(name, sources, lane, expected, tmp_path, request, monkeypatch):
    native = tmp_path / "native"
    native.mkdir()
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class="app.Main", descriptor="()" + lane)
    assert observe_scalar(compiled, native, monkeypatch) == expected
    actual = record_process((str(native / "observed"),), native, "raw-native")
    java, classes, compiler = prepare_reference(sources, tmp_path, request,
                                                observer=observer_source("app.Main", lane, ()))
    assert compiler[0] == 0, compiler
    reference = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccRawObserve"),
                               tmp_path, "raw-reference")
    expected_bytes = expected.to_bytes(8 if lane == "J" else 4, "little", signed=True)
    (tmp_path / "comparison.json").write_text(json.dumps({
        "case": name, "source_inputs": {path: hashlib.sha256(text.encode()).hexdigest() for path, text in sources.items()},
        "source_rewritten": False, "comparison": "raw_process_streams",
        "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
        "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
        "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
    }, indent=2) + "\n")
    assert actual == reference == (0, expected_bytes, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-import-reference")
@pytest.mark.parametrize("name,sources,message,jdk_message", INVALID, ids=[row[0] for row in INVALID])
def test_exact_invalid_static_imports_match_jdk_rejections(name, sources, message, jdk_message, tmp_path, request):
    _, _, actual = prepare_reference(sources, tmp_path, request)
    assert actual[0] == 1, actual
    diagnostic = actual[2].decode("utf-8")
    assert any(Path(filename).name + ":" in diagnostic for filename in sources)
    assert re.search(jdk_message, diagnostic), diagnostic


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-import-reference")
@pytest.mark.parametrize("name", INHERITED_NAMES)
def test_inherited_name_import_originals_have_independent_jdk_rejections(name, tmp_path, request):
    _, _, actual = prepare_reference(inherited_name_project(name), tmp_path, request)
    assert actual[0] == 1, actual
    diagnostic = actual[2].decode("utf-8")
    assert "Main.java:" in diagnostic
    assert re.search("cannot be applied|non-static method|incompatible types|unreported exception", diagnostic), diagnostic
