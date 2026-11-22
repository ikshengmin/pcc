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
""")


def test_gc2_bridge_automatic_finalizers_cpython_control(tmp_path):
    assert_reference_program("TRY_REENTER = False\n" + REFERENCE + BODY,
                             "GC2_LIFECYCLE_OK\n", tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize("reenter", (False, True), ids=("ordinary", "callback-reentry"))
def test_gc2_bridge_automatic_weakref_then_finalizer(tmp_path, explicit_owned_runtime, reenter):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "gc2_bridge_finalizers.py"
    binary = tmp_path / "gc2_bridge_finalizers"
    source.write_text(f"TRY_REENTER = {reenter!r}\n" + NATIVE + BODY)
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(explicit_owned_runtime))
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
            assert re.fullmatch(expected + r"GC2_LIFECYCLE_OK\n", result["stdout"]), result
