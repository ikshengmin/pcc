"""The pinned Go 1.27.3 standard-library closure of package testing.

tests/go/upstream/go1.27.3/pcc-std-closure-lock.json (selection tool:
scripts/pin_go_std_closure.py; loader: scripts/go_std_closure.py) pins every
non-test .go file of the import closure of testing, fmt, os and
testing/internal/testdeps for linux/darwin on amd64/arm64, unchanged from the
official go1.27.3.src.tar.gz, with sha256 and git blob at the go1.27.3
commit. Compiler-owned packages (unsafe, runtime and its subpackages) are not
pinned. Supplying the closure lets math/bits' external test package import
the original `testing`; the first blocker is then inside it.
"""
import shutil

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go_packages
from scripts import ci_go_math_bits_project as project
from scripts.go_std_closure import FIXTURE, LOCK, load_std_closure
from scripts.pin_go_std_closure import evaluate, name_ok, selected


def test_lock_verifies_the_closure_and_excludes_owned_packages():
    lock, packages = load_std_closure()
    assert set(lock['roots']) <= set(packages) and len(packages) == 107
    assert sum(len(units) for units in packages.values()) == 667 == len(lock['files'])
    assert not any(path in {'unsafe', 'runtime'} or path.startswith(('runtime/', 'internal/runtime/'))
                   for path in packages)
    # The math/bits files of the closure are the project's own pinned normal files.
    _, files = project.load_inputs()
    assert tuple(name for name, _ in packages['math/bits']) == project.NORMAL
    assert all(text.encode() == files[project.PACKAGE_DIR + '/' + name] for name, text in packages['math/bits'])


def test_a_changed_byte_is_rejected(tmp_path):
    copy = tmp_path / 'fixture'
    shutil.copytree(FIXTURE / 'src', copy / 'src')
    shutil.copy(FIXTURE / LOCK, copy / LOCK)
    target = copy / 'src/testing/testing.go'
    target.write_bytes(target.read_bytes().replace(b'package testing', b'package testinG', 1))
    with pytest.raises(ValueError, match='checksum/size/bound mismatch: src/testing/testing.go'):
        load_std_closure(copy)


@pytest.mark.parametrize('expression,goos,goarch,expected', [
    ('linux && !cgo', 'linux', 'amd64', True),
    ('unix && !linux', 'darwin', 'arm64', True),
    ('windows || plan9', 'linux', 'amd64', False),
    ('go1.27 && (amd64 || arm64)', 'darwin', 'amd64', True),
    ('goexperiment.foo', 'linux', 'amd64', True),  # either value is pinned
    ('purego', 'linux', 'amd64', False),
])
def test_build_constraint_selection(expression, goos, goarch, expected):
    assert selected('//go:build ' + expression + '\n\npackage p\n', goos, goarch) is expected


@pytest.mark.parametrize('name,goos,goarch,expected', [
    ('zero_linux_amd64.go', 'linux', 'amd64', True), ('zero_linux_amd64.go', 'linux', 'arm64', False),
    ('sys_unix.go', 'darwin', 'arm64', True), ('file_windows.go', 'linux', 'amd64', False),
    ('asm_arm64.go', 'darwin', 'arm64', True),
])
def test_file_name_selection(name, goos, goarch, expected):
    assert name_ok(name, goos, goarch) is expected


def test_evaluate_respects_precedence():
    assert evaluate('a || b && c', {'a': True, 'b': False, 'c': False}.get) is True
    assert evaluate('!(a || b)', {'a': False, 'b': False}.get) is True


def test_math_bits_external_tests_with_the_pinned_closure_stop_inside_testing():
    _, files = project.load_inputs()
    _, std = load_std_closure()
    source = lambda names: [(name, files[project.PACKAGE_DIR + '/' + name].decode('utf-8')) for name in names]
    packages = {path: units for path, units in std.items() if path != project.PACKAGE}
    packages.update({
        'main': [('m.go', 'package main;import _ "math/bits_test";func main(){}')],
        project.PACKAGE: source(project.NORMAL + project.INTERNAL_TEST),
        project.PACKAGE + '_test': source(project.EXTERNAL_TEST),
    })
    # Formerly bits_test.go:10:2, `import "testing"` without its sources. The
    # original testing package now parses until AllocsPerRun's float64 result.
    with pytest.raises(UnsupportedGo, match=r"^allocs.go:20:44: Go: unsupported type 'float64'") as caught:
        compile_go_packages(packages, target_triple='x86_64-unknown-linux-gnu')
    assert caught.value.code == 'go.unsupported.floating-types'
    assert std['testing'][0][0] == 'allocs.go'
