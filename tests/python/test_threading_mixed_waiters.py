"""Released resources remain available to native and virtual waiters.

Python threading's Lock/RLock/Semaphore contracts do not grant ownership merely
because a waiter is notified. A queued task cannot reserve a resource while a
native waiter blocks the only carrier that could run that task.
"""

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


pytestmark = pytest.mark.xdist_group(name="threading_mixed_waiters")
KINDS = ("Lock", "RLock", "BoundedSemaphore")
PROGRAM = '''import threading
import sys
import pcc.virtual_thread as vt
from pcc.extern import extern, c_int64
pause = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)
pins = extern("py_virtual_thread_pinned_count", (), c_int64)
resource = threading.__RESOURCE__()
gate = threading.Event()
entered = threading.Event()
held = threading.Event()
allow_release = threading.Event()
stopper_entered = threading.Event()
stopper_release = threading.Event()
active = 0
carriers = int(sys.argv[1])

def waiter():
    global active
    vt.yield_now()
    resource.acquire()
    active += 1
    assert active == 1
    vt.yield_now()
    active -= 1
    resource.release()
    return 1

def native_acquire():
    global active
    entered.set()
    assert resource.acquire(blocking=True)
    active += 1
    assert active == 1
    held.set()
    if carriers == 2:
        assert allow_release.wait(timeout=5)
    active -= 1
    resource.release()
    return 2

def blocker():
    gate.wait()
    return vt.call(native_acquire)

def stopper():
    stopper_entered.set()
    assert stopper_release.wait(timeout=5)
    return 4

def wait_parked(task):
    for _ in range(2000):
        if vt.state(task) == 3:
            return
        pause(1000000)
    raise RuntimeError("virtual waiter did not park")

def main():
    resource.acquire()
    assert vt.carrier_pool_start(carriers) == carriers
    stopped = None
    if carriers == 2:
        stopped = vt.spawn(stopper)
        assert stopper_entered.wait(timeout=2)
    first = vt.spawn(waiter)
    wait_parked(first)
    second = vt.spawn(blocker)
    wait_parked(second)
    gate.set()
    assert entered.wait(timeout=2)
    resource.release()
    if carriers == 2:
        # Both carriers are pinned until the native waiter obtains the free
        # resource. Then release the spare so the old virtual waiter must
        # retry, find it held, and park a second time before it may enter.
        assert held.wait(timeout=2)
        stopper_release.set()
        wait_parked(first)
        allow_release.set()
    finished = False
    for _ in range(2000):
        if vt.outcome(first) != 0 and vt.outcome(second) != 0:
            assert vt.result(first) + vt.result(second) == 3
            finished = True
            break
        pause(1000000)
    assert finished
    assert active == 0
    assert resource.acquire(blocking=False)
    resource.release()
    if carriers == 2:
        assert vt.result(stopped) == 4
    assert vt.carrier_pool_stop() == carriers
    assert pins() == 0
    print("MIXED_WAITERS_OK")
main()
'''


RETRY_PROGRAM = '''import threading
import sys
import pcc.virtual_thread as vt
from pcc.extern import c_obj, c_int64, extern
unpark = extern("py_virtual_thread_unpark", (c_obj,), c_int64)
resource = threading.__RESOURCE__()
entered = []
def waiter(value):
    vt.yield_now()
    resource.acquire()
    entered.append(value)
    resource.release()
    return value
def main():
    mode = sys.argv[1]
    resource.acquire()
    first = vt.spawn(waiter, 1)
    second = vt.spawn(waiter, 2)
    vt.run_until_idle(32)
    assert vt.state(first) == 3 and vt.state(second) == 3
    if mode == "spurious":
        for _ in range(3):
            assert unpark(first) == 0
            vt.run_until_idle(32)
            assert vt.state(first) == 3
            assert entered == []
        resource.release()
    else:
        if mode == "notified":
            resource.release()
        assert vt.cancel(first)
        if mode == "queued":
            resource.release()
    vt.run_until_idle(64)
    assert vt.outcome(second) == vt.OUTCOME_RETURNED
    assert vt.result(second) == 2
    if mode == "spurious":
        assert vt.result(first) == 1
        assert sorted(entered) == [1, 2]
    else:
        assert vt.outcome(first) == vt.OUTCOME_CANCELLED
        assert entered == [2]
    assert resource.acquire(blocking=False)
    resource.release()
    print("WAITER_RETRY_CLEANUP_OK")
main()
'''


@pytest.mark.parametrize("kind", KINDS)
def test_parked_acquire_reloads_receiver_and_retries(tmp_path, kind):
    source = tmp_path / "mixed_waiter.py"
    source.write_text(PROGRAM.replace("__RESOURCE__", kind))
    output = tmp_path / "mixed_waiter.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self")
    body = re.search(r"(?ms)^define[^\n]*@[^\n]*waiter__gen_resume\([^\n]*\{\n(.*?)^}", output.read_text())
    assert body is not None
    helper = {"Lock": "lock", "RLock": "rlock", "BoundedSemaphore": "semaphore"}[kind]
    calls = re.findall(r"\bcall\b[^\n]*@py_threading_" + helper + r"_acquire_vthread\(", body[1])
    assert len(calls) == 2
    assert ".repark" in body[1]


@pytest.fixture(scope="module")
def mixed_binaries(tmp_path_factory):
    root = tmp_path_factory.mktemp("mixed-threading-waiters")
    binaries = {}
    for kind in KINDS:
        source = root / (kind + ".py")
        source.write_text(PROGRAM.replace("__RESOURCE__", kind))
        binary = source.with_suffix("")
        build = subprocess.run([sys.executable, "-m", "pcc", "--backend", "self",
                                str(source), "-o", str(binary)], capture_output=True,
                               text=True, timeout=120, env=dict(os.environ, PCC_NO_AUTO_PCC1="1"))
        assert build.returncode == 0, build.stdout + build.stderr
        binaries[kind] = binary
    return binaries


@pytest.mark.integration
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("carriers", (1, 2))
@pytest.mark.parametrize("collector", range(5))
def test_native_and_virtual_waiters_make_progress(mixed_binaries, kind, carriers, collector):
    result = subprocess.run([str(mixed_binaries[kind]), str(carriers)],
                            capture_output=True, text=True, timeout=12,
                            env=dict(os.environ, PCC_GC_BACKEND=str(collector)))
    assert (result.returncode, result.stdout, result.stderr) == (0, "MIXED_WAITERS_OK\n", "")


@pytest.fixture(scope="module")
def retry_binaries(tmp_path_factory):
    root = tmp_path_factory.mktemp("threading-waiter-retry")
    binaries = {}
    for kind in KINDS:
        source = root / (kind + ".py")
        source.write_text(RETRY_PROGRAM.replace("__RESOURCE__", kind))
        binary = source.with_suffix("")
        build = subprocess.run([sys.executable, "-m", "pcc", "--backend", "self",
                                str(source), "-o", str(binary)], capture_output=True,
                               text=True, timeout=120, env=dict(os.environ, PCC_NO_AUTO_PCC1="1"))
        assert build.returncode == 0, build.stdout + build.stderr
        binaries[kind] = binary
    return binaries


@pytest.mark.integration
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ("queued", "notified", "spurious"))
@pytest.mark.parametrize("collector", range(5))
def test_cancelled_or_spuriously_woken_waiter_keeps_progress(retry_binaries, kind, mode, collector):
    result = subprocess.run([str(retry_binaries[kind]), mode], capture_output=True,
                            text=True, timeout=10,
                            env=dict(os.environ, PCC_GC_BACKEND=str(collector)))
    assert (result.returncode, result.stdout, result.stderr) == (0, "WAITER_RETRY_CLEANUP_OK\n", "")
