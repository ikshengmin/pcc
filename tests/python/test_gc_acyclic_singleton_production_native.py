"""CPython event oracle versus the actual sealed-candidate production sweep."""
import os
import re
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
@pytest.mark.parametrize("arm", ("chain", "rescue", "caller", "withdrawn", "holder", "mixed", "mixed-tail", "mixed-rescue", "late-new", "worker", "pin", "interior", "dict-holder", "field-holder", "dict-replace", "list-replace", "set-holder", "dict-update", "list-slice", "set-update", "capi-dict", "capi-list", "capi-attr", "extension-field", "caller-old-holder", "peer-hold", "peer-depart", "peer-new"))
def test_acyclic_layered_production_sweep_matches_cpython(_layered_sweep, backend, arm):
    oracle = ROOT / "tests/fixtures/native/constructor/acyclic_layers_weakref_oracle.py"
    expected = run_process_group_timeout([sys.executable, str(oracle), arm], timeout=15)
    assert expected.returncode == 0 and expected.stderr == ""
    assert expected.stdout == {
        "chain": "a-del a-weak b-del b-weak b-dead\n",
        "rescue": "a-del a-weak b-live\n",
        "caller": "a-del a-weak b-live\n",
        "withdrawn": "a-del a-weak b-del b-weak b-dead\n",
        "holder": "a-del a-weak b-live\n",
        "mixed": "a-del a-weak b-weak b-del b-dead\n",
        "mixed-tail": "a-del a-weak b-weak b-del tail-del b-dead\n",
        "mixed-rescue": "a-del a-weak b-live\n",
        "late-new": "a-del a-weak b-del b-new b-weak b-dead\n",
        "worker": "a-del a-weak b-del b-weak b-dead\n",
        "pin": "a-del a-weak b-live\n",
        "interior": "a-del a-weak b-live\n",
        "dict-holder": "a-del a-weak b-live\n",
        "field-holder": "a-del a-weak b-live\n",
        "dict-replace": "a-del a-weak b-live\n",
        "list-replace": "a-del a-weak b-live\n",
        "set-holder": "a-del a-weak b-live\n",
        "dict-update": "a-del a-weak b-live\n",
        "list-slice": "a-del a-weak b-live\n",
        "set-update": "a-del a-weak b-live\n",
        "capi-dict": "a-del a-weak b-live\n",
        "capi-list": "a-del a-weak b-live\n",
        "capi-attr": "a-del a-weak b-live\n",
        "extension-field": "a-del a-weak b-live\n",
        "caller-old-holder": "a-del a-weak b-live\n",
        "peer-hold": "a-del a-weak b-live\n",
        "peer-depart": "a-del a-weak b-del b-weak b-dead\n",
        "peer-new": "a-del a-weak b-del b-new b-weak b-dead\n",
    }[arm]
    actual = run_process_group_timeout(
        [str(_layered_sweep), arm], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    assert actual.returncode == 0, actual.stderr
    if arm == "peer-depart":
        # The maintainer approved independent sibling order within one
        # layer: compare exactly which events occur and each object's
        # ordering, not CPython's explicit A-before-B reference release.
        for trace in (expected.stdout, actual.stdout):
            *events, verdict = trace.split()
            assert verdict == "b-dead"
            assert sorted(events) == ["a-del", "a-weak", "b-del", "b-weak"]
            assert events.index("a-del") < events.index("a-weak")
            assert events.index("b-del") < events.index("b-weak")
        events = actual.stdout.split()
        assert max(events.index("a-del"), events.index("b-del")) < min(
            events.index("a-weak"), events.index("b-weak")
        )
    else:
        assert actual.stdout == expected.stdout, actual.stderr
    rechecks = 2 if arm in ("mixed", "mixed-tail") else 1 if arm in ("withdrawn", "holder", "dict-holder", "field-holder", "dict-replace", "list-replace", "set-holder", "dict-update", "list-slice", "set-update", "capi-dict", "capi-list", "capi-attr", "extension-field", "caller-old-holder") else 0
    assert actual.stderr == f"full={rechecks} scans=0\n"


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
@pytest.mark.parametrize("count", (63, 64, 65))
@pytest.mark.parametrize("keep", (True, False), ids=("keep", "drop"))
def test_layered_remark_distinguishes_roots_across_mask_word(_layered_sweep, backend, count, keep):
    oracle = ROOT / "tests/fixtures/native/constructor/acyclic_layers_weakref_oracle.py"
    expected = run_process_group_timeout(
        [sys.executable, str(oracle), "rescue" if keep else "withdrawn"], timeout=15,
    )
    actual = run_process_group_timeout(
        [str(_layered_sweep), "wide-keep" if keep else "wide-drop", str(count)], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    assert expected.returncode == 0 and not expected.stderr
    assert (actual.returncode, actual.stdout) == (0, expected.stdout), actual.stderr
    assert actual.stderr == f"full={int(not keep)} scans=0\n"


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
@pytest.mark.parametrize("arm", (
    "silent", "silent-live-del", "silent-unrelated-weakrefs", "silent-unrelated-weakrefs-live-del",
))
def test_acyclic_sweep_without_observers_keeps_legacy_path(_layered_sweep, backend, arm):
    actual = run_process_group_timeout(
        [str(_layered_sweep), arm], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    # PASS0 calls is counted only by the legacy path on this sealed cohort.
    # Test the actual route and reclamation, not a source-name assertion.
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        0, "legacy=1 reclaimed=2 callbacks=0 recheck=0\n", "",
    )

@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
def test_acyclic_sweep_with_only_weak_callbacks_keeps_review_path(_layered_sweep, backend):
    actual = run_process_group_timeout(
        [str(_layered_sweep), "only-weakrefs"], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        0, "legacy=0 reclaimed=2 callbacks=2 recheck=0\n", "",
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
def test_acyclic_sweep_with_only_finalizers_keeps_layer_order(_layered_sweep, backend):
    actual = run_process_group_timeout(
        [str(_layered_sweep), "finalizer-only"], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        0, "legacy=0 reclaimed=2 callbacks=2 recheck=0\na-del b-del\n", "",
    )


@pytest.fixture(scope="module")
def _layered_cpy_owner(tmp_path_factory):
    binary = tmp_path_factory.mktemp("gc_layered_cpy_owner") / "sweep"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_layered_cpy_owner.c", binary,
        runtime_archive=admitted_runtime(),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
@pytest.mark.parametrize("arm", ("keep", "withdraw"))
def test_layered_sweep_checks_independent_cpy_owner(_layered_cpy_owner, backend, arm):
    actual = run_process_group_timeout(
        [str(_layered_cpy_owner), arm], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    expected = ("del=1 released=0 reclaimed=1 usable=1 full=0\n" if arm == "keep"
                else "del=1 released=1 reclaimed=2 usable=0 full=1\n")
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, expected, "")



@pytest.fixture(scope="module")
def _callback_scale_sweep(tmp_path_factory):
    binary = tmp_path_factory.mktemp("gc_callback_scale_production") / "sweep"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_acyclic_layers_scale.c", binary,
        runtime_archive=admitted_runtime(),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 3))
@pytest.mark.parametrize("count", (4, 16, 64))
@pytest.mark.parametrize("public", (False, True), ids=("sealed", "public"))
def test_callback_chain_scale_matches_cpython(_callback_scale_sweep, backend, count, public):
    oracle = ROOT / "tests/fixtures/native/constructor/acyclic_layers_scale_oracle.py"
    expected = run_process_group_timeout([sys.executable, str(oracle), str(count)], timeout=15)
    actual = run_process_group_timeout(
        [str(_callback_scale_sweep), str(count), "0", "0", "2", str(int(public))], timeout=15,
        env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                 PCC_GC_DEBT_THRESHOLD="1099511627776"),
    )
    assert expected.returncode == 0 and not expected.stderr
    assert actual.returncode == 0 and not actual.stderr, actual
    assert re.fullmatch(
        rf"weak=0 scans=0 n={count} live=0 del={count} reclaimed=\d+ full=0"
        rf" windows={-1 if public else 2 * count} us=\d+\n"
        rf"events={expected.stdout.strip()} callbacks={count} public={int(public)}\n",
        actual.stdout,
    ), actual
