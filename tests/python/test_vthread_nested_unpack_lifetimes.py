"""Nested parking assignments consume private containers at each unpack boundary."""

import os
import subprocess
import sys

import pytest


VALUE_PROGRAM = '''import gc
import pcc.virtual_thread as vt

events = []


class Value:
    def __init__(self, label):
        self.label = label

    def __del__(self):
        events.append('drop:' + self.label)


class Sink:
    def __setitem__(self, key, value):
        if isinstance(value, list):
            events.append('store-list:' + str(key))
        else:
            events.append('store:' + value.label)


def position(items, number):
    events.append((number, list(items)))
    vt.yield_now()
    gc.collect()
    return number


def destination(number):
    events.append('target:' + str(number))
    vt.yield_now()
    gc.collect()
    return number


def worker():
    big = 2**80 + 11
    negative = -(2**90 + 22)
    items = [0, 0, 0]
    (items[position(items, 0)], *items[position(items, 1)]), items[position(items, 2)] = ((big, negative, 33), 44)
    print(items, events)
    events.clear()
    items = [0, 0, 0, 0]
    [items[position(items, 0)], [items[position(items, 1)], *items[position(items, 2)]]], items[position(items, 3)] = [big, [negative, 4, 5]], 99
    print(items, events)
    events.clear()
    sink = Sink()
    (sink[destination(0)], sink[destination(1)]), sink[destination(2)] = ((Value('first'), Value('second')), Value('third'))
    gc.collect()
    print(events)


def main():
    task = vt.spawn(worker)
    vt.run(1, 128)
    error = vt.exception(task)
    if error is not None:
        raise error


main()
'''


LIFETIME_PROGRAM = '''import gc
import pcc.virtual_thread as vt

events = []


class Value:
    def __init__(self, label):
        self.label = label

    def __del__(self):
        events.append('drop:' + self.label)


class Sink:
    def __setitem__(self, key, value):
        events.append('store:' + value.label)


def destination(number):
    events.append('target:' + str(number))
    vt.yield_now()
    gc.collect()
    return number


def failure():
    events.append('failure')
    vt.yield_now()
    raise ValueError('later target')


def worker():
    sink = Sink()
    # Only the outer final target parks; the inner container is still consumed.
    (sink[0], sink[1]), sink[destination(2)] = ((Value('first'), Value('second')), Value('third'))
    gc.collect()
    print(events)
    events.clear()
    # A user binding must keep its original values alive after temporary owners go.
    source = ((Value('held-first'), Value('held-second')), Value('held-third'))
    (sink[destination(0)], sink[destination(1)]), sink[destination(2)] = source
    assert len(events) == 6
    print(source[0][0].label, source[0][1].label, source[1].label, events)
    events.clear()
    source = None
    gc.collect()
    print(sorted(events))
    events.clear()
    try:
        (sink[destination(0)], sink[failure()]), sink[destination(2)] = ((Value('early'), Value('error')), Value('outer'))
    except ValueError as error:
        events.append(str(error))
    gc.collect()
    print(events)


def main():
    task = vt.spawn(worker)
    vt.run(1, 128)
    error = vt.exception(task)
    if error is not None:
        raise error


main()
'''


@pytest.mark.parametrize('program', (VALUE_PROGRAM, LIFETIME_PROGRAM),
                         ids=('nested-starred-values', 'aliases-and-exceptions'))
def test_nested_unpack_preserves_lifetimes_across_parking(
    tmp_path, python_program_compiler, pcc_runtime_archive, program,
):
    reference = program.split('def main():', 1)[0].replace('    vt.yield_now()', '    pass')
    reference += '\nworker()\n'
    expected = subprocess.run([sys.executable, '-c', reference], check=True,
                              capture_output=True, text=True, timeout=30).stdout
    source = tmp_path / 'nested_unpack.py'
    source.write_text(program + "\nfrom pcc.extern import c_int64, extern\n"
                      "active_backend = extern('pcc_gc_backend', (), c_int64)\n"
                      "print('backend', active_backend())\n", encoding='utf-8')
    binary = tmp_path / 'nested_unpack'
    python_program_compiler(str(source), str(binary), backend='self', libpython_mode='off',
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='/nonexistent',
                     PCC_HOST_PCC='/nonexistent/pcc', PCC_HOST_PYTHON='/nonexistent/python'),
        )
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == expected + f'backend {backend}\n'
