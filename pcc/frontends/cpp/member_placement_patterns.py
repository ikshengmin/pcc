"""Typed reserved placement-new replay for ordinary-class member templates.

N4659 [expr.new], [new.delete.placement], [temp.res]. No token replay,
heap substitution, runtime allocation, or invented constructor proof.
"""
from copy import copy
from dataclasses import dataclass

from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.templates import Pattern, dependent, value_dependent
from pcc.frontends.cpp.types import PointerType


def active_definition(parser):
    origin = getattr(parser, 'template_function', None)
    owner = getattr(origin, 'class_owner', None)
    return (owner is not None and not getattr(owner, 'template_origin', None)
            and not getattr(parser, 'class_template_method', None))


def capture_group(parser):
    group = parser.scope.global_scope().names.get('operator new')
    if group is None:
        return None
    # Preserve declaration identities, not a scope lookup at instantiation.
    # Later global declarations cannot silently join this overload set.
    snapshot = copy(group)
    snapshot.overloads = tuple(copy(overload) for overload in group.overloads)
    return snapshot


@dataclass(frozen=True)
class PlacementPOI:
    scope_identity: object
    declaration_identities: tuple
    group: object
    coord: object


def bind_selected_poi(parser, instance, coord):
    """Save unknown-body uses now, enforce only when dependent placement exists."""
    origin = instance.template_origin
    needed = getattr(origin, 'has_dependent_placement_new', False)
    if origin.pattern is not None and not needed:
        return
    if parser.instantiating_template:
        instance.placement_poi_unqualified_nested = True
        if needed:
            require_selected_poi(instance, coord)
        return
    scope = parser.scope.global_scope()
    group = scope.names.get('operator new')
    declarations = (() if group is None else
                    (id(group), group.kind, tuple(id(item) for item in group.overloads)))
    prior = getattr(instance, 'placement_poi', None)
    if prior is None:
        instance.placement_poi = PlacementPOI(scope, declarations, capture_group(parser), coord)
    elif prior.scope_identity is not scope or prior.declaration_identities != declarations:
        instance.placement_poi_conflicting = True
    if needed:
        require_selected_poi(instance, coord)


def require_selected_poi(instance, coord):
    if getattr(instance, 'placement_poi_unqualified_nested', False):
        raise CppDiagnostic('cpp.unsupported.placement-instantiation-context',
                            'nested deferred placement needs a propagated point-of-instantiation context', coord)
    if getattr(instance, 'placement_poi_conflicting', False):
        raise CppDiagnostic('cpp.unsupported.placement-multiple-poi',
                            'different placement lookup contexts for one specialization are not qualified', coord)
    poi = getattr(instance, 'placement_poi', None)
    if poi is None:
        raise CppDiagnostic('cpp.unsupported.placement-instantiation-context',
                            'dependent placement requires a captured selected ODR-use context', coord)
    return poi


def check_concrete_initializer(parser, dtype, arguments, kind, coord):
    """Definition-time selection only: no C AST, body requests or evaluation.

    Uses the same candidate/access/usability owners as constructor_call. Cases
    needing conversion/default-expression execution are explicitly unsupported,
    not provisionally accepted because the member template might stay cold.
    """
    from pcc.frontends.cpp.types import unqualified, ReferenceType
    from pcc.frontends.cpp.classes import is_class_type
    from pcc.frontends.cpp.constructors import is_copy_constructor, construction_cannot_escape
    from pcc.frontends.cpp.inheritance import is_proper_base
    from pcc.frontends.cpp.sema import resolve_overload
    from pcc.frontends.cpp.default_constructors import require_usable
    owner = unqualified(dtype)
    group = owner.definition.constructors
    if not group.overloads:
        raise CppDiagnostic('cpp.unsupported.placement-implicit-construction',
                            'implicit placement initialization is not implemented', coord)
    if (len(arguments) == 1 and (unqualified(arguments[0].dtype) is owner
                                 or is_proper_base(owner, arguments[0].dtype))
            and not any(is_copy_constructor(candidate, owner) for candidate in group.overloads)):
        raise CppDiagnostic('cpp.unsupported.class-initialization',
                            'implicit copy constructor requires special-member semantics', coord)
    selected = resolve_overload(group, [arg.dtype for arg in arguments], parser.types, coord,
                                [arg.null_pointer for arg in arguments],
                                [arg.lvalue or arg.assignment_result for arg in arguments])
    parser.scope.check_member_access(selected, coord)
    require_usable(selected, coord, object_type=dtype, initialization_kind=kind)
    if (len(arguments) != len(selected.parameters)
            or any(isinstance(param, ReferenceType) or is_class_type(param)
                   for param in selected.parameters)
            or any(is_class_type(arg.dtype) for arg in arguments)):
        raise CppDiagnostic('cpp.unsupported.placement-definition-initialization',
                            'definition-time defaults or class/reference conversions are not qualified', coord)
    if not construction_cannot_escape(selected):
        raise CppDiagnostic('cpp.unsupported.placement-constructor-effects',
                            'definition-time constructor effects are not yet proved', coord)


def retain(parser, dtype, placement, arguments, kind, global_only, group, coord):
    from pcc.frontends.cpp.classes import is_class_type, require_complete
    from pcc.frontends.cpp.parser import Expression
    if parser.unevaluated_depth:
        raise CppDiagnostic('cpp.unsupported.allocation',
                            'unevaluated placement new is not implemented', coord)
    deferred_lookup = dependent(dtype)
    if deferred_lookup:
        parser.template_function.has_dependent_placement_new = True
    if not deferred_lookup and (group is None or group.kind != 'function'):
        raise CppDiagnostic('cpp.undeclared-allocation-function',
                            'placement new requires its allocation function declaration', coord)
    if not dependent(dtype):
        if not is_class_type(dtype):
            raise CppDiagnostic('cpp.unsupported.placement-object-type',
                                'placement initialization requires a class constructor', coord)
        require_complete(dtype, coord)
        if not any(value_dependent(arg) for arg in arguments):
            check_concrete_initializer(parser, dtype, arguments, kind, coord)
    result = PointerType(dtype)
    value = (dtype, len(placement), kind, global_only, group, deferred_lookup)
    return Expression(Pattern('member-placement-new', coord, value, placement + arguments), result,
                      constant_fact=not_constant(result, 'placement construction', coord))


def instantiate(parser, pattern, bindings, environment):
    from pcc.frontends.cpp.allocation import lower_reserved_placement
    dtype, count, kind, global_only, group, deferred_lookup = pattern.value
    concrete = parser._substitute_pattern_type(dtype, bindings, pattern.coord)
    if deferred_lookup:
        # [expr.new] allocation lookup on the substituted allocated class is
        # not ordinary dependent postfix-call lookup. Never invent <new>.
        # The common owner still refuses class-specific/nonreserved forms.
        instance = getattr(parser, 'member_template_instance', None)
        poi = require_selected_poi(instance, pattern.coord)
        group = poi.group
        if group is None:
            raise CppDiagnostic('cpp.unsupported.placement-instantiation-context',
                                'no placement declaration at captured use; later/end-of-TU instantiation contexts are not qualified', pattern.coord)
    children = [parser._instantiate_expression(child, bindings, environment)
                for child in pattern.children]
    return lower_reserved_placement(parser, concrete, children[:count], children[count:],
                                    kind, global_only, group, pattern.coord)
