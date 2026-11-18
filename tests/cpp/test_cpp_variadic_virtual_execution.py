"""Same-source Linux SysV variadic virtual execution, host-Python PCC only.

N4659 [expr.call], [class.virtual], [cstdarg.syn]; AMD64 psABI AL bound.
Recovered fixtures: three from recorded successful creation commands; abi_sites
from a complete prior source read. No unavailable historical hash is claimed.
Each compiler creates its own objects and vtables. AL object inspection is
STATIC evidence, not an external assembly raw-AL execution or native pcc1.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

import pytest
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_reference_cpp, run_executable,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'variadic_virtual_sysv'
CASES = ('zero-int-dispatch', 'floating-promotion', 'mixed-overflow', 'abi_sites',
         'protected_format_hook', 'qualified_order')
TARGET = 'x86_64-unknown-linux-gnu'
EXPECTED = Execution(42, b'\x2a\x00\x00\x00', b'')
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(sys.platform != 'linux' or platform.machine() not in ('x86_64', 'AMD64'),
                                 reason='host Linux x86_64 SysV execution only')]

# A fresh Python process owns each cold compile. Spawning is forbidden INSIDE
# it; the parent independently enforces the wall timeout. No external fallback.
COLD = r'''
import resource,sys
from pathlib import Path
from unittest.mock import patch
resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import compile_host_cpp,write_host_executable
source=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir()
def forbidden(*args,**kwargs):
    raise AssertionError('owned C++ compilation invoked an external process')
with patch('subprocess.Popen',forbidden), patch('os.system',forbidden):
    text=preprocess_cpp(source.read_bytes().decode('utf-8'),source_name=source.name,
                        target_triple='x86_64-unknown-linux-gnu')
    artifact=compile_host_cpp(((source.name,text),))
for i,(ir,obj) in enumerate(zip(artifact.ir,artifact.objects)):
    (out/(str(i)+'.ll')).write_text(ir)
    (out/(str(i)+'.o')).write_bytes(obj)
write_host_executable(artifact,out/'program')
'''


def record_execution(path, value):
    path.write_text(json.dumps({'returncode': value.returncode,
                               'stdout_hex': value.stdout.hex(),
                               'stderr_hex': value.stderr.hex()}, indent=2))


def run_checked(command, log, timeout):
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    log.with_suffix('.stdout').write_bytes(result.stdout)
    log.with_suffix('.stderr').write_bytes(result.stderr)
    assert result.returncode == 0, (command, result.returncode, result.stdout, result.stderr)
    return result


@pytest.mark.parametrize('name', CASES)
def test_variadic_virtual_originals_cold_twice_and_cpp17(name, tmp_path):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'real C++17 G++ reference compiler is required'
    source = FIXTURES / (name + '.cpp')
    original = source.read_bytes()
    (tmp_path / 'source.sha256').write_text(hashlib.sha256(original).hexdigest())
    errors, owned, references = [], [], []
    for index in range(2):
        directory = tmp_path / ('cold-' + str(index))
        try:
            run_checked([sys.executable, '-c', COLD, str(source.resolve()), str(directory)],
                        tmp_path / ('cold-' + str(index)), 90)
            actual = run_executable(directory / 'program', timeout=10)
            record_execution(directory / 'execution.json', actual)
            owned.append(actual)
        except Exception as error:
            errors.append(error)
            (tmp_path / ('cold-error-' + str(index) + '.txt')).write_text(repr(error))
        # Preserve both independent reference outcomes even if an owned arm fails.
        try:
            reference = compile_reference_cpp(((source.name, original.decode('utf-8')),),
                        tmp_path / ('reference-' + str(index)), compiler=compiler, timeout=30)
            observed = run_executable(reference, timeout=10)
            record_execution(tmp_path / ('reference-execution-' + str(index) + '.json'), observed)
            references.append(observed)
        except Exception as error:
            errors.append(error)
            (tmp_path / ('reference-error-' + str(index) + '.txt')).write_text(repr(error))
    assert source.read_bytes() == original
    for value in owned + references:
        assert_exact(value, EXPECTED)
    if errors:
        raise errors[0]
    assert len(owned) == len(references) == 2
    for filename in ('0.ll', '0.o', 'program'):
        assert (tmp_path / 'cold-0' / filename).read_bytes() == (tmp_path / 'cold-1' / filename).read_bytes(), filename


def indirect_al_sites(disassembly):
    """Fail-closed, bounded straight-line proof at four specific callsites.

    Only plain non-accumulator setup moves may separate the last AL definition
    from its indirect call. Unknown instructions/branches cannot validate stale
    AL. The ABI permits an upper bound, not necessarily the minimal count.
    """
    expected = {'_Z4zeroP1V': 0, '_Z8integersP1V': 0, '_Z4pairP1V': 2, '_Z4manyP1V': 8}
    bodies, current = {}, None
    for line in disassembly.splitlines():
        label = re.match(r'^\s*[0-9a-f]+ <([^>]+)>:', line)
        if label:
            current = label.group(1)
            bodies.setdefault(current, [])
            continue
        instruction = re.match(r'^\s*[0-9a-f]+:\s+(?:[0-9a-f]{2}\s+)+\s*(\S.*)$', line)
        if instruction and current is not None:
            text = instruction.group(1).strip()
            if text.startswith('rex.WB '):
                text = text[len('rex.WB '):]
            bodies[current].append(text)
    result = {}
    for name, lower in expected.items():
        assert name in bodies, ('missing ABI observer function', name)
        body = bodies[name]
        calls = [i for i, text in enumerate(body) if re.match(r'call\s+(?:r\w+|QWORD PTR\s*\[)', text)]
        assert len(calls) == 1, (name, calls)
        value = None
        for text in reversed(body[max(0, calls[0] - 8):calls[0]]):
            if re.fullmatch(r'xor\s+(?:eax,eax|rax,rax)', text):
                value = 0
                break
            match = re.fullmatch(r'mov\s+(?:al|eax|rax),(0x[0-9a-f]+|[0-9]+)', text)
            if match:
                value = int(match.group(1), 0) & 255
                break
            move = re.fullmatch(r'mov\s+([^,]+),(.+)', text)
            assert move and move.group(1).strip() not in ('al', 'ah', 'ax', 'eax', 'rax'), (name, 'unproven AL tail', text)
        assert value is not None, (name, 'missing AL definition')
        assert lower <= value <= 8, (name, value, lower)
        result[name] = value
    return result


def test_variadic_virtual_cli_object_indirect_al_bounds(tmp_path):
    objdump = shutil.which('objdump')
    assert objdump, 'independent GNU objdump required for object ABI inspection'
    source = FIXTURES / 'abi_sites.cpp'
    original = source.read_bytes()
    output = tmp_path / 'abi_sites.o'
    command = [sys.executable, '-m', 'pcc', '--backend', 'self', '--no-cache',
               '--target', TARGET, '--cpp-arg=-std=c++17', '-O0', str(source.resolve()),
               '--emit-obj', str(output)]
    run_checked(command, tmp_path / 'cli', 90)
    assert output.is_file()
    inspection = run_checked([objdump, '-dr', '-Mintel', str(output)], tmp_path / 'objdump', 10)
    observed = indirect_al_sites(inspection.stdout.decode('utf-8'))
    assert len(observed) == 4
    (tmp_path / 'static-al-bounds.json').write_text(json.dumps(observed, indent=2))
    assert source.read_bytes() == original
