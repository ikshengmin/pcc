"""A declined admission is not decided again until one of its inputs changes.

The resource pool polls its workers every 10 ms. While a pending task does
not fit, each poll used to sort every pending estimate again and append a
"backoff" line to the admission trace: about 120 s of CPU in a Stage1
coordinator. The decision reads the ready tasks, the active reservations,
the available bytes, the observations and whether a guard state exists;
while none of them changes it would decline again, so the pool waits.
"""
from __future__ import annotations

from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy
from tests.python.test_worker_resource_plan import MIB, _spec_lifetime_fixture


def test_pool_does_not_redecide_an_unchanged_declined_admission(tmp_path, monkeypatch):
    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch)
    for item in tasks:
        item["estimate_bytes"] = 300 * MIB
    polls = {"count": 0}

    def poll(pid):
        # The first worker runs for 30 polls (about 0.3 s); the second ends.
        if pid == 100 and polls["count"] < 30:
            polls["count"] += 1
            return pool._WORKER_RUNNING
        return 0

    decisions = []
    original_choose = policy.choose_task

    def counting_choose(*args, **kwargs):
        decisions.append(args[0])
        return original_choose(*args, **kwargs)

    monkeypatch.setattr(pool, "_poll_resource_worker", poll)
    monkeypatch.setattr(policy, "choose_task", counting_choose)
    trace = tmp_path / "admission.tsv"
    observations = []
    pool.run_resource_worker_processes(
        commands, tasks, 2, 512 * MIB, observations=observations, trace_path=str(trace),
    )
    rows = [line.split("\t")[0] for line in trace.read_text().splitlines()]
    assert [row for row in rows if row != "backoff"] == ["start", "retire", "start", "retire"]
    # One decision admits the first task and one declines the second while
    # the first runs; the next is made after it retires. Each poll used to
    # decide and log again (30 backoff lines here).
    assert polls["count"] == 30
    assert rows.count("backoff") == 1, rows
    assert len(decisions) == 3, decisions
    assert len(observations) == 2
