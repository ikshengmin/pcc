"""Exercise conservative capture without relying on an ASLR collision."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.owned_runtime_c_fixture import admitted_runtime
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def process_entry_root_words(tmp_path_factory):
    binary = tmp_path_factory.mktemp("gc_process_entry_root_words") / "words"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_process_entry_root_words.c",
        binary,
        runtime_archive=admitted_runtime(),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend", (0,))
@pytest.mark.parametrize(
    "args,mask",
    [((), 4), (("two",), 4), (("worker", "full"), 8), (("peer",), 4)],
    ids=("one-entry-frame", "two-entry-frames", "foreign-worker", "parked-main"),
)
def test_gc_process_entry_root_words_native(process_entry_root_words, backend, args, mask):
    actual = run_process_group_timeout(
        [str(process_entry_root_words), *args],
        timeout=15,
        env=dict(
            os.environ,
            PCC_GC_BACKEND=str(backend),
            PCC_GC_DEBT_THRESHOLD="1099511627776",
        ),
    )
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        0, f"snapshot={mask} callback={mask}\n", "",
    )
