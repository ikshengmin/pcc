"""Actual C facade, actual GC4 forwarding commits, and unchanged reference counts."""
from pathlib import Path
import os
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.xdist_group(name="capi_borrowed_item_address")]


@pytest.fixture(scope="module")
def borrowed_item_probe(tmp_path_factory, pcc_runtime_archive):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    out = tmp_path_factory.mktemp("capi_borrowed_item_address") / "probe"
    return compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc4_capi_borrowed_item.c", out,
        runtime_archive=Path(pcc_runtime_archive),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )


def _check(binary, gc, kind, mode):
    actual = subprocess.run(
        [str(binary), str(gc), kind, mode], capture_output=True, text=True,
        timeout=15, env=dict(os.environ, PCC_GC_BACKEND=str(gc), PATH="/nonexistent"),
    )
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        0, f"BORROW_OK {gc} {kind} {mode}\n", "",
    ), (gc, kind, mode, actual)


@pytest.mark.parametrize("kind", tuple("ltdes"), ids=["list", "tuple", "dict", "dict-error", "dict-string"])
@pytest.mark.parametrize("mode", ("1", "2"), ids=["one-hop", "two-hops"])
def test_forwarded_borrow_returns_and_pins_terminal(borrowed_item_probe, kind, mode):
    _check(borrowed_item_probe, 4, kind, mode)


@pytest.mark.parametrize("gc", range(5))
@pytest.mark.parametrize("kind", tuple("ltdes"), ids=["list", "tuple", "dict", "dict-error", "dict-string"])
def test_unforwarded_borrow_preserves_pin_and_refcount(borrowed_item_probe, gc, kind):
    _check(borrowed_item_probe, gc, kind, "b")


@pytest.fixture(scope="module")
def borrowed_handoff_probe(tmp_path_factory, pcc_runtime_archive):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    out = tmp_path_factory.mktemp("capi_borrowed_handoff") / "probe"
    return compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc4_capi_borrowed_handoff.c", out,
        runtime_archive=Path(pcc_runtime_archive),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )


@pytest.mark.parametrize("mode", ("w", "c"), ids=["real-stop-rewrite", "real-graph-contention"])
def test_borrowed_result_handoff_uses_actual_registered_slot_and_commit_lock(borrowed_handoff_probe, mode):
    result = subprocess.run(
        [str(borrowed_handoff_probe), mode], capture_output=True, text=True,
        timeout=15, env=dict(os.environ, PCC_GC_BACKEND="4", PATH="/nonexistent"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, f"HANDOFF_OK {mode}\n", ""), (mode, result)
