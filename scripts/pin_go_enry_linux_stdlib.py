"""Pin Go 1.27.3 Linux-amd64/v1 normal standard package sources.

Offline maintenance, NOT a compiler path. Pass the official binary distribution
tarball and an output file. The official go command
only selects the target graph; all selected bytes, support headers and the
tool itself come from a fresh extraction of the same checked archive, never
an independently mutable installation. No downloaded or produced
archive/object is used by PCC. Generated go_asm.h is a pending PCC obligation.
The default roots are bytes and regexp (Go Enry); --root selects an explicit
normal package list, not test build instances.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import io
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

RELEASE='go1.27.3'
ARCHIVE_SHA='65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40'
GO_SHA='5f5eb6d0b2fb6aa0bb5d5f8c5f1c944e0de955c925f0111af92a9d8f79680b5b'
ARCHIVE_BYTES=70_588_678
ROOTS=('bytes','regexp')
TRIPLE='x86_64-unknown-linux-gnu'
FIELDS={'GoFiles':'go','SFiles':'asm','HFiles':'header'}
NONDEFAULT=('CgoFiles','CFiles','CXXFiles','MFiles','FFiles','SwigFiles','SwigCXXFiles','SysoFiles','EmbedFiles')
INCLUDE=re.compile(rb'^\s*#\s*include\s+"([^"\r\n]+)"\s*(?://.*)?$',re.M)

def sha(value):
    return hashlib.sha256(value).hexdigest()

def digest_file(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def selected_rows(data):
    decoder=json.JSONDecoder(); result={}
    while data.strip():
        data=data.lstrip();row,n=decoder.raw_decode(data);data=data[n:]
        name=row['ImportPath']
        if name in result or not row.get('Standard') or row.get('Incomplete') or row.get('Error') or row.get('DepsErrors'):
            raise ValueError('invalid or duplicate selected official package: '+name)
        result[name]=row
    return result

def generate(archive, *, roots=ROOTS):
    roots = tuple(roots)
    if (not roots or len(set(roots)) != len(roots)
            or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", name)
                   or name in ('all', 'std', 'cmd') or '...' in name
                   or any(part in ("", ".", "..") for part in name.split("/")) for name in roots)):
        raise ValueError('normal Go package roots must be unique standard import paths')
    # Check and extract the *same* bounded immutable bytes. Checking only the
    # files go list already selected would not detect an omitted candidate in
    # another installation. No source tree, backend, or tool from that
    # installation may influence selection.
    with archive.open('rb') as f:
        if os.fstat(f.fileno()).st_size!=ARCHIVE_BYTES:
            raise ValueError('wrong official Go linux-amd64 distribution size')
        raw=f.read(ARCHIVE_BYTES+1)
    if len(raw)!=ARCHIVE_BYTES or sha(raw)!=ARCHIVE_SHA:
        raise ValueError('wrong official Go linux-amd64 distribution archive')
    with tempfile.TemporaryDirectory(prefix='pcc-go-release-') as tmp:
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as original:
            original.extractall(tmp,filter='data')
        return _select(Path(tmp)/'go', roots=roots)


def _select(root, *, roots=ROOTS):
    root=root.resolve(strict=True); tool=root/'bin/go'
    if digest_file(tool)!=GO_SHA:
        raise ValueError('wrong official Go package selector')
    with tempfile.TemporaryDirectory(prefix='pcc-go-pin-') as tmp:
        env=dict(PATH=str(root/'bin')+os.pathsep+os.defpath,HOME=tmp,GOCACHE=tmp+'/cache',
                 GOROOT=str(root),GOOS='linux',GOARCH='amd64',GOAMD64='v1',CGO_ENABLED='0',
                 GOENV='off',GOWORK='off',GO111MODULE='off',GOTOOLCHAIN='local',GOPROXY='off',
                 GOSUMDB='off',GOFLAGS='',GOEXPERIMENT='')
        proc=subprocess.run([str(tool),'list','-deps','-json',*roots],check=True,
                            env=env,cwd=tmp,capture_output=True,timeout=45)
    rows=selected_rows(proc.stdout.decode())
    if not set(roots)<=rows.keys() or any(n not in rows for r in rows.values() for n in r.get('Imports',())):
        raise ValueError('incomplete selected import graph')
    files={};pkgs={};needed={}
    def add(path):
        if path not in files:
            real=(root/path).resolve(strict=True)
            if not real.is_relative_to(root):
                raise ValueError('original source escapes distribution: '+path)
            data=real.read_bytes()
            files[path]=[len(data),sha(data)]
        return (root/path).read_bytes()
    add('VERSION');add('LICENSE')
    if (root/'VERSION').read_bytes().splitlines()[0]!=RELEASE.encode():
        raise ValueError('wrong go release identity')
    for name,row in sorted(rows.items()):
        prefix='src/'+name+'/'
        if Path(row['Dir']).resolve(strict=True)!=(root/'src'/name).resolve(strict=True):
            raise ValueError('source path outside selected package: '+name)
        if any(row.get(k) for k in NONDEFAULT):
            raise ValueError('additional native/embed source role requires qualification: '+name)
        roles={key:[] for key in FIELDS.values()}
        for label,role in FIELDS.items():
            for basename in row.get(label,()):
                if Path(basename).name!=basename or basename in roles[role]:
                    raise ValueError('invalid or repeated package filename')
                add(prefix+basename);roles[role].append(basename)
        pkgs[name]={'imports':sorted(row.get('Imports',())),'files':roles}
        includes=[]
        def headers(path,seen):
            if path in seen:return
            seen.add(path); data=add(path)
            for line in data.splitlines():
                if not re.match(rb'^\s*#\s*include\b',line):continue
                match=INCLUDE.fullmatch(line)
                if not match:raise ValueError('unsupported assembler include syntax: '+path)
                header=match[1].decode()
                if header=='go_asm.h':
                    includes.append({'from':path,'generated':'go_asm.h'});continue
                if Path(header).is_absolute() or '..' in Path(header).parts:
                    raise ValueError('escaping assembly header')
                # Go asm searches the source directory before -I GOROOT/pkg/include.
                options=[(Path(path).parent/header).as_posix(), 'pkg/include/'+header]
                loc=next((r for r in options if (root/r).is_file()),None)
                if loc is None:raise ValueError('missing assembler include: '+path+': '+header)
                includes.append({'from':path,'original':loc});headers(loc,seen)
        seen=set()
        for nm in roles['asm']:headers(prefix+nm,seen)
        if includes:needed[name]=sorted(includes,key=lambda x:(x['from'],x.get('original',''),x.get('generated','')))
    return {'schema':1,'release':RELEASE,'distribution':'go1.27.3.linux-amd64.tar.gz',
            'distribution_sha256':ARCHIVE_SHA,'selector_sha256':GO_SHA,
            'target':{'triple':TRIPLE,'goos':'linux','goarch':'amd64','goamd64':'v1','cgo':False,
                      'build_tags':[],'goexperiment':''},
            'roots':list(roots),'packages':pkgs,'assembly_includes':needed,'files':dict(sorted(files.items()))}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('--root', action='append', help='repeat for each normal package root')
    opts=p.parse_args();v=generate(opts.archive, roots=tuple(opts.root) if opts.root else ROOTS)
    opts.output.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
