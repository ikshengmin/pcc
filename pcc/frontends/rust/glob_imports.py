"""Weak module glob bindings in a closed Rust source graph.

Items and named imports shadow globs *per namespace*, independent of order
(Rust Reference items.use.glob). Keep the definition identity and the actual
visibility of each route; a private module can re-export a public declaration.
Cycles of module globs propagate names to a bounded fixed point. No wildcard
inventories are synthesized for enums, external libraries or the macro prelude.
"""
from collections import defaultdict, deque

from .crate_context import ModuleId
from .syntax import CrateSelfAlias, Module, Record


class ModuleGlobImports:
    MAX_EDGES = 4096
    MAX_WORK = 262144

    def __init__(self, namespaces):
        self.ns = namespaces
        self.entries = defaultdict(list)
        self.edges = defaultdict(list)
        self.count = 0
        self.remaining = self.MAX_WORK
        self.cache = {}
        self.active = set()
        self.binding_targets = False

    def add(self, owner, item):
        self.count += 1
        if self.count > self.MAX_EDGES:
            raise item.glob.error('module glob import count exceeds implementation limit')
        self.entries[owner].append(item)

    def charge(self, token, cost=1):
        self.remaining -= cost
        if self.remaining < 0:
            raise token.error('module glob resolution exceeds work limit')

    def bind_targets(self):
        # Strong declaration/named-alias paths only. Filling a missing segment
        # from an incomplete glob propagation could select a transient module.
        # A target that itself requires a glob-supplied name fails, never binds
        # the first source encountered. Every target is validated even if unused.
        self.binding_targets = True
        try:
            for owner, items in self.entries.items():
                for item in items:
                    source = self._target(owner, item)
                    self.edges[owner].append((source, self.ns._scope(item.public, owner), item.glob))
        finally:
            self.binding_targets = False
            # A named target might ALSO import a second namespace supplied by
            # a glob. Never reuse its provisional, strong-only binding map.
            self.ns._resolved_uses.clear()
        for owner, items in self.entries.items():
            for item, (source, _, _) in zip(items, self.edges[owner]):
                if self._target(owner, item) != source:
                    raise item.glob.error('glob-dependent target changes during import resolution')

    def _target(self, owner, item):
        self.charge(item.glob, 1 + len(item.path))
        path = tuple(t.text for t in item.path)
        if path == ('crate',):
            source = self.ns.root
        elif path == ('self',):
            source = owner
        elif all(seg == 'super' for seg in path):
            if len(path) > len(owner.components):
                raise item.glob.error('too many leading super path segments')
            source = ModuleId(owner.crate, owner.components[:-len(path)])
        else:
            decl = self.ns.resolve('::'.join(path), 'type', owner, item.glob,
                                   _use_root=self.ns.policy.edition == 2015)
            if isinstance(decl.syntax, CrateSelfAlias):
                source = self.ns.root
            elif isinstance(decl.syntax, Module):
                source = decl.identity.module.child(decl.identity.name)
            else:
                raise item.glob.error('glob import of non-module or enum variants is not implemented')
        if source == owner:
            raise item.glob.error('cannot glob-import a module into itself')
        return source

    _DISJOINT = object()

    def _narrow(self, a, b):
        if self.ns._inside(a, b):
            return a
        if self.ns._inside(b, a):
            return b
        return self._DISJOINT

    def weak(self, owner, namespace, name):
        if self.binding_targets or not self.entries.get(owner):
            return {}
        key = owner, namespace, name
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        token = self.entries[owner][0].glob
        if key in self.active or len(self.active) >= 64:
            raise token.error('cyclic named/glob binding or glob query recursion limit')
        self.active.add(key)
        try:
            result = self._collect(owner, namespace, name, token)
            self.cache[key] = result
            return result
        finally:
            self.active.remove(key)

    def _collect(self, owner, namespace, name, token):
        # Discover only this name's reachable providers. A strong binding, even
        # a non-exported one, cuts off all wildcard paths *in that namespace*.
        reverse = defaultdict(list)
        seen = set()
        stack = [owner]
        values = defaultdict(dict)
        work = deque()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            self.charge(token)
            strong = self.ns._strong_binding(node, namespace, name)
            if strong is not None:
                decl, scope = strong
                if namespace == 'value' and isinstance(decl.syntax, Record) and decl.syntax.tuple_fields:
                    # A constructor is only as visible as the least-visible field.
                    self.charge(token, len(decl.syntax.field_public))
                    for field in decl.syntax.field_public:
                        scope = self._narrow(scope, self.ns._scope(field, decl.identity.module))
                        if scope is self._DISJOINT:
                            break
                if scope is self._DISJOINT:
                    continue
                values[node][decl.identity] = (decl, scope)
                work.append((node, decl.identity))
                continue
            for source, scope, _ in self.edges.get(node, ()):
                self.charge(token)
                reverse[source].append((node, scope))
                stack.append(source)

        # Finite lattice: for each canonical declaration, visibility can only
        # grow toward the crate root. Distinct definitions are NOT collapsed.
        while work:
            node, identity = work.popleft()
            decl, scope = values[node][identity]
            for dest, glob_scope in reverse.get(node, ()):
                self.charge(token)
                if not self.ns._within(dest, scope) or not self.ns.visible(decl, dest):
                    continue
                offered = self._narrow(scope, glob_scope)
                if offered is self._DISJOINT:
                    continue
                old = values[dest].get(identity)
                if old is None or not self.ns._inside(offered, old[1]):
                    values[dest][identity] = (decl, offered)
                    work.append((dest, identity))
        if values[owner] and any(node != owner and len(found) > 1 for node, found in values.items()):
            # An ambiguous intermediate binding isn't exported as one of its
            # alternatives. Filtering its private contender here would silently
            # admit the remaining path, e.g. {use a::*; pub use b::*}.
            # A separate unambiguous route can exist, but requires provenance
            # through ambiguous SCCs; do not guess which route supplies it.
            raise token.error(f'ambiguous upstream glob import for {namespace}-namespace name {name!r}')
        return values[owner]

    def public_declarations(self, owner):
        """Unshadowed, externally public glob names for the missing_docs owner."""
        if not self.entries.get(owner):
            return
        token = self.entries[owner][0].glob
        names, seen, stack = set(), set(), [owner]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            entries = self.ns.modules[node].items
            aliases = self.ns._uses[node]
            edges = self.edges.get(node, ())
            self.charge(token, 1 + len(entries) + 2 * len(aliases) + len(edges))
            names.update(entries)
            for name in aliases:
                names.update((ns, name) for ns in ('type','value'))
            stack.extend(src for src, _, _ in edges)
        self.charge(token, len(names))
        for space, name in sorted(names):
            if self.ns._strong_binding(owner, space, name) is not None:
                continue
            candidates = self.weak(owner, space, name)
            self.charge(token, len(candidates))
            if len(candidates) == 1:
                decl, scope = next(iter(candidates.values()))
                if scope is None:
                    yield decl
            elif any(scope is None for _,scope in candidates.values()):
                # Do not silently waive missing_docs through ambiguous reexports.
                raise token.error('missing_docs on ambiguous public glob reexports is not implemented')
