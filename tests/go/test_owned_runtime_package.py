"""The owned, partial runtime package.

https://go.dev/ref/spec#Import_declarations; Go runtime/error.go (the
`Error` interface contract). Package runtime is runtime-coupled, so it is
owned by the compiler rather than compiled from host or caller sources
(docs/multilanguage-roadmap.md). When no source is supplied for it, imports
of "runtime" bind PCC's owned package, which implements `Error`
(`interface { error; RuntimeError() }`); every other exported name is the
explicit go.unsupported.owned-runtime-surface boundary, while unexported
names and an unused import stay ordinary errors. math/bits' bits_test.go
imports runtime for `runtime.Error`. Original sources; the reference column
was compared with a local development go1.23.6.
"""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go_packages
from pcc.frontends.go.packages import OWNED_PACKAGES
from pcc.frontends.go.syntax import GoParser
from scripts import ci_go_math_bits_project as project
from tests.go.darwin_execution import REFERENCE_UNAVAILABLE, reference_rejection

TARGET = 'x86_64-unknown-linux-gnu'


def compile_main(source):
    return compile_go_packages({'main': [('a.go', source)]}, target_triple=TARGET)


def test_owned_runtime_declares_the_error_interface():
    (name, text), = OWNED_PACKAGES['runtime']
    unit = GoParser().parse(text, name, require_main=False)
    assert unit.token.text == 'runtime' and [dtype.token.text for dtype in unit.types] == ['Error']


VALID = 'package main\nimport "runtime"\ntype E = runtime.Error\nfunc main() {}\n'


def test_runtime_error_resolves_through_the_owned_package():
    compile_main(VALID)


@pytest.mark.parametrize('source,message', [
    ('package main\nimport "runtime"\nfunc main() { _ = runtime.GOOS }\n', 'does not implement GOOS yet'),
    ('package main\nimport "runtime"\nfunc main() { runtime.GC() }\n', 'does not implement GC yet'),
])
def test_other_runtime_names_are_an_explicit_boundary(source, message):
    with pytest.raises(UnsupportedGo, match=message) as caught:
        compile_main(source)
    assert caught.value.code == 'go.unsupported.owned-runtime-surface'


@pytest.mark.parametrize('source,message', [
    ('package main\nimport "runtime"\nfunc main() {}\n', 'imported and not used: runtime'),
    ('package main\nimport "runtime"\nfunc main() { _ = runtime.error }\n', 'unexported'),
])
def test_ordinary_import_errors_remain(source, message):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_main(source)
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_gives_the_same_verdicts(tmp_path):
    cases = ((VALID, True),
             ('package main\nimport "runtime"\nfunc main() { _ = runtime.GOOS }\n', True),
             ('package main\nimport "runtime"\nfunc main() {}\n', False))
    for index, (source, accepted) in enumerate(cases):
        directory = tmp_path / str(index)
        directory.mkdir()
        returncode, stderr = reference_rejection((('a.go', source),), directory)
        assert (returncode == 0) == accepted, stderr


def test_math_bits_external_test_package_now_stops_at_the_testing_import():
    _, files = project.load_inputs()
    source = lambda names: [(name, files[project.PACKAGE_DIR + '/' + name].decode('utf-8')) for name in names]
    packages = {
        'main': [('m.go', 'package main;import _ "math/bits_test";func main(){}')],
        project.PACKAGE: source(project.NORMAL + project.INTERNAL_TEST),
        project.PACKAGE + '_test': source(project.EXTERNAL_TEST),
    }
    # Formerly bits_test.go:458:23, :461:3, :716:10, :851:4, :1038:26 and
    # :9:2 (this runtime import). Without the pinned standard-library closure,
    # `testing` has no source; tests/go/test_std_closure_pin.py supplies it.
    with pytest.raises(GoDiagnostic, match=r'bits_test.go:10:2: Go: import source unavailable: testing'):
        compile_go_packages(packages, target_triple=TARGET)
