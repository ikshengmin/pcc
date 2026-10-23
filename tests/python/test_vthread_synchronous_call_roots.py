"""Synchronous call operands remain owned across callbacks and later parks."""

import os
import subprocess
import sys


PROGRAM = '''import gc
import json
import pcc.virtual_thread as vt

events = []
selected = ValueError('later operand')


class Held:
    def __del__(self):
        events.append('released')


def payload(number):
    value = {'items': [number, 2**80 + 7], 'name': 'kept'}
    gc.collect()
    return value


def flag():
    gc.collect()
    return True


def fail():
    gc.collect()
    raise selected


def worker():
    for number in range(3):
        vt.yield_now()
        rendered = json.dumps(payload(number), sort_keys=flag())
        gc.collect()
        vt.yield_now()
        print(rendered)
    try:
        json.dumps([Held()], sort_keys=fail())
    except ValueError as error:
        print(error is selected, str(error))
    gc.collect()
    vt.yield_now()
    print(events)


def main():
    task = vt.spawn(worker)
    vt.run(1, 128)
    error = vt.exception(task)
    if error is not None:
        raise error


main()
'''


def test_synchronous_call_roots_survive_collection_and_later_parking(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    reference = PROGRAM.split('def main():', 1)[0].replace('    vt.yield_now()', '    pass')
    reference += '\nworker()\n'
    expected = subprocess.run([sys.executable, '-c', reference], check=True,
                              capture_output=True, text=True, timeout=30).stdout
    source = tmp_path / 'synchronous_call.py'
    source.write_text(PROGRAM + "\nfrom pcc.extern import c_int64, extern\n"
                      "backend = extern('pcc_gc_backend', (), c_int64)\n"
                      "print('backend', backend())\n", encoding='utf-8')
    binary = tmp_path / 'synchronous_call'
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
