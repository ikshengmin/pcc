"""Checked Java class model, not a managed-object execution implementation.

The same source syntax and scalar/structured-flow sema are reused. Receiver
fields and constructors remain typed nodes, never fabricated stack objects or
raw-malloc payloads. Layout describes primitive payload fields only; runtime
identity/GC roots, construction/publication and dispatch remain shared-runtime
prerequisites. No descriptor below is registered with a runtime.
"""
from collections.abc import Mapping
from dataclasses import dataclass

from .diagnostics import JavaError, UnsupportedJava
from .source_project import SourceProject
from .source_sema import SourceAnalyzer
from .source_syntax import Node, SourceMethod, SourceParser
from .source_types import INTEGRAL, promoted, select_method


@dataclass(frozen=True)
class ReceiverType:
    class_name: str


@dataclass(frozen=True)
class PrimitiveFieldSlot:
    field: object
    offset: int
    size: int
    alignment: int


@dataclass(frozen=True)
class SourceClassModel:
    source: object
    constructors: tuple
    fields: tuple[PrimitiveFieldSlot, ...]
    payload_size: int
    payload_alignment: int
    initialization_steps: tuple = ()
    superclass: str = "java/lang/Object"
    runtime_registered: bool = False


class ClassAnalyzer(SourceAnalyzer):
    def __init__(self, program, project):
        super().__init__(program, project)
        self.constructor_edge = {}
        self.before_super = False
        self.active_constructor_call = None
        self.constant_locals = {}
        self.current_initializer = None

    def declare_methods(self):
        if self.program.name == "java/lang/Object":
            raise UnsupportedJava("bootstrap Object requires the shared runtime definition", code="java.unsupported.bootstrap-object")
        for method in self.program.methods:
            if "static" in method.modifiers:
                continue
            if (not method.parameters and method.token.text in {
                    "hashCode", "getClass", "toString", "clone", "finalize", "notify", "notifyAll", "wait"}
                    or method.token.text == "wait" and method.parameter_types in (("long",), ("long", "int"))):
                method.token.error("Object override/final/access semantics require the inherited-method domain", code="java.unsupported.object-inherited-methods")
        # Instance overrides of Object were explicitly gated above. Reuse the
        # source owner's ordinary overload and static/inherited conflict checks.
        super().declare_methods()
        constructors = self.program.constructors
        if not constructors:
            token = self.program.token
            constructors = (SourceMethod(token, "void", (), Node("block", token), owner=self.program.name,
                                         modifiers=frozenset({"public"} if "public" in self.program.modifiers else ()),
                                         is_constructor=True, implicit=True),)
        self.constructors = {}
        for constructor in constructors:
            key = (constructor.token.text, constructor.parameter_types)
            if key in self.constructors:
                constructor.token.error("duplicate constructor signature")
            self.constructors[key] = constructor

    def require_receiver(self, token):
        static = ("static" in self.current_field.modifiers if self.current_field is not None else
                  self.method is None or "static" in self.method.modifiers)
        if static:
            token.error("instance receiver is not available in a static context")
        if self.before_super:
            token.error("instance receiver cannot be used before constructor invocation")

    def evaluate_field(self, field):
        if "static" in field.modifiers:
            return super().evaluate_field(field)
        if self.field_states.get(field.token.text) == "complete":
            return None
        previous = self.current_field
        self.current_field = field
        try:
            if field.initializer is not None:
                self.expression(field.initializer, set())
                self.assignment_value(field.initializer, field.dtype)
            self.field_states[field.token.text] = "complete"
        finally:
            self.current_field = previous
        # A mutable instance field is never a constant variable.
        return None

    def field_read(self, node, field, *, explicit_receiver=False):
        if (self.current_field is not None and not explicit_receiver and "." not in node.value
                and field.owner == self.program.name and field.ordinal >= self.current_field.ordinal
                and ("static" in field.modifiers) == ("static" in self.current_field.modifiers)):
            node.token.error("illegal forward or self reference in a field initializer")
        if (self.current_initializer is not None and not explicit_receiver and "." not in node.value
                and "static" not in field.modifiers and field.owner == self.program.name
                and field.ordinal >= self.current_initializer.value):
            node.token.error("illegal forward reference in an instance initializer")
        if "static" not in field.modifiers:
            self.require_receiver(node.token)
            node.kind, node.constant = "instance_field", None
        else:
            constant = self.project.analyzers[field.owner].evaluate_field(field)
            # JLS 15.29 permits simple and TypeName-qualified constant names,
            # not a Primary field access such as this.K. Retain that distinct
            # checked shape: its receiver must still be evaluated eventually.
            node.kind = "receiver_static_field" if explicit_receiver else "static_field"
            node.constant = None if explicit_receiver else constant
        node.symbol, node.dtype = field, field.dtype

    def expression(self, node, assigned):
        if node.kind == "this":
            self.require_receiver(node.token)
            node.dtype = ReceiverType(self.program.name)
            node.symbol = self.program
            return
        if node.kind == "receiver_field":
            self.require_receiver(node.token)
            field = self.fields.get(node.value)
            if field is None:
                node.token.error("undefined instance field " + node.value)
            self.field_read(node, field, explicit_receiver=True)
            return
        if node.kind == "name" and not any(node.value in scope for scope in self.scopes):
            field = self.project.resolve_field(self, node)
            if "." in node.value and "static" not in field.modifiers:
                node.token.error("a class qualifier cannot select an instance field")
            self.field_read(node, field)
            return
        if node.kind == "receiver_call":
            self.require_receiver(node.token)
            for argument in node.children:
                self.expression(argument, assigned)
                if argument.dtype == "void":
                    argument.token.error("void value cannot be a method argument")
            method = select_method(self.methods, node)
            if method is None:
                node.token.error("unresolved receiver method " + node.value)
            node.symbol, node.dtype = method, method.result
            return
        super().expression(node, assigned)
        if node.kind == "call" and "static" not in node.symbol.modifiers:
            if "." in node.value:
                node.token.error("a class qualifier cannot select an instance method")
            self.require_receiver(node.token)
            node.kind = "receiver_call"

    def statement(self, node, assigned):
        if self.current_initializer is not None and node.kind == "return":
            node.token.error("return is not permitted in an instance initializer")
        if node.kind == "constructor_call":
            if node is not self.active_constructor_call:
                node.token.error("constructor invocation must be first in a constructor body")
            self.before_super = True
            try:
                for argument in node.children:
                    self.expression(argument, assigned)
            finally:
                self.before_super = False
            if node.value == "super":
                if node.children:
                    node.token.error("implicit Object superclass has only a no-argument constructor")
                node.symbol = "java/lang/Object::<init>()V"
            else:
                call = Node("call", node.token, self.program.token.text, node.children)
                constructor = select_method(self.constructors, call)
                if constructor is None:
                    node.token.error("no applicable same-class constructor")
                node.symbol = constructor
                self.constructor_edge[id(self.method)] = constructor
            return set(assigned)
        implicit_field = (node.kind == "assign" and not any(node.token.text in scope for scope in self.scopes)
                          and node.token.text in self.fields)
        if node.kind == "field_assign" or implicit_field:
            self.require_receiver(node.token)
            field = self.fields.get(node.token.text)
            if field is None:
                node.token.error("undefined instance field " + node.token.text)
            if "static" in field.modifiers or "final" in field.modifiers:
                node.token.error("cannot assign a final constant field " + node.token.text)
            # The forward-write exception is for simple assignment. A compound
            # assignment also reads the old value (JLS 15.26.2), just like ++,
            # and must obey the simple-name forward-reference restriction.
            if (implicit_field and self.current_initializer is not None and node.value != "="
                    and field.ordinal >= self.current_initializer.value):
                node.token.error("illegal forward reference in an instance initializer")
            node.kind, node.symbol = "field_assign", field
            for child in node.children:
                self.expression(child, assigned)
            if node.value == "=":
                self.assignment_value(node.children[0], field.dtype)
            elif node.value in ("&=", "|=", "^=") and field.dtype == node.children[0].dtype == "boolean":
                node.operation_type = "boolean"
            else:
                if field.dtype not in INTEGRAL:
                    node.token.error("instance compound arithmetic requires an integer field")
                if node.value in ("++", "--"):
                    node.operation_type = promoted(field.dtype)
                else:
                    self.require_int(node.children[0])
                    node.operation_type = promoted(field.dtype, node.children[0].dtype,
                                                   shift=node.value in ("<<=", ">>=", ">>>="))
                if node.value in ("/=", "%="):
                    self.require_nonzero_constant_divisor(node.children[0])
            return set(assigned)
        return super().statement(node, assigned)

    def analyze(self):
        for initializer in self.program.instance_initializers:
            self.method = SourceMethod(initializer.token, "void", (), initializer, owner=self.program.name)
            self.scopes, self.controls, self.constant_locals = [{}], [], {}
            self.current_initializer = initializer
            try:
                if self.statement(initializer, set()) is None:
                    initializer.token.error("instance initializer must be able to complete normally")
                # Preserve the local bindings as part of the checked plan.
                initializer.symbol = self.method
            finally:
                self.current_initializer = None
                self.method = None
        super().analyze()
        for constructor in self.constructors.values():
            self.method = constructor
            self.scopes, self.controls, self.constant_locals = [{}], [], {}
            assigned = {self.declare(parameter, dtype, final)
                        for parameter, dtype, final in zip(constructor.parameters, constructor.parameter_types,
                                                           constructor.parameter_final)}
            children = constructor.body.children
            self.active_constructor_call = children[0] if children and children[0].kind == "constructor_call" else None
            self.statement(constructor.body, assigned)
            constructor.constructor_target = (self.active_constructor_call.symbol if self.active_constructor_call is not None
                                              else "java/lang/Object::<init>()V")
            constructor.initialization_steps = (() if isinstance(constructor.constructor_target, SourceMethod)
                                                else self.program.initialization_steps)
            self.active_constructor_call = None
        for constructor in self.constructors.values():
            visited, current = set(), constructor
            while id(current) in self.constructor_edge:
                if id(current) in visited:
                    constructor.token.error("recursive constructor invocation")
                visited.add(id(current))
                current = self.constructor_edge[id(current)]
        return self.program


class ClassModelProject(SourceProject):
    def __init__(self, programs):
        super().__init__(programs, analyzer_type=ClassAnalyzer)
        self.models = {}

    def analyze(self):
        super().analyze()
        sizes = {"boolean": 1, "byte": 1, "short": 2, "char": 2, "int": 4, "long": 8}
        for program in self.programs:
            slots, offset, alignment = [], 0, 1
            for field in program.fields:
                if "static" in field.modifiers:
                    continue
                size = sizes[field.dtype]
                alignment = max(alignment, size)
                offset = (offset + size - 1) // size * size
                slots.append(PrimitiveFieldSlot(field, offset, size, size))
                offset += size
            payload_size = (offset + alignment - 1) // alignment * alignment
            self.models[program.name] = SourceClassModel(program,
                tuple(self.analyzers[program.name].constructors.values()), tuple(slots), payload_size, alignment,
                program.initialization_steps)
        return self


def analyze_class_sources(sources):
    """Analyze supplied classes without claiming object allocation or emission.

    Keys are diagnostic names, not files to open. Every explicit constructor,
    field initializer and instance/static body is checked. The resulting payload
    plan contains no Python header, tracing descriptor, object address or vtable.
    Native compile_source(s) keeps its existing explicit instance-feature gate.
    """
    if not isinstance(sources, Mapping) or not sources or len(sources) > 256:
        raise JavaError("class source model requires between 1 and 256 supplied units")
    if any(not isinstance(name, str) or not isinstance(text, str) for name, text in sources.items()):
        raise JavaError("class source model names and contents must be text")
    if sum(len(text) for text in sources.values()) > 8_388_608:
        raise UnsupportedJava("class source model exceeds the 8 MiB aggregate input limit", code="java.unsupported.project-size-limit")
    try:
        programs = [SourceParser(sources[name], name, class_model=True).parse() for name in sorted(sources)]
        return ClassModelProject(programs).analyze()
    except RecursionError as error:
        raise UnsupportedJava("class source model exceeds the bounded parser/analysis nesting limit", code="java.unsupported.nesting-limit") from error
