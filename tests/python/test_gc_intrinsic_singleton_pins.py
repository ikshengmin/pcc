"""Exact image singletons need no pins; heap immortality alone is insufficient."""

import os
from pathlib import Path
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_intrinsic_singletons_preserve_flags_and_balance_with_heap_controls(
    tmp_path, pcc_runtime_archive,
):
    binary = tmp_path / "singleton_pins"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_intrinsic_singleton_pins.c", binary,
        runtime_archive=Path(pcc_runtime_archive),
    )
    # The collector is fixed by the first managed object: one run each.
    for backend in range(5):
        run = subprocess.run(
            [str(binary), str(backend)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "INTRINSIC_SINGLETON_PINS_OK\n", "",
        ), backend


PROGRAM = """import gc


def through_call(value):
    identity = id(value)
    junk = [[i, str(i)] for i in range(300)]
    gc.collect()
    assert id(value) == identity
    assert len(junk) == 300
    return value


def main():
    values = [None, True, False, NotImplemented, Ellipsis]
    for repeat in range(3):
        for value in values:
            assert through_call(value) is value
    print("SINGLETON_VALUES_OK")


main()
"""


def test_dynamic_singletons_keep_identity_through_calls_and_collection(
    tmp_path, pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "singleton_values.py"
    binary = tmp_path / "singleton_values"
    source.write_text(PROGRAM)
    compile_python(
        str(source), str(binary), backend="self", libpython_mode="off",
        ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "SINGLETON_VALUES_OK\n", "",
        ), backend
