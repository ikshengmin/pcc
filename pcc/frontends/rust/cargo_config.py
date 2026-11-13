"""Owned Cargo feature and profile selection for one package's cfg inputs.

Cargo reference, features.html and profiles.html (retrieved 2026-11-13).
Given parsed manifests (TOML tables, never a Cargo process), this computes the
package's enabled features, the optional dependencies they enable and the
dependency-feature requests they make, plus the profile's debug-assertions
and panic settings. `cargo_cfg_options` turns them into the `CfgOptions` that
`cargo build` would pass to rustc for this package: `feature="..."` per
enabled feature, `test` only when asked, and the profile options.

Dependency resolution, the dependencies' own features, build scripts,
`[target.'cfg(..)'.dependencies]` selection and unification across a
dependency graph are not modeled here.
"""
from __future__ import annotations

from dataclasses import dataclass

from .configuration import CfgOptions
from .syntax import RustError

_DEPENDENCY_TABLES = ('dependencies', 'build-dependencies')
_BUILTIN_PROFILES = {
    'dev': {'debug-assertions': True, 'panic': 'unwind'},
    'release': {'debug-assertions': False, 'panic': 'unwind'},
    'test': {'inherits': 'dev'},
    'bench': {'inherits': 'release'},
}


@dataclass(frozen=True)
class CargoFeatureSelection:
    features: tuple[str, ...]
    optional_dependencies: tuple[str, ...]
    dependency_features: tuple[tuple[str, str, bool], ...]  # (dependency, feature, weak)


def _optional_dependencies(manifest):
    optional = set()
    tables = [manifest.get(name, {}) for name in _DEPENDENCY_TABLES]
    for target in manifest.get('target', {}).values():
        tables.extend(target.get(name, {}) for name in _DEPENDENCY_TABLES)
    for table in tables:
        for name, spec in table.items():
            if isinstance(spec, dict) and spec.get('optional') is True:
                optional.add(name)
    return optional


def resolve_features(manifest, *, features=(), default_features=True):
    """Close the requested features over this package's `[features]` table."""
    table = manifest.get('features', {})
    if not isinstance(table, dict) or any(
            not isinstance(values, list) or not all(isinstance(v, str) for v in values)
            for values in table.values()):
        raise RustError('Cargo [features] must map names to lists of strings')
    optional = _optional_dependencies(manifest)
    explicit_dep = {value[4:] for values in table.values() for value in values if value.startswith('dep:')}
    # An optional dependency not named by any `dep:` value has an implicit
    # feature of its own name that enables it.
    implicit = {name: ['dep:' + name] for name in optional - explicit_dep if name not in table}
    known = {**implicit, **table}
    requested = list(features)
    if default_features and 'default' in known:
        requested.append('default')
    enabled, dependencies, requests = set(), set(), set()
    pending = list(requested)
    for name in requested:
        if name not in known:
            raise RustError(f'package does not have feature {name!r}')
    while pending:
        name = pending.pop()
        if name in enabled:
            continue
        enabled.add(name)
        for value in known[name]:
            if value.startswith('dep:'):
                dependency = value[4:]
                if dependency not in optional:
                    raise RustError(f'feature {name!r} enables {value!r}, which is not an optional dependency')
                dependencies.add(dependency)
            elif '/' in value:
                dependency, feature = value.split('/', 1)
                weak = dependency.endswith('?')
                dependency = dependency.rstrip('?')
                requests.add((dependency, feature, weak))
                if not weak and dependency in optional:
                    dependencies.add(dependency)
                    if dependency in implicit:
                        pending.append(dependency)
            elif value in known:
                pending.append(value)
            else:
                raise RustError(f'feature {name!r} includes {value!r}, which is neither a feature nor a dependency')
    # A weak request applies only to a dependency enabled some other way.
    requests = {(d, f, w) for d, f, w in requests if not w or d in dependencies or d not in optional}
    return CargoFeatureSelection(tuple(sorted(enabled)), tuple(sorted(dependencies)), tuple(sorted(requests)))


def profile_options(manifest, profile):
    """(debug_assertions, panic) of a profile, following `inherits`."""
    profiles = manifest.get('profile', {})
    chain, settings, name = [], {}, profile
    while True:
        if name in chain:
            raise RustError(f'Cargo profile inheritance cycle through {name!r}')
        chain.append(name)
        declared = profiles.get(name)
        builtin = _BUILTIN_PROFILES.get(name)
        if declared is None and builtin is None:
            raise RustError(f'Cargo profile {name!r} is not defined')
        for key, value in (declared or {}).items():
            settings.setdefault(key, value)
        parent = (declared or {}).get('inherits', (builtin or {}).get('inherits'))
        if parent is None:
            for key, value in (builtin or {}).items():
                settings.setdefault(key, value)
            break
        if name in {'dev', 'release'}:
            raise RustError(f'Cargo profile {name!r} cannot inherit another profile')
        name = parent
    debug_assertions, panic = settings['debug-assertions'], settings['panic']
    if type(debug_assertions) is not bool or panic not in {'unwind', 'abort'}:
        raise RustError(f'Cargo profile {profile!r} has unsupported debug-assertions/panic values')
    # Cargo ignores `panic` for test and bench targets, which always unwind.
    if profile in {'test', 'bench'} or 'test' in chain or 'bench' in chain:
        panic = 'unwind'
    return debug_assertions, panic


def cargo_cfg_options(package_manifest, *, workspace_manifest=None, features=(),
                      default_features=True, profile='release', test=False):
    """The cfg inputs `cargo build` gives rustc for this package."""
    selection = resolve_features(package_manifest, features=features, default_features=default_features)
    # Profiles are read from the workspace root manifest when there is one.
    debug_assertions, panic = profile_options(workspace_manifest or package_manifest, profile)
    cfg = tuple(f'feature="{name}"' for name in selection.features)
    return CfgOptions(cfg=cfg, test=test, debug_assertions=debug_assertions, panic=panic), selection
