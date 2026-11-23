"""Public cyclic-list reachability, not swept-shell or backing-array accounting."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from tests.python.process_timeout import run_process_group_timeout


_PROGRAM = '''"""Public reachability of built-in cyclic lists, independent of sweep counters."""

import gc
import threading
import weakref


class Payload:
    pass


def make_cycle(sentinel, witnesses, roots):
    items = []
    items.append(items)
    items.append(sentinel)
    payload = Payload()
    items.append(payload)
    witness = weakref.ref(payload)
    # Resolve while the creator owns the object, never in the collecting
    # thread before collection. Store only the weak observer.
    assert witness() is payload
    witnesses.append(witness)
    if roots is not None:
        roots.append(items)


def create_cycles(sentinel, witnesses, roots):
    make_cycle(sentinel, witnesses, None)
    make_cycle(sentinel, witnesses, roots)


def verify_and_drop(sentinel, witnesses, roots, checks):
    anchored = roots[0]
    checks.append(
        len(anchored) == 3
        and anchored[0] is anchored
        and anchored[1] is sentinel
        and witnesses[1]() is anchored[2]
    )
    roots[0] = None


def main():
    gc.disable()
    sentinel = []
    sentinel.append(sentinel)
    witnesses = []
    roots = []
    checks = []
    creator = threading.Thread(target=create_cycles, args=(sentinel, witnesses, roots))
    creator.start()
    creator.join()
    assert len(witnesses) == 2 and len(roots) == 1

    gc.collect(0)
    assert witnesses[0]() is None
    assert len(sentinel) == 1 and sentinel[0] is sentinel

    observer = threading.Thread(target=verify_and_drop, args=(sentinel, witnesses, roots, checks))
    observer.start()
    observer.join()
    assert checks == [True]

    # A surviving gen-0 object may have been promoted on CPython.
    gc.collect()
    assert witnesses[1]() is None
    assert len(sentinel) == 1 and sentinel[0] is sentinel
    print("unrooted expired; rooted live then expired; sentinel preserved")


main()
'''


_EXPECTED = "unrooted expired; rooted live then expired; sentinel preserved\n"
_FINAL_ASSERTION = "assert witnesses[1]() is None"


def _write_program(tmp_path: Path) -> Path:
    source = tmp_path / "cleared_list_public.py"
    source.write_bytes(_PROGRAM.encode("utf-8"))
    return source


def _run_cpython(source: Path):
    return run_process_group_timeout(
        [sys.executable, "-I", "-B", str(source)],
        env=dict(os.environ, NO_COLOR="1"),
        timeout=10,
    )


def _reference(source: Path) -> str:
    result = _run_cpython(source)
    assert (result.returncode, result.stdout, result.stderr) == (0, _EXPECTED, "")
    return result.stdout


def test_public_cyclic_list_fixture_detects_a_retained_root(tmp_path: Path) -> None:
    source = _write_program(tmp_path)
    _reference(source)
    release = "    roots[0] = None"
    assert _PROGRAM.count(release) == 1
    control = tmp_path / "retained_root.py"
    control.write_bytes(_PROGRAM.replace(release, "    pass", 1).encode("utf-8"))
    result = _run_cpython(control)
    assert result.returncode == 1 and not result.stdout, result
    # Merely failing is not enough: the anchored cycle must pass its first
    # reachability check but fail to expire when its root is deliberately kept.
    final_assertion_line, = (
        number for number, line in enumerate(_PROGRAM.splitlines(), 1)
        if line.strip() == _FINAL_ASSERTION
    )
    lines = result.stderr.splitlines()
    assert f'File "{control}", line {final_assertion_line}, in main' in (
        line.strip() for line in lines
    ), result.stderr
    assert lines[-1] == "AssertionError", result.stderr


@pytest.mark.integration
def test_public_cyclic_list_collection_matches_cpython(
    tmp_path: Path,
    pcc_runtime_archive: Path,
) -> None:
    from pcc.frontends.python.pipeline import compile_python

    source = _write_program(tmp_path)
    expected = _reference(source)
    binary = tmp_path / ("cleared_list_public.exe" if os.name == "nt" else "cleared_list_public")
    compile_python(
        str(source), str(binary),
        backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in (0,):
        result = run_process_group_timeout(
            [str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)), timeout=10,
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), (
            backend, result,
        )
