"""Exercise the four-mutator GC1 candidate without explicit collection."""
import os
from pathlib import Path
import re

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_worker_free_gc1_collections_progress_on_four_allocating_mutators(
    tmp_path, explicit_owned_runtime,
):
    source = Path(__file__).with_name("fixtures") / "gc1_four_mutators_native.c"
    binary = tmp_path / "gc1_four_mutators"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    env = dict(os.environ, PCC_GC_BACKEND="1", PCC_GC_DEBT_THRESHOLD="131072")
    for run in range(3):
        result = _record_execution(tmp_path, f"four-mutators-{run}", [str(binary)], env)
        assert (result["returncode"], result["stderr"]) == (0, ""), result
        assert re.fullmatch(
            r"GC1_FOUR_MUTATORS_OK commits=[1-9]\d* sweeps=[1-9]\d* max_stw_us=[1-9]\d*\n",
            result["stdout"],
        ), result
