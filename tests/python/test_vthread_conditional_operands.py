"""Conditional operands preserve selection, owners and side effects across a park."""

import subprocess
import sys


PROGRAM = '''import gc
import pcc.virtual_thread as vt

events = []

class Flag:
    def __init__(self, truth):
        self.truth = truth

    def __bool__(self):
        events.append('bool' + str(self.truth))
        return self.truth

    def __del__(self):
        events.append('drop' + str(self.truth))

def fetch(n):
    events.append(n)
    vt.yield_now()
    return n * 10

def fail():
    events.append('fail')
    vt.yield_now()
    raise ValueError('selected branch')

def plain(a, b):
    return a + b

def worker():
    a = 3
    yes = True
    no = False
    out = []
    out.append(yes and plain(a, fetch(1)))
    out.append(no or plain(a, fetch(2)))
    out.append(no and fail())
    out.append(yes or fail())
    out.append(plain(a, fetch(3)) if yes else fail())
    out.append(fail() if no else plain(a, fetch(4)))
    out.append(7 + (plain(a, fetch(5)) if yes else 0))
    out.append((yes and plain(a, fetch(6))) if yes else 0)
    marker = Flag(False)
    out.append((marker and fail()) is marker)
    del marker
    value = Flag(True) and plain(a, fetch(7))
    out.append(value)
    try:
        out.append(yes and plain(a, fail()))
    except ValueError:
        events.append('caught')
    gc.collect()
    return out, list(events)

def main():
    task = vt.spawn(worker)
    vt.run(1, 256)
    result = vt.result(task)
    print(result[0])
    print(result[1])

main()
'''


def test_conditional_values_keep_branch_semantics_across_parking(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    # A single task has the same expression semantics as this synchronous
    # CPython execution; only its explicit scheduler yields are removed.
    reference = PROGRAM.split('def main():', 1)[0].replace('    vt.yield_now()', '    pass')
    reference += '\nresult = worker()\nprint(result[0])\nprint(result[1])\n'
    expected = subprocess.run(
        [sys.executable, '-c', reference], capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout
    source = tmp_path / 'conditional_operands.py'
    output = tmp_path / 'conditional_operands'
    source.write_text(PROGRAM, encoding='utf-8')
    python_program_compiler(
        str(source), str(output), backend='self', libpython_mode='off',
        runtime_archive=str(pcc_runtime_archive),
    )
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
