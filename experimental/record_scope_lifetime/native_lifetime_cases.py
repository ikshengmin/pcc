"""Future emitted-execution corpus; host success is only the reference oracle.

Run each case with each of PCC's five collectors only after an existing,
identity-verified native compiler/runtime is explicitly selected. This file does
not select, download, provision, or build a compiler and is not a lifetime pass.
No implicit claim is made that all these surfaces are supported in PCC today.
"""

import gc
import weakref


def alias_and_late_closure():
    payload = [1]
    alias = payload

    def read():
        return payload

    payload = [2]
    alias.append(3)
    assert alias == [1, 3]
    assert read() is payload and payload == [2]
    return 1


def exception_finally_escape():
    events = []
    escaped = []
    try:
        value = [1]
        escaped.append(value)
        raise ValueError("expected")
    except ValueError:
        value.append(2)
    finally:
        events.append("finally")
    assert escaped[0] is value and value == [1, 2]
    assert events == ["finally"]
    return 1


def generator_suspend_close():
    events = []

    def produce():
        payload = [1]
        try:
            yield payload
            payload.append(3)
            yield payload
        finally:
            events.append("closed")

    suspended = produce()
    escaped = next(suspended)
    gc.collect()
    escaped.append(2)
    assert next(suspended) is escaped and escaped == [1, 2, 3]
    suspended.close()
    assert events == ["closed"]
    return 1


def weakref_finalizer_resurrection():
    revived = []
    events = []

    class Payload:
        def __del__(self):
            events.append("finalized")
            revived.append(self)

    value = Payload()
    seen = weakref.ref(value)
    del value
    gc.collect()
    assert events == ["finalized"] and len(revived) == 1
    # A fresh weakref observes the resurrected live owner. Do not presume the
    # original weakref survives every collector's cyclic-finalization protocol.
    after = weakref.ref(revived[0])
    revived.clear()
    gc.collect()
    assert after() is None and seen() is None and events == ["finalized"]
    return 1


CASES = (alias_and_late_closure, exception_finally_escape,
         generator_suspend_close, weakref_finalizer_resurrection)


if __name__ == "__main__":
    for case in CASES:
        assert case() == 1
        print(case.__name__ + ": ok")
