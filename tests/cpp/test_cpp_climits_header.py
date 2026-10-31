"""Target-correct owned <climits>; no multibyte/locale runtime qualification.

Original PCC cases based on N4659 [climits.syn] and ISO C 5.2.4.2.1.
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
The MB_LEN_MAX macro mirrors the existing C compatibility header's compile-time
capacity declaration, not a validated runtime locale policy or host-libc parity.
These tests cannot certify absent locale or multibyte runtime operations.
"""
import os
import re
import shutil
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppParser
from pcc.frontends.cpp.constants import KNOWN
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import Execution, TARGET
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_public_driver import compile_cpp
from tests.cpp.test_cpp_public_driver_execution import link_object


TARGETS = (
    ('x86_64-unknown-linux-gnu', 64, True),
    ('x86_64-pc-windows-msvc', 32, True),
    ('aarch64-unknown-linux-gnu', 64, False),
)
# Full target constant value and promoted macro type, not only decimal text.
LIMITS = (
    ('CHAR_BIT', 'int', 8), ('SCHAR_MIN', 'int', -128), ('SCHAR_MAX', 'int', 127),
    ('UCHAR_MAX', 'int', 255), ('SHRT_MIN', 'int', -32768), ('SHRT_MAX', 'int', 32767),
    ('USHRT_MAX', 'int', 65535), ('INT_MIN', 'int', -2147483648),
    ('INT_MAX', 'int', 2147483647), ('UINT_MAX', 'unsigned int', 4294967295),
    ('LLONG_MIN', 'long long', -9223372036854775808),
    ('LLONG_MAX', 'long long', 9223372036854775807),
    ('ULLONG_MAX', 'unsigned long long', 18446744073709551615),
    ('MB_LEN_MAX', 'int', 16),
)


def target_limits(long_bits, signed_char):
    return LIMITS + (
        ('CHAR_MIN', 'int', -128 if signed_char else 0),
        ('CHAR_MAX', 'int', 127 if signed_char else 255),
        ('LONG_MIN', 'long', -(1 << (long_bits - 1))),
        ('LONG_MAX', 'long', (1 << (long_bits - 1)) - 1),
        ('ULONG_MAX', 'unsigned long', (1 << long_bits) - 1),
    )


@pytest.mark.parametrize('target,long_bits,signed_char', TARGETS)
def test_climits_values_are_typed_target_constant_expressions(target, long_bits, signed_char):
    limits = target_limits(long_bits, signed_char)
    source = '#include <climits>\n#include <climits>\n'
    source += ''.join('const ' + dtype + ' value_' + name + '=' + name + ';' for name, dtype, _ in limits)
    parser = CppParser(target_triple=target)
    parser.parse(preprocess_cpp(source, target_triple=target))
    for name, dtype, value in limits:
        fact = parser.scope.names['value_' + name].constant_fact
        assert fact.status == KNOWN and fact.value == value
        assert fact.dtype == dtype


@pytest.mark.parametrize('target,long_bits,signed_char', TARGETS)
def test_climits_macros_choose_their_promoted_overload_types(target, long_bits, signed_char):
    codes = {'int': 'i', 'unsigned int': 'j', 'long': 'l', 'unsigned long': 'm',
             'long long': 'x', 'unsigned long long': 'y'}
    source = '#include <climits>\n' + ''.join('int pick(' + dtype + ');' for dtype in codes)
    limits = target_limits(long_bits, signed_char)
    source += ''.join('int probe_' + name + '(){return pick(' + name + ');}' for name, _, _ in limits)
    parser = CppParser(target_triple=target)
    ast = parser.parse(preprocess_cpp(source, target_triple=target))
    calls = []
    class Calls(c_ast.NodeVisitor):
        def visit_FuncCall(self, node):
            calls.append(node.name.name)
    Calls().visit(ast)
    assert calls == ['_Z4pick' + codes[dtype] for _, dtype, _ in limits]


@pytest.mark.parametrize('target,long_bits,signed_char', TARGETS)
def test_every_limit_is_usable_in_a_preprocessor_condition(target, long_bits, signed_char):
    source = '#include <climits>\n'
    for name, _dtype, value in target_limits(long_bits, signed_char):
        # Unsigned suffix is necessary for UINT/ULONG/ULL maxima in #if, too.
        expected = str(value) + ('ULL' if value > 9223372036854775807 else 'LL')
        if value == -9223372036854775808:
            expected = '(-9223372036854775807LL - 1LL)'
        source += '#if ' + name + ' != ' + expected + '\n#error incorrect ' + name + '\n#endif\n'
    source += 'int main(){return 42;}'
    assert 'return 42' in preprocess_cpp(source, target_triple=target)


def test_climits_does_not_import_fake_c_typedefs_or_unrelated_macros():
    source = '#include <climits>\n'
    for name in ('EOF', 'NULL', 'MB_CUR_MAX'):
        source += '#ifdef ' + name + '\n#error leaked ' + name + '\n#endif\n'
    text = preprocess_cpp(source + 'int main(){return 42;}')
    assert 'typedef' not in text
    CppParser().parse(text)


@pytest.mark.parametrize('target,long_bits,signed_char', TARGETS)
def test_c_and_cpp_multibyte_capacity_declarations_agree_without_runtime_claim(target, long_bits, signed_char):
    from pcc.frontends.c.preprocessor import preprocess

    sentinel = '\nPCC_CAPACITY_OBSERVER MB_LEN_MAX\n'
    c_text = preprocess('#include <limits.h>' + sentinel, target_triple=target)
    cpp_text = preprocess_cpp('#include <climits>' + sentinel, target_triple=target)
    for text in (c_text, cpp_text):
        assert re.findall(r'PCC_CAPACITY_OBSERVER\s+(\d+)', text) == ['16']


PROGRAMS = (
    '#include <climits>\nint main(){if(CHAR_BIT!=8||CHAR_MIN!=-128||CHAR_MAX!=127)return 1;'
    'if(SCHAR_MIN!=-128||UCHAR_MAX!=255||SHRT_MIN!=-32768||USHRT_MAX!=65535)return 2;'
    'if(INT_MIN!=-2147483647-1||UINT_MAX!=4294967295U)return 3;return 42;}',
    '#include <climits>\nint main(){if(LONG_MAX!=9223372036854775807L)return 1;'
    'if(LONG_MIN!=-9223372036854775807L-1L||ULONG_MAX!=18446744073709551615UL)return 2;'
    'if(LLONG_MIN!=-9223372036854775807LL-1LL||ULLONG_MAX!=18446744073709551615ULL)return 3;return 42;}',
    '#include <climits>\nint choose(unsigned int){return 17;}int choose(long){return 25;}'
    'int choose(int){return 1;}int main(){return choose(UINT_MAX)+choose(LONG_MAX);}',
    '#include <climits>\nint main(){unsigned char bytes[CHAR_BIT]={};bytes[CHAR_BIT-1]=42;return bytes[7];}',
)


@pytest.mark.parametrize('source', PROGRAMS)
def test_climits_public_compilation_is_byte_stable(source):
    first = compile_cpp(source, 'climits.cpp')
    assert first == compile_cpp(source, 'climits.cpp')
    obj = emit_owned_object(first[0][1], TARGET)
    assert obj == emit_owned_object(first[0][1], TARGET)


@pytest.mark.integration
@pytest.mark.parametrize('source', PROGRAMS)
def test_climits_public_objects_execute_against_explicit_reference(source, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned climits compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        result = compile_cpp(source, 'climits.cpp')
        assert result == compile_cpp(source, 'climits.cpp')
        obj = emit_owned_object(result[0][1], TARGET)
        assert obj == emit_owned_object(result[0][1], TARGET)
        executable = link_object(obj)
    binary = tmp_path / 'owned'
    binary.write_bytes(executable)
    binary.chmod(0o700)
    expected = Execution(42, b'', b'')
    record_execution(binary, expected)
    unit = tmp_path / 'climits.cpp'
    unit.write_text(source)
    oracle = tmp_path / 'oracle'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', str(unit), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record_execution(oracle, expected)
