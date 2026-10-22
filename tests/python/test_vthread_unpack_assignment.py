"""Unpacked values survive parking while assignment targets run in order."""

import subprocess
import sys


PROGRAM = '''import gc
import pcc.virtual_thread as vt

events = []

class Value:
    def __init__(self, label):
        self.label = label

    def __del__(self):
        events.append('drop' + self.label)

class Sink:
    def __setitem__(self, key, value):
        events.append('store' + value.label)

def index(items, number):
    events.append(list(items))
    vt.yield_now()
    return number

def destination(number):
    events.append('target' + str(number))
    vt.yield_now()
    return number

def worker():
    items = [0, 0]
    values = [11, 22]
    items[index(items, 0)], items[index(items, 1)] = values
    sink = Sink()
    sink[destination(0)], sink[destination(1)] = Value('first'), Value('second')
    gc.collect()
    return items, events

def main():
    task = vt.spawn(worker)
    vt.run(1, 128)
    print(vt.result(task))

main()
'''


def test_unpack_preserves_values_and_target_order_across_parking(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    reference = PROGRAM.split('def main():', 1)[0].replace('    vt.yield_now()', '    pass')
    reference += '\nprint(worker())\n'
    expected = subprocess.run(
        [sys.executable, '-c', reference], check=True, capture_output=True,
        text=True, timeout=30,
    ).stdout
    source = tmp_path / 'unpack_targets.py'
    output = tmp_path / 'unpack_targets'
    source.write_text(PROGRAM, encoding='utf-8')
    python_program_compiler(
        str(source), str(output), backend='self', libpython_mode='off',
        runtime_archive=str(pcc_runtime_archive),
    )
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
