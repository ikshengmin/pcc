"""Conditional compilation: authenticated and owned cfg configurations.

Rust Reference conditional-compilation.html, retrieved 2026-11-03. Cargo
features are outside-source --cfg inputs. Two configuration owners exist:

* `RustConfiguration`, bound to an actual CheckedRustSource command, whose
  explicit cfg set is empty; built-in/target predicates are gaps there.
* `OwnedRustConfiguration`, built by PCC from an explicit `CfgOptions`
  (rustc's --cfg, --test, -C debug-assertions and -C panic) and PCC's own
  record of each admitted target's built-in cfg set. No host tool is asked;
  an unrecorded target, an unset option-dependent name and an unstable cfg
  are explicit errors, never guesses.
"""
from dataclasses import dataclass

from .attribute_metadata import meta_list, offset
from .syntax import RustError


# Built-in cfg of each admitted target: rustc 1.92.0 `rustc --print cfg
# --target T` with default codegen options, minus the option-dependent
# `debug_assertions` and `panic` entries (CfgOptions supplies them).
_LINUX_X86_64 = (
    ('target_abi', ''), ('target_arch', 'x86_64'), ('target_endian', 'little'),
    ('target_family', 'unix'), ('target_feature', 'fxsr'), ('target_feature', 'sse'),
    ('target_feature', 'sse2'), ('target_has_atomic', '16'), ('target_has_atomic', '32'),
    ('target_has_atomic', '64'), ('target_has_atomic', '8'), ('target_has_atomic', 'ptr'),
    ('target_os', 'linux'), ('target_pointer_width', '64'), ('target_vendor', 'unknown'),
    ('unix', None),
)
TARGET_CFG = {
    'x86_64-unknown-linux-gnu': _LINUX_X86_64 + (('target_env', 'gnu'),),
    'x86_64-unknown-linux-musl': _LINUX_X86_64 + (('target_env', 'musl'), ('target_feature', 'crt-static')),
    'aarch64-apple-darwin': (
        ('target_abi', ''), ('target_arch', 'aarch64'), ('target_endian', 'little'),
        ('target_env', ''), ('target_family', 'unix'),
        *(('target_feature', feature) for feature in (
            'aes', 'crc', 'dit', 'dotprod', 'dpb', 'dpb2', 'fcma', 'fhm', 'flagm', 'fp16',
            'frintts', 'jsconv', 'lor', 'lse', 'neon', 'paca', 'pacg', 'pan', 'pmuv3', 'ras',
            'rcpc', 'rcpc2', 'rdm', 'sb', 'sha2', 'sha3', 'ssbs', 'vh')),
        ('target_has_atomic', '128'), ('target_has_atomic', '16'), ('target_has_atomic', '32'),
        ('target_has_atomic', '64'), ('target_has_atomic', '8'), ('target_has_atomic', 'ptr'),
        ('target_os', 'macos'), ('target_pointer_width', '64'), ('target_vendor', 'apple'),
        ('unix', None),
    ),
}
# PCC spellings of the same targets.
TARGET_CFG['arm64-apple-darwin'] = TARGET_CFG['aarch64-apple-darwin']

# Names rustc 1.92.0 rejects on stable as experimental (E0658), and the gated
# list forms `version(..)` and compact `target(..)`.
GATED_CFG = frozenset({
    'overflow_checks', 'ub_checks', 'contract_checks', 'target_thread_local',
    'target_has_atomic_equal_alignment', 'target_has_atomic_load_store', 'sanitize',
    'sanitizer_cfi_generalize_pointers', 'sanitizer_cfi_normalize_integers',
    'relocation_model', 'fmt_debug', 'emscripten_wasm_eh', 'target_has_reliable_f16',
    'target_has_reliable_f128', 'target_has_reliable_f16_math', 'target_has_reliable_f128_math',
})
# Names a compiler invocation sets itself; an explicit --cfg must not spoof them.
_BUILTIN_NAMES = frozenset({name for entries in TARGET_CFG.values() for name, _ in entries}
                           | {'windows', 'debug_assertions', 'panic', 'test', 'proc_macro',
                              'doc', 'doctest', 'miri', 'clippy', 'rustfmt'} | GATED_CFG)


def _parse_cfg_spec(spec):
    """One rustc --cfg spec: `name` or `name="value"` (no escapes)."""
    if type(spec) is not str:
        raise RustError('cfg specs must be strings such as feature="name"')
    name, separator, value = spec.partition('=')
    if not (name.isascii() and name.isidentifier()):
        raise RustError(f'invalid cfg spec {spec!r}: expected an identifier name')
    if not separator:
        return name, None
    if len(value) < 2 or value[0] != '"' or value[-1] != '"' or any(c in value[1:-1] for c in '"\\'):
        raise RustError(f'invalid cfg spec {spec!r}: a value is one double-quoted string without escapes')
    return name, value[1:-1]


@dataclass(frozen=True)
class CfgOptions:
    """Explicit owned cfg inputs, as rustc's --cfg, --test, -C debug-assertions
    and -C panic. `None` means not chosen: a predicate on that name is an
    explicit error rather than a default."""
    cfg: tuple = ()
    test: bool = False
    debug_assertions: bool | None = None
    panic: str | None = None

    def __post_init__(self):
        if type(self.cfg) is not tuple:
            raise RustError('CfgOptions.cfg must be a tuple of cfg specs')
        for name, _value in map(_parse_cfg_spec, self.cfg):
            if name in _BUILTIN_NAMES:
                raise RustError(f'cfg {name!r} is built in; set it through its target or CfgOptions field')
        if type(self.test) is not bool or self.debug_assertions not in {None, True, False}:
            raise RustError('CfgOptions.test and debug_assertions must be booleans')
        if self.panic not in {None, 'unwind', 'abort'}:
            raise RustError("CfgOptions.panic must be 'unwind' or 'abort'")

    def entries(self):
        return tuple(map(_parse_cfg_spec, self.cfg))


@dataclass(frozen=True)
class OwnedRustConfiguration:
    """PCC-owned cfg evaluation for one explicit target and CfgOptions."""
    target_triple: str
    edition: int
    options: CfgOptions

    def __post_init__(self):
        if self.target_triple not in TARGET_CFG:
            raise RustError(f'built-in cfg of target {self.target_triple!r} is not recorded; '
                            'owned cfg evaluation cannot guess it')
        if type(self.options) is not CfgOptions:
            raise RustError('owned cfg evaluation requires explicit CfgOptions')

    @property
    def entries(self):
        options = self.options
        entries = set(TARGET_CFG[self.target_triple]) | set(options.entries())
        if options.test:
            entries.add(('test', None))
        if options.debug_assertions:
            entries.add(('debug_assertions', None))
        if options.panic is not None:
            entries.add(('panic', options.panic))
        return frozenset(entries)

    def predicate(self, item):
        if item.form == 'literal':
            raise item.token.error('unsupported literal in a cfg predicate (rustc E0565)')
        if item.form == 'word' and item.name in {'true', 'false'}:
            return item.name == 'true'
        if item.form == 'list':
            if item.name in {'version', 'target'}:
                raise item.token.error(f'cfg({item.name}(..)) is experimental (rustc E0658)')
            if item.name not in {'all', 'any', 'not'}:
                raise item.token.error(f'invalid cfg predicate {item.name!r} (rustc E0537)')
        if '::' in item.name:
            raise item.token.error('a cfg predicate key must be an identifier')
        if item.form == 'list':
            if item.name in {'all', 'any', 'not'}:
                if item.name == 'not' and len(item.children) != 1:
                    raise item.token.error('cfg not requires exactly one predicate')
                # Every child is validated, as rustc does, before combining.
                values = tuple(self.predicate(child) for child in item.children)
                if item.name == 'not':
                    return not values[0]
                return all(values) if item.name == 'all' else any(values)
            if item.name in {'version', 'target'}:
                raise item.token.error(f'cfg({item.name}(..)) is experimental (rustc E0658)')
            raise item.token.error(f'invalid cfg predicate {item.name!r} (rustc E0537)')
        if item.name in GATED_CFG:
            raise item.token.error(f'cfg({item.name}) is experimental (rustc E0658)')
        if item.name == 'debug_assertions' and self.options.debug_assertions is None:
            raise item.token.error('cfg(debug_assertions) requires an explicit CfgOptions.debug_assertions choice')
        if item.name == 'panic' and self.options.panic is None:
            raise item.token.error('cfg(panic) requires an explicit CfgOptions.panic strategy')
        # Any other name is a plain set membership: an unknown name is false,
        # as in rustc (where Cargo's check-cfg only warns).
        return (item.name, item.value if item.form == 'value' else None) in self.entries


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


@dataclass(frozen=True)
class ConditionalItemAttribute:
    token: object
    predicate: object
    source_span: tuple[int, int]


@dataclass(frozen=True)
class ConfiguredItemAttribute:
    syntax: ConditionalItemAttribute
    configuration: RustConfiguration
    enabled: bool


@dataclass(frozen=True)
class ConfiguredItem:
    """Retained original syntax; only enabled items enter name/IR owners."""
    attributes: tuple[ConfiguredItemAttribute, ...]
    item: object
    tokens: tuple
    source_span: tuple[int, int]

    @property
    def enabled(self):
        return all(attribute.enabled for attribute in self.attributes)


def item_cfg_attribute(parser, token, start):
    items = meta_list(parser, cfg_literals=True, paths=True)
    if len(items) != 1:
        raise token.error('cfg requires exactly one configuration predicate')
    end = parser.expect(']')
    return ConditionalItemAttribute(token, items[0],
                                    (offset(parser, start), offset(parser, end) + 1))


def crate_cfg_attribute(parser, token, start):
    items = meta_list(parser, cfg_literals=True, paths=True)
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
