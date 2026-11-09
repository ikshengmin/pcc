"""PEP 442: finalizer-created sibling weakrefs die before their callback.

The same source is compared with CPython and owned pcc0/pcc1, all five GCs.
Producer/consumer thread exit removes conservative target aliases. The
reference and native executions are CI-only, not source-review evidence.
"""
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

    refs = []
    saved = []
    observations = []
    finalized = []
    checked = []

    def callback(ref):
        observations.append(ref() is None)
        observations.append(refs[0]() is None)
        # Reentrant collection must not redeliver this pending callback.
        gc.collect()

    class Item:
        def __init__(self, resurrect):
            self.cycle = self
            self.payload = 777
            self.resurrect = resurrect

        def __del__(self):
            finalized.append(self.resurrect)
            refs.append(weakref.ref(self))
            refs.append(weakref.ref(self, callback))
            if self.resurrect:
                saved.append(self)

    def produce_dead():
        Item(False)

    def produce_resurrected():
        Item(True)

    def check_and_drop_resurrected():
        checked.append(refs[0]() is saved[0])
        checked.append(refs[1]() is saved[0])
        checked.append(saved[0].payload == 777)
        saved.clear()

    def run_worker(function):
        worker = threading.Thread(target=function)
        worker.start()
        worker.join()

    def main():
        gc.disable()
        run_worker(produce_dead)
        for attempt in range(8):
            gc.collect()
            if len(observations) == 2:
                break
        assert finalized == [False], finalized
        assert observations == [True, True], observations
        assert len(refs) == 2
        refs.clear()
        observations.clear()
        run_worker(produce_resurrected)
        for attempt in range(8):
            gc.collect()
            if len(saved) == 1:
                break
        assert finalized == [False, True], finalized
        assert len(refs) == 2 and len(saved) == 1
        assert observations == []
        run_worker(check_and_drop_resurrected)
        assert checked == [True, True, True], checked
        for attempt in range(8):
            gc.collect()
            if len(observations) == 2:
                break
        assert observations == [True, True], observations
        assert finalized == [False, True], finalized
        assert refs[0]() is None and refs[1]() is None
        print('FINALIZER_CREATED_WEAKREFS_OK')

    main()
""")


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_finalizer_created_weakrefs_native_five_gc(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM, "FINALIZER_CREATED_WEAKREFS_OK\n", tmp_path,
        python_program_compiler, mode, explicit_owned_runtime, capfd,
        provenance_probe="2",
    )
    assert (tmp_path / "compiler-wrapper.stderr").read_text() == ""
