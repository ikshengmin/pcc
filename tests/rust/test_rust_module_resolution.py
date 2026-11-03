"""Scoped owned item resolution, not source flattening or core acceptance.

Rust Reference: items/modules.html, paths.html, visibility-and-privacy.html,
names/scopes.html. Outlined modules currently feed facts-only owned Sema;
inline reference lowering still requires the real whole-source checking adapter.
"""
from pathlib import Path
import json
import hashlib
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.module_resolution import analyze_crate_syntax
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('modules_scopes', 'modules_generic_nominals', 'modules_enum_paths',
            'modules_private_generic', 'modules_shared_reference', 'modules_mutable_reference')
VALUE_PROGRAMS, REFERENCE_PROGRAMS = PROGRAMS[:4], PROGRAMS[4:]
PRIVATE_CALLER_ORIGINAL = ('mod a{pub fn id<T>(x:T)->T{x}}'
    'mod b{struct R{x:u64} pub fn f()->u64{let r=crate::a::id(R{x:42});r.x}}'
    'pub fn answer()->u64{b::f()}')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def compile_source(text, **options):
    return compile_rust(text, experimental_rustc_checked=True, **options)


def analyze(sources):
    return analyze_crate_syntax(parse_crate_sources('src/lib.rs', sources,
                                                   crate_name='example', edition=2021))


@pytest.mark.parametrize('name', VALUE_PROGRAMS)
@pytest.mark.parametrize('target,magic', [('x86_64-unknown-linux-gnu', b'\x7fELF'),
                                        ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe')])
def test_scoped_value_originals_emit_real_owned_objects(name, target, magic):
    result = compile_source(source(name), target_triple=target)
    assert result.emit_object().startswith(magic)
    assert 'answer' in result.symbols


def test_outlined_modules_feed_distinct_existing_semantic_and_instance_tables():
    sources = {
        'src/lib.rs': 'mod a; mod b; fn f()->u64{a::id(a::make()).x+b::id(b::make()).x}',
        'src/a.rs': 'pub struct R{pub x:u64} pub fn make()->R{R{x:40}} pub fn id<T>(x:T)->T{x}',
        'src/b.rs': 'pub struct R{pub x:u64} pub fn make()->R{R{x:2}} pub fn id<T>(x:T)->T{x}',
    }
    checker = analyze(sources)
    records = tuple(checker.generics.definitions.values())
    assert len(records) == 2 and {r.token.text for r in records} == {'R'}
    assert len({r.item_identity for r in records}) == 2
    assert len({r.declaration_name for r in records}) == 2
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 2
    assert len({e.value.declaration_name for e in entries}) == 2
    assert len({e.value.instance_arguments for e in entries}) == 2
    assert all(e.value.item_identity.module.components in {('a',), ('b',)} for e in entries)
    assert checker.module_resolution.crate.sources['src/a.rs'].text == sources['src/a.rs']


def test_generic_replay_resolves_in_declaration_module_not_caller():
    text = ('mod a{fn same()->u64{40} pub fn id<T>(x:T)->u64{same()}}'
            'mod b{fn same()->u64{2} pub fn f()->u64{crate::a::id(1u64)+same()}}'
            'fn answer()->u64{b::f()}')
    checker = analyze({'src/lib.rs': text})
    instance = next(iter(checker.function_generics.instances.entries.values())).value
    call = instance.body.children[-1]
    assert call.kind == 'call'
    assert call.binding.item_identity.module.components == ('a',)
    assert call.binding.token.text == 'same'


INVALID = (
    ('private-function', 'mod a{fn f()->u64{1}} fn g()->u64{a::f()}', 'private item', 'E0603'),
    ('private-module', 'mod a{mod b{pub fn f(){}}} fn g(){a::b::f();}', 'private item', 'E0603'),
    ('private-field-read', 'mod a{pub struct R{x:u64} pub fn make()->R{R{x:1}}} fn g()->u64{a::make().x}', 'private field', 'E0616'),
    ('private-field-build', 'mod a{pub struct R{x:u64}} fn g(){let r=a::R{x:1};}', 'private field', 'E0451'),
    ('private-result', 'mod a{struct R{pub x:u64} pub fn make()->R{R{x:1}}} fn g(){a::make();}', 'private type', 'private'),
    ('nominal', 'mod a{pub struct R{pub x:u64} pub fn take(x:R){}} mod b{pub struct R{pub x:u64}} fn g(){a::take(b::R{x:1});}', 'type mismatch', 'E0308'),
    ('no-parent-glob', 'fn f(){} mod a{pub fn g(){f();}}', 'unknown value-namespace', 'E0425'),
    ('unused-abstract', 'mod a{pub fn unused<T>(x:T){missing();}}', 'unknown value-namespace', 'E0425'),
    ('local-shadow', 'mod a{pub fn f(){}} fn g(){let f=0u64;f();}', 'unshadowed', 'E0618'),
    ('too-many-super', 'mod a{pub fn f(){super::super::f();}}', 'too many leading super', 'E0433'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_scoped_semantic_errors_reject_before_ir(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('invalid module reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
def test_reference_modules_cannot_use_the_boolean_as_certification(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked module reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source(source(name))


def test_outlined_reference_context_never_treats_root_as_complete_certification(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('analysis helper invoked IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        analyze({'src/lib.rs': 'mod a;', 'src/a.rs': 'pub fn f(x:&u64)->u64{*x}'})


@pytest.mark.parametrize('text,message', [
    ('mod a{pub trait T{fn f();}}', 'module-scoped trait'),
    ('mod a{struct R{x:u64} impl R{fn f(self){}}}', 'module-scoped trait'),
    ('mod a{pub extern "C" fn f(){}}', 'foreign/export ABI'),
    ('mod a{} use a::f;', "unknown use target"),
    ('mod a;', 'module source missing'),
])
def test_unimplemented_module_context_forms_fail_explicitly(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unsupported module reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_unused_nested_reference_signature_is_checked_before_owned_lowering(tmp_path, monkeypatch):
    import subprocess
    text = 'mod a{pub fn f(x:&u64)->u64{*x}}'
    check = certify_source(text, tmp_path/'actual-check')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile invoked tool'))
        result = compile_source(text, rustc_check=check)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.externally_checked
    assert (tmp_path/'actual-check/original.rs').read_bytes() == text.encode('utf-8')
    assert check.provenance.source_sha256 == hashlib.sha256(text.encode('utf-8')).hexdigest()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_unchanged_module_originals_match_actual_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_the_exact_scoped_invalid_originals(tmp_path, monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError, compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('rustc-invalid module reached IR'))
    tool = toolchain()
    compiler = str((Path(tool.root)/'bin/rustc').resolve())
    for name, text, _, code in INVALID:
        directory = tmp_path/name
        with pytest.raises(RustExternalCheckError, match=code):
            compile_rust_checked(text, toolchain=tool, evidence_directory=directory)
        assert (directory/'original.rs').read_text() == text
        processes = json.loads((directory/'processes.json').read_text())
        assert processes[0]['command'] == [compiler, '-vV']
        assert processes[1]['command'] == [compiler, '--print=sysroot']
        assert processes[2]['command'] == [compiler, '--edition=2021', '--target=x86_64-unknown-linux-gnu',
            '-Coverflow-checks=off', '--emit=metadata', '--crate-type=lib', '--crate-name=certified_input',
            str((directory/'original.rs').resolve()), '-o', str((directory/'checked.rmeta').resolve())]
        assert len(processes) == 3 and all(p['status'] == 'COMPLETE' for p in processes)
        assert [p['exit_code'] for p in processes[:2]] == [0, 0]
        assert processes[-1]['exit_code'] > 0
        assert (directory/'rustc-version.stdout').read_text() == tool.verbose_version
        assert Path((directory/'rustc-sysroot.stdout').read_text().strip()).resolve() == Path(tool.root).resolve()
        for label, process in zip(('rustc-version', 'rustc-sysroot', 'check'), processes):
            for stream in ('stdout', 'stderr'):
                assert hashlib.sha256((directory/(label+'.'+stream)).read_bytes()).hexdigest() == process[stream+'_sha256']
        assert code in (directory/'check.stderr').read_text()
        assert not (directory/'checked.rmeta').exists()


def test_outlined_semantic_failure_keeps_original_file_and_token_coordinate():
    sources = {'src/lib.rs': 'mod a;', 'src/a.rs': '\npub fn unused(){missing();}'}
    with pytest.raises(RustError, match=r'^src/a\.rs: 2:17: unknown value-namespace'):
        analyze(sources)


@pytest.mark.parametrize('text,message', [
    ('mod T{pub struct R{pub x:u64}} fn unused<T>(x:T::R){}', 'associated types on generic parameters'),
    ('mod T{pub fn f(){}} fn unused<T>(x:T){T::f();}', 'associated items on generic parameters'),
])
def test_type_parameter_scope_cannot_recapture_a_same_named_module(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('generic scope was ignored'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


def test_exact_private_caller_generic_identity_original_is_legal():
    # Exact independent-review original, retained separately from the observer.
    checker = analyze({'src/lib.rs': PRIVATE_CALLER_ORIGINAL})
    instance = next(iter(checker.function_generics.instances.entries.values())).value
    assert instance.item_identity.module.components == ('a',)
    parameter_type = instance.params[0][1]
    assert checker.generics.definitions[parameter_type].item_identity.module.components == ('b',)
    assert checker.generics.definitions[parameter_type].token.text == 'R'


def test_unused_abstract_body_still_rejects_a_lexically_private_type():
    text = ('mod b{struct R{x:u64}}'
            'mod a{pub fn bad<T>(x:T){let r=crate::b::R{x:1};}}')
    with pytest.raises(RustError, match='private item'):
        analyze({'src/lib.rs': text})


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_accepts_exact_private_caller_original_before_owned_compile(tmp_path, monkeypatch):
    import subprocess
    check = certify_source(PRIVATE_CALLER_ORIGINAL, tmp_path/'actual-check')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile invoked tool'))
        result = compile_source(PRIVATE_CALLER_ORIGINAL, rustc_check=check)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.externally_checked
    assert (tmp_path/'actual-check/original.rs').read_bytes() == PRIVATE_CALLER_ORIGINAL.encode('utf-8')
    assert check.provenance.source_sha256 == hashlib.sha256(PRIVATE_CALLER_ORIGINAL.encode('utf-8')).hexdigest()


@pytest.mark.parametrize('text,message', [
    ('mod a{pub fn id<T>(x:T)->T{x}} fn f(){let item=a::id::<u64>;}', 'direct call'),
    ('mod a{pub enum E<T>{V(T)}} fn f(x:a::E<u64>)->u64{match x{a::E::V::<u64>(n)=>n}}', 'enum variant pattern segments'),
    ('mod a{pub enum E<T>{V(T)}} fn f(x:a::E<u64>)->u64{match x{a::E::V::<bool>(n)=>n}}', 'enum variant pattern segments'),
])
def test_last_segment_type_arguments_are_not_silently_discarded(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unsupported path reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)
