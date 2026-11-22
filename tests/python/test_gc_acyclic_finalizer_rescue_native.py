"""Native, test-invoked #9 correctness arm: rescue or terminal settlement."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_native_acyclic_finalizer_recheck_and_terminal_child_order(tmp_path, explicit_owned_runtime):
    repo = Path(__file__).resolve().parents[2]
    source = Path(__file__).with_name("fixtures") / "gc_acyclic_finalizer_rescue.c"
    binary = tmp_path / "acyclic_rescue"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    for backend in (1, 2, 3):
        for arm in ("rescue", "no-rescue"):
            for extra in ((), ("nested",)):
                lane = f"gc{backend}-{arm}" + ("-nested" if extra else "")
                result = _record_execution(
                    tmp_path, lane, [str(binary), arm, *extra],
                    dict(os.environ, PCC_GC_BACKEND=str(backend)),
                )
                assert (result["returncode"], result["stdout"], result["stderr"]) == (
                    0, "GC_ACYCLIC_FINALIZER_RESCUE_OK\n", "",
                ), (lane, result)

    # Count actual visits with the same two candidates and three sizes
    # of unrelated live heap, with direct, old-owner and new-owner publication.
    # An incomplete journal must reach the full oracle, not return false dead.
    for backend in (1, 2, 3):
        for source, expected in (("bounded", "bounded 3 2"), ("journal", "bounded 3 3"),
                                 ("allocated", "bounded 3 3"), ("fallback", "fallback full")):
            visits = []
            for size in (128, 512, 2048):
                result = _record_execution(
                    tmp_path, f"gc{backend}-{source}-{size}",
                    [str(binary), "rescue", source, str(size)],
                    dict(os.environ, PCC_GC_BACKEND=str(backend)),
                )
                assert result["returncode"] == 0 and not result["stderr"], (
                    backend, source, size, result)
                metrics, bounded, marker = result["stdout"].splitlines()
                assert bounded == expected
                assert marker == "GC_ACYCLIC_FINALIZER_RESCUE_OK"
                name, calls, nodes, saved, _elapsed = metrics.split()
                assert (name, int(calls), int(saved)) == ("recheck", 1, 2)
                visits.append(int(nodes))
            assert visits[1] - visits[0] == 2 * (512 - 128), (backend, source, visits)
            assert visits[2] - visits[0] == 2 * (2048 - 128), (backend, source, visits)
