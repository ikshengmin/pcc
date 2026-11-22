"""A pinned stdlib source plan supplies bytes, never an external compiler or runtime."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go_packages
from pcc.frontends.go.source_plan import GoSourcePlanError, LockedGoSourcePlan, TARGET, load_go_source_plan
from tests.go.owned_execution import Execution, compile_module, compile_reference_packages, run, write_artifact
from tests.go.test_execution import forbid_external_compiler

RECIPE=Path(__file__).resolve().parents[2]/'projects/go-enry-2.9.6/pcc-go1.27.3-linux-amd64-stdlib.json'
PIN='5bbd292ccfe72450743154637f8abfabcd2d90954a6712d5d2edcfd4f6f44f3a'
ROOT='example.org/input'
MAIN={'main':(('main.go','package main;import dep "example.org/input";func Result()int{return dep.Original()};func main(){if Result()!=42{panic("wrong original source")}}'),)}
DEP=b'package input;func Original()int{return 42}'
def sha(b):return hashlib.sha256(b).hexdigest()
def raw(m):return json.dumps(m,sort_keys=True,separators=(',',':')).encode()
def load(root,m):
    b=raw(m)
    return load_go_source_plan(root,b,expected_sha256=sha(b))
def write(root,p,b):
    f=root/p;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
def tiny(root, *, assembly=False, name=ROOT):
    # Synthetic source supply for API tests, not a Go-distribution pin.
    files={'VERSION':b'go1.27.3\n','LICENSE':b'test fixture\n','src/'+name+'/data.go':DEP}
    row={'go':['data.go'],'asm':[],'header':[]};inc={}
    if assembly:
        fl='src/'+name+'/entry.s';row['asm']=['entry.s']
        files[fl]=b'#include "textflag.h"\n#include "go_asm.h"\n'
        files['pkg/include/textflag.h']=b'/* original test header */\n'
        inc[name]=[{'from':fl,'original':'pkg/include/textflag.h'},{'from':fl,'generated':'go_asm.h'}]
    for f,b in files.items():write(root,f,b)
    return {'schema':1,'release':'go1.27.3','distribution':'go1.27.3.linux-amd64.tar.gz',
        'distribution_sha256':sha(b'test-only archive'),'selector_sha256':sha(b'test-only selector'),
        'target':deepcopy(TARGET),'roots':[name],'packages':{name:{'imports':[],'files':row}},
        'assembly_includes':inc,'files':{f:[len(b),sha(b)] for f,b in files.items()}}

def test_public_compiler_keeps_explicit_supply_and_default_no_host_fallback(monkeypatch,tmp_path):
    recipe=tiny(tmp_path)
    monkeypatch.setenv('GOROOT',str(tmp_path))
    with pytest.raises(GoDiagnostic,match='import source unavailable'):
        compile_go_packages(MAIN)
    with pytest.raises(TypeError,match='use load_go_source_plan'):LockedGoSourcePlan()
    with pytest.raises(GoSourcePlanError,match='checked original'):
        compile_go_packages(MAIN,standard_library={})
    with pytest.raises(GoSourcePlanError,match='original.*checksum|manifest checksum'):
        load_go_source_plan(tmp_path,raw(recipe),expected_sha256='0'*64)
    p=load(tmp_path,recipe)
    assert p.goamd64 == 'v1'
    with pytest.raises(GoSourcePlanError,match='replaces locked'):
        compile_go_packages({**MAIN,ROOT:[('other.go','package input')]},standard_library=p)
    with pytest.raises(GoSourcePlanError,match='target'):
        compile_go_packages(MAIN,standard_library=p,target_triple='arm64-apple-darwin')
    with pytest.raises(TypeError):p.original_files['VERSION']=b'other'
    with pytest.raises(TypeError):p.packages[ROOT]=None

@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_full_verified_nonruntime_source_snapshot_produces_real_owned_elf_and_native_reference(tmp_path,monkeypatch):
    root=tmp_path/'release';recipe=tiny(root)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        snap=load(root,recipe)
        # If the compiler reopens a source path after verifying it, this
        # ordinary valid replacement silently changes the actual result.
        write(root,'src/'+ROOT+'/data.go',DEP.replace(b'42',b'59'))
        mod=compile_go_packages(MAIN,standard_library=snap)
        value=compile_module(mod,observe_result=True);main=compile_module(mod)
    observed=tmp_path/'observed';write_artifact(value,observed)
    assert run(observed)==Execution(0,(42).to_bytes(8,'little'),b'')
    path=tmp_path/'owned';write_artifact(main,path)
    ref=compile_reference_packages({**MAIN,ROOT:(('data.go',DEP.decode()),)},tmp_path/'ref',
        compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(path)==run(ref)==Execution(0,b'',b'')

@pytest.mark.parametrize('change,diagnostic',[
    ('root','inventory'),('missing-import','incomplete import'),('wrong-size','wrong-size'),
    ('same-size','checksum'),('header-bytes','checksum'),('extra-file','inventory'),
    ('missing-go','inventory'),('escaping','invalid'),('escaping-header','escapes'),
    ('orphan-header','unreachable'),('cycle','cycle'),('native-omitted','inventory'),
    ('bad-target','target'),('duplicated-name','invalid'),('oversize','identity'),
])
def test_corrupt_packages_files_headers_and_target_are_not_partial_supply(change,diagnostic,tmp_path):
    m=tiny(tmp_path,assembly=True);row=m['packages'][ROOT];go='src/'+ROOT+'/data.go'
    if change=='root':m['roots']=['not-selected']
    elif change=='missing-import':row['imports']=['missing']
    elif change=='wrong-size':write(tmp_path,go,b'abc')
    elif change=='same-size':write(tmp_path,go,DEP.replace(b'42',b'59'))
    elif change=='header-bytes':write(tmp_path,'pkg/include/textflag.h',b'/* replaced test header */\n')
    elif change=='extra-file':m['files']['src/unused/x.go']=[0,sha(b'')]
    elif change=='missing-go':del m['files'][go]
    elif change=='escaping':row['files']['go']=['../data.go']
    elif change=='escaping-header':m['assembly_includes'][ROOT][0]['original']='src/other/header.h'
    elif change=='orphan-header':m['assembly_includes'][ROOT][0]['from']='src/local_unrelated/foo.s'
    elif change=='cycle':row['imports']=[ROOT]
    elif change=='native-omitted':row['files']['asm']=[];m['assembly_includes']={}
    elif change=='bad-target':m['target']['goamd64']='v3'
    elif change=='duplicated-name':row['files']['go'].append('data.go')
    elif change=='oversize':m['files'][go][0]=1<<29
    with pytest.raises(GoSourcePlanError,match=diagnostic):load(tmp_path,m)

def test_symlinked_directory_does_not_read_outside_explicit_supply(tmp_path):
    root=tmp_path/'release';m=tiny(root)
    directory=root/'src'/ROOT
    moved=tmp_path/'other';directory.rename(moved);directory.symlink_to(moved,target_is_directory=True)
    with pytest.raises(GoSourcePlanError,match='explicit root'):load(root,m)

def test_source_only_module_or_standin_cannot_discharge_assembly_or_layout_abi(tmp_path,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    m=tiny(tmp_path,assembly=True)
    forbid_external_compiler(monkeypatch)
    snap=load(tmp_path,m)
    assert snap.unresolved({ROOT})==((ROOT,'Go assembly/ABI owner',('src/'+ROOT+'/entry.s',)),
        (ROOT,'PCC-generated layout header',('go_asm.h',)))
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unowned native inputs yielded an IR result'))
    with pytest.raises(GoSourcePlanError,match='Go assembly/ABI owner.*PCC-generated layout header'):
        compile_go_packages(MAIN,standard_library=snap)

def test_locked_runtime_text_is_not_an_alternate_implementation(tmp_path):
    m=tiny(tmp_path,name='runtime')
    go='src/runtime/data.go'
    value=b'package runtime;func Pretend()int{return 42}'
    write(tmp_path,go,value);m['files'][go]=[len(value),sha(value)]
    plan=load(tmp_path,m)
    assert plan.unresolved({'runtime'})==(('runtime','PCC runtime/intrinsic owner',()),)
    with pytest.raises(UnsupportedGo,match='does not implement Pretend'):
        compile_go_packages({'main':[('main.go','package main;import "runtime";func main(){_=runtime.Pretend()}')]},
                            standard_library=plan)

def test_genuine_selected_go_sources_must_agree_with_the_pinned_imports(tmp_path):
    m=tiny(tmp_path);go='src/'+ROOT+'/data.go';v=b'package input;import _ "unknown";func Original()int{return 42}'
    write(tmp_path,go,v);m['files'][go]=[len(v),sha(v)]
    with pytest.raises(GoSourcePlanError,match='imports differ'):
        compile_go_packages(MAIN,standard_library=load(tmp_path,m))

def test_release_recipe_preserves_mixed_and_compiler_owned_inputs_as_unsatisfied():
    b=RECIPE.read_bytes();assert sha(b)==PIN
    m=json.loads(b)
    assert m['roots']==['bytes','regexp'] and len(m['packages'])==49
    assert {r:sum(len(v['files'][r]) for v in m['packages'].values()) for r in ('go','asm','header')}=={
        'go':396,'asm':31,'header':5}
    assert len(m['files'])==437 and sum(v[0] for v in m['files'].values())==4314449
    assert m['files']['src/bytes/iter.go'][1]=='628f8dc51af2c70325050c0125340ec01c6a7da84c53f2f5ae3ec040c7e5fc6f'
    assert {k for k,v in m['assembly_includes'].items() if any('generated' in n for n in v)}=={
        'internal/bytealg','internal/runtime/gc/scan','runtime'}
    assert 'src/runtime/cgo/abi_amd64.h' in m['files'] and 'pkg/include/textflag.h' in m['files']

def test_offline_regeneration_rejects_any_untrusted_archive_before_starting_a_selector(monkeypatch,tmp_path):
    from scripts.pin_go_enry_linux_stdlib import generate
    forbid_external_compiler(monkeypatch)
    path=tmp_path/'wrong-distribution.tar.gz';path.write_bytes(b'not the official Go distribution')
    with pytest.raises(ValueError,match='distribution size'):generate(path)

def test_even_a_complete_assembly_free_plan_cannot_silently_skip_its_declared_roots(tmp_path):
    plan=load(tmp_path,tiny(tmp_path))
    with pytest.raises(GoSourcePlanError,match='did not visit all'):
        compile_go_packages({'main':(('x.go','package main;func main(){}'),)},standard_library=plan)
