"""Owned `missing_docs` lint: rustc's MissingDoc pass for a crate's lint level.

https://doc.rust-lang.org/rustc/lints/listing/allowed-by-default.html#missing-docs
The level comes from the crate-root `#![allow|warn|deny|forbid(missing_docs)]`
attributes in order (a later one wins; overriding `forbid` is rustc E0453).
Only `deny` and `forbid` reject; a test build (`--test`) skips the lint.

Checked, as rustc does (verdicts in tests/rust/test_rust_owned_lints.py):
the crate root, and every exported item: one reachable from the crate root
through `pub` modules or named by a `pub use` re-export, `pub(crate)` and
narrower excluded. Exported items need documentation, and so do their named
`pub` struct fields (positional ones never), enum variants and struct-variant
fields, trait items, and `pub` inherent-impl items of an exported type. Trait
impl items, `use` and impl blocks are never checked. Any doc comment (even
empty), `#[doc = "..."]` or `#[doc(hidden)]` is documentation; a
`#[doc(hidden)]` item, or one defined inside a hidden module, item or impl
type, needs none, even when re-exported. Item-level lint attributes are
rejected by the parser as unimplemented, so the crate level is the only one.
"""
from __future__ import annotations

from .syntax import ConstItem, Enumeration, Module, Record, RustError, StaticItem, Trait

LEVELS = ('allow', 'warn', 'deny', 'forbid')


def missing_docs_level(attributes):
    """The crate's missing_docs level and the attribute that set it."""
    level, setter, forbidden = 'allow', None, None
    for attribute in attributes:
        if not any(item.name == 'missing_docs' for item in attribute.builtin_lints):
            continue
        if forbidden is not None and attribute.level != 'forbid':
            raise attribute.token.error(f"{attribute.level}(missing_docs) incompatible with previous "
                                        "forbid (rustc E0453)")
        level, setter = attribute.level, attribute
        if level == 'forbid':
            forbidden = attribute
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


def check_missing_docs(crate, attributes, *, test=False):
    """Reject the first exported item without documentation when the crate
    denies or forbids missing_docs."""
    level, setter = missing_docs_level(attributes)
    if level not in ('deny', 'forbid') or test:
        return
    from .module_resolution import ModuleNamespaces
    namespaces = ModuleNamespaces(crate)

    def missing(token, what):
        raise token.error(f"missing documentation for {what} (missing_docs, {level} at the crate root)")

    if not has_doc(crate.root.body.documentation):
        missing(setter.token, 'the crate')

    # Modules defined inside a hidden module, or hidden themselves.
    hidden_modules = {}
    pending = [(crate.root, False)]
    while pending:
        module, inherited = pending.pop()
        here = inherited or hidden(module.outer_documentation) or hidden(module.body.documentation)
        hidden_modules[module.identity] = here
        pending.extend((child, here) for child in module.children)

    exported, seen = {}, set()

    def consider(declaration):
        syntax = declaration.syntax
        if id(syntax) in seen:
            return
        seen.add(id(syntax))
        is_hidden = hidden_modules.get(declaration.identity.module, False)
        if isinstance(syntax, Module):
            child = namespaces.modules.get(declaration.identity.module.child(declaration.identity.name))
            documentation = syntax.documentation + (child.body.documentation if child is not None else ())
            is_hidden = is_hidden or hidden(documentation)
            if not is_hidden and not has_doc(documentation):
                missing(syntax.token, 'a module')
            if child is not None:
                visit(child.identity)
            return
        documentation = getattr(syntax, 'documentation', ())
        is_hidden = is_hidden or hidden(documentation)
        exported[id(syntax)] = is_hidden
        if is_hidden:
            return
        if not has_doc(documentation):
            missing(syntax.token, describe(syntax))
        if isinstance(syntax, Record) and not syntax.tuple_fields:
            for (field, _), public, docs in zip(syntax.fields, syntax.field_public, syntax.field_documentation):
                if public is True and not has_doc(docs):
                    missing(field, 'a struct field')
        if isinstance(syntax, Enumeration):
            for (variant, _), docs in zip(syntax.variants, syntax.variant_documentation):
                if hidden(docs):
                    continue
                if not has_doc(docs):
                    missing(variant, 'a variant')
                for field, field_docs in zip(syntax.named_payloads.get(variant.text, ()),
                                             syntax.payload_documentation.get(variant.text, ())):
                    if not has_doc(field_docs):
                        missing(field, 'a struct field')
        if isinstance(syntax, Trait):
            for function in syntax.methods:
                if not hidden(function.documentation) and not has_doc(function.documentation):
                    missing(function.token, function_kind(function))
            for associated in syntax.associated_types:
                if not hidden(associated.documentation) and not has_doc(associated.documentation):
                    missing(associated.token, 'an associated type')

    def visit(module_id):
        module = namespaces.modules[module_id]
        for declaration in module.items.values():
            if declaration.public is True:
                consider(declaration)
        for name, use in namespaces._uses.get(module_id, {}).items():
            if use.public is True:
                for declaration in namespaces._bind_use(module_id, name).values():
                    consider(declaration)

    visit(crate.root.identity)

    # `pub` inherent-impl items of an exported, unhidden type.
    for module_id, module in namespaces.modules.items():
        for function in module.body.functions:
            implementation = function.implementation
            if implementation is None or implementation.trait is not None or function.public is not True:
                continue
            owner = implementation.owner
            name = owner.name if hasattr(owner, 'name') else owner
            try:
                declaration = namespaces.resolve(name, 'type', module_id, implementation.token)
            except RustError:
                continue  # an unresolved owner is reported by semantic checking
            if exported.get(id(declaration.syntax), True) is False and not hidden(function.documentation) \
                    and not has_doc(function.documentation):
                missing(function.token, function_kind(function))
