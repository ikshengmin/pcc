"""Keyword expansion keeps its merge/error timing when later operands park."""

import subprocess
import sys


PROGRAM = '''import pcc.virtual_thread as vt
events = []

def pause(value):
    events.append('pause' + str(value))
    vt.yield_now()
    return value

def fail():
    events.append('late')
    vt.yield_now()
    raise ValueError('late argument')

def mutate(mapping):
    vt.yield_now()
    mapping['a'] = 99
    return 10

def plain(left, right):
    return left + right

def named(*, a, b):
    return [a, b]

class Box:
    def named(self, *, a, b):
        return [a, b]

class Mapping:
    def keys(self):
        events.append('keys')
        return ['a']

    def __getitem__(self, key):
        events.append(key)
        return 3

def worker():
    values = {'a': 3}
    before = 4
    box = Box()
    out = []
    out.append(named(**values, b=plain(before, pause(10))))
    out.append(box.named(**values, b=plain(before, pause(20))))
    out.append(named(**values, b=mutate(values)))
    out.append(named(**Mapping(), b=plain(before, pause(30))))
    try:
        named(a=1, **values, b=fail())
    except TypeError:
        events.append('duplicate')
    try:
        named(**[('a', 1)], b=fail())
    except TypeError:
        events.append('not a mapping')
    return out, events

def main():
    task = vt.spawn(worker)
    vt.run(1, 128)
    result = vt.result(task)
    print(result[0])
    print(result[1])

main()
'''


def test_keyword_values_spill_without_reordering_mapping_merges(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    reference = PROGRAM.split('def main():', 1)[0].replace('    vt.yield_now()', '    pass')
    reference += '\nresult = worker()\nprint(result[0])\nprint(result[1])\n'
    expected = subprocess.run(
        [sys.executable, '-c', reference], check=True, capture_output=True,
        text=True, timeout=30,
    ).stdout
    source = tmp_path / 'keyword_operands.py'
    output = tmp_path / 'keyword_operands'
    source.write_text(PROGRAM, encoding='utf-8')
    python_program_compiler(
        str(source), str(output), backend='self', libpython_mode='off',
        runtime_archive=str(pcc_runtime_archive),
    )
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
