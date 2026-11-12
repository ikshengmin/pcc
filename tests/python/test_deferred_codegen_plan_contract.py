"""Producer/consumer contract for the v2 deferred plan's fallback width.

The process scheduler owns budgeted admission; the wire field must also be safe
if the plan is resumed without that budget. These host checks stop before any
worker or linker executes. The native integration below exercises publication.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def deferred_plan(tmp_path, monkeypatch):
    from pcc.frontends.python import native_deferred
    from pcc.frontends.python.pipeline_frontend_workers import write_worker_manifest

    # Native executable admission is orthogonal to these host wire checks.
    monkeypatch.setattr(native_deferred, "_native_worker", lambda _path: None)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"unit-test archive; never linked")
    sources = [str(tmp_path / ("source" + str(index) + ".py")) for index in range(3)]
    names = ["source" + str(index) for index in range(3)]
    manifests = []
    for index in (2, 0, 1):
        manifest = tmp_path / ("worker" + str(index))
        write_worker_manifest(
            str(manifest), str(tmp_path / ("result" + str(index))),
            str(tmp_path), "", str(tmp_path), sources, names, [index],
            entry_module="source0", sibling_inits=[], ir_scaffold_mode="on",
            verbose=False,
        )
        manifests.append(str(manifest))
    return str(tmp_path / "plan"), {
        "worker_executable": str(tmp_path / "native-worker"),
        "output_path": str(tmp_path / "output"),
        "runtime_archive": str(runtime),
        "artifact_root": str(tmp_path),
        "module_count": 3,
        "oversized_count": 1,
        "manifests": manifests,
    }


@pytest.mark.parametrize("requested_jobs", [1, 2, 3, 10])
@pytest.mark.parametrize("resume_with_budget", [False, True])
def test_budgeted_plan_preserves_safe_fallback_and_scheduler_width(
    deferred_plan, monkeypatch, requested_jobs, resume_with_budget,
):
    from pcc.frontends.python import (
        deferred_frontend_schedule as scheduler,
        native_deferred,
        pipeline_frontend_parallel as parallel,
    )

    plan_path, fields = deferred_plan
    budget = 16 * 1024**3
    # These are inputs to a scheduling model; no child processes are launched.
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(budget))
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "auto")
    monkeypatch.setattr(scheduler, "parallel_cpu_budget", lambda: 10)
    jobs = parallel._codegen_safe_worker_jobs(requested_jobs, True, 2, plan_path)
    assert jobs == requested_jobs
    parallel._write_deferred_codegen_plan(plan_path, safe_jobs=jobs, **fields)
    if not resume_with_budget:
        monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES")
    observed = []

    class SchedulingBoundaryReached(Exception):
        pass

    def observe_schedule(commands, manifests, pco_commands, sidecars, oversized, fallback):
        assert len(commands) == len(manifests) == len(pco_commands) == len(sidecars) == 3
        assert oversized == 1
        observed.append((fallback, scheduler._resource_width_and_budget(fallback)))
        raise SchedulingBoundaryReached

    monkeypatch.setattr(native_deferred, "run_frontend_pco_commands", observe_schedule)
    with pytest.raises(SchedulingBoundaryReached):
        native_deferred.run(plan_path)
    assert observed == [
        (min(requested_jobs, 2), (10, budget) if resume_with_budget else None),
    ]


@pytest.mark.parametrize("invalid_jobs", [-1, 0, 3, 10])
def test_reader_still_rejects_unsafe_fallback_before_scheduling(
    deferred_plan, monkeypatch, invalid_jobs,
):
    from pcc.frontends.python import native_deferred, pipeline_frontend_parallel

    plan_path, fields = deferred_plan
    pipeline_frontend_parallel._write_deferred_codegen_plan(plan_path, safe_jobs=2, **fields)
    lines = Path(plan_path).read_text().splitlines()
    lines[9] = str(invalid_jobs)
    Path(plan_path).write_text("\n".join(lines) + "\n")

    def forbidden(*args):
        pytest.fail("an invalid fallback width must not reach scheduling")

    monkeypatch.setattr(native_deferred, "run_frontend_pco_commands", forbidden)
    with pytest.raises(ValueError, match="native deferred codegen plan count mismatch"):
        native_deferred.run(plan_path)


@pytest.mark.integration
def test_native_budgeted_three_module_plan_compiles_and_executes(
    tmp_path, native_pcc1_compiler, pcc_runtime_archive,
):
    from scripts.verify_nolibpython import linked_libraries
    from tests.python.process_timeout import run_process_group_timeout

    (tmp_path / "left.py").write_text("def left() -> int:\n    return 20\n")
    (tmp_path / "right.py").write_text("def right() -> int:\n    return 22\n")
    source = tmp_path / "main.py"
    source.write_text(
        "from left import left\nfrom right import right\n"
        "def main() -> int:\n    return left() + right()\nprint(main())\n"
    )
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True,
        check=True, timeout=10,
    )
    source.write_text(
        source.read_text()
        + 'from pcc.extern import c_int64, extern\n'
        + 'observed_gc = extern("pcc_gc_backend", (), c_int64)\n'
        + 'print("GC", observed_gc())\n'
    )
    output = tmp_path / "program"
    codegen = tmp_path / "codegen.plan"
    link = tmp_path / "link.plan"
    environment = dict(os.environ)
    # Keep the enclosing guard's tree budget, including its admission checks.
    assert int(environment.get("PCC_WORKER_TREE_BUDGET_BYTES", "0")) > 0
    environment.update(
        PYTHONPATH=str(tmp_path), PCC_RUNTIME_ARCHIVE=str(pcc_runtime_archive),
        PCC_HOST_PYTHON="/usr/bin/false", PCC_HOST_PCC="/usr/bin/false",
        PCC_RUNTIME_CC="/usr/bin/false", PCC_NO_AUTO_PCC1="1",
        # Request the failing width even on a two-core reference host. This
        # sets a CPU ceiling, not a bypass of the process/memory admission.
        PCC_PY_FRONTEND_JOBS="3", PCC_SELF_BACKEND_JOBS="2",
        PCC_PYTHON_IR_PASSES="off", PCC_GC_BACKEND="0",
        PCC_DIRECT_INDEXED_KERNEL_CAPTURE="1", PCC_DIRECT_INDEXED_KERNEL_EMIT="1",
        PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK="1",
        PCC_DIRECT_INDEXED_KERNEL_FUSE_USES="1",
        PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND="1",
        PCC_DIRECT_INDEXED_NATIVE_OBJECT="1",
        PCC_DEFER_FRONTEND_CODEGEN_PLAN=str(codegen),
        PCC_DEFER_FRONTEND_OUTPUT=str(output), PCC_DEFER_SELF_LINK_PLAN=str(link),
    )
    root = Path(__file__).resolve().parents[2]
    wrapper = root / "scripts" / "run_pcc_native_deferred.py"
    compiled = run_process_group_timeout(
        [sys.executable, str(wrapper), str(native_pcc1_compiler), str(codegen),
         str(link), "--", str(native_pcc1_compiler), "--backend", "self",
         str(source), "-o", str(output)],
        env=environment, timeout=120,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    lines = codegen.read_text().splitlines()
    assert lines[0] == "pcc.frontend-codegen-plan.v2"
    assert int(lines[7]) == int(lines[10]) == 3
    assert 1 <= int(lines[9]) <= 2
    inputs = Path(str(codegen) + ".internal-inputs").read_text().splitlines()
    assert inputs[:2] == ["pcc.macho-internal-inputs.v1", "3"]
    assert len(inputs[2:]) == 3 and all(item.startswith("PCO\t") for item in inputs[2:])
    libraries = linked_libraries(output.read_bytes())
    assert libraries
    assert not any("libpython" in name.lower() or "llvm" in name.lower() for name in libraries)
    assert reference.stdout == "42\n"
    for collector in range(5):
        environment["PCC_GC_BACKEND"] = str(collector)
        executed = subprocess.run(
            [str(output)], env=environment, capture_output=True, text=True, timeout=10,
        )
        assert executed.returncode == 0, (collector, executed.stderr)
        assert executed.stdout == reference.stdout + f"GC {collector}\n", (collector, executed.stdout)
        assert executed.stderr == "", executed.stderr
