"""Execute the startup-publication race through the owned native setter."""

import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def setter_race_binary(tmp_path_factory, pcc_runtime_archive):
    source = Path(__file__).with_name("fixtures") / "gc_backend_configuration_race_native.c"
    binary = tmp_path_factory.mktemp("setter_configuration_race") / "setter_race"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=pcc_runtime_archive,
        cpp_args=("-I", str(REPO / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group("setter_configuration_race")
@pytest.mark.parametrize("backend", [1, 2, 4])
def test_published_tracing_mode_wins_after_optimistic_setter_check(setter_race_binary, backend):
    run = subprocess.run(
        [str(setter_race_binary), str(backend)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="0",
                 PCC_GC_TRACING_ONLY="0", PCC_GC4_TRACING_ONLY="0"),
    )
    expected = f"SETTER_RACE_NATIVE_OK backend={backend} rejected=1 pause_restored=1\n"
    assert (run.returncode, run.stdout, run.stderr) == (0, expected, ""), run
