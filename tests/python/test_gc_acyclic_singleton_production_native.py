"""CPython event oracle versus the actual sealed-candidate production sweep."""
import os
from pathlib import Path
import sys

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.owned_runtime_c_fixture import admitted_runtime
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def _singleton_sweep(tmp_path_factory):
    binary = tmp_path_factory.mktemp("gc_acyclic_production") / "sweep"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_acyclic_singleton_production.c", binary,
        runtime_archive=admitted_runtime(),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
@pytest.mark.parametrize("arm", (
    "terminal", "rescue", "thread-hold", "thread-drop", "thread-publish", "thread-publish-clear",
))
def test_acyclic_singleton_production_sweep_matches_cpython(_singleton_sweep, backend, arm):
    oracle = ROOT / "tests/fixtures/native/constructor/acyclic_singleton_weakref_oracle.py"
    expected = run_process_group_timeout([sys.executable, str(oracle), arm], timeout=15)
    assert expected.returncode == 0 and expected.stderr == ""
    assert expected.stdout == {
        "terminal": "del:1 weak:1 live:0\n",
        "rescue": "del:1 live:1\n",
        "thread-hold": "del:1 thread:1 live:1 after:1\n",
        "thread-drop": "del:1 thread:1 weak:1 live:0 after:0\n",
        "thread-publish": "del:1 thread:1 live:1 after:1\n",
        "thread-publish-clear": "del:1 thread:1 weak:1 live:0 after:0\n",
    }[arm]
    actual = run_process_group_timeout(
        [str(_singleton_sweep), arm, "route", "scrub-reader"], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    assert (actual.returncode, actual.stdout) == (0, expected.stdout)
    # A stopped candidate-only remark must not masquerade as one that passed
    # by falling back globally. A write and deletion may leave the same shade
    # as a publication; unlike a current root it still needs the full oracle.
    assert actual.stderr == ("full=1\n" if arm == "thread-publish-clear" else "full=0\n")


@pytest.fixture(scope="module")
def _layered_sweep(tmp_path_factory):
    binary = tmp_path_factory.mktemp("gc_acyclic_layers_production") / "sweep"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_acyclic_layers_production.c", binary,
        runtime_archive=admitted_runtime(),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
@pytest.mark.parametrize("arm", ("chain", "rescue", "caller"))
def test_acyclic_layered_production_sweep_matches_cpython(_layered_sweep, backend, arm):
    oracle = ROOT / "tests/fixtures/native/constructor/acyclic_layers_weakref_oracle.py"
    expected = run_process_group_timeout([sys.executable, str(oracle), arm], timeout=15)
    assert expected.returncode == 0 and expected.stderr == ""
    assert expected.stdout == {
        "chain": "a-del a-weak b-del b-weak b-dead\n",
        "rescue": "a-del a-weak b-live\n",
        "caller": "a-del a-weak b-live\n",
    }[arm]
    actual = run_process_group_timeout(
        [str(_layered_sweep), arm], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    assert (actual.returncode, actual.stdout) == (0, expected.stdout), actual.stderr
    assert actual.stderr == ("full=1\n" if arm == "rescue" else "full=0\n")
