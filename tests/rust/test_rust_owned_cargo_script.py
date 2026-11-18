"""Linux native host scripts. No test confuses a control run with orjson."""
import platform
import sys

import pytest

from pcc.frontends.rust.cargo_package_sources import RootArchivePin
from pcc.frontends.rust.cargo_owned_script import ScriptStageError,run_locked_leaf_script
from scripts.ci_orjson_source_frontier import (
    PACKAGE_STORE,UPSTREAM_REVISION,UPSTREAM_TREE,UPSTREAM_TAR_SHA256,load_inputs,
)
from tests.rust.test_rust_locked_cargo_build import _reg,_store

pytestmark=[pytest.mark.integration,pytest.mark.xdist_group(name='rust-owned-cargo-script')]
_native=sys.platform=='linux' and platform.machine().lower() in ('x86_64','amd64') and sys.maxsize==(1<<63)-1


def _script(tmp_path,source,*,timeout=1):
    store,pin,files=_store(tmp_path,"build='build.rs'\n",extra_root={'build.rs':_reg(source.encode())})
    return run_locked_leaf_script(store,pin,'primary',original_files=files,timeout=timeout)


def test_complete_unchanged_script_compiles_links_and_executes_the_actual_branch(tmp_path):
    source="""const VERSION: &'static str = env!("CARGO_PKG_VERSION");
fn twice(n:u32)->u32{n*2u32}
fn main(){let v=VERSION; if twice(21u32)!=42u32 {loop{}}}
"""
    if not _native:
        with pytest.raises(ScriptStageError,match="qualified static Linux"):
            _script(tmp_path,source)
        return
    positive=tmp_path/'positive';positive.mkdir()
    ok=_script(positive,source)
    assert ok.returncode==0 and ok.stdout==ok.stderr==b''
    assert len(ok.object_sha256)==len(ok.executable_sha256)==64 and ok.object_sha256!=ok.executable_sha256
    # One expected numeric value changes. The same genuine code, not an AST
    # evaluator or fabricated zero exit, must now enter its nonterminating loop.
    negative=tmp_path/'negative';negative.mkdir()
    with pytest.raises(ScriptStageError,match='execution: build-script timeout'):
        _script(negative,source.replace('42u32','43u32'),timeout=0.2)


@pytest.mark.parametrize('code,reason',[
    ('fn helper(){}','fn main'),
    ('fn main(x:i32){}','fn main'),
    ('fn main()->u32{42}','fn main'),
    ('fn main(){let x=env!("NOT_PROVIDED");}','NOT_PROVIDED'),
    ('fn main(){println!("cargo:rustc-cfg=not_executed");}','owned std::io'),
])
def test_no_script_abi_invention_or_stdout_from_source_text(tmp_path,code,reason):
    if not _native:
        with pytest.raises(ScriptStageError,match="qualified static Linux"):
            _script(tmp_path,code)
        return
    with pytest.raises(ScriptStageError,match=reason):
        _script(tmp_path,code)


def test_native_api_authenticates_archive_before_rust_or_machine_code(tmp_path):
    store,pin,files=_store(tmp_path,"build='build.rs'\n",extra_root={'build.rs':_reg(b'fn main(){}')})
    path=store/'upstream.tar.gz'
    path.write_bytes(path.read_bytes()+b'changed before execution')
    with pytest.raises(ScriptStageError) as e:
        run_locked_leaf_script(store,pin,'primary',original_files=files)
    assert e.value.stage==('source authentication' if _native else 'selection')
