"""First-declaration defaulted default C1s, not user-written empty bodies.

N4659 [dcl.fct.def.default], [class.ctor], [dcl.init], [dcl.init.aggr].
This owner retains defaulting, deletion and triviality independently of the
generated initialization body. Class subobjects, later defaulting and
copy/move synthesis retain their separate special-member boundaries.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.inline_constructors import DeferredConstructor
from pcc.frontends.cpp.types import ConstType, PointerType


class DefaultedConstructorDefinition(DeferredConstructor):
    """A checked body plan, published only when selected in evaluated code."""


def defer_definition(definition, symbol, parameter_scope, declaration, this):
    if symbol.internal_linkage:
        raise CppDiagnostic('cpp.unsupported.class-linkage',
                            'defaulted constructors require distinct internal-linkage definition ownership', symbol.coord)
    symbol.defined = True
    symbol.constructor_deleted = False
    symbol.constructor_trivial = None  # Determined only after class completion.
    symbol.definition_linkage = 'external_odr'
    pending = DefaultedConstructorDefinition(
        symbol, parameter_scope, declaration, this, (), symbol.coord,
    )
    symbol.defaulted_definition = pending
    definition.defaulted_constructors.append(pending)


def declare_implicit_default_constructor(parser, definition):
    """[class.ctor]/4-7: a class without a user-declared constructor has an
    implicitly-declared default constructor: public, defaulted on its first
    declaration, hence not user-provided (value-initialization zeroes the
    object first). It is defined like `T() = default;`: the base, the class's
    own table pointer ([class.cdtor]/4) and members, through the same deferred
    owner, and is emitted only when construction selects it."""
    from pcc.frontends.cpp.constructors import _complete_name, _declaration
    from pcc.frontends.cpp.sema import Scope, Symbol
    owner = definition.dtype
    coord = owner.coord
    symbol = Symbol(owner.name, 'function', coord, dtype='void', parameters=())
    symbol.class_owner, symbol.access = owner, 'public'
    symbol.owner = definition.scope
    symbol.constructor = True
    symbol.constructor_explicit = False
    symbol.constructor_user_provided = False
    symbol.constructor_defaulted = True
    symbol.constructor_defaulted_on_first_declaration = True
    symbol.constructor_implicit = True
    symbol.constructor_trivial = False
    symbol.constructor_deleted = False
    symbol.method_static = False
    symbol.method_const = False
    symbol.internal_linkage = owner.internal_linkage
    symbol.c_name = _complete_name(owner, ())
    definition.constructors.overloads.append(symbol)
    parser.function_projection_names.add(symbol.c_name)
    declaration, this = _declaration(parser, symbol, [], [], True)
    defer_definition(definition, symbol, Scope(definition.scope), declaration, this)
    return declaration


def bind_defaulted_definitions(parser, definition):
    if not definition.defaulted_constructors:
        return
    if any(is_class_type(member.dtype) for member in definition.members):
        raise CppDiagnostic('cpp.unsupported.constructor-subobjects',
                            'defaulted C1 subobjects require recursive special-member deletion and initialization',
                            definition.dtype.coord)
    base = getattr(definition, 'base', None)
    if base is not None:
        from pcc.frontends.cpp.default_member_initializers import has_initializers
        from pcc.frontends.cpp.inheritance import base_chain
        chain = [base.dtype, *base_chain(base.dtype)]
        if any(item.definition.constructors.overloads or has_initializers(item.definition) for item in chain):
            raise CppDiagnostic('cpp.unsupported.constructor-subobjects',
                                'defaulted C1 base subobjects require recursive special-member initialization',
                                definition.dtype.coord)
    from pcc.frontends.cpp.constructors import (
        closed_construction_body,
        lower_member_initializers,
    )
    for pending in definition.defaulted_constructors:
        symbol = pending.symbol
        symbol.constructor_deleted = definition.default_constructor_deleted
        symbol.constructor_trivial = not symbol.constructor_user_provided and not definition.polymorphic and not any(
            getattr(member, 'default_initializer_tokens', None) is not None
            for member in definition.members)
        if symbol.constructor_deleted:
            # An unused deleted defaulted declaration is valid. Aggregate
            # initialization can bypass it; do not manufacture a failing body.
            continue
        stores = lower_member_initializers(parser, definition, pending.this, {})
        body = c_ast.Compound(stores, pending.coord)
        closed = closed_construction_body(body, {})
        symbol.closed_construction_body = symbol.closed_normal_body = closed
        if not closed:
            # Bound DMI expressions remain intact. Their calls need an owned
            # implicit exception specification, not a guessed noexcept body.
            symbol.defaulted_definition_unavailable = True
            continue
        symbol.nonthrowing = True
        pending.function = c_ast.FuncDef(pending.declaration, None, body, pending.coord)
    # DMI binding, conversion and deletion checks belong to class completion.
    # [class.ctor]/7 does not, however, implicitly define an unused defaulted
    # C1. An eager projected body could introduce an otherwise absent extern
    # data relocation merely because its class was declared.


def request_definition(parser, symbol):
    context = getattr(symbol, 'defaulted_definition', None)
    if context is None or parser.unevaluated_depth:
        return
    # The common call owner has already checked selection/access/deletion and
    # all supported effects. Use the session's sole worklist, not a C1 queue.
    parser.instance_registry.request((symbol, ()), symbol, context)


def emit_definition(parser, context):
    from pcc.frontends.cpp.inline_constructors import project_definition
    return project_definition(parser, context)


def require_usable(symbol, coord, *, object_type=None, initialization_kind='default'):
    if initialization_kind not in ('default', 'value'):
        raise ValueError('unknown constructor initialization kind')
    if not getattr(symbol, 'constructor_defaulted', False):
        return
    if symbol.constructor_deleted:
        raise CppDiagnostic('cpp.deleted-default-constructor',
                            'selected defaulted default constructor is deleted', coord)
    if getattr(symbol, 'defaulted_definition_unavailable', False):
        raise CppDiagnostic('cpp.unsupported.default-member-initializer-path',
                            'defaulted C1 with unknown initializer calls requires implicit exception semantics', coord)
    if (isinstance(object_type, ConstType)
            and not symbol.class_owner.definition.const_default_constructible):
        if initialization_kind == 'value':
            # Preserve the existing const nonaggregate value-init capability
            # boundary instead of silently deciding its N4659/DR constraints.
            raise CppDiagnostic('cpp.unsupported.const-class-initialization',
                                'const nonaggregate value-initialization constraints are not qualified', coord)
        raise CppDiagnostic('cpp.const-initializer',
                            'const class default initialization requires initialized subobjects', coord)


def value_initialization_prefix(parser, selected, address, coord, initialization_kind):
    if (initialization_kind != 'value'
            or not getattr(selected, 'constructor_defaulted', False)
            or getattr(selected, 'constructor_user_provided', True)):
        return [], address
    # The selected constructor is defaulted on its first declaration, hence
    # not user-provided. [dcl.init]/8.2 requires zero initialization before its
    # default initialization. Do not put these stores in C1: default-init calls
    # the same function but must leave omitted scalar members untouched.
    owner = selected.class_owner
    name = parser._fresh_projection_name('__pcc_cpp_value_init_')
    pointer = c_ast.ID(name, coord)
    temporary = parser._decl(name, PointerType(owner), coord, address)
    typename = c_ast.Typename(None, [], parser._scalar_type(None, owner, coord), coord)
    zero_object = c_ast.CompoundLiteral(typename, c_ast.InitList([], coord), coord)
    # The existing C aggregate-zero and whole-object store owners cover
    # interior/tail padding too. Member-by-member zero assignments would not.
    # This is initialization of the target storage, not source C++ assignment
    # or construction/destruction of an additional class object.
    zero = c_ast.Assignment('=', c_ast.UnaryOp('*', pointer, coord), zero_object, coord)
    return [temporary, zero], c_ast.ID(name, coord)
