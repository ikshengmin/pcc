"""Owned, structured attribute metadata; not a substitute for core context.

Semantic reference: rust-lang/rust 1159e78c4747b02ef996e55082b704c09b970588,
compiler/rustc_attr_parsing/src/attributes/{stability,util}.rs,
compiler/rustc_passes/src/check_attr.rs and rustc_ast_passes/src/feature_gate.rs.
Stability and gated rustdoc declarations remain compilation-context obligations.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MetaItem:
    token: object
    name: str
    form: str
    value: str | None
    children: tuple
    source_span: tuple[int, int]


@dataclass(frozen=True)
class CrateLintAttribute:
    """Original syntax, not an owned implementation of the named lint."""
    token: object
    level: str
    lints: tuple[MetaItem, ...]
    source_span: tuple[int, int]


def crate_lint_attribute(parser, token, start):
    values = meta_list(parser)
    if not values:
        raise token.error('crate lint transition requires missing_docs; empty lint lists are not implemented')
    for item in values:
        if item.name != 'missing_docs' or item.form != 'word':
            raise item.token.error('crate lint transition implements only missing_docs word metadata; other lints, groups and reasons are not implemented')
    end = parser.expect(']')
    return CrateLintAttribute(token, token.text, values,
                              (offset(parser, start), offset(parser, end) + 1))


@dataclass(frozen=True)
class StableMetadata:
    token: object
    feature: str
    since: str
    version: tuple[int, int, int] | None
    source_span: tuple[int, int]


def offset(parser, token):
    return parser.line_offsets[token.line - 1] + token.column - 1


def meta_list(parser, depth=0):
    if depth >= 32:
        raise parser.token.error('attribute metadata nesting exceeds implementation limit (32)')
    parser.expect('(')
    values = []
    while not parser.accept(')'):
        token = parser.name()
        form, value, children = 'word', None, ()
        if parser.accept('='):
            literal = parser.take()
            if literal.kind != 'string':
                raise literal.error('attribute metadata requires a literal string; expression/macro expansion is not implemented')
            form, value = 'value', literal.literal_value
        elif parser.token.text == '(':
            form, children = 'list', meta_list(parser, depth + 1)
        end = parser.tokens[parser.pos - 1]
        end_offset = end.source_end if end.source_end is not None else offset(parser, end) + len(end.text)
        values.append(MetaItem(token, token.text, form, value, children, (offset(parser, token), end_offset)))
        if parser.accept(')'):
            break
        parser.expect(',')
    return tuple(values)


def stable_metadata(parser, token, start):
    values = meta_list(parser)
    fields = {}
    for item in values:
        if item.name not in {'feature', 'since'}:
            raise item.token.error('stable accepts only feature and since metadata')
        if item.name in fields:
            raise item.token.error(f'duplicate stable metadata key {item.name!r}')
        if item.form != 'value':
            raise item.token.error('stable feature and since require literal strings')
        fields[item.name] = item.value
    if set(fields) != {'feature', 'since'}:
        raise token.error('stable requires both feature and since metadata')
    if not fields['feature'].isidentifier():
        raise token.error('stable feature must be an identifier')
    since = fields['since']
    version = None
    if since != 'CURRENT_RUSTC_VERSION':
        # Pinned rustc accepts major.minor (implicit zero patch), optional plus
        # signs in unsigned parsing, and u16 components. No channel suffix.
        parts = since.split('.')
        if len(parts) not in {2, 3} or any(not re.fullmatch(r'\+?[0-9]+', p) for p in parts):
            raise token.error('stable since must be a Rust version or CURRENT_RUSTC_VERSION')
        normalized = [p.lstrip('+').lstrip('0') or '0' for p in parts]
        if any(len(p) > 5 or int(p) > 65535 for p in normalized):
            raise token.error('stable version components must fit u16')
        version = tuple(int(p) for p in normalized)
        if len(version) == 2:
            version += (0,)
    end = parser.expect(']')
    return StableMetadata(token, fields['feature'], since, version, (offset(parser, start), offset(parser, end) + 1))


def validate_doc_options(options, *, crate):
    """Return required compiler features; do not enable them or execute cfg.

Only the original core-root option families and plain hidden are admitted.
The retained test directives belong to a future doctest consumer, not this
program's lint/feature environment. No doctest execution is claimed.
"""
    requirements = []
    urls = {'html_favicon_url', 'html_logo_url', 'html_playground_url', 'issue_tracker_base_url', 'html_root_url'}
    for item in options:
        name = item.name
        if name in urls or name in {'html_no_source', 'rust_logo', 'cfg_hide'}:
            if not crate:
                raise item.token.error(f'doc({name}) requires crate placement; non-crate handling is not implemented')
        if name in urls:
            if item.form != 'value':
                raise item.token.error(f'doc({name}) requires a literal string')
        elif name in {'hidden', 'html_no_source', 'rust_logo'}:
            if item.form != 'word':
                raise item.token.error(f'doc({name}) requires a word option')
            if name == 'rust_logo':
                requirements.append((item.token, 'rustdoc_internals'))
        elif name == 'cfg_hide':
            if item.form != 'list' or any(c.form not in {'word', 'value'} for c in item.children):
                raise item.token.error('doc(cfg_hide) requires a list of cfg metadata')
            requirements.append((item.token, 'doc_cfg_hide'))
        elif name == 'test':
            if item.form != 'list':
                raise item.token.error('doc(test) requires a list')
            for directive in item.children:
                if directive.name == 'no_crate_inject' and directive.form == 'word':
                    if not crate:
                        raise directive.token.error('doc(test(no_crate_inject)) requires crate placement')
                elif directive.name == 'attr' and directive.form == 'list':
                    for attr in directive.children:
                        if attr.name not in {'allow', 'warn', 'deny', 'forbid'} or attr.form != 'list' or any(c.form != 'word' for c in attr.children):
                            raise attr.token.error('this doctest attribute requires an unimplemented doctest semantic context')
                else:
                    raise directive.token.error('unsupported doc(test) directive')
        else:
            raise item.token.error(f'unsupported doc option {name!r}; no documentation metadata is silently discarded')
    return requirements
