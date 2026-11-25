"""Direct, source-built Rust library dependencies, not sysroot/Cargo loading.

Each closed dependency is independently parsed, checked and lowered. Modules
retain their defining scope; only already-checked monomorphic scalar Rust
function signatures may cross the executable boundary. No AST
from a foreign body is added to the consumer, and no caller-supplied declaration,
object, metadata or checking token can attest a provider.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .crate_context import ItemDeclaration, ItemId, _logical_path
from .editions import require_edition
from .syntax import Function, Module, RustError, _KEYWORDS
from .targets import resolve_target
from .types import INTEGER_TYPES


@dataclass(frozen=True)
class RustSourceDependency:
    """Closed, byte-exact source files of one direct crate, with per-file SHA-256."""
    crate_name: str
    root_path: str
    edition: int
    target_triple: str
    source: bytes
    source_sha256: str
    # Additional original files, each (logical path, UTF-8 bytes, expected SHA-256).
    module_sources: tuple[tuple[str, bytes, str], ...] = ()


_SYSTEM_NAMES = frozenset({'core', 'alloc', 'std'})
_SCALARS = INTEGER_TYPES | {'bool', '()'}


def _name(value):
    return (type(value) is str and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value)
            and value not in _KEYWORDS and value not in {'_', 'gen'})


def checked_inputs(externs, target):
    from .module_resolution import _LANGUAGE_PRIMITIVE_USE_TARGETS

    if type(externs) is not dict or not 1 <= len(externs) <= 8:
        raise RustError('external source map requires 1 to 8 named direct dependencies')
    frozen = []
    for alias, source in externs.items():
        if not _name(alias) or type(source) is not RustSourceDependency or not _name(source.crate_name):
            raise RustError('external sources require an explicit Rust crate name, alias and RustSourceDependency')
        if alias in _SYSTEM_NAMES or source.crate_name in _SYSTEM_NAMES:
            raise RustError('system crates require an owned sysroot; an external source alias cannot supply one')
        if alias in _LANGUAGE_PRIMITIVE_USE_TARGETS:
            raise RustError('external aliases shadowing primitive types require additional namespace resolution')
        _logical_path(source.root_path)
        require_edition(source.edition)
        if resolve_target(source.target_triple).triple != target.triple:
            raise RustError('external source target does not match the consumer target')
        extra = source.module_sources
        if type(extra) is not tuple or len(extra) >= 256:
            raise RustError('external source closure must be a tuple of at most 256 files')
        files, total = {}, 0
        for row in ((source.root_path, source.source, source.source_sha256), *extra):
            if type(row) is not tuple or len(row) != 3:
                raise RustError('external source file needs a path, original bytes and expected SHA-256')
            path, raw, expected = row
            _logical_path(path)
            if path in files:
                raise RustError(f'duplicate external source path {path!r}')
            if type(raw) is not bytes or len(raw) > 1024 * 1024:
                raise RustError('an external source file requires at most 1 MiB of original bytes')
            total += len(raw)
            if total > 8 * 1024 * 1024:
                raise RustError('external source closure exceeds 8 MiB of original bytes')
            if type(expected) is not str or sha256(raw).hexdigest() != expected:
                raise RustError(f'external source SHA-256 differs from its expected pin: {path}')
            try:
                files[path] = raw.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise RustError(f'external source bytes are not UTF-8: {path}') from exc
        frozen.append((alias, source, files))
    return sorted(frozen, key=lambda row: row[0])


def link_context(target, providers):
    # A consumer built against a different dependency or alias must not export
    # the same symbol simply because its own source file remained unchanged.
    edges = []
    for alias, provider in sorted(providers.items()):
        identity = provider.resolution.root.crate
        edges.append((alias, identity.name, identity.root_path, identity.edition,
                      identity.source_closure_sha256, identity.link_context_sha256))
    key = json.dumps(('direct-scalar-extern-v1', target.triple, edges), separators=(',', ':'))
    return sha256(key.encode()).hexdigest()


@dataclass(frozen=True)
class CompiledSourceLibrary:
    resolution: object
    compilation: object

    def root_declaration(self):
        owner = self.resolution.root
        return ItemDeclaration(ItemId(owner, 'type', owner.crate.name), True, self)

    def member(self, parts, namespace, requester, token, *, optional, module=None):
        # Begin at the actual defining module (already visible through its
        # import path), while checking every next binding from the original
        # foreign requester. A public re-export may name a module below a
        # private ancestor; re-walking its textual canonical path is wrong.
        declaration = self.resolution.resolve('::'.join(parts), namespace, requester, token,
                                              _optional_final=optional,
                                              _start_module=self.resolution.root if module is None else module)
        if declaration is None:
            return None
        if namespace == 'type' and isinstance(declaration.syntax, Module):
            return declaration
        fn = declaration.syntax
        if (not isinstance(fn, Function) or fn.implementation is not None or fn.body is None
                or fn.type_parameters or fn.lifetime_parameters or fn.abi != 'Rust'
                or fn.result not in _SCALARS or any(ty not in _SCALARS for _, ty, _ in fn.params)):
            raise token.error('only public monomorphic scalar Rust functions can be imported from source crates')
        return declaration
