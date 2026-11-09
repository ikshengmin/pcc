"""Terminal weakref callbacks clear siblings before reentrant user code.

Specification: weakref.ref callback ordering and PEP 442 disposal step 1.
The acyclic target exercises GC0's immediate terminal-deallocation path;
producer-thread exit also makes the same program collectable by GC1-4.
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

    events = []
    refs = []
    keep = []

    class Item:
        pass

    def older_callback(ref):
        events.append('older')
        events.append(ref() is None)

    def unrelated_callback(ref):
        events.append('unrelated')

    def newer_callback(ref):
        events.append('newer')
        events.append(refs[0]() is None)
        # The global chain's next node refers to a different, live target.
        # Its final owner disappears here, as do both same-target owners.
        refs.clear()
        gc.collect()

    def produce():
        target = Item()
        unrelated = Item()
        keep.append(unrelated)
        refs.append(weakref.ref(target, older_callback))
        refs.append(weakref.ref(unrelated, unrelated_callback))
        refs.append(weakref.ref(target, newer_callback))

    def main():
        worker = threading.Thread(target=produce)
        worker.start()
        worker.join()
        for attempt in range(8):
            gc.collect()
            if len(events) == 4:
                break
        assert events == ['newer', True, 'older', True], events
        assert refs == []
        keep.clear()
        gc.collect()
        assert events == ['newer', True, 'older', True], events
        print('TERMINAL_WEAKREF_CALLBACKS_OK')

    main()
""")


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_terminal_weakref_callback_mutation_native_five_gc(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM, "TERMINAL_WEAKREF_CALLBACKS_OK\n", tmp_path,
        python_program_compiler, mode, explicit_owned_runtime, capfd,
        provenance_probe="2",
    )
    assert (tmp_path / "compiler-wrapper.stderr").read_text() == ""
