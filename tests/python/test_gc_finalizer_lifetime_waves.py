"""A finalizer cannot precede the release of a still-readable strong owner.

Compare public lifetime facts, not CPython's reference-counting schedule.
The fixture records only scalar labels. It never retains an Item to observe it.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

from tests.python.owned_regression_support import _record_execution


SOURCE = Path(__file__).parent / "fixtures" / "gc_finalizer_lifetime_waves.py"
A = list(range(100, 116))
B = list(range(200, 216))


def _read_labels(line, name):
    key, raw = line.split(" ", 1)
    assert key == name
    values = ast.literal_eval(raw)
    assert type(values) is list and all(type(v) is int for v in values)
    return values


def _assert_lifetimes(result, *, reference=False):
    assert (result["returncode"], result["stderr"]) == (0, ""), result
    text = result["stdout"]
    assert len(text) <= 8192  # Reject incomplete/oversized recorder evidence.
    head, births, held_a, held_b, calls, end = text.splitlines()
    version, *counts = head.split()
    assert version == "GC4WAVE1" and end == "END_GC4WAVE1"
    born_a, born_b, at_a, both_held, b_still_held, final, overflow = map(int, counts)
    assert (born_a, born_b, overflow) == (16, 32, 0)
    assert 0 <= at_a <= both_held <= b_still_held <= final <= 128
    assert _read_labels(births, "BIRTH") == A + B
    # Payloads actually read *after* the corresponding public collection;
    # expected birth IDs alone would not prove the owners are still in scope.
    assert _read_labels(held_a, "HOLD-A") == A[::2]
    assert _read_labels(held_b, "HOLD-B") == B[::2]
    observed = _read_labels(calls, "CALL")
    assert len(observed) == final
    seen = set(observed)
    assert seen <= set(A + B), "callback for an unknown birth"
    assert len(seen) == len(observed), "a logical label finalized more than once"
    assert not set(observed[:at_a]).intersection(B), "callback before wave B existed"
    assert not set(observed[:both_held]).intersection(A[::2]), "A keeper finalized early"
    assert not set(observed[:b_still_held]).intersection(B[::2]), "B keeper finalized early"
    # Non-vacuity: each keeper cohort must appear in a later window, not
    # necessarily at a specific assignment or collection. Absence of another
    # label at the final checkpoint is not evidence of a leak or reachability.
    assert set(observed[both_held:]).intersection(A[::2]), "A keeper not seen in a later window"
    assert set(observed[b_still_held:]).intersection(B[::2]), "B keeper not seen in a later window"
    if reference:
        assert seen == set(A + B), "CPython reference did not complete this control"


def test_two_wave_finalizer_reference(tmp_path):
    result = _record_execution(
        tmp_path, "cpython", [sys.executable, str(SOURCE)], os.environ.copy(),
    )
    _assert_lifetimes(result, reference=True)


@pytest.mark.integration
def test_native_two_wave_finalizers_respect_strong_owners_all_gcs(
    tmp_path, threaded_pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python

    output = tmp_path / "gc_finalizer_lifetime_waves"
    compile_python(
        str(SOURCE), str(output), backend="self",
        runtime_archive=str(threaded_pcc_runtime_archive),
    )
    for backend in (0,):
        label = f"gc{backend}"
        result = _record_execution(
            tmp_path, label, [str(output)],
            dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        try:
            _assert_lifetimes(result)
        except AssertionError as exc:
            raise AssertionError(f"{label}: {exc}") from exc
