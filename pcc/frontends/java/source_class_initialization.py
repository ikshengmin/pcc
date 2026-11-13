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

This is the checked nonlowering model only. The retained steps and slots
are obligations: no initialization state/lock, ExceptionInInitializerError
path, static root or trigger is emitted or registered. Static final
fields of source-class type, mutable static fields, blank static finals and
static initializer blocks remain explicit gaps, as do these fields in the
native static-only domain.
"""
from dataclasses import dataclass

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


def is_class_variable_initialization(field):
    """Whether a checked static field is initialized by step 9."""
    return ("static" in field.modifiers and field.initializer is not None
            and field.constant is None and field.string_constant is None)


class ClassInitializationAnalyzer:
    """Mixin used only by ClassAnalyzer, never the native static analyzer."""

    def evaluate_static_field(self, field):
        name = field.token.text
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


def class_initialization_plan(program, recorded):
    """Step-9 fields in textual order, their slots and the shared obligation."""
    fields = tuple(sorted(recorded, key=lambda field: field.ordinal))
    if not fields:
        return (), (), ()
    slots = tuple(StaticFieldSlot(field, isinstance(field.dtype, ArrayType)) for field in fields)
    requirements = CLASS_INITIALIZATION_REQUIREMENTS + (
        STATIC_REFERENCE_REQUIREMENTS if any(slot.reference for slot in slots) else ())
    obligation = ClassInitializationObligation(program.name, tuple(field.token for field in fields), requirements)
    return fields, slots, (obligation,)
