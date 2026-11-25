"""GC0 must finish a foreign thread's last release without owner assistance."""

import subprocess
import sys
import textwrap

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program,
    explicit_owned_runtime,
)


PROGRAM = textwrap.dedent("""\
    import gc
    import threading
    import weakref

    slot = [None]
    retained_weakref = [None]
    outcomes = []
    ready = threading.Event()
    may_exit = threading.Event()

    class Item:
        def __del__(self):
            outcomes.append("finalized")

    def weak_callback(ref):
        assert ref() is None
        outcomes.append("weakref")

    def publish():
        item = Item()
        retained_weakref[0] = weakref.ref(item, weak_callback)
        slot[0] = item

    def owner():
        publish()
        ready.set()
        may_exit.wait()

    def main():
        gc.disable()
        worker = threading.Thread(target=owner)
        worker.start()
        try:
            assert ready.wait(5)
            assert outcomes == []
            # Do not keep a local strong reference from inspecting slot[0].
            slot[0] = None
            # A design that queues the decrement for the owning thread or
            # gc.collect() fails here, even if it eventually finalizes.
            assert outcomes == ["finalized", "weakref"], outcomes
            assert retained_weakref[0]() is None
            assert not may_exit.is_set()
        finally:
            may_exit.set()
            worker.join(5)
        assert not worker.is_alive()
        gc.collect()
        assert outcomes == ["finalized", "weakref"], outcomes
        print("GC0_FOREIGN_LAST_DROP_OK")

    main()
""")

EXPECTED = "GC0_FOREIGN_LAST_DROP_OK\n"


def test_gc0_cross_thread_last_drop_cpython():
    run = subprocess.run(
        [sys.executable, "-c", PROGRAM], capture_output=True, text=True, timeout=20,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == EXPECTED


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_gc0_cross_thread_last_drop_native(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM, EXPECTED, tmp_path, python_program_compiler, mode,
        explicit_owned_runtime, capfd,
    )
