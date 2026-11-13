"""Canonical lang-item collection for non-lowering compiler-context inspection.

This phase reuses the ordinary parser and closed module loader. It records
compiler feature requests as unresolved obligations; neither a caller flag nor
an item named Sized/Copy supplies builtin semantics. Normal Parser/compile_rust
entrypoints still reject these internal attributes. No IR, Checker, external
compiler, prelude synthesis or primitive trait implementation occurs here.

Pinned semantics: rust-lang/rust 1159e78c4747b02ef996e55082b704c09b970588,
compiler/rustc_passes/src/lang_items.rs and compiler/rustc_hir/src/lang_items.rs.
The first four marker entries all require Target::Trait / Exact(0). Other lang
items and builtin marker semantics remain explicit dependencies; declared
supertraits are checked by the shared hierarchy consumer.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType

from .attribute_metadata import offset
from .crate_context import _parse_crate_sources
from .syntax import Parser, RustError, Trait


MARKER_LANG_ITEMS = frozenset({'sized', 'meta_sized', 'pointee_sized', 'copy'})
_CONTEXT_FEATURES = frozenset({'no_core', 'lang_items', 'staged_api',
                               'rustdoc_internals', 'doc_cfg_hide'})


@dataclass(frozen=True)
class LanguageItemAttribute:
    token: object
    name: str
    source_span: tuple[int, int]


@dataclass(frozen=True)
class ContextObligation:
    token: object
    feature: str
    declared: bool
    description: str


@dataclass(frozen=True)
class LanguageItemBinding:
    attribute: LanguageItemAttribute
    identity: object  # Existing ItemId, not a spelling-based marker or new namespace.


@dataclass(frozen=True)
class LanguageItemContext:
    crate: object
    language_items: object
    feature_requests: tuple
    obligations: tuple[ContextObligation, ...]
    trait_hierarchy: object

    def require(self, name, token):
        if name not in MARKER_LANG_ITEMS:
            raise token.error(f'language item {name!r} is not implemented in this context slice')
        binding = self.language_items.get(name)
        if binding is None:
            raise token.error(f'required canonical language item {name!r} is missing')
        return binding.identity

    def require_owned_semantics(self):
        self.crate.policy.require_owned_lang_item_context()
        if self.obligations:
            obligation = self.obligations[0]
            raise obligation.token.error('unresolved owned compiler context: ' + obligation.description)
        # Even a complete declaration table is not a proof of the Sized hierarchy,
        # Copy/Clone implementations, feature channel or staged API semantics.
        raise RustError('lang-item context inspection does not establish owned builtin semantics')


class _ContextSyntaxParser(Parser):
    """Only used by inspect_lang_item_context; never a lowering admission mode."""
    core_prelude = False
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.feature_requests = []
        self.context_nodes = []

    def parse(self, **kwargs):
        # Inline children share this list through the existing parser's copy of
        # source/token state; outlined roots get their own list in the loader.
        self.context_nodes.append(self)
        return super().parse(**kwargs)

    def validate_crate_policy(self):
        # Retain the unmet requirement, not a permission to enter normal Sema.
        if self.crate_policy.no_core is not None:
            self.attribute_requirements.append((self.crate_policy.no_core, 'no_core'))

    def validate_attribute_context(self):
        # The enclosing collector turns every retained requirement into a
        # located, unresolved ContextObligation on the canonical crate graph.
        # ModuleResolution rejects that graph before constructing any Checker.
        return

    def compiler_context_attribute(self, attribute, start):
        if attribute.text != 'feature':
            return False
        self.expect('(')
        if self.accept(')'):
            self.expect(']')
            return True
        while True:
            feature = self.name()
            if feature.text not in _CONTEXT_FEATURES:
                raise feature.error(f'compiler feature {feature.text!r} is not implemented in context inspection')
            if len(self.feature_requests) >= 256:
                raise feature.error('compiler feature request count exceeds implementation limit (256)')
            self.feature_requests.append(feature)
            if self.accept(')'):
                break
            self.expect(',')
            if self.accept(')'):
                break
        self.expect(']')
        return True

    def language_item_attribute(self, attribute):
        self.expect('=')
        literal = self.take()
        if literal.kind != 'string':
            raise literal.error('language item requires a literal string')
        if literal.literal_value not in MARKER_LANG_ITEMS:
            raise literal.error(f'language item {literal.literal_value!r} is not implemented in this context slice')
        self.attribute_requirements.append((attribute, 'lang_items'))
        return LanguageItemAttribute(attribute, literal.literal_value,
                                     (offset(self, attribute), literal.source_end))


def inspect_lang_item_context(crate_root, sources, *, crate_name, edition):
    """Collect source-defined identities/obligations, never certify or lower.

    Inputs are the same closed, UTF-8, bounded module source map as the ordinary
    loader. The returned crate is deliberately rejected by ModuleResolution
    while compiler-context obligations remain; no token or activation flag is
    accepted. Installed stable rustc cannot supply a nightly core-context proof.
    """
    crate, parsers = _parse_crate_sources(crate_root, sources, crate_name=crate_name,
                                        edition=edition, parser_type=_ContextSyntaxParser)
    root_parser = parsers[0]
    requests = tuple(root_parser.feature_requests)
    declared = {token.text for token in requests}
    obligations = []
    seen_requirements = set()
    for parser in parsers:
        for node in parser.context_nodes:
            for token, feature in node.attribute_requirements:
                key = (token.source_path, token.line, token.column, feature)
                if key in seen_requirements:
                    continue
                seen_requirements.add(key)
                obligations.append(ContextObligation(token, feature, feature in declared,
                    f'{feature} is source-declared but its owned semantics are not implemented' if feature in declared
                    else f'{feature} requires a source feature declaration and owned compiler semantics'))
    for token in requests:
        obligations.append(ContextObligation(token, token.text, True,
            f'feature({token.text}) requests compiler-internal behavior; no owned feature activation is implemented'))

    # The same scoped name/privacy resolver used by production module Sema,
    # without constructing executable inputs or bypassing its admission guard.
    from .module_resolution import ModuleNamespaces
    from .trait_hierarchy import TraitHierarchy
    namespaces = ModuleNamespaces(crate)
    declarations = {declaration.identity: declaration.syntax
                    for module in namespaces.modules.values()
                    for declaration in module.items.values()
                    if isinstance(declaration.syntax, Trait)}
    def parent(identity, path, token):
        declaration = namespaces.resolve(path, 'type', identity.module, token)
        if not isinstance(declaration.syntax, Trait):
            raise token.error('supertrait path does not name a declared trait')
        return declaration.identity
    hierarchy = TraitHierarchy(declarations, parent)
    bindings = {}
    pending = [crate.root]
    while pending:
        module = pending.pop()
        pending.extend(reversed(module.children))
        for declaration in module.items.values():
            item = declaration.syntax
            attribute = getattr(item, 'language_item', None)
            if attribute is None:
                continue
            if not isinstance(item, Trait):
                raise attribute.token.error('language item requires a trait target')
            if item.type_parameters:
                raise attribute.token.error('language item trait requires exactly zero generic parameters')
            existing = bindings.get(attribute.name)
            if existing is not None and existing.identity != declaration.identity:
                first = existing.identity
                raise attribute.token.error(f'duplicate language item {attribute.name!r}; first declaration is '
                    + '::'.join((*first.module.components, first.name)))
            bindings[attribute.name] = LanguageItemBinding(attribute, declaration.identity)
    obligations = tuple(obligations)
    crate = replace(crate, compiler_context_obligations=obligations)
    return LanguageItemContext(crate, MappingProxyType(bindings), requests, obligations, hierarchy)
