"""Supplied-source class creation binding, never managed allocation or emission.

JLS 6.5.5,8.8.9,12.4.1,15.9.1/3/4. The fixed-arity strict-invocation
subset reuses actual source constructor declarations and canonical class types.
Runtime ordering below is an unresolved contract, not an executed plan.
"""
from dataclasses import dataclass

from .source_syntax import Node
from .source_types import BootstrapStringType, ClassType, select_method


CLASS_CREATION_STEPS = (
    "initialize-class", "allocate-and-default-initialize-or-throw-OOME",
    "arguments-left-to-right", "invoke-selected-constructor", "yield-fresh-reference",
)
CLASS_CREATION_REQUIREMENTS = (
    "shared-managed-class-allocation-and-layout",
    "owned-bootstrap-Object-definition-and-super-construction",
    "class-initialization-before-allocation",
    "default-instance-initialization-before-arguments",
    "left-to-right-arguments-and-abrupt-completion",
    "constructor-invocation-and-abrupt-completion",
    "allocation-failure-before-argument-evaluation",
    "shared-owning-root-and-safepoint-ABI",
    "fresh-reference-identity-and-construction-publication",
)


@dataclass(frozen=True)
class ClassConstructionObligation:
    owner: str
    type_name: str
    constructor_descriptor: str
    tokens: tuple
    requirements: tuple[str, ...] = CLASS_CREATION_REQUIREMENTS


class ClassConstructionAnalyzer:
    def class_creation_expression(self, node, assigned):
        if node.kind != "class_new":
            return False
        dtype = self.project.resolve_named_type(self.program, node.value)
        if isinstance(dtype, BootstrapStringType):
            node.token.error("bootstrap String construction requires its owned constructor/allocation definition",
                             code="java.unsupported.string-literals")
        # The parser admits a simple class type, not interfaces, arrays,
        # abstract/generic/nested classes or an invented bootstrap definition.
        assert isinstance(dtype, ClassType)
        target = self.project.classes[dtype.class_name]
        for argument in node.children:
            self.expression(argument, assigned)
            if argument.dtype == "void":
                argument.token.error("void value cannot be a constructor argument")
        constructors = self.project.analyzers[target.name].constructors
        accessible = {key: method for key, method in constructors.items()
                      if target is self.program or "private" not in method.modifiers and
                      (target.package == self.program.package or "public" in method.modifiers)}
        call = Node("call", node.token, target.token.text, node.children)
        constructor = select_method(accessible, call)
        if constructor is None:
            if select_method(constructors, call) is not None:
                node.token.error("source constructor is not accessible: " + target.name.replace("/", "."))
            node.token.error("no applicable supplied-source constructor for " + target.name.replace("/", "."))
        node.dtype, node.symbol = dtype, constructor
        node.runtime_steps = CLASS_CREATION_STEPS
        self.require_callable_string_context(constructor, node.token)
        self.project.record_class_construction(self.program, node, constructor)
        # No value/object/constant/identity, address, header, vtable, GC root or
        # runtime registration is created. Arguments retain their original ASTs.
        return True
