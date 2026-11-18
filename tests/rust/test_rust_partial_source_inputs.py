"""An explicit target and a pinned environment subset do not imply a Cargo build."""
from dataclasses import FrozenInstanceError
import pytest

from pcc.frontends.rust import CfgOptions, CompileEnvironment, RustError, TargetPlatformFacts, compile_rust
from pcc.frontends.rust.configuration import OwnedRustConfiguration
from pcc.frontends.rust.syntax import Parser

GNU='x86_64-unknown-linux-gnu'
DARWIN='aarch64-apple-darwin'


@pytest.mark.parametrize('target,enabled', [(GNU,True),(DARWIN,False)])
def test_only_abi_facts_are_known_and_each_selected_branch_reaches_the_actual_object(target,enabled):
    facts=TargetPlatformFacts()
    s='''pub fn answer()->u64{
        let mut x=40u64;
        #[cfg(all(any(target_arch="x86_64",target_arch="aarch64"),target_endian="little"))]
        if true { x+=1u64; }
        #[cfg(not(target_os="macos"))] if true {x+=1u64;}
        x
    }'''
    cfg=OwnedRustConfiguration(target,2024,facts)
    p=Parser(s,edition=2024,configuration=cfg); fn,=p.parse()
    one,two=fn.body.conditional_statements
    assert (one.enabled,two.enabled)==(True,enabled)
    assert {k for k,v in cfg.entries}=={'target_arch','target_os','target_endian'}
    result=compile_rust(s,target_triple=target,edition=2024,cfg=facts)
    assert result.emit_object().startswith(b'\x7fELF' if target==GNU else b'\xcf\xfa\xed\xfe')
    assert not result.externally_checked


@pytest.mark.parametrize('predicate',[
    'feature="optional"', 'not(feature="optional")','test','panic="abort"','debug_assertions',
    'target_feature="avx512f"', 'target_pointer_width="64"', 'unknown','target_arch',
    'any(target_os="linux",not(feature="might_be_on"))','all(any(),unknown)',
])
def test_partial_cfg_never_guesses_unobserved_flags_even_under_not_or_short_circuit(predicate):
    s='#[cfg('+predicate+')] pub fn f()->u64{7}'
    with pytest.raises(RustError,match='target-platform-only context'):
        compile_rust(s,edition=2024,cfg=TargetPlatformFacts(),target_triple=GNU)


def test_explicit_complete_cfg_retains_ordinary_closed_world_behavior():
    s='#[cfg(not(feature="off"))] pub fn answer()->u64{42}'
    assert 'answer' in compile_rust(s,cfg=CfgOptions()).symbols


def test_partial_manifest_environment_binds_only_observed_values_and_never_reads_host(monkeypatch):
    monkeypatch.setenv('SECRET_NOT_AN_INPUT','not part of this compilation')
    known={'CARGO_PKG_VERSION':'3.13.0'}
    env=CompileEnvironment.partial(known)
    known['CARGO_PKG_VERSION']='changed'
    p=Parser('fn v(){env!("CARGO_PKG_VERSION");}',environment=env); p.parse()
    expansion,=p.macro_expansions
    assert expansion.value=='3.13.0'
    for name in ('env','option_env'):
        with pytest.raises(RustError,match='absence has not been established'):
            compile_rust('fn f(){'+name+'!("SECRET_NOT_AN_INPUT");}',env=env)
    assert env.unlisted_unknown is True
    with pytest.raises(FrozenInstanceError):
        env.unlisted_unknown=False


def test_environment_empty_string_is_known_and_differs_from_nonexistent():
    env=CompileEnvironment.partial({'KNOWN':''})
    p=Parser('fn f(){env!("KNOWN");}',environment=env);p.parse()
    assert p.macro_expansions[0].value==''
    with pytest.raises(RustError,match='not defined at compile time'):
        compile_rust('fn f(){env!("ABSENT");}',env={})


def test_build_script_style_active_target_cfg_and_static_version_parse_with_missing_runtime_visible():
    s='''fn main(){
        #[cfg(any(target_arch="aarch64",target_os="linux"))]
        if true { println!("target={{enabled}}"); }
        let build_version=env!("CARGO_PKG_VERSION");
        eprintln!("version: {build_version}");
    }'''
    ctx=OwnedRustConfiguration(GNU,2024,TargetPlatformFacts())
    env=CompileEnvironment.partial({'CARGO_PKG_VERSION':'1.0.2'})
    p=Parser(s,source_path='src.rs',edition=2024,configuration=ctx,environment=env);fn,=p.parse()
    assert fn.token.text=='main'
    assert [x.name for x in p.macro_expansions]==['println','env','eprintln']
    print_plan,version,err_plan=p.macro_expansions
    assert print_plan.literal_pieces==('target={enabled}\n',)
    assert version.value=='1.0.2' and err_plan.captures==('build_version',)
    with pytest.raises(RustError,match='requires the owned std::io::_print'):
        compile_rust(s,edition=2024,cfg=TargetPlatformFacts(),env=env,target_triple=GNU)
    with pytest.raises(RustError,match='explicit OwnedRustConfiguration'):
        compile_rust(s,edition=2024,env=env,target_triple=GNU)


@pytest.mark.parametrize('system,machine,order,match', [
    ('linux','x86_64','little',True), ('linux','AMD64','little',True),
    ('linux','aarch64','little',False), ('darwin','x86_64','little',False),
    ('linux','x86_64','big',False),
])
def test_source_gate_labels_its_three_facts_not_a_full_host_triple(system,machine,order,match,monkeypatch):
    from scripts import ci_orjson_source_frontier as gate
    # Both Linux/musl and Linux/glibc match the three platform inputs. Neither
    # may be reported as an observation of the selected GNU source triple.
    records=[]
    for libc in ('glibc','musl'):
        monkeypatch.setattr(gate.platform,'libc_ver',lambda:(libc,'example'))
        records.append(gate.build_script_platform_record(system,machine,order))
    a,b=records
    assert a==b and a['full_host_triple'] is None
    assert a['declared_source_target']==GNU
    assert a['three_platform_facts_match'] is match
    assert a['observed_host']=={'sys_platform':system,'machine':machine.lower(),'byteorder':order}
    assert a['cfg']==({'target_arch':'x86_64','target_os':'linux','target_endian':'little'} if match else {})
