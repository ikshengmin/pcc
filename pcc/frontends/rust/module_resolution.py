"""Per-module Rust namespaces feeding the existing owned semantic checker.

Canonical declaration keys identify storage only after scoped lookup and privacy
checks. They are never searched as a flattened source namespace. Private and
`pub use` (re-export) imports resolve within the supplied module closure.
External crates and module-scoped trait/method candidate sets remain explicit
gaps; no import synthesizes source or supplies library definitions.
"""
from hashlib import sha256
from pathlib import PurePosixPath
from types import MappingProxyType

from .crate_context import (
    CrateId, CrateSyntax, ModuleId, SourceFile, _Loader,
)
from .generics import PRIMITIVES
from .syntax import CrateSelfAlias, Enumeration, Module, ModuleBody, Record, RustError
from .types import TypeApplication
from .visibility import scope_components, within


# A bare use target can resolve in the language prelude even when this frontend
# cannot lower that primitive. Do not drop its type namespace while importing
# a same-spelled ordinary function from the value namespace.
_LANGUAGE_PRIMITIVE_USE_TARGETS = PRIMITIVES | {
    'char', 'str', 'i8', 'i16', 'i128', 'u8', 'u16', 'u128',
    'f16', 'f32', 'f64', 'f128',
}


_NOT_REEXPORTED = object()


class ModuleNamespaces:
    """Name/privacy lookup only; never builds Checker inputs or grants admission."""
    def __init__(self, crate):
        self.crate = crate
        self.policy = crate.policy
        self.extern_prelude = {item.token.text: crate.root.items['type', item.token.text]
                               for item in crate.root.body.crate_aliases}
        self.modules = {}
        self.by_key = {}
        pending = [crate.root]
        while pending:
            module = pending.pop()
            self.modules[module.identity] = module
            pending.extend(reversed(module.children))
            for declaration in module.items.values():
                self.by_key[declaration.identity.binding_key] = declaration
        self._uses = {}
        self._resolved_uses = {}
        self._resolving_uses = set()
        # Every restricted visibility names an ancestor of its owner, checked
        # eagerly for items, fields and uses, whether or not they are used.
        for module in self.modules.values():
            owner = module.identity
            for declaration in module.items.values():
                self._scope(declaration.public, owner)
            for record in module.body.records:
                for visibility in record.field_public:
                    self._scope(visibility, owner)
            for item in module.body.uses:
                self._scope(item.public, owner)
        for module in self.modules.values():
            imports = self._uses[module.identity] = {}
            for item in module.body.uses:
                name = item.binding.text
                if name in imports:
                    raise item.token.error('multiple use bindings sharing a spelling require namespace-merge resolution, which is not implemented')
                imports[name] = item
        # Resolve even unused imports. A later call is never permission to
        # skip unavailable dependencies, privacy, conflicts or import cycles.
        for module, imports in self._uses.items():
            for name in imports:
                self._bind_use(module, name)

    @property
    def root(self):
        return self.crate.root.identity

    def _scope(self, visibility, owner):
        """None for unrestricted `pub`, else the module whose subtree sees it."""
        components = scope_components(visibility, owner.components, self.policy.edition)
        return None if components is None else ModuleId(owner.crate, components)

    @staticmethod
    def _within(requester, scope):
        return scope is None or (requester.crate == scope.crate and
                                 within(requester.components, scope.components))

    @classmethod
    def _inside(cls, inner, outer):
        """Whether every module seeing scope `inner` also sees scope `outer`."""
        if outer is None:
            return True
        return inner is not None and cls._within(inner, outer)

    def visible(self, declaration, requester):
        return self._within(requester, self._scope(declaration.public, declaration.identity.module))

    def has_local_type_binding(self, name, module):
        """Lexical items/imports only, excluding every implicit prelude."""
        return (('type', name) in self.modules[module].items or
                'type' in self._resolved_uses.get((module, name), {}))

    def _bind_use(self, module, name):
        key = (module, name)
        if key in self._resolved_uses:
            return self._resolved_uses[key]
        item = self._uses[module][name]
        if key in self._resolving_uses:
            raise item.token.error('cyclic use bindings require fixed-point import resolution, which is not implemented')
        if len(self._resolving_uses) >= 128:
            raise item.token.error('use binding dependency chain exceeds implementation limit (128)')
        if len(item.path) == 1 and item.path[0].text in _LANGUAGE_PRIMITIVE_USE_TARGETS:
            raise item.token.error('unqualified primitive use targets require language-prelude resolution, which is not implemented')
        self._resolving_uses.add(key)
        try:
            path = '::'.join(token.text for token in item.path)
            bindings = {}
            # A simple use imports every modeled namespace, not whichever one
            # the first consumer happens to request. Canonical declaration
            # identity stays at the target, separately from alias visibility.
            reexport = self._scope(item.public, module)
            for namespace in ('type', 'value'):
                declaration = self.resolve(path, namespace, module, item.token,
                                           _use_root=self.policy.edition == 2015,
                                           _optional_final=True,
                                           _reexport=reexport)
                if declaration is None:
                    continue
                # Generic normalization and associated-path lookup still
                # recognize these language-prelude names before scoped lookup.
                # Do not admit a nominal type alias that those owners could
                # silently reinterpret as a primitive. Value-only aliases do
                # not shadow the type namespace and retain ordinary lookup.
                # Module aliases retain module lookup in qualified paths, but
                # bare type positions fall back to the language primitive.
                if (namespace == 'type' and name in PRIMITIVES
                        and not isinstance(declaration.syntax, (Module, CrateSelfAlias))):
                    raise item.binding.error('primitive-shadowing type use bindings require scoped primitive lookup, which is not implemented')
                if (namespace, name) in self.modules[module].items:
                    raise item.binding.error(f'duplicate imported {namespace}-namespace item {name!r}')
                bindings[namespace] = declaration
            if not bindings:
                raise item.token.error(f'unknown use target {path!r} in the supplied module closure')
            self._resolved_uses[key] = bindings
            return bindings
        finally:
            self._resolving_uses.remove(key)

    def _binding(self, module, namespace, name, requester, token):
        """The declaration a name denotes in `module`, and the visibility
        scope of that binding (the item itself, or the use binding naming it)."""
        declaration = self.modules[module].items.get((namespace, name))
        if declaration is not None:
            return declaration, self._scope(declaration.public, module)
        item = self._uses[module].get(name)
        if item is None:
            return None, module
        scope = self._scope(item.public, module)
        if not self._within(requester, scope):
            raise token.error(f'private use binding {name!r} is inaccessible from this module')
        return self._bind_use(module, name).get(namespace), scope

    def _start(self, path, requester, token):
        parts = path.split('::')
        current = requester
        if parts[0] == 'crate':
            current = self.root
            parts.pop(0)
        elif parts[0] == 'self':
            parts.pop(0)
        else:
            while parts and parts[0] == 'super':
                if not current.components:
                    raise token.error('too many leading super path segments')
                current = ModuleId(current.crate, current.components[:-1])
                parts.pop(0)
        if not parts or any(not p for p in parts):
            raise token.error('item path must name a declaration')
        return current, parts

    def resolve(self, path, namespace, requester, token, *, _use_root=False, _optional_final=False,
                _reexport=_NOT_REEXPORTED):
        current, parts = self._start(path, requester, token)
        if _use_root and path.split('::', 1)[0] not in {'crate', 'self', 'super'}:
            current = self.root
        for index, name in enumerate(parts):
            space = namespace if index == len(parts) - 1 else 'type'
            declaration, scope = self._binding(current, space, name, requester, token)
            implicit = index == 0 and space == 'type' and path.split('::', 1)[0] not in {'crate', 'self', 'super'}
            if declaration is None and implicit:
                declaration = self.extern_prelude.get(name)
                if declaration is not None:
                    scope = self._scope(declaration.public, declaration.identity.module)
            if declaration is None:
                if implicit and name in self.policy.implicit_extern_prelude:
                    raise token.error('implicit library/prelude source dependency is unavailable; external crate loading is not implemented')
                if _optional_final and index == len(parts) - 1:
                    return None
                raise token.error(f'unknown {space}-namespace item {name!r} in module path {path!r}')
            if not self.visible(declaration, requester):
                raise token.error(f'private item {name!r} is inaccessible from this module')
            if index == len(parts) - 1:
                # A use cannot widen visibility (rustc E0364/E0365): its own
                # scope must lie inside the re-exported binding's scope.
                if _reexport is not _NOT_REEXPORTED and not self._inside(_reexport, scope):
                    if scope == ModuleId(scope.crate, ()):
                        raise token.error(f'{name!r} is only public within the crate, and cannot be re-exported outside')
                    raise token.error(f'{name!r} is private, and cannot be re-exported')
                return declaration
            if isinstance(declaration.syntax, CrateSelfAlias):
                current = self.root
            elif isinstance(declaration.syntax, Module):
                # Imported modules retain their target owner/name. The alias
                # spelling must not invent a child under the importing scope.
                current = declaration.identity.module.child(declaration.identity.name)
            else:
                raise token.error('intermediate module path component is not a module')
        raise AssertionError('empty path escaped validation')


class ModuleResolution(ModuleNamespaces):
    def __init__(self, crate):
        crate.policy.require_owned_lang_item_context()
        if crate.compiler_context_obligations:
            obligation = crate.compiler_context_obligations[0]
            raise obligation.token.error("unresolved owned compiler context: " + obligation.description)
        super().__init__(crate)
        self.functions, self.records, self.enums = [], [], []
        for module in self.modules.values():
            if module.body.traits or module.body.implementations:
                item = (module.body.traits or module.body.implementations)[0]
                raise item.token.error('module-scoped trait and inherent method resolution is not implemented')
            for declaration in module.items.values():
                item = declaration.syntax
                if isinstance(item, (Module, CrateSelfAlias)):
                    continue
                item.item_identity = declaration.identity
                if isinstance(item, Record):
                    self.records.append(item)
                elif isinstance(item, Enumeration):
                    self.enums.append(item)
                else:
                    if item.abi != 'Rust' or item.no_mangle:
                        raise item.token.error('foreign/export ABI in a resolved module crate is not implemented')
                    self.functions.append(item)

    def type_name(self, path, requester, token):
        if path in self.by_key:
            return path  # Already-resolved nominal type, not a lexical spelling.
        declaration = self.resolve(path, 'type', requester, token)
        if not isinstance(declaration.syntax, (Record, Enumeration)):
            raise token.error('type path does not name a record or enum')
        return declaration.identity.binding_key

    def function(self, path, requester, token):
        declaration = self.resolve(path, 'value', requester, token)
        return declaration.syntax

    def associated_function(self, owner, name, requester, token):
        if isinstance(owner, TypeApplication):
            return None
        if owner in PRIMITIVES:
            # Rust resolves a real module member before primitive associated
            # items, but an absent member can fall back to the primitive. Keep
            # privacy errors and nominal type shadowing out of that fallback.
            declaration = self.resolve(owner, 'type', requester, token, _optional_final=True)
            if declaration is not None and isinstance(declaration.syntax, (Module, CrateSelfAlias)):
                member = self.resolve(owner + '::' + name, 'value', requester, token,
                                      _optional_final=True)
                if member is not None:
                    return member.syntax
            return None
        if owner == 'Self' or owner in self.by_key:
            return None
        if owner in {'crate', 'self'} or all(p == 'super' for p in owner.split('::')):
            return self.function(owner + '::' + name, requester, token)
        declaration = self.resolve(owner, 'type', requester, token)
        if not isinstance(declaration.syntax, (Module, CrateSelfAlias)):
            return None
        return self.function(owner + '::' + name, requester, token)

    def type_access(self, ty, requester, token):
        from .generics import children
        pending = [ty]
        while pending:
            item = pending.pop()
            name = item.name if isinstance(item, TypeApplication) else item
            if isinstance(name, str) and name in self.by_key:
                declaration = self.by_key[name]
                if not self.visible(declaration, requester):
                    raise token.error(f'private type {declaration.identity.name!r} is inaccessible from this module')
            pending.extend(children(item))

    def field_access(self, record, index, requester, token):
        if record.item_identity is not None:
            self.type_access(record.item_identity.binding_key, requester, token)
        if record.item_identity is None:
            return
        if not self._within(requester, self._scope(record.field_public[index], record.item_identity.module)):
            raise token.error(f'private field {record.fields[index][0].text!r} is inaccessible from this module')


def inline_crate(parser, functions):
    """Reuse already-parsed exact source; outlined files cannot be invented."""
    path, source = 'lib.rs', parser.source
    raw_hash = sha256(source.encode('utf-8')).hexdigest()
    file = SourceFile(path, source, raw_hash)
    files = MappingProxyType({path: file})
    encoded = path.encode('utf-8')
    closure = sha256(len(encoded).to_bytes(8, 'big') + encoded + bytes.fromhex(raw_hash)).hexdigest()
    edition = parser.crate_policy.edition
    identity = CrateId('rust', path, edition, closure)
    body = ModuleBody(functions, parser.records, parser.enums, parser.implementations,
                      parser.traits, parser.modules, parser.crate_documentation,
                      parser.crate_aliases, parser.crate_policy, parser.conditional_items,
                      uses=parser.uses)
    graph = _Loader(files).load_body(file, body, ModuleId(identity, ()), PurePosixPath('.'), None, inline=False)
    return CrateSyntax(graph, edition, files)


def analyze_crate_syntax(crate, *, pointer_bits=64):
    """Owned Sema for supplied modules; no IR or reference-certification claim.

    Outlined reference-bearing crates require a future actual same-closure
    external checker. A root-only token cannot authorize unseen module bytes.
    """
    from .reference_admission import reject_uncertified_references
    from .semantics import Checker
    resolution = ModuleResolution(crate)
    reject_uncertified_references(resolution.functions, resolution.records, resolution.enums)
    checker = Checker(resolution.functions, resolution.records, resolution.enums,
                      pointer_bits=pointer_bits, module_resolution=resolution)
    checker.check()
    from .moves import MoveChecker
    MoveChecker(checker).check()
    return checker
