"""Shared reference slots stay valid while other carriers rebind them.

Instance fields, module globals and class attributes are reference slots that
several virtual threads can read while others rebind them. A reader loaded
the slot and then retained the value without the graph lock, while a writer
released the value it displaced: the value could be freed between the load
and the retain (`[BAD_INCREF]`, crashes, readers seeing a reused object).
GC0 heap stores also rewrote the slot without the lock, so two writers
released the same old value twice.

Writers now swap a shared slot inside a graph transaction and release the old
value after it; readers load and retain inside one. Closure cells (a list
item) already did. Every shape runs on four carriers and five collectors.

On GC0 a field read takes no transaction when the field holds NULL, a tagged
int or a static singleton: no writer can free those. The mixed shape rebinds
a field between such values and lists while readers check every value.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import sys
import pcc.virtual_thread as vt


class Box:
    shared_cls = [0, 0]

    def __init__(self):
        self.value = [0, 0]


box = Box()
glob = [0, 0]


def make_cell():
    cell = [0, 0]

    def write(seed, step):
        nonlocal cell
        cell = [seed, step]

    def read():
        return cell

    return write, read


cell_write, cell_read = make_cell()
SHAPE = sys.argv[1]


def store(seed, step):
    global glob
    if SHAPE == "field":
        box.value = [seed, step]
    elif SHAPE == "mixed":
        kind = step % 5
        if kind == 0:
            box.value = step
        elif kind == 1:
            box.value = None
        elif kind == 2:
            box.value = True
        elif kind == 3:
            box.value = False
        else:
            box.value = [seed, step]
    elif SHAPE == "global":
        glob = [seed, step]
    elif SHAPE == "cell":
        cell_write(seed, step)
    else:
        Box.shared_cls = [seed, step]


def load():
    if SHAPE == "field" or SHAPE == "mixed":
        return box.value
    if SHAPE == "global":
        return glob
    if SHAPE == "cell":
        return cell_read()
    return Box.shared_cls


def writer(seed):
    step = 0
    while step < 20000:
        store(seed, step)
        if step % 500 == 0:
            vt.yield_now()
        step += 1
    return seed


def valid(value):
    if value is None or value is True or value is False:
        return SHAPE == "mixed"
    if isinstance(value, list):
        return len(value) == 2
    return SHAPE == "mixed" and isinstance(value, int) and 0 <= value < 20000


def reader(seed):
    bad = 0
    step = 0
    while step < 20000:
        if not valid(load()):
            bad += 1
        if step % 500 == 0:
            vt.yield_now()
        step += 1
    return bad


def main():
    threads = [vt.spawn(writer, seed) for seed in range(3)]
    threads += [vt.spawn(reader, seed) for seed in range(3)]
    vt.run(4, 100000000)
    print(SHAPE, [vt.result(thread) for thread in threads], len(load()))


main()
'''


@pytest.fixture(scope="module")
def shared_slot_program(tmp_path_factory, threaded_pcc_runtime_archive):
    directory = tmp_path_factory.mktemp("shared_slots")
    source = directory / "shared_slots.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = directory / "shared_slots"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    return executable


@pytest.mark.integration
@pytest.mark.parametrize("shape", ("field", "mixed", "cell"))
def test_shared_slot_rebound_while_read_on_four_carriers(shared_slot_program, shape):
    expected = f"{shape} [0, 1, 2, 0, 0, 0] 2\n"
    for backend in range(5):
        for attempt in range(3):
            ran = subprocess.run([str(shared_slot_program), shape], capture_output=True,
                                 text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent",
                                          PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected, ""), (
                shape, backend, attempt)
