"""Owned Cargo feature and profile selection for one package's cfg inputs.

Cargo reference, features.html and profiles.html (retrieved 2026-11-13).
Given parsed manifests (TOML tables, never a Cargo process), this computes the
package's enabled features, the optional dependencies they enable and the
dependency-feature requests they make, plus the profile's debug-assertions
and panic settings. `cargo_cfg_options` turns them into the `CfgOptions` that
`cargo build` would pass to rustc for this package: `feature="..."` per
enabled feature, `test` only when asked, and the profile options.

`cargo_environment` gives the compile-time environment Cargo sets for a
package's crates (environment-variables.html, "Environment variables Cargo
sets for crates"), as observed from cargo 1.92: every CARGO_PKG_* field is
set, empty when the manifest omits it; authors are joined with `:`; the
version's pre-release excludes build metadata; `{ workspace = true }` fields
come from `[workspace.package]`. Values that depend on files, paths or the
cargo invocation are marked unavailable unless supplied.

Dependency resolution, the dependencies' own features, build scripts,
`[target.'cfg(..)'.dependencies]` selection and unification across a
dependency graph are not modeled here.
"""
from __future__ import annotations

import re
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


_SEMVER = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
                     r'(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?')
_PACKAGE_STRINGS = (('CARGO_PKG_DESCRIPTION', 'description'), ('CARGO_PKG_HOMEPAGE', 'homepage'),
                    ('CARGO_PKG_REPOSITORY', 'repository'), ('CARGO_PKG_LICENSE', 'license'),
                    ('CARGO_PKG_RUST_VERSION', 'rust-version'))


def _package_field(package, workspace_manifest, key):
    """A [package] field, following `{ workspace = true }` inheritance."""
    value = package.get(key)
    if isinstance(value, dict) and value.get('workspace') is True:
        inherited = ((workspace_manifest or {}).get('workspace', {}).get('package', {}))
        if key not in inherited:
            raise RustError(f'package field {key!r} inherits from [workspace.package], which does not define it')
        return inherited[key], True
    return value, False


def cargo_environment(package_manifest, *, workspace_manifest=None, manifest_dir=None,
                      crate_name=None, primary=None):
    """The compile-time environment Cargo sets for one package's crate."""
    from .builtin_macros import CompileEnvironment
    package = package_manifest.get('package')
    if not isinstance(package, dict) or not isinstance(package.get('name'), str):
        raise RustError('Cargo manifest requires [package] name')
    variables, unavailable = {}, {}
    variables['CARGO_PKG_NAME'] = package['name']
    library = package_manifest.get('lib', {}).get('name')
    variables['CARGO_CRATE_NAME'] = crate_name or library or package['name'].replace('-', '_')
    version, _ = _package_field(package, workspace_manifest, 'version')
    version = '0.0.0' if version is None else version
    match = _SEMVER.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise RustError(f'Cargo package version {version!r} is not a semantic version')
    variables['CARGO_PKG_VERSION'] = version
    for name, part in zip(('MAJOR', 'MINOR', 'PATCH', 'PRE'), match.groups()):
        variables['CARGO_PKG_VERSION_' + name] = part or ''
    authors, _ = _package_field(package, workspace_manifest, 'authors')
    if authors is not None and (not isinstance(authors, list) or not all(isinstance(a, str) for a in authors)):
        raise RustError('Cargo package authors must be a list of strings')
    variables['CARGO_PKG_AUTHORS'] = ':'.join(authors or ())
    for variable, key in _PACKAGE_STRINGS:
        value, _ = _package_field(package, workspace_manifest, key)
        if value is not None and not isinstance(value, str):
            raise RustError(f'Cargo package field {key!r} must be a string')
        variables[variable] = value or ''
    # Paths are rebased by Cargo when inherited, and an absent readme is
    # inferred from the package's files: neither is guessed here.
    license_file, inherited = _package_field(package, workspace_manifest, 'license-file')
    if inherited:
        unavailable['CARGO_PKG_LICENSE_FILE'] = 'Cargo rebases an inherited license-file path'
    else:
        variables['CARGO_PKG_LICENSE_FILE'] = license_file or ''
    readme, inherited = _package_field(package, workspace_manifest, 'readme')
    if inherited:
        unavailable['CARGO_PKG_README'] = 'Cargo rebases an inherited readme path'
    elif readme is None:
        unavailable['CARGO_PKG_README'] = 'Cargo infers an unspecified readme from the package files'
    else:
        variables['CARGO_PKG_README'] = 'README.md' if readme is True else '' if readme is False else readme
    if manifest_dir is None:
        reason = 'the package directory is a machine-specific path; supply manifest_dir'
        unavailable['CARGO_MANIFEST_DIR'] = unavailable['CARGO_MANIFEST_PATH'] = reason
    else:
        variables['CARGO_MANIFEST_DIR'] = manifest_dir
        variables['CARGO_MANIFEST_PATH'] = manifest_dir.rstrip('/') + '/Cargo.toml'
    if primary is None:
        unavailable['CARGO_PRIMARY_PACKAGE'] = 'it depends on the cargo invocation; supply primary'
    elif primary:
        variables['CARGO_PRIMARY_PACKAGE'] = '1'
    for name, reason in (('CARGO', 'it is the path of the running cargo executable'),
                         ('OUT_DIR', 'it is set only for packages whose build script ran'),
                         ('CARGO_BIN_NAME', 'it is set only for binary targets'),
                         ('CARGO_TARGET_TMPDIR', 'it is set only for integration tests and benches')):
        unavailable[name] = reason
    return CompileEnvironment.of(variables, unavailable)
