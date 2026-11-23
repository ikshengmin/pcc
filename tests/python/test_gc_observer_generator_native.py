"""A pending generator excludes the same-stop negative observer hint."""
import os
from pathlib import Path
import sys

import pytest

from tests.owned_runtime_c_fixture import admitted_runtime
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "tests/fixtures/native/constructor"
pytestmark = pytest.mark.xdist_group("gc-observer-generator-native")


@pytest.fixture(scope="module")
def observer_programs(tmp_path_factory):
    from pcc import build
    from pcc.frontends.python.pipeline import compile_python

    runtime = admitted_runtime()
    output = tmp_path_factory.mktemp("generator-observer-guard")
    probe = build(
        ROOT / "tests/fixtures/native/gc_observer_generator_marker.c",
        kind="object", backend="self", optimize=0,
        out_dir=output / "obj", use_compile_cache=False,
    )
    programs = []
    for source, inputs in (
        ("gc_observer_generator_oracle.py", ()),
        ("gc_observer_generator_native.py", (str(probe.output_path),)),
    ):
        binary = output / Path(source).stem
        compile_python(
            str(SOURCES / source), str(binary), backend="self",
            ir_scaffold_mode="on", runtime_archive=str(runtime), object_inputs=inputs,
        )
        programs.append(binary)
    return tuple(programs)


@pytest.mark.integration
@pytest.mark.parametrize("gc_backend", (0,))
def test_generator_observer_matches_cpython(observer_programs, gc_backend):
    oracle = run_process_group_timeout(
        [sys.executable, str(SOURCES / "gc_observer_generator_oracle.py")], timeout=15,
    )
    result = run_process_group_timeout(
        [str(observer_programs[0])], timeout=15, env=dict(
            os.environ, PCC_GC_BACKEND=str(gc_backend),
            PCC_GC_DEBT_THRESHOLD="1099511627776",
        ),
    )
    assert oracle.returncode == 0 and not oracle.stderr
    assert (result.returncode, result.stdout, result.stderr) == (0, oracle.stdout, "")


@pytest.mark.integration
@pytest.mark.parametrize("gc_backend", (1, 2, 3))
def test_pending_generator_suppresses_negative_hint_in_its_own_callback(
    observer_programs, gc_backend,
):
    result = run_process_group_timeout(
        [str(observer_programs[1])], timeout=15, env=dict(
            os.environ, PCC_GC_BACKEND=str(gc_backend),
            PCC_GC_DEBT_THRESHOLD="1099511627776",
        ),
    )
    assert result.returncode == 0 and not result.stderr
    assert result.stdout.endswith("suspended closed once; unstarted not closed\n")
