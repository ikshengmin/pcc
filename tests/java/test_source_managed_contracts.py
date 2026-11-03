"""Shared compiler plans consumed by Java source analysis, not native objects.

JLS21 4.3, 4.12.5, 8.3, 10.1-10.7, 15.9, 15.10 and 3.10.5:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-10.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.9
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.10.5
All new model, native-boundary and independent JDK obligations are CI-only,
execution UNRUN. The JDK lane alone allocates objects from the exact original.
"""
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from pcc.frontends.java import (
    UnsupportedJava,
    analyze_class_sources,
    compile_source,
    compile_sources,
)
from pcc.frontends.java.managed_objects import type_identity
from pcc.frontends.java.source_construction_model import CLASS_CREATION_STEPS
from pcc.frontends.java.source_string_model import StringSourceConstant
from pcc.frontends.java.source_types import ArrayType, BootstrapStringType, ClassType
from pcc.support.managed_objects import (
    MANAGED_REQUIREMENTS,
    ManagedContractError,
    ManagedValuePlan,
    pointer_map,
    unmet_requirements,
)
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_unicode_escapes import JDK_REASON


MIXED_SOURCE = (
    'class ArrayExample{int count=42;ArrayExample next=this;'
    'int[] values=new int[]{41};String label="\\uD800😀";'
    'ArrayExample self(){return this;}'
    'int run(){values[0]++;return next==self()?values[0]:0;}}'
)


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def expression_nodes(program):
    for field in program.fields:
        if field.initializer is not None:
            yield from walk(field.initializer)
    for method in (*program.methods, *program.constructors):
        yield from walk(method.body)
    for initializer in program.instance_initializers:
        yield from walk(initializer)


def test_checked_mixed_fields_produce_shared_edges_without_inventing_offsets(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("managed model reached IR"))
    sources = {"ArrayExample.java": MIXED_SOURCE}
    project = analyze_class_sources(sources)
    model = project.models["ArrayExample"]
    value = model.managed_value
    assert isinstance(value, ManagedValuePlan)
    assert value.identity == type_identity(ClassType("ArrayExample"))
    assert value.storage == "managed"
    layout = value.layout
    scalar, = layout.scalars
    assert (scalar.path, scalar.kind, scalar.size, scalar.alignment) == (("count",), "int", 4, 4)
    assert scalar.offset is None
    assert [(edge.path, edge.target) for edge in layout.references] == [
        (("next",), type_identity(ClassType("ArrayExample"))),
        (("values",), type_identity(ArrayType("int"))),
        (("label",), type_identity(BootstrapStringType())),
    ]
    assert all(edge.offset is None and edge.role == "owned" and edge.nullable for edge in layout.references)
    assert layout.fixed_size is layout.alignment is None
    assert model.payload_size is model.payload_alignment is None
    assert all(slot.offset is None for slot in model.fields)
    with pytest.raises(ManagedContractError, match="unresolved"):
        pointer_map(layout, pointer_size=8)
    assert set(MANAGED_REQUIREMENTS) | {"object_abi"} <= set(model.managed_requirements)
    assert "managed-literal-materialization" in model.managed_requirements
    assert not model.runtime_registered and not model.semantic_context_complete
    assert "java/lang/String" not in project.classes and "java/lang/Object" not in project.classes
    assert sources == {"ArrayExample.java": MIXED_SOURCE}
    with pytest.raises(FrozenInstanceError):
        value.storage = "static"


def test_primitive_payload_map_does_not_claim_the_object_header_or_runtime_registration():
    project = analyze_class_sources({"A.java": "class A{byte first;long second;char third;static final int K=1;}"})
    model = project.models["A"]
    layout = model.managed_value.layout
    assert [(item.path, item.offset, item.size) for item in layout.scalars] == [
        (("first",), 0, 1), (("second",), 8, 8), (("third",), 16, 2),
    ]
    assert (layout.fixed_size, layout.alignment) == (24, 8)
    assert (model.payload_size, model.payload_alignment) == (24, 8)
    resolved = pointer_map(layout, pointer_size=8)
    assert resolved.owned == resolved.borrowed == resolved.update_only == ()
    assert set(MANAGED_REQUIREMENTS) | {"object_abi"} <= set(model.managed_requirements)
    assert model.managed_value.storage == "managed" and not model.runtime_registered


def test_common_identity_keeps_packages_and_unresolved_bootstrap_string_distinct():
    sources = {
        "A.java": "package app;import provider.String;class A{String value;}",
        "String.java": "package provider;public class String{}",
        "Other.java": "package other;class String{}",
        "B.java": "package app;class B{String value;}",
    }
    project = analyze_class_sources(sources)
    supplied = project.models["app/A"].managed_value.layout.references[0].target
    bootstrap = project.models["app/B"].managed_value.layout.references[0].target
    assert supplied.key == "provider/String" and bootstrap.key == "java/lang/String"
    assert supplied != bootstrap != type_identity(ClassType("java/lang/String"))
    assert project.models["provider/String"].managed_value.identity != project.models["other/String"].managed_value.identity
    assert type_identity(ArrayType("byte")) != type_identity(ArrayType("boolean"))
    renamed_inputs = analyze_class_sources({"DiagnosticOnly.java": "package app;class A{}"})
    assert renamed_inputs.models["app/A"].managed_value.identity == project.models["app/A"].managed_value.identity


@pytest.mark.parametrize("component,size", [
    ("boolean", 1), ("byte", 1), ("short", 2), ("char", 2), ("int", 4), ("long", 8),
])
def test_checked_array_expressions_keep_known_elements_and_unresolved_object_extent(component, size):
    text = ("class A{static " + component + "[] make(int n){" + component
            + "[] empty={};return new " + component + "[n];}}")
    project = analyze_class_sources({"A.java": text})
    nodes = [node for node in expression_nodes(project.classes["A"])
             if node.kind in {"array_initializer", "array_new"}]
    assert len(nodes) == 2
    for node in nodes:
        value = node.managed_value
        assert value.identity == type_identity(ArrayType(component)) and value.storage == "managed"
        layout = value.layout
        assert layout.fixed_size is layout.alignment is None
        assert layout.element_stride == size
        assert (layout.element.fixed_size, layout.element.alignment) == (size, size)
        scalar, = layout.element.scalars
        assert (scalar.kind, scalar.offset, scalar.size) == (component, 0, size)
        assert pointer_map(layout.element, pointer_size=8).owned == ()
        with pytest.raises(ManagedContractError, match="unresolved"):
            pointer_map(layout, pointer_size=8)
        assert "java-array-header-and-length-layout" in unmet_requirements(value)
        assert "rooted_lifetime" in unmet_requirements(value)
    assert nodes[1].runtime_steps == ("dimension", "negative-size-check", "allocate-zeroed-array")


def test_string_utf16_facts_are_separate_from_managed_value_and_interned_identity():
    text = ('class A{static final String FIRST="\\uD800😀";static final String SECOND="different";'
            'String text(){return FIRST+"x";}}')
    project = analyze_class_sources({"A.java": text})
    first, second = project.classes["A"].fields
    assert first.string_constant == StringSourceConstant((0xd800, 0xd83d, 0xde00))
    assert first.string_constant != second.string_constant
    assert first.initializer.managed_value.identity == second.initializer.managed_value.identity
    result = project.classes["A"].methods[0].body.children[0].children[0]
    assert result.string_constant.units == (0xd800, 0xd83d, 0xde00, ord("x"))
    for node in (first.initializer, second.initializer, result):
        value = node.managed_value
        assert value.identity == type_identity(BootstrapStringType())
        assert value.storage == "unresolved" and value.layout is None
        assert {"owned-bootstrap-type-definition", "managed-literal-materialization",
                "interned-reference-identity", "object_abi", "runtime_registration"} <= set(unmet_requirements(value))
        assert node.constant is None
        assert not hasattr(node, "object") and not hasattr(node, "address")
    with pytest.raises(FrozenInstanceError):
        first.string_constant.units = ()


def test_checked_reference_reads_calls_and_nulls_do_not_become_runtime_objects():
    project = analyze_class_sources({"ArrayExample.java": MIXED_SOURCE})
    nodes = list(expression_nodes(project.classes["ArrayExample"]))
    references = [node for node in nodes if isinstance(node.dtype, (ArrayType, ClassType, BootstrapStringType))]
    assert {"this", "instance_field", "receiver_call", "array_initializer", "string"} <= {node.kind for node in references}
    for node in references:
        assert node.managed_value.identity == type_identity(node.dtype)
        assert node.managed_value.storage != "static"
        assert "runtime_registration" in unmet_requirements(node.managed_value)
    null_project = analyze_class_sources({"A.java": "class A{A value(){return true?this:null;}String text(String s){return s;}}"})
    null_nodes = list(expression_nodes(null_project.classes["A"]))
    null, = [node for node in null_nodes if node.kind == "null"]
    assert null.managed_value is None
    string_read = next(node for node in null_nodes if isinstance(node.dtype, BootstrapStringType))
    assert "managed-literal-materialization" not in unmet_requirements(string_read.managed_value)


def test_construction_bindings_and_order_are_preserved_beneath_the_shared_plan():
    project = analyze_class_sources({"A.java": "class A{A(){}A(int n){}A make(){return new A(1);}}"})
    model = project.models["A"]
    node = model.source.methods[0].body.children[0].children[0]
    assert node.kind == "class_new" and node.runtime_steps == CLASS_CREATION_STEPS
    assert node.symbol is next(item for item in model.constructors if item.parameter_types == ("int",))
    assert node.managed_value.identity == model.managed_value.identity
    assert node.managed_value.layout is None and node.constant is None
    assert "runtime_registration" in unmet_requirements(node.managed_value)
    obligation, = model.compiler_context_obligations
    assert obligation.constructor_descriptor == "(I)V" and node.token in obligation.tokens
    assert not model.runtime_registered


@pytest.mark.parametrize("text", [
    MIXED_SOURCE,
    "class ArrayExample{static int run(){int[] a=new int[]{42};return a[0];}}",
    'class ArrayExample{static String run(){return "original";}}',
    "class ArrayExample{static ArrayExample run(){return new ArrayExample();}}",
])
def test_consumed_common_plans_never_admit_native_managed_lowering(text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("managed reference reached native lowering"))
    analyze_class_sources({"ArrayExample.java": text})
    for invoke in (lambda: compile_source(text, filename="ArrayExample.java"),
                   lambda: compile_sources({"ArrayExample.java": text}, entry_class="ArrayExample")):
        with pytest.raises(UnsupportedJava):
            invoke()
    for dtype in (ArrayType("int"), ClassType("ArrayExample"), BootstrapStringType(), "null"):
        for lane in (SourceLowerer.scalar_type, SourceLowerer.abi_type):
            with pytest.raises(UnsupportedJava) as caught:
                lane(dtype)
            assert caught.value.code == "java.unsupported.declared-result-type"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-array-model-reference")
def test_exact_managed_field_array_and_utf16_original_against_independent_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, [("managed-fields", MIXED_SOURCE)], [], [],
        invocation=r'new ArrayExample().run()==42&&new ArrayExample().label.equals("\uD800😀")?42:0',
        source_input=Path(__file__))
