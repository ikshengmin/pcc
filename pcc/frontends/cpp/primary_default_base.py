"""Qualify an actual primary dynamic base default constructor before using C1."""
from dataclasses import dataclass
from pcc.frontends.cpp.diagnostics import CppDiagnostic


@dataclass(frozen=True)
class BaseDefaultPlan:
    status: str
    selected: object = None
    reason: str = ''


def classify(parser, definition):
    from pcc.frontends.cpp.classes import is_class_type
    from pcc.frontends.cpp.types import array_type
    from pcc.frontends.cpp.sema import resolve_overload
    from pcc.frontends.cpp.inheritance import require_member_access
    from pcc.frontends.cpp.default_constructors import DefaultedConstructorDefinition
    from pcc.frontends.cpp.constructors import closed_construction_body

    base = definition.base.dtype.definition

    def unsupported(reason):
        return BaseDefaultPlan('unsupported', reason=reason)

    def deleted(reason):
        return BaseDefaultPlan('implicitly-deleted', reason=reason)

    if (not definition.polymorphic or not base.polymorphic or base.base is not None
            or getattr(base.dtype, 'template_origin', None) is not None):
        return unsupported('requires one primary dynamic base with no further base')
    if any(is_class_type(m.dtype) or array_type(m.dtype) for m in base.members):
        return unsupported('base class/array subobjects require recursive qualification')
    try:
        selected = resolve_overload(base.constructors, [], parser.types, definition.dtype.coord)
    except CppDiagnostic as error:
        if error.code in ('cpp.no-viable-overload', 'cpp.ambiguous-overload', 'cpp.use-of-deleted-function'):
            return deleted(error.code)
        raise
    try:
        require_member_access(definition.scope, selected, base.dtype, definition.dtype, definition.dtype.coord)
        destructor = base.destructor
        if destructor is not None:
            require_member_access(definition.scope, destructor, base.dtype, definition.dtype, definition.dtype.coord)
            from pcc.frontends.cpp.default_destructors import require_usable
            require_usable(destructor, definition.dtype.coord)
        else:
            from pcc.frontends.cpp.default_destructors import require_implicit_usable
            require_implicit_usable(base, definition.dtype.coord)
    except CppDiagnostic as error:
        if error.code in ('cpp.member-access', 'cpp.invalid-destructor'):
            return deleted(error.code)
        raise
    if getattr(selected, 'constructor_deleted', False):
        return deleted('selected base constructor is deleted')
    context = getattr(selected, 'defaulted_definition', None)
    if (getattr(selected, 'constructor_user_provided', True)
            or not getattr(selected, 'constructor_defaulted_on_first_declaration', False)
            or selected.parameters or not isinstance(context, DefaultedConstructorDefinition)
            or context.symbol is not selected or context.function is None):
        return unsupported('selected base requires an actually synthesized default C1 body')
    if not closed_construction_body(context.function.body, {}):
        return unsupported('synthesized base has unqualified initializer effects')
    # Without virtual/further bases or class subobjects, complete C1 has the
    # same actions as base-subobject initialization in this primary layout.
    return BaseDefaultPlan('selected-qualified', selected)
