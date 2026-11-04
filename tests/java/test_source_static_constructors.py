"""Private utility constructor declarations on the native scalar source route.

JLS21 8.8, 8.8.3, 8.8.7, 8.8.9, 8.8.10 and 15.9:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.8
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9

Source-authored CI obligations, execution UNRUN. The native compiler retains
explicit private no-arg empty declarations but never executes a constructor,
manufactures a default constructor, or emits an instance ABI. Every ordinary
method in every complete supplied source map is still checked and emitted.
Native and independent selected-JDK lanes use identical source bytes. The JDK
accepts valid capability-boundary sources; only invalid Java gets a reference
rejection. No extracted library methods or fabricated dependencies qualify a
module. The unchanged complete RegularizedGamma boundary lives in the member
class corpus and is not complete-module acceptance.
"""
from copy import deepcopy
from dataclasses import replace
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
from pcc.frontends.java.source_lowering import SourceLowerer
from pcc.frontends.java.source_project import SourceProject
from pcc.frontends.java.source_sema import Binding
from pcc.frontends.java.source_syntax import Node, SourceField, SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_local_inference import LANES, descriptor, observer_source
from tests.java.test_source_project import unit
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, record_process, tool_identity
from tests.java.test_source_scalars import NATIVE_REASON
from tests.java.test_source_static_member_classes import (
    JDK_REASON,
    PRIMITIVE_VALUES,
    REFERENCE_DRIVER,
    expected_bytes,
    write_sources,
)


TARGET = "x86_64-unknown-linux-gnu"
CONSTRUCTOR_CODE = "java.unsupported.constructor-execution"


def utility(members, *, name="Utility", package="p", imports=""):
    return {(package.replace(".", "/") + "/" if package else "") + name + ".java":
            unit(package, name, members, imports=imports)}


def row(name, members, *, entry="p.Utility", expected=42):
    return name, utility(members), entry, "I", (), expected


# Each entry is a complete source map, not a run-body extracted from a class.
CASES = [
    row("private-empty", "private Utility(){}public static int run(){return 42;}"),
    row("private-comment-only", "private Utility(){/* intentionally empty */\n// no statements\n}"
        "public static int run(){return 42;}"),
    row("private-after-method", "public static int run(){return 42;}private Utility(){}"),
    row("constructor-and-same-spelling-method", "private Utility(){}"
        "public static int Utility(){return 40;}public static int Utility(int n){return n;}"
        "public static int run(){return Utility()+Utility(2);}"),
    row("void-same-spelling-method", "private Utility(){}public static void Utility(){return;}"
        "public static int run(){Utility();return 42;}"),
    row("private-nest-members", "private Utility(){}private static final int BASE=40;"
        "private static int add(int n){return BASE+n;}public static int run(){return Inner.read();}"
        "private static class Inner{private Inner(){}private static int read(){return add(2);}}"),
    row("siblings-deep-and-empty-owner", "private Utility(){}"
        "public static int run(){return Left.f()+Right.f();}"
        "static class Left{private Left(){}private static int f(){return 7;}"
        "static class Deep{private Deep(){}private static int f(){return Left.f()+28;}}}"
        "static class Right{private Right(){}private static int f(){return Left.Deep.f();}}"
        "static class Empty{private Empty(){}}"),
    row("canonical-deep-entry", "private Utility(){}public static class Middle{private Middle(){}"
        "public static class Deep{private Deep(){}public static int run(){return 42;}}}",
        entry="p.Utility.Middle.Deep"),
    row("all-uncalled-methods-retained", "private Utility(){}public static int run(){return 42;}"
        "private static int unused(int n){return n+7;}static void idle(){return;}"
        "static class Uncalled{private Uncalled(){}static long unused(){return 9L;}"
        "static class Deep{private Deep(){}static double unused(){return -0.0;}}}"),
    row("same-simple-constructor-in-distinct-nests", "private Utility(){}"
        "public static int run(){return A.Same.f()+B.Same.f();}"
        "static class A{private A(){}static class Same{private Same(){}static int f(){return 20;}}}"
        "static class B{private B(){}static class Same{private Same(){}static int f(){return 22;}}}"),
    row("literal-dollar-nested", "private Utility(){}public static int run(){return Inner$Legal.f();}"
        "static class Inner$Legal{private Inner$Legal(){}static int f(){return 42;}}"),
    ("literal-dollar-top-level", utility("private Utility$Legal(){}public static int run(){return 42;}",
                                       name="Utility$Legal"), "p.Utility$Legal", "I", (), 42),
    ("canonical-type-and-static-imports", {
        "app/Main.java": unit("app", "Main", "private Main(){}public static int run(){return Inner.f()+TWO;}",
                              imports="import provider.Tools.Inner;import static provider.Tools.TWO;"),
        "provider/Tools.java": unit("provider", "Tools", "private Tools(){}public static final int TWO=2;"
                                    "public static class Inner{private Inner(){}public static int f(){return 40;}}"),
     }, "app.Main", "I", (), 42),
    ("import-method-with-constructor-spelling", {
        "app/Main.java": unit("app", "Main", "private Main(){}public static int run(){return Tools();}",
                              imports="import static provider.Tools.Tools;"),
        "provider/Tools.java": unit("provider", "Tools", "private Tools(){}public static int Tools(){return 42;}"),
     }, "app.Main", "I", (), 42),
    ("multiple-units-no-entry-in-helper", {
        "p/Main.java": unit("p", "Main", "private Main(){}public static int run(){return Helper.add(40,2);}"),
        "p/Helper.java": unit("p", "Helper", "private Helper(){}static int add(int a,int b){return a+b;}"
                             "private static int unused(){return 7;}"),
        "p/Empty.java": unit("p", "Empty", "private Empty(){}"),
        "p/Default.java": unit("p", "Default", ""),
     }, "p.Main", "I", (), 42),
    ("literal-dollar-is-not-nest-separator", {
        **utility("private Utility(){}public static int run(){return Inner.f()+Utility$Legal.f();}"
                  "static class Inner{private Inner(){}static int f(){return 40;}}"),
        "p/Utility$Legal.java": unit("p", "Utility$Legal", "private Utility$Legal(){}static int f(){return 2;}"),
     }, "p.Utility", "I", (), 42),
    # Exact former class-model boundary, with no added run or changed access.
    ("former-class-model-private-original", {
        "Record.java": "class Record{private Record(){}static int run(){return 42;}}",
     }, "Record", "I", (), 42),
]
CASES += [
    ("primitive-" + dtype, utility("private Utility(){}public static " + dtype + " run(" + dtype + " input){"
        "return Inner.copy(input);}static class Inner{private Inner(){}static " + dtype + " copy(" + dtype + " input){"
        "var local=input;final " + dtype + " result;result=Sibling.copy(local);return result;}}"
        "static class Sibling{private Sibling(){}static " + dtype + " copy(" + dtype + " input){return input;}}"),
     "p.Utility", LANES[dtype], ((LANES[dtype], value),), value)
    for dtype, value in PRIMITIVE_VALUES.items()
]
CASES += [
    ("raw-" + name, utility("private Utility(){}public static " + dtype + " run(" + dtype + " input){return Inner.copy(input);}"
        "static class Inner{private Inner(){}static " + dtype + " copy(" + dtype + " input){return input;}}"),
     "p.Utility", lane, ((lane, bits),), bits)
    for name, dtype, lane, bits in (
        ("float-subnormal", "float", "F", 0x00000001),
        ("double-subnormal", "double", "D", 0x0000000000000001),
        ("float-max-finite", "float", "F", 0x7f7fffff),
        ("double-max-finite", "double", "D", 0x7fefffffffffffff),
    )
]


def compile_case(sources, entry, lane, arguments):
    return compile_sources(sources, entry_class=entry, descriptor=descriptor(lane, arguments))


def forbid_lowering(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("rejected original reached lowering"))


@pytest.mark.parametrize("name,sources,entry,lane,arguments,expected", CASES, ids=[case[0] for case in CASES])
def test_complete_utility_maps_keep_all_ordinary_methods_and_no_constructor_functions(
        name, sources, entry, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    before = dict(sources)
    forward = compile_case(sources, entry, lane, arguments)
    backward = compile_case(dict(reversed(tuple(sources.items()))), entry, lane, arguments)
    project = SourceProject([SourceParser(text, filename).parse() for filename, text in sources.items()])
    required = {(program.name, method.token.text, method.descriptor)
                for program in project.programs for method in program.methods}
    forbidden = {(program.name, constructor.token.text, constructor.descriptor)
                 for program in project.programs for constructor in program.constructors}
    assert forbidden
    # A declared void method may legally share the constructor spelling and
    # descriptor. Its ordinary method symbol is required, never a ctor body.
    forbidden -= required
    assert set(forward.emitted_class_methods) == required
    assert len(forward.emitted_class_methods) == len(required)
    assert set(forward.emitted_class_methods).isdisjoint(forbidden)
    assert forward.emitted_class_methods == backward.emitted_class_methods
    assert str(forward.module) == str(backward.module)
    ir_text = str(forward.module)
    assert all(method_symbol(*method) in ir_text for method in required)
    assert all(method_symbol(*constructor) not in ir_text for constructor in forbidden)
    assert "bc_" not in ir_text and "<init>" not in ir_text
    assert forward.emit_object().startswith(b"\x7fELF")
    assert sources == before


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources,entry,lane,arguments,expected", CASES, ids=[case[0] for case in CASES])
def test_complete_utility_maps_execute_exact_owned_primitive_bytes(
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


@pytest.mark.parametrize("api", ("source", "sources"))
def test_both_native_entrypoints_retain_exact_private_constructor_original(api, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    text = "class Record{private Record(){}static int run(){return 42;}}"
    compiled = (compile_source(text, filename="Record.java") if api == "source" else
                compile_sources({"Record.java": text}, entry_class="Record"))
    assert compiled.emitted_class_methods == (("Record", "run", "()I"),)


def test_parser_retains_explicit_identity_and_native_sema_never_manufactures_defaults():
    sources = {
        "p/Utility$Legal.java": unit("p", "Utility$Legal", "private Utility$Legal(){/* empty */}"
            "public static int run(){return 42;}static class Inner$Legal{private Inner$Legal(){}}"
            "static class NoDeclaration{}"),
        "p/Default.java": unit("p", "Default", ""),
    }
    parsed = [SourceParser(text, filename).parse() for filename, text in sources.items()]
    outer, default = parsed
    inner, no_declaration = outer.member_classes
    originals = (outer.constructors[0], inner.constructors[0])
    assert not default.constructors and not no_declaration.constructors
    project = SourceProject(parsed).analyze()
    assert project.classes[outer.name] is outer
    assert outer.constructors[0] is originals[0] and inner.constructors[0] is originals[1]
    for program, constructor in ((outer, originals[0]), (inner, originals[1])):
        assert constructor.owner == program.name and constructor.token.text == program.simple_name
        assert constructor.is_constructor and not constructor.implicit
        assert constructor.result == "void" and constructor.descriptor == "()V"
        assert constructor.modifiers == frozenset({"private"})
        assert constructor.parameters == constructor.parameter_types == constructor.parameter_final == ()
        assert constructor.body.kind == "block" and constructor.body.children == []
        assert constructor.locals == [] and constructor.constructor_target is None
        assert constructor.initialization_steps == ()
        assert constructor not in program.methods
    assert not default.constructors and not no_declaration.constructors
    model = analyze_class_sources({"Record.java": "class Record{private Record(){}static int run(){return 42;}}"}).models["Record"]
    assert len(model.constructors) == 1
    assert not model.constructors[0].implicit
    assert model.constructors[0].modifiers == frozenset({"private"})
    assert model.constructors[0].constructor_target == "java/lang/Object::<init>()V"


def boundary(name, members, code="constructor-execution"):
    return name, utility(members), "p.Utility", "java.unsupported." + code


# These complete sources are valid Java. Native refusal is a capability gap,
# including semantically harmless statements: syntactically empty means {}.
CAPABILITIES = [
    boundary("package-constructor", "Utility(){}public static int run(){return 42;}"),
    boundary("public-constructor", "public Utility(){}public static int run(){return 42;}"),
    boundary("protected-constructor", "protected Utility(){}public static int run(){return 42;}"),
    boundary("semicolon-body", "private Utility(){;}public static int run(){return 42;}"),
    boundary("return-body", "private Utility(){return;}public static int run(){return 42;}"),
    boundary("empty-nested-block-body", "private Utility(){{}}public static int run(){return 42;}"),
    boundary("local-body", "private Utility(){int n=42;}public static int run(){return 42;}"),
    boundary("scalar-call-body", "private Utility(){touch();}static void touch(){}public static int run(){return 42;}"),
    boundary("dead-branch-body", "private Utility(){if(false){touch();}}static void touch(){}public static int run(){return 42;}"),
    boundary("loop-body", "private Utility(){for(int n=0;n<1;n++){}}public static int run(){return 42;}"),
    boundary("do-loop-body", "private Utility(){do{}while(false);}public static int run(){return 42;}"),
    boundary("while-loop-body", "private Utility(){while(test()){}}static boolean test(){return false;}public static int run(){return 42;}"),
    boundary("overloaded-constructors", "private Utility(){}private Utility(int n){}public static int run(){return 42;}"),
    boundary("uncalled-nested-constructor", "private Utility(){}public static int run(){return 42;}"
             "static class Unused{private Unused(){;}}"),
    boundary("explicit-super", "private Utility(){super();}public static int run(){return 42;}", "object-expressions"),
    boundary("explicit-this", "private Utility(){this(42);}private Utility(int n){}public static int run(){return 42;}", "object-expressions"),
    boundary("new-in-entry", "private Utility(){}public static int run(){new Utility();return 42;}", "object-expressions"),
    boundary("new-in-uncalled-method", "private Utility(){}public static int run(){return 42;}"
             "static void unused(){new Utility();}", "object-expressions"),
    boundary("new-in-dead-branch", "private Utility(){}public static int run(){if(false){new Utility();}return 42;}", "object-expressions"),
    boundary("new-from-same-nest", "private Utility(){}public static int run(){return 42;}"
             "static class Inner{private Inner(){}static void unused(){new Utility();new Inner();}}", "object-expressions"),
    boundary("instance-state", "private Utility(){}int value;public static int run(){return 42;}", "instance-methods"),
    boundary("instance-method", "private Utility(){}int unused(){return 7;}public static int run(){return 42;}", "instance-methods"),
    boundary("instance-initializer", "private Utility(){}{}public static int run(){return 42;}", "instance-methods"),
    boundary("static-initializer", "private Utility(){}static {}public static int run(){return 42;}", "static-initialization"),
    boundary("mutable-static", "private Utility(){}static int value=42;public static int run(){return value;}", "static-initialization"),
    boundary("nonconstant-static", "private Utility(){}static final int VALUE=run();public static int run(){return 42;}", "static-initialization"),
    boundary("nonstatic-member", "private Utility(){}class Inner{private Inner(){}}public static int run(){return 42;}", "member-types"),
    boundary("uncalled-string-method", 'private Utility(){}public static int run(){return 42;}static boolean unused(){return "text"=="text";}', "string-literals"),
    boundary("throws-constructor", "private Utility() throws Exception {}public static int run(){return 42;}", "throwable-types"),
]
CAPABILITIES += [
    boundary("parameter-" + dtype, "private Utility(" + dtype + " input){}public static int run(){return 42;}")
    for dtype in LANES
]
CAPABILITIES += [
    boundary("final-parameter", "private Utility(final int input){}public static int run(){return 42;}"),
    boundary("named-parameter", "private Utility(Object input){}public static int run(){return 42;}", "declared-parameter-type"),
    boundary("array-parameter", "private Utility(int[] input){}public static int run(){return 42;}", "array-storage"),
    ("uncalled-source-unit", {
        **utility("private Utility(){}public static int run(){return 42;}"),
        "p/Unused.java": unit("p", "Unused", "private Unused(){;}static int unused(){return 7;}"),
     }, "p.Utility", CONSTRUCTOR_CODE),
]


@pytest.mark.parametrize("name,sources,entry,code", CAPABILITIES, ids=[case[0] for case in CAPABILITIES])
def test_valid_java_outside_constructor_declaration_capability_is_not_discarded(name, sources, entry, code, monkeypatch):
    forbid_lowering(monkeypatch)
    before = dict(sources)
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(sources, entry_class=entry)
    assert caught.value.code == code
    assert sources == before


def invalid(name, members, message):
    return name, utility(members), "p.Utility", message


INVALID = [
    invalid("duplicate-empty-constructor", "private Utility(){}private Utility(){}public static int run(){return 42;}", "duplicate constructor"),
    invalid("duplicate-access-not-overload", "public Utility(){}private Utility(){}public static int run(){return 42;}", "duplicate constructor"),
    invalid("duplicate-public-parameter-signature", "public Utility(int first){}private Utility(int second){}public static int run(){return 42;}", "duplicate constructor"),
    invalid("duplicate-parameter-signature", "private Utility(int first){}private Utility(int second){}public static int run(){return 42;}", "duplicate constructor"),
    invalid("duplicate-nested-constructor", "private Utility(){}public static int run(){return 42;}"
            "static class Inner{private Inner(){}private Inner(){}}", "duplicate constructor"),
    invalid("repeated-private", "private private Utility(){}public static int run(){return 42;}", "duplicate modifier"),
    invalid("conflicting-access", "private public Utility(){}public static int run(){return 42;}", "mutually exclusive access modifiers"),
    invalid("repeated-final-parameter", "private Utility(final final int input){}public static int run(){return 42;}", "duplicate final parameter modifier"),
    invalid("uncalled-invalid-local", "private Utility(){}public static int run(){return 42;}"
            "private static int unused(){int n;return n;}", "not definitely assigned"),
    invalid("uncalled-invalid-deep-method", "private Utility(){}public static int run(){return 42;}"
            "static class Deep{private Deep(){}static int unused(){return missing();}}", "unresolved same-class"),
    invalid("uncalled-invalid-return", "private Utility(){}public static int run(){return 42;}"
            "static int unused(){return true;}", "integer value required"),
    invalid("duplicate-ordinary-method", "private Utility(){}public static int run(){return 42;}"
            "static int unused(){return 7;}static long unused(){return 9L;}", "duplicate method signature"),
    invalid("constructor-is-not-simple-call", "private Utility(){}public static int run(){Utility();return 42;}", "unresolved same-class"),
    invalid("constructor-is-not-qualified-call", "private Utility(){}public static int run(){Utility.Utility();return 42;}", "unresolved source static method"),
    ("constructor-is-not-static-import", {
        "app/Main.java": unit("app", "Main", "private Main(){}public static int run(){return 42;}",
                              imports="import static provider.Tools.Tools;"),
        "provider/Tools.java": unit("provider", "Tools", "private Tools(){}"),
     }, "app.Main", "no accessible static member"),
    ("private-method-stays-private", {
        **utility("private Utility(){}private static int hidden(){return 42;}"),
        "p/Main.java": unit("p", "Main", "private Main(){}public static int run(){return Utility.hidden();}"),
     }, "p.Main", "source static method is not accessible"),
    ("private-type-stays-private", {
        **utility("private Utility(){}private static class Hidden{private Hidden(){}public static int f(){return 42;}}"),
        "p/Main.java": unit("p", "Main", "private Main(){}public static int run(){return Utility.Hidden.f();}"),
     }, "p.Main", "source class is not accessible"),
]
INVALID += [
    invalid("illegal-" + modifier + "-constructor", "private " + modifier + " Utility(){}public static int run(){return 42;}",
            "invalid constructor modifier")
    for modifier in ("static", "final", "abstract", "native", "synchronized", "strictfp")
]


@pytest.mark.parametrize("name,sources,entry,message", INVALID, ids=[case[0] for case in INVALID])
def test_invalid_originals_remain_ordinary_java_errors_before_lowering(name, sources, entry, message, monkeypatch):
    forbid_lowering(monkeypatch)
    before = dict(sources)
    with pytest.raises(JavaError, match=message) as caught:
        compile_sources(sources, entry_class=entry)
    assert type(caught.value) is JavaError and caught.value.code is None
    assert sources == before


@pytest.mark.parametrize("entry", ("Utility", "<init>"))
def test_constructor_declaration_cannot_be_selected_as_native_entry(entry, monkeypatch):
    forbid_lowering(monkeypatch)
    with pytest.raises(JavaError, match="source entry method not found") as caught:
        compile_sources(utility("private Utility(){}public static int run(){return 42;}"),
                        entry_class="p.Utility", entry=entry, descriptor="()V")
    assert type(caught.value) is JavaError and caught.value.code is None


def test_same_spelling_void_method_is_an_ordinary_selectable_entry(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    text = "class Utility{private Utility(){}static void Utility(){return;}static int run(){return 42;}}"
    compiled = compile_source(text, entry="Utility", descriptor="()V")
    assert compiled.entry == ("Utility", "()V")
    assert set(compiled.emitted_class_methods) == {("Utility", "Utility", "()V"), ("Utility", "run", "()I")}


AST_MUTATIONS = (
    "body-statement", "body-not-block", "constructor-flag", "implicit-flag", "name", "owner", "result",
    "public-access", "package-access", "parameter-name", "parameter-type", "parameter-final",
    "locals", "delegation", "initialization-steps", "class-initializers", "class-initialization-steps",
    "instance-field", "instance-method", "duplicate", "illegal-modifier", "conflicting-access",
)


def mutate_constructor(program, mutation):
    """Known retained AST fields only; no arbitrary malformed-object claim."""
    constructor, = program.constructors
    if mutation == "body-statement":
        constructor.body.children.append(Node("empty", constructor.body.token))
    elif mutation == "body-not-block":
        constructor.body.kind = "empty"
    elif mutation == "constructor-flag":
        constructor.is_constructor = False
    elif mutation == "implicit-flag":
        constructor.implicit = True
    elif mutation == "name":
        constructor.token = replace(constructor.token, text="Other")
    elif mutation == "owner":
        constructor.owner = "p/Other"
    elif mutation == "result":
        constructor.result = "int"
    elif mutation == "public-access":
        constructor.modifiers = frozenset({"public"})
    elif mutation == "package-access":
        constructor.modifiers = frozenset()
    elif mutation == "parameter-name":
        constructor.parameters = (replace(constructor.token, text="value"),)
    elif mutation == "parameter-type":
        constructor.parameter_types = ("int",)
    elif mutation == "parameter-final":
        constructor.parameter_final = (False,)
    elif mutation == "locals":
        constructor.locals.append(Binding("value", 0))
    elif mutation == "delegation":
        constructor.constructor_target = "java/lang/Object::<init>()V"
    elif mutation == "initialization-steps":
        constructor.initialization_steps = (Node("empty", constructor.body.token),)
    elif mutation == "class-initializers":
        program.instance_initializers = (Node("block", constructor.body.token),)
    elif mutation == "class-initialization-steps":
        program.initialization_steps = (Node("empty", constructor.body.token),)
    elif mutation == "instance-field":
        program.fields += (SourceField(replace(constructor.token, text="value"), "int", None,
                                      frozenset(), program.name, len(program.fields)),)
    elif mutation == "instance-method":
        program.methods[0].modifiers = frozenset({"private"})
    elif mutation == "duplicate":
        program.constructors += (deepcopy(constructor),)
    elif mutation == "illegal-modifier":
        constructor.modifiers = frozenset({"private", "static"})
    elif mutation == "conflicting-access":
        constructor.modifiers = frozenset({"private", "public"})
    else:
        raise AssertionError(mutation)


@pytest.mark.parametrize("phase", ("before-sema", "after-sema"))
@pytest.mark.parametrize("owner", ("p/Utility", "p/Utility$Deep", "p/ZUnused"))
@pytest.mark.parametrize("mutation", AST_MUTATIONS)
def test_actual_constructor_ast_is_rechecked_before_any_function_is_created(phase, owner, mutation, monkeypatch):
    from pcc.frontends.java import source_lowering
    forbid_bytecode_or_external_owner(monkeypatch)
    sources = {
        **utility("private Utility(){}public static int run(){return 42;}"
                  "static class Deep{private Deep(){}static int unused(){return 7;}}"),
        "p/ZUnused.java": unit("p", "ZUnused", "private ZUnused(){}static int unused(){return 9;}"),
    }
    programs = [SourceParser(text, filename).parse() for filename, text in sources.items()]
    if phase == "after-sema":
        project = SourceProject(programs).analyze()
        target = project.classes[owner]
    else:
        target = {program.name: program for program in
                  (programs[0], programs[0].member_classes[0], programs[1])}[owner]
    mutate_constructor(target, mutation)
    monkeypatch.setattr(source_lowering.ir, "Function", lambda *args, **kwargs:
                        pytest.fail("a constructor mutation passed admission before function emission"))
    lowerer = None
    with pytest.raises(JavaError) as caught:
        if phase == "before-sema":
            project = SourceProject(programs).analyze()
        lowerer = SourceLowerer(project.classes["p/Utility"], TARGET, project.programs)
        lowerer.lower(("run", "()I"))
    if mutation in {"duplicate", "illegal-modifier", "conflicting-access"}:
        assert type(caught.value) is JavaError and caught.value.code is None
        assert ("duplicate constructor" if mutation == "duplicate" else "invalid constructor modifier") in str(caught.value)
    else:
        assert isinstance(caught.value, UnsupportedJava) and caught.value.code == CONSTRUCTOR_CODE
    if lowerer is not None:
        assert lowerer.functions == {}


# Same independent batch compiler as the member-class corpus. Distinct mode
# labels distinguish valid capability sources from invalid Java. Originals are
# never transformed; the separate observer is added only for runtime cases.
def reference_batch(tmp_path, request, cases, *, mode):
    assert mode in {"run", "accept", "reject"}
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    inputs = [Path(__file__), Path(__file__).with_name("owned_execution.py"),
              Path(__file__).with_name("test_source_local_inference.py"),
              Path(__file__).with_name("test_source_static_member_classes.py"),
              Path(__file__).with_name("test_source_raw_reference.py")]
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    owner = {**before, "reference_owner": "explicit existing development-only JDK",
             "version": version.group(1), "source_level": level, "target_level": level,
             "platform_contract": "selected current JDK platform, not --release compatibility",
             "production_dependency": False, "source_rewritten": False, "mode": mode,
             "complete_supplied_projects": True, "managed_runtime_qualified": False,
             "constructor_execution_qualified": False}
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
        if mode == "run":
            entry, lane, arguments, _ = rest
            write_sources(directory, {"PccNarrowObserve.java": observer_source(entry, lane, arguments)}, retained)
        rows.append("\t".join([name, mode, *sources]))
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
@pytest.mark.xdist_group(name="java-static-constructor-reference")
def test_identical_complete_utility_maps_match_selected_jdk_raw_processes(tmp_path, request, monkeypatch):
    java = reference_batch(tmp_path, request, CASES, mode="run")
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
            "original_source_rewritten": False, "complete_supplied_project": True,
            "constructor_execution_qualified": False,
            "descriptor": descriptor(lane, arguments), "arguments": arguments,
            "observer_sha256": digest(directory / "PccNarrowObserve.java"),
            "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
        }, indent=2))
        assert actual == reference == (0, expected_bytes(lane, expected), b""), name


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-constructor-reference")
def test_identical_constructor_capability_originals_are_valid_selected_jdk_sources(tmp_path, request):
    reference_batch(tmp_path, request, CAPABILITIES, mode="accept")
    for name, sources, _, _ in CAPABILITIES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", (name, (directory / "compiler.stderr.bin").read_bytes())


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-static-constructor-reference")
def test_identical_invalid_constructor_originals_have_selected_jdk_rejections(tmp_path, request):
    reference_batch(tmp_path, request, INVALID, mode="reject")
    for name, sources, _, _ in INVALID:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "1", name
        diagnostic = (directory / "compiler.stderr.bin").read_text()
        assert any(re.search(re.escape(Path(filename).name) + r":\d+:", diagnostic) for filename in sources), (name, diagnostic)
