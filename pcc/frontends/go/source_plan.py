"""Explicit locked source inputs for the owned Go frontend.

The caller chooses a release directory and a manifest with a separately
trusted digest. This module never chooses Go files by walking the host, never
runs go, and never loads any official binary, object, or runtime. An authenticated
assembly source is still source, not an assembled definition or ABI owner.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

MAX_FILES=2048
MAX_FILE_BYTES=4*1024*1024
MAX_TOTAL_BYTES=16*1024*1024
MAX_LOCK_BYTES=1024*1024
MAX_PACKAGES=256
TRIPLE='x86_64-unknown-linux-gnu'
TARGET={'triple':TRIPLE,'goos':'linux','goarch':'amd64','goamd64':'v1','cgo':False,
        'build_tags':[],'goexperiment':''}
ROLES={'go':'.go','asm':'.s','header':'.h'}
SHA=re.compile(r'[0-9a-f]{64}')
PATH=re.compile(r'[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*')


class GoSourcePlanError(ValueError):
    """Bad original source supply or unavailable owner, never an accepted build."""


def _bad(message):
    raise GoSourcePlanError(message)


def _path(s):
    if type(s) is not str or not PATH.fullmatch(s) or any(c in ('.','..') for c in s.split('/')):
        _bad('invalid locked relative path: '+repr(s))
    return s


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    result={}
    for k,v in pairs:
        if k in result:_bad('duplicate source plan key: '+k)
        result[k]=v
    return result


def _read_original(rootfd, relative, size):
    # Descriptor-relative directory descent keeps each file under the chosen
    # source tree. A wrong file, symlink, FIFO or oversize object cannot be
    # accepted based on its path or an earlier stat. Retain the verified bytes
    # for the compiler instead of reopening them after the checksum check.
    parts=_path(relative).split('/')
    fd=os.dup(rootfd)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        raw=os.open(parts[-1],os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=fd)
        with os.fdopen(raw,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size!=size:
                _bad('nonregular or wrong-size original source: '+relative)
            b=f.read(size+1)
            if len(b)!=size:_bad('changed original source length: '+relative)
            return b
    except OSError as exc:
        raise GoSourcePlanError('cannot open original source beneath the explicit root: '+relative) from exc
    finally:
        os.close(fd)


def runtime_owner(path):
    return path in ('unsafe','runtime','C') or path.startswith(('runtime/','internal/runtime/'))


@dataclass(frozen=True)
class LockedGoPackage:
    imports: tuple
    go: tuple
    assembly: tuple
    headers: tuple
    includes: tuple


_VERIFIED_SOURCE = object()


@dataclass(frozen=True, init=False)
class LockedGoSourcePlan:
    """Immutable snapshot from load_go_source_plan. No tool paths or file handles."""
    release: str
    target: str
    lock_sha256: str
    roots: tuple
    packages: object
    original_files: object

    def __init__(self, *args, **kwargs):
        raise TypeError('use load_go_source_plan with original release bytes and a trusted digest')

    @classmethod
    def _snapshot(cls, release, expected, roots, packages, files):
        value=object.__new__(cls)
        for name,field in (('release',release),('target',TARGET['triple']),('lock_sha256',expected),
                           ('roots',tuple(roots)),('packages',MappingProxyType(packages)),
                           ('original_files',MappingProxyType(files)),('_validated',_VERIFIED_SOURCE)):
            object.__setattr__(value,name,field)
        return value

    def units(self, path):
        package=self.packages[path]
        return tuple((name,self.original_files[name].decode('utf-8')) for name in package.go)

    def unresolved(self, paths):
        out=[]
        for path in sorted(set(paths)):
            pkg=self.packages[path]
            if runtime_owner(path):
                out.append((path,'PCC runtime/intrinsic owner',()))
            if pkg.assembly:
                out.append((path,'Go assembly/ABI owner',pkg.assembly))
            generated=tuple(sorted({name for _,kind,name in pkg.includes if kind=='generated'}))
            if generated:
                out.append((path,'PCC-generated layout header',generated))
        return tuple(out)

    def check_target(self, target):
        if getattr(self,'_validated',None) is not _VERIFIED_SOURCE:
            _bad('compile requires a checked original source snapshot')
        if self.target!=target:
            _bad('source plan does not target '+target)

    def check_imports(self, path, imports):
        if set(imports)!=set(self.packages[path].imports):
            _bad('owned source imports differ from complete pinned selection: '+path)

    def check_completion(self, used, main_package):
        # Called on the successful frontend path, never to turn a parser error
        # into an expected PASS. If a required owner is missing the public
        # compile entry cannot return even a seemingly complete IR module.
        if not set(self.roots)<=used:
            _bad('project '+main_package+' did not visit all declared standard-library roots')
        missing=self.unresolved(used)
        if missing:
            _bad('standard-library link/runtime owners still unavailable: '+
                 '; '.join(path+': '+reason for path,reason,_ in missing))


def load_go_source_plan(directory, lock_bytes, *, expected_sha256):
    """Snapshot every target-selected original file from an explicitly supplied GOROOT.

    The manifest is a release recipe, not permission to read arbitrary paths.
    Use its trusted expected SHA from the calling project. Only the qualified
    Linux-amd64/v1 pure-Go release selection is currently admitted. Native
    source attachments are pinned, not implicitly compiled.
    """
    if (type(lock_bytes) is not bytes or len(lock_bytes)>MAX_LOCK_BYTES or
            type(expected_sha256) is not str or not SHA.fullmatch(expected_sha256) or
            _sha(lock_bytes)!=expected_sha256):
        _bad('locked Go source manifest checksum/size mismatch')
    try:
        data=json.loads(lock_bytes,object_pairs_hook=_object)
    except (ValueError,UnicodeError) as exc:
        raise GoSourcePlanError('invalid locked Go source manifest') from exc
    if (type(data) is not dict or set(data)!={'schema','release','distribution','distribution_sha256',
            'selector_sha256','target','roots','packages','assembly_includes','files'} or
            type(data['schema']) is not int or data['schema']!=1 or
            data['target']!=TARGET or data['target']['cgo'] is not False or
            not isinstance(data['release'],str) or not re.fullmatch(r'go1\.\d+\.\d+',data['release']) or
            data['distribution']!=data['release']+'.linux-amd64.tar.gz' or
            not all(isinstance(data[k],str) and SHA.fullmatch(data[k]) for k in ('distribution_sha256','selector_sha256'))):
        _bad('unsupported Go release identity or target configuration')
    packages,inc,rows,roots=data['packages'],data['assembly_includes'],data['files'],data['roots']
    if (type(packages) is not dict or not 1<=len(packages)<=MAX_PACKAGES or type(rows) is not dict or
            not 2<=len(rows)<=MAX_FILES or type(inc) is not dict or not inc.keys()<=packages.keys() or
            type(roots) is not list or not roots or not all(type(k) is str and k in packages for k in roots) or
            len(set(roots))!=len(roots)):
        _bad('invalid locked Go package/root inventory')
    used={'LICENSE','VERSION'};tree={}
    for name,row in packages.items():
        _path(name)
        if (type(row) is not dict or set(row)!={'imports','files'} or type(row['imports']) is not list or
                not all(type(i) is str and i in packages for i in row['imports']) or
                len(row['imports'])!=len(set(row['imports'])) or
                type(row['files']) is not dict or set(row['files'])!=set(ROLES)):
            _bad('incomplete import/source role inventory for '+name)
        groups={};prefix='src/'+name+'/'
        for role,extension in ROLES.items():
            seq=row['files'][role]
            if (type(seq) is not list or not all(type(n) is str and '/' not in _path(n)
                    and n.endswith(extension) and not n.endswith('_test.go') for n in seq)
                    or len(seq)!=len(set(seq))):
                _bad('invalid '+role+' source for '+name)
            groups[role]=tuple(prefix+n for n in seq);used.update(groups[role])
        if not groups['go']:_bad('locked package has no original Go source: '+name)
        records=[];local_sources=set(groups['asm'])|set(groups['header'])
        if type(inc.get(name,[])) is not list:_bad('invalid assembly include inventory')
        for record in inc.get(name,[]):
            if type(record) is not dict or set(record) not in ({'from','generated'},{'from','original'}):
                _bad('invalid assembly provenance')
            from_path=_path(record['from'])
            if 'generated' in record:
                if record['generated']!='go_asm.h':_bad('unknown generated Go assembler input')
                records.append((from_path,'generated','go_asm.h'))
            else:
                dest=_path(record['original'])
                if not (dest.startswith(prefix) or dest.startswith('pkg/include/')) or not dest.endswith('.h'):
                    _bad('assembly include escapes its package and release include root')
                records.append((from_path,'original',dest));used.add(dest)
        reachable=set(local_sources)
        while True:
            more={dest for src,role,dest in records if src in reachable and role=='original'}-reachable
            if not more:break
            reachable.update(more)
        if any(src not in reachable for src,_,_ in records):_bad('unreachable assembly include owner in '+name)
        tree[name]=LockedGoPackage(tuple(row['imports']),groups['go'],groups['asm'],groups['header'],tuple(records))
    reachable=set();active=set()
    def visit(path):
        if path in active:_bad('cycle in pinned Go package graph')
        if path in reachable:return
        active.add(path)
        for dep in tree[path].imports:visit(dep)
        active.remove(path);reachable.add(path)
    for name in roots:visit(name)
    if reachable!=set(tree) or set(rows)!=used:
        _bad('original file/package inventory is not exact for its roots')
    total=0
    for f,row in rows.items():
        _path(f)
        if (type(row) is not list or len(row)!=2 or type(row[0]) is not int or not 0<=row[0]<=MAX_FILE_BYTES or
                type(row[1]) is not str or not SHA.fullmatch(row[1])):
            _bad('invalid original-byte identity: '+f)
        total+=row[0]
    if total>MAX_TOTAL_BYTES:_bad('locked Go source plan exceeds whole input limit')
    if not hasattr(os,'O_NOFOLLOW') or not hasattr(os,'O_DIRECTORY') or os.open not in os.supports_dir_fd:
        _bad('host lacks checked directory-relative source input support')
    try:
        rootfd=os.open(Path(directory).resolve(strict=True),os.O_RDONLY|os.O_DIRECTORY)
    except (OSError,RuntimeError) as exc:
        raise GoSourcePlanError('explicit original Go release directory is unavailable') from exc
    original={}
    try:
        for f,(size,sha) in rows.items():
            value=_read_original(rootfd,f,size)
            if _sha(value)!=sha:_bad('original source checksum mismatch: '+f)
            original[f]=value
    finally:
        os.close(rootfd)
    if not original['VERSION'].splitlines() or original['VERSION'].splitlines()[0]!=data['release'].encode():
        _bad('original VERSION does not match locked Go release')
    return LockedGoSourcePlan._snapshot(data['release'],expected_sha256,roots,tree,original)
