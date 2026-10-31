"""Original generic-function declaration/move cases, not core/alloc success."""
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TypeParameter
from pcc.support.instantiation import InstanceRegistry, InstanceState
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross

PROGRAMS = ('generic_functions', 'generic_function_records', 'generic_function_payloads',
            'generic_function_recursion', 'generic_function_order', 'generic_function_divergence')
RETAINED_GENERIC_FUNCTION = 'fn f<T>(x:T){}'
RETAINED_GENERIC_IDENTITY = 'fn unused<T>(value:T)->T{value}'


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


INVALID = [
    ('fn bad<T,T>(x:T){}', 'duplicate generic parameter'),
    ('fn bad<T>(x:Unknown)->T{x}', 'unsupported Rust type'),
    ('fn bad<T>(x:T)->U{x}', 'unsupported Rust type'),
    ('fn bad<T>(x:T)->T{missing(x)}', 'unknown function'),
    ('fn bad<T>(x:T)->T{x+x}', 'operator'),
    ('fn bad<T>(x:T,y:T)->bool{x==y}', 'operator'),
    ('fn bad<T>(x:T)->T{1}', 'type mismatch'),
    ('fn bad<T,U>(x:T)->U{x}', 'type mismatch'),
    ('fn bad<T>(x:T)->bool{if x{true}else{false}}', 'type mismatch'),
    ('fn bad<T>(x:T)->i64{x as i64}', 'supported casts'),
    ('fn bad<T>(x:i64)->T{x as T}', 'supported casts'),
    ('fn bad<T>(x:T)->T{x} fn answer()->i64{bad(1)}', 'explicit type arguments'),
    ('fn id<T>(x:T)->T{x} fn answer()->i64{id::<i64,u64>(1)}', 'argument count'),
    ('fn id(x:i64)->i64{x} fn answer()->i64{id::<i64>(1)}', 'nongeneric'),
    ('fn id<T>(x:T)->T{x} fn answer()->i64{id::<bool>(true)}', 'type mismatch'),
    ('fn id<T>(x:T)->T{x} fn answer()->i64{id::<i64>()}', 'expects 1 arguments'),
    ('fn id<T>(x:T)->T{x} fn answer(){let id=1;id::<i64>(2);}', 'unshadowed'),
    ('fn bad<T:Copy>(x:T)->T{x}', 'bounds and default'),
    ('fn bad<T=i64>(x:T)->T{x}', 'bounds and default'),
    ('fn bad<T>(x:T)->T where T:Copy{x}', 'where constraints'),
    ('fn bad<const N:u64>(){}', 'const generic'),
    ('extern "C" fn bad<T>(x:T){}', 'foreign/export ABI'),
    ('#[no_mangle] fn bad<T>(x:T){}', 'foreign/export ABI'),
    ('#[repr(C)] struct R{x:i64} fn id<T>(x:T)->T{x} fn f(x:&R){id::<&R>(x);}', 'reference arguments'),
]


@pytest.mark.parametrize('source,message', INVALID)
def test_declarations_and_calls_fail_before_lowering_even_when_uncalled(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid generic body reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.parametrize('body', [
    'fn bad<T>(x:T)->(T,T){(x,x)}',
    'fn bad<T>(x:T)->T{let y=x;x}',
    'fn bad<T>(x:T,b:bool)->T{if b{let y=x;}x}',
    'fn take<T>(x:T){} fn bad<T>(x:T){while true{take::<T>(x);}}',
])
@pytest.mark.parametrize('used', [False, True])
def test_abstract_type_is_not_copy_even_when_only_concrete_arguments_are_copy(body, used, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid abstract move reached IR'))
    # The declaration error must occur before any call-site arity or result
    # obligation. Adding a primitive instantiation cannot make T become Copy.
    source = body + (' fn answer(){bad::<u64>(7);}' if used else '')
    with pytest.raises(RustError, match='moved'):
        compile_source(source)


@pytest.mark.parametrize('source', [
    RETAINED_GENERIC_FUNCTION,
    RETAINED_GENERIC_IDENTITY,
    'fn unused<T>(){}',  # Unused function type parameters are legal, unlike data parameters.
    'fn id<T>(x:T)->T{x} fn answer()->i64{id::<i64>(42)}',
    'fn id<T>(x:T)->T{x} fn answer(){id::<()>(());}',
    'fn id<T>(x:T)->T{match x{value=>value}}',
    'fn pair<T,U>(x:T,y:U)->(T,U){(x,y)}',
    'fn choose<T>(x:T,y:T,b:bool)->T{if b{x}else{y}}',
    'fn replace<T>(mut x:T,y:T)->T{x=y;x}',
    'struct R{x:i64} fn id<T>(x:T)->T{x} fn answer()->i64{id::<R>(R{x:42}).x}',
    'fn id<T>(x:T)->T{x} fn outer<U>(x:U)->U{id::<U>(x)}',
])
def test_valid_abstract_and_concrete_function_shapes(source):
    compile_source(source)


def test_unused_template_is_checked_but_neither_instantiated_nor_emitted():
    parser = Parser(RETAINED_GENERIC_IDENTITY)
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums)
    checker.check()
    assert isinstance(functions[0].params[0][1], TypeParameter)
    assert not checker.copy_type(functions[0].params[0][1])
    assert len(checker.function_generics.instances) == 0
    result = compile_source(RETAINED_GENERIC_IDENTITY)
    assert not result.symbols and 'define ' not in result.ir_text


def test_shared_registry_reuses_in_progress_function_placeholder(monkeypatch):
    source = ('fn walk<T>(n:i64,x:T)->T{if n==0{x}else{walk::<T>(n-1,x)}}'
              'fn answer()->u64{walk::<u64>(3,42)}')
    seen = []
    original = InstanceRegistry.get
    def observe(self, key):
        entry = original(self, key)
        if entry is not None and entry.state is InstanceState.IN_PROGRESS:
            seen.append(entry)
        return entry
    monkeypatch.setattr(InstanceRegistry, 'get', observe)
    parser = Parser(source)
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums)
    checker.check()
    registry = checker.function_generics.instances
    assert isinstance(registry, InstanceRegistry) and len(registry) == 1
    entry = next(iter(registry.entries.values()))
    assert entry in seen and entry.value is entry.result
    assert entry.state is InstanceState.COMPLETE
    assert entry.value.instance_arguments == ('u64',)
    assert entry.value in functions and len(functions) == 3


def test_complete_argument_identity_and_deterministic_private_symbols():
    source = ('fn id<T>(x:T)->T{x} fn other<T>(x:T)->T{x}'
              'fn answer()->u64{let a=id::<u64>(42);let b=id::<i64>(-1);'
              'let c=other::<u64>(a);id::<u64>(c)}')
    first, second = compile_source(source), compile_source(source)
    assert first.ir_text == second.ir_text and first.symbols == second.symbols
    assert set(first.symbols) == {'answer', 'id::<u64>', 'id::<i64>', 'other::<u64>'}
    assert len(set(first.symbols.values())) == 4
    assert first.emit_object() == second.emit_object()


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_original_generic_function_program_runs_full_width_owned_native(name, tmp_path, monkeypatch):
    _native(name, tmp_path, monkeypatch)


@pytest.mark.parametrize('name', PROGRAMS)
def test_original_generic_function_program_cross_emits_arm64_macho(name, monkeypatch):
    _cross(name, monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('name', PROGRAMS)
def test_development_only_rustc_checks_identical_generic_function_source(name, tmp_path):
    _rustc(name, tmp_path)
