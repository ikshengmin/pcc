"""Host requirements of the development rustc observer, independent of Rust semantics."""
import subprocess
import sys

import pytest

from tests.rust.str_execution import _reference_command, _reference_environment


def test_windows_observer_receives_its_sdk_without_exposing_it_to_fixture_source():
    inherited = {'Path': 'a', 'Home': 'h', 'SystemRoot': 'win', 'Temp': 'tmp',
                 'ProgramFiles(x86)': 'pf', 'WindowsSdkDir': 'sdk', 'LIB': 'lib',
                 'LINK': '/entry:other', 'RUSTFLAGS': 'unexpected', 'CI_TOKEN': 'private'}
    request = {'build_cfg': '17', 'path': 'selected'}
    base = _reference_environment(inherited, request, windows=True)
    observer = _reference_environment(inherited, request, windows=True, link=True)
    assert base == {'path': 'selected', 'Home': 'h', 'SystemRoot': 'win', 'Temp': 'tmp', 'build_cfg': '17'}
    assert observer == {**base, 'ProgramFiles(x86)': 'pf', 'WindowsSdkDir': 'sdk', 'LIB': 'lib'}
    assert _reference_environment({}, None, windows=True, link=True) == {}


def test_posix_explicit_environment_and_case_sensitivity_are_unchanged():
    inherited = {'PATH': 'native', 'HOME': 'h', 'Path': 'not-PATH', 'RUSTFLAGS': 'surprise', 'LIB': '/lib'}
    assert _reference_environment(inherited, {'MyValue': 'explicit'}, windows=False, link=True) == {
        'PATH': 'native', 'HOME': 'h', 'MyValue': 'explicit'}


@pytest.mark.xdist_group(name="rust-native-reference")
def test_reference_failure_retains_actual_stage_and_tool_diagnostic():
    with pytest.raises(subprocess.CalledProcessError) as caught:
        _reference_command('rustc observer executable', [sys.executable, '-c',
                           "import sys;sys.stderr.write('native reason');raise SystemExit(7)"], timeout=5)
    assert caught.value.returncode == 7 and caught.value.stderr == b'native reason'
    assert any('rustc observer executable subprocess diagnostics' in note and 'native reason' in note
               for note in caught.value.__notes__)
    assert _reference_command('real child', [sys.executable, '-c',
                              "import sys;sys.stdout.buffer.write(b'\\xf0\\x9f\\x8c\\xb1')"], timeout=5) == '🌱'.encode()
