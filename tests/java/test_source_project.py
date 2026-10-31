"""Original multi-unit sources, not classes/JARs or rewritten library fragments."""
import json
import platform
import sys

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources, method_symbol
from tests.java.owned_execution import observe_i32
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


NATIVE_REASON = None if sys.platform == "linux" and platform.machine() == "x86_64" else "owned Java project observer requires Linux x86_64"


def unit(package, name, members, public=True, imports=""):
    return (("package " + package + ";" if package else "") + imports +
            ("public " if public else "") + "class " + name + " {" + members + "}")


PROJECTS = [
    ({"Main.java": unit("p", "Main", "static int run(){return Hidden.Helper.f();}"),
      "Hidden.java": unit("java.lang", "Hidden", "", False),
      "Helper.java": unit("Hidden", "Helper", "public static int f(){return 42;}")}, "p.Main", 42),
    ({"Main.java": unit("p", "Main", "static int run(){return Helper.f();}"),
      "Helper.java": unit("java.lang", "Helper", "public static int f(){return 42;}")}, "p.Main", 42),
    ({"Main.java": unit("p", "Main", "static int run(){return Helper.add(40,2);}"),
      "Helper.java": unit("p", "Helper", "static int add(int a,int b){return a+b;}", False)}, "p.Main", 42),
    ({"Main.java": unit("app", "Main", "static int run(){return Add.sum(2147483647,1);}",
                        imports="import arithmetic.Add;"),
      "Add.java": unit("arithmetic", "Add", "public static int sum(int a,int b){return a+b;}")}, "app.Main", -2147483648),
    ({"Main.java": unit("app", "Main", "static int run(){return arithmetic.Add.sum(40,2);}"),
      "Add.java": unit("arithmetic", "Add", "public static int sum(int a,int b){return a+b;}")}, "app.Main", 42),
    ({"A.java": unit("p", "A", "static int step(int n){if(n==0)return 0;return B.step(n-1)+1;}static int run(){return step(42);}"),
      "B.java": unit("p", "B", "static int step(int n){if(n==0)return 0;return A.step(n-1)+1;}")}, "p.A", 42),
    ({"Main.java": unit("p", "Main", "static int run(){return Lib.f()+Lib.f(2);}"),
      "Lib.java": unit("p", "Lib", "static int f(){return 40;}static int f(int x){return x;}")}, "p.Main", 42),
    ({"Main.java": unit("p", "Main", "private static int f(){return 42;}static int run(){return Main.f();}",
                        imports="import p.Main;import p.Main;")}, "p.Main", 42),
    ({"Main.java": unit("p", "Main", "static int run(){return Choice.f();}", imports="import q.Choice;"),
      "Local.java": unit("p", "Choice", "static int f(){return 7;}"),
      "Imported.java": unit("q", "Choice", "public static int f(){return 42;}")}, "p.Main", 42),
    ({"Main.java": unit("p", "Main", "static int run(){return A.f()-B.f();}"),
      "A.java": unit("p", "A", "static int f(){return -2000000000;}"),
      "B.java": unit("p", "B", "static int f(){return 127;}")}, "p.Main", -2000000127),
]


# Keep the original fully qualified programs byte-for-byte. They are not
# native-success cases while the bootstrap implicit-type directory is absent.
PACKAGE_QUALIFIED_ORIGINALS = [PROJECTS[0], PROJECTS[4]]
PROJECTS = [item for index, item in enumerate(PROJECTS) if index not in (0, 4)]


@pytest.mark.parametrize("original,entry_class,expected", PACKAGE_QUALIFIED_ORIGINALS)
def test_original_package_qualified_programs_are_explicit_capability_failures(original, entry_class, expected):
    with pytest.raises(UnsupportedJava, match="package-qualified calls require complete implicit-type metadata"):
        compile_sources(original, entry_class=entry_class)


def test_missing_standard_implicit_type_cannot_be_reinterpreted_as_a_supplied_package():
    original = {"Main.java": unit("p", "Main", "static int run(){return System.Helper.f();}"),
                "Helper.java": unit("System", "Helper", "public static int f(){return 42;}")}
    with pytest.raises(UnsupportedJava, match="complete implicit-type metadata"):
        compile_sources(original, entry_class="p.Main")


@pytest.mark.parametrize("original,entry_class,expected", PROJECTS)
def test_project_source_graph_has_distinct_owned_symbols_and_deterministic_ir(original, entry_class, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    forward = compile_sources(original, entry_class=entry_class)
    backward = compile_sources(dict(reversed(tuple(original.items()))), entry_class=entry_class)
    assert str(forward.module) == str(backward.module)
    assert len(forward.emitted_class_methods) == len(set(forward.emitted_class_methods))
    for owner, name, descriptor in forward.emitted_class_methods:
        assert method_symbol(owner, name, descriptor) in str(forward.module)
    assert forward.emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("original,entry_class,expected", PROJECTS)
def test_original_source_project_executes_full_i32(original, entry_class, expected, tmp_path, monkeypatch):
    path = tmp_path / "original-project.json"
    path.write_text(json.dumps(original, sort_keys=True))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compilation = compile_sources(original, entry_class=entry_class)
        compilation.emit_object()
    assert observe_i32(compilation, tmp_path, monkeypatch) == expected
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize("original,entry_class,message", [
    ({"a": unit("p", "A", "static int run(){return 1;}"),
      "b": unit("p", "A", "static int f(){return 2;}")}, "p.A", "duplicate source class"),
    ({"a": unit("p", "A", "static int run(){return 1;}"),
      "b": unit("p.A", "B", "static int f(){return 2;}")}, "p.A", "class/package name conflict"),
    ({"a": unit("p", "A", "static int run(){return B.f();}"),
      "b": unit("p", "B", "private static int f(){return 2;}")}, "p.A", "not accessible"),
    ({"a": unit("p", "A", "static int run(){return q.B.f();}"),
      "b": unit("q", "B", "static int f(){return 2;}")}, "p.A", "complete implicit-type metadata"),
    ({"a": unit("p", "A", "static int run(){return q.B.f();}"),
      "b": unit("q", "B", "protected static int f(){return 2;}")}, "p.A", "complete implicit-type metadata"),
    ({"a": unit("p", "A", "static int run(){return q.B.f();}"),
      "b": unit("q", "B", "public static int f(){return 2;}", False)}, "p.A", "complete implicit-type metadata"),
    ({"a": unit("p", "A", "static int run(){return 42;}", imports="import q.B;"),
      "b": unit("q", "B", "static int f(){return 2;}", False)}, "p.A", "not accessible"),
    ({"a": unit("p", "A", "static int run(){return 42;}", imports="import q.A;"),
      "b": unit("q", "A", "static int f(){return 2;}")}, "p.A", "conflicting single-type import"),
    ({"a": unit("p", "A", "static int run(){return 42;}", imports="import q.B;import r.B;"),
      "b": unit("q", "B", "static int f(){return 2;}"),
      "c": unit("r", "B", "static int f(){return 2;}")}, "p.A", "conflicting single-type import"),
    ({"a": unit("p", "A", "static int run(){int B=1;return B.f();}"),
      "b": unit("p", "B", "static int f(){return 2;}")}, "p.A", "cannot be dereferenced"),
    ({"a": unit("p", "A", "static int run(){int q=1;return q.B.f();}"),
      "b": unit("q", "B", "public static int f(){return 2;}")}, "p.A", "cannot be dereferenced"),
    ({"a": unit("p", "A", "static int run(){return B.f();}"),
      "b": unit("p", "B", "static int f(int x){return x;}")}, "p.A", "unresolved source static method"),
    ({"a": unit("p", "A", "static int run(){return Missing.f();}")}, "p.A", "unresolved source class"),
    ({"a": unit("p", "A", "static int run(){return 42;}"),
      "b": unit("unused", "Bad", "static int f(){int x;return x;}")}, "p.A", "not definitely assigned"),
    ({"a": unit("p", "A", "static int run(){return 42;}")}, "p.Missing", "entry class not found"),
])
def test_project_semantic_errors_precede_lowering(original, entry_class, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid project reached source lowering")
    monkeypatch.setattr(SourceLowerer, "lower", forbidden)
    with pytest.raises(JavaError, match=message):
        compile_sources(original, entry_class=entry_class)


@pytest.mark.parametrize("imports,message", [
    ("import absent.Library;", "requires supplied source"),
    ("import p.*;", "on-demand imports"),
    ("import static p.A.f;", "static imports"),
])
def test_source_project_lookup_gaps_are_not_ignored(imports, message):
    with pytest.raises(UnsupportedJava, match=message):
        compile_sources({"Main.java": unit("p", "Main", "static int run(){return 42;}", imports=imports)}, entry_class="p.Main")


def test_class_name_obscures_package_qualifier_without_guessing_a_fully_qualified_match():
    with pytest.raises(UnsupportedJava, match="member types"):
        compile_sources({"A": unit("local", "p", "static int run(){return p.Other.f();}"),
                         "B": unit("p", "Other", "public static int f(){return 42;}")}, entry_class="local.p")


def test_single_class_api_shares_qualified_source_call_resolution():
    compilation = compile_source(unit("p", "C", "static int run(){return C.answer();}static int answer(){return 42;}"))
    assert compilation.emitted_class_methods == (("p/C", "run", "()I"), ("p/C", "answer", "()I"))


@pytest.mark.parametrize("modifier", ["", "protected", "private"])
def test_imported_class_does_not_grant_access_to_nonpublic_static_method(modifier):
    original = {"Main.java": unit("p", "Main", "static int run(){return B.f();}", imports="import q.B;"),
                "B.java": unit("q", "B", modifier + " static int f(){return 42;}")}
    with pytest.raises(JavaError, match="static method is not accessible"):
        compile_sources(original, entry_class="p.Main")
