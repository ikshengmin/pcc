import subprocess
import sys
import textwrap

from pcc.frontends.python.pipeline import compile_python


def test_class_chained_assignment_initializes_all_aliases(tmp_path):
    src = tmp_path / "class_chained_assignment.py"
    src.write_text(
        textwrap.dedent(
            """
            class C:
                a = b = c = 7

            print(C.a)
            print(C.b)
            print(C.c)
            """
        )
    , encoding="utf-8")
    exe = tmp_path / "class_chained_assignment.out"
    compile_python(str(src), str(exe), libpython_mode="off", ir_scaffold_mode="on")
    out = subprocess.check_output([str(exe)], text=True).splitlines()
    assert out == ["7", "7", "7"]


CHAIN_PROGRAM = '''import gc
import pcc.virtual_thread as vt

events = []
binding = 0
sequence = 0

class Sink:
    def __init__(self):
        self.values = {}

    def __setitem__(self, key, value):
        events.append(binding)
        self.values[key] = value

def payload():
    global sequence
    sequence += 1
    events.append('rhs' + str(sequence))
    return [sequence]

def normal():
    global binding
    events.clear()
    binding = 0
    sink = Sink()
    sink[0] = binding = 7
    sink[1] = (binding := 8)
    first = second = payload()
    pair = a, b = (3, 4)
    return [list(events), sink.values, first is second, pair, a, b]

def index(items, value):
    events.append('index' + str(value))
    vt.yield_now()
    if value == 1:
        events.append(items[0][0])
    return value

def fail_index():
    events.append('failure')
    vt.yield_now()
    raise ValueError('later target')

def worker():
    events.clear()
    items = [None, None]
    items[index(items, 0)] = items[index(items, 1)] = payload()
    shared = items[0] is items[1]
    try:
        items[index(items, 0)] = items[fail_index()] = payload()
    except ValueError:
        events.append('caught')
    gc.collect()
    return [list(events), items, shared]

def main():
    print(normal())
    task = vt.spawn(worker)
    vt.run(1, 128)
    print(vt.result(task))

main()
'''


def test_chain_evaluates_rhs_once_and_stores_targets_in_order(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    reference = CHAIN_PROGRAM.split('def main():', 1)[0].replace('    vt.yield_now()', '    pass')
    reference += '\nprint(normal())\nprint(worker())\n'
    expected = subprocess.run(
        [sys.executable, '-c', reference], check=True, capture_output=True,
        text=True, timeout=30,
    ).stdout
    source = tmp_path / 'chained_targets.py'
    output = tmp_path / 'chained_targets'
    source.write_text(CHAIN_PROGRAM, encoding='utf-8')
    python_program_compiler(
        str(source), str(output), backend='self', libpython_mode='off',
        runtime_archive=str(pcc_runtime_archive),
    )
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
