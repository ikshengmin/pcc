"""Ordinary Python exception-chain witness; no injected runtime state.

CPython traceback frames can retain the generator until collection. The owned
GC0 lane additionally sets require_immediate=True to prove finalization already
occurred at the failing function's error exit, before inspecting its exception.
Each producer runs on a real worker thread. Joining it retires its stack before
the main thread collects, avoiding stale conservative roots in count-free GC.
"""

import gc
import threading


events = []


def values():
    try:
        yield 1
    finally:
        events.append("closed")


def fail(mode, cause):
    gen = values()
    assert next(gen) == 1
    try:
        raise ValueError("middle")
    except ValueError:
        if mode == 1:
            raise TypeError("inner") from None
        if mode == 2:
            raise TypeError("inner") from cause
        raise TypeError("inner")


def exercise(mode, require_immediate):
    cause = LookupError("explicit cause")
    try:
        raise KeyError("outer")
    except KeyError as outer:
        try:
            fail(mode, cause)
        except TypeError as inner:
            # Native GC0-only timing proof. Do not move gen cleanup into a
            # finally block: that changes which exception is handled there.
            if require_immediate:
                assert events == ["closed"], "generator was not finalized at error exit"
            assert str(inner) == "inner"
            assert isinstance(inner.__context__, ValueError)
            assert str(inner.__context__) == "middle"
            assert inner.__context__.__context__ is outer
            assert outer.__context__ is None
            if mode == 2:
                assert inner.__cause__ is cause
            else:
                assert inner.__cause__ is None
            assert inner.__suppress_context__ is (mode != 0)
        else:
            raise AssertionError("missing inner exception")


def worker(mode, require_immediate, main_ident, results):
    try:
        assert threading.get_ident() != main_ident, "producer ran on the main thread"
        exercise(mode, require_immediate)
    except BaseException as error:
        # Do not retain the exception or its traceback in shared state. A
        # failed worker assertion must still fail the main-thread test.
        results.append("error: " + type(error).__name__ + ": " + str(error))
    else:
        results.append("ok")


def main(require_immediate):
    gc.disable()
    main_ident = threading.get_ident()
    for mode in range(3):
        events.clear()
        results = []
        producer = threading.Thread(
            target=worker, args=(mode, require_immediate, main_ident, results),
        )
        producer.start()
        producer.join()
        assert results == ["ok"], results
        for index in range(4):
            gc.collect()
        assert events == ["closed"]
    print("GENERATOR_FINALIZER_CONTEXT_OK")


main(False)
