"""Checked Java class model, not a managed-object execution implementation.

The same source syntax and scalar/structured-flow sema are reused. Receiver
fields and constructors remain typed nodes, never fabricated stack objects or
raw-malloc payloads. Layout describes primitive payload fields only; runtime
identity/GC roots, construction/publication and dispatch remain shared-runtime
prerequisites. Classes containing array or nominal references have no payload
layout yet. Named references resolve to supplied source classes. String source
facts use an explicitly unresolved bootstrap dependency; no String or Object
definition, inheritance graph, interned object or reference ABI is synthesized.
No descriptor below is registered with a runtime.
"""
from collections.abc import Mapping
from dataclasses import dataclass

from pcc.support.managed_objects import ManagedValuePlan, unmet_requirements

from .diagnostics import JavaError, UnsupportedJava
from .managed_objects import PRIMITIVE_SIZES, class_value_plan, reference_value_plan
from .source_array_model import ArrayModelAnalyzer
from .source_construction_model import ClassConstructionAnalyzer, ClassConstructionObligation
from .source_project import SourceProject
from .source_sema import SourceAnalyzer
from .source_string_model import (
    MAX_STRING_FOLD_WORK, MAX_STRING_UNITS, StringContextObligation, StringModelAnalyzer,
)
from .source_syntax import Node, SourceMethod, SourceParser
from .source_types import ArrayType, BootstrapStringType, ClassType, INTEGRAL, NUMERIC, NamedType, promoted, select_method, widening


# Existing receiver API retains the same canonical source identity as named
# declarations. Neither spelling supplies a reference representation.
ReceiverType = ClassType


@dataclass(frozen=True)
class PrimitiveFieldSlot:
    field: object
    offset: int | None
    size: int
    alignment: int


@dataclass(frozen=True)
class ArrayReferenceFieldSlot:
    """Source reference, not a guessed pointer-sized managed-storage slot."""
    field: object
    offset: None = None
    size: None = None
    alignment: None = None
    default_value: None = None  # JLS 4.12.5: null before instance initialization.


@dataclass(frozen=True)
class ClassReferenceFieldSlot:
    """Nominal reference source slot; all managed layout remains unknown."""
    field: object
    offset: None = None
    size: None = None
    alignment: None = None
    default_value: None = None


@dataclass(frozen=True)
class SourceClassModel:
    source: object
    constructors: tuple
    fields: tuple[PrimitiveFieldSlot | ArrayReferenceFieldSlot | ClassReferenceFieldSlot, ...]
    payload_size: int | None
    payload_alignment: int | None
    initialization_steps: tuple = ()
    superclass: str = "java/lang/Object"
    runtime_registered: bool = False
    compiler_context_obligations: tuple = ()
    semantic_context_complete: bool = False
    managed_value: ManagedValuePlan | None = None

    @property
    def managed_requirements(self):
        """A payload plan never discharges the shared runtime obligations."""
        return unmet_requirements(self.managed_value)


class ClassAnalyzer(ClassConstructionAnalyzer, StringModelAnalyzer, ArrayModelAnalyzer, SourceAnalyzer):
    def __init__(self, program, project):
        super().__init__(program, project)
        self.constructor_edge = {}
        self.before_super = False
        self.active_constructor_call = None
        self.constant_locals = {}
        self.current_initializer = None
        self.string_constant_locals = {}
        self._class_members_declared = False

    def assignment_value(self, node, target):
        if isinstance(target, (ClassType, BootstrapStringType)):
            if not widening(node.dtype, target):
                node.token.error("incompatible source reference assignment from " + str(node.dtype) + " to " + str(target))
            return
        return super().assignment_value(node, target)

    def declare_methods(self):
        # SourceAnalyzer may request an empty ordinary-method table again.
        # Constructor bindings must retain the one actual declaration object.
        if self._class_members_declared:
            return
        if self.program.name == "java/lang/String":
            raise UnsupportedJava("bootstrap String requires the shared owned String definition and literal/interning contract",
                                  code="java.unsupported.string-literals")
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
        self._class_members_declared = True

    def require_receiver(self, token):
        static = ("static" in self.current_field.modifiers if self.current_field is not None else
                  self.method is None or "static" in self.method.modifiers)
        if static:
            token.error("instance receiver is not available in a static context")
        if self.before_super:
            token.error("instance receiver cannot be used before constructor invocation")

    def evaluate_field(self, field):
        if "static" in field.modifiers and isinstance(field.dtype, BootstrapStringType):
            return self.evaluate_string_field(field)
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
        # Neither a mutable field nor a final array reference is a constant variable.
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
            node.string_constant = None if explicit_receiver else field.string_constant
        node.symbol, node.dtype = field, field.dtype
        if isinstance(field.dtype, BootstrapStringType):
            self.project.require_string_context(self.program, node.token,
                                                materialization=field.string_constant is not None)

    def expression(self, node, assigned):
        # Attach storage obligations only after the original semantic owner
        # succeeds, including its early-return array/receiver/creation paths.
        self.class_expression(node, assigned)
        node.managed_value = reference_value_plan(
            node.dtype, materialization=node.string_constant is not None)

    def class_expression(self, node, assigned):
        if self.class_creation_expression(node, assigned):
            return
        if node.kind in {"name", "call"} and "." in str(node.value):
            root = node.value.split(".", 1)[0]
            binding = next((scope[root] for scope in reversed(self.scopes) if root in scope), None)
            if binding is None:
                binding = self.fields.get(root)
            if binding is not None and isinstance(binding.dtype, (ClassType, BootstrapStringType)):
                node.token.error("source reference member access requires the next managed receiver domain",
                                 code="java.unsupported.object-expressions")
        if self.array_expression(node, assigned):
            return
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
            self.require_callable_string_context(method, node.token)
            return
        super().expression(node, assigned)
        if node.kind == "call":
            self.require_callable_string_context(node.symbol, node.token)
        if node.kind == "call" and "static" not in node.symbol.modifiers:
            if "." in node.value:
                node.token.error("a class qualifier cannot select an instance method")
            self.require_receiver(node.token)
            node.kind = "receiver_call"

    def statement(self, node, assigned):
        if node.kind == "var" and isinstance(node.value, NamedType):
            node.value = self.project.resolve_named_type(self.program, node.value)
        if node.kind == "array_assign":
            return self.array_assignment(node, assigned)
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
                self.require_callable_string_context(constructor, node.token)
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
            if isinstance(field.dtype, BootstrapStringType) and node.value == "+=":
                if any(child.dtype == "void" for child in node.children):
                    node.token.error("void value cannot participate in String concatenation")
                node.token.error("String compound concatenation requires owned conversion/allocation semantics",
                                 code="java.unsupported.string-literals")
            if node.value == "=":
                self.assignment_value(node.children[0], field.dtype)
            elif node.value in ("&=", "|=", "^=") and field.dtype == node.children[0].dtype == "boolean":
                node.operation_type = "boolean"
            else:
                integral_only = node.value in {"&=", "|=", "^=", "<<=", ">>=", ">>>="}
                if field.dtype not in (INTEGRAL if integral_only else NUMERIC):
                    node.token.error("instance compound arithmetic requires an integer field or an admissible numeric field")
                if node.value in ("++", "--"):
                    node.operation_type = promoted(field.dtype)
                else:
                    (self.require_int if integral_only else self.require_numeric)(node.children[0])
                    node.operation_type = promoted(field.dtype, node.children[0].dtype,
                                                   shift=node.value in ("<<=", ">>=", ">>>="))
                if node.value in ("/=", "%="):
                    self.arithmetic_domain(node, node.value[:-1], node.operation_type, node.children[0])
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
    semantic_context_complete = False

    def __init__(self, programs):
        self._bootstrap_string_type = BootstrapStringType()
        self._string_contexts = {}
        self._string_fold_work = 0
        self._class_constructions = {}
        super().__init__(programs, analyzer_type=ClassAnalyzer)
        self.models = {}

    @property
    def compiler_context_obligations(self):
        strings = tuple(StringContextObligation(owner, tuple(tokens), tuple(sorted(requirements)))
                        for owner, (tokens, requirements) in sorted(self._string_contexts.items()))
        constructions = tuple(obligation for _, obligation in sorted(self._class_constructions.items()))
        return strings + constructions

    @property
    def class_creation_context_complete(self):
        """No construction obligations, never evidence of allocation acceptance."""
        return not self._class_constructions

    def record_class_construction(self, caller, node, constructor):
        token = node.token
        key = (caller.name, token.filename, token.line, token.column)
        self._class_constructions[key] = ClassConstructionObligation(
            caller.name, node.dtype.class_name, constructor.descriptor, (token,))

    @property
    def string_context_complete(self):
        """Absence of String-specific obligations, never compiler acceptance."""
        return not self._string_contexts

    def require_string_context(self, caller, token, *, materialization=False):
        tokens, requirements = self._string_contexts.setdefault(caller.name, ({}, set()))
        tokens[token] = None
        requirements.update(("owned-bootstrap-type-definition", "shared-owning-root-and-safepoint-ABI"))
        if materialization:
            requirements.update(("managed-literal-materialization", "interned-reference-identity",
                                 "shared-owning-root-and-safepoint-ABI"))
        return self._bootstrap_string_type

    def charge_string_work(self, count, token):
        if count > MAX_STRING_UNITS or self._string_fold_work + count > MAX_STRING_FOLD_WORK:
            token.error("String source constant exceeds the bounded UTF-16 value/folding budget",
                        code="java.unsupported.string-literals")
        self._string_fold_work += count

    def external_type_dependency(self, caller, tokens):
        if tuple(token.text for token in tokens) == ("java", "lang", "String"):
            return self.require_string_context(caller, tokens[0])
        return super().external_type_dependency(caller, tokens)

    def resolve_named_type(self, caller, dtype):
        if not isinstance(dtype, NamedType):
            return dtype
        token = dtype.tokens[0]
        if len(dtype.tokens) != 1:
            # As with qualified call owners, missing bootstrap public-type
            # metadata cannot prove that a leading spelling names a package
            # rather than a type with a member type.
            token.error("qualified reference declarations require complete implicit-type/member-type metadata; use a resolved single-type import",
                        code="java.unsupported.implicit-type-metadata")
        target = self.visible_simple_type(caller, token.text)
        if isinstance(target, BootstrapStringType):
            return self.require_string_context(caller, token)
        if target is None:
            if token.text == "String":
                return self.require_string_context(caller, token)
            token.error("source reference declaration requires supplied class source for " + str(dtype),
                        code="java.unsupported.source-dependency")
        self.require_class_access(caller, target, token)
        return ClassType(target.name)

    def resolve_qualified_owner(self, analyzer, node):
        first = node.value.split(".", 1)[0]
        if (not any(first in scope for scope in analyzer.scopes) and first not in analyzer.fields
                and first == "String"):
            binding = self.visible_simple_type(analyzer.program, first)
            if binding is None or isinstance(binding, BootstrapStringType):
                self.require_string_context(analyzer.program, node.token)
                node.token.error("String members require supplied owned bootstrap method/field definitions",
                                 code="java.unsupported.source-dependency")
        return super().resolve_qualified_owner(analyzer, node)

    def resolve_declaration_types(self, program):
        for field in program.fields:
            field.dtype = self.resolve_named_type(program, field.dtype)
        for method in (*program.methods, *program.constructors):
            method.result = self.resolve_named_type(program, method.result)
            method.parameter_types = tuple(self.resolve_named_type(program, dtype)
                                           for dtype in method.parameter_types)

    def analyze(self):
        super().analyze()
        for program in self.programs:
            slots, offset, alignment = [], 0, 1
            reference_layout = any(isinstance(field.dtype, (ArrayType, ClassType, BootstrapStringType)) and "static" not in field.modifiers
                                   for field in program.fields)
            for field in program.fields:
                if "static" in field.modifiers:
                    continue
                if isinstance(field.dtype, ArrayType):
                    slots.append(ArrayReferenceFieldSlot(field))
                    continue
                if isinstance(field.dtype, (ClassType, BootstrapStringType)):
                    slots.append(ClassReferenceFieldSlot(field))
                    continue
                size = PRIMITIVE_SIZES[field.dtype]
                alignment = max(alignment, size)
                offset = (offset + size - 1) // size * size
                slots.append(PrimitiveFieldSlot(field, None if reference_layout else offset, size, size))
                offset += size
            payload_size = (offset + alignment - 1) // alignment * alignment
            if reference_layout:
                payload_size, alignment = None, None
            obligations = tuple(item for item in self.compiler_context_obligations
                                if item.owner == program.name)
            managed_value = class_value_plan(program, slots, payload_size, alignment,
                requirements=tuple(requirement for item in obligations for requirement in item.requirements))
            self.models[program.name] = SourceClassModel(program,
                tuple(self.analyzers[program.name].constructors.values()), tuple(slots), payload_size, alignment,
                program.initialization_steps,
                compiler_context_obligations=obligations, managed_value=managed_value)
        return self


def analyze_class_sources(sources):
    """Analyze supplied classes without claiming object allocation or emission.

    Keys are diagnostic names, not files to open. Every explicit constructor,
    field initializer and instance/static body is checked. The resulting payload
    plan contains no Python header, tracing descriptor, object address or vtable.
    Native compile_source(s) keeps its existing explicit instance-feature gate.
    Ordinary named field/result/parameter/local types resolve through supplied
    source class identity, never host JDK metadata or a managed pointer.
    String source constants additionally retain immutable missing-bootstrap and
    materialization/root obligations, never complete dependency acceptance.
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
