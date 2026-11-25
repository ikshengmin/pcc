"""Independent compiler owners compete inside one unchanged process-tree guard."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python import worker_resource_plan as policy

ROOT = Path(__file__).resolve().parents[2]
CAP = 1024 ** 3
ADMISSION_STATE = "PCC_WORKER_POOL_ADMISSION_STATE"


@pytest.mark.parametrize("failed_first", [False, True])
def test_sibling_compiler_pools_share_one_guard_admission(tmp_path, failed_first):
    # Two genuine compiler pool driver processes, not threads or a simulated
    # state table. Each launches an independently sampled real worker.
    if failed_first:
        (tmp_path / "fail-A").touch()
    worker = tmp_path / "worker.py"
    worker.write_text('''import os, shlex, sys, time
from pathlib import Path
from pcc.frontends.python import pipeline_frontend_workers as workers
root, label = Path(sys.argv[1]), sys.argv[2]
def event(kind):
    with (root / "timeline").open("a") as out:
        out.write(kind + " " + label + "\\n")
event("start")
(root / ("started-" + label)).touch()
(root / ("inherited-" + label)).write_text(os.environ.get("PCC_WORKER_POOL_ADMISSION_STATE", ""))
if label == "A":
    until = time.monotonic() + 12
    while not (root / "release-A").exists():
        assert time.monotonic() < until
        time.sleep(.01)
if label == "B":
    # A real descendant pool: the already admitted B subprocess must
    # retain the guard but not reacquire its parent's advisory lease.
    from pcc.frontends.python.worker_process_pool import run_resource_worker_processes
    commands = [shlex.join([sys.executable, "-B", str(root / "worker.py"), str(root), "C"])]
    tasks = [{"class": "nested", "inputs": [1], "estimate_bytes": 0,
              "restartable": True, "report_path": str(root / "rss-C")}]
    run_resource_worker_processes(commands, tasks, 1, int(os.environ["PCC_WORKER_TREE_BUDGET_BYTES"]),
                                  observations=[])
event("end")
if label == "A" and (root / "fail-A").exists():
    sys.exit(17)
rss = max(1, workers._coordinator_rss_bytes())
peak = max(rss, workers._worker_peak_rss_bytes())
Path(os.environ["PCC_WORKER_RESOURCE_REPORT"]).write_text(
    "pcc.worker-resource.v2\\n" + str(os.getpid()) + "\\ncomplete\\n" + str(rss)
    + "\\n" + str(peak) + "\\n" + os.environ["PCC_WORKER_RESOURCE_TOKEN"] + "\\n")
''', encoding="utf-8")
    owner = tmp_path / "owner.py"
    owner.write_text('''import os, shlex, subprocess, sys
from pathlib import Path
from pcc.frontends.python.worker_process_pool import run_resource_worker_processes
root, label = Path(sys.argv[1]), sys.argv[2]
(root / ("ready-" + label)).touch()
commands = [shlex.join([sys.executable, "-B", str(root / "worker.py"), str(root), label])]
tasks = [{"class": "peer-" + label, "inputs": [1], "estimate_bytes": 0,
          "restartable": True, "report_path": str(root / ("rss-" + label))}]
try:
    run_resource_worker_processes(commands, tasks, 1, int(os.environ["PCC_WORKER_TREE_BUDGET_BYTES"]),
                                  observations=[])
except subprocess.CalledProcessError as error:
    assert label == "A" and (root / "fail-A").exists() and error.returncode == 17
    (root / "failure-observed").write_text(str(error.returncode))
''', encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text('''import subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
children = []
def start(label):
    child = subprocess.Popen([sys.executable, "-B", str(root / "owner.py"), str(root), label])
    children.append(child)
    return child
def wait(name, seconds=8):
    until = time.monotonic() + seconds
    while not (root / name).exists():
        assert time.monotonic() < until, name
        assert all(child.poll() is None for child in children), name
        time.sleep(.01)
try:
    start("A")
    wait("started-A")
    start("B")
    wait("ready-B")
    # A is a real live worker throughout B's first admission attempts.
    # The old implementation launches B here instead of reserving one owner.
    time.sleep(1)
finally:
    (root / "release-A").touch()
    for child in children:
        assert child.wait(timeout=12) == 0
''', encoding="utf-8")
    guard = tmp_path / "guard.json"
    bound_state = str(guard) + ".worker-rss.tsv"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1",
               PCC_NO_AUTO_PCC1="1", PCC_TEST_NO_NATIVE_PROVISIONING="1",
               **{ADMISSION_STATE: bound_state})
    result = subprocess.run([
        sys.executable, "-B", str(ROOT / "scripts/run_process_tree_sample.py"),
        "--result", str(guard), "--samples", str(tmp_path / "samples.tsv"),
        "--stdout", str(tmp_path / "driver.stdout"), "--stderr", str(tmp_path / "driver.stderr"),
        "--cwd", str(ROOT), "--timeout", "90", "--interval", "0.03",
        "--no-performance-lock", "--max-tree-rss-bytes", str(CAP),
        "--", sys.executable, "-B", str(driver), str(tmp_path),
    ], env=env, capture_output=True, text=True, timeout=30)
    receipt = json.loads(guard.read_text(encoding="utf-8"))
    assert result.returncode == 0, (result, (tmp_path / "driver.stderr").read_text())
    assert receipt["status"] == "COMPLETE" and receipt["returncode"] == 0
    assert receipt["timeout_s"] == 90 and receipt["max_tree_rss_bytes"] == CAP
    assert receipt["peak_tree_rss_bytes"] <= CAP and not receipt.get("post_exit_cleanup_pids")
    assert (tmp_path / "timeline").read_text().splitlines() == [
        "start A", "end A", "start B", "start C", "end C", "end B",
    ]
    # These are descendants of already admitted owners, not independent
    # contenders; a nested pool must not deadlock on its own parent's lease.
    assert all((tmp_path / ("inherited-" + name)).read_text() == "" for name in ("A", "B", "C"))
    assert (tmp_path / "failure-observed").exists() is failed_first
    if failed_first:
        assert (tmp_path / "failure-observed").read_text() == "17"


def _bind_guard(monkeypatch, path, *, deadline):
    monkeypatch.setenv(policy.TREE_ADMISSION_STATE_ENV, str(path))
    monkeypatch.setenv(policy.TREE_STATE_ENV, str(path))
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(CAP))
    monkeypatch.setenv(policy.TREE_RECOVERY_ENV,
                       "pcc.worker-tree-recovery.v1\t0.5,1.0\t1.5\t" + str(deadline))


def test_shared_lease_keeps_guard_identity_and_original_deadline(tmp_path, monkeypatch):
    import time

    first = tmp_path / "first.state"
    other = tmp_path / "other.state"
    _bind_guard(monkeypatch, first, deadline=time.monotonic() + 5)
    assert policy.acquire_shared_pool_slot("", CAP) is None
    assert policy.acquire_shared_pool_slot(str(other), CAP) is None
    stream = policy.acquire_shared_pool_slot(str(first), CAP)
    assert stream is not None
    try:
        # A distinct guard is not a contender even while this owner holds.
        _bind_guard(monkeypatch, other, deadline=time.monotonic() + 5)
        independent = policy.acquire_shared_pool_slot(str(other), CAP)
        assert independent is not None
        independent.close()

        # The waited-on owner cannot extend the supervisor's absolute deadline.
        _bind_guard(monkeypatch, first, deadline=time.monotonic() + .14)
        started = time.monotonic()
        with pytest.raises(policy.WorkerMemoryError, match="original guard deadline"):
            policy.acquire_shared_pool_slot(str(first), CAP)
        assert .12 <= time.monotonic() - started < 2
    finally:
        stream.close()

    _bind_guard(monkeypatch, first, deadline=time.monotonic() + 5)
    next_owner = policy.acquire_shared_pool_slot(str(first), CAP)
    assert next_owner is not None
    next_owner.close()


@pytest.mark.parametrize("fault", ["budget", "deadline", "schema", "expired"])
def test_bound_shared_lease_does_not_accept_mismatched_guard(tmp_path, monkeypatch, fault):
    import time

    path = tmp_path / "bound.state"
    deadline = time.monotonic() + 5
    _bind_guard(monkeypatch, path, deadline=deadline)
    if fault == "budget":
        monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(CAP + 1))
    elif fault == "deadline":
        monkeypatch.setenv(policy.TREE_RECOVERY_ENV, "pcc.worker-tree-recovery.v1\t0.5\t0.5\tnan")
    elif fault == "schema":
        monkeypatch.setenv(policy.TREE_RECOVERY_ENV, "not-the-guard\t0.5\t0.5\t" + str(deadline))
    else:
        _bind_guard(monkeypatch, path, deadline=time.monotonic() - .01)
    with pytest.raises(policy.WorkerMemoryError, match="guard (budget/deadline|deadline)"):
        policy.acquire_shared_pool_slot(str(path), CAP)
    assert not Path(str(path) + ".pool-admission.lock").exists()
