"""Owned cfg configuration: target built-ins, CfgOptions, item gating, Cargo.

https://doc.rust-lang.org/reference/conditional-compilation.html
https://doc.rust-lang.org/cargo/reference/features.html
https://doc.rust-lang.org/cargo/reference/profiles.html
PCC evaluates cfg from an explicit CfgOptions (rustc's --cfg, --test,
-C debug-assertions, -C panic) and its own record of each admitted target's
built-in cfg; no host tool is consulted. Every item kind is parsed in full and
then kept or rolled back. Reference checks compare the recorded target data
with `rustc --print cfg` and each predicate's selection with rustc's own.
"""
import json
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import CfgOptions, RustError, compile_rust
from pcc.frontends.rust.cargo_config import cargo_cfg_options, profile_options, resolve_features
from pcc.frontends.rust.configuration import GATED_CFG, TARGET_CFG, OwnedRustConfiguration
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.syntax import Parser
from tests.rust.test_rust_ffi import record_run
from tests.rust.test_rust_targets import PROGRAMS as PROGRAM_DIRECTORY

FAST = CfgOptions(cfg=('feature="fast"',), debug_assertions=False, panic='abort')
LINUX_X86_64 = sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}


def compile_source(source, cfg=FAST, **options):
    return compile_rust(source, experimental_rustc_checked=True, cfg=cfg, **options)


def rustc_flags(options):
    flags = []
    for spec in options.cfg:
        flags += ['--cfg', spec]
    if options.test:
        flags.append('--test')
    if options.debug_assertions is not None:
        flags += ['-C', 'debug-assertions=' + ('yes' if options.debug_assertions else 'no')]
    if options.panic is not None:
        flags += ['-C', 'panic=' + options.panic]
    return flags


# --- recorded target data ---------------------------------------------------

@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'x86_64-unknown-linux-musl', 'aarch64-apple-darwin'])
def test_recorded_target_cfg_matches_rustc_print_cfg(target):
    printed = subprocess.run([shutil.which('rustc'), '--print', 'cfg', '--target', target],
                             capture_output=True, text=True, timeout=30, check=True).stdout
    entries = set()
    for line in printed.splitlines():
        name, _, value = line.partition('=')
        entries.add((name, json.loads(value) if value else None))
    # Default codegen options add these two; CfgOptions owns them in PCC.
    assert entries == set(TARGET_CFG[target]) | {('debug_assertions', None), ('panic', 'unwind')}


def test_pcc_target_spellings_share_the_recorded_data():
    assert TARGET_CFG['arm64-apple-darwin'] == TARGET_CFG['aarch64-apple-darwin']
    with pytest.raises(RustError, match='is not recorded'):
        OwnedRustConfiguration('x86_64-linux', 2021, FAST)


# --- predicate evaluation against rustc -------------------------------------

PREDICATES = (
    'unix', 'windows', 'target_os="linux"', 'target_os="macos"', 'target_arch="x86_64"',
    'target_arch="aarch64"', 'target_pointer_width="64"', 'target_endian="little"',
    'target_env="gnu"', 'target_env=""', 'target_family="unix"', 'target_vendor="apple"',
    'target_has_atomic="64"', 'target_has_atomic="128"', 'target_feature="sse2"',
    'target_feature="neon"', 'target_abi=""', 'feature="fast"', 'feature="slow"',
    'not(feature="slow")', 'all(unix,feature="fast")', 'any(windows,feature="slow")', 'all()',
    'any()', 'true', 'false', 'test', 'debug_assertions', 'panic="abort"', 'panic="unwind"',
    'docsrs', 'doc', 'miri', 'proc_macro', 'feature', 'target_os',
)
OPTION_SETS = (FAST, CfgOptions(cfg=('feature="slow"', 'docsrs'), debug_assertions=True, panic='unwind'))


def selected_by_pcc(predicate, options, target):
    source = '#[cfg(' + predicate + ')] pub fn selected()->u64{1} pub fn f()->u64{2}'
    return 'selected' in compile_source(source, cfg=options, target_triple=target).symbols


@pytest.mark.parametrize('options', OPTION_SETS, ids=['fast-abort', 'slow-unwind-debug'])
@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_owned_predicates_select_from_the_recorded_target_and_options(options, target):
    entries = OwnedRustConfiguration(target, 2021, options).entries
    linux = target.startswith('x86_64')
    expected = {
        'unix': True, 'windows': False, 'target_os="linux"': linux, 'target_os="macos"': not linux,
        'target_feature="sse2"': linux, 'target_feature="neon"': not linux,
        'target_has_atomic="128"': not linux, 'feature="fast"': options is FAST,
        'debug_assertions': options.debug_assertions, 'panic="abort"': options.panic == 'abort',
        'test': False, 'doc': False, 'feature': False, 'all()': True, 'any()': False,
        'true': True, 'false': False, 'docsrs': options is not FAST,
    }
    for predicate, value in expected.items():
        assert selected_by_pcc(predicate, options, target) is value, predicate
    assert ('target_pointer_width', '64') in entries


def host_target():
    if not shutil.which('rustc'):
        return None
    info = subprocess.run([shutil.which('rustc'), '-vV'], capture_output=True, text=True, timeout=30).stdout
    host = next(line.split(': ', 1)[1] for line in info.splitlines() if line.startswith('host: '))
    return host if host in TARGET_CFG else None


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if host_target() else 'development-only rustc for a recorded host target is not installed')
@pytest.mark.parametrize('options', OPTION_SETS, ids=['fast-abort', 'slow-unwind-debug'])
def test_every_predicate_selects_what_rustc_selects_on_the_host_target(options, tmp_path):
    target = host_target()
    for predicate in PREDICATES:
        source = tmp_path / 'probe.rs'
        source.write_text('#[cfg(' + predicate + ')] compile_error!("selected");\npub fn f(){}\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021',
                              '--emit=metadata', *rustc_flags(options), str(source),
                              '-o', str(tmp_path / 'probe.rmeta')], capture_output=True, text=True, timeout=30)
        rustc_selected = 'selected' in run.stderr
        assert run.returncode == (1 if rustc_selected else 0), run.stderr
        assert selected_by_pcc(predicate, options, target) is rustc_selected, predicate


# --- explicit errors --------------------------------------------------------

@pytest.mark.parametrize('predicate,message', [
    *((name, 'experimental') for name in sorted(GATED_CFG)),
    ('version("1.80")', 'experimental'),
    ('target(os="linux")', 'experimental'),
    ('accessible(core::mem)', 'E0537'),
    ('nonsense(unix)', 'E0537'),
    ('not(unix,windows)', 'exactly one predicate'),
])
def test_unstable_and_invalid_predicates_are_rejected_like_rustc(predicate, message):
    # rustc 1.92.0: E0658 for each experimental cfg, E0537 for other list names.
    with pytest.raises(RustError, match=message):
        compile_source('#[cfg(' + predicate + ')] pub fn f(){}')


@pytest.mark.parametrize('predicate,options,message', [
    ('debug_assertions', CfgOptions(panic='abort'), 'explicit CfgOptions.debug_assertions'),
    ('panic="abort"', CfgOptions(debug_assertions=False), 'explicit CfgOptions.panic'),
    # Short-circuiting never hides an unset choice.
    ('any(unix,debug_assertions)', CfgOptions(panic='abort'), 'explicit CfgOptions.debug_assertions'),
])
def test_option_dependent_names_require_an_explicit_choice(predicate, options, message):
    with pytest.raises(RustError, match=message):
        compile_source('#[cfg(' + predicate + ')] pub fn f(){}', cfg=options)


@pytest.mark.parametrize('kwargs,message', [
    ({'cfg': ['feature="x"']}, 'tuple of cfg specs'),
    ({'cfg': ('feature=x',)}, 'one double-quoted string'),
    ({'cfg': ('feature="a\\"b"',)}, 'one double-quoted string'),
    ({'cfg': ('9x',)}, 'identifier name'),
    ({'cfg': ('unix',)}, 'is built in'),
    ({'cfg': ('target_os="windows"',)}, 'is built in'),
    ({'cfg': ('debug_assertions',)}, 'is built in'),
    ({'panic': 'immediate'}, "'unwind' or 'abort'"),
    ({'test': 1}, 'must be booleans'),
])
def test_cfg_options_reject_malformed_or_builtin_spoofing_specs(kwargs, message):
    with pytest.raises(RustError, match=message):
        CfgOptions(**kwargs)


def test_owned_configuration_and_a_checked_source_cannot_both_select_cfg():
    configuration = OwnedRustConfiguration('x86_64-unknown-linux-gnu', 2021, FAST)
    with pytest.raises(RustError, match='cannot both select cfg'):
        Parser('pub fn f(){}', configuration=configuration, rustc_check=object())
    with pytest.raises(RustError, match='for this edition'):
        Parser('pub fn f(){}', edition=2024, configuration=configuration)


def test_without_any_configuration_cfg_keeps_its_explicit_boundary():
    with pytest.raises(RustError, match='cfg requires an actual source/options-bound CheckedRustSource'):
        compile_rust('#[cfg(unix)] pub fn f(){}', experimental_rustc_checked=True)


# --- item gating ------------------------------------------------------------

def test_every_item_kind_is_parsed_then_kept_or_rolled_back():
    source = ('#[cfg(feature="slow")] struct S{x:u64}\n'
              '#[cfg(feature="slow")] enum E{A}\n'
              '#[cfg(feature="slow")] trait T{fn t();}\n'
              '#[cfg(feature="slow")] impl S{fn m(){}}\n'
              '#[cfg(feature="slow")] fn f()->u64{1}\n'
              '#[cfg(feature="fast")] fn f()->u64{2}\n'
              '#[cfg(feature="slow")] mod gone{pub fn g(){}}\n'
              '#[cfg(feature="slow")] use self::gone::g;\n'
              '#[cfg(feature="slow")] extern crate dependency;\n'
              '#[cfg(feature="slow")] pub(in crate::nowhere) fn hidden(){}\n'
              'pub fn answer()->u64{f()}\n')
    configuration = OwnedRustConfiguration('x86_64-unknown-linux-gnu', 2021, FAST)
    parser = Parser(source, configuration=configuration)
    functions = parser.parse()
    assert [f.token.text for f in functions] == ['f', 'answer']
    assert not (parser.records or parser.enums or parser.traits or parser.implementations
                or parser.modules or parser.uses or parser.crate_aliases or parser.visibility_restrictions)
    enabled = [item.enabled for item in parser.conditional_items]
    assert enabled == [False] * 5 + [True] + [False] * 4
    disabled_struct = parser.conditional_items[0]
    assert source[slice(*disabled_struct.source_span)] == '#[cfg(feature="slow")] struct S{x:u64}'
    assert disabled_struct.item[0].token.text == 'S'
    assert parser.conditional_items[5].item[0].token.text == 'f'
    # The same declarations are rejected when enabled.
    for line, message in (('#[cfg(feature="fast")] pub(in crate::nowhere) fn hidden(){}', 'ancestor modules'),
                          ('#[cfg(feature="fast")] extern crate dependency;', 'external crate dependency')):
        with pytest.raises(RustError, match=message):
            compile_source(line + ' pub fn answer()->u64{1}')


def test_disabled_items_still_need_valid_supported_syntax():
    # rustc strips only parsed items: a disabled item's syntax is still checked.
    with pytest.raises(RustError, match="expected"):
        compile_source('#[cfg(feature="slow")] fn broken( {} pub fn f(){}')


def test_disabled_outlined_module_file_is_never_loaded():
    configuration = OwnedRustConfiguration('x86_64-unknown-linux-gnu', 2021, FAST)
    crate = parse_crate_sources('src/lib.rs', {
        'src/lib.rs': '#[cfg(feature="slow")] mod absent;\n#[cfg(feature="fast")] mod present;\npub fn f(){}',
        'src/present.rs': '#[cfg(unix)] pub fn on_unix(){}\n#[cfg(windows)] pub fn on_windows(){}',
    }, crate_name='cfg_test', edition=2021, configuration=configuration)
    present, = crate.root.children
    assert present.identity.components == ('present',)
    assert [f.token.text for f in present.body.functions] == ['on_unix']
    with pytest.raises(RustError, match='module source missing'):
        parse_crate_sources('src/lib.rs', {'src/lib.rs': '#[cfg(feature="fast")] mod absent;'},
                            crate_name='cfg_test', edition=2021, configuration=configuration)


# --- Cargo features and profiles ---------------------------------------------

MANIFEST = {
    'features': {
        'default': ['fast', 'tls/rustls'],
        'fast': ['dep:accel'],
        'extra': ['serde?/derive', 'docs'],
        'docs': [],
    },
    'dependencies': {
        'accel': {'version': '1', 'optional': True},
        'tls': {'version': '1', 'optional': True},
        'serde': {'version': '1', 'optional': True},
        'log': '0.4',
    },
}


def test_cargo_features_follow_the_cargo_reference_rules():
    selection = resolve_features(MANIFEST)
    # `tls/rustls` enables optional tls and its implicit feature; `dep:accel`
    # names accel without an implicit feature of that name.
    assert selection.features == ('default', 'fast', 'tls')
    assert selection.optional_dependencies == ('accel', 'tls')
    assert selection.dependency_features == (('tls', 'rustls', False),)
    weak = resolve_features(MANIFEST, features=('extra',), default_features=False)
    assert weak.features == ('docs', 'extra') and weak.optional_dependencies == ()
    assert weak.dependency_features == ()  # serde?/derive applies only to an enabled serde
    both = resolve_features(MANIFEST, features=('extra', 'serde'), default_features=False)
    assert both.features == ('docs', 'extra', 'serde')
    assert both.dependency_features == (('serde', 'derive', True),)


@pytest.mark.parametrize('manifest,features,message', [
    (MANIFEST, ('missing',), "does not have feature 'missing'"),
    ({'features': {'a': ['dep:log']}, 'dependencies': {'log': '0.4'}}, ('a',), 'not an optional dependency'),
    ({'features': {'a': ['b']}}, ('a',), "neither a feature nor a dependency"),
    ({'features': {'a': 'b'}}, (), 'lists of strings'),
])
def test_invalid_cargo_feature_requests_are_errors(manifest, features, message):
    with pytest.raises(RustError, match=message):
        resolve_features(manifest, features=features)


def test_cargo_profiles_follow_inherits_and_builtin_defaults():
    manifest = {'profile': {'release': {'panic': 'abort'}, 'dist': {'inherits': 'release'},
                            'checked': {'inherits': 'dist', 'debug-assertions': True},
                            'loop-a': {'inherits': 'loop-b'}, 'loop-b': {'inherits': 'loop-a'}}}
    assert profile_options(manifest, 'dev') == (True, 'unwind')
    assert profile_options(manifest, 'release') == (False, 'abort')
    assert profile_options(manifest, 'dist') == (False, 'abort')
    assert profile_options(manifest, 'checked') == (True, 'abort')
    assert profile_options(manifest, 'test') == (True, 'unwind')
    assert profile_options({'profile': {'dev': {'panic': 'abort'}}}, 'test') == (True, 'unwind')
    with pytest.raises(RustError, match='inheritance cycle'):
        profile_options(manifest, 'loop-a')
    with pytest.raises(RustError, match='not defined'):
        profile_options(manifest, 'missing')


def test_uv_default_features_and_release_profile_give_its_cfg_inputs():
    import tomllib
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'projects' / 'uv-0.13.7'
    package = tomllib.loads((root / 'crates/uv/Cargo.toml').read_text())
    workspace = tomllib.loads((root / 'Cargo.toml').read_text())
    options, selection = cargo_cfg_options(package, workspace_manifest=workspace, profile='release')
    assert options.cfg == tuple('feature="%s"' % name for name in (
        'default', 'performance', 'performance-memory-allocator', 'test-crates-io', 'test-defaults',
        'test-ecosystem', 'test-git', 'test-git-lfs', 'test-pypi', 'test-python', 'test-python-eol',
        'test-python-managed', 'test-r2', 'test-slow', 'test-universal'))
    assert (options.test, options.debug_assertions, options.panic) == (False, False, 'abort')
    assert selection.optional_dependencies == ('uv-performance-memory-allocator',)
    # `uv-test?/git` is weak and uv-test stays disabled.
    assert selection.dependency_features == (('uv-client', 'test-pypi', False),)


# --- the original fixture ---------------------------------------------------

def fixture_source():
    return (PROGRAM_DIRECTORY / 'cfg_owned.rs').read_text()


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_cfg_fixture_compiles_for_each_recorded_target(target, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile invoked a tool'))
    result = compile_source(fixture_source(), target_triple=target)
    assert {'base', 'chosen', 'speed::bits'} & set(result.symbols) == {'base', 'speed::bits'}
    assert result.emit_object()


OBSERVER = '''.intel_syntax noprefix
.text
.globl _start
_start:
 call {symbol}
 sub rsp,16
 mov QWORD PTR [rsp],rax
 mov eax,1
 mov edi,1
 mov rsi,rsp
 mov edx,8
 syscall
 mov eax,60
 xor edi,edi
 syscall
'''


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if LINUX_X86_64 and shutil.which('rustc') and shutil.which('cc')
                      else 'requires Linux x86-64 owned execution and a development rustc/cc reference')
def test_cfg_fixture_owned_execution_matches_rustc_with_the_same_options(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    original = fixture_source()
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile invoked a tool'))
        result = compile_source(original)
        binary = link_static_executable([parse_relocatable(result.emit_object()),
                                         assemble_file(OBSERVER.format(symbol=result.symbols['answer']))])
    expected = bytes.fromhex('1032547698badcfe')
    record_run(binary, expected, tmp_path)
    (tmp_path / 'lib.rs').write_text(original)
    (tmp_path / 'main.rs').write_text('use std::io::Write;\nfn main(){std::io::stdout()'
                                      '.write_all(&rust_reference::answer().to_le_bytes()).unwrap();}\n')
    common = ['--edition=2021', '-C', 'overflow-checks=off', *rustc_flags(FAST)]
    rustc = shutil.which('rustc')
    subprocess.run([rustc, '--crate-name', 'rust_reference', '--crate-type=rlib', *common,
                    str(tmp_path / 'lib.rs'), '-o', str(tmp_path / 'librust_reference.rlib')],
                   check=True, capture_output=True, timeout=60)
    subprocess.run([rustc, '--edition=2021', '-C', 'panic=abort', '-C', 'linker=' + shutil.which('cc'),
                    '--extern', 'rust_reference=' + str(tmp_path / 'librust_reference.rlib'),
                    str(tmp_path / 'main.rs'), '-o', str(tmp_path / 'reference')],
                   check=True, capture_output=True, timeout=60)
    reference = subprocess.run([str(tmp_path / 'reference')], capture_output=True, timeout=10)
    assert (reference.returncode, reference.stdout) == (0, expected)
