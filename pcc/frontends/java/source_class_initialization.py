"""Static final fields initialized by class initialization (class model only).

JLS 4.12.4, 8.3.1.2, 8.3.2, 8.3.3, 12.4.1 and 12.4.2:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-12.html#jls-12.4.2
A static final field of primitive or String type whose initializer is a
constant expression is a constant variable: it is initialized before any
other step (12.4.2 step 6) and every simple or TypeName-qualified read
folds. Every other initialized static final field, including every
primitive-array field, is a class variable whose initializer runs in
textual order during step 9; reading it is an active use that requires the
class to be initialized first (12.4.1), and its value is never folded.

Static initializer blocks (JLS 8.7) are checked in a static context and
run as step-9 steps in textual order with the field initializers. A blank
static final of primitive or array type must be definitely assigned by them
(JLS 8.3.1.2, 16, 16.8): this model admits its assignments as top-level
`name = value;` statements of a static initializer, where definite
assignment and unassignment are exact without a flow graph. A simple-name
read in a static initializer or class variable initializer before that
assignment, a second assignment and a missing one are errors; an assignment
nested in a statement or expression is an explicit gap.

This is the checked nonlowering model only. The retained steps and slots
are obligations: no initialization state/lock, ExceptionInInitializerError
path, static root or trigger is emitted or registered. Static final
fields of source-class type, mutable static fields and nested blank-final
assignments remain explicit gaps, as do all of these in the native
static-only domain.
"""
from dataclasses import dataclass, field as dataclass_field

from .floating_semantics import converted_constant
from .source_types import ArrayType, NUMERIC


@dataclass(frozen=True)
class StaticFieldSlot:
    """A class variable initialized in step 9; no storage address is assigned."""
    field: object
    reference: bool  # a primitive-array reference, which a static root must own
    offset: None = None


@dataclass(frozen=True)
class ClassInitializationObligation:
    owner: str
    tokens: tuple
    requirements: tuple


CLASS_INITIALIZATION_REQUIREMENTS = (
    "class-initialization-state-lock-and-erroneous-state",
    "class-initialization-trigger-at-first-active-use",
    "exception-in-initializer-error",
)
STATIC_REFERENCE_REQUIREMENTS = ("static-reference-root",)


@dataclass
class BlankStaticFinalPlan:
    """Definite assignment of blank static finals at each static position."""
    blank: dict  # name -> SourceField
    assignments: dict = dataclass_field(default_factory=dict)  # id(top-level assign) -> field
    statements: dict = dataclass_field(default_factory=dict)  # id(top-level statement) -> DA set before it
    after_blocks: list = dataclass_field(default_factory=list)  # (block ordinal, DA set after the block)

    def before_field(self, ordinal):
        """DA set before a class variable initializer at this field ordinal."""
        assigned = frozenset()
        for position, after in self.after_blocks:
            if position <= ordinal:
                assigned = after
        return assigned


def _gap(token, message):
    token.error(message, code="java.unsupported.static-initialization")


def _static_type(field):
    if field.dtype not in NUMERIC | {"boolean"} and not isinstance(field.dtype, ArrayType):
        # Source-class references need managed static roots and the
        # construction domain; only a constant String variable folds.
        _gap(field.token, "nonconstant static initialization requires the shared Java initialization contract")


def _top_level_locals(statement):
    if statement.kind == "var":
        return {statement.token.text}
    if statement.kind == "sequence":
        return {child.token.text for child in statement.children if child.kind == "var"}
    return set()


def _nested_blank_writes(node, blank, exempt):
    """Assignments to a blank final name other than the admitted top-level
    ones and top-level ones to a shadowing local."""
    pending = [node]
    while pending:
        current = pending.pop()
        if current is None or not hasattr(current, "kind"):
            continue
        if current.kind == "assign" and current.token.text in blank and id(current) not in exempt:
            yield current
        if current.kind == "mutation" and current.children:
            target = current.children[0]
            while target.kind == "group" and target.children:
                target = target.children[0]
            if target.kind == "name" and target.value in blank:
                yield current
        pending.extend(current.children or ())


def blank_static_final_plan(program):
    """Exact DA/DU for blank static finals assigned by top-level statements."""
    blank = {field.token.text: field for field in program.fields
             if "static" in field.modifiers and field.initializer is None}
    plan = BlankStaticFinalPlan(blank)
    if not blank and not program.static_initializers:
        return plan
    candidates, local_writes = [], set()
    for block in program.static_initializers:
        shadowed = set()
        for statement in block.children:
            if statement.kind == "assign" and statement.token.text in blank:
                if statement.token.text in shadowed:
                    local_writes.add(id(statement))  # a top-level local of the same name
                else:
                    candidates.append(statement)
            shadowed |= _top_level_locals(statement)
    top_level = {id(statement) for statement in candidates}
    for block in program.static_initializers:
        for write in _nested_blank_writes(block, blank, top_level | local_writes):
            _gap(write.token, "a blank static final assigned outside a top-level `name = value;` statement of a "
                              "static initializer requires definite-unassignment flow analysis")
    for field in blank.values():
        _static_type(field)
    assigned = set()
    for block in program.static_initializers:
        for statement in block.children:
            plan.statements[id(statement)] = frozenset(assigned)
            if id(statement) in top_level:
                name = statement.token.text
                if statement.value != "=":
                    # A compound assignment or increment also reads the variable.
                    statement.token.error("blank static final field " + name + (
                        " may already have been assigned" if name in assigned
                        else " is not definitely assigned before this read"))
                if name in assigned:
                    # JLS 16: a blank final is assigned only when definitely unassigned.
                    statement.token.error("blank static final field " + name + " may already have been assigned")
                plan.assignments[id(statement)] = blank[name]
                assigned.add(name)
        plan.after_blocks.append((block.value, frozenset(assigned)))
    for name, field in blank.items():
        if name not in assigned:
            # JLS 8.3.1.2: definitely assigned at the end of the static initializers.
            field.token.error("blank static final field " + name + " is not definitely assigned by a static initializer")
    return plan


def is_class_variable_initialization(field):
    """Whether a checked static field is initialized by step 9."""
    return ("static" in field.modifiers and field.initializer is not None
            and field.constant is None and field.string_constant is None)


class ClassInitializationAnalyzer:
    """Mixin used only by ClassAnalyzer, never the native static analyzer."""

    current_static_initializer = None
    current_static_statement = None

    def blank_static_plan(self):
        plan = getattr(self, "_blank_static_plan", None)
        if plan is None:
            plan = self._blank_static_plan = blank_static_final_plan(self.program)
            for field in plan.blank.values():
                self.project.record_class_initialization(self.program, field)
            for block in self.program.static_initializers:
                self.project.record_class_initialization(self.program, block)
        return plan

    def analyze_static_initializers(self, method_type):
        """JLS 8.7 static initializers, each in a static context, in textual order."""
        plan = self.blank_static_plan()
        for initializer in self.program.static_initializers:
            self.method = method_type(initializer.token, "void", (), initializer, owner=self.program.name,
                                      modifiers=frozenset({"static"}))
            self.scopes, self.controls, self.constant_locals = [{}], [], {}
            self.current_static_initializer = initializer
            self.current_static_statement = None
            try:
                if self.statement(initializer, set()) is None:
                    initializer.token.error("static initializer must be able to complete normally")
                initializer.symbol = self.method
            finally:
                self.current_static_initializer = self.current_static_statement = None
                self.method = None
        return plan

    def enter_static_statement(self, node):
        if self.current_static_initializer is not None and id(node) in self.blank_static_plan().statements:
            self.current_static_statement = node

    def static_field_assignment(self, node, assigned):
        """A top-level `name = value;` assigning a blank static final, if this is one."""
        field = self.blank_static_plan().assignments.get(id(node))
        if field is None or self.current_static_initializer is None:
            return False
        for child in node.children:
            self.expression(child, assigned)
        self.assignment_value(node.children[0], field.dtype)
        node.kind, node.symbol = "static_field_assign", field
        return True

    def check_static_field_read(self, node, field):
        """JLS 8.3.3 and 16: a simple-name read of this class's static field."""
        if self.current_static_initializer is not None:
            if field.ordinal >= self.current_static_initializer.value:
                node.token.error("illegal forward reference in a static initializer")
            statement = self.current_static_statement
            assigned = (self.blank_static_plan().statements[id(statement)] if statement is not None
                        else frozenset())
        elif self.current_field is not None and "static" in self.current_field.modifiers:
            assigned = self.blank_static_plan().before_field(self.current_field.ordinal)
        else:
            return
        if field.initializer is None and field.token.text not in assigned:
            node.token.error("blank static final field " + field.token.text
                             + " is not definitely assigned before this read")

    def evaluate_static_field(self, field):
        name = field.token.text
        if field.initializer is None:
            # A blank static final: assigned by a static initializer, never a constant.
            self.blank_static_plan()
            return None
        state = self.field_states.get(name)
        if state == "complete":
            return field.constant
        if state == "evaluating":
            field.token.error("cyclic field initialization requires the shared Java initialization contract",
                              code="java.unsupported.cyclic-initialization")
        if field.dtype not in NUMERIC | {"boolean"} and not isinstance(field.dtype, ArrayType):
            # Source-class references need managed static roots and the
            # construction domain; only a constant String variable folds.
            field.token.error("nonconstant static initialization requires the shared Java initialization contract",
                              code="java.unsupported.static-initialization")
        self.field_states[name] = "evaluating"
        previous = self.current_field
        self.current_field = field
        try:
            self.expression(field.initializer, set())
            self.assignment_value(field.initializer, field.dtype)
            # JLS 4.12.4: only a primitive (or String) final variable whose
            # initializer is a constant expression is a constant variable.
            constant = None if isinstance(field.dtype, ArrayType) else field.initializer.constant
            field.constant = (None if constant is None else
                              converted_constant(constant, field.initializer.dtype, field.dtype))
            if field.constant is None:
                self.project.record_class_initialization(self.program, field)
            self.field_states[name] = "complete"
            return field.constant
        finally:
            self.current_field = previous


def _is_field(item):
    return hasattr(item, "ordinal")


def _textual_position(item):
    # A static block whose value is k precedes the field with ordinal k.
    return (item.ordinal, 1) if _is_field(item) else (item.value, 0)


def class_initialization_plan(program, recorded):
    """Step-9 steps (initialized fields and static blocks) in textual order,
    the static slots they initialize (including blank finals) and the shared
    obligation."""
    steps = tuple(sorted((item for item in recorded if not _is_field(item) or item.initializer is not None),
                         key=_textual_position))
    if not steps:
        return (), (), ()
    fields = sorted((item for item in recorded if _is_field(item)), key=lambda field: field.ordinal)
    slots = tuple(StaticFieldSlot(field, isinstance(field.dtype, ArrayType)) for field in fields)
    requirements = CLASS_INITIALIZATION_REQUIREMENTS + (
        STATIC_REFERENCE_REQUIREMENTS if any(slot.reference for slot in slots) else ())
    obligation = ClassInitializationObligation(program.name, tuple(item.token for item in steps), requirements)
    return steps, slots, (obligation,)
