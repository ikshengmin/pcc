"""Finite compiler-owned standard marker semantics, NOT a loaded core crate.

Contract v1 covers function/static-method Copy/Sized obligations on existing finite value
layouts. The defining identity is core; std and the edition prelude reexport it.
Local lexical bindings win before that prelude. no_core, source lang attributes,
staged_api and the inspection registry cannot activate this contract.

The temporary public admission boundary is an actual CheckedRustSource for ANY
explicit marker bound, including value-only code. This descriptor is provenance,
not a token, borrow checker, trust flag, standard-library loader or final pcc1
compiler certification. Clone is only the transitive Copy supertrait obligation;
its methods, implementations and explicit bounds remain unsupported.
"""
from dataclasses import dataclass

from .crate_policy import CratePolicy
from .types import FixedArrayType, TupleType, TypeApplication, TypeParameter

CONTRACT_VERSION = 'pcc.core-markers.finite-values.v1'


@dataclass(frozen=True)
class StandardMarkerIdentity:
    defining_crate: str
    language_item: str
    contract_version: str = CONTRACT_VERSION


SIZED = StandardMarkerIdentity('core', 'sized')
COPY = StandardMarkerIdentity('core', 'copy')
CLONE = StandardMarkerIdentity('core', 'clone')


@dataclass(frozen=True)
class CheckedMarkerContext:
    contract_version: str
    source_sha256: str
    compiler_sha256: str
    compiler_version: str
    edition: str
    target_triple: str
    implicit_library: str


class StandardMarkers:
    def __init__(self, checker, policy=None):
        self.checker = checker
        resolution = checker.module_resolution
        self.policy = resolution.policy if resolution is not None else policy or CratePolicy()
        self.policy.require_owned_lang_item_context()
        # Declaration-bound keys survive scope restoration and raw-body replay.
        self.constraints = {}
        # Trait calls obey the required declaration even when its concrete
        # impl method declares a weaker body environment. Neither map changes
        # canonical parameter identity or creates an instantiation registry.
        self.call_constraints = {}
        self.first_explicit_bound = None

    def resolve(self, bound, scope):
        first = bound.path.split('::', 1)[0]
        if first in scope:
            raise bound.token.error('generic parameter shadows the requested standard marker; expected a trait')
        checker = self.checker
        resolution = checker.module_resolution
        # Trait declarations are all in the module namespace, including later
        # declarations and the one whose signature is currently being checked.
        local = (first in checker.generics.definitions or
                 any(first == trait.token.text for trait in checker.traits.declarations))
        if resolution is not None:
            module = resolution.modules[checker.current_module]
            local = (('type', first) in module.items or first in resolution.extern_prelude)
        if local or first in {'crate', 'self', 'super'}:
            raise bound.token.error('local/user trait bounds are not implemented; a local name is not a standard marker')
        parts = bound.path.split('::')
        if len(parts) == 1 and self.policy.standard_prelude_path is not None:
            name = parts[0]
        elif (len(parts) == 3 and parts[0] in self.policy.implicit_extern_prelude
              and parts[1] == 'marker'):
            name = parts[2]
        else:
            raise bound.token.error('standard marker path is unavailable; external trait loading is not implemented')
        if name not in {'Copy', 'Sized'}:
            raise bound.token.error('only canonical standard Copy and Sized bounds are implemented; arbitrary traits/Clone methods are not')
        return COPY if name == 'Copy' else SIZED

    def prepare(self, function, scope):
        for parameter in scope.values():
            self.constraints[parameter] = frozenset({SIZED})
        for name, bounds in function.effective_parameter_bounds:
            parameter = scope[name.text]
            explicit, relaxed = set(), False
            for bound in bounds:
                identity = self.resolve(bound, scope)
                if bound.relaxed:
                    if identity != SIZED or relaxed:
                        raise bound.token.error('only one relaxed canonical ?Sized bound is implemented')
                    relaxed = True
                else:
                    explicit.add(identity)
                if self.first_explicit_bound is None:
                    self.first_explicit_bound = bound.token
            obligations = set() if relaxed else {SIZED}
            obligations.update(explicit)
            if COPY in obligations:
                # Rust Copy: Clone and Clone: Sized. ?Sized only removes the
                # default; it cannot cancel an explicit/supertrait obligation.
                obligations.update({CLONE, SIZED})
            self.constraints[parameter] = frozenset(obligations)

    def copy_parameter(self, parameter):
        return COPY in self.constraints.get(parameter, ())

    def require_sized(self, ty, token):
        if isinstance(ty, TypeParameter):
            if ty in self.constraints and SIZED not in self.constraints[ty]:
                raise token.error('type parameter requires canonical Sized at this by-value/storage position; ?Sized does not provide DST layout support')
        elif isinstance(ty, (FixedArrayType, TupleType, TypeApplication)):
            from .generics import children
            for item in children(ty):
                self.require_sized(item, token)
        # A reference itself is Sized regardless of its referent; concrete DST
        # referents are still rejected by the existing type/layout capability.

    def proves_sized(self, ty):
        if isinstance(ty, TypeParameter):
            # Only function parameters may relax the default in this slice.
            return SIZED in self.constraints.get(ty, {SIZED})
        if isinstance(ty, (FixedArrayType, TupleType, TypeApplication)):
            from .generics import children
            return all(self.proves_sized(item) for item in children(ty))
        return True  # Called only after existing type/layout validation.

    def check_call(self, parameters, arguments, token):
        for parameter, argument in zip(parameters, arguments):
            obligations = self.call_constraints.get(parameter, self.constraints[parameter])
            if SIZED in obligations and not self.proves_sized(argument):
                raise token.error('generic call does not satisfy canonical Sized obligation')
            if COPY in obligations and not self.checker.copy_type(argument):
                raise token.error('generic call does not satisfy canonical Copy obligation')

    def checked_context(self, checked, source, target, overflow_checks):
        if self.first_explicit_bound is None:
            return None
        from .external_check import CheckedRustSource
        if type(checked) is not CheckedRustSource:
            raise self.first_explicit_bound.error('standard marker bounds require an actual source/options-bound CheckedRustSource during this transition')
        checked.validate(source, target_triple=target, overflow_checks=overflow_checks)
        record = checked.provenance
        if record.edition != str(self.policy.edition):
            raise self.first_explicit_bound.error('standard marker context and checked edition differ')
        return CheckedMarkerContext(CONTRACT_VERSION, record.source_sha256,
                                    record.compiler_sha256, record.compiler_version,
                                    record.edition, record.target_triple,
                                    self.policy.implicit_library_import)
