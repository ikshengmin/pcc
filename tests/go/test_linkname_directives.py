"""//go:linkname validation and binding to owned runtime objects.

cmd/compile (https://pkg.go.dev/cmd/compile#hdr-Compiler_Directives) admits
the directive only in files that import unsafe, and its local name must be a
declared package function or variable. The only bindable targets are the
owned runtime error values of runtime/panic.go. The runtime owns the bound
variable's storage, so the package declares only a reference (no global, no
initializer). The whole operand of a terminal panic reports the runtime
object's Error() text (test_runtime_error_panics.py); every other value use
needs the owned runtime error object and interface values, an explicit gap.
Every other target, the one-name form and function aliases stay explicit gaps.
The unchanged math/bits bits_errors.go is the first project input.
"""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.runtime_provider import RUNTIME_ERROR_VALUES, runtime_object
from pcc.frontends.go.syntax import GoParser


HEADER = 'package main\nimport _ "unsafe"\n'
INVALID = (
    ('without-unsafe-import', 'package main\n//go:linkname e runtime.divideError\nvar e error\nfunc main(){_=e}\n',
     'only allowed in Go files that import "unsafe"', (2, 1)),
    ('undeclared-local', HEADER + '//go:linkname missing runtime.divideError\nfunc main(){}\n',
     'must refer to declared function or variable', (3, 1)),
    ('constant-local', HEADER + '//go:linkname C runtime.divideError\nconst C = 1\nfunc main(){_=C}\n',
     'must refer to declared function or variable', (3, 1)),
    ('initialized-variable', HEADER + '//go:linkname e runtime.divideError\nvar e error = nil\nfunc main(){_=e}\n',
     'duplicated definition of symbol runtime.divideError', (4, 5)),
)
GAPS = (
    ('one-name-form', HEADER + '//go:linkname X\nvar X int\nfunc main(){}\n',
     'go.unsupported.linkname-directives', (3, 1)),
    ('other-package-target', HEADER + '//go:linkname X dep.X\nvar X int\nfunc main(){}\n',
     'go.unsupported.linkname-directives', (3, 1)),
    ('unowned-runtime-target', HEADER + '//go:linkname e runtime.unknownError\nvar e error\nfunc main(){_=e}\n',
     'go.unsupported.linkname-directives', (3, 1)),
    ('function-alias', HEADER + '//go:linkname f runtime.divideError\nfunc f(){}\nfunc main(){}\n',
     'go.unsupported.linkname-directives', (3, 1)),
    ('different-declared-type', HEADER + '//go:linkname e runtime.divideError\nvar e int\nfunc main(){_=e}\n',
     'go.unsupported.linkname-directives', (3, 1)),
    ('bound-runtime-error-value', HEADER + '//go:linkname e runtime.divideError\nvar e error\nfunc main(){_=e}\n',
     'go.unsupported.interface-types', (5, 15)),
    ('named-unsafe-import', 'package main\nimport "unsafe"\nfunc main(){_=unsafe.Sizeof(0)}\n',
     'go.unsupported.unsafe-operations', (2, 8)),
)


def test_runtime_error_values_follow_runtime_panic_text():
    assert {symbol: value.error_text for symbol, value in RUNTIME_ERROR_VALUES.items()} == {
        'runtime.divideError': 'runtime error: integer divide by zero',
        'runtime.overflowError': 'runtime error: integer overflow',
    }
    assert runtime_object('runtime.unknownError') is None and runtime_object(None) is None


def test_directives_are_recorded_per_file_without_rewriting_source():
    program = GoParser().parse(HEADER + '//go:linkname e runtime.overflowError\nvar e error\nfunc main(){}\n',
                               'original.go')
    directive, = program.linknames
    assert (directive.local, directive.target) == ('e', 'runtime.overflowError')
    assert (directive.token.line, directive.token.column) == (3, 1)


def test_bound_variable_never_becomes_an_ordinary_nil_global(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('runtime-owned error reached IR'))
    with pytest.raises(UnsupportedGo, match='runtime-owned runtime.overflowError values require the owned runtime'):
        compile_go(HEADER + '//go:linkname e runtime.overflowError\nvar e error\nfunc main(){_=e}\n', 'original.go')


@pytest.mark.parametrize('use', ['_=e', 'if e!=nil{}', 'var f error=e;_=f', 'panic(e!=nil)'])
def test_every_value_use_of_a_runtime_owned_error_is_an_explicit_gap(use, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('runtime-owned error reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(HEADER + '//go:linkname e runtime.divideError\nvar e error\nfunc main(){' + use + '}\n',
                   'original.go')
    assert caught.value.code == 'go.unsupported.interface-types'
    assert 'runtime-owned runtime.divideError values' in caught.value.message


def test_unused_runtime_owned_declaration_compiles_without_package_storage():
    # The runtime owns the variable: no global, initializer or symbol reference.
    module = compile_go(HEADER + '//go:linkname e runtime.divideError\nvar e error\n'
                        '//go:linkname o runtime.overflowError\nvar o error\nfunc main(){}\n', 'original.go')
    text = str(module)
    assert 'divideError' not in text and 'overflowError' not in text
    assert not any(line.startswith('@') and ' global ' in line for line in text.splitlines())


@pytest.mark.parametrize('name,original,message,position', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_linkname_uses_are_ordinary_diagnostics(name, original, message, position, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid directive reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic
    assert (caught.value.line, caught.value.column) == position


@pytest.mark.parametrize('name,original,code,position', GAPS, ids=[row[0] for row in GAPS])
def test_remaining_linkname_and_unsafe_uses_are_explicit_gaps(name, original, code, position, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unowned directive reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code
    assert (caught.value.line, caught.value.column) == position
