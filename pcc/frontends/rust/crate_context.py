"""Owned source/module identities needed before staged APIs and lang items.

This is the parser's crate-loading phase, not another compiler or a capability
waiver. It consumes a closed supplied source map, keeps module boundaries, and
has no subprocess/filesystem lookup. Name resolution, stability inheritance,
feature activation and lang-item checking remain separate unimplemented steps;
no object/IR API accepts this graph as permission to bypass those checks.

Rules: Rust Reference items.mod.outlined (including foo.rs vs foo/mod.rs),
items.mod.namespace and names.scopes. Source paths are POSIX logical paths.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from types import MappingProxyType

from .syntax import ModuleBody, Parser, RustError


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str
    sha256: str


@dataclass(frozen=True)
class CrateId:
    name: str
    root_path: str
    edition: int
    source_closure_sha256: str


@dataclass(frozen=True)
class ModuleId:
    # Empty components is the crate root. Identity is not the file's basename:
    # two module declarations may legally load one source into distinct scopes.
    crate: CrateId
    components: tuple[str, ...]

    def child(self, name):
        return ModuleId(self.crate, self.components + (name,))


@dataclass(frozen=True)
class ItemId:
    module: ModuleId
    namespace: str
    name: str

    @property
    def binding_key(self):
        # Opaque storage key AFTER scoped resolution. Never a source-level
        # lookup namespace, and impossible to spell as a Rust identifier.
        import json
        crate = self.module.crate
        value = [crate.name, crate.root_path, crate.edition,
                 crate.source_closure_sha256, list(self.module.components),
                 self.namespace, self.name]
        return '__pcc_scope$' + json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode().hex()



@dataclass(frozen=True)
class ItemDeclaration:
    identity: ItemId
    public: bool
    syntax: object


@dataclass(frozen=True)
class LoadedModule:
    identity: ModuleId
    source: SourceFile
    body: ModuleBody
    public: bool
    outer_documentation: tuple
    items: object  # Read-only mapping (namespace, name) -> canonical ItemDeclaration.
    children: tuple


@dataclass(frozen=True)
class CrateSyntax:
    root: LoadedModule
    edition: int
    sources: object
    # Edition is retained as project context, NOT an edition acceptance claim.
    semantic_context_complete: bool = False
    compiler_context_obligations: tuple = ()

    @property
    def policy(self):
        return self.root.body.crate_policy


def _logical_path(path):
    if not isinstance(path, str) or not path or '\\' in path or '\x00' in path:
        raise RustError('invalid logical Rust source path')
    try:
        path.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise RustError('Rust source paths must be valid UTF-8 text') from exc
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in {'.', '..'} for part in path.split('/')):
        raise RustError('Rust source-map keys must be normalized relative POSIX paths')
    if str(p) != path:
        raise RustError('Rust source-map keys must be normalized relative POSIX paths')
    return path


def _join_path(directory, relative, token):
    if not relative or '\\' in relative or '\x00' in relative or PurePosixPath(relative).is_absolute():
        raise token.error('module path must be a relative POSIX source-map path')
    parts = list(directory.parts) if str(directory) != '.' else []
    for part in relative.split('/'):
        if part in {'', '.'}:
            continue
        if part == '..':
            if not parts:
                raise token.error('module path escapes the supplied source-map root')
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise token.error('module path does not name a source file')
    return '/'.join(parts)


class _Loader:
    def __init__(self, sources, parser_type=Parser, configuration=None, environment=None):
        self.sources = sources
        self.parser_type = parser_type
        self.configuration = configuration
        self.environment = environment
        self.context_parsers = []
        self.active_files = []
        self.module_count = 0

    def parse_file(self, path, identity, directory, declaration=None):
        if path in self.active_files:
            raise RustError(f'{path}: circular outlined module inclusion')
        source = self.sources.get(path)
        if source is None:
            raise RustError(f'{path}: Rust module source is missing from the supplied closure')
        self.active_files.append(path)
        try:
            options = {} if self.configuration is None else {'configuration': self.configuration}
            if self.environment is not None:
                options['environment'] = self.environment
            parser = self.parser_type(source.text, source_path=path, edition=identity.crate.edition, **options)
            self.context_parsers.append(parser)
            functions = parser.parse(_module=bool(identity.components))
            body = ModuleBody(functions, parser.records, parser.enums,
                              parser.implementations, parser.traits, parser.modules,
                              parser.crate_documentation, parser.crate_aliases, parser.crate_policy,
                              parser.conditional_items, uses=parser.uses, consts=parser.consts)
            return self.load_body(source, body, identity, directory, declaration,
                                  inline=False)
        except RustError as exc:
            if str(exc).startswith(path + ':'):
                raise
            raise RustError(f'{path}: {exc}') from exc
        finally:
            self.active_files.pop()

    def load_body(self, source, body, identity, directory, declaration, *, inline):
        self.module_count += 1
        if self.module_count > 256 or len(identity.components) > 32:
            raise RustError('Rust module graph exceeds implementation limits (256 modules /32 levels)')
        items = {}
        def register(namespace, item):
            token = item.token
            key = (namespace, token.text)
            if key in items:
                raise token.error(f'duplicate {namespace}-namespace item {token.text!r} in module')
            items[key] = ItemDeclaration(ItemId(identity, namespace, token.text), item.public, item)
        for item in body.records + body.enums + body.traits + body.modules + body.crate_aliases:
            register('type', item)
        for function in body.functions:
            if function.implementation is None:
                register('value', function)
        for const in body.consts:
            register('value', const)
        children = []
        for module in body.modules:
            child_id = identity.child(module.token.text)
            if module.body is not None:
                children.append(self.load_body(source, module.body, child_id,
                    directory / module.token.text, module, inline=True))
                continue
            if module.path is not None:
                if inline:
                    raise module.token.error('path attributes on modules inside inline modules require additional directory ownership rules; not implemented')
                path = _join_path(PurePosixPath(source.path).parent, module.path, module.token)
                # With #[path], rustc's directory ownership is the parent of
                # the selected file; no fictitious basename directory is added.
                child_directory = PurePosixPath(path).parent
            else:
                candidates = [str(directory / (module.token.text + '.rs')),
                              str(directory / module.token.text / 'mod.rs')]
                present = [p for p in candidates if p in self.sources]
                if len(present) > 1:
                    raise module.token.error('module source is ambiguous: both name.rs and name/mod.rs are present')
                if not present:
                    raise module.token.error('module source missing from supplied closure: ' + ' or '.join(candidates))
                path = present[0]
                child_directory = directory / module.token.text
            children.append(self.parse_file(path, child_id, child_directory, module))
        return LoadedModule(identity, source, body,
                            True if declaration is None else declaration.public,
                            () if declaration is None else declaration.documentation,
                            MappingProxyType(items), tuple(children))


def parse_crate_sources(crate_root, sources, *, crate_name, edition, configuration=None, environment=None):
    """Parse a supplied original multi-file crate without flattening its scopes.

    No Cargo build, external crates, macros, prelude synthesis or lang-item
    registration occur here. cfg selection uses only an explicit
    OwnedRustConfiguration (shared by every file, so a disabled `mod` never
    loads its file); without one, cfg stays an explicit boundary.
    A `no_core`/staged context still fails in the same existing Parser path.
    The complete original source map is frozen before any file is parsed.
    """
    return _parse_crate_sources(crate_root, sources, crate_name=crate_name,
                                edition=edition, parser_type=Parser,
                                configuration=configuration, environment=environment)[0]


def _parse_crate_sources(crate_root, sources, *, crate_name, edition, parser_type, configuration=None,
                         environment=None):
    root = _logical_path(crate_root)
    if (not isinstance(crate_name, str) or not crate_name.isascii()
            or not crate_name.isidentifier() or crate_name == "_"):
        raise RustError("Rust crate context requires an explicit ASCII crate name")
    if type(edition) is not int or edition not in {2015, 2018, 2021, 2024}:
        raise RustError('Rust crate context requires an explicit recognized edition')
    if not hasattr(sources, 'items') or len(sources) > 256:
        raise RustError('Rust crate source map must contain at most256 files')
    frozen = {}
    total = 0
    for path, text in sources.items():
        path = _logical_path(path)
        if not isinstance(text, str):
            raise RustError('Rust crate sources must be decoded UTF-8 text')
        try:
            raw = text.encode('utf-8')
        except UnicodeEncodeError as exc:
            raise RustError('Rust crate sources must be valid UTF-8 text') from exc
        total += len(raw)
        if len(raw) > 1024 * 1024 or total > 8 * 1024 * 1024:
            raise RustError('Rust source closure exceeds implementation limits (1MiB file /8MiB total)')
        frozen[path] = SourceFile(path, text, sha256(raw).hexdigest())
    frozen = MappingProxyType(frozen)
    digest = sha256()
    for path, source in sorted(frozen.items()):
        encoded = path.encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        digest.update(bytes.fromhex(source.sha256))
    identity = CrateId(crate_name, root, edition, digest.hexdigest())
    loader = _Loader(frozen, parser_type, configuration, environment)
    graph = loader.parse_file(root, ModuleId(identity, ()), PurePosixPath(root).parent)
    return CrateSyntax(graph, edition, frozen), loader.context_parsers
