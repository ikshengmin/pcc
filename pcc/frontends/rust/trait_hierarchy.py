"""Declared supertrait obligations shared by static Sema and core context.

Keys are the existing caller's declaration identities. This is graph validation,
not a trait solver, an automatic implementation, or a substitute for Sized/Copy
builtin semantics. The core-context caller uses canonical ItemIds; normal static
Sema uses its existing root trait declaration names and concrete impl registry.
"""
from types import MappingProxyType


class TraitHierarchy:
    def __init__(self, declarations, resolve_parent):
        if len(declarations) > 1024:
            raise next(iter(declarations.values())).token.error(
                "trait hierarchy exceeds implementation limit (1024 declarations)")
        direct = {}
        for identity, declaration in declarations.items():
            parents = []
            for token, path in declaration.supertraits:
                parent = resolve_parent(identity, path, token)
                if parent not in declarations:
                    raise token.error('supertrait path does not name a declared trait')
                if parent not in parents:
                    parents.append(parent)
            direct[identity] = tuple(parents)
        closure = {}
        depth = {}
        closure_entries = 0
        # Iterative DFS: no recursion limit can turn a source cycle into an
        # interpreter failure. A generous bounded path is an explicit gap.
        for start in declarations:
            pending, active = [(start, False)], set()
            while pending:
                identity, leaving = pending.pop()
                if identity in closure:
                    continue
                if leaving:
                    depth[identity] = 1 + max((depth[p] for p in direct[identity]), default=0)
                    if depth[identity] > 128:
                        raise declarations[identity].token.error(
                            "supertrait dependency depth exceeds implementation limit (128)")
                    inherited = set(direct[identity])
                    for parent in direct[identity]:
                        inherited.update(closure[parent])
                    closure_entries += len(inherited)
                    if closure_entries > 65536:
                        raise declarations[identity].token.error(
                            "supertrait closure exceeds implementation limit (65536 obligations)")
                    closure[identity] = frozenset(inherited)
                    active.remove(identity)
                    continue
                if identity in active:
                    raise declarations[identity].token.error('cycle in supertrait requirements')
                if len(active) >= 128:
                    raise declarations[identity].token.error('supertrait dependency depth exceeds implementation limit (128)')
                active.add(identity)
                pending.append((identity, True))
                for parent in reversed(direct[identity]):
                    pending.append((parent, False))
        self.direct = MappingProxyType(direct)
        self.ancestors = MappingProxyType(closure)

    def require_concrete_impls(self, implementations, tokens):
        # Never infer an impl from a subtrait or merge inherited members into a
        # different trait's namespace. Every required trait has its own impl.
        for trait, owner in implementations:
            for parent in self.ancestors[trait]:
                if (parent, owner) not in implementations:
                    raise tokens[trait, owner].error('missing required supertrait implementation')
