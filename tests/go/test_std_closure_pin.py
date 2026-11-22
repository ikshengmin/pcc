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

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
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
    # The union passes the nested channel send and stops at the following
    # receive expression. This is not a platform build selection.
    with pytest.raises(GoDiagnostic, match=r"^benchmark\.go:247:2: Go: unsupported expression token '<-'$") as caught:
        compile_go_packages(packages, target_triple="x86_64-unknown-linux-gnu")
    assert (caught.value.filename, caught.value.line, caught.value.column) == ('benchmark.go', 247, 2)
    assert [name for name, _ in std['testing'][:2]] == ['allocs.go', 'benchmark.go']


def test_pinned_benchmark_method_keeps_the_real_previously_blocked_bound():
    from pcc.frontends.go.syntax import GoParser

    _, std = load_std_closure()
    benchmark = dict(std['testing'])['benchmark.go']
    # Examine the complete unchanged Set declaration; the extra package line
    # only makes it an independent parser input, not a compiled package.
    begin = benchmark.index('func (f *durationOrCountFlag) Set(s string) error {')
    end = benchmark.index('\n}\n', begin) + len('\n}\n')
    method = benchmark[begin:end]
    assert 'strconv.ParseInt(s[:len(s)-1], 10, 0)' in method
    parsed = GoParser().parse('package testing\n'+method, 'benchmark.go', require_main=False)
    call = parsed.functions[0].body[0].body[0].exprs[0]
    assert (call.kind, call.value, call.callee.args[0].value) == ('call', 'ParseInt', 'strconv')
    view = call.args[0]
    assert view.kind == 'slice' and view.slice_bounds == (False, True, False)
    base, high = view.args
    length, one = high.args
    assert (base.value, high.kind, high.value) == ('s', 'binary', '-')
    assert (length.kind, length.value, length.args[0].value, one.value) == ('call', 'len', 's', '1')


@pytest.mark.integration
def test_previous_benchmark_bound_executes_when_proven_and_stays_closed_otherwise(tmp_path, monkeypatch):
    from tests.go.owned_execution import Execution, compile_owned, run, write_artifact
    from tests.go.test_execution import forbid_external_compiler

    # Same spelling, known nonempty constant input: this is a slice behavior
    # check, not a claim that the complete dynamic testing.Set can execute.
    original = 'package main;func Result()int{const s="314x";v:=s[:len(s)-1];if v=="314"{return 42};return 0};func main(){}'
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('bound.go', original),), observe_result=True)
        with pytest.raises(UnsupportedGo) as unproven:
            compile_go('package main;func F(s string)string{return s[:len(s)-1]};func main(){}')
        assert unproven.value.code == 'go.unsupported.string-bounds-panic'
    native = tmp_path / 'known-bound'
    write_artifact(artifact, native)
    assert run(native) == Execution(0, (42).to_bytes(8, 'little'), b'')


def test_reference_gap_receipt_separates_missing_runtime_and_changed_vendored_bytes():
    from scripts.go_std_closure import target_selection_gaps

    lock = {
        "roots": ["os", "fmt"],
        "owned_excluded": ["runtime", "runtime/...", "internal/runtime/...", "unsafe"],
        "files": [
            {"path": "src/os/file.go", "sha256": "os-hash"},
            {"path": "src/os/file_darwin.go", "sha256": "darwin"},
            {"path": "src/fmt/print.go", "sha256": "locked-fmt"},
        ],
    }
    selected = {
        "src/math/bits/bits_test.go": project.sha256(b"original"),
        "src/os/file.go": "os-hash",
        "src/fmt/print.go": "different-fmt",
        "src/internal/runtime/maps/map.go": "runtime-go",
        "src/internal/runtime/sys/abi_amd64.s": "runtime-assembly",
        "src/internal/runtimeX/other.go": "not-runtime",
    }
    receipt = target_selection_gaps(selected, {"src/math/bits/bits_test.go": b"original"}, lock)
    assert receipt["status"] == "LOCKED_BYTES_DIFFER"
    assert receipt["selected_files"] == 6 and receipt["matching_files"] == 2
    assert receipt["changed_locked_files"] == ["src/fmt/print.go"]
    assert receipt["union_not_selected"] == 1
    assert receipt["missing_by_role"] == {
        "compiler_owned": {"go": 1, "assembly": 1},
        "other": {"go": 1},
    }
    assert receipt["roots"]["os"] == {"selected_go": 1, "matched_go": 1}


def test_reference_gap_receipt_refuses_inconsistent_original_lock_overlap():
    from scripts.go_std_closure import target_selection_gaps

    path = "src/math/bits/bits.go"
    lock = {"files": [{"path": path, "sha256": "not-original"}], "owned_excluded": [], "roots": []}
    with pytest.raises(ValueError, match="disagrees with pinned original"):
        target_selection_gaps({path: "selected"}, {path: b"original"}, lock)


def test_linux_profiling_source_pin_checks_selected_bytes_without_granting_admission():
    from scripts.go_std_closure import load_linux_profiling_pin, verify_linux_profiling_selection

    pin = load_linux_profiling_pin()
    selected = {entry["path"]: entry["sha256"] for entry in pin["files"]}
    assert len(selected) == 22
    assert pin["complete_build_closure"] is False
    assert verify_linux_profiling_selection(selected, project.REFERENCE_BUILD_IDS_SHA256) == 22
    path, digest = selected.popitem()
    with pytest.raises(ValueError, match="profiling source differs"):
        verify_linux_profiling_selection(selected, project.REFERENCE_BUILD_IDS_SHA256)
    with pytest.raises(ValueError, match="profiling source differs"):
        verify_linux_profiling_selection({**selected, path: "0" * 64}, project.REFERENCE_BUILD_IDS_SHA256)
    with pytest.raises(ValueError, match="profiling selection identity differs"):
        verify_linux_profiling_selection({**selected, path: digest}, "0" * 64)
