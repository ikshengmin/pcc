"""Plan an explicit-target, locked Cargo resolver-3 release library build.

No planned unit, generated file, or build-script output is an execution
receipt. Unsupported resolution must fail, not silently omit dependencies.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
import tomllib

from .cargo_config import resolve_features
from .cargo_package_sources import CargoSourceError
from .syntax import RustError


@dataclass(frozen=True)
class CargoPlatform:
    triple: str
    @property
    def family(self):
        try:
            return {
                'x86_64-unknown-linux-gnu':'unix', 'aarch64-unknown-linux-gnu':'unix',
                'x86_64-unknown-linux-musl':'unix', 'aarch64-apple-darwin':'unix',
                'x86_64-pc-windows-msvc':'windows', 'x86_64-pc-windows-gnu':'windows',
            }[self.triple]
        except (KeyError,TypeError) as exc:
            raise CargoSourceError('no admitted family for the declared Cargo platform') from exc


@dataclass(frozen=True, order=True)
class Unit:
    namespace: str  # host build deps vs target library deps
    package: str
    kind: str       # library, compile-build-script, run-build-script

    def label(self):
        return f'{self.namespace}/{self.package}/{self.kind}'


@dataclass(frozen=True)
class PlannedUnit:
    unit: Unit
    source_path: str
    code_target: str
    package_target: str
    externs: tuple[tuple[str,Unit], ...]
    features: tuple[str, ...]
    prerequisites: tuple[Unit, ...]


@dataclass(frozen=True)
class LinksEdge:
    provider: Unit
    consumer: Unit
    links: str


@dataclass(frozen=True)
class CargoBuildPlan:
    declared_host: CargoPlatform
    target: CargoPlatform
    units: tuple[PlannedUnit, ...]
    metadata_routes: tuple[LinksEdge, ...]
    active_packages: tuple[str, ...]
    inactive_locked_packages: tuple[str, ...]

    def record(self):
        return {
            'owner': 'owned resolver-3 locked library build plan only; NO unit has executed',
            'selection': {'declared_build_host': self.declared_host.triple,
                          'explicit_target': self.target.triple, 'profile': 'release',
                          'command_kind': 'library build', 'dev_dependencies': False},
            'active_packages': list(self.active_packages),
            'inactive_locked_packages': list(self.inactive_locked_packages),
            'units': [{'id': p.unit.label(), 'source': p.source_path, 'code_target': p.code_target,
                       'package_target':p.package_target,
                       'executes_on':self.declared_host.triple if p.unit.kind=='run-build-script' else None,
                       'externs': {a:u.label() for a,u in p.externs},
                       'features': list(p.features), 'requires': [u.label() for u in p.prerequisites],
                       'status': 'UNRUN'} for p in self.units],
            'direct_links_metadata_routes': [
                {'from': e.provider.label(), 'to': e.consumer.label(), 'links': e.links,
                 'status': 'UNRUN', 'metadata_observed': False} for e in self.metadata_routes],
        }


_VNUM = r'(?:0|[1-9][0-9]{0,8})'
_REQ = re.compile(r'(\^|=)?('+_VNUM+r'(?:\.'+_VNUM+r'){0,2})')
_FULL = re.compile(_VNUM+r'\.'+_VNUM+r'\.'+_VNUM)
_TRIPLE = re.compile(r'[A-Za-z0-9_]+(?:-[A-Za-z0-9_.]+){2,}')


def _satisfies(requirement, locked):
    """Plain stable caret or exact full version. Reject unsupported ranges."""
    if type(requirement) is not str or not _FULL.fullmatch(locked):
        raise CargoSourceError('unsupported version in a locked build dependency')
    m = _REQ.fullmatch(requirement)
    if not m:
        raise CargoSourceError('unsupported Cargo version requirement: '+str(requirement))
    specified = tuple(map(int,m[2].split('.')))
    low = specified+(0,)*(3-len(specified))
    actual = tuple(map(int,locked.split('.')))
    if m[1]=='=':
        if len(specified)!=3:
            raise CargoSourceError('partial exact requirement is not supported')
        return actual==low
    # First nonzero component, or the last specified zero: ^0.0 -> <0.1.0.
    idx = next((i for i,v in enumerate(specified) if v),len(specified)-1)
    hi = low[:idx]+(low[idx]+1,)+(0,)*(2-idx)
    return low<=actual<hi


def _matches(selector,platform):
    if selector in ('cfg(unix)','cfg(windows)'):
        return selector=='cfg('+platform.family+')'
    if _TRIPLE.fullmatch(selector):
        return selector==platform.triple
    raise CargoSourceError('unsupported conditional dependency: '+selector)


def _dependencies(manifest,table,platform):
    result = list(manifest.get(table,{}).items())
    for selector,group in manifest.get('target',{}).items():
        if table in group and group[table] and _matches(selector,platform):
            result.extend(group[table].items())
    return result


def plan_locked_library(closure, *, host, target, features=(), default_features=True):
    """Use only authenticated source and pinned package versions.

    The role of a normal dependency is inherited; build dependencies and
    proc-macros are host libraries. A build script is compiled for the host,
    then run for its package's target. Its own package's normal libraries
    are not available as Rust build dependencies.
    """
    for p in (host,target):
        if (not isinstance(p,CargoPlatform) or p.family not in ('unix','windows') or
                type(p.triple) is not str or not _TRIPLE.fullmatch(p.triple)):
            raise CargoSourceError('invalid declared Cargo platform')
    if (type(default_features) is not bool or not isinstance(features,(list,tuple,set,frozenset))
            or not all(type(f) is str for f in features)):
        raise CargoSourceError('invalid requested Cargo features')
    manifests = {n:tomllib.loads(p.files['Cargo.toml'].decode()) for n,p in closure.packages.items()}
    root = closure.root.name
    if manifests[root].get('lib',{}).get('proc-macro'):
        raise CargoSourceError('a primary proc-macro build is not supported')
    if manifests[root]['package'].get('resolver')!='3':
        raise CargoSourceError('this explicit build-unit planner requires Cargo resolver 3')
    if ('workspace' in manifests[root]['package'] or any(k in manifests[root] for k in ('workspace','patch','replace')) or
            any(k in ('.cargo/config.toml','.cargo/config') for k in closure.root.files)):
        raise CargoSourceError('workspace resolution or project Cargo overrides require a separate owner')
    locked = {r['name']:r.get('dependencies',()) for r in closure.lock['package']}
    requests = {('target',root):(set(features),default_features)}
    pending = {('target',root)}
    selected,edges = {},{}
    steps = 0
    while pending:
        key=min(pending)
        pending.remove(key)
        steps+=1
        if steps>4096:
            raise CargoSourceError('Cargo feature resolution exceeds limit')
        role,name=key
        requested,defaults=requests[key]
        manifest=manifests[name]
        try:
            selection=resolve_features(manifest,features=requested,default_features=defaults)
        except RustError as exc:
            raise CargoSourceError(f'{name}: {exc}') from exc
        selected[key]=selection.features
        active=set(selection.optional_dependencies)
        declared_aliases=set()
        for group in (manifest,*manifest.get('target',{}).values()):
            for table in ('dependencies','build-dependencies','dev-dependencies'):
                declared_aliases.update(group.get(table,{}))
        dep_features={}
        for alias,feature,_ in selection.dependency_features:
            if not feature or alias not in declared_aliases:
                raise CargoSourceError(f'{name}: feature request names an undeclared dependency: {alias}')
            dep_features.setdefault(alias,set()).add(feature)
        libraries,build_libraries={},{}
        for table in ('dependencies','build-dependencies'):
            build_dep=table=='build-dependencies'
            if build_dep and closure.packages[name].build_script is None:
                continue
            pf=host if build_dep or role=='host' else target
            for alias,spec in _dependencies(manifest,table,pf):
                spec={'version':spec} if type(spec) is str else spec
                if (not isinstance(spec,dict) or set(spec)-{
                        'version','package','optional','default-features','features'} or
                        type(spec.get('optional',False)) is not bool or
                        type(spec.get('default-features',True)) is not bool or
                        not isinstance(spec.get('features',[]),list) or
                        not all(type(v) is str for v in spec.get('features',[]))):
                    raise CargoSourceError(f'unsupported {name} dependency: {alias}')
                if spec.get('optional') and alias not in active:
                    continue
                child=spec.get('package',alias)
                if (type(child) is not str or child not in locked[name] or child not in manifests or
                        not _satisfies(spec.get('version'),closure.packages[child].version)):
                    raise CargoSourceError(f'{name} active dependency {child} does not match Cargo.lock')
                proc=manifests[child].get('lib',{}).get('proc-macro',False)
                if type(proc) is not bool:
                    raise CargoSourceError('invalid proc-macro target')
                child_key=('host' if build_dep or proc else role,child)
                bindings=build_libraries if build_dep else libraries
                crate_name=(alias if 'package' in spec else manifests[child].get('lib',{}).get('name',child))
                if not isinstance(crate_name,str) or not re.fullmatch('[A-Za-z0-9_-]+',crate_name):
                    raise CargoSourceError('unsupported dependency crate name')
                extern_name=crate_name.replace('-','_')
                if extern_name in bindings and bindings[extern_name]!=child_key:
                    raise CargoSourceError('multiple packages provide one dependency alias: '+extern_name)
                bindings[extern_name]=child_key
                additions=set(spec.get('features',()))|dep_features.get(alias,set())
                old_features,old_defaults=requests.get(child_key,(set(),False))
                updated=(old_features|additions,old_defaults or spec.get('default-features',True))
                if child_key not in requests or requests[child_key]!=updated:
                    requests[child_key]=updated
                    pending.add(child_key)
        edges[key]=(libraries,build_libraries)
    def library(key):
        r,n=key
        return Unit(r,n,'proc-macro' if manifests[n].get('lib',{}).get('proc-macro') else 'library')

    def extern_bindings(mapping):
        return tuple(sorted((a,library(k)) for a,k in mapping.items()))

    planned,routes,links_owners={},[],{}
    for key in sorted(selected):
        role,name=key
        pkg=closure.packages[name]
        pf=host if role=='host' else target
        normal,build=edges[key]
        if pkg.links:
            prior=links_owners.setdefault(pkg.links,name)
            if prior!=name:
                raise CargoSourceError('duplicate native links owner: '+pkg.links)
        lib=library(key)
        dependencies={library(k) for k in normal.values()}
        if pkg.build_script:
            compile_unit=Unit(role,name,'compile-build-script')
            run_unit=Unit(role,name,'run-build-script')
            run_prereqs={compile_unit}
            for r,n in sorted(set(normal.values())):
                links=closure.packages[n].links
                if links:
                    producer=Unit(r,n,'run-build-script')
                    routes.append(LinksEdge(producer,run_unit,links))
                    run_prereqs.add(producer)
            if any(closure.packages[n].links for r,n in build.values()):
                raise CargoSourceError('build-dependency links metadata is not supported')
            planned[compile_unit]=PlannedUnit(compile_unit,pkg.build_script,host.triple,pf.triple,
                                              extern_bindings(build),selected[key],
                                              tuple(sorted({library(k) for k in build.values()})))
            planned[run_unit]=PlannedUnit(run_unit,pkg.build_script,host.triple,pf.triple,(),
                                          selected[key],tuple(sorted(run_prereqs)))
            dependencies.add(run_unit)
        planned[lib]=PlannedUnit(lib,pkg.lib_path,pf.triple,pf.triple,extern_bindings(normal),
                                 selected[key],tuple(sorted(dependencies)))
    ordered,done=[],set()
    while len(done)<len(planned):
        ready=sorted(u for u,p in planned.items() if u not in done and all(d in done for d in p.prerequisites))
        if not ready:
            raise CargoSourceError('cyclic or unprovided Cargo build-unit prerequisite')
        ordered.extend(planned[u] for u in ready)
        done.update(ready)
    active_packages=tuple(sorted({name for role,name in selected}))
    return CargoBuildPlan(host,target,tuple(ordered),tuple(routes),active_packages,
                          tuple(sorted(set(manifests)-set(active_packages))))
