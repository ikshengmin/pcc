"""A virtual-thread call's new result can feed any operand consumer.

Tuple unpacking, list and dict methods, subscript stores and calls take an
object operand through a root registered before the producer runs; the
producer stores its new reference there before any release or error check
that follows can park or collect. The channel, oneshot, receive, select,
result, exception and sender-clone calls returned their result after that
cleanup instead, so `a, b = vt.recv(rx)` and `tx, rx = vt.mpsc(4)` failed to
compile ("slot-call Call lacks an immediate owned-result handoff"). Join and
spawn already published into the waiting root.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import pcc.virtual_thread as vt


def pair(seed):
    return (seed, seed * 10)


def failing(seed):
    raise ValueError("bad " + str(seed))


def consumer(rx):
    total = 0
    status, item = vt.recv(rx)
    while status == vt.RECV_VALUE:
        left, right = item
        total += left * right
        status, item = vt.recv(rx)
    return (status, total)


def producer(tx):
    vt.send(tx, (2, 3))
    vt.send(tx, (4, 5))
    vt.close_sender(tx)
    return 2


def feeder(sender, value):
    vt.send(sender, value)
    return value


def chooser(left_rx, right_rx):
    winner, status, value = vt.select2(left_rx, right_rx)
    return (winner, status, value)


def joiner(target):
    first, second = vt.join(target)
    return first + second


def describe(value):
    return "result:" + str(value)


def main():
    tx, rx = vt.mpsc(4)
    one_tx, one_rx = vt.oneshot()
    other_tx, other_rx = vt.oneshot()
    held = []
    held.append(vt.sender_clone(tx))
    work = vt.spawn(pair, 3)
    broken = vt.spawn(failing, 4)
    threads = [
        vt.spawn(consumer, rx),
        vt.spawn(producer, tx),
        vt.spawn(feeder, other_tx, 9),
        vt.spawn(chooser, one_rx, other_rx),
        vt.spawn(joiner, work),
        work,
    ]
    vt.close_sender(held[0])
    vt.run(1, 100000)
    for thread in threads:
        assert vt.exception(thread) is None
    status, total = vt.result(threads[0])
    winner, chosen, value = vt.result(threads[3])
    first, second = vt.result(work)
    errors = {}
    errors["broken"] = vt.exception(broken)
    print(status == vt.RECV_SENDER_CLOSED, total, winner == vt.SELECT_RIGHT,
          chosen == vt.RECV_VALUE, value, vt.result(threads[4]), first, second,
          describe(vt.result(threads[1])), type(errors["broken"]).__name__,
          str(errors["broken"]), len(held))


main()
'''

EXPECTED = "True 26 True True 9 33 3 30 result:2 ValueError bad 4 1\n"


@pytest.mark.integration
def test_owned_virtual_thread_results_feed_operand_consumers(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "owned_results.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "owned_results"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), backend
