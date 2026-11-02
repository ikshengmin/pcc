"""Owned C++ stdint.h: N4659 [cstdint.syn], [depr.c.headers], C 7.20.

The owned C ABI, not host libc, chooses implementation-defined fast types.
Native/reference programs test portable integer guarantees and Linux LP64
exact-width interfaces; target-only checks are not native Windows/ARM claims.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.preprocessor import preprocess
from pcc.frontends.cpp import CppParser
from pcc.frontends.cpp.constants import KNOWN
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import Execution, TARGET
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_public_driver import compile_cpp
from tests.cpp.test_cpp_public_driver_execution import link_object


TARGETS = (
    ('x86_64-unknown-linux-gnu', 'long', 32, False),
    ('aarch64-unknown-linux-gnu', 'long', 32, True),
    ('x86_64-pc-windows-msvc', 'long long', 16, True),
)


def aliases(wide):
    result = {}
    for family in ('', '_least', '_fast'):
        for bits, signed in ((8, 'signed char'), (16, 'short'), (32, 'int'), (64, wide)):
            result['int' + family + str(bits) + '_t'] = signed
            result['uint' + family + str(bits) + '_t'] = 'unsigned ' + ('char' if bits == 8 else signed)
    for name in ('intptr_t', 'intmax_t'):
        result[name] = wide
        result['u' + name] = 'unsigned ' + wide
    return result


def limits(wide, wchar_bits, wchar_unsigned):
    result = []
    for family in ('', '_LEAST', '_FAST'):
        for bits in (8, 16, 32, 64):
            stem = 'INT' + family + str(bits)
            dtype = wide if bits == 64 else 'int'
            result.extend(((stem + '_MIN', dtype, -(1 << (bits - 1))),
                           (stem + '_MAX', dtype, (1 << (bits - 1)) - 1),
                           ('U' + stem + '_MAX', 'unsigned ' + dtype if bits >= 32 else 'int',
                            (1 << bits) - 1)))
    for stem in ('INTPTR', 'INTMAX', 'PTRDIFF'):
        result.extend(((stem + '_MIN', wide, -(1 << 63)), (stem + '_MAX', wide, (1 << 63) - 1)))
    result.extend((name, 'unsigned ' + wide, (1 << 64) - 1)
                  for name in ('UINTPTR_MAX', 'UINTMAX_MAX', 'SIZE_MAX'))
    result.extend((('SIG_ATOMIC_MIN', 'int', -(1 << 31)), ('SIG_ATOMIC_MAX', 'int', (1 << 31) - 1),
                   ('WINT_MIN', 'int', -(1 << 31)), ('WINT_MAX', 'int', (1 << 31) - 1)))
    wchar_type = 'unsigned int' if wchar_unsigned and wchar_bits == 32 else 'int'
    result.extend((('WCHAR_MIN', wchar_type, 0 if wchar_unsigned else -(1 << (wchar_bits - 1))),
                   ('WCHAR_MAX', wchar_type, (1 << (wchar_bits if wchar_unsigned else wchar_bits - 1)) - 1)))
    return tuple(result)


@pytest.mark.parametrize('target,wide,wchar_bits,wchar_unsigned', TARGETS)
def test_stdint_aliases_are_the_owned_target_c_integer_types(target, wide, wchar_bits, wchar_unsigned):
    parser = CppParser(target_triple=target)
    parser.parse(preprocess_cpp('#include <stdint.h>\n#include <stdint.h>\n', target_triple=target))
    expected = aliases(wide)
    for name, dtype in expected.items():
        assert parser.scope.names[name].kind == 'type'
        assert parser.scope.names[name].dtype == dtype
    assert 'FILE' not in parser.scope.names and 'va_list' not in parser.scope.names


@pytest.mark.parametrize('target,wide,wchar_bits,wchar_unsigned', TARGETS)
def test_stdint_limits_are_exact_target_constant_expressions(target, wide, wchar_bits, wchar_unsigned):
    rows = limits(wide, wchar_bits, wchar_unsigned)
    source = '#include <stdint.h>\n' + ''.join(
        'const ' + dtype + ' value_' + name + '=' + name + ';' for name, dtype, _ in rows)
    parser = CppParser(target_triple=target)
    parser.parse(preprocess_cpp(source, target_triple=target))
    for name, dtype, value in rows:
        fact = parser.scope.names['value_' + name].constant_fact
        assert (fact.status, fact.dtype, fact.value) == (KNOWN, dtype, value)


@pytest.mark.parametrize('target,wide,wchar_bits,wchar_unsigned', TARGETS)
def test_limit_and_constant_macros_keep_their_promoted_integer_types(target, wide, wchar_bits, wchar_unsigned):
    codes = {'int': 'i', 'unsigned int': 'j', 'long': 'l', 'unsigned long': 'm',
             'long long': 'x', 'unsigned long long': 'y'}
    rows = [(name, dtype) for name, dtype, _ in limits(wide, wchar_bits, wchar_unsigned)]
    for bits in (8, 16, 32, 64):
        rows.append(('INT' + str(bits) + '_C(42)', wide if bits == 64 else 'int'))
        rows.append(('UINT' + str(bits) + '_C(42)', 'unsigned ' + wide if bits == 64 else
                     'unsigned int' if bits == 32 else 'int'))
    rows.extend((('INTMAX_C(42)', wide), ('UINTMAX_C(42)', 'unsigned ' + wide)))
    source = '#include <stdint.h>\n' + ''.join('int pick(' + dtype + ');' for dtype in codes)
    source += ''.join('int probe_' + str(index) + '(){return pick(' + expression + ');}'
                      for index, (expression, _) in enumerate(rows))
    parser = CppParser(target_triple=target)
    tree = parser.parse(preprocess_cpp(source, target_triple=target))
    calls = []
    class Calls(c_ast.NodeVisitor):
        def visit_FuncCall(self, node):
            calls.append(node.name.name)
    Calls().visit(tree)
    assert calls == ['_Z4pick' + codes[dtype] for _, dtype in rows]


@pytest.mark.parametrize('target,wide,wchar_bits,wchar_unsigned', TARGETS)
def test_all_integer_limits_remain_usable_in_preprocessor_conditions(target, wide, wchar_bits, wchar_unsigned):
    source = '#include <stdint.h>\n'
    for name, _dtype, value in limits(wide, wchar_bits, wchar_unsigned):
        expected = str(value) + ('ULL' if value > (1 << 63) - 1 else 'LL')
        if value == -(1 << 63):
            expected = '(-9223372036854775807LL - 1LL)'
        source += '#if ' + name + ' != ' + expected + '\n#error incorrect ' + name + '\n#endif\n'
    source += 'int main(){return 42;}'
    assert 'return 42' in preprocess_cpp(source, target_triple=target)


@pytest.mark.parametrize('target,wide,wchar_bits,wchar_unsigned', TARGETS)
def test_cpp_and_owned_c_headers_have_identical_integer_macro_contract(target, wide, wchar_bits, wchar_unsigned):
    names = [name for name, _, _ in limits(wide, wchar_bits, wchar_unsigned)]
    names += [name + '_C(42)' for name in ('INT8', 'UINT8', 'INT16', 'UINT16', 'INT32', 'UINT32',
                                         'INT64', 'UINT64', 'INTMAX', 'UINTMAX')]
    tail = '\n'.join('PCC_INTEGER_' + str(index) + ' ' + name for index, name in enumerate(names))
    source = '#include <stdint.h>\n' + tail + '\n'
    include = Path(__file__).resolve().parents[2] / 'utils/fake_libc_include'
    c_text = preprocess(source, include_dirs=[str(include)], cpp_args=['-nostdinc'], target_triple=target)
    cpp_text = preprocess_cpp(source, target_triple=target)
    def expansions(text):
        return [' '.join(line.split()) for line in text.splitlines() if line.startswith('PCC_INTEGER_')]
    assert expansions(c_text) == expansions(cpp_text)
    assert len(expansions(cpp_text)) == len(names)


PROGRAMS = (
    '#include <stdint.h>\nint main(){int8_t a=INT8_MIN;uint16_t b=UINT16_MAX;'
    'if(a!=-128||b!=65535)return 1;return 42;}',
    '#include <stdint.h>\nint main(){int64_t a=INT64_MIN;uint64_t b=UINT64_MAX;'
    'if(a!=-9223372036854775807LL-1LL||b!=18446744073709551615ULL)return 1;return 42;}',
    '#include <stdint.h>\nint choose(int){return 17;}int choose(unsigned long){return 25;}'
    'int main(){return choose(UINT16_C(65535))+choose(UINT64_C(42));}',
    '#include <stdint.h>\n#include <cstring>\n#include <cstdio>\n#include <cstdlib>\n'
    'extern "C" int64_t bump(int64_t n){return n+1;}'
    'int main(){return int(bump(INT64_C(41)));}',
)


@pytest.mark.parametrize('source', PROGRAMS)
def test_stdint_originals_have_byte_stable_public_artifacts(source):
    first = compile_cpp(source, 'stdint.cpp')
    assert first == compile_cpp(source, 'stdint.cpp')
    assert emit_owned_object(first[0][1], TARGET) == emit_owned_object(first[0][1], TARGET)


@pytest.mark.integration
@pytest.mark.parametrize('source', PROGRAMS)
def test_stdint_originals_execute_against_explicit_cpp17_reference(source, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned stdint compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        first = compile_cpp(source, 'stdint.cpp')
        assert first == compile_cpp(source, 'stdint.cpp')
        obj = emit_owned_object(first[0][1], TARGET)
        assert obj == emit_owned_object(first[0][1], TARGET)
        executable = link_object(obj)
    binary = tmp_path / 'owned'
    binary.write_bytes(executable)
    binary.chmod(0o700)
    expected = Execution(42, b'', b'')
    record_execution(binary, expected)
    unit = tmp_path / 'stdint.cpp'
    unit.write_text(source)
    oracle = tmp_path / 'oracle'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', str(unit), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record_execution(oracle, expected)
