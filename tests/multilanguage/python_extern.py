"""Source-only provider inputs for a compiled-Python, one-executable ABI gate.

The Rust providers are value-only experimental inputs. The legacy opt-in is
caller attestation, not an external rustc certificate or owned borrow checking.
No Java bytecode route, runtime adapter, generated C wrapper, or host compiler
implements any provider's semantics.
"""
from dataclasses import dataclass

from tests.multilanguage.owned_execution import (
    FIXTURES, JAVA_CLASS, JAVA_DESCRIPTOR, JAVA_METHOD, TARGET,
)

INPUT_FILES = ('python_scalars.cpp', 'Interop.java', 'scalars.go', 'python_scalars.rs')
CALLER = FIXTURES / 'python_scalar_caller.py'
DELTA64 = 1 + 2 + 4 + 8 + 16 + 0x100000020 + 0x200000040
WEIGHTED_DELTA64 = 1 + 2*2 + 3*4 + 4*8 + 5*16 + 6*0x100000020 + 7*0x200000040
EXPECTED = (
    -7000000000000 - WEIGHTED_DELTA64,
    -2000000000 - 127,
    -5000000000000 - DELTA64,
    -1000000000 - 127,
    5000000000000 + WEIGHTED_DELTA64,
    -2000000000 - 127 - DELTA64,
)
EXPECTED_STDOUT = ''.join(str(value) + '\n' for value in EXPECTED).encode('ascii')


@dataclass(frozen=True)
class Providers:
    inputs: tuple[tuple[str, bytes], ...]
    ir: tuple[tuple[str, str], ...]
    objects: tuple[tuple[str, bytes], ...]
    exports: tuple[tuple[str, tuple[str, ...]], ...]


def compile_providers():
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.frontends.go import compile_go_sources, function_symbol
    from pcc.frontends.java import compile_source, method_symbol
    from pcc.frontends.rust import compile_rust

    inputs = tuple((name, (FIXTURES / name).read_bytes()) for name in INPUT_FILES)
    cpp_source, java_source, go_source, rust_source = (data.decode('utf-8') for _, data in inputs)
    ((name, cpp_ir, _returns, _definitions),) = CEvaluator(
        target_triple=TARGET, backend='self',
    ).compile_translation_units(
        [TranslationUnit(INPUT_FILES[0], None, cpp_source)],
        use_system_cpp=False, use_compile_cache=False, frontend_opt_level=0,
    )
    assert name == INPUT_FILES[0]
    java = compile_source(java_source, filename=INPUT_FILES[1], entry=JAVA_METHOD,
                          descriptor=JAVA_DESCRIPTOR, target=TARGET)
    go = compile_go_sources(((INPUT_FILES[2], go_source),), target_triple=TARGET)
    rust = compile_rust(rust_source, experimental_rustc_checked=True,
                        overflow_checks=False, module_name='python_scalar_interop',
                        target_triple=TARGET)
    assert rust.experimental and not rust.borrow_checked_by_pcc and not rust.externally_checked
    exports = (
        ('cpp', ('python_cpp_mix',)),
        ('java', (method_symbol(JAVA_CLASS, JAVA_METHOD, JAVA_DESCRIPTOR),)),
        ('go', (function_symbol('Fold'),)),
        ('rust', (rust.symbols['python_mix32'], rust.symbols['python_mix64'])),
    )
    assert java.entry_symbol == exports[1][1][0]
    caller = CALLER.read_bytes()
    for _language, symbols in exports:
        for symbol in symbols:
            assert ("extern('" + symbol + "'").encode('ascii') in caller
    ir = (('cpp', cpp_ir), ('java', str(java.module)),
          ('go', str(go)), ('rust', rust.ir_text))
    objects = tuple((language, emit_owned_object(text, TARGET)) for language, text in ir)
    return Providers(inputs, ir, objects, exports)
