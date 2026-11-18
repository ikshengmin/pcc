"""Built-in `env!` / `option_env!` over an explicit compile-time environment.

https://doc.rust-lang.org/std/macro.env.html
https://doc.rust-lang.org/std/macro.option_env.html
https://doc.rust-lang.org/cargo/reference/environment-variables.html
PCC evaluates both macros at parse time against a `CompileEnvironment` the
caller supplies (never the host process environment). Argument rules and
messages follow rustc; values follow the environment, and Cargo's crate
environment is derived from manifests. Every expansion is recorded on the
parser. `env!` yields a `&'static str` literal (executed natively in
test_rust_static_str and for uv-version); `option_env!` stops at the
explicit gap of the missing core Option model.
"""
import os
import shutil
import subprocess

import pytest

from pcc.frontends.rust import CompileEnvironment, RustError, compile_rust
from pcc.frontends.rust.cargo_config import cargo_environment
from pcc.frontends.rust.syntax import Parser

ENV = CompileEnvironment.of({'PCC_SET': 'value', 'PCC_EMPTY': ''}, {'PCC_HIDDEN': 'a recorded reason'})


def expand(body, environment=ENV):
    """The recorded expansions, and the parse error (None when it parses)."""
    parser = Parser('pub fn f(){ let v = ' + body + '; }', environment=environment)
    try:
        parser.parse()
    except RustError as error:
        return parser.macro_expansions, str(error)
    return parser.macro_expansions, None


EXPANDED = (
    ('env!("PCC_SET")', 'env', ('PCC_SET',), 'value'),
    ('env!["PCC_SET"]', 'env', ('PCC_SET',), 'value'),
    ('env!{"PCC_SET"}', 'env', ('PCC_SET',), 'value'),
    ('env!("PCC_SET",)', 'env', ('PCC_SET',), 'value'),
    ('env!(r"PCC_SET")', 'env', ('PCC_SET',), 'value'),
    ('env!("PCC_EMPTY")', 'env', ('PCC_EMPTY',), ''),
    ('env!("PCC_SET", "unused message")', 'env', ('PCC_SET', 'unused message'), 'value'),
    ('option_env!("PCC_SET")', 'option_env', ('PCC_SET',), 'value'),
    ('option_env!("PCC_UNSET")', 'option_env', ('PCC_UNSET',), None),
)


@pytest.mark.parametrize('body,name,arguments,value', EXPANDED, ids=[row[0] for row in EXPANDED])
def test_expansion_records_the_value_and_env_yields_a_string_literal(body, name, arguments, value):
    expansions, message = expand(body)
    expansion, = expansions
    assert (expansion.name, expansion.arguments, expansion.value) == (name, arguments, value)
    assert (expansion.token.text, expansion.token.column) == (name, 21)
    if name == 'env':
        assert message is None
    else:
        assert 'the core Option model' in message


REJECTED = (
    ('env!("PCC_UNSET")', 'environment variable `PCC_UNSET` not defined at compile time'),
    ('env!("PCC_UNSET", "custom message")', 'custom message'),
    ('env!("")', 'environment variable `` not defined at compile time'),
    ('env!()', '`env!()` takes 1 or 2 arguments'),
    ('env!("PCC_SET", "m", "x")', '`env!()` takes 1 or 2 arguments'),
    ('env!(PCC_SET)', 'expected string literal'),
    ('env!(b"PCC_SET")', 'expected string literal'),
    ('option_env!("PCC_SET", "x")', 'option_env! takes 1 argument'),
)


@pytest.mark.parametrize('body,message', REJECTED, ids=[row[0] for row in REJECTED])
def test_invalid_uses_report_rustc_messages_without_expanding(body, message):
    expansions, actual = expand(body)
    assert expansions == [] and actual is not None and actual.endswith(message)


@pytest.mark.parametrize('body,environment,message', [
    ('env!(concat!("PCC_", "SET"))', ENV, 'require eager macro expansion'),
    ('env!("PCC_HIDDEN")', ENV, "'PCC_HIDDEN' is unavailable in the explicit compile-time environment: a recorded reason"),
    ('env!("PCC_SET")', None, 'requires an explicit compile-time environment'),
    ('option_env!("PCC_SET")', None, 'host process environment is never read'),
])
def test_unowned_arguments_and_missing_environments_are_explicit(body, environment, message):
    expansions, actual = expand(body, environment)
    assert expansions == [] and actual is not None and message in actual


def test_compile_rust_takes_a_mapping_or_an_environment_and_never_reads_the_host(monkeypatch):
    monkeypatch.setenv('PCC_FROM_HOST', 'leaked')
    with pytest.raises(RustError, match='requires an explicit compile-time environment'):
        compile_rust('pub fn f(){ let v = env!("PCC_FROM_HOST"); }', experimental_rustc_checked=True)
    with pytest.raises(RustError, match='`PCC_FROM_HOST` not defined at compile time'):
        compile_rust('pub fn f(){ let v = env!("PCC_FROM_HOST"); }', experimental_rustc_checked=True, env={})
    assert 'define' in compile_rust('pub fn f()->&\'static str{ env!("X") }', experimental_rustc_checked=True,
                                    env={'X': 'y'}).ir_text
    with pytest.raises(RustError, match='map strings to strings'):
        CompileEnvironment.of({'X': 1})
    with pytest.raises(RustError, match='both defined and unavailable'):
        CompileEnvironment.of({'X': '1'}, {'X': 'reason'})


_RUSTC_BASE_ENV = ('PATH', 'HOME', 'RUSTUP_HOME', 'CARGO_HOME', 'RUSTUP_TOOLCHAIN')
_RUSTC_WINDOWS_ENV = (
    # Windows loader, Rust home/temp and Visual Studio discovery locations.
    'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
    'PROGRAMW6432', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
    'APPDATA', 'LOCALAPPDATA',
    # Preserve an already selected MSVC/SDK toolchain, not compiler flags.
    'VSINSTALLDIR', 'VCINSTALLDIR', 'VCTOOLSINSTALLDIR', 'VCTOOLSVERSION',
    'VISUALSTUDIOVERSION', 'VSCMD_ARG_HOST_ARCH', 'VSCMD_ARG_TGT_ARCH',
    'WINDOWSSDKDIR', 'WINDOWSSDKVERSION',
    'WINDOWSSDKLIBVERSION', 'UNIVERSALCRTSDKDIR', 'UCRTVERSION',
    'INCLUDE', 'LIB', 'LIBPATH',
)


def _rustc_toolchain_environment(environ, *, windows):
    """Retain toolchain/OS locators without leaking arbitrary macro inputs."""
    if windows:
        # Windows environment names are case-insensitive. Retain the original
        # spelling and values rather than inventing SDK paths or PATH ordering.
        allowed = set(_RUSTC_BASE_ENV + _RUSTC_WINDOWS_ENV)
        return {key: value for key, value in environ.items() if key.upper() in allowed}
    return {key: environ[key] for key in _RUSTC_BASE_ENV if key in environ}


def isolated_rustc_environment():
    # Dropping Windows discovery variables can make rustc fall through to the
    # unrelated GNU coreutils link.exe on PATH. Still isolate PCC_UNSET/HIDDEN
    # and every unrelated host variable; never inherit RUSTFLAGS or LINK flags.
    keep = _rustc_toolchain_environment(os.environ, windows=os.name == 'nt')
    return {**keep, **dict(ENV.variables)}


def _run_env_reference(*args, **kwargs):
    """Retain captured diagnostics without changing subprocess failure semantics."""
    try:
        return subprocess.run(*args, **kwargs)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        error.add_note(
            "Rust env reference subprocess diagnostics:\n"
            + "returncode=" + repr(getattr(error, "returncode", None)) + "\n"
            + "timeout=" + repr(getattr(error, "timeout", None)) + "\n"
            + "stdout=" + repr(error.stdout) + "\n"
            + "stderr=" + repr(error.stderr)
        )
        raise


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_expands_and_rejects_the_same_uses(tmp_path):
    rustc, environment = shutil.which('rustc'), isolated_rustc_environment()
    printed = ''.join('print!("{:?}|", ' + body + ');' for body, *_ in EXPANDED)
    (tmp_path / 'main.rs').write_text('fn main(){' + printed + '}\n')
    _run_env_reference([rustc, '--edition=2021', str(tmp_path / 'main.rs'), '-o', str(tmp_path / 'main')],
                   check=True, capture_output=True, timeout=60, env=environment)
    output = _run_env_reference([str(tmp_path / 'main')], check=True, capture_output=True, text=True, timeout=10).stdout
    expected = ''.join(('None' if value is None else f'Some("{value}")' if name == 'option_env' else f'"{value}"') + '|'
                       for _, name, _, value in EXPANDED)
    assert output == expected
    for index, (body, message) in enumerate(REJECTED):
        result = 'Option<&\'static str>' if body.startswith('option_env') else '&\'static str'
        (tmp_path / f'case{index}.rs').write_text(f'pub fn f()->{result}{{ {body} }}\n')
        run = _run_env_reference([rustc, '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              str(tmp_path / f'case{index}.rs'), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60, env=environment)
        assert run.returncode != 0 and 'error: ' + message in run.stderr, (body, run.returncode, run.stdout, run.stderr)


# --- Cargo's crate environment ------------------------------------------------

WORKSPACE = '''[workspace]
members = ["member"]
resolver = "2"
[workspace.package]
edition = "2021"
homepage = "https://example.invalid/home"
authors = ["A One <a@example.invalid>", "B Two"]
license = "MIT OR Apache-2.0"
'''
MEMBER = '''[package]
name = "my-member"
version = "1.2.3-beta.4+build.5"
edition = { workspace = true }
homepage = { workspace = true }
authors = { workspace = true }
license = { workspace = true }
'''
# Observed from `cargo run` of this workspace with cargo 1.92.0.
EXPECTED = {
    'CARGO_PKG_NAME': 'my-member', 'CARGO_CRATE_NAME': 'my_member',
    'CARGO_PKG_VERSION': '1.2.3-beta.4+build.5', 'CARGO_PKG_VERSION_MAJOR': '1',
    'CARGO_PKG_VERSION_MINOR': '2', 'CARGO_PKG_VERSION_PATCH': '3', 'CARGO_PKG_VERSION_PRE': 'beta.4',
    'CARGO_PKG_AUTHORS': 'A One <a@example.invalid>:B Two', 'CARGO_PKG_DESCRIPTION': '',
    'CARGO_PKG_HOMEPAGE': 'https://example.invalid/home', 'CARGO_PKG_REPOSITORY': '',
    'CARGO_PKG_LICENSE': 'MIT OR Apache-2.0', 'CARGO_PKG_LICENSE_FILE': '', 'CARGO_PKG_RUST_VERSION': '',
}


def member_environment(**options):
    import tomllib
    return cargo_environment(tomllib.loads(MEMBER), workspace_manifest=tomllib.loads(WORKSPACE), **options)


def test_cargo_environment_follows_cargo_for_inherited_and_missing_fields():
    environment = member_environment()
    assert dict(environment.variables) == EXPECTED
    unavailable = dict(environment.unavailable)
    assert {'CARGO_PKG_README', 'CARGO_MANIFEST_DIR', 'CARGO_PRIMARY_PACKAGE', 'OUT_DIR'} <= set(unavailable)
    supplied = dict(member_environment(manifest_dir='/work/member', primary=True).variables)
    assert supplied['CARGO_MANIFEST_PATH'] == '/work/member/Cargo.toml'
    assert supplied['CARGO_PRIMARY_PACKAGE'] == '1'


@pytest.mark.parametrize('package,message', [
    ({'package': {'name': 'p', 'version': '1.0'}}, 'not a semantic version'),
    ({'package': {'name': 'p', 'license': {'workspace': True}}}, 'does not define it'),
    ({'package': {'name': 'p', 'authors': 'one'}}, 'list of strings'),
    ({'package': {}}, r'requires \[package\] name'),
])
def test_invalid_manifests_are_errors(package, message):
    with pytest.raises(RustError, match=message):
        cargo_environment(package)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('cargo') else 'development-only cargo is not installed')
def test_cargo_sets_the_same_crate_environment(tmp_path):
    (tmp_path / 'member/src').mkdir(parents=True)
    (tmp_path / 'Cargo.toml').write_text(WORKSPACE)
    (tmp_path / 'member/Cargo.toml').write_text(MEMBER)
    printed = ''.join(f'println!("{name}={{:?}}", env!("{name}"));' for name in EXPECTED)
    (tmp_path / 'member/src/main.rs').write_text('fn main(){' + printed + '}\n')
    run = subprocess.run([shutil.which('cargo'), 'run', '-q', '--offline', '-p', 'my-member'],
                         cwd=tmp_path, capture_output=True, text=True, timeout=300,
                         env={**os.environ, 'CARGO_TARGET_DIR': str(tmp_path / 'target')})
    assert run.returncode == 0, run.stderr
    observed = dict(line.split('=', 1) for line in run.stdout.splitlines())
    assert {name: value.strip('"') for name, value in observed.items()} == EXPECTED


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.parametrize("failure", ("exit", "timeout"))
@pytest.mark.parametrize("captured", ((b"out\xff", b"err\xfe"), ("out", "err"), (None, None)))
def test_env_reference_retains_original_subprocess_failure(monkeypatch, failure, captured):
    command = ["rustc", "main.rs"]
    environment = {"PATH": "toolchain", "PCC_SET": "value", "PCC_EMPTY": ""}
    options = dict(check=True, capture_output=True, timeout=60, env=environment)
    stdout, stderr = captured
    if failure == "exit":
        original = subprocess.CalledProcessError(1, command, output=stdout, stderr=stderr)
    else:
        original = subprocess.TimeoutExpired(command, 60, output=stdout, stderr=stderr)
    original.add_note("prior note")
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise original

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(type(original)) as raised:
        _run_env_reference(command, **options)
    assert raised.value is original
    assert original.stdout is stdout and original.stderr is stderr
    assert original.cmd is command
    assert getattr(original, "returncode", None) == (1 if failure == "exit" else None)
    assert getattr(original, "timeout", None) == (60 if failure == "timeout" else None)
    assert calls == [((command,), options)]
    assert calls[0][1]["env"] is environment
    assert original.__notes__ == ["prior note",
        "Rust env reference subprocess diagnostics:\n"
        + "returncode=" + repr(1 if failure == "exit" else None) + "\n"
        + "timeout=" + repr(60 if failure == "timeout" else None) + "\n"
        + "stdout=" + repr(stdout) + "\n" + "stderr=" + repr(stderr)]


@pytest.mark.xdist_group(name='rust-native-reference')
def test_env_reference_preserves_success_and_unchecked_rejection(monkeypatch):
    command = ["rustc", "main.rs"]
    for code in (0, 1):
        result = subprocess.CompletedProcess(command, code, stdout="out", stderr="err")
        calls = []

        def run(*args, **kwargs):
            calls.append((args, kwargs))
            return result

        monkeypatch.setattr(subprocess, "run", run)
        assert _run_env_reference(command, capture_output=True, timeout=60) is result
        assert calls == [((command,), dict(capture_output=True, timeout=60))]


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.parametrize('windows', [False, True])
def test_env_reference_keeps_only_platform_toolchain_locators(windows):
    source = {key: 'value:' + key for key in _RUSTC_BASE_ENV + _RUSTC_WINDOWS_ENV}
    source.update({'PCC_UNSET': 'must not leak', 'PCC_HIDDEN': 'must not leak',
                   'PCC_FROM_HOST': 'must not leak', 'TOKEN': 'must not leak',
                   'RUSTFLAGS': '--cfg bad', 'LINK': '/bad', 'CL': '/bad',
                   'VSTEL_MSBUILDPROJECTFULLPATH': 'not a toolchain locator'})
    before = dict(source)
    actual = _rustc_toolchain_environment(source, windows=windows)
    keys = _RUSTC_BASE_ENV + (_RUSTC_WINDOWS_ENV if windows else ())
    assert actual == {key: source[key] for key in keys}
    assert source == before
    assert _rustc_toolchain_environment({}, windows=windows) == {}


@pytest.mark.xdist_group(name='rust-native-reference')
def test_env_reference_windows_names_preserve_case_and_values():
    source = {'Path': r'C:\Git\usr\bin;C:\SDK\bin',
              'ProgramFiles(x86)': r'C:\Program Files (x86)',
              'SystemRoot': r'C:\Windows', 'vCtOoLsVeRsIoN': '14.44',
              'Lib': 'sdk libs', 'pcc_unset': 'must not leak',
              'rustflags': '--cfg bad'}
    assert _rustc_toolchain_environment(source, windows=True) == {
        key: value for key, value in source.items()
        if key not in ('pcc_unset', 'rustflags')}
    assert _rustc_toolchain_environment(source, windows=False) == {}


@pytest.mark.xdist_group(name='rust-native-reference')
def test_env_reference_explicit_values_win_and_absent_inputs_stay_absent(monkeypatch):
    source = {'PATH': 'same-path', 'PCC_SET': 'host-wrong', 'PCC_EMPTY': 'host-wrong',
              'PCC_UNSET': 'host-wrong', 'PCC_HIDDEN': 'host-wrong'}
    monkeypatch.setattr(os, 'environ', source)
    actual = isolated_rustc_environment()
    assert actual == {'PATH': 'same-path', 'PCC_SET': 'value', 'PCC_EMPTY': ''}
    assert source['PCC_UNSET'] == 'host-wrong'
