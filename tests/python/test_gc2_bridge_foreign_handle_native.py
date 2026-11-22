"""C-owned foreign payload remains anchored while the alternate GC2 marker runs."""
import os
import re
import textwrap

import pytest

from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime
from tests.python.test_gc_foreign_handle_collection_native import (
    C_OWNED_PROGRAM,
    FOREIGN_PROGRAM_HEADER,
    _assert_c_owned_foreign_result,
    _build_foreign_program,
    _run_foreign_program,
)


PROGRAM = FOREIGN_PROGRAM_HEADER + textwrap.dedent("""    import threading
    from pcc.unsafe import atomic_load_i64, global_addr

    hold = extern("fixture_foreign_handle_hold", (c_obj,), c_void)
    hold_second = extern("fixture_foreign_handle_hold_second", (c_obj,), c_void)
    drop = extern("fixture_foreign_handle_drop", (), c_void)
    drop_second = extern("fixture_foreign_handle_drop_second", (), c_void)
    raw_owner = extern("fixture_foreign_handle_check_raw_owner", (), c_int64)
    held_refcount = extern("fixture_foreign_handle_held_refcount", (), c_int64)
    bridge_mode = extern("pcc_gc2_exact_list_bridge_mode", (), c_int64)
    telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
    SWEEP_CALLS = 126

    def visits():
        return atomic_load_i64(global_addr("pcc_gc2_review_list_bridge_visits"), 0, "acquire")

    def create_holds():
        handle = new_handle()
        assert handle is not None
        hold(handle)
        hold_second(handle)

    def drive_auto_sweep():
        before = telemetry(SWEEP_CALLS)
        before_visits = visits()
        attempt = 0
        while (telemetry(SWEEP_CALLS) == before or
               (bridge_mode() != 0 and visits() == before_visits)) and attempt < 18:
            for i in range(2000):
                cycle = [i]
                cycle.append(cycle)
                assert cycle[0] == i
            attempt += 1
        assert telemetry(SWEEP_CALLS) > before
        assert bridge_mode() == 0 or visits() > before_visits

    def main():
        gc.disable()
        gc.set_threshold(20, 2, 2)
        keeper = [[17]]
        source_thread = threading.Thread(target=create_holds)
        source_thread.start()
        source_thread.join(5)
        assert not source_thread.is_alive()
        assert release_count() == 0
        assert held_refcount() >= 2
        gc.enable()
        drive_auto_sweep()
        assert release_count() == 0
        assert raw_owner() == 1
        refs = held_refcount()
        assert refs >= 2
        drop_second()
        drive_auto_sweep()
        assert release_count() == 0
        assert raw_owner() == 1
        assert held_refcount() == refs - 1
        drop()
        for _ in range(4):
            gc.collect()
            assert release_count() <= 1
        assert release_count() == 1
        assert keeper[0][0] == 17
        print(bridge_mode())
        print(visits())
        print("GC2_C_HOLDER_OK")

    main()
""")


@pytest.mark.integration
def test_gc2_bridge_auto_sweep_respects_two_then_one_c_owner(tmp_path, explicit_owned_runtime):
    from pcc.frontends.python.pipeline import compile_python

    binary = _build_foreign_program(tmp_path, explicit_owned_runtime, compile_python, PROGRAM)
    for repeat in range(3):
        for enabled in (False, True):
            env = dict(os.environ, PCC_GC_BACKEND="2", PCC_GC_DEBT_THRESHOLD="131072")
            env.pop("PCC_GC2_EXACT_LIST_BRIDGE", None)
            if enabled:
                env["PCC_GC2_EXACT_LIST_BRIDGE"] = "1"
            result = _record_execution(tmp_path, f"c-holders-{int(enabled)}-{repeat}", [str(binary)], env)
            assert (result["returncode"], result["stderr"]) == (0, ""), result
            expected = r"2\n[1-9]\d*\n" if enabled else r"0\n0\n"
            assert re.fullmatch(expected + r"GC2_C_HOLDER_OK\n", result["stdout"]), result


@pytest.mark.integration
def test_gc2_bridge_flag_does_not_change_c_holders_in_other_backends(
    tmp_path, explicit_owned_runtime, monkeypatch,
):
    from pcc.frontends.python.pipeline import compile_python

    # Identical owned program and expected C ABI contract; only the inherited
    # GC2 review selector changes between fresh runtime processes.
    binary = _build_foreign_program(tmp_path, explicit_owned_runtime, compile_python, C_OWNED_PROGRAM)
    for backend in (0, 1, 3, 4):
        for enabled in (False, True):
            if enabled:
                monkeypatch.setenv("PCC_GC2_EXACT_LIST_BRIDGE", "1")
            else:
                monkeypatch.delenv("PCC_GC2_EXACT_LIST_BRIDGE", raising=False)
            _assert_c_owned_foreign_result(backend, _run_foreign_program(binary, backend))
