"""Owned `missing_docs` lint: rustc's MissingDoc pass for a crate's lint level.

https://doc.rust-lang.org/rustc/lints/listing/allowed-by-default.html#missing-docs
Crate and module inner lint attributes apply where the item is defined.
Nested scopes inherit their parent's level. `deny` inside `forbid` has no
effect; attempting to lower it to `warn` or `allow` is rustc E0453.
Only `deny` and `forbid` reject. `--test` configures which items exist; it
does not exempt the resulting public declarations from missing_docs.

Checked, as rustc does (verdicts in tests/rust/test_rust_owned_lints.py):
the crate root, and every exported item: one reachable from the crate root
through `pub` modules or named by a `pub use` re-export, `pub(crate)` and
narrower excluded. Exported items need documentation, and so do their named
`pub` struct fields (positional ones never), enum variants and struct-variant
fields, trait items, and `pub` inherent-impl items of an exported type. Trait
impl items, `use` and impl blocks are never checked. Any doc comment (even
empty), `#[doc = "..."]` or `#[doc(hidden)]` is documentation; a
`#[doc(hidden)]` item suppresses inherited checking; a lexically nested
module can set a stricter level again. A same-scope hidden/strict conflict
whose hard verdict differs needs the original source bound to rustc. Item-level lint attributes are
still rejected; no lint group, cap, Clippy or rustdoc execution is inferred.
"""
from __future__ import annotations

from .syntax import (
    ConstItem, Enumeration, ExternalCrateAlias, Module, Record, RustError, StaticItem, Trait,
)

LEVELS = ('allow', 'warn', 'deny', 'forbid')


def missing_docs_level(attributes, inherited=('allow', None)):
    """The effective level and the original attribute that owns it."""
    level, setter = inherited
    for attribute in attributes:
        if not any(item.name == 'missing_docs' for item in attribute.builtin_lints):
            continue
        if level == 'forbid':
            if attribute.level in ('deny', 'forbid'):
                continue  # do not remove the inherited prohibition
            raise attribute.token.error(f"{attribute.level}(missing_docs) incompatible with previous "
                                        "forbid (rustc E0453)")
        level, setter = attribute.level, attribute
    return level, setter


def has_doc(documentation):
    """rustc's has_doc: a doc comment, `#[doc = "..."]` or `#[doc(hidden)]`."""
    for entry in documentation:
        if entry.syntax in ('line', 'block') or entry.text is not None:
            return True
        if any(option.name == 'hidden' for option in entry.options):
            return True
    return False


def hidden(documentation):
    return any(option.name == 'hidden' for entry in documentation for option in entry.options)


def describe(syntax, kind=None):
    if kind is not None:
        return kind
    if isinstance(syntax, Record):
        return 'a struct'
    if isinstance(syntax, Enumeration):
        return 'an enum'
    if isinstance(syntax, Trait):
        return 'a trait'
    if isinstance(syntax, Module):
        return 'a module'
    if isinstance(syntax, ConstItem):
        return 'a constant'
    if isinstance(syntax, StaticItem):
        return 'a static'
    return 'a function'


def function_kind(function):
    receiver = function.params and function.params[0][0].text == 'self'
    return 'a method' if receiver else 'an associated function'


def scoped_module_lints(crate):
    """Stable lexical module, source and original attributes; not an admission token."""
    result = []
    pending = list(reversed(crate.root.children))
    while pending:
        node = pending.pop()
        if node.body.inner_lints:
            result.append((node.identity.components, node.source.path, node.body.inner_lints))
        pending.extend(reversed(node.children))
    return tuple(result)


def check_missing_docs(crate, attributes, *, test=False, namespaces=None):
    """Validate forbidden overrides in all live modules, then lint exports at definition."""
    root = crate.root.identity
    levels = {}
    pending = [(crate.root, (('allow', None, root),))]
    while pending:
        module, before = pending.pop()
        local = attributes if module.identity == root else module.body.inner_lints
        doc_hidden = hidden(module.outer_documentation) or hidden(module.body.documentation)
        reset = ('allow', None, module.identity)
        # A hidden declaration suppresses inherited missing_docs (even an
        # inherited forbid). At that SAME site two real compilers order its
        # suppression differently relative to an explicit inner lint. Keep
        # both possibilities, not a guessed compiler version from target cfg.
        inner_hidden = tuple(entry.source_span[0] for entry in module.body.documentation
                             if hidden((entry,)))
        # Outer attributes precede the module body even when it comes from
        # another file. A hidden inner attribute has a source position inside
        # the same body as local lint attributes.
        positions = ((attribute.source_span[0], attribute) for attribute in local)
        events = sorted((*positions, *((offset, None) for offset in inner_hidden)),
                        key=lambda entry: entry[0])
        options, failures = [], []
        for prior in before:
            state = reset if hidden(module.outer_documentation) else prior
            try:
                for _, attribute in events:
                    if attribute is None:
                        state = reset
                    else:
                        level, setter, origin = state
                        new_level, new_setter = missing_docs_level((attribute,), (level, setter))
                        state = (new_level, new_setter, origin if setter is new_setter else module.identity)
            except RustError as exc:
                failures.append(exc)
            else:
                if state not in options:
                    options.append(state)
        if failures:
            if options:
                raise RustError('missing_docs and doc(hidden) override order requires a same-source rustc check')
            raise failures[0]
        if doc_hidden and reset not in options:
            options.append(reset)
        levels[module.identity] = tuple(options)
        pending.extend((child, tuple(options)) for child in reversed(module.children))
    # Live items were already determined by cfg; real --test does not cap lints.
    if not any(level in ('deny', 'forbid') for choices in levels.values() for level, _, _ in choices):
        return
    if namespaces is None:
        from .module_resolution import ModuleNamespaces
        namespaces = ModuleNamespaces(crate)

    def missing(token, what, module):
        choices = levels[module]
        strict = [choice for choice in choices if choice[0] in ('deny', 'forbid')]
        if not strict:
            return
        if len(strict) != len(choices):
            raise token.error('missing_docs and doc(hidden) override order requires a same-source rustc check')
        level, _, origin = strict[0]
        owner = 'at the crate root' if origin == root else 'in module ' + '::'.join(origin.components)
        raise token.error(f"missing documentation for {what} (missing_docs, {level} {owner})")

    root_setter = next((s for _, s, _ in levels[root] if s is not None), None)
    if not has_doc(crate.root.body.documentation) and root_setter is not None:
        missing(root_setter.token, 'the crate', root)

    exported, seen = {}, set()

    def consider(declaration):
        # A local use (even a public re-export) does not move its definition
        # into this crate. The provider has already checked its own levels.
        if (declaration.identity.module.crate != root.crate or
                isinstance(declaration.syntax, ExternalCrateAlias)):
            return
        syntax = declaration.syntax
        if id(syntax) in seen:
            return
        seen.add(id(syntax))
        if isinstance(syntax, Module):
            child = namespaces.modules.get(declaration.identity.module.child(declaration.identity.name))
            documentation = syntax.documentation + (child.body.documentation if child is not None else ())
            if not has_doc(documentation):
                # An inner attribute applies to the module declaration itself too.
                owner = child.identity if child is not None else declaration.identity.module
                missing(syntax.token, 'a module', owner)
            if child is not None:
                visit(child.identity)
            return
        documentation = getattr(syntax, 'documentation', ())
        is_hidden = hidden(documentation)
        exported[id(syntax)] = is_hidden
        if is_hidden:
            return
        scope = declaration.identity.module
        if not has_doc(documentation):
            missing(syntax.token, describe(syntax), scope)
        if isinstance(syntax, Record) and not syntax.tuple_fields:
            for (field, _), public, docs in zip(syntax.fields, syntax.field_public, syntax.field_documentation):
                if public is True and not has_doc(docs):
                    missing(field, 'a struct field', scope)
        if isinstance(syntax, Enumeration):
            for (variant, _), docs in zip(syntax.variants, syntax.variant_documentation):
                if hidden(docs):
                    continue
                if not has_doc(docs):
                    missing(variant, 'a variant', scope)
                for field, field_docs in zip(syntax.named_payloads.get(variant.text, ()),
                                             syntax.payload_documentation.get(variant.text, ())):
                    if not has_doc(field_docs):
                        missing(field, 'a struct field', scope)
        if isinstance(syntax, Trait):
            for function in syntax.methods:
                if not hidden(function.documentation) and not has_doc(function.documentation):
                    missing(function.token, function_kind(function), scope)
            for associated in syntax.associated_types:
                if not hidden(associated.documentation) and not has_doc(associated.documentation):
                    missing(associated.token, 'an associated type', scope)

    def visit(module_id):
        module = namespaces.modules[module_id]
        for declaration in module.items.values():
            if declaration.public is True:
                consider(declaration)
        for name, use in namespaces._uses.get(module_id, {}).items():
            if use.public is True:
                for declaration in namespaces._bind_use(module_id, name).values():
                    consider(declaration)

        for declaration in namespaces.globs.public_declarations(module_id):
            consider(declaration)

    visit(crate.root.identity)

    # `pub` inherent-impl items of an exported, unhidden type.
    for module_id, module in namespaces.modules.items():
        for function in module.body.functions:
            implementation = function.implementation
            if implementation is None or implementation.trait is not None or function.public is not True \
                    or hidden(implementation.documentation):
                continue
            owner = implementation.owner
            name = owner.name if hasattr(owner, 'name') else owner
            if name in namespaces.by_key:
                declaration = namespaces.by_key[name]  # already resolved by ModuleResolution
            else:
                try:
                    declaration = namespaces.resolve(name, 'type', module_id, implementation.token)
                except RustError:
                    continue  # an unresolved owner is reported by semantic checking
            if exported.get(id(declaration.syntax), True) is False and not hidden(function.documentation) \
                    and not has_doc(function.documentation):
                missing(function.token, function_kind(function), module_id)
