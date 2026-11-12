"""First-declaration defaulted D1s with deletion and evaluated-use ownership.

N4659 [dcl.fct.def.default], [class.dtor]/5-9. A defaulted declaration is
not a user-provided empty body. Keep its deletion/triviality separate from
the generated member cleanup, and never emit unused member references.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.inline_methods import DeferredMethod
from pcc.frontends.cpp.types import unqualified


class DefaultedDestructorDefinition(DeferredMethod):
    """A completed declaration whose body is needed only on evaluated use."""


def defer_definition(definition, symbol, scope, declaration, this):
    if symbol.internal_linkage:
        raise CppDiagnostic('cpp.unsupported.class-linkage',
                            'defaulted destructors require distinct internal-linkage definition ownership', symbol.coord)
    symbol.defined = True
    symbol.destructor_defaulted = True
    symbol.destructor_user_provided = False
    symbol.destructor_deleted = False
    symbol.destructor_trivial = None
    symbol.definition_linkage = 'external_odr'
    symbol.defaulted_destructor_definition = DefaultedDestructorDefinition(
        symbol, 'void', scope, [], declaration, this, (), symbol.coord,
    )


def bind_definition(definition):
    symbol = definition.destructor
    if symbol is None or not getattr(symbol, 'destructor_defaulted', False):
        return
    from pcc.frontends.cpp.member_initialization import requires_destruction
    base = getattr(definition, 'base', None)
    symbol.destructor_trivial = not (any(requires_destruction(member.dtype) for member in definition.members)
                                     or base is not None and requires_destruction(base.dtype))
    symbol.destructor_deleted = _member_deletion(definition)


def _member_deletion(definition):
    """Deletion of implicit member D1s can be checked without defining them."""
    deleted = False
    base = getattr(definition, 'base', None)
    if base is not None:
        # [class.dtor]/5: an inaccessible or deleted base destructor deletes it.
        child = base.dtype.definition.destructor
        if child is None:
            deleted = _member_deletion(base.dtype.definition)
        else:
            deleted = getattr(child, 'destructor_deleted', False)
            try:
                definition.scope.check_named_member_access(child, base.coord)
            except CppDiagnostic as error:
                if error.code != 'cpp.member-access':
                    raise
                deleted = True
    for member in definition.members:
        if not is_class_type(member.dtype):
            continue
        child_definition = unqualified(member.dtype).definition
        child = child_definition.destructor
        if child is None:
            deleted = _member_deletion(child_definition) or deleted
            continue
        deleted = getattr(child, 'destructor_deleted', False) or deleted
        try:
            definition.scope.check_member_access(child, member.coord)
        except CppDiagnostic as error:
            if error.code != 'cpp.member-access':
                raise
            # A defaulted destructor is deleted, not ill-formed merely by
            # being declared. Diagnose only an actual potentially-invoked use.
            deleted = True
    return deleted


def require_usable(symbol, coord):
    if getattr(symbol, 'destructor_deleted', False):
        raise CppDiagnostic('cpp.invalid-destructor',
                            'selected defaulted destructor is deleted', coord)


def require_implicit_usable(definition, coord):
    if _member_deletion(definition):
        raise CppDiagnostic('cpp.invalid-destructor',
                            'potentially invoked implicit member destructor is deleted', coord)


def request_definition(parser, symbol, coord):
    pending = getattr(symbol, 'defaulted_destructor_definition', None)
    if pending is None or parser.unevaluated_depth:
        return
    require_usable(symbol, coord)
    if pending.function is None:
        from pcc.frontends.cpp.destructors import complete_body
        body = complete_body(
            parser, symbol.class_owner.definition, symbol, pending.parameter_scope,
            pending.this, pending.coord, lambda: c_ast.Compound([], pending.coord),
        )
        pending.function = c_ast.FuncDef(pending.declaration, None, body, pending.coord)
    # Use the existing session worklist; no alternate definition queue or
    # eager external relocation is introduced by an unused declaration.
    parser.instance_registry.request((symbol, ()), symbol, pending)


def emit_definition(parser, pending):
    from pcc.frontends.cpp.inline_constructors import project_definition
    return project_definition(parser, pending)
