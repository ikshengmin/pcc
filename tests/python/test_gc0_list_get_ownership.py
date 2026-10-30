"""An owning list read must retain its selected item before replacement."""

import os
from pathlib import Path

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]


def test_owning_getters_survive_concurrent_element_replacement(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "list_get_ownership"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/list_get_owning_concurrency.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    for getter in ("0", "1"):
        for probe in ("0", "2"):
            run = run_process_group_timeout(
                [str(binary), getter], timeout=90,
                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="0",
                         PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe),
            )
            expected = ("LIST_GET_OWNING_CONCURRENCY_OK\n" if getter == "0"
                        else "LIST_GETITEM_OWNING_CONCURRENCY_OK\n")
            assert (run.returncode, run.stdout, run.stderr) == (0, expected, ""), (getter, probe)


def test_getter_owners_preserve_finalizers_weakrefs_and_index_errors(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "list_get_lifetime"
    compile_python(
        str(ROOT / "tests/fixtures/native/list_get_owner_lifetime.py"), str(binary),
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
                0, "LIST_GET_OWNER_LIFETIME_OK\n", "",
            ), (backend, probe)
