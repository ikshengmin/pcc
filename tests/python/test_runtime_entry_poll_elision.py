"""Runtime library functions poll at their loops, not at every entry.

Every compiled function began with a thread safepoint poll: two flag
loads, an or and a branch, before any work. In the runtime library that
was a fixed cost on every one of the thousands of runtime calls a request
makes (an allocate/free lifecycle alone is about forty): the GC0 HTTP/1
parser driver ran 7.7% faster without it, GC3 4.3%. A runtime port's
function now polls only at its loop back-edges and its explicit
pcc_thread_safepoint calls (allocation polls explicitly), so a stop still
reaches every runtime thread within a loop iteration or one bounded call
chain; blocking runtime calls park explicitly. Compiled program code keeps
its entry poll (an application gate). The comments that root objects
"before the entry poll" protect against that poll; none depends on it.

The native program stops the world from one thread (gc.collect) while
others allocate through runtime-heavy loops, and compares with CPython on
all five collectors.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

RUNTIME_PORT = '''__pcc_runtime_port__ = True

from pcc.extern import c_abi_export
from pcc.unsafe import load_i8


@c_abi_export("probe_straight")
def probe_straight(data, index: int) -> int:
    return (load_i8(data, index) & 255) + 1


@c_abi_export("probe_loop")
def probe_loop(data, count: int) -> int:
    total: int = 0
    index: int = 0
    while index < count:
        total = total + (load_i8(data, index) & 255)
        index = index + 1
    return total
'''

PROGRAM = '''import gc
import threading
import time


def straight(x):
    return x + 1


def churn(results, index):
    total = 0
    for k in range(1500):
        row = {"k": k, "v": [k, str(k), (k, k + 1)]}
        text = ",".join(str(v) for v in row["v"][:2])
        total += len(text) + len(row)
    results[index] = total


def collector(stop, counts):
    # A bounded number of stops while the workers run: a collection in a
    # tight loop starves them on every build (each one stops the world).
    while not stop.is_set() and counts[0] < 40:
        gc.collect()
        counts[0] += 1
        time.sleep(0.002)


def main():
    results = [0, 0, 0]
    stop = threading.Event()
    counts = [0]
    stopper = threading.Thread(target=collector, args=(stop, counts))
    stopper.start()
    workers = [threading.Thread(target=churn, args=(results, i)) for i in range(3)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    stop.set()
    stopper.join()
    print(results, straight(41), counts[0] > 0)


main()
'''


def _function(text: str, symbol: str) -> str:
    body = re.search(r"^define[^\n]*@" + re.escape(symbol) + r"\(.*?^}", text, re.M | re.S)
    assert body is not None, symbol
    return body.group(0)


def test_runtime_port_functions_poll_only_at_loops(tmp_path: Path):
    from pcc.frontends.python import pipeline

    source = tmp_path / "poll_probe.py"
    source.write_text(RUNTIME_PORT, encoding="utf-8")
    output = tmp_path / "poll_probe.ll"
    pipeline.compile_python(
        str(source), str(output), emit_llvm_only=True, python_library=True, backend="self",
    )
    text = output.read_text()
    straight = _function(text, "probe_straight")
    assert "@pcc_thread_stop_requested" not in straight
    assert "@pcc_thread_safepoint(" not in straight
    loop = _function(text, "probe_loop")
    # The back-edge poll remains: one flag load inside the loop latch.
    assert loop.count("load atomic i32, ptr @pcc_thread_stop_requested") == 1
    assert "@pcc_thread_safepoint(" in loop


def test_application_functions_keep_their_entry_poll():
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    typed = infer_module(parse_and_lift(PROGRAM, "entry_poll.py", "entry_poll"))
    codegen = L1CodeGen(typed, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    text = str(codegen.generate(typed))
    straight = _function(text, "user_entry_poll_straight")
    assert "load atomic i32, ptr @pcc_thread_stop_requested" in straight


def test_stops_reach_runtime_heavy_threads_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "entry_poll.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "entry_poll"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=180)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
