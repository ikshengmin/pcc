"""Compilation-directory ownership of default worker calibration history."""

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as plan


@pytest.fixture
def fake_pool(monkeypatch, tmp_path):
    history = []
    monkeypatch.setattr(pool, "_RESOURCE_OBSERVATIONS", history)
    monkeypatch.setattr(pool, "_RESOURCE_OBSERVATION_OWNERS", {})
    monkeypatch.setattr(pool, "_command_spec", lambda command: ([command], []))
    # Model a measured 1 MiB owner; zero means RSS is unavailable.
    owner_rss_bytes = 1024**2
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: owner_rss_bytes)
    monkeypatch.setattr(plan, "read_tree_state", lambda *args, **kwargs: None)
    monkeypatch.setenv(plan.TREE_STATE_ENV, "")
    monkeypatch.setattr(pool, "_resource_event", lambda *args: None)
    monkeypatch.setattr(pool, "_resource_diagnostic", lambda *args: None)
    monkeypatch.setattr(plan, "read_worker_resource", lambda *args: ("complete", 1, 1024))
    monkeypatch.setattr(pool, "_poll_resource_worker", lambda pid: 0)
    monkeypatch.setattr(pool, "_retire_resource_worker", lambda pid: None)
    monkeypatch.setattr(pool, "_stop_resource_worker", lambda pid: None)
    launches = []

    def start(specs, index):
        launches.append((specs, index))
        return 1000 + len(launches)

    monkeypatch.setattr(pool, "_start_resource_worker", start)

    def make_task(owner, execution_class):
        return {"class": execution_class, "inputs": [1], "estimate_bytes": 1,
                "report_path": str(tmp_path / (str(len(launches)) + ".rss")),
                "observation_owner": owner}

    def run(owner, execution_class, observations=None):
        task = make_task(owner, execution_class)
        pool.run_resource_worker_processes(
            ["never-executed"], [task], 1, 512 * 1024**2,
            observations=observations,
        )
        return task

    return history, run, make_task


@pytest.mark.parametrize("owner_rss", [0, -1], ids=["zero", "negative"])
def test_scope_rejects_nonpositive_owner_rss_before_launch(fake_pool, monkeypatch, owner_rss):
    global_history, run, _make_task = fake_pool
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: owner_rss)

    def unexpected_start(*args):
        pytest.fail("worker launched without measured owner RSS")

    monkeypatch.setattr(pool, "_start_resource_worker", unexpected_start)
    with pool.ResourceObservationScope("owner") as scope:
        with pytest.raises(plan.WorkerMemoryError, match="positive tree budget and measured owner RSS"):
            run("owner", "class")
        assert scope.history == []
        assert global_history == []
    assert pool._RESOURCE_OBSERVATION_OWNERS == {}


def test_split_pools_keep_calibration_until_scope_exits(fake_pool):
    global_history, run, _make_task = fake_pool
    with pool.ResourceObservationScope("owner") as scope:
        task = run("owner", "opaque-class")
        assert len(scope.history) == 1
        calibrated = plan.estimated_task_bytes(task, scope.history)
        assert calibrated > task["estimate_bytes"]
        identity = scope.history
        run("owner", "opaque-class")
        assert scope.history is identity
        assert len(scope.history) == 2
        assert plan.estimated_task_bytes(task, scope.history) == calibrated
        assert global_history == []
    assert pool._RESOURCE_OBSERVATION_OWNERS == {}
    # Exiting removes the registry owner, not a caller's captured list.
    assert scope.history is identity and len(identity) == 2
    with pool.ResourceObservationScope("new-owner") as fresh:
        assert fresh.history == []


def test_nested_interleaved_owners_never_remove_each_others_rows(fake_pool):
    global_history, run, _make_task = fake_pool
    with pool.ResourceObservationScope("outer") as outer:
        run("outer", "outer-class")
        with pool.ResourceObservationScope("inner") as inner:
            run("inner", "inner-class")
            run("outer", "outer-class")
            run("unregistered", "legacy-class")
            assert len(outer.history) == 2
            assert len(inner.history) == 1
        assert pool._RESOURCE_OBSERVATION_OWNERS == {"outer": outer}
        assert len(inner.history) == 1
        run("outer", "outer-class")
        assert len(outer.history) == 3
        assert [row[0] for row in global_history] == ["legacy-class"]
    assert [row[0] for row in global_history] == ["legacy-class"]


def test_explicit_histories_are_unchanged_by_scope_routing(fake_pool):
    global_history, run, _make_task = fake_pool
    explicit = []
    with pool.ResourceObservationScope("owner") as scope:
        run("owner", "explicit-class", explicit)
        run("owner", "explicit-global", global_history)
        assert scope.history == []
        assert [row[0] for row in explicit] == ["explicit-class"]
    assert [row[0] for row in explicit] == ["explicit-class"]
    assert [row[0] for row in global_history] == ["explicit-global"]


@pytest.mark.parametrize("owners", [
    ("left", "right"), ("left", "missing"), ("missing", "missing"),
])
def test_mixed_and_unregistered_pools_keep_global_default(fake_pool, owners):
    global_history, _run, make_task = fake_pool
    with pool.ResourceObservationScope("left") as left:
        with pool.ResourceObservationScope("right") as right:
            tasks = [make_task(owner, "class-" + owner) for owner in owners]
            assert pool._default_resource_observations(tasks) is global_history
            pool.run_resource_worker_processes(
                ["never-executed"] * len(tasks), tasks, 1, 512 * 1024**2,
            )
            assert len(global_history) == 2
            assert left.history == right.history == []
    assert len(global_history) == 2


def test_scope_failure_cleanup_preserves_other_histories(fake_pool):
    global_history, run, _make_task = fake_pool
    with pool.ResourceObservationScope("survivor") as survivor:
        run("survivor", "survivor-class")
        for index in range(3):
            owner = "failed" + str(index)
            with pytest.raises(RuntimeError, match="later phase"):
                with pool.ResourceObservationScope(owner) as failed:
                    run(owner, "class" + str(index))
                    raise RuntimeError("later phase")
            assert owner not in pool._RESOURCE_OBSERVATION_OWNERS
            assert len(failed.history) == 1
            assert len(survivor.history) == 1
        assert global_history == []
    assert pool._RESOURCE_OBSERVATION_OWNERS == {}


def test_duplicate_owner_cannot_steal_outer_scope(fake_pool):
    _global_history, run, _make_task = fake_pool
    with pool.ResourceObservationScope("owner") as outer:
        run("owner", "class")
        with pytest.raises(ValueError, match="already has an owner"):
            with pool.ResourceObservationScope("owner"):
                pytest.fail("duplicate scope entered")
        assert pool._RESOURCE_OBSERVATION_OWNERS["owner"] is outer
        assert len(outer.history) == 1
    assert pool._RESOURCE_OBSERVATION_OWNERS == {}


def test_custom_and_nonoptin_tasks_keep_default_without_callbacks(fake_pool):
    global_history, _run, make_task = fake_pool

    class Task(dict):
        def get(self, key, default=None):
            raise AssertionError("custom task accessed by scope routing")

    class Inventory(list):
        def __iter__(self):
            raise AssertionError("custom inventory iterated by scope routing")

    with pool.ResourceObservationScope("owner") as scope:
        assert pool._default_resource_observations([Task()]) is global_history
        assert pool._default_resource_observations(Inventory()) is global_history
        assert pool._default_resource_observations([{}]) is global_history
        assert pool._default_resource_observations([]) is global_history
        task = make_task("owner", "class")
        assert pool._default_resource_observations([task]) is scope.history
        assert pool._default_resource_observations((task,)) is scope.history


def test_explicit_malformed_history_keeps_original_estimator_error(fake_pool):
    _global_history, run, _make_task = fake_pool
    malformed = [("foreign",)]
    with pool.ResourceObservationScope("owner") as scope:
        with pytest.raises(plan.WorkerMemoryError, match="observation shape"):
            run("owner", "class", malformed)
        assert scope.history == []
        assert malformed == [("foreign",)]


def test_task_producers_supply_exact_directory_owners(tmp_path):
    import shlex
    import sys

    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    manifest = tmp_path / "export.manifest"
    workers.write_worker_manifest(
        str(manifest), str(tmp_path / "result"), str(tmp_path), "", "",
        [str(source)], ["module"], [0], entry_module="module", sibling_inits=[],
        ir_scaffold_mode="on", verbose=False, job_kind="export",
    )
    command = shlex.join([
        sys.executable, "--pcc-python-multi-codegen-worker", str(manifest),
    ])
    task = workers.resource_tasks_for_commands([command])[0]
    assert task["observation_owner"] == str(tmp_path)

    preload = tmp_path / "preload_deltas"
    preload.mkdir()
    exports = preload / "exports.json"
    roots = preload / "roots.txt"
    exports.write_text("{}", encoding="utf-8")
    roots.write_text("module.Class\n", encoding="utf-8")
    command = shlex.join([
        sys.executable, "--pcc-preload-delta-worker", str(exports), str(roots),
        str(preload / "output.json"),
    ])
    task = workers.resource_tasks_for_commands([command])[0]
    assert task["observation_owner"] == str(preload)
