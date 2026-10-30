"""Controls distinguishing dynamic handle validity from a static lifetime proof."""

import gc
import weakref

import pytest

from .scope_record import LifetimeError, Region


def test_payload_alias_escapes_lookup_and_is_not_revoked_by_close():
    with Region() as region:
        borrow = region.new_scope()
        borrow.define("payload", [])
        escaped = borrow.lookup("payload")
    with pytest.raises(LifetimeError):
        borrow.lookup("payload")
    # Ordinary Python aliases stay valid. A handle guard is not a borrow checker
    # that proves all derived references dead or authorizes native deallocation.
    escaped.append(7)
    assert escaped == [7]


def test_closed_stale_handle_still_retains_its_payload():
    class Payload:
        pass

    with Region() as region:
        borrow = region.new_scope()
        payload = Payload()
        seen = weakref.ref(payload)
        borrow.define("payload", payload)
        del payload
    gc.collect()
    assert seen() is not None
    with pytest.raises(LifetimeError):
        borrow.lookup("payload")
    del borrow
    gc.collect()
    assert seen() is None


def test_move_does_not_establish_exclusive_mutability():
    with Region() as region:
        borrow = region.new_scope()
        borrow.define("payload", [])
        capture = borrow.capture()
        successor = borrow.move()
        capture.lookup("payload").append(1)
        assert successor.lookup("payload") is capture.lookup("payload")
        assert successor.lookup("payload") == [1]
        capture.release()


def test_suspension_keeps_origin_live_until_generator_cleanup():
    observed = []

    def produce():
        with Region() as region:
            borrow = region.new_scope()
            borrow.define("payload", [])
            observed.append(borrow)
            try:
                yield borrow.lookup("payload")
            finally:
                observed.append("finally")

    generator = produce()
    escaped = next(generator)
    assert observed[0].lookup("payload") is escaped
    generator.close()
    assert observed[-1] == "finally"
    with pytest.raises(LifetimeError):
        observed[0].lookup("payload")
    escaped.append(2)
    assert escaped == [2]


@pytest.mark.parametrize("case_name", ["alias_and_late_closure", "exception_finally_escape",
                                       "generator_suspend_close", "weakref_finalizer_resurrection"])
def test_future_native_corpus_host_reference_only(case_name):
    from . import native_lifetime_cases

    assert getattr(native_lifetime_cases, case_name)() == 1
