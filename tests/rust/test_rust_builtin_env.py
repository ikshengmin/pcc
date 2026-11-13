"""Built-in `env!` / `option_env!` over an explicit compile-time environment.

https://doc.rust-lang.org/std/macro.env.html
https://doc.rust-lang.org/std/macro.option_env.html
https://doc.rust-lang.org/cargo/reference/environment-variables.html
PCC evaluates both macros at parse time against a `CompileEnvironment` the
caller supplies (never the host process environment). Argument rules and
messages follow rustc; values follow the environment, and Cargo's crate
environment is derived from manifests. The expanded `&'static str` has no
runtime representation yet, so every expansion is recorded on the parser and
the use then stops at that explicit gap: no owned execution is claimed.
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
    parser = Parser('pub fn f(){ let v = ' + body + '; }', environment=environment)
    with pytest.raises(RustError) as error:
        parser.parse()
    return parser.macro_expansions, str(error.value)


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
def test_expansion_records_the_value_then_stops_at_runtime_strings(body, name, arguments, value):
    expansions, message = expand(body)
    expansion, = expansions
    assert (expansion.name, expansion.arguments, expansion.value) == (name, arguments, value)
    assert (expansion.token.text, expansion.token.column) == (name, 21)
    assert 'runtime string values are not implemented' in message


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
    assert expansions == [] and actual.endswith(message)


@pytest.mark.parametrize('body,environment,message', [
    ('env!(concat!("PCC_", "SET"))', ENV, 'require eager macro expansion'),
    ('env!("PCC_HIDDEN")', ENV, "'PCC_HIDDEN' is unavailable in the explicit compile-time environment: a recorded reason"),
    ('env!("PCC_SET")', None, 'requires an explicit compile-time environment'),
    ('option_env!("PCC_SET")', None, 'host process environment is never read'),
])
def test_unowned_arguments_and_missing_environments_are_explicit(body, environment, message):
    expansions, actual = expand(body, environment)
    assert expansions == [] and message in actual


def test_compile_rust_takes_a_mapping_or_an_environment_and_never_reads_the_host(monkeypatch):
    monkeypatch.setenv('PCC_FROM_HOST', 'leaked')
    with pytest.raises(RustError, match='requires an explicit compile-time environment'):
        compile_rust('pub fn f(){ let v = env!("PCC_FROM_HOST"); }', experimental_rustc_checked=True)
    with pytest.raises(RustError, match='`PCC_FROM_HOST` not defined at compile time'):
        compile_rust('pub fn f(){ let v = env!("PCC_FROM_HOST"); }', experimental_rustc_checked=True, env={})
    with pytest.raises(RustError, match='runtime string values'):
        compile_rust('pub fn f(){ let v = env!("X"); }', experimental_rustc_checked=True, env={'X': 'y'})
    with pytest.raises(RustError, match='map strings to strings'):
        CompileEnvironment.of({'X': 1})
    with pytest.raises(RustError, match='both defined and unavailable'):
        CompileEnvironment.of({'X': '1'}, {'X': 'reason'})


def isolated_rustc_environment():
    """Only the toolchain locators plus the test variables reach rustc."""
    keep = {key: os.environ[key] for key in ('PATH', 'HOME', 'RUSTUP_HOME', 'CARGO_HOME', 'RUSTUP_TOOLCHAIN')
            if key in os.environ}
    return {**keep, **dict(ENV.variables)}


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_expands_and_rejects_the_same_uses(tmp_path):
    rustc, environment = shutil.which('rustc'), isolated_rustc_environment()
    printed = ''.join('print!("{:?}|", ' + body + ');' for body, *_ in EXPANDED)
    (tmp_path / 'main.rs').write_text('fn main(){' + printed + '}\n')
    subprocess.run([rustc, '--edition=2021', str(tmp_path / 'main.rs'), '-o', str(tmp_path / 'main')],
                   check=True, capture_output=True, timeout=60, env=environment)
    output = subprocess.run([str(tmp_path / 'main')], check=True, capture_output=True, text=True, timeout=10).stdout
    expected = ''.join(('None' if value is None else f'Some("{value}")' if name == 'option_env' else f'"{value}"') + '|'
                       for _, name, _, value in EXPANDED)
    assert output == expected
    for index, (body, message) in enumerate(REJECTED):
        result = 'Option<&\'static str>' if body.startswith('option_env') else '&\'static str'
        (tmp_path / f'case{index}.rs').write_text(f'pub fn f()->{result}{{ {body} }}\n')
        run = subprocess.run([rustc, '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              str(tmp_path / f'case{index}.rs'), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60, env=environment)
        assert run.returncode != 0 and 'error: ' + message in run.stderr, (body, run.stderr)


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
