"""Automatic GC2 finalizers/weakrefs with the same exact-list review ELF off/on."""
import os
import re
import textwrap

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    assert_reference_program,
    explicit_owned_runtime,
)


BODY = textwrap.dedent("""    import gc
    import threading
    import weakref

    kept = []
    events = []
    live = []

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

    def allocation_pressure(count):
        for i in range(count):
            temporary = []
            temporary.append(i)
            temporary.append(temporary)
            assert temporary[0] == i

    def main():
        gc.disable()
        gc.set_threshold(20, 2, 2)
        survivor = [101]
        live.append(survivor)
        creator = threading.Thread(target=create_terminal_cycle)
        creator.start()
        creator.join(5)
        assert not creator.is_alive()
        allocation_pressure(5000)
        assert events == []
        gc.enable()
        i = 0
        while (len(events) < 2 or not bridge_reached()) and i < 16:
            allocation_pressure(2000)
            i += 1
        assert events == ["weakref", "finalized"], events
        assert kept[0]() is None
        assert live[0] is survivor
        assert live[0][0] == 101
        allocation_pressure(6000)
        assert events == ["weakref", "finalized"], events
        report_bridge()
        print("GC2_LIFECYCLE_OK")

    main()
""")
REFERENCE = "def bridge_reached(): return True\ndef report_bridge(): pass\n"
NATIVE = textwrap.dedent("""    from pcc.extern import extern, c_int64
    from pcc.unsafe import atomic_load_i64, global_addr

    mode = extern("pcc_gc2_exact_list_bridge_mode", (), c_int64)

    def read_visits():
        return atomic_load_i64(global_addr("pcc_gc2_review_list_bridge_visits"), 0, "acquire")

    def bridge_reached():
        return mode() == 0 or read_visits() > 0

    def report_bridge():
        print(mode())
        print(read_visits())

    def advance_reference_only():
        pass
""")


def test_gc2_bridge_automatic_finalizers_cpython_control(tmp_path):
    assert_reference_program("TRY_REENTER = False\n" + REFERENCE + BODY,
                             "GC2_LIFECYCLE_OK\n", tmp_path)


def _run_owned_off_and_on(tmp_path, runtime, program, final_line):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "gc2_bridge_lifecycle.py"
    binary = tmp_path / "gc2_bridge_lifecycle"
    source.write_text(NATIVE + program)
    compile_python(str(source), str(binary), backend="self", runtime_archive=str(runtime))
    for repeat in range(3):
        for enabled in (False, True):
            env = dict(os.environ, PCC_GC_BACKEND="2", PCC_GC_DEBT_THRESHOLD="131072")
            env.pop("PCC_GC2_EXACT_LIST_BRIDGE", None)
            if enabled:
                env["PCC_GC2_EXACT_LIST_BRIDGE"] = "1"
            result = _record_execution(tmp_path, f"finalizers-{int(enabled)}-{repeat}",
                                       [str(binary)], env)
            assert (result["returncode"], result["stderr"]) == (0, ""), result
            expected = r"2\n[1-9]\d*\n" if enabled else r"0\n0\n"
            assert re.fullmatch(expected + final_line + r"\n", result["stdout"]), result


@pytest.mark.integration
@pytest.mark.parametrize("reenter", (False, True), ids=("ordinary", "callback-reentry"))
def test_gc2_bridge_automatic_weakref_then_finalizer(tmp_path, explicit_owned_runtime, reenter):
    _run_owned_off_and_on(tmp_path, explicit_owned_runtime,
                          f"TRY_REENTER = {reenter!r}\n" + BODY, "GC2_LIFECYCLE_OK")


RESURRECTION = textwrap.dedent("""    import gc
    import threading
    import weakref

    retired = []
    rescued = []
    events = []

    class Payload:
        def __init__(self):
            self.value = 101

    class Item:
        def __init__(self):
            self.peer = self
            self.data = [[Payload()]]

        def __del__(self):
            events.append("finalizer")
            rescued.append(self)

    def before_rescue(handle):
        assert handle() is None
        events.append("first weakref")

    def after_rescue(handle):
        assert handle() is None
        events.append("second weakref")

    def leave_a_cycle():
        target = Item()
        retired.append(weakref.ref(target, before_rescue))

    def inspect_and_drop_rescue():
        target = rescued.pop()
        assert target.peer is target
        assert target.data[0][0].value == 101
        retired.append(weakref.ref(target, after_rescue))

    def joined(callback):
        worker = threading.Thread(target=callback)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()

    def drive_until_events(count):
        rounds = 0
        while (len(events) < count or not bridge_reached()) and rounds < 20:
            for j in range(2000):
                garbage = [j]
                garbage.append(garbage)
                assert garbage[0] == j
            rounds += 1
        assert len(events) == count, events

    def main():
        gc.disable()
        gc.set_threshold(20, 2, 2)
        keeper = [[17]]
        joined(leave_a_cycle)
        assert events == []
        gc.enable()
        advance_reference_only()
        drive_until_events(2)
        assert events == ["first weakref", "finalizer"]
        assert retired[0]() is None
        assert len(rescued) == 1
        joined(inspect_and_drop_rescue)
        advance_reference_only()
        drive_until_events(3)
        assert events == ["first weakref", "finalizer", "second weakref"]
        assert len(rescued) == 0
        assert retired[1]() is None
        assert keeper[0][0] == 17
        report_bridge()
        print("GC2_RESURRECTION_OK")

    main()
""")


def test_gc2_bridge_resurrection_cpython_control(tmp_path):
    # Compare life-cycle semantics, not CPython's automatic generation
    # cadence. Both owned cases leave this hook a no-op.
    advance = "def advance_reference_only(): gc.collect()\n"
    assert_reference_program(REFERENCE + advance + RESURRECTION, "GC2_RESURRECTION_OK\n", tmp_path)


@pytest.mark.integration
def test_gc2_bridge_keeps_resurrected_subgraph_then_releases_once(tmp_path, explicit_owned_runtime):
    _run_owned_off_and_on(tmp_path, explicit_owned_runtime, RESURRECTION, "GC2_RESURRECTION_OK")
