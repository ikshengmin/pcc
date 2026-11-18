"""Read a pinned root Git archive and its full, offline Cargo registry source set.

The old bounded original-source fixture is not a Cargo dependency store. No
checkout, extraction, script execution, registry access, or version selection
occurs here. Real manifest/source bytes remain available to the build planner.
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import io
import os
import stat
from pathlib import Path
import re
import tarfile
import tomllib
import zlib
from types import MappingProxyType


class CargoSourceError(ValueError):
    """A source identity, archive, or bounded Cargo input was not established."""


# Separate, explicit package-store limits, not replacements for the original
# isolated source/yyjson fixture's 32-file and 2 MiB aggregate integrity gate.
MAX_PACKAGES = 64
MAX_COMPRESSED_PACKAGE = 1 << 20
MAX_COMPRESSED_CLOSURE = 8 << 20
MAX_TAR_PACKAGE = 8 << 20
MAX_TAR_CLOSURE = 32 << 20
MAX_MEMBER = 1 << 20
MAX_MEMBERS = 4096
REGISTRY = 'registry+https://github.com/rust-lang/crates.io-index'
_NAME = re.compile(r'[A-Za-z0-9_-]+')
_VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.+-]+)?')


def _immutable(obj):
    if isinstance(obj,dict):
        return MappingProxyType({k:_immutable(v) for k,v in obj.items()})
    if isinstance(obj,list):
        return tuple(_immutable(v) for v in obj)
    return obj


def _git(kind, data):
    return hashlib.sha1(kind + b' ' + str(len(data)).encode('ascii') + b'\0' + data).digest()


def _tree_id(node):
    entries = []
    for name,obj in sorted(node.items(),key=lambda entry:entry[0] + (b'/' if isinstance(entry[1],dict) else b'')):
        mode, oid = (b'40000', _tree_id(obj)) if isinstance(obj,dict) else obj
        entries.append(mode + b' ' + name + b'\0' + oid)
    return _git(b'tree',b''.join(entries))


def _package_file(store, components):
    """Only bounded regular files under the selected immutable package store."""
    path=Path(store)
    if path.is_symlink() or not path.is_dir():
        raise CargoSourceError('missing or symlinked Cargo source store')
    for part in components:
        if not part or part in ('.','..') or '/' in part or '\\' in part:
            raise CargoSourceError('invalid package-store component')
        path=path/part
        if path.is_symlink():
            raise CargoSourceError('symlinked Cargo source-store input')
    # The checkout/store directory is trusted (as are compiler .py files).
    # Authenticate bytes before use, and inspect the actual opened handle.
    # O_NONBLOCK avoids waiting on a replaced FIFO where supported.
    flags=os.O_RDONLY | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_BINARY',0)
    try:
        with os.fdopen(os.open(path,flags),'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_COMPRESSED_PACKAGE:
                raise CargoSourceError('nonregular or oversize original package archive: '+path.name)
            raw=stream.read(MAX_COMPRESSED_PACKAGE+1)
    except OSError as exc:
        raise CargoSourceError('unavailable source archive: '+path.name) from exc
    if len(raw)>MAX_COMPRESSED_PACKAGE:
        raise CargoSourceError('compressed package exceeds the input limit')
    return raw


@dataclass(frozen=True)
class RootArchivePin:
    revision: str
    tree: str
    sha256: str
    prefix: str


@dataclass(frozen=True)
class CargoPackageSource:
    name: str
    version: str
    archive_sha256: str
    compressed_bytes: int
    tar_bytes: int
    files: object  # immutable relative path -> original bytes
    manifest: object  # deeply immutable normalized Cargo.toml (root uses the original)
    lib_path: str
    build_script: str | None
    links: str | None

    def record(self):
        return {'name':self.name,'version':self.version,'archive_sha256':self.archive_sha256,
                'compressed_bytes':self.compressed_bytes,'expanded_member_bytes':sum(map(len,self.files.values())),
                'member_count':len(self.files),'lib_path':self.lib_path,'build_script':self.build_script,
                'links':self.links,'license_declaration':self.manifest['package'].get('license')}


@dataclass(frozen=True)
class CargoSourceClosure:
    root: CargoPackageSource
    packages: object  # bounded planner admits unique package names; checked below
    lock: object
    root_pin: RootArchivePin

    def record(self):
        return {'owner':'offline authenticated original package sources only; no unit executed',
                'root_revision':self.root_pin.revision,'root_git_tree':self.root_pin.tree,
                'cargo_lock_sha256':hashlib.sha256(self.root.files['Cargo.lock']).hexdigest(),
                'root':self.root.record(),
                'registry':[self.packages[n].record() for n in sorted(self.packages) if n!=self.root.name]}


def _path_parts(text):
    if (type(text) is not str or not text or any(ord(c)<32 or ord(c)==127 for c in text)
            or len(text.encode('utf-8'))>512 or '\\' in text or '\0' in text or ':' in text
            or any(p in ('','.','..') for p in text.split('/')) or len(text.split('/'))>32):
        raise CargoSourceError('noncanonical package member path')
    return text.split('/')


def _unpack(raw, checksum, prefix, budget, tree=None):
    if type(checksum) is not str or not re.fullmatch('[0-9a-f]{64}',checksum) or hashlib.sha256(raw).hexdigest()!=checksum:
        raise CargoSourceError('package archive SHA-256 mismatch: '+prefix)
    budget[0]+=len(raw)
    if budget[0]>MAX_COMPRESSED_CLOSURE:
        raise CargoSourceError('compressed source closure exceeds limit')
    try:
        # Limit the complete tar stream (headers/PAX/padding too), before a tar
        # parser is allowed to allocate based on any declared member size.
        limit=min(MAX_TAR_PACKAGE,MAX_TAR_CLOSURE-budget[1])
        with gzip.GzipFile(fileobj=io.BytesIO(raw),mode='rb') as stream:
            payload=stream.read(limit+1)
        if len(payload)>limit:
            raise CargoSourceError('expanded tar stream exceeds package or closure limit')
        budget[1]+=len(payload)
        files, seen, tree_nodes = {}, set(), {}
        last=0
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:') as archive:
            for member in archive:
                budget[2]+=1
                if budget[2]>MAX_MEMBERS:
                    raise CargoSourceError('source closure exceeds member limit')
                parts=_path_parts(member.name)
                if parts[0]!=prefix or member.name in seen:
                    raise CargoSourceError('wrong package root or duplicate member')
                seen.add(member.name)
                if not (member.isdir() or member.type in (tarfile.REGTYPE,tarfile.AREGTYPE)):
                    raise CargoSourceError('links, devices and nonregular source members are not admitted')
                if (member.sparse is not None or member.mode&0o7000 or not 0<=member.size<=MAX_MEMBER
                        or member.isdir() and member.size!=0):
                    raise CargoSourceError('unsafe or oversized source member')
                last=member.offset_data + ((member.size+511)//512)*512
                if last>len(payload):
                    raise CargoSourceError('truncated package member')
                if len(parts)==1:
                    if not member.isdir():
                        raise CargoSourceError('archive root is not a directory')
                    continue
                location=tree_nodes
                for part in parts[1:-1]:
                    location=location.setdefault(part.encode('utf-8'),{})
                    if not isinstance(location,dict):
                        raise CargoSourceError('file used as a parent directory')
                leaf=parts[-1].encode('utf-8')
                if member.isdir():
                    if not isinstance(location.setdefault(leaf,{}),dict):
                        raise CargoSourceError('file/directory collision')
                    continue
                if leaf in location:
                    raise CargoSourceError('file/directory collision')
                reader=archive.extractfile(member)
                if reader is None:
                    raise CargoSourceError('unreadable source member')
                data=reader.read(member.size+1)
                if len(data)!=member.size:
                    raise CargoSourceError('truncated source member')
                files['/'.join(parts[1:])]=data
                location[leaf]=(b'100755' if member.mode&0o111 else b'100644',_git(b'blob',data))
        if len(payload)-last<1024 or len(payload)%512 or any(memoryview(payload)[last:]):
            raise CargoSourceError('malformed stream or data after terminal tar record')
        if tree is not None and _tree_id(tree_nodes).hex()!=tree:
            raise CargoSourceError('root archive does not reproduce its pinned Git tree')
        return files,len(payload)
    except (tarfile.TarError, OSError, EOFError, UnicodeError, zlib.error) as exc:
        raise CargoSourceError(f'invalid archive {prefix}: {exc}') from exc


def _toml(files,path):
    try:
        return tomllib.loads(files[path].decode('utf-8'))
    except (KeyError,ValueError,UnicodeDecodeError) as exc:
        raise CargoSourceError('invalid or absent '+path) from exc


def _source(raw, checksum, prefix, budget, tree=None, expected=None):
    files,tar_bytes=_unpack(raw,checksum,prefix,budget,tree)
    manifest=_toml(files,'Cargo.toml');pkg=manifest.get('package')
    if not isinstance(pkg,dict):
        raise CargoSourceError('Cargo.toml lacks its package')
    name,version=pkg.get('name'),pkg.get('version')
    if (type(name) is not str or not _NAME.fullmatch(name) or type(version) is not str
            or not _VERSION.fullmatch(version) or expected is not None and (name,version)!=expected):
        raise CargoSourceError('locked package identity differs from its Cargo.toml')
    library=manifest.get('lib',{})
    if not isinstance(library,dict):
        raise CargoSourceError('invalid Cargo.toml library table')
    default_lib='src/lib.rs' if pkg.get('autolib',True) or 'lib' in manifest else None
    lib=library.get('path',default_lib)
    if type(lib) is not str:
        raise CargoSourceError('package has no supported library target')
    build=pkg.get('build','build.rs' if 'build.rs' in files else False)
    if build is True: build='build.rs'
    if build is False: build=None
    for part in (lib,build):
        if part is not None and (type(part) is not str or not _path_parts(part) or part not in files):
            raise CargoSourceError('manifest target source is absent')
    links=pkg.get('links')
    if links is not None and (type(links) is not str or not _NAME.fullmatch(links) or build is None):
        raise CargoSourceError('links needs a named native library and its actual build script')
    return CargoPackageSource(name,version,checksum,len(raw),tar_bytes,MappingProxyType(files),
                              _immutable(manifest),lib,build,links)


def load_cargo_source_closure(store,root_pin,*,original_files):
    """Verify even inactive Cargo.lock packages; do not equate presence with use."""
    if (type(root_pin) is not RootArchivePin or type(root_pin.revision) is not str
            or type(root_pin.tree) is not str or not re.fullmatch('[0-9a-f]{40}',root_pin.revision)
            or not re.fullmatch('[0-9a-f]{40}',root_pin.tree) or len(_path_parts(root_pin.prefix))!=1):
        raise CargoSourceError('an immutable source archive pin is required')
    budget=[0,0,0]
    root=_source(_package_file(store,('upstream.tar.gz',)),root_pin.sha256,root_pin.prefix,budget,root_pin.tree)
    if (not hasattr(original_files,'items') or any(
            type(name) is not str or type(data) is not bytes or name not in root.files
            or root.files[name]!=data for name,data in original_files.items())):
        raise CargoSourceError('full Git archive differs from an original fixture input')
    lock=_toml(root.files,'Cargo.lock'); rows=lock.get('package')
    if lock.get('version')!=4 or not isinstance(rows,list) or not 1<=len(rows)<=MAX_PACKAGES:
        raise CargoSourceError('unsupported or oversized Cargo.lock')
    packages={}; root_seen=False
    for rec in rows:
        if not isinstance(rec,dict):
            raise CargoSourceError('invalid Cargo.lock entry')
        name,version=rec.get('name'),rec.get('version')
        if (type(name) is not str or not _NAME.fullmatch(name) or type(version) is not str
                or not _VERSION.fullmatch(version) or name in packages):
            raise CargoSourceError('invalid or nonunique locked package name/version')
        if 'source' not in rec:
            if root_seen or (name,version)!=(root.name,root.version) or 'checksum' in rec:
                raise CargoSourceError('unexpected path/workspace package in the source lock')
            root_seen=True;packages[name]=root;continue
        if rec['source']!=REGISTRY or type(rec.get('checksum')) is not str:
            raise CargoSourceError('only the checksum-authenticated registry source is supported')
        p=name+'-'+version
        raw=_package_file(store,('registry',p+'.crate'))
        packages[name]=_source(raw,rec['checksum'],p,budget,expected=(name,version))
    for rec in rows:
        deps=rec.get('dependencies',[])
        if (type(deps) is not list or any(type(name) is not str or name not in packages for name in deps)
                or len(deps)!=len(set(deps))):
            raise CargoSourceError('nonunique, unknown or version-qualified Cargo.lock dependency ID')
    if not root_seen:
        raise CargoSourceError('root package absent from Cargo.lock')
    return CargoSourceClosure(root,MappingProxyType(packages),_immutable(lock),root_pin)


def archive_identities(closure):
    """Compact identities; the original 15 MiB source set need not stay live."""
    return tuple(
        ((('upstream.tar.gz',) if p is closure.root else ('registry',p.name+'-'+p.version+'.crate')),
         p.compressed_bytes,p.archive_sha256)
        for p in closure.packages.values()
    )


def verify_cargo_source_archives(store, identities):
    """Recheck the exact archives consumed. Unselected store paths are never read."""
    for parts,size,checksum in identities:
        data=_package_file(store,parts)
        if len(data)!=size or hashlib.sha256(data).hexdigest()!=checksum:
            raise CargoSourceError('authenticated package archive changed: '+parts[-1])
