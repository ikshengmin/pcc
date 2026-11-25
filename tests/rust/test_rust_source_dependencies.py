"""Direct source externs: Rust Reference names.preludes.extern / visibility-and-privacy.

The positive dependency and consumer are unchanged Meson Rust 2018 source
files. Its separate rustc reference additionally uses Meson's original main.
No test supplies or substitutes a core library.
"""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json
import os
import platform
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, RustSourceDependency, compile_rust_crate
from tests.rust.certification_support import actual_check_prerequisite
from tests.rust.str_execution import _reference_command, _reference_environment


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'projects/numpy-2.4.4/vendored-meson/meson/test cases/rust/21 transitive dependencies'
TARGET = 'x86_64-unknown-linux-gnu'
ON_TARGET = sys.platform.startswith('linux') and platform.machine().lower() in {'x86_64', 'amd64'}
NATIVE = None if ON_TARGET else 'native observer requires Linux x86-64'
A = '60209049ed8206d5e421b7e616b02db12acd1a45bd5e44535707df3be8f85055'
B = 'ead90bfb9aa125133f27b44b63ebb8dc298d4db6f4ea58e395e45c201d1bd963'


def originals():
    first = (PROJECT / 'liba/lib.rs').read_bytes()
    second = (PROJECT / 'libb/lib.rs').read_bytes()
    assert (sha256(first).hexdigest(), sha256(second).hexdigest()) == (A, B)
    return RustSourceDependency('liba', 'lib.rs', 2018, TARGET, first, A), second.decode()


def leaf(text, name='leaf', *, edition=2018):
    raw = text.encode()
    return RustSourceDependency(name, 'lib.rs', edition, TARGET, raw, sha256(raw).hexdigest())


def consumer(source, providers, *, edition=2018, **options):
    return compile_rust_crate('lib.rs', {'lib.rs': source}, crate_name='libb', edition=edition,
                              target_triple=TARGET, extern_sources=providers, **options)


def test_separate_scope_and_dependency_identity_change_the_actual_link_names():
    text = 'pub fn foo()->i32{5} pub fn bar()->i32{foo()+first::foo()+other::foo()}'
    a = leaf('pub fn foo()->i32{6}', 'provider', edition=2015)
    b = leaf('pub fn foo()->i32{7}', 'provider', edition=2021)
    out = consumer(text, {'first': a, 'other': b})
    libraries = dict(out.source_dependencies)
    assert set(out.symbols) == {'foo', 'bar'}
    assert [libraries[k].source_crate_id.edition for k in ('first', 'other')] == [2015, 2021]
    symbols = [out.symbols['foo']] + [libraries[k].symbols['foo'] for k in ('first', 'other')]
    assert len(set(symbols)) == 3
    for symbol in symbols:
        assert any('call i32' in line and symbol in line for line in out.ir_text.splitlines())
    # Binding aliases are part of the consuming unit's identity; dict order
    # isn't. The provider keeps its canonical name, never the consumer alias.
    assert consumer(text, {'other': b, 'first': a}).symbols == out.symbols
    swapped = consumer(text, {'first': b, 'other': a})
    assert swapped.source_crate_id != out.source_crate_id
    assert swapped.symbols['bar'] != out.symbols['bar']
    assert dict(swapped.source_dependencies)['other'].symbols == libraries['first'].symbols


def test_local_module_shadows_an_extern_and_a_named_import_keeps_the_real_function():
    src = leaf('pub fn read()->i32{19}')
    shadow = consumer('mod e{pub fn read()->i32{2}} pub fn result()->i32{e::read()}', {'e': src})
    symbol = dict(shadow.source_dependencies)['e'].symbols['read']
    assert symbol not in shadow.ir_text
    named = consumer('use e::read as f; pub fn result()->i32{f()}', {'e': src})
    assert any('call i32' in line and symbol in line for line in named.ir_text.splitlines())


STRICT_CONSUMER = '//! Consumer docs.\n#![deny(missing_docs)]\n'


@pytest.mark.parametrize(('binding', 'call'), [
    ('use dep::f;', 'f()'),
    ('use dep;', 'dep::f()'),
    ('pub use dep::f as exposed;', 'exposed()'),
    ('pub use dep as exposed;', 'exposed::f()'),
    ('pub use dep;', 'dep::f()'),
])
def test_missing_docs_resolves_imports_with_the_checker_and_lints_only_here(binding, call):
    # The consumer documents its own API. Neither a foreign definition with
    # no docs nor its new local spelling is a new item defined here.
    dep = leaf('pub fn f()->i32{27}')
    out = consumer(STRICT_CONSUMER + binding + '\n/// Read the leaf.\npub fn read()->i32{' + call + '}',
                   {'dep': dep})
    provider = dict(out.source_dependencies)['dep']
    assert set(out.symbols) == {'read'} and set(provider.symbols) == {'f'}
    assert any('call i32' in line and provider.symbols['f'] in line for line in out.ir_text.splitlines())


def test_a_single_segment_extern_use_cannot_hide_a_conflict_cycle_or_private_scope():
    dep = leaf('pub fn f()->i32{27}')
    with pytest.raises(RustError, match="duplicate imported type-namespace item 'dep'"):
        consumer('mod dep{} use dep;', {'dep': dep})
    with pytest.raises(RustError, match='cyclic use bindings require fixed-point import resolution'):
        consumer('use self::next as dep; use self::dep as next;', {'dep': dep})
    with pytest.raises(RustError, match="private use binding 'dep' is inaccessible from this module"):
        consumer('mod inner{use dep;} pub fn bad()->i32{inner::dep::f()}', {'dep': dep})


# Rust Reference names.resolution.expansion.imports.ambiguity.glob-vs-outer.
# Both compilers below consume these exact consumer and provider bytes.
GLOB_LEAF = b'pub fn f()->i32{27}\n'
LOCAL_GLOB = 'mod local{pub mod dep{pub fn f()->i32{2}}}\nuse local::*;\n'
PICK = 'use dep::f as pick;\npub fn read()->i32{pick()}'
GLOB_EXTERN = (
    # id, exact consumer, PCC definition (None means E0659), reference value
    ('member', LOCAL_GLOB + PICK, None, None),
    ('self-name', LOCAL_GLOB + 'use dep;\npub fn read()->i32{dep::f()}', None, None),
    ('public-self-name', LOCAL_GLOB + 'pub use dep;\npub fn read()->i32{dep::f()}', None, None),
    ('same-extern-identity', 'mod bridge{pub use dep;}\nuse crate::bridge::*;\n' + PICK, 'extern', 27),
    ('ordinary-expression', LOCAL_GLOB + 'pub fn read()->i32{dep::f()}', 'local::dep::f', 2),
    ('qualified', LOCAL_GLOB + 'use crate::local::dep::f as pick;\npub fn read()->i32{pick()}',
     'local::dep::f', 2),
    ('strong-local', 'mod dep{pub fn f()->i32{3}}\n' + LOCAL_GLOB + PICK, 'dep::f', 3),
    ('strong-named-import', 'mod strong{pub mod a{pub fn f()->i32{4}}}\nuse crate::strong::a as dep;\n'
     + LOCAL_GLOB + PICK, 'strong::a::f', 4),
    ('unrelated-glob', 'mod local{pub fn unrelated()->i32{6}}\nuse local::*;\n' + PICK, 'extern', 27),
)


@pytest.mark.parametrize(('label', 'source', 'definition', 'value'), GLOB_EXTERN, ids=[row[0] for row in GLOB_EXTERN])
def test_glob_vs_extern_is_an_import_ambiguity_between_distinct_definitions(label, source, definition, value,
                                                                            monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned resolution'))
    dep = leaf(GLOB_LEAF.decode(), name='dep')
    if definition is None:
        with pytest.raises(RustError) as error:
            consumer(source, {'dep': dep})
        column = 5 if label == 'public-self-name' else 1
        assert str(error.value).endswith(
            f"lib.rs: 3:{column}: 'dep' is ambiguous between a glob import and an extern-prelude crate (rustc E0659)")
        return
    out = consumer(source, {'dep': dep})
    target = dict(out.source_dependencies)['dep'].symbols['f'] if definition == 'extern' else out.symbols[definition]
    # Only read calls another function; all provider/local f bodies are
    # distinct literal returns. Assert which real definition was called.
    calls = [line for line in out.ir_text.splitlines() if 'call i32' in line]
    assert len(calls) == 1 and target in calls[0], (value, calls)


def test_macros_inherit_across_a_local_outline_but_not_across_the_source_crate_boundary():
    # The leaf's textual macros live only in its crate. The consumer's own
    # macro is in scope at its outlined module, and $crate is the consumer.
    dep = leaf('macro_rules! only_leaf { () => { 27i32 }; }\npub fn f()->i32{only_leaf!()}')
    root = (STRICT_CONSUMER + 'macro_rules! read_both { () => { $crate::offset() + f() }; }\n'
            'fn offset()->i32{4}\n/// Reader module.\npub mod inner;')
    inner = 'use dep::f;\n/// Read both.\npub fn read()->i32{read_both!()}'
    result = compile_rust_crate('lib.rs', {'lib.rs': root, 'inner.rs': inner}, crate_name='libb',
                                edition=2018, target_triple=TARGET, extern_sources={'dep': dep})
    provider = dict(result.source_dependencies)['dep']
    assert set(result.symbols) == {'offset', 'inner::read'} and set(provider.symbols) == {'f'}
    for symbol in (result.symbols['offset'], provider.symbols['f']):
        assert any('call i32' in line and symbol in line for line in result.ir_text.splitlines())
    with pytest.raises(RustError, match='cannot find macro `only_leaf` in this scope'):
        consumer('pub fn read()->i32{only_leaf!()}', {'dep': dep})


def test_missing_docs_retains_local_definition_locations_and_provider_lint_policy():
    dep = leaf('pub fn f()->i32{27}')
    with pytest.raises(RustError) as local:
        consumer(STRICT_CONSUMER + 'pub use dep::f;\npub fn read()->i32{f()}', {'dep': dep})
    assert str(local.value).endswith('lib.rs: 4:8: missing documentation for a function '
                                     '(missing_docs, deny at the crate root)')
    # A real local module shadows the extern; its re-exported definition is
    # still subject to our lint, not the leaf's default allow.
    with pytest.raises(RustError) as shadow:
        consumer(STRICT_CONSUMER + 'mod dep {\npub fn f()->i32{7}\n}\npub use dep::f;', {'dep': dep})
    assert str(shadow.value).endswith('lib.rs: 4:8: missing documentation for a function '
                                      '(missing_docs, deny at the crate root)')
    strict_leaf = replace(leaf('//! Provider docs.\n#![forbid(missing_docs)]\npub fn f()->i32{27}'),
                          root_path='vendor/leaf.rs')
    with pytest.raises(RustError) as foreign:
        consumer('pub use dep::f;', {'dep': strict_leaf})
    assert str(foreign.value).endswith('vendor/leaf.rs: 3:8: missing documentation for a function '
                                       '(missing_docs, forbid at the crate root)')


def test_missing_docs_does_not_invent_missing_or_out_of_scope_imports():
    dep = leaf('pub fn f()->i32{27}')
    with pytest.raises(RustError) as absent:
        consumer(STRICT_CONSUMER + 'use dep::f;\n/// Read.\npub fn read()->i32{f()}', {'other': dep})
    assert str(absent.value).endswith("lib.rs: 3:1: unknown type-namespace item 'dep' in module path 'dep::f'")
    with pytest.raises(RustError) as private:
        consumer(STRICT_CONSUMER + 'pub use dep::f;', {'dep': leaf('pub(crate) fn f()->i32{27}')})
    assert str(private.value).endswith("lib.rs: 3:5: private item 'f' is inaccessible from this module")
    # A nested public re-export does not move the definition into this crate.
    nested = consumer(STRICT_CONSUMER + 'pub use dep::nested::f;\n/// Read.\npub fn read()->i32{f()}',
                      {'dep': leaf('pub mod nested{pub fn f()->i32{27}}')})
    provider = dict(nested.source_dependencies)['dep']
    assert provider.symbols['nested::f'] in nested.ir_text
    with pytest.raises(RustError, match="private item 'nested'"):
        consumer(STRICT_CONSUMER + 'pub use dep::nested::f;', {'dep': leaf('mod nested{pub fn f()->i32{27}}')})


@pytest.mark.parametrize(('provider', 'call', 'message'), [
    ('fn read()->i32{1}', 'dep::read()', 'private item'),
    ('pub(crate) fn read()->i32{1}', 'dep::read()', 'private item'),
    ('pub fn read()->i32{1}', 'dep::missing()', 'unknown value-namespace item'),
    ('pub fn id<T>(x:T)->T{x}', 'dep::id(9i32)', 'monomorphic scalar'),
    ('pub struct P{pub x:i32} pub fn read()->P{P{x:1}}', 'dep::read()', 'monomorphic scalar'),
    ('pub unsafe fn read()->i32{1}', 'dep::read()', 'unsafe context'),
    ('pub fn read(x:i32)->i32{x}', 'dep::read()', 'expects 1 arguments'),
    ('pub fn read()->i32{true}', 'dep::read()', 'type mismatch'),
])
def test_bad_provider_privacy_signature_call_or_body_is_not_accepted(provider, call, message):
    with pytest.raises(RustError, match=message):
        consumer('pub fn answer()->i32{' + call + '}', {'dep': leaf(provider)})


def test_unpinned_mismatched_and_system_inputs_are_not_admitted():
    a, b = originals()
    for spec, alias, message in [
        (replace(a, source=a.source.replace(b'123', b'124')), 'liba', 'SHA-256'),
        (replace(a, target_triple='aarch64-apple-darwin'), 'liba', 'does not match'),
        (replace(a, crate_name='core'), 'liba', 'owned sysroot'),
        (a, 'std', 'owned sysroot'),
        (replace(a, root_path='../lib.rs'), 'liba', 'normalized relative'),
    ]:
        with pytest.raises(RustError, match=message):
            consumer(b, {alias: spec})
    with pytest.raises(RustError, match='2015 extern crate'):
        consumer(b, {'liba': a}, edition=2015)
    with pytest.raises(RustError, match='cfg/environment'):
        consumer(b, {'liba': a}, env={})
    with pytest.raises(RustError, match='1 to 8'):
        consumer(b, {'dep' + str(i): a for i in range(9)})
    with pytest.raises(RustError, match='multiple aliases'):
        consumer(b, {'liba': a, 'other': a})
    with pytest.raises(RustError, match='unknown type-namespace item'):
        consumer(b, None)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE)
def test_original_meson_libraries_link_only_with_the_defining_owned_object_and_return_246(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import ElfError, SHN_UNDEF, link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file

    a, b = originals()
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned compile/link'))
        result = consumer(b, {'liba': a})
        provider = dict(result.source_dependencies)['liba']
        assert result.source_crate_id.name == 'libb' and provider.source_crate_id.name == 'liba'
        assert not result.externally_checked and not provider.externally_checked
        b_obj, a_obj = (parse_relocatable(unit.emit_object()) for unit in (result, provider))
        foo, bar = provider.symbols['foo'], result.symbols['bar']
        assert any(s.name == foo and s.section_index != SHN_UNDEF for s in a_obj.symbols)
        assert any(s.name == foo and s.section_index == SHN_UNDEF for s in b_obj.symbols)
        observer = assemble_file(f'''.intel_syntax noprefix
.text
.globl _start
_start:
 call {bar}
 push rax
 mov rsi, rsp
 mov edx, 4
 mov eax, 1
 mov edi, 1
 syscall
 mov eax, 60
 xor edi, edi
 syscall
''')
        with pytest.raises(ElfError) as missing:
            link_static_executable([observer, b_obj])
        assert str(missing.value) == 'undefined static ELF symbols: ' + foo
        image = link_static_executable([observer, b_obj, a_obj])
    exe = tmp_path / 'owned-meson-observer'
    exe.write_bytes(image)
    exe.chmod(0o700)
    run = subprocess.run([str(exe)], capture_output=True, timeout=5)
    (tmp_path / 'owned-meson-observer.stdout').write_bytes(run.stdout)
    assert (run.returncode, run.stdout, run.stderr) == (0, (246).to_bytes(4, 'little'), b'')
    assert originals() == (a, b)


def _audited_rustc_is_1_91_1():
    # The receipt names the installed release; another release is not this
    # reference and deselects the test instead of skipping it.
    if not os.environ.get('PCC_RUSTC_INSTALL_RECEIPT'):
        return False
    from tests.rust.test_rust_certified_references import toolchain
    version = toolchain().verbose_version.splitlines()
    return ('release: 1.91.1' in version
            and 'commit-hash: ed61e7d7e242494fb7057f2657300d9e77bb4fcb' in version)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE or actual_check_prerequisite())
@pytest.mark.pcc_gate(probe=_audited_rustc_is_1_91_1)
def test_same_glob_and_extern_sources_against_the_audited_rustc_1_91_1(tmp_path):
    from tests.rust.rustc_reference_host import run_reference
    from tests.rust.test_rust_certified_references import toolchain

    audited = toolchain()
    version = audited.verbose_version.splitlines()
    if ('release: 1.91.1' not in version
            or 'commit-hash: ed61e7d7e242494fb7057f2657300d9e77bb4fcb' not in version):
        pytest.fail('needs the audited original Rust 1.91.1 toolchain, not another release')
    rustc = audited.root / 'bin/rustc'
    assert sha256(rustc.read_bytes()).hexdigest() == audited.compiler_sha256
    env = _reference_environment(os.environ, None, windows=False)
    assert _reference_command('original compiler identity', [str(rustc), '-vV'], timeout=10,
                              env=env).decode() == audited.verbose_version
    common = [str(rustc), '--edition=2018', '--target=' + TARGET, '-Coverflow-checks=off']
    leaf_path, leaf_rlib = tmp_path / 'dep.rs', tmp_path / 'libdep.rlib'
    leaf_path.write_bytes(GLOB_LEAF)
    _reference_command('build source dep', [*common, '--crate-type=rlib', '--crate-name=dep',
                       str(leaf_path), '-o', str(leaf_rlib)], env=env, timeout=20)
    observer = tmp_path / 'observer.rs'
    observer.write_text('fn main(){use std::io::Write;'
                        'std::io::stdout().write_all(&client::read().to_le_bytes()).unwrap();}')
    for label, text, definition, value in GLOB_EXTERN:
        directory = tmp_path / label
        directory.mkdir()
        source, unit = directory / 'lib.rs', directory / 'libclient.rlib'
        source.write_bytes(text.encode())
        record = run_reference(label, [*common, '--crate-type=rlib', '--crate-name=client', '--error-format=json',
                               '--extern', 'dep=' + str(leaf_rlib), str(source), '-o', str(unit)],
                               capture_output=True, check=False, timeout=20, env=env)
        if definition is None:
            assert record.returncode != 0 and not unit.exists(), label
            diagnostics = [json.loads(line) for line in record.stderr.splitlines()]
            assert any(d.get('level') == 'error' and (d.get('code') or {}).get('code') == 'E0659'
                       and any(s.get('is_primary') and s['file_name'] == str(source) and s['line_start'] == 3
                               for s in d.get('spans', ())) for d in diagnostics), (label, record.stderr)
        else:
            assert record.returncode == 0 and unit.is_file(), (label, record.stderr)
            binary = directory / 'observer'
            _reference_command(label + ' observer', [*common, '--extern', 'client=' + str(unit), '-L',
                               'dependency=' + str(tmp_path), str(observer), '-o', str(binary)],
                               env=_reference_environment(os.environ, None, windows=False, link=True), timeout=20)
            assert _reference_command(label + ' result', [str(binary)], env=env,
                                      timeout=5) == value.to_bytes(4, 'little', signed=True)
        assert source.read_bytes() == text.encode() and leaf_path.read_bytes() == GLOB_LEAF


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE or actual_check_prerequisite())
def test_same_original_meson_two_crates_and_main_rustc_reference(tmp_path):
    from tests.rust.test_rust_certified_references import toolchain

    a, b = originals()
    audited = toolchain()
    rustc = audited.root / 'bin/rustc'
    assert sha256(rustc.read_bytes()).hexdigest() == audited.compiler_sha256
    env = _reference_environment(os.environ, None, windows=False)
    common = [str(rustc), '--edition=2018', '--target=' + TARGET, '-Coverflow-checks=off']
    for name, deps in [('liba', []), ('libb', ['--extern', 'liba=' + str(tmp_path / 'libliba.rlib')])]:
        _reference_command(name, [*common, '--crate-type=rlib', '--crate-name', name, *deps,
                           str(PROJECT / name / 'lib.rs'), '-o', str(tmp_path / ('lib' + name + '.rlib'))],
                           env=env, timeout=30)
    exe = tmp_path / 'rustc-meson-main'
    _reference_command('original Meson main', [*common, '--extern', 'libb=' + str(tmp_path / 'liblibb.rlib'),
                       '-L', 'dependency=' + str(tmp_path), str(PROJECT / 'main.rs'), '-o', str(exe)],
                       env=env, timeout=30)
    assert _reference_command('execute Meson main', [str(exe)], env=env, timeout=5) == b'246\n'
    assert originals() == (a, b)


def nested_leaf(*, value=27, root='pub mod numbers;', child_public='pub'):
    # A real outlined provider (distinct crate/link identity); not a stand-in
    # for core or any other compiler-owned system library.
    child = f'{child_public} mod inner {{ pub fn read() -> i32 {{ {value} }} }}'.encode()
    return replace(leaf(root, name='provider'),
                   module_sources=(('numbers.rs', child, sha256(child).hexdigest()),))


@pytest.mark.parametrize(('edition', 'prefix'), [
    (2015, 'extern crate dep as arithmetic;'),
    (2018, 'use dep as arithmetic;'),
    (2021, 'extern crate dep as arithmetic;'),
])
def test_named_source_module_aliases_resolve_real_nested_definition(edition, prefix, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned source resolution'))
    text = ('#![no_std]\n' + prefix + '\nuse arithmetic::numbers as numbers;\n'
            'use numbers::inner::read as get;\npub fn result() -> i32 { get() + numbers::inner::read() }')
    result = consumer(text, {'dep': nested_leaf()}, edition=edition)
    provider = dict(result.source_dependencies)['dep']
    target = provider.symbols['numbers::inner::read']
    assert sum('call i32' in line and target in line for line in result.ir_text.splitlines()) == 2
    assert provider.source_crate_id.name == 'provider'
    assert set(provider.symbols) == {'numbers::inner::read'}
    assert result.emit_object().startswith(b'\x7fELF') and provider.emit_object().startswith(b'\x7fELF')


def test_outlined_source_pins_and_visibility_are_not_bypassed():
    dependency = nested_leaf()
    text = 'use dep::numbers::inner::read; pub fn result()->i32{read()}'
    first = consumer(text, {'dep': dependency})
    second = consumer(text, {'dep': nested_leaf(value=28)})
    assert first.symbols['result'] != second.symbols['result']
    assert dict(first.source_dependencies)['dep'].symbols != dict(second.source_dependencies)['dep'].symbols
    with pytest.raises(RustError, match='private item'):
        consumer(text, {'dep': nested_leaf(child_public='')})
    with pytest.raises(RustError, match='private item'):
        consumer(text, {'dep': nested_leaf(root='mod numbers;')})
    name, raw, digest = dependency.module_sources[0]
    with pytest.raises(RustError, match='SHA-256'):
        consumer(text, {'dep': replace(dependency, module_sources=((name, raw + b' ', digest),))})
    with pytest.raises(RustError, match='module source missing'):
        consumer(text, {'dep': replace(dependency, module_sources=())})
    with pytest.raises(RustError, match='duplicate external source path'):
        consumer(text, {'dep': replace(dependency, module_sources=dependency.module_sources * 2)})
    with pytest.raises(RustError, match='external source module glob'):
        consumer('extern crate dep; use dep::numbers::*;', {'dep': dependency})


def test_nested_public_function_import_is_not_limited_to_leaf_root():
    source = leaf('mod details { pub mod numbers { pub fn get() -> i32 { 31 } } }\n'
                  'pub use details::numbers as math;')
    result = consumer('use dep::math; pub fn result()->i32{math::get()}', {'dep': source})
    provider = dict(result.source_dependencies)['dep']
    assert provider.symbols['details::numbers::get'] in result.ir_text
    with pytest.raises(RustError, match='private item'):
        consumer('use dep::details::numbers;', {'dep': source})


def test_rust_2015_declared_crate_and_module_scope_keep_normal_privacy():
    dep = nested_leaf()
    with pytest.raises(RustError, match='shadowing primitive'):
        consumer('mod m { extern crate dep as i32; }', {'dep': dep})
    with pytest.raises(RustError, match='2015 extern crate'):
        consumer('use dep::numbers::inner::read; fn f()->i32{read()}', {'dep': dep}, edition=2015)
    root = ('extern crate dep as linked;\n'
            'mod worker { use linked::numbers::inner::read; pub fn go()->i32{read()} }\n'
            'pub fn run()->i32{worker::go()}')
    got = consumer(root, {'dep': dep}, edition=2015)
    assert dict(got.source_dependencies)['dep'].symbols['numbers::inner::read'] in got.ir_text
    with pytest.raises(RustError, match='private use binding'):
        consumer('mod left { extern crate dep as local; }\n'
                 'mod right { fn f()->i32{crate::left::local::numbers::inner::read()} }',
                 {'dep': dep})
    with pytest.raises(RustError, match='unknown type-namespace'):
        consumer('mod left { extern crate dep as local; }\n'
                 'mod right { fn f()->i32{local::numbers::inner::read()} }',
                 {'dep': dep})


NESTED_ORIGINAL = ('#![no_std]\nextern crate dep as linked;\n'
                   'use linked::numbers as arithmetic;\n'
                   'pub fn run() -> i32 { arithmetic::inner::read() + 5 }')


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE)
def test_rust_2015_multifile_dependency_links_its_actual_defining_elf_object(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import ElfError, link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file

    original = NESTED_ORIGINAL
    with monkeypatch.context() as deny:
        deny.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned compile/link'))
        result = consumer(original, {'dep': nested_leaf(value=27)}, edition=2015)
        provider = dict(result.source_dependencies)['dep']
        owner_symbol = provider.symbols['numbers::inner::read']
        call = result.symbols['run']
        observer = assemble_file(f'''.intel_syntax noprefix
.text
.globl _start
_start:
 call {call}
 push rax
 mov rsi, rsp
 mov edx, 4
 mov eax, 1
 mov edi, 1
 syscall
 mov eax, 60
 xor edi, edi
 syscall
''')
        own = parse_relocatable(result.emit_object())
        defining = parse_relocatable(provider.emit_object())
        with pytest.raises(ElfError, match=owner_symbol):
            link_static_executable([observer, own])
        image = link_static_executable([observer, own, defining])
    binary = tmp_path / 'owned-2015-source-dependency'
    binary.write_bytes(image)
    binary.chmod(0o700)
    ran = subprocess.run([str(binary)], capture_output=True, timeout=5)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, (32).to_bytes(4, 'little'), b'')


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE or actual_check_prerequisite())
def test_same_2015_multifile_crate_originals_match_audited_reference(tmp_path):
    from tests.rust.test_rust_certified_references import toolchain

    audited = toolchain()
    rustc = audited.root / 'bin/rustc'
    assert sha256(rustc.read_bytes()).hexdigest() == audited.compiler_sha256
    env = _reference_environment(os.environ, None, windows=False)
    common = [str(rustc), '--target=' + TARGET, '-Coverflow-checks=off']
    dep = nested_leaf(value=27)
    originals = ((dep.root_path, dep.source, dep.source_sha256), *dep.module_sources)
    source_dir = tmp_path / 'provider'
    source_dir.mkdir()
    for name, raw, digest in originals:
        assert sha256(raw).hexdigest() == digest
        (source_dir / name).write_bytes(raw)
    provider = tmp_path / 'libprovider.rlib'
    _reference_command('original provider', [*common, '--edition=2018', '--crate-name=provider',
                       '--crate-type=rlib', str(source_dir / dep.root_path), '-o', str(provider)],
                       env=env, timeout=20)
    client_source = tmp_path / 'client.rs'
    client_source.write_bytes(NESTED_ORIGINAL.encode())
    client = tmp_path / 'libclient.rlib'
    _reference_command('original 2015 client', [*common, '--edition=2015', '--crate-name=client',
                       '--crate-type=rlib', '--extern', 'dep=' + str(provider),
                       str(client_source), '-o', str(client)], env=env, timeout=20)
    observer = tmp_path / 'observer.rs'
    observer.write_text('extern crate client;use std::io::Write;'
                        'fn main(){std::io::stdout().write_all(&client::run().to_le_bytes()).unwrap();}')
    binary = tmp_path / 'reference'
    _reference_command('2015 client observer', [*common, '--edition=2015', '--extern',
                       'client=' + str(client), '-L', 'dependency=' + str(tmp_path),
                       str(observer), '-o', str(binary)],
                       env=_reference_environment(os.environ, None, windows=False, link=True), timeout=20)
    reference = _reference_command('execute 2015 originals', [str(binary)], env=env, timeout=5)
    # The immediately preceding owned link test executes the same originals
    # and observes the same four bytes without any external compiler.
    assert reference == (32).to_bytes(4, 'little')
    assert client_source.read_bytes() == NESTED_ORIGINAL.encode()
    assert all((source_dir / name).read_bytes() == raw for name, raw, _ in originals)
