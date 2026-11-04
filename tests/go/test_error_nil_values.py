"""Nil-only error values, not boxing, dispatch, runtime globals or Go ABI.

https://go.dev/ref/spec#Errors https://go.dev/ref/spec#The_zero_value
https://go.dev/ref/spec#Assignability https://go.dev/ref/spec#Method_sets
https://go.dev/ref/spec#Method_declarations https://go.dev/ref/spec#Comparison_operators

Complete native and identical official-Go execution obligations use the
existing test_execution.CASES entry for error_nil_values.go. All authored
execution obligations remain UNRUN; the original math/bits closure is rejected.
"""
from copy import deepcopy

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.packages import parse_package
from pcc.frontends.go.types import ERROR_TYPE, PointerType, is_error_interface
from tests.go.test_execution import forbid_external_compiler, units


def source(body='', declarations=''):
    return 'package main;' + (declarations + ';' if declarations else '') + 'func main(){' + body + '}'


GOOD_METHOD = 'type T int;func(t T)Error()string{return "message"}'


def test_predeclared_error_is_canonical_and_not_a_semantic_pointer_or_string():
    program = parse_package((('original.go', source('', 'type A=error;func f(e error)error{return e}')),), require_main=True)
    parameter = program.functions[0].params[0][1]
    assert parameter is ERROR_TYPE and deepcopy(parameter) is ERROR_TYPE
    assert is_error_interface(parameter) and not isinstance(parameter, PointerType)
    assert parameter != 'string' and str(parameter) == 'error'


@pytest.mark.parametrize('original', [
    source('var e error;_=e==nil;_=nil!=e'),
    source('var e error=nil;e=error(nil);_=e'),
    source('var e error;var n E=e;e=n;_=e', 'type E error'),
    source('var e A;var f error=e;_=f', 'type A=error'),
    source('_=f(nil)', 'func f(e error)error{return e}'),
    source('_=f()', 'func f()(e error){return}'),
    source('a,b:=f();_=a==b', 'func f()(error,error){return nil,nil}'),
    source('_=f(1)', 'func f[T any](x T)error{var e error;_=x;return e}'),
])
def test_zero_nil_copy_alias_and_direct_call_paths_keep_owned_ir(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


def test_complete_program_and_cross_package_nil_error_transport_are_owned(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(units(('error_nil_values.go',))[0][1])
    compile_go_packages({
        'main': [('main.go', 'package main;import "lib";func main(){var e error;_=value.Pass(e)==nil}')],
        'lib': [('lib.go', 'package value;type E error;func Pass(e error)E{return e}')],
    })


INVALID = [
    (source('var e error=1;_=e'), 'does not implement error'),
    (source('var e error="x";_=e'), 'does not implement error'),
    (source('var e error=T(0);_=e', 'type T int'), 'does not implement error'),
    (source('var e error=T(0);_=e', 'type T int;func(t T)Error(x int)string{return "x"}'), 'does not implement error'),
    (source('var e error=T(0);_=e', 'type T int;type S string;func(t T)Error()S{return "x"}'), 'does not implement error'),
    (source('var e error=T(0);_=e', 'type T int;func(t T)Error()(string,int){return "x",1}'), 'does not implement error'),
    (source('var t T;var e error=t;_=e', 'type T int;func(t *T)Error()string{return "x"}'), 'does not implement error'),
    (source('var e error;_=e<e'), 'permit only equality'),
    (source('var e error;_=e+e'), 'permit only equality'),
    (source('const e error=nil;_=e'), 'not a constant'),
    (source('const b=error(nil)==nil;_=b'), 'not a constant'),
    (source('x:=nil;_=x'), 'cannot use untyped nil'),
    (source('', 'type E error;func(e E)M(){}'), 'receiver base cannot be an interface'),
]


@pytest.mark.parametrize('original,message', INVALID)
def test_invalid_error_assignment_signature_operation_and_constant_rules(original, message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid error value reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as failure:
        compile_go(original)
    assert not isinstance(failure.value, UnsupportedGo)


UNSUPPORTED = [
    ('interface-types', source('var e error=T(0);_=e', GOOD_METHOD)),
    ('interface-types', source('var p *T;var e error=p;_=e', GOOD_METHOD)),
    ('interface-types', source('var p *T;var e error=p;_=e', 'type T int;func(t *T)Error()string{return "x"}')),
    ('interface-types', source('var e error=T(0);_=e', 'type S=string;type T int;func(t T)Error()S{return "x"}')),
    ('interface-types', source('var e error;_=e.Error()')),
    ('interface-types', source('var e error;_=e.Error')),
    ('interface-types', source('_=error.Error(nil)')),
    ('interface-types', source('_=error.Error')),
    ('interface-types', source('var e error;switch e{case nil:}')),
    ('interface-types', source('var e error;_=&e')),
    ('interface-types', source('var p *error;_=p')),
    ('interface-types', source('_=(*error)(nil)')),
    ('interface-types', source('', 'type S struct{e error}')),
    ('interface-types', source('_=e', 'var e error')),
    ('interface-types', source('_=e', 'var e=error(nil)')),
    ('interface-types', source('var a any;_=a')),
    ('array-element-types', source('var a [0]error;_=a')),
    ('generic-boundaries', source('var e error;_=f(e)', 'func f[T any](x T)T{return x}')),
    ('generic-boundaries', source('_=f[error](nil)', 'func f[T any](x T)T{return x}')),
    ('generic-boundaries', source('_=f[A](nil)', 'type A=error;func f[T any](x T)T{return x}')),
    ('builtin-io', source('var e error;panic(e)')),
    ('builtin-io', source('var e error;print(e)')),
]


@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_concrete_dynamic_storage_and_runtime_boundaries_stay_closed(original, code, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unowned error value reached IR'))
    with pytest.raises(UnsupportedGo) as failure:
        compile_go(original)
    assert failure.value.code == 'go.unsupported.' + code


def test_linknamed_package_error_cannot_become_a_fabricated_nil_global(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    original = 'package main\n//go:linkname e runtime.overflowError\nvar e error\nfunc main(){_=e}\n'
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('runtime-owned error became an ordinary global'))
    with pytest.raises(UnsupportedGo) as failure:
        compile_go(original, 'original.go')
    # Preserve the exact original. Its newly recognized compiler directive
    # fails before the independent package-interface storage frontier.
    # This early capability boundary does not certify the rest of the source.
    assert failure.value.code == 'go.unsupported.linkname-directives'
    assert (failure.value.filename, failure.value.line) == ('original.go', 2)
