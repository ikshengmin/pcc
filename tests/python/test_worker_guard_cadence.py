"""Pure publication/lifecycle model; no compiler or real process is launched."""

import ast
import os
from pathlib import Path
import re
import shlex
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy


@pytest.fixture(autouse=True)
def _legacy_recovery_environment(monkeypatch):
    # Pure virtual clocks must not inherit a real enclosing guard's deadline.
    # Cases opting in below install their own immutable synthetic envelope.
    monkeypatch.delenv(policy.TREE_RECOVERY_ENV, raising=False)


MIB = 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]


def test_macos_compiler_guards_keep_the_bootstrap_publication_cadence():
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    expected = {
        "macos-pcc0-regressions": [("macos-regressions", "supervisor", "1200", "0.25", [])],
        "macos-pcc0-float-doc-regressions": [
            ("macos-float-doc", "supervisor", "1200", "0.25", ["--profile", "float-doc"]),
        ],
        "macos-pcc0-indexed-split": [
            # The separate host-only run does not use worker admission.
            ("macos-indexed-split", "handoff-xdist-supervisor", "300", "1", ["--handoff-xdist"]),
            ("macos-indexed-split", "supervisor", "1200", "0.25", ["--profile", "indexed-split"]),
        ],
    }

    def assert_commands(text):
        for job, rows in expected.items():
            bodies = re.findall(r"(?ms)^  " + re.escape(job)
                                + r":\n(.*?)(?=^  [A-Za-z0-9_-]+:|\Z)", text)
            assert len(bodies) == 1, job
            # Bound each shell command by its continuation lines, not a blank
            # line or a later job. Unrelated C++ guards have separate contracts.
            commands = re.findall(
                r"(?m)^          (?:env -u LC_ALL )?uv run python "
                r"scripts/run_process_tree_sample\.py \\\n"
                r"((?:            [^\n]*\\\n)*            [^\n]*)",
                bodies[0],
            )
            assert bodies[0].count("scripts/run_process_tree_sample.py") == len(commands) == len(rows)
            for command, (directory, supervisor, timeout, interval, tail) in zip(commands, rows):
                out = "build/" + directory
                prefix = out + "/work/" + supervisor
                # Exact argv rejects missing/duplicate options and child-side
                # --interval lookalikes while retaining every original guard.
                assert shlex.split(command.replace("\\\n", " ")) == [
                    "--result", prefix + "/result.json",
                    "--samples", prefix + "/samples.tsv",
                    "--stdout", prefix + "/target.stdout",
                    "--stderr", prefix + "/target.stderr",
                    "--timeout", timeout, "--interval", interval, "--progress-interval", "30",
                    "--auto-tree-rss-ceiling-bytes", "4294967296",
                    "--min-tree-rss-bytes", "2147483648",
                    "--darwin-preflight-reserve-bytes", "536870912",
                    "--", "uv", "run", "python", "scripts/ci_macos_regression_gate.py",
                    "--out-dir", out, *tail,
                ]

    assert_commands(workflow)
    # Keep rejection controls in this existing node: the host inventory is exact.
    regression_blocks = re.findall(
        r"(?ms)^  macos-pcc0-regressions:\n.*?(?=^  [A-Za-z0-9_-]+:|\Z)", workflow,
    )
    assert len(regression_blocks) == 1
    regression_block = regression_blocks[0]
    for before, after in (
        ("--interval 0.25", "--interval 1"),
        ("--interval 0.25", ""),
        ("--interval 0.25", "--interval 0.25 --interval 1"),
        ("--timeout 1200", "--timeout 1201"),
        ("--auto-tree-rss-ceiling-bytes 4294967296", "--auto-tree-rss-ceiling-bytes 4294967297"),
        ("--out-dir build/macos-regressions", "--out-dir build/macos-regressions --interval 0.25"),
        ("  macos-pcc0-regressions:\n", "  misplaced-macos-pcc0-regressions:\n"),
    ):
        assert before in regression_block
        mutated_block = regression_block.replace(before, after, 1)
        with pytest.raises(AssertionError):
            assert_commands(workflow.replace(regression_block, mutated_block, 1))


@pytest.mark.parametrize("interval,frozen,fails,recovery", [
    pytest.param(1.0, False, True, False, id="legacy-cadence-exhausts-continuous-outage"),
    pytest.param(0.25, False, False, False, id="bootstrap-cadence-recovers-after-retirement"),
    pytest.param(0.25, True, True, False, id="frozen-publication-still-fails-closed"),
    pytest.param(1.0, False, False, True, id="configured-recovery-keeps-reap-barrier"),
    pytest.param(1.0, True, True, True, id="configured-recovery-still-expires"),
])
def test_publication_between_poll_and_retirement_preserves_admission_barrier(
    tmp_path, monkeypatch, interval, frozen, fails, recovery,
):
    # A synchronized sample can be published while the coordinator is polling
    # a child, before it observes/reaps that child's exit. Retirement advances
    # the barrier and correctly rejects that sample. At a one-second sleep
    # cadence, the pre-launch and post-retirement waits can together exhaust
    # the same two-second outage, even though the guard keeps publishing.
    # The 20ms sample work and 31ms publication-to-retirement gap model this
    # ordering, not a claim to reproduce every event of a historical CI run.
    initial = 100.0
    owner = os.getpid()
    budget = 512 * MIB
    tree = tmp_path / "tree.tsv"
    tasks = [
        {"class": "cadence", "inputs": [2 - index], "estimate_bytes": 64 * MIB,
         "report_path": str(tmp_path / ("rss" + str(index))), "depends_on": index - 1}
        for index in range(2)
    ]
    clock = SimpleNamespace(now=initial)
    period = interval + 0.02
    # The configured successor must stay alive across a publication and a
    # subsequent scheduler read. Its old 20ms lifetime ended before the next
    # 1.02s publication, so that case never exercised active recovery.
    # Keep the original legacy cadence cases' timing unchanged.
    successor_lifetime = period + 0.02 if recovery else 0.02
    next_publication = [initial + period]
    live = {}
    starts, reaped, stopped, reads, observations = [], [], [], [], []

    def publish_tree():
        rows = [f"{owner}\t0\t{64 * MIB}"]
        rows.extend(f"{pid}\t{owner}\t{16 * MIB}" for pid in live)
        tree.write_text(policy.TREE_STATE_SCHEMA + "\n" + str(clock.now) + "\n"
                        + str(budget) + "\n" + "\n".join(rows) + "\n")

    def advance(target):
        assert clock.now <= target < initial + 10.0
        while next_publication[0] <= target:
            clock.now = next_publication[0]
            if not frozen:
                publish_tree()
            next_publication[0] += period
        clock.now = target

    def publish_report(item, phase):
        Path(tasks[item["index"]]["report_path"]).write_text(
            policy.RESOURCE_REPORT_SCHEMA + "\n" + str(item["pid"])
            + "\n" + phase + "\n" + str(16 * MIB) + "\n" + str(16 * MIB)
            + "\n" + item["token"] + "\n",
        )

    def start(specs, index):
        # No missing, old-barrier or otherwise incompatible read may launch
        # either task. The actual reader owns every freshness/identity check.
        detail = reads[-1][1]
        assert detail["reason"] == "accepted"
        assert detail["sampled_at"] >= detail["not_before"]
        assert not live
        pid = owner + 100 + index
        item = {"pid": pid, "index": index, "started": clock.now,
                "token": next(value.split("=", 1)[1] for value in specs[index][1]
                              if value.startswith(policy.RESOURCE_TOKEN_ENV + "="))}
        live[pid] = item
        starts.append((index, clock.now))
        publish_report(item, "started")
        return pid

    def poll(pid):
        item = live[pid]
        if item["index"] == 0:
            if clock.now < initial + 1.0 - 1e-8:
                return pool._WORKER_RUNNING
            # The independent guard can publish during a scheduler pause.
            # Keep the child present until its successful exit is observed.
            advance(initial + 1.051269)
        elif clock.now - item["started"] < successor_lifetime:
            return pool._WORKER_RUNNING
        publish_report(item, "complete")
        return 0

    def retire(pid):
        reaped.append(pid)
        live.pop(pid)

    def stop(pid):
        stopped.append(pid)
        live.pop(pid)

    original_reader = policy.read_tree_state

    def read_state(*args, **kwargs):
        detail = kwargs.pop("diagnostic", None)
        if detail is None:
            detail = {}
        result = original_reader(*args, diagnostic=detail, **kwargs)
        reads.append((clock.now, dict(detail), list(args[3])))
        return result

    clock.monotonic = lambda: clock.now
    clock.sleep = lambda seconds: advance(clock.now + seconds)
    monkeypatch.setattr(policy, "time", clock)
    monkeypatch.setattr(pool, "time", clock)
    monkeypatch.setattr(policy, "read_tree_state", read_state)
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: 64 * MIB)
    monkeypatch.setattr(pool, "_start_resource_worker", start)
    monkeypatch.setattr(pool, "_poll_resource_worker", poll)
    monkeypatch.setattr(pool, "_retire_resource_worker", retire)
    monkeypatch.setattr(pool, "_stop_resource_worker", stop)
    monkeypatch.setenv(policy.TREE_STATE_ENV, str(tree))
    if recovery:
        monkeypatch.setenv(policy.TREE_RECOVERY_ENV,
                           "pcc.worker-tree-recovery.v1\t1.0,3.0\t4.0\t120.0")
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "0")
    publish_tree()
    failure = None
    try:
        pool.run_resource_worker_processes(
            ["never-executed-0", "never-executed-1"], tasks, 1, budget,
            observations=observations,
        )
    except policy.WorkerMemoryError as error:
        failure = str(error)
    assert not live and not stopped
    assert any(detail["reason"] == "active_missing" for _, detail, _ in reads)
    assert any(detail["reason"] == "before_barrier" and not active
               for _, detail, active in reads)
    assert policy.STATE_MAX_AGE_SECONDS == 2.0
    if fails:
        prefix = "worker tree RSS state is missing, stale, or incompatible; last_rejected_snapshot="
        assert failure.startswith(prefix)
        detail_text, active_text = failure[len(prefix):].split("; active_tasks=", 1)
        detail = ast.literal_eval(detail_text)
        assert detail["reason"] == ("age" if frozen else "before_barrier")
        assert ast.literal_eval(active_text) == []
        assert [index for index, _ in starts] == [0]
        assert reaped == [owner + 100] and len(observations) == 1
        first_rejected = next(at for at, record, _ in reads if record["reason"] != "accepted")
        allowance = 4.0 if recovery else 2.0
        assert allowance <= clock.now - first_rejected < allowance + 0.03
    else:
        assert failure is None
        assert [index for index, _ in starts] == [0, 1]
        assert reaped == [owner + 100, owner + 101] and len(observations) == 2
        assert starts[1][1] > initial + 1.051269
        assert any(detail["reason"] == "accepted" and active for _, detail, active in reads)
        if recovery:
            successor = [owner + 101]
            rejected_at = next(at for at, detail, active in reads
                               if active == successor and detail["reason"] != "accepted")
            recovered_at, detail = next((at, detail) for at, detail, active in reads
                                        if active == successor and detail["reason"] == "accepted")
            assert starts[1][1] <= detail["sampled_at"] <= recovered_at < rejected_at + 4.0
