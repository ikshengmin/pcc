"""Generic name += owns replacement results and preserves parameter edges."""

import os
import subprocess
import sys


PROGRAM = '''import gc
import struct

events = []

class Value:
    def __init__(self, name):
        self.name = name
    def __iadd__(self, other):
        events.append('add:' + self.name)
        if other == 'raise':
            raise ValueError('in-place failure')
        if other == 'same':
            return self
        return Value(other)
    def __del__(self):
        events.append('drop:' + self.name)

class ReturnRight:
    def __iadd__(self, other):
        return other

def conditional(value, change):
    if change:
        value += 'child'
    return value

def conditional_number(value, change):
    if change:
        value += 3
    return value

def exercise():
    original = Value('original')
    child = conditional(original, True)
    unchanged = conditional(original, False)
    assert unchanged is original
    assert child.name == 'child'
    assert original.name == 'original'
    assert conditional_number(7, False) == 7
    assert conditional_number(7, True) == 10
    replacement = Value('old')
    replacement += 'new'
    assert events[-1] == 'drop:old'
    alias = replacement
    replacement += 'same'
    assert replacement is alias
    try:
        replacement += 'raise'
    except ValueError as error:
        events.append(str(error))
    assert replacement is alias and replacement.name == 'new'
    receiver = ReturnRight()
    receiver += child
    assert receiver is child and receiver.name == 'child'
    print(events)
    # Explicit releases make the finalizer order part of the observation.
    del receiver, child, unchanged, original, alias, replacement
    gc.collect()
    print(events)

def grow(count):
    out = bytearray(b'abc')
    out += b'd'
    # The prior mixed bytearray/bytes addition makes later targets dynamic.
    # This is the second-loop ownership shape of runtime stack-map encoding.
    for left, right in [(1, 2)] * count:
        out += struct.pack('<II', left, right)
    return out

def main():
    exercise()
    print(grow(3) == bytearray(b'abcd' + struct.pack('<II', 1, 2) * 3))

main()
'''

MEMORY_PROBE = '''
from pcc.extern import c_int64, extern
live_bytes = extern('pcc_os_heap_in_use_bytes', (), c_int64)
gc_backend = extern('pcc_gc_backend', (), c_int64)

def memory_probe():
    grow(4)
    before = live_bytes()
    result = grow(8000)
    after = live_bytes()
    assert bytes(result) == b'abcd' + struct.pack('<II', 1, 2) * 8000
    if gc_backend() == 0:
        assert after - before < len(result) + 1048576
    print('augassign-owners-ok', gc_backend())

memory_probe()
'''


def test_generic_augassign_owns_results_and_parameter_branches(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    source = tmp_path / 'augassign_owners.py'
    source.write_text(PROGRAM, encoding='utf-8')
    expected = subprocess.check_output([sys.executable, str(source)], text=True, timeout=20)
    source.write_text(PROGRAM + MEMORY_PROBE, encoding='utf-8')
    binary = tmp_path / 'augassign_owners'
    python_program_compiler(
        str(source), str(binary), backend='self', libpython_mode='off',
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=45,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='/nonexistent',
                     PCC_HOST_PYTHON='/nonexistent/python', PCC_HOST_PCC='/nonexistent/pcc'),
        )
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == expected + f'augassign-owners-ok {backend}\n'
