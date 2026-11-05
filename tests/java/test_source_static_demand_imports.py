"""Static field/method on-demand imports from complete supplied source units.

JLS21 6.3,6.4.1,6.5.6.1,6.6.1,7.5.4 and 15.12.1-2:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html#jls-7.5.4
https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.4.1
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.12

Single method imports shadow matching signatures. Mixed unshadowed overloads
remain an explicit frontier because the selected JDK21 disagrees with the JLS.
On-demand types, inherited declarations and managed execution stay separate.
All original maps are shared unchanged with actual native/JDK obligations.
"""
import hashlib
import json

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_sources
from pcc.frontends.java.source_project import SourceProject
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_project import unit
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, observer_source, record_process
from tests.java.test_source_scalars import NATIVE_REASON
from tests.java.test_source_static_imports import FIELD, ON_DEMAND_ORIGINAL, prepare_reference, project
from tests.java.test_source_static_member_classes import (
    JDK_REASON,
    NESTED_DEMAND_ORIGINAL,
)


IMPORT = "import static provider.Values.*;"
TWO_IMPORTS = "import static provider.A.*;import static other.B.*;"
FIELDS = {"provider.A": "public static final int VALUE=7;",
          "other.B": "public static final int VALUE=9;"}
METHODS = {"provider.A": "public static int f(int n){return 7;}",
           "other.B": "public static int f(int n){return 9;}"}
CASES = [
    ("former-unused-boundary", ON_DEMAND_ORIGINAL),
    ("former-unused-nested-owner-boundary", NESTED_DEMAND_ORIGINAL),
    ("field", project(IMPORT, "return VALUE;", FIELD)),
    ("constant-in-initializer", project(IMPORT, "return COPY;", FIELD,
                                       members="static final int COPY=VALUE;")),
    ("constant-switch", project(IMPORT, "switch(42){case VALUE:return VALUE;default:return 0;}", FIELD)),
    ("same-name-field-and-method", project(IMPORT, "return answer+answer();",
        {"provider.Values": "public static final int answer=40;public static int answer(){return 2;}"})),
    ("duplicate-demand-and-single", project(IMPORT + IMPORT + "import static provider.Values.f;",
        "return VALUE+f();", {"provider.Values": "public static final int VALUE=40;public static int f(){return 2;}"})),
    ("all-owner-overloads", project(IMPORT, "return f(1)+f(1L);",
        {"provider.Values": "public static int f(int n){return 40;}public static int f(long n){return 2;}"})),
    ("cross-owner-overloads", project(TWO_IMPORTS, "return f(1)+f(1L);",
        {"provider.A": "public static int f(int n){return 40;}",
         "other.B": "public static int f(long n){return 2;}"})),
    ("unused-field-and-method-ambiguity", project(TWO_IMPORTS, "return 42;",
        {name: FIELDS[name] + METHODS[name] for name in FIELDS})),
    ("single-field-shadows-demand-conflict", project(TWO_IMPORTS + "import static chosen.C.VALUE;",
        "return VALUE;", {**FIELDS, "chosen.C": "public static final int VALUE=42;"})),
    ("single-field-before-demand", project("import static chosen.C.VALUE;" + TWO_IMPORTS,
        "return VALUE;", {**FIELDS, "chosen.C": "public static final int VALUE=42;"})),
    ("single-signature-shadows-demand-conflict", project(TWO_IMPORTS + "import static chosen.C.f;",
        "return f(1);", {**METHODS, "chosen.C": "public static int f(int n){return 42;}"})),
    ("unused-mixed-overloads", project(IMPORT + "import static chosen.C.f;", "return 42;",
        {"provider.Values": "public static int f(long n){return 7;}",
         "chosen.C": "public static int f(int n){return 9;}"})),
    ("lexical-method-shadows-mixed-overloads", project(IMPORT + "import static chosen.C.f;", "return f();",
        {"provider.Values": "public static int f(long n){return 7;}",
         "chosen.C": "public static int f(int n){return 9;}"}, members="static int f(){return 42;}")),
    ("single-field-keeps-demand-method", project(IMPORT + "import static chosen.C.answer;",
        "return answer+answer();", {"provider.Values": "public static int answer(){return 2;}",
                                    "chosen.C": "public static final int answer=40;"})),
    ("single-method-keeps-demand-field", project(IMPORT + "import static chosen.C.answer;",
        "return answer+answer();", {"provider.Values": "public static final int answer=40;",
                                    "chosen.C": "public static int answer(){return 2;}"})),
    ("member-field-shadows-demand-conflict", project(TWO_IMPORTS, "return VALUE;", FIELDS,
        members="static final int VALUE=42;")),
    ("local-shadows-demand-conflict", project(TWO_IMPORTS, "int VALUE=42;return VALUE;", FIELDS)),
    ("parameter-shadows-demand-conflict", project(TWO_IMPORTS, "return take(42);", FIELDS,
        members="static int take(int VALUE){return VALUE;}")),
    ("member-method-shadows-demand-conflict", project(TWO_IMPORTS, "return f();", METHODS,
        members="static int f(){return 42;}")),
    ("private-narrow-overload-ignored", project(IMPORT, "return f(1);",
        {"provider.Values": "private static int f(int n){return 7;}public static int f(long n){return 42;}"})),
    ("package-access-import", project("import static app.Values.*;", "return VALUE+f();",
        {"app.Values": "static final int VALUE=40;static int f(){return 2;}"})),
    ("protected-same-package-import", project("import static app.Values.*;", "return f();",
        {"app.Values": "protected static int f(){return 42;}"})),
    ("empty-owner-import", project(IMPORT, "return 42;", {"provider.Values": ""})),
    ("inaccessible-members-unused", project(IMPORT, "return 42;",
        {"provider.Values": "private static final int VALUE=7;private static int f(){return 9;}"})),
    ("private-type-does-not-block-value-import", project(IMPORT, "return VALUE;",
        {"provider.Values": "private static class Hidden{}public static final int VALUE=42;"})),
    ("canonical-nested-owner", project("import static provider.Outer.Tools.*;", "return VALUE+f();",
        {"provider.Outer": "public static class Tools{public static final int VALUE=40;public static int f(){return 2;}}"})),
    ("compilation-unit-scope-reaches-member", project(IMPORT, "return Reader.read();", FIELD,
        members="static class Reader{static int read(){return VALUE;}}")),
    ("outer-method-shadows-import-in-member", project(IMPORT, "return Reader.read();",
        {"provider.Values": "public static int f(){return 7;}"},
        members="static int f(){return 42;}static class Reader{static int read(){return f();}}")),
    ("self-import-excludes-private-but-keeps-lexical-scope", project("import static app.Main.*;",
        "return VALUE;", {}, members="private static final int VALUE=42;")),
    ("void-import", project(IMPORT, "ping();return 42;", {"provider.Values": "public static void ping(){}"})),
]


# Exact r1 originals retained. JLS 6.4.1 permits both overloads, while the
# selected JDK21 has rejected the first. Keep both normative conformance
# expectations visible, never rewrite originals or change the oracle.
MIXED_METHOD_FRONTIERS = [
    ("single-narrow-keeps-demand-wide", project(IMPORT + "import static chosen.C.f;", "return f(1)+f(1L);",
        {"provider.Values": "public static int f(int n){return 7;}public static int f(long n){return 2;}",
         "chosen.C": "public static int f(int n){return 40;}"})),
    ("single-wide-keeps-demand-narrow", project("import static chosen.C.f;" + IMPORT, "return f(1)+f(1L);",
        {"provider.Values": "public static int f(int n){return 40;}public static int f(long n){return 7;}",
         "chosen.C": "public static int f(long n){return 2;}"})),
]


INVALID = [
    ("ambiguous-demand-field", project(TWO_IMPORTS, "return VALUE;", FIELDS), "ambiguous static on-demand imported field"),
    ("ambiguous-demand-signature", project(TWO_IMPORTS, "return f(1);", METHODS), "ambiguous primitive method"),
    ("ambiguous-cross-signatures", project(TWO_IMPORTS, "return f(1,1);",
        {"provider.A": "public static int f(int a,long b){return 42;}",
         "other.B": "public static int f(long a,int b){return 7;}"}), "ambiguous primitive method"),
    ("single-signature-still-ambiguous", project(IMPORT + "import static provider.A.f;import static other.B.f;",
        "return f(1);", {**METHODS, "provider.Values": "public static int f(int n){return 42;}"}),
        "ambiguous primitive method"),
    ("member-wrong-arity-still-shadows", project(IMPORT, "return f(1);",
        {"provider.Values": "public static int f(int n){return 42;}"}, members="static int f(){return 7;}"),
        "unresolved same-class static method"),
    ("unassigned-local-shadows-demand", project(IMPORT, "int VALUE;return VALUE;", FIELD), "not definitely assigned"),
    ("private-field-not-imported", project(IMPORT, "return VALUE;",
        {"provider.Values": "private static final int VALUE=42;"}), "undefined local variable or constant field"),
    ("private-method-not-imported", project(IMPORT, "return f();",
        {"provider.Values": "private static int f(){return 42;}"}), "unresolved same-class static method"),
    ("package-member-not-imported-across-package", project(IMPORT, "return VALUE;",
        {"provider.Values": "static final int VALUE=42;"}), "undefined local variable or constant field"),
    ("protected-method-not-imported-across-package", project(IMPORT, "return f();",
        {"provider.Values": "protected static int f(){return 42;}"}), "unresolved same-class static method"),
    ("import-does-not-bind-owner-type", project(IMPORT, "return Values.VALUE;", FIELD), "unresolved source class qualifier"),
    ("field-obscures-single-imported-type", project("import provider.Values;import static provider.Other.*;",
        "return Values.VALUE;", {**FIELD, "provider.Other": "public static final int Values=7;"}), "cannot be dereferenced"),
    ("private-nested-owner", project("import static provider.Outer.Hidden.*;", "return 42;",
        {"provider.Outer": "private static class Hidden{public static int f(){return 42;}}"}), "source class is not accessible"),
    ("noncanonical-alias-owner", project("import provider.Values;import static Values.*;", "return 42;", FIELD), "unnamed package"),
    ("star-is-terminal", project("import static provider.Values.*.VALUE;", "return 42;", FIELD), "expected ';'"),
    ("uncalled-owner-body-still-checked", project(IMPORT, "return 42;",
        {"provider.Values": "public static int bad(){int x;return x;}"}), "not definitely assigned"),
    ("import-does-not-leak-to-neighbor-unit", {
        **project(IMPORT, "return Other.read();", FIELD),
        "app/Other.java": unit("app", "Other", "static int read(){return VALUE;}"),
    }, "undefined local variable or constant field"),
    ("inaccessible-owner", {
        "app/Main.java": unit("app", "Main", "public static int run(){return 42;}", imports=IMPORT),
        "provider/Values.java": unit("provider", "Values", "public static final int VALUE=42;", False),
    }, "source class is not accessible"),
]

TYPE_FRONTIERS = [
    project(IMPORT, "return 42;", {"provider.Values": "public static class Member{}"}),
    project(IMPORT, "return VALUE;", {"provider.Values": "public static class Member{}public static final int VALUE=42;"}),
]


@pytest.mark.parametrize("name,sources", CASES, ids=[row[0] for row in CASES])
def test_static_demand_originals_retain_all_declarations_and_owned_ir(name, sources, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    original = dict(sources)
    forward = compile_sources(sources, entry_class="app.Main")
    backward = compile_sources(dict(reversed(tuple(sources.items()))), entry_class="app.Main")
    assert str(forward.module) == str(backward.module)
    assert forward.emitted_class_methods == backward.emitted_class_methods
    assert forward.emit_object().startswith(b"\x7fELF") and sources == original


def test_static_demand_tables_preserve_tokens_and_declaration_identity():
    sources = project(IMPORT + IMPORT + "import static chosen.C.f;", "return f(1)+f(1L);",
        {"provider.Values": "public static final int VALUE=42;public static int f(int n){return 7;}"
                            "public static int f(long n){return 2;}",
         "chosen.C": "public static int f(int n){return 40;}"})
    programs = [SourceParser(text, path).parse() for path, text in sources.items()]
    analyzed = SourceProject(programs)
    caller = analyzed.classes["app/Main"]
    assert [tuple(t.text for t in tokens) for tokens in caller.static_imports] == [
        ("provider", "Values", "*"), ("provider", "Values", "*"), ("chosen", "C", "f")]
    star = caller.static_imports[0][-1]
    assert star.filename == "app/Main.java" and star.column == sources["app/Main.java"].index("*") + 1
    fields = analyzed.static_demand_fields[caller.name]["VALUE"]
    assert fields == {"provider/Values": analyzed.analyzers["provider/Values"].fields["VALUE"]}
    assert len(analyzed.static_demand_methods[caller.name]["f"]) == 2
    single = analyzed.static_methods[caller.name]["f"]
    demand = analyzed.static_demand_methods[caller.name]["f"]
    assert set(single) == {("chosen/C", ("int",))}
    assert set(demand) == {("provider/Values", ("int",)), ("provider/Values", ("long",))}
    for (owner, parameters), method in {**single, **demand}.items():
        assert method is analyzed.analyzers[owner].methods["f", parameters]
    with pytest.raises(UnsupportedJava, match="mixed single-static and on-demand") as caught:
        analyzed.imported_methods(caller, "f", caller.token)
    assert caught.value.code == "java.unsupported.static-imports"


@pytest.mark.parametrize("name,sources", MIXED_METHOD_FRONTIERS, ids=[row[0] for row in MIXED_METHOD_FRONTIERS])
def test_mixed_static_demand_overloads_keep_explicit_source_frontier(name, sources, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *a: pytest.fail("mixed import frontier reached lowering"))
    original = dict(sources)
    for inputs in (sources, dict(reversed(tuple(sources.items())))):
        with pytest.raises(UnsupportedJava, match="mixed single-static and on-demand") as caught:
            compile_sources(inputs, entry_class="app.Main")
        assert caught.value.code == "java.unsupported.static-imports"
    assert sources == original


@pytest.mark.parametrize("name,sources,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_static_demand_originals_fail_before_lowering(name, sources, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *a: pytest.fail("invalid static demand reached lowering"))
    original = dict(sources)
    for inputs in (sources, dict(reversed(tuple(sources.items())))):
        with pytest.raises(JavaError, match=message) as caught:
            compile_sources(inputs, entry_class="app.Main")
        assert type(caught.value) is JavaError and caught.value.code is None
    assert sources == original


@pytest.mark.parametrize("sources", TYPE_FRONTIERS)
def test_accessible_static_demand_types_are_not_silently_dropped(sources, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *a: pytest.fail("type-import frontier reached lowering"))
    with pytest.raises(UnsupportedJava, match="static on-demand member types") as caught:
        compile_sources(sources, entry_class="app.Main")
    assert caught.value.code == "java.unsupported.static-imports"


@pytest.mark.parametrize("imports,code", [
    ("import static absent.Values.*;", "source-dependency"),
    ("import static provider.Outer$Tools.*;", "source-dependency"),
    ("import provider.*;", "on-demand-imports"),
])
def test_static_demand_preserves_missing_and_noncanonical_dependencies(imports, code):
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(project(imports, "return 42;", FIELD), entry_class="app.Main")
    assert caught.value.code == "java.unsupported." + code


def test_static_demand_does_not_replace_inherited_object_method_lookup():
    sources = project(IMPORT, "return equals(1);", {"provider.Values": "public static int equals(int n){return 42;}"})
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(sources, entry_class="app.Main")
    assert caught.value.code == "java.unsupported.object-inherited-methods"


def test_model_static_demand_skips_instance_members_without_runtime_admission():
    sources = project(IMPORT, "return 42;", {"provider.Values": "public int value;public int f(){return 7;}"})
    model = analyze_class_sources(sources)
    assert model.static_demand_fields["app/Main"] == model.static_demand_methods["app/Main"] == {}
    assert not model.semantic_context_complete and not model.models["provider/Values"].runtime_registered


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources", CASES, ids=[row[0] for row in CASES])
def test_same_static_demand_originals_execute_owned_native_bytes(name, sources, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class="app.Main")
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42
    assert (tmp_path / "observed.stdout.bin").read_bytes() == b"\x2a\x00\x00\x00"
    assert (tmp_path / "observed.stderr.bin").read_bytes() == b""


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-static-demand-reference")
@pytest.mark.parametrize("name,sources", CASES, ids=[row[0] for row in CASES])
def test_same_static_demand_originals_match_jdk_raw_processes(name, sources, tmp_path, request, monkeypatch):
    native = tmp_path / "native"
    native.mkdir()
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class="app.Main")
    assert observe_scalar(compiled, native, monkeypatch) == 42
    actual = record_process((str(native / "observed"),), native, "raw-native")
    java, classes, compiler = prepare_reference(sources, tmp_path, request, observer=observer_source("app.Main", "I", ()))
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
@pytest.mark.xdist_group(name="java-static-demand-reference")
@pytest.mark.parametrize("name,sources,message", INVALID, ids=[row[0] for row in INVALID])
def test_same_invalid_static_demand_originals_have_jdk_rejections(name, sources, message, tmp_path, request):
    _, _, compiler = prepare_reference(sources, tmp_path, request)
    assert compiler[0] == 1, compiler
    assert b".java:" in compiler[2]


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-demand-reference")
@pytest.mark.parametrize("sources", TYPE_FRONTIERS)
def test_static_demand_type_frontier_originals_are_jdk_valid(sources, tmp_path, request):
    _, _, compiler = prepare_reference(sources, tmp_path, request)
    assert compiler[0] == 0, compiler


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-demand-reference")
@pytest.mark.parametrize("name,sources", MIXED_METHOD_FRONTIERS, ids=[row[0] for row in MIXED_METHOD_FRONTIERS])
def test_mixed_static_demand_originals_retain_jdk_conformance_obligation(name, sources, tmp_path, request):
    # This is intentionally not waived or changed to the selected JDK's
    # observed rejection/result. Preserve the normative JLS expectation and
    # original bytes while the owned mixed-overload capability stays closed.
    java, classes, compiler = prepare_reference(sources, tmp_path, request,
        observer=observer_source("app.Main", "I", ()))
    assert compiler[0] == 0, compiler
    actual = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccRawObserve"),
                            tmp_path, "mixed-reference")
    assert actual == (0, b"\x2a\x00\x00\x00", b"")
