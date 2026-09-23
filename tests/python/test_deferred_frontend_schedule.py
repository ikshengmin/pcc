from itertools import combinations

import pytest

from pcc.py_frontend import deferred_frontend_schedule as scheduler


def test_call_node_score_matches_wire_marker_count():
    for payload in (
        b"",
        b'"Call"',
        b'prefix"Call"middle"Call"suffix',
        b'"Caller"\\"Call"\xff"Call"',
    ):
        assert scheduler._call_node_score(payload) == payload.count(b'"Call"')


@pytest.mark.integration
def test_call_node_score_executes_natively_under_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_py_runtime_archive,
):
    import inspect
    import os
    import subprocess

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    payloads = (
        b"",
        b'"Call"',
        b'prefix"Call"middle"Call"suffix',
        b'"Caller"\\"Call"\xff"Call"',
    )
    source = tmp_path / "ast_score.py"
    cases = repr(tuple((payload, payload.count(b'"Call"')) for payload in payloads))
    source.write_text(
        "from pcc.unsafe import abi_constant, load_i8, null, ptr_is_null\n"
        + inspect.getsource(scheduler._native_ast_reads_available)
        + "\n_NATIVE_AST_READS = _native_ast_reads_available()\n"
        + inspect.getsource(scheduler._call_node_score)
        + "\nfor payload, expected in " + cases + ":\n"
        + "    assert _call_node_score(payload) == expected\n"
        + "print('ast-score-ok')\n"
    )
    binary = tmp_path / "ast_score"
    python_program_compiler(
        str(source), str(binary), backend="self", libpython_mode="off",
        runtime_archive=str(pcc_py_runtime_archive),
    )
    for backend in range(5):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert run.returncode == 0, (backend, run.stdout, run.stderr)
        assert run.stdout.strip() == "ast-score-ok"


@pytest.mark.parametrize("budget_gib", [1, 2, 4, 8, 16])
@pytest.mark.parametrize("cpus", [1, 2, 4, 12, 64])
@pytest.mark.parametrize("phase", ["frontend", "pco0", "pco1", "pco2", "pco3", "pco4"])
def test_every_admitted_window_fits_the_budget(budget_gib, cpus, phase):
    sizes = [0, 30000, 200000, 1000000, 1900000, 3000000, 7000000, 14000000, 50000000]
    budget = budget_gib * 1073741824
    if phase == "frontend":
        floor = scheduler.indexed_frontend_floor_bytes
        groups = scheduler.frontend_groups(sizes, budget, cpus)
    else:
        gc_backend = int(phase[-1])
        floor = lambda size: scheduler.indexed_pco_floor_bytes(size, gc_backend)
        groups = scheduler.pco_groups(sizes, budget, cpus, gc_backend)
    assert sorted(index for indices, _ in groups for index in indices) == list(range(len(sizes)))
    for indices, width in groups:
        assert 1 <= width <= min(cpus, 12)
        # Check every possible active set, not only the first launch batch.
        if width > 1:
            for window in combinations(indices, min(len(indices), width)):
                charged = sum(floor(sizes[i]) for i in window)
                assert charged <= budget - 1073741824


def test_small_frontend_jobs_are_not_limited_by_combined_emit_estimates():
    groups = scheduler.frontend_groups([100000] * 30, 8 * 1073741824, 12)
    assert groups == [(list(range(30)), 9)]
    assert scheduler.frontend_groups([100000] * 30, 8 * 1073741824, 2) == [(list(range(30)), 2)]


def test_pco_phase_uses_its_own_budget_instead_of_serial_safe_lane():
    sizes = [60000000, 14000000, 1000000, 1000000]
    assert scheduler.pco_groups(sizes, 6 * 1073741824, 12) == [([0], 3), ([1], 8), ([2, 3], 12)]
    for gc_backend in range(1, 5):
        assert scheduler.pco_groups(sizes, 6 * 1073741824, 12, gc_backend) == [([0], 1), ([1], 2), ([2, 3], 12)]
    assert scheduler.pco_groups([1000000] * 30, 6 * 1073741824, 2) == [(list(range(30)), 2)]


@pytest.mark.parametrize("raw,budget", [("2", "8589934592"), ("", "8589934592"), ("auto", ""), ("auto", "invalid")])
@pytest.mark.parametrize("phase", ["frontend", "pco"])
def test_explicit_or_unbudgeted_calls_preserve_conservative_policy(monkeypatch, raw, budget, phase):
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", raw)
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", budget)
    calls = []
    monkeypatch.setattr(scheduler, "run_worker_processes", lambda commands, width: calls.append((commands, width)))
    run = scheduler.run_frontend_commands if phase == "frontend" else scheduler.run_pco_commands
    run(["a", "b", "c"], ["unused"] * 3, 1, 2)
    assert calls == [(["a"], 1), (["b", "c"], 2)]


@pytest.mark.parametrize("gc_backend", ["0", "1", "2", "3", "4", "unknown"])
def test_auto_pco_reads_all_sidecars_before_launch_and_preserves_command_ownership(tmp_path, monkeypatch, gc_backend):
    sizes = [1000000, 60000000, 14000000, 1000000]
    sidecars = []
    for index, size in enumerate(sizes):
        path = tmp_path / (str(index) + ".pidx")
        with path.open("wb") as stream:
            stream.truncate(size)
        sidecars.append(str(path))
    calls = []
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "auto")
    monkeypatch.setenv("PCC_GC_BACKEND", gc_backend)
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(6 * 1073741824))
    monkeypatch.setattr(scheduler, "parallel_cpu_budget", lambda: 12)
    monkeypatch.setattr(
        scheduler, "run_weighted_worker_processes",
        lambda commands, reservations, width, budget: calls.append(
            (commands, reservations, width, budget)
        ),
    )
    scheduler.run_pco_commands(["a", "b", "c", "d"], sidecars, 2, 1)
    selected_gc = 0 if gc_backend == "0" else -1
    assert calls == [(
        ["b", "c", "a", "d"],
        [scheduler.indexed_pco_floor_bytes(sizes[index], selected_gc)
         for index in (1, 2, 0, 3)],
        12,
        5 * 1073741824,
    )]
    calls.clear()
    with pytest.raises(ValueError, match="inventory mismatch"):
        scheduler.run_pco_commands(["a"], sidecars, 0, 1)
    with pytest.raises(OSError):
        scheduler.run_pco_commands(["a", "b"], [sidecars[0], str(tmp_path / "missing")], 0, 1)
    assert not calls


def test_gc0_reservations_cover_all_392_system_peak_measurements():
    import json
    from pathlib import Path

    corpus = json.loads((Path(__file__).parents[1] / "data/pco_gc0_worker_peaks.json").read_text())
    assert corpus["gc_backend"] == 0
    assert len(corpus["workers"]) == 392
    for row in corpus["workers"]:
        peak = max(row["max_rss_bytes"], row["peak_footprint_bytes"])
        required = (peak * 5 + 3) // 4 + 128 * 1048576
        assert scheduler.indexed_pco_floor_bytes(row["input_bytes"]) >= required, row["index"]


def test_auto_reads_assigned_ast_and_passes_selected_groups(tmp_path, monkeypatch):
    sizes = [14000000, 7000000, 100000, 100000]
    manifests = []
    for index, size in enumerate(sizes):
        ast = tmp_path / ("module_" + str(index) + ".json")
        with ast.open("wb") as stream:
            stream.truncate(size)
        manifest = tmp_path / (str(index) + ".manifest")
        manifest.write_text("\n".join([
            "pcc.py_frontend.codegen_worker.v4", "result", "out", "exports", "",
            str(tmp_path), "", "", "", "", "1", str(index),
        ]) + "\n")
        manifests.append(str(manifest))
    calls = []
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "auto")
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", "8589934592")
    monkeypatch.setattr(scheduler, "parallel_cpu_budget", lambda: 12)
    monkeypatch.setattr(
        scheduler, "run_weighted_worker_processes",
        lambda commands, reservations, width, budget: calls.append(
            (commands, reservations, width, budget)
        ),
    )
    scheduler.run_frontend_commands(["a", "b", "c", "d"], manifests, 2, 2)
    assert calls == [(
        ["a", "b", "c", "d"],
        [scheduler.indexed_frontend_floor_bytes(size) for size in sizes],
        12,
        7 * 1073741824,
    )]


def test_pco_admission_executes_natively_under_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_py_runtime_archive,
):
    import inspect
    import os
    import subprocess

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    sizes = [0, 129227, 1000000, 6851869, 11263590, 14911544, 60000000]
    cases = []
    for budget_gib in (1, 6, 8):
        for cpus in (1, 12, 64):
            budget = budget_gib * 1073741824
            for gc_backend in range(5):
                cases.append((budget, cpus, gc_backend, scheduler.pco_groups(sizes, budget, cpus, gc_backend)))
    source = tmp_path / "pco_admission.py"
    constants = "\n".join(
        name + " = " + repr(getattr(scheduler, name))
        for name in ("_GIB", "_DRIVER_RESERVE", "_PCO_BASE", "_PCO_PER_SIDECAR_MB",
                     "_PCO_LEGACY_BASE", "_PCO_LEGACY_PER_SIDECAR_MB", "_PCO_CAP", "_MAX_WIDTH")
    )
    source.write_text(constants + "\n\n" + "\n\n".join(
        inspect.getsource(function) for function in (
            scheduler.indexed_pco_floor_bytes, scheduler._admission_groups, scheduler.pco_groups,
        )
    ) + "\nsizes = " + repr(sizes) + "\ncases = " + repr(cases) + '''
def main():
    for budget, cpus, gc_backend, expected in cases:
        assert pco_groups(sizes, budget, cpus, gc_backend) == expected
    print("pco-admission-ok")
main()
''')
    binary = tmp_path / "pco_admission"
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off",
                            runtime_archive=str(pcc_py_runtime_archive))
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout.strip() == "pco-admission-ok"
