"""Unpack arity is checked before targets and preserves iterator exceptions."""

import os
import subprocess
import sys

import pytest


ARITY_PROGRAM = '''import pcc.virtual_thread as vt

events = []


def index(number):
    events.append(number)
    return number


def pause_index(number):
    events.append(number)
    vt.yield_now()
    return number


def plain(source):
    events.clear()
    target = [None, None]
    try:
        target[index(0)], target[index(1)] = source
    except ValueError as error:
        print('ValueError', str(error))
    except Exception as error:
        print('other error', str(error))
    print(target, events)


def worker():
    events.clear()
    target = [0, 0, 0]
    try:
        target[pause_index(0)], (target[pause_index(1)], target[pause_index(2)]) = 7, (8,)
    except ValueError as error:
        print('ValueError', str(error))
    print(target, events)


def main():
    plain([1])
    plain([1, 2, 3])
    plain((1,))
    plain((1, 2, 3))
    task = vt.spawn(worker)
    vt.run(1, 128)
    error = vt.exception(task)
    if error is not None:
        raise error


main()
'''


PROTOCOL_PROGRAM = '''import gc

events = []
selected = ValueError('iterator failed')


class Cursor:
    def __init__(self, size, fail):
        self.size = size
        self.position = 0
        self.fail = fail

    def __iter__(self):
        events.append('iter')
        return self

    def __len__(self):
        raise AssertionError('unpack requested a custom length')

    def __next__(self):
        position = self.position
        self.position += 1
        events.append(position)
        gc.collect()
        if self.fail and position == 1:
            raise selected
        if self.size >= 0 and position >= self.size:
            raise StopIteration('finished')
        return 2**80 + position


def index(number):
    events.append('target:' + str(number))
    return number


def exercise(source):
    events.clear()
    target = [None, None]
    try:
        target[index(0)], target[index(1)] = source
    except ValueError as error:
        print(str(error), error is selected)
    print(target, events)


def main():
    exercise(Cursor(0, False))
    exercise(Cursor(1, False))
    exercise(Cursor(2, False))
    exercise(Cursor(3, False))
    exercise(Cursor(-1, False))
    exercise(Cursor(2, True))
    exercise('ab')
    exercise('a')
    exercise('abc')
    exercise({3})
    exercise({3, 4, 5})


main()
'''

@pytest.mark.parametrize('name,program', (
    ('sequence-and-parking', ARITY_PROGRAM),
    ('iterator-protocol', PROTOCOL_PROGRAM),
), ids=('sequence-and-parking', 'iterator-protocol'))
def test_unpack_checks_boundary_before_target_stores(
    tmp_path, python_program_compiler, pcc_runtime_archive, name, program,
):
    reference = program
    if name == 'sequence-and-parking':
        reference = program.split('def main():', 1)[0].replace('    vt.yield_now()', '    pass')
        reference += '\nplain([1])\nplain([1, 2, 3])\nplain((1,))\nplain((1, 2, 3))\nworker()\n'
    expected = subprocess.run([sys.executable, '-c', reference], check=True,
                              capture_output=True, text=True, timeout=30).stdout
    source = tmp_path / 'unpack.py'
    source.write_text(program + "\nfrom pcc.extern import c_int64, extern\n"
                      "active_backend = extern('pcc_gc_backend', (), c_int64)\n"
                      "print('backend', active_backend())\n", encoding='utf-8')
    binary = tmp_path / 'unpack'
    python_program_compiler(str(source), str(binary), backend='self', libpython_mode='off',
                            runtime_archive=str(pcc_runtime_archive))
    (tmp_path / 'expected.stdout').write_text(expected)
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='/nonexistent',
                     PCC_HOST_PCC='/nonexistent/pcc', PCC_HOST_PYTHON='/nonexistent/python'),
        )
        (tmp_path / f'gc{backend}.stdout').write_text(result.stdout)
        (tmp_path / f'gc{backend}.stderr').write_text(result.stderr)
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == expected + f'backend {backend}\n'
