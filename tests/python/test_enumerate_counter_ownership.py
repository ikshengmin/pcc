"""Synthetic enumerate counters follow local and suspended-frame ownership."""

import os
import subprocess
import sys

import pytest


PROGRAM = '''import gc

def scan(values, start):
    result = []
    for index, item in enumerate(values, start):
        gc.collect()
        if item == 'skip':
            continue
        result.append((index, item))
    return result

def repeat(values):
    result = []
    for turn in range(3):
        for index, item in enumerate(values):
            result.append((turn, index, item))
    return result

def suspended(values):
    for index, item in enumerate(values, -3):
        yield index, item
        gc.collect()

def main():
    print(scan('abc', 0))
    print(scan(['a', 'skip', 'b'], -4))
    print(repeat(['a', 'b']))
    print(list(suspended(['a', 'b', 'c'])))
    print([pair for pair in enumerate(['a', 'b'], start=5)])
    print([(index, left, right) for index, (left, right) in enumerate([(1, 2), (3, 4)])])

main()
'''


@pytest.mark.parametrize('scaffold', ('on', 'off'))
def test_enumerate_counters_are_owned_across_iteration_and_resume(
    tmp_path, python_program_compiler, pcc_runtime_archive, scaffold,
):
    source = tmp_path / 'enumerate_owners.py'
    source.write_text(PROGRAM, encoding='utf-8')
    expected = subprocess.check_output([sys.executable, str(source)], text=True, timeout=15)
    binary = tmp_path / 'enumerate_owners'
    python_program_compiler(
        str(source), str(binary), backend='self', libpython_mode='off',
        ir_scaffold_mode=scaffold, runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='/nonexistent',
                     PCC_HOST_PYTHON='/nonexistent/python', PCC_HOST_PCC='/nonexistent/pcc'),
        )
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == expected, (backend, result.stdout)
