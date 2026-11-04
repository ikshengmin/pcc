"""Native static member classes, distinct from inner/managed class execution.

JLS21 6.3-6.6, 7.5.1/7.5.3, 8.1.3/8.5, 13.1 and 15.12.1:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html#jls-7.5
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.5
https://docs.oracle.com/javase/specs/jls/se21/html/jls-13.html#jls-13.1
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.12.1

Authored CI obligations, execution UNRUN. Native and selected-JDK references
consume the same complete source maps without rewriting, extracting methods,
or fabricating dependencies. F/D results and arguments are raw IEEE bits.
The complete pinned RegularizedGamma remains an explicit unsupported original,
not module acceptance. Its missing dependencies are never replaced by stubs.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import (
    JavaError,
    UnsupportedJava,
    analyze_class_sources,
    compile_source,
    compile_sources,
    method_symbol,
)
from pcc.frontends.java.source_project import (
    OBJECT_INHERITED_METHOD_NAMES,
    SourceProject,
)
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_local_inference import (
    LANES,
    WIDTHS,
    descriptor,
    observer_source,
)
from tests.java.test_source_project import unit
from tests.java.test_source_raw_reference import (
    JVM_LIMITS,
    digest,
    record_process,
    tool_identity,
)
from tests.java.test_source_scalars import NATIVE_REASON


JDK_REASON = (None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
              "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")


def outer(members, *, imports=""):
    return {"p/Outer.java": unit("p", "Outer", members, imports=imports)}


def app(imports, body, declarations, *, public=True):
    return {
        "app/Main.java": unit("app", "Main", "public static int run(){" + body + "}", imports=imports),
        "provider/Outer.java": unit("provider", "Outer", declarations, public),
    }


def row(name, members, expected=42, *, entry="p.Outer", imports=""):
    return name, outer(members, imports=imports), entry, "I", (), expected


CASES = [
    # Exact former boundary inputs, including their original outer wrapper.
    row("outer-constructor", "public static int run(){return 42;}private Outer(){}"),
    row("nested-constructor", "public static int run(){return 42;}static class Inner{private Inner(){}}"),
    row("private-nest", "private static final int BASE=40;private static int add(int n){return BASE+n;}"
        "public static int run(){return Inner.read();}"
        "private static class Inner{private static final int TWO=2;private static int read(){return add(TWO);}}"),
    row("siblings-and-deep-owner", "public static int run(){return Left.f()+Right.f();}"
        "static class Left{private static int f(){return 7;}static class Deep{private static int f(){return Left.f()+28;}}}"
        "static class Right{private static int f(){return Left.Deep.f();}}"),
    row("sibling-mutual-recursion", "public static int run(){return A.f(42);}"
        "static class A{static int f(int n){if(n==0)return 0;return B.f(n-1)+1;}}"
        "static class B{static int f(int n){if(n==0)return 0;return A.f(n-1)+1;}}"),
    row("nearest-overload-set", "static int f(int n){return 7;}public static int run(){return Inner.read();}"
        "static class Inner{static int f(long n){return 42;}static int read(){return f(1);}}"),
    row("outer-method-when-name-absent", "private static int answer(){return 42;}public static int run(){return Inner.read();}"
        "static class Inner{static int read(){return answer();}}"),
    row("nearest-enclosing-field", "static final int VALUE=7;public static int run(){return Middle.Inner.read();}"
        "static class Middle{static final int VALUE=40;static class Inner{static int read(){return VALUE+2;}}}"),
    row("own-field-before-outer", "static final int VALUE=7;public static int run(){return Inner.read();}"
        "static class Inner{static final int VALUE=42;static int read(){return VALUE;}}"),
    row("locals-and-parameters-before-fields", "static final int VALUE=7;public static int run(){return Inner.read(21);}"
        "static class Inner{static final int VALUE=3;static int read(int VALUE){return VALUE+local();}"
        "static int local(){int VALUE=21;return VALUE;}}"),
    row("outer-later-constant-is-not-own-forward-reference", "public static int run(){return Inner.COPY;}"
        "static class Inner{static final int COPY=VALUE;}static final int VALUE=42;"),
    row("qualified-own-later-constant", "public static int run(){return Inner.FIRST;}"
        "static class Inner{static final int FIRST=Inner.LATER;static final int LATER=42;}"),
    row("same-simple-name-distinct-nests", "public static int run(){return First.Same.f()+Second.Same.f();}"
        "static class First{static class Same{static int f(){return 20;}}}"
        "static class Second{static class Same{static int f(){return 22;}}}"),
    row("canonical-deep-entry", "public static class Middle{public static class Inner{public static int run(){return 42;}}}",
        entry="p.Outer.Middle.Inner"),
    row("qualified-type-chain", "public static int run(){return Outer.Middle.Inner.f();}"
        "static class Middle{static class Inner{static int f(){return 42;}}}"),
    row("unused-members-retained", "public static int run(){return Inner.f();}static int unused(){return 7;}"
        "static class Inner{static int f(){return 42;}static int unused(){return 9;}"
        "static class Uncalled{static int unused(){return 11;}}}"),
    row("void-nested-call", "public static int run(){Inner.touch();return 42;}"
        "static class Inner{static void touch(){return;}}"),
    row("dollar-member-simple-name", "public static int run(){return Outer.Inner$Legal.f();}"
        "static class Inner$Legal{static int f(){return 42;}}"),
    row("qualified-inherited-name-overload", "public static int run(){return Inner.hashCode(1);}"
        "static class Inner{static int hashCode(int n){return 42;}}"),
    row("simple-method-name-does-not-obscure-type", "static int Inner(){return 7;}"
        "public static int run(){return Inner.f();}static class Inner{static int f(){return 42;}}"),
    row("own-inherited-name-overload", "static int hashCode(int n){return 7;}"
        "public static int run(){return Inner.read();}static class Inner{"
        "static int hashCode(int n){return 42;}static int read(){return hashCode(1);}}"),
    ("canonical-type-import", app("import provider.Outer.Inner;import provider.Outer.Inner;", "return Inner.f();",
        "private static final int VALUE=42;public static class Inner{public static int f(){return VALUE;}}"),
     "app.Main", "I", (), 42),
    ("canonical-static-import", app("import static provider.Outer.Inner.VALUE;import static provider.Outer.Inner.f;"
        "import static provider.Outer.Inner.f;", "return VALUE+f(2);",
        "public static class Inner{public static final int VALUE=40;public static int f(int n){return n;}}"),
     "app.Main", "I", (), 42),
    ("nested-type-shadows-import", {
        **outer("public static int run(){return Inner.f();}static class Inner{static int f(){return 42;}}",
                imports="import provider.Inner;"),
        "provider/Inner.java": unit("provider", "Inner", "public static int f(){return 7;}"),
     }, "p.Outer", "I", (), 42),
    ("outer-field-before-static-import", {
        **outer("static final int VALUE=42;public static int run(){return Inner.f();}"
                "static class Inner{static int f(){return VALUE;}}", imports="import static provider.Values.VALUE;"),
        "provider/Values.java": unit("provider", "Values", "public static final int VALUE=7;"),
     }, "p.Outer", "I", (), 42),
    ("outer-method-before-static-import", {
        **outer("static int f(long n){return 42;}public static int run(){return Inner.read();}"
                "static class Inner{static int read(){return f(1);}}", imports="import static provider.Values.f;"),
        "provider/Values.java": unit("provider", "Values", "public static int f(int n){return 7;}"),
     }, "p.Outer", "I", (), 42),
    ("literal-dollar-is-not-canonical-separator", {
        **outer("public static int run(){return Inner.f()+Outer$Legal.f();}static class Inner{static int f(){return 40;}}"),
        "p/Outer$Legal.java": unit("p", "Outer$Legal", "static int f(){return 2;}"),
     }, "p.Outer", "I", (), 42),
]
CASES += [
    ("same-package-" + (modifier or "default"), {
        "p/Main.java": unit("p", "Main", "public static int run(){return Outer.Inner.f();}"),
        "p/Outer.java": unit("p", "Outer", modifier + " static class Inner{"
                             + modifier + " static int f(){return 42;}}"),
     }, "p.Main", "I", (), 42)
    for modifier in ("", "protected", "public")
]

# Every primitive traverses outer -> nested -> sibling calls, with var and a
# blank final remaining locals of their original declaring method and owner.
PRIMITIVE_VALUES = {"boolean": 1, "byte": -128, "short": -32768, "char": 65535,
                    "int": -2147483648, "long": 9007199254740993,
                    "float": 0x80000000, "double": 0x8000000000000000}
CASES += [
    ("primitive-" + dtype, outer(
        "public static " + dtype + " run(" + dtype + " input){return Inner.copy(input);}"
        "static class Inner{static " + dtype + " copy(" + dtype + " input){"
        "var local=input;final " + dtype + " result;result=Sibling.copy(local);return result;}}"
        "static class Sibling{static " + dtype + " copy(" + dtype + " input){return input;}}"),
     "p.Outer", LANES[dtype], ((LANES[dtype], value),), value)
    for dtype, value in PRIMITIVE_VALUES.items()
]
CASES += [
    ("raw-" + name, outer("public static " + dtype + " run(" + dtype + " input){return Inner.copy(input);}"
                         "static class Inner{static " + dtype + " copy(" + dtype + " input){return input;}}"),
     "p.Outer", lane, ((lane, bits),), bits)
    for name, dtype, lane, bits in (
        ("float-subnormal", "float", "F", 0x00000001),
        ("double-subnormal", "double", "D", 0x0000000000000001),
        ("float-max-finite", "float", "F", 0x7f7fffff),
        ("double-max-finite", "double", "D", 0x7fefffffffffffff),
    )
]


def compile_case(sources, entry, lane, arguments):
    return compile_sources(sources, entry_class=entry, descriptor=descriptor(lane, arguments))


def expected_bytes(lane, expected):
    return expected.to_bytes(WIDTHS[lane], "little", signed=lane not in {"Z", "C", "F", "D"})


def forbid_lowering(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("rejected original reached lowering"))


@pytest.mark.parametrize("name,sources,entry,lane,arguments,expected", CASES, ids=[case[0] for case in CASES])
def test_original_member_projects_keep_all_methods_and_deterministic_owned_ir(
        name, sources, entry, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    before = dict(sources)
    forward = compile_case(sources, entry, lane, arguments)
    backward = compile_case(dict(reversed(tuple(sources.items()))), entry, lane, arguments)
    assert sources == before
    assert forward.emitted_class_methods == backward.emitted_class_methods
    assert str(forward.module) == str(backward.module)
    project = SourceProject([SourceParser(text, filename).parse() for filename, text in sources.items()])
    required = {(program.name, method.token.text, method.descriptor)
                for program in project.programs for method in program.methods}
    assert set(forward.emitted_class_methods) == required
    assert len(forward.emitted_class_methods) == len(required)
    assert forward.class_name == project.canonical_classes[entry.replace(".", "/")].name
    assert all(method_symbol(*method) in str(forward.module) for method in required)
    assert "bc_" not in str(forward.module)
    assert forward.emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources,entry,lane,arguments,expected", CASES, ids=[case[0] for case in CASES])
def test_same_original_member_projects_execute_exact_native_bytes(
        name, sources, entry, lane, arguments, expected, tmp_path, monkeypatch):
    retained = {}
    write_sources(tmp_path, sources, retained)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_case(sources, entry, lane, arguments)
    assert observe_scalar(compiled, tmp_path, monkeypatch, arguments) == expected
    assert (tmp_path / "observed.stdout.bin").read_bytes() == expected_bytes(lane, expected)
    assert (tmp_path / "observed.stderr.bin").read_bytes() == b""
    assert all(digest(path) == checksum for path, checksum in retained.items())


def test_parser_preserves_canonical_binary_lexical_and_compilation_unit_identity():
    text = ("package p;import q.Choice;import static q.Choice.f;public class Outer$Legal{"
            "static final class Inner$Legal{private static class Deep{static int read(){return 42;}}}}")
    program = SourceParser(text, "Original.java").parse()
    inner, = program.member_classes
    deep, = inner.member_classes
    assert [(item.name, item.source_name, item.simple_name, item.enclosing_name, item.package)
            for item in (program, inner, deep)] == [
        ("p/Outer$Legal", "p/Outer$Legal", "Outer$Legal", None, "p"),
        ("p/Outer$Legal$Inner$Legal", "p/Outer$Legal/Inner$Legal", "Inner$Legal", "p/Outer$Legal", "p"),
        ("p/Outer$Legal$Inner$Legal$Deep", "p/Outer$Legal/Inner$Legal/Deep", "Deep",
         "p/Outer$Legal$Inner$Legal", "p"),
    ]
    assert all(item.token.filename == "Original.java" for item in (program, inner, deep))
    assert inner.imports is deep.imports is program.imports
    assert inner.static_imports is deep.static_imports is program.static_imports
    assert deep.methods[0].owner == deep.name
    assert "static" in inner.modifiers and "final" in inner.modifiers
    assert "private" in deep.modifiers


def test_native_single_unit_api_and_project_api_share_complete_nested_closure(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    _, sources, entry, lane, arguments, _ = next(case for case in CASES if case[0] == "unused-members-retained")
    filename, text = next(iter(sources.items()))
    single = compile_source(text, filename=filename)
    project = compile_case(sources, entry, lane, arguments)
    required = {("p/Outer", "run", "()I"), ("p/Outer", "unused", "()I"),
                ("p/Outer$Inner", "f", "()I"), ("p/Outer$Inner", "unused", "()I"),
                ("p/Outer$Inner$Uncalled", "unused", "()I")}
    assert set(single.emitted_class_methods) == set(project.emitted_class_methods) == required
    assert str(single.module) == str(project.module)


def test_nested_single_static_imports_bind_original_binary_owned_declarations():
    _, sources, *_ = next(case for case in CASES if case[0] == "canonical-static-import")
    project = SourceProject([SourceParser(text, filename).parse() for filename, text in sources.items()]).analyze()
    owner = project.classes["provider/Outer$Inner"]
    assert project.canonical_classes["provider/Outer/Inner"] is owner
    field = project.static_fields["app/Main"]["VALUE"][owner.name]
    assert field is project.analyzers[owner.name].fields["VALUE"]
    method, = project.static_methods["app/Main"]["f"].values()
    assert method is project.analyzers[owner.name].methods["f", ("int",)]
    assert field.owner == method.owner == owner.name


@pytest.mark.parametrize("dtype", tuple(LANES))
def test_nested_inference_and_blank_final_bindings_keep_original_primitive_owner(dtype):
    _, sources, _, _, _, _ = next(case for case in CASES if case[0] == "primitive-" + dtype)
    project = SourceProject([SourceParser(text, filename).parse() for filename, text in sources.items()]).analyze()
    method, = project.classes["p/Outer$Inner"].methods
    assert method.owner == "p/Outer$Inner"
    assert [(binding.name, binding.dtype) for binding in method.locals] == [
        ("input", dtype), ("local", dtype), ("result", dtype),
    ]
    assert not method.locals[1].is_final
    assert method.locals[2].is_final and method.locals[2].is_blank_final
    assert project.classes["p/Outer$Sibling"].methods[0].owner == "p/Outer$Sibling"


def invalid(name, members, message):
    return name, outer(members), "p.Outer", message


INVALID = [
    invalid("duplicate-sibling", "public static int run(){return 42;}static class Inner{}static class Inner{}", "duplicate member class"),
    invalid("same-name-as-parent", "public static int run(){return 42;}static class Outer{}", "same simple name as an enclosing class"),
    invalid("same-name-as-ancestor", "public static int run(){return 42;}static class Middle{static class Outer{}}", "same simple name as an enclosing class"),
    invalid("nearest-wrong-type", "static int f(int n){return 42;}public static int run(){return Inner.read();}"
            "static class Inner{static int f(boolean n){return 7;}static int read(){return f(1);}}", "unresolved same-class static method"),
    invalid("nearest-wrong-arity", "static int f(int n){return 42;}public static int run(){return Inner.read();}"
            "static class Inner{static int f(){return 7;}static int read(){return f(1);}}", "unresolved same-class static method"),
    invalid("qualified-no-outer-method-fallback", "static int f(){return 42;}public static int run(){return Inner.f();}"
            "static class Inner{}", "unresolved source static method"),
    invalid("qualified-no-outer-field-fallback", "static final int VALUE=42;public static int run(){return Inner.VALUE;}"
            "static class Inner{}", "undefined local variable or constant field"),
    invalid("local-obscures-member-type", "public static int run(){int Inner=1;return Inner.f();}"
            "static class Inner{static int f(){return 42;}}", "cannot be dereferenced"),
    invalid("parameter-obscures-member-type", "public static int run(){return f(1);}static int f(int Inner){return Inner.f();}"
            "static class Inner{static int f(){return 42;}}", "cannot be dereferenced"),
    invalid("field-obscures-member-type", "static final int Inner=1;public static int run(){return Inner.f();}"
            "static class Inner{static int f(){return 42;}}", "cannot be dereferenced"),
    invalid("outer-field-obscures-sibling-type", "static final int Choice=1;static class Choice{static int f(){return 42;}}"
            "public static int run(){return Inner.read();}static class Inner{static int read(){return Choice.f();}}", "cannot be dereferenced"),
    invalid("intermediate-field-obscures-type", "public static int run(){return Outer.Middle.Inner.f();}"
            "static class Middle{static final int Inner=1;static class Inner{static int f(){return 42;}}}", "cannot be dereferenced as a member type"),
    invalid("intermediate-method-obscures-type", "public static int run(){return Outer.Middle.Inner.f();}"
            "static class Middle{static int Inner(){return 1;}static class Inner{static int f(){return 42;}}}", "cannot be dereferenced as a member type"),
    invalid("intermediate-inherited-method-obscures-type", "public static int run(){return Outer.Middle.getClass.f();}"
            "static class Middle{static class getClass{static int f(){return 42;}}}", "cannot be dereferenced as a member type"),
    invalid("own-forward-constant", "public static int run(){return Inner.FIRST;}"
            "static class Inner{static final int FIRST=LATER;static final int LATER=42;}", "illegal forward"),
    invalid("uncalled-invalid-nested-method", "public static int run(){return 42;}"
            "static class Inner{static int unused(){int n;return n;}}", "not definitely assigned"),
    invalid("uncalled-invalid-deep-method", "public static int run(){return 42;}"
            "static class Inner{static class Deep{static int unused(){final int n;n=7;n=42;return n;}}}", "definitely unassigned"),
    invalid("uncalled-invalid-inferred-local", "public static int run(){return 42;}"
            "static class Inner{static int unused(){var n=n;return 42;}}", "own inferred initializer"),
    invalid("duplicate-method-within-nested-owner", "public static int run(){return 42;}"
            "static class Inner{static int f(){return 1;}static long f(){return 2L;}}", "duplicate method signature"),
    invalid("duplicate-field-within-nested-owner", "public static int run(){return 42;}"
            "static class Inner{static final int K=1,K=2;}", "duplicate field name"),
    ("binary-dollar-collision", {**outer("public static int run(){return 42;}static class Inner{}"),
        "p/Outer$Inner.java": unit("p", "Outer$Inner", "")}, "p.Outer", "duplicate source binary class"),
    ("canonical-package-collision", {**outer("public static int run(){return 42;}static class Inner{}"),
        "p/Outer/Inner.java": unit("p.Outer", "Inner", "")}, "p.Outer", "duplicate source class"),
    ("top-level-package-conflict-retained", {**outer("public static int run(){return 42;}static class Inner{}"),
        "p/Outer/Other.java": unit("p.Outer", "Other", "")}, "p.Outer", "class/package name conflict"),
    ("binary-dollar-not-entry-alias", outer("static class Inner{public static int run(){return 42;}}"),
     "p.Outer$Inner", "entry class not found"),
    invalid("binary-dollar-not-source-alias", "public static int run(){return Outer$Inner.f();}"
            "static class Inner{static int f(){return 42;}}", "unresolved source class qualifier"),
    ("unrelated-private-type", {
        **outer("private static class Inner{public static int f(){return 42;}}"),
        "p/Main.java": unit("p", "Main", "public static int run(){return Outer.Inner.f();}"),
     }, "p.Main", "source class is not accessible"),
    ("unrelated-private-method", {
        **outer("static class Inner{private static int f(){return 42;}}"),
        "p/Main.java": unit("p", "Main", "public static int run(){return Outer.Inner.f();}"),
     }, "p.Main", "source static method is not accessible"),
    ("unrelated-private-field", {
        **outer("static class Inner{private static final int VALUE=42;}"),
        "p/Main.java": unit("p", "Main", "public static int run(){return Outer.Inner.VALUE;}"),
     }, "p.Main", "source constant field is not accessible"),
    ("public-inner-in-inaccessible-enclosing", app("import provider.Outer.Inner;", "return 42;",
        "public static class Inner{public static int f(){return 42;}}", public=False),
     "app.Main", "source class is not accessible"),
    ("private-intermediate-enclosing", app("import provider.Outer.Middle.Inner;", "return 42;",
        "private static class Middle{public static class Inner{public static int f(){return 42;}}}"),
     "app.Main", "source class is not accessible"),
    ("private-self-type-import", outer("public static int run(){return 42;}private static class Inner{}",
        imports="import p.Outer.Inner;"), "p.Outer", "source class is not accessible"),
    ("private-self-method-import", outer("public static int run(){return 42;}static class Inner{private static int f(){return 42;}}",
        imports="import static p.Outer.Inner.f;"), "p.Outer", "no accessible static member"),
    ("private-self-field-import", outer("public static int run(){return 42;}static class Inner{private static final int VALUE=42;}",
        imports="import static p.Outer.Inner.VALUE;"), "p.Outer", "no accessible static member"),
    ("cross-package-package-member-type", app("import provider.Outer.Inner;", "return 42;", "static class Inner{}"),
     "app.Main", "source class is not accessible"),
    ("cross-package-protected-member-type", app("import provider.Outer.Inner;", "return 42;", "protected static class Inner{}"),
     "app.Main", "source class is not accessible"),
]
INVALID += [
    ("unnamed-owner-" + ("named-importer" if package else "unnamed-importer") + "-" + kind, {
        (package + "/" if package else "") + "Main.java": unit(
            package, "Main", "public static int run(){return 42;}", imports=imports),
        "Outer.java": unit("", "Outer", "public static class Inner{public static int f(){return 42;}}"),
     }, (package + "." if package else "") + "Main", "unnamed package")
    for package in ("", "app")
    for kind, imports in (("type", "import Outer.Inner;"), ("static", "import static Outer.Inner.f;"))
]
INVALID += [
    ("cross-package-" + modifier + "-method", app("import provider.Outer.Inner;", "return Inner.f();",
        "public static class Inner{" + modifier + " static int f(){return 42;}}"),
     "app.Main", "source static method is not accessible")
    for modifier in ("private", "protected")
]


@pytest.mark.parametrize("name,sources,entry,message", INVALID, ids=[case[0] for case in INVALID])
def test_invalid_member_originals_fail_as_java_errors_before_lowering(name, sources, entry, message, monkeypatch):
    forbid_lowering(monkeypatch)
    before = dict(sources)
    with pytest.raises(JavaError, match=message) as caught:
        compile_sources(sources, entry_class=entry)
    assert type(caught.value) is JavaError and caught.value.code is None
    assert sources == before


BOUNDARIES = [
    ("nonstatic-inner", "class Inner{}", "member-types"),
    ("member-interface", "static interface Inner{}", "member-types"),
    ("member-enum", "static enum Inner{VALUE}", "member-types"),
    ("member-record", "static record Inner(){}", "member-types"),
    ("generic-member", "static class Inner<T>{}", "class-supertypes-generics"),
    ("inherited-member", "static class Inner extends Outer{}", "class-supertypes-generics"),
    ("nested-instance-method", "static class Inner{int f(){return 42;}}", "instance-methods"),
    ("nested-instance-field", "static class Inner{int value;}", "instance-methods"),
    ("nested-static-block", "static class Inner{static {int n=1;}}", "static-initialization"),
    ("nested-mutable-static", "static class Inner{static int value=42;}", "static-initialization"),
    ("nested-blank-static-final", "static class Inner{static final int VALUE;}", "static-initialization"),
    ("nested-nonconstant-initializer", "static class Inner{static final int VALUE=f();static int f(){return 42;}}", "static-initialization"),
    ("unused-object-allocation", "static class Inner{static int unused(){new Inner();return 42;}}", "object-expressions"),
    ("unused-anonymous-class", "static class Inner{static int unused(){new Inner(){};return 42;}}", "object-expressions"),
    ("unused-local-class", "static class Inner{static int unused(){class Local{}return 42;}}", "local-types"),
    ("unused-string", 'static class Inner{static int unused(){return "text";}}', "string-literals"),
    ("nested-array-state", "static class Inner{static final int[] VALUES={42};}", "array-storage"),
    ("cross-owner-constant-cycle", "static final int VALUE=Inner.VALUE;static class Inner{static final int VALUE=Outer.VALUE;}", "cyclic-initialization"),
]


@pytest.mark.parametrize("name,members,code", BOUNDARIES, ids=[case[0] for case in BOUNDARIES])
def test_nested_runtime_and_unimplemented_declarations_are_not_silently_discarded(name, members, code, monkeypatch):
    forbid_lowering(monkeypatch)
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(outer("public static int run(){return 42;}" + members), entry_class="p.Outer")
    assert caught.value.code == "java.unsupported." + code


@pytest.mark.parametrize("suffix", ("", "public static final int Inner=7;", "public static int Inner(){return 7;}"))
def test_static_import_of_member_type_remains_explicit_even_with_same_name_values(suffix, monkeypatch):
    forbid_lowering(monkeypatch)
    sources = app("import static provider.Outer.Inner;", "return 42;", "public static class Inner{}" + suffix)
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(sources, entry_class="app.Main")
    assert caught.value.code == "java.unsupported.static-imports"


@pytest.mark.parametrize("imports,body,code", [
    ("import provider.Outer$Inner;", "return 42;", "source-dependency"),
    ("import static provider.Outer$Inner.f;", "return 42;", "source-dependency"),
    ("", "return provider.Outer.Inner.f();", "implicit-type-metadata"),
    ("import provider.Outer.*;", "return 42;", "on-demand-imports"),
    ("import static provider.Outer.Inner.*;", "return 42;", "static-imports"),
])
def test_member_support_does_not_guess_binary_import_aliases_or_unknown_package_roots(imports, body, code, monkeypatch):
    forbid_lowering(monkeypatch)
    sources = app(imports, body, "public static class Inner{public static int f(){return 42;}}")
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(sources, entry_class="app.Main")
    assert caught.value.code == "java.unsupported." + code


@pytest.mark.parametrize("origin", ("outer", "import"))
@pytest.mark.parametrize("name", sorted(OBJECT_INHERITED_METHOD_NAMES))
def test_inherited_object_name_in_nested_scope_blocks_outer_and_import_fallback(name, origin, monkeypatch):
    forbid_lowering(monkeypatch)
    declaration = "public static int " + name + "(int n){return 42;}"
    sources = outer((declaration if origin == "outer" else "") +
                    "public static int run(){return Inner.read();}"
                    "static class Inner{static int read(){return " + name + "(1);}}",
                    imports="import static provider.Values." + name + ";" if origin == "import" else "")
    if origin == "import":
        sources["provider/Values.java"] = unit("provider", "Values", declaration)
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(sources, entry_class="p.Outer")
    assert caught.value.code == "java.unsupported.object-inherited-methods"


@pytest.mark.parametrize("name", ("private-nest", "canonical-deep-entry", "canonical-type-import", "primitive-float"))
def test_native_member_support_does_not_admit_nested_class_model_or_managed_runtime(name, monkeypatch):
    forbid_lowering(monkeypatch)
    _, sources, *_ = next(case for case in CASES if case[0] == name)
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources(sources)
    assert caught.value.code == "java.unsupported.member-types"


def test_complete_pinned_regularized_gamma_preserves_its_first_native_and_model_boundaries(monkeypatch):
    root = Path(__file__).parent
    paths = [root / name for name in ("RegularizedGamma.java", "RegularizedGamma.LICENSE", "RegularizedGamma.NOTICE")]
    original = {path: path.read_bytes() for path in paths}
    assert hashlib.sha256(original[paths[0]]).hexdigest() == "f144d0986938692a6ed89dc151da772b2836e8b7fd6b2146880f23db5c6dd230"
    assert len(original[paths[0]]) == 5855
    source = original[paths[0]].decode("utf-8")
    assert len(source.splitlines()) == 155
    assert "private RegularizedGamma()" in source.splitlines()[35]
    assert "public static final class P" in source.splitlines()[45]
    assert "public static final class Q" in source.splitlines()[103]
    assert source.splitlines()[76].index("new Policy") + 1 == 44
    lock = json.loads((root / "RegularizedGamma.source.json").read_text())
    assert lock["revision"] == "77f715769e4039acb4babd9add80b78a06ca7279"
    assert {item["file"] for item in lock["files"]} == {path.name for path in paths}
    for item in lock["files"]:
        raw = original[root / item["file"]]
        assert len(raw) == item["size"]
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
        assert hashlib.sha1(blob).hexdigest() == item["git_blob"]
    assert lock["complete_original_source"] is True
    assert lock["complete_module_source_closure"] is False
    assert lock["complete_build_closure"] is False
    assert lock["project_accepted"] is False
    forbid_lowering(monkeypatch)
    with pytest.raises(UnsupportedJava) as native:
        compile_source(source, filename="RegularizedGamma.java", entry="value", descriptor="(DD)D")
    assert native.value.code == "java.unsupported.object-expressions"
    assert str(native.value).startswith("RegularizedGamma.java:77:44:")
    assert lock["native_expected_boundary"]["code"] == native.value.code
    assert lock["native_expected_boundary"]["line"] == 77
    assert lock["native_expected_boundary"]["column"] == 44
    assert "BoostGamma" in lock["unresolved_dependencies"]
    with pytest.raises(UnsupportedJava) as model:
        analyze_class_sources({"RegularizedGamma.java": source})
    assert model.value.code == "java.unsupported.member-types"
    assert str(model.value).startswith("RegularizedGamma.java:46:")
    assert all(path.read_bytes() == raw for path, raw in original.items())


# This independent dev-only driver compiles explicit full source maps in one
# JVM. It does not discover a sourcepath/classpath dependency or alter originals.
# Batching avoids launching javac once per scalar under the existing 90s gate.
REFERENCE_DRIVER = r'''import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import javax.tools.ToolProvider;
public final class PccMemberReference {
  public static void main(String[] args) throws Exception {
    Path root=Path.of(args[0]);
    var compiler=ToolProvider.getSystemJavaCompiler();
    if(compiler==null)throw new IllegalStateException("selected JDK lacks jdk.compiler");
    for(String row:Files.readAllLines(root.resolve("cases.tsv"),StandardCharsets.UTF_8)){
      String[] fields=row.split("\t",-1);
      Path directory=root.resolve(fields[0]);
      Path classes=directory.resolve("classes");Files.createDirectory(classes);
      var command=new ArrayList<String>();
      for(String value:new String[]{"-proc:none","-encoding","UTF-8","-source",args[1],
          "-target",args[1],"-classpath",classes.toString(),"-sourcepath",classes.toString(),
          "-d",classes.toString()})command.add(value);
      for(int i=2;i<fields.length;i++)command.add(directory.resolve(fields[i]).toString());
      if(fields[1].equals("run"))command.add(directory.resolve("PccNarrowObserve.java").toString());
      var out=new ByteArrayOutputStream();var err=new ByteArrayOutputStream();
      Files.write(directory.resolve("compiler-arguments.txt"),command,StandardCharsets.UTF_8);
      int status=compiler.run(null,out,err,command.toArray(new String[0]));
      Files.write(directory.resolve("compiler.stdout.bin"),out.toByteArray());
      Files.write(directory.resolve("compiler.stderr.bin"),err.toByteArray());
      Files.writeString(directory.resolve("compiler-status.txt"),Integer.toString(status));
      System.out.println(fields[0]+"\t"+fields[1]+"\t"+status);
    }
  }
}
'''


def write_sources(directory, sources, retained):
    for filename, text in sources.items():
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = text.encode("utf-8")
        path.write_bytes(raw)
        retained[path] = hashlib.sha256(raw).hexdigest()


def reference_batch(tmp_path, request, cases, *, runtime):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    inputs = [Path(__file__), Path(__file__).with_name("owned_execution.py"),
              Path(__file__).with_name("test_source_local_inference.py"),
              Path(__file__).with_name("test_source_raw_reference.py")]
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    owner = {**before, "reference_owner": "explicit existing development-only JDK",
             "version": version.group(1), "source_level": level, "target_level": level,
             "platform_contract": "selected current JDK platform, not --release compatibility",
             "production_dependency": False, "source_rewritten": False,
             "complete_supplied_projects": True, "managed_runtime_qualified": False}
    (tmp_path / "owner-before.json").write_text(json.dumps(owner, indent=2))

    def finish():
        after = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
        unchanged = all(digest(path) == checksum for path, checksum in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**owner, "after": after,
            "original_observer_hashes": {str(path): checksum for path, checksum in retained.items()},
            "source_inputs_unchanged": unchanged}, indent=2))
        assert before == after and unchanged

    request.addfinalizer(finish)
    assert record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")[0] == 0
    rows = []
    for name, sources, *rest in cases:
        directory = tmp_path / name
        directory.mkdir()
        write_sources(directory, sources, retained)
        if runtime:
            entry, lane, arguments, _ = rest
            observer = observer_source(entry, lane, arguments)
            write_sources(directory, {"PccNarrowObserve.java": observer}, retained)
        rows.append("\t".join([name, "run" if runtime else "reject", *sources]))
    manifest = tmp_path / "cases.tsv"
    manifest.write_text("\n".join(rows) + "\n")
    retained[manifest] = digest(manifest)
    driver = tmp_path / "PccMemberReference.java"
    driver.write_text(REFERENCE_DRIVER, encoding="utf-8")
    retained[driver] = digest(driver)
    classes = tmp_path / "driver-classes"
    classes.mkdir()
    compiler = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
        "-classpath", str(classes), "-d", str(classes), str(driver)), tmp_path, "driver-compile", 20)
    assert compiler[0] == 0, compiler
    batch = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccMemberReference", str(tmp_path), level),
                           tmp_path, "batch-compile", 30)
    assert batch[0] == 0, batch
    return java


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-static-member-reference")
def test_identical_complete_member_projects_match_selected_jdk_raw_processes(tmp_path, request, monkeypatch):
    java = reference_batch(tmp_path, request, CASES, runtime=True)
    for name, sources, entry, lane, arguments, expected in CASES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", (name, (directory / "compiler.stderr.bin").read_bytes())
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"), "PccNarrowObserve"),
                                   directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            compiled = compile_case(sources, entry, lane, arguments)
        assert observe_scalar(compiled, native, monkeypatch, arguments) == expected
        actual = record_process((str(native / "observed"),), native, "raw-native")
        (directory / "comparison.json").write_text(json.dumps({
            "case": name, "comparison": "raw_process_streams", "entry_class": entry,
            "source_sha256": {filename: hashlib.sha256(text.encode("utf-8")).hexdigest()
                              for filename, text in sources.items()},
            "original_modifiers_or_source_rewritten": False, "complete_supplied_project": True,
            "descriptor": descriptor(lane, arguments), "arguments": arguments,
            "observer_sha256": digest(directory / "PccNarrowObserve.java"),
            "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
        }, indent=2))
        assert actual == reference == (0, expected_bytes(lane, expected), b""), name


# A binary spelling in the PCC entry selector is not Java source syntax and is
# tested separately; no reference compile rejection is claimed for that input.
JDK_INVALID = [case for case in INVALID if case[0] != "binary-dollar-not-entry-alias"]


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-member-reference")
def test_identical_invalid_member_projects_have_selected_jdk_rejections(tmp_path, request):
    reference_batch(tmp_path, request, JDK_INVALID, runtime=False)
    for name, sources, _, _ in JDK_INVALID:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "1", name
        diagnostic = (directory / "compiler.stderr.bin").read_text()
        assert any(re.search(re.escape(Path(filename).name) + r":\d+:", diagnostic) for filename in sources), (name, diagnostic)
