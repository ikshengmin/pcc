"""Owned cfg_attr evaluation in the authenticated no-explicit-cfg context.

Rust Reference conditional-compilation.html, retrieved 2026-11-03. Cargo
features are outside-source --cfg inputs. The existing checker admits no such
flags, so its explicit feature set is empty. Built-in/target cfg and additional
custom configurations require a later, separately authenticated context.
"""
from dataclasses import dataclass

from .attribute_metadata import meta_list, offset


@dataclass(frozen=True)
class RustConfiguration:
    source_sha256: str
    edition: int
    target_triple: str
    explicit_cfg: tuple[str, ...]

    def predicate(self, item):
        if self.explicit_cfg != ():
            raise item.token.error('nonempty Rust cfg inputs are not implemented by the authenticated adapter')
        if item.form == 'word' and item.name in {'true', 'false'}:
            return item.name == 'true'
        if item.name == 'feature' and item.form == 'value':
            # No --cfg feature entries occur in this exact checked command.
            return False
        if item.form == 'list' and item.name in {'all', 'any', 'not'}:
            if item.name == 'not' and len(item.children) != 1:
                raise item.token.error('cfg not requires exactly one predicate')
            # Validate every child, including an otherwise short-circuited
            # unsupported target predicate. Never guess target cfg from a host.
            values = tuple(self.predicate(child) for child in item.children)
            if item.name == 'not':
                return not values[0]
            return all(values) if item.name == 'all' else any(values)
        raise item.token.error('cfg predicate requires an unimplemented built-in/target or custom configuration context')


@dataclass(frozen=True)
class ConditionalCrateAttribute:
    token: object
    predicate: object
    attributes: tuple
    source_span: tuple[int, int]


@dataclass(frozen=True)
class ConfiguredCrateAttribute:
    syntax: ConditionalCrateAttribute
    configuration: RustConfiguration
    enabled: bool


def crate_cfg_attribute(parser, token, start):
    items = meta_list(parser, cfg_literals=True)
    if len(items) < 2:
        raise token.error('cfg_attr requires a predicate and at least one attribute in this implementation')
    end = parser.expect(']')
    return ConditionalCrateAttribute(token, items[0], items[1:],
                                     (offset(parser, start), offset(parser, end) + 1))


def apply_crate_cfg(attribute, configuration, policy):
    enabled = configuration.predicate(attribute.predicate)
    if enabled:
        for item in attribute.attributes:
            if item.name == 'no_std' and item.form == 'word':
                policy = policy.with_no_std(item.token)
            elif item.name == 'cfg_attr' and item.form == 'list' and len(item.children) >= 2:
                nested = ConditionalCrateAttribute(item.token, item.children[0], item.children[1:], item.source_span)
                policy, _result = apply_crate_cfg(nested, configuration, policy)
            else:
                raise item.token.error('active cfg_attr payload has unimplemented crate attribute semantics')
    # A false predicate retains all original payload metadata but applies none
    # of it, as Rust requires. It never authorizes an unknown active attribute.
    return policy, ConfiguredCrateAttribute(attribute, configuration, enabled)
