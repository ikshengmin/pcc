"""Terminal weak callbacks and finalizers on the worker-free GC1 candidate."""
import os
import textwrap

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    assert_reference_program,
    explicit_owned_runtime,
)

BODY = textwrap.dedent("""\
    import gc
    import threading
    import weakref

    kept = []
    events = []

    class Item:
        def __init__(self):
            self.peer = self
        def __del__(self):
            events.append("finalized")

    def on_dead(handle):
        assert handle() is None
        events.append("weakref")
        if TRY_REENTER:
            gc.collect()
            assert events == ["weakref"]

    def create_terminal_cycle():
        target = Item()
        kept.append(weakref.ref(target, on_dead))

    def drive_allocation(count):
        for i in range(count):
            root = []
            root.append(i)
            root.append(root)
            assert root[0] == i

    def main():
        gc.disable()
        gc.set_threshold(20, 2, 2)
        worker = threading.Thread(target=create_terminal_cycle)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        drive_allocation(5000)
        assert events == []
        gc.enable()
        i = 0
        while len(events) < 2 and i < 12:
            drive_allocation(2000)
            i += 1
        assert events == ["weakref", "finalized"], events
        assert kept[0]() is None
        drive_allocation(6000)
        assert events == ["weakref", "finalized"], events
        print("GC1_AUTOMATIC_FINALIZERS_OK")

    main()
""")
EXPECTED = "GC1_AUTOMATIC_FINALIZERS_OK\n"


def program(reenter):
    return f"TRY_REENTER = {reenter!r}\n" + BODY


def test_automatic_weakref_and_finalizer_reference(tmp_path):
    # Reentrant gc.collect is a PCC scope check, not a CPython contract.
    assert_reference_program(program(False), EXPECTED, tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize("reenter", (False, True), ids=("ordinary", "callback-reentry"))
def test_worker_free_gc1_runs_weakref_and_finalizer_automatically(
    explicit_owned_runtime, tmp_path, reenter,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "auto_finalizers.py"
    binary = tmp_path / "auto_finalizers"
    source.write_text(program(reenter))
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(explicit_owned_runtime))
    env = dict(os.environ, PCC_GC_BACKEND="1", PCC_GC_DEBT_THRESHOLD="131072")
    result = _record_execution(tmp_path, "auto-finalizers", [str(binary)], env)
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, EXPECTED, "",
    ), result
