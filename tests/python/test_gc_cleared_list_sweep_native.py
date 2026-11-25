"""Public collection releases populated cyclic lists and preserves live neighbours.

Python gc.collect and weakref reference semantics: create the same number of
populated self-referential lists as the former public-collection witness, in a
thread that has joined before collection. Observe payload lifetime rather
than addresses of already freed objects or a retired collector's internal
sweep states.
"""
import subprocess
import sys
import textwrap

import pytest

from tests.python.owned_regression_support import assert_owned_program, explicit_owned_runtime


PROGRAM = textwrap.dedent("""\
    import gc
    import threading
    import weakref

    count = 777
    finalized = []
    weak_events = []
    refs = []

    class Payload:
        def __init__(self, ident):
            self.ident = ident
        def __del__(self):
            finalized.append(self.ident)

    def weak_callback(ref):
        assert ref() is None
        weak_events.append(1)

    sentinel = []
    sentinel.append(sentinel)
    live = Payload(-1)
    sentinel.append(live)
    live_ref = weakref.ref(live)

    def produce():
        for i in range(count):
            obj = Payload(i)
            cycle = [obj, sentinel]
            cycle.append(cycle)
            refs.append(weakref.ref(obj, weak_callback))

    def main():
        gc.disable()
        worker = threading.Thread(target=produce)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        assert len(refs) == count
        assert finalized == [] and weak_events == []
        gc.collect()
        assert sorted(finalized) == list(range(count))
        assert len(weak_events) == count
        for ref in refs:
            assert ref() is None
        assert sentinel[0] is sentinel and sentinel[1] is live
        assert live_ref() is live
        gc.collect()
        assert sorted(finalized) == list(range(count))
        assert len(weak_events) == count
        print("CYCLIC_LIST_COLLECTION_OK")

    main()
""")
EXPECTED = "CYCLIC_LIST_COLLECTION_OK\n"


def test_public_collect_retires_populated_cyclic_lists_cpython():
    run = subprocess.run([sys.executable, "-c", PROGRAM],
                         capture_output=True, text=True, timeout=20)
    assert (run.returncode, run.stdout, run.stderr) == (0, EXPECTED, ""), run


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_public_collect_retires_populated_cyclic_lists(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
