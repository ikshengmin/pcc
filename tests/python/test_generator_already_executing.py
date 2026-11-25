"""A running generator refuses a second resume, as CPython's "generator
already executing".

Word 40 of a generator holds bit 0 (done) and bit 1 (running). Every resume
entry (next, send, throw, close) claims the running bit with one CAS and
clears it when the frame returns, so a resume from inside the generator
itself or from another thread raises ValueError instead of entering the same
frame twice. Before this, a self re-entry recursed until RecursionError and
four threads ran one generator frame at the same time.

Each program is compiled with pcc and its output is compared with the same
program run by CPython.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

SELF = textwrap.dedent("""
    holder = []


    def by_next():
        yield next(holder[0])


    def by_send():
        yield holder[0].send(None)


    def by_close():
        holder[0].close()
        yield 1


    def by_throw():
        yield holder[0].throw(KeyError("k"))


    def attempt(name, factory):
        holder.clear()
        g = factory()
        holder.append(g)
        try:
            next(g)
            print(name, "entered twice")
        except ValueError as exc:
            print(name, "ValueError", exc)
        # The failed resume finished the generator; it stays usable as such.
        try:
            next(g)
            print(name, "resumed after the error")
        except StopIteration:
            print(name, "StopIteration after the error")


    def counting():
        yield 1
        yield 2


    def main():
        attempt("next", by_next)
        attempt("send", by_send)
        attempt("close", by_close)
        attempt("throw", by_throw)
        g = counting()
        print("values", list(g))
        try:
            next(g)
        except StopIteration:
            print("exhausted StopIteration")
        print("close of a finished generator", g.close())


    main()
    """).lstrip()

THREADS = textwrap.dedent("""
    import threading
    import time

    state = {"inside": 0, "release": 0}


    def worker_body():
        total = 0
        while True:
            state["inside"] = 1
            while state["release"] == 0:
                time.sleep(0.001)
            state["release"] = 0
            total += 1
            yield total


    def drive(g, results):
        results.append(next(g))


    def wait_inside():
        while state["inside"] == 0:
            time.sleep(0.001)
        state["inside"] = 0


    def main():
        g = worker_body()
        results = []
        for round_number in range(3):
            thread = threading.Thread(target=drive, args=(g, results))
            thread.start()
            wait_inside()
            for name in ("next", "send", "close"):
                try:
                    if name == "next":
                        next(g)
                    elif name == "send":
                        g.send(None)
                    else:
                        g.close()
                    print(round_number, name, "entered a running generator")
                except ValueError as exc:
                    print(round_number, name, "ValueError", exc)
            state["release"] = 1
            thread.join()
        print("results", results)


    main()
    """).lstrip()


@pytest.mark.parametrize(("name", "program"), [("self", SELF), ("threads", THREADS)],
                         ids=["self", "threads"])
def test_a_running_generator_refuses_a_second_resume(tmp_path, name, program):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / (name + ".py")
    source.write_text(program, encoding="utf-8")
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True,
                               timeout=120)
    assert reference.returncode == 0, reference.stderr
    assert "ValueError generator already executing" in reference.stdout
    executable = tmp_path / (name + ".out")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                            env=dict(os.environ))
    assert (result.returncode, result.stdout) == (0, reference.stdout), result.stderr
