"""A terminal cycle's finalizer and weakref callback must not be lost."""

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

    kept = []
    outcomes = []

    class Item:
        def __del__(self):
            outcomes.append("finalized")

    def weak_callback(ref):
        assert ref() is None
        outcomes.append("weakref")

    def make_cycle():
        item = Item()
        item.peer = item
        kept.append(weakref.ref(item, weak_callback))

    def main():
        gc.disable()
        # The constructing thread leaves no stack root after join.
        worker = threading.Thread(target=make_cycle)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        assert outcomes == []
        gc.collect()
        assert sorted(outcomes) == ["finalized", "weakref"], outcomes
        assert kept[0]() is None
        gc.collect()
        assert sorted(outcomes) == ["finalized", "weakref"], outcomes
        print("GC_CYCLE_WEAKREF_SYNC_OK")

    main()
""")

EXPECTED = "GC_CYCLE_WEAKREF_SYNC_OK\n"


def test_gc_cycle_weakref_sync_cpython():
    run = subprocess.run(
        [sys.executable, "-c", PROGRAM], capture_output=True, text=True, timeout=20,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == EXPECTED


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_gc_cycle_weakref_sync_native(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM, EXPECTED, tmp_path, python_program_compiler, mode,
        explicit_owned_runtime, capfd,
    )
