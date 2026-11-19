"""Direct, source-built Rust library dependencies, not sysroot/Cargo loading.

Each leaf is independently parsed, checked and lowered. Only already-checked,
monomorphic scalar Rust function signatures may cross this boundary. No AST
from a foreign body is added to the consumer, and no caller-supplied declaration,
object, metadata or checking token can attest a provider.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .crate_context import ItemDeclaration, ItemId, _logical_path
from .editions import require_edition
from .syntax import Function, RustError, _KEYWORDS
from .targets import resolve_target
from .types import INTEGER_TYPES


@dataclass(frozen=True)
class RustSourceDependency:
    """Exact UTF-8 bytes of one leaf crate, plus the caller's expected SHA-256."""
    crate_name: str
    root_path: str
    edition: int
    target_triple: str
    source: bytes
    source_sha256: str


_SYSTEM_NAMES = frozenset({'core', 'alloc', 'std'})
_SCALARS = INTEGER_TYPES | {'bool', '()'}


def _name(value):
    return (type(value) is str and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value)
            and value not in _KEYWORDS and value not in {'_', 'gen'})


def checked_inputs(externs, target, edition):
    from .module_resolution import _LANGUAGE_PRIMITIVE_USE_TARGETS

    if type(externs) is not dict or not 1 <= len(externs) <= 8:
        raise RustError('external source map requires 1 to 8 named direct dependencies')
    if edition < 2018:
        raise RustError('the supplied extern prelude requires Rust 2018 or later; 2015 extern crate is not implemented')
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
        raw = source.source
        if type(raw) is not bytes or len(raw) > 1024 * 1024:
            raise RustError('an external source leaf requires at most 1 MiB of original bytes')
        if type(source.source_sha256) is not str or sha256(raw).hexdigest() != source.source_sha256:
            raise RustError('external source SHA-256 differs from its expected pin')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise RustError('external source bytes are not UTF-8') from exc
        frozen.append((alias, source, text))
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

    def member(self, parts, namespace, requester, token, *, optional):
        if len(parts) != 1:
            raise token.error('only direct root function paths into a source dependency are implemented')
        # Absolute within the actual provider; access is checked from the
        # original, foreign requester, NOT from inside the providing crate.
        declaration = self.resolution.resolve('crate::' + parts[0], namespace, requester, token,
                                              _optional_final=optional)
        if declaration is None:
            return None
        fn = declaration.syntax
        if (not isinstance(fn, Function) or fn.implementation is not None or fn.body is None
                or fn.type_parameters or fn.lifetime_parameters or fn.abi != 'Rust'
                or fn.result not in _SCALARS or any(ty not in _SCALARS for _, ty, _ in fn.params)):
            raise token.error('only public monomorphic scalar Rust functions can be imported from source crates')
        return declaration
