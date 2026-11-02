"""Per-module Rust namespaces feeding the existing owned semantic checker.

Canonical declaration keys identify storage only after scoped lookup and privacy
checks. They are never searched as a flattened source namespace. This first
slice deliberately has no imports/reexports, external crates or module-scoped
trait/method candidate sets; those require their actual resolution rules.
"""
from hashlib import sha256
from pathlib import PurePosixPath
from types import MappingProxyType

from .crate_context import (
    CrateId, CrateSyntax, ModuleId, SourceFile, _Loader,
)
from .syntax import CrateSelfAlias, Enumeration, Module, ModuleBody, Record, RustError
from .types import TypeApplication


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

    @property
    def root(self):
        return self.crate.root.identity

    def visible(self, declaration, requester):
        owner = declaration.identity.module
        return (declaration.public or
                requester.crate == owner.crate and
                requester.components[:len(owner.components)] == owner.components)

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

    def resolve(self, path, namespace, requester, token):
        current, parts = self._start(path, requester, token)
        for index, name in enumerate(parts):
            space = namespace if index == len(parts) - 1 else 'type'
            declaration = self.modules[current].items.get((space, name))
            implicit = index == 0 and space == 'type' and path.split('::', 1)[0] not in {'crate', 'self', 'super'}
            if declaration is None and implicit:
                declaration = self.extern_prelude.get(name)
            if declaration is None:
                if implicit and name in self.policy.implicit_extern_prelude:
                    raise token.error('implicit library/prelude source dependency is unavailable; external crate loading is not implemented')
                raise token.error(f'unknown {space}-namespace item {name!r} in module path {path!r}')
            if not self.visible(declaration, requester):
                raise token.error(f'private item {name!r} is inaccessible from this module')
            if index == len(parts) - 1:
                return declaration
            if isinstance(declaration.syntax, CrateSelfAlias):
                current = self.root
            elif isinstance(declaration.syntax, Module):
                current = current.child(name)
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
        if owner in {'Self', 'bool', 'i32', 'i64', 'u32', 'u64', 'usize', 'isize', '()'} or owner in self.by_key:
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
        if record.item_identity is None or record.field_public[index]:
            return
        owner = record.item_identity.module
        if requester.crate != owner.crate or requester.components[:len(owner.components)] != owner.components:
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
                      parser.crate_aliases, parser.crate_policy)
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
