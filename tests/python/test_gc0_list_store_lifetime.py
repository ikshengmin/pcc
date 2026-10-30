"""Native list replacement must serialize publication and retire after unlock."""

import os
from pathlib import Path

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]


def test_list_store_and_root_copy_keep_live_owners_under_contention(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "list_store_root_copy"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/list_store_root_copy_race.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    for probe in ("0", "2"):
        run = run_process_group_timeout(
            [str(binary)], timeout=90,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="0",
                     PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "ROOT_COPY_CONCURRENCY_OK\n", "",
        ), probe


def test_list_replacement_finalizes_after_unlock_on_all_collectors(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "list_store_finalizer"
    compile_python(
        str(ROOT / "tests/fixtures/native/list_store_finalizer.py"), str(binary),
        backend="self", libpython_mode="off", ir_scaffold_mode="on",
        runtime_archive=str(threaded_pcc_runtime_archive),
    )
    for backend in range(5):
        for probe in ("0", "2"):
            run = run_process_group_timeout(
                [str(binary)], timeout=45,
                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                         PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe),
            )
            assert (run.returncode, run.stdout, run.stderr) == (
                0, "GC0_LIST_STORE_FINALIZER_OK\n", "",
            ), (backend, probe)
