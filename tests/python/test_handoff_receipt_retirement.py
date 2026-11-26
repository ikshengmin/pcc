"""Real pool retirement with sealed handoffs and owned receipt companions.

Worker processes and the coordinator RSS sample are simulated; commands and
initial task order are fixed. Pool admission, report reading, sealed handoff
retirement and receipt ownership checks are real. Synthetic bytes prove ordering, not
compiler output, native execution, bootstrap qualification or performance.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import pipeline_indexed_handoff as handoff
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy
from tests.python import test_pipeline_indexed_handoff as handoff_fixtures
from tests.python.process_timeout import run_process_group_timeout


MIB = 1024 * 1024
RECEIPTS_MODULE = "pcc.backend.indexed_input_receipts"


@pytest.fixture
def sealed_case():
    case = handoff_fixtures.HandoffContractTests(methodName="runTest")
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def _scenario(case, monkeypatch, *, failure="", companion="", dump=True):
    events, tokens, pids = [], {}, {}
    companion_path = Path(str(case.sidecar) + ".receipt.json")
    owner_path = Path(str(case.sidecar) + ".receipt.owner")
    dump_path = case.root / "dump"
    saved_companions = {}

    class Observations(list):
        def append(self, value):
            events.append(("observation", len(self)))
            super().append(value)

    observations = Observations()
    tasks = [
        {"class": "frontend", "inputs": [1], "estimate_bytes": 0,
         "report_path": str(case.root / "frontend.rss"), "restartable": True,
         "handoff_slot": 0},
        dict(case.task, report_path=str(case.root / "backend.rss"),
             restartable=True, handoff_slot=0),
        {"class": "successor", "inputs": [1], "estimate_bytes": 0,
         "report_path": str(case.root / "successor.rss"), "restartable": True,
         "depends_on": 1},
    ]
    # Neither of these unvalidated fields may supply diagnostic cleanup's path.
    decoy = case.root / "unrelated.pidx"
    decoy.write_bytes(b"not owned by the sealed request")
    tasks[1]["sidecar_path"] = str(decoy)
    tasks[1]["input_path"] = str(decoy)
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: MIB)
    monkeypatch.setattr(pool, "_command_spec", lambda command: (["mock-worker"], []))
    monkeypatch.setattr(pool.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(policy, "resource_task_order", lambda *args, **kwargs: ([0, 1, 2], []))

    receipts = context = None
    if dump:
        from pcc.backend import indexed_input_receipts as receipts

        dump_path.mkdir()
        source = case.root / "sample.py"
        source.write_text("value = 42\n")
        monkeypatch.setenv("PCC_DEBUG_SELF_IR_DUMP_DIR", str(dump_path))
        monkeypatch.setenv("PCC_DEBUG_SELF_IR_INVOCATION", "1" * 32)
        invocation = receipts.begin_invocation([str(source)], ["sample"])
        assert invocation is not None
        receipts.attach_manifest(str(case.manifest), invocation, [0])
        context = receipts.worker_context(str(case.manifest), 0, "sample", str(source))
        assert context is not None
        original_companion_retire = receipts.retire_sidecar_request

        def companion_retire(path):
            assert path == str(case.sidecar), "use only the sealed provider return"
            assert not case.sidecar.exists() and not case.seal.exists()
            assert case.output.is_file() and case.receipt.is_file()
            events.append(("companion", path))
            original_companion_retire(path)

        monkeypatch.setattr(receipts, "retire_sidecar_request", companion_retire)
    else:
        monkeypatch.delenv("PCC_DEBUG_SELF_IR_DUMP_DIR", raising=False)
        monkeypatch.delenv("PCC_DEBUG_SELF_IR_INVOCATION", raising=False)

    def retain_companions():
        for path in (companion_path, owner_path):
            if path.exists():
                saved_companions[path] = path.read_bytes()

    def publish_companion():
        if companion == "collision":
            companion_path.write_bytes(b"foreign existing companion")
        receipts.publish_sidecar_request(str(case.sidecar), context, ["mem2reg"])
        if companion == "poison":
            (dump_path / "indexed-capture-failed").write_bytes(b"earlier failure")
        elif companion == "foreign_context":
            request = json.loads(companion_path.read_text())
            request["context"]["invocation"] = "2" * 32
            companion_path.write_text(json.dumps(request))
            owner = json.loads(owner_path.read_text())
            owner["companion_sha256"] = hashlib.sha256(companion_path.read_bytes()).hexdigest()
            owner_path.write_text(json.dumps(owner))
        elif companion in ("wrong_owner", "wrong_hash"):
            owner = json.loads(owner_path.read_text())
            owner["invocation" if companion == "wrong_owner" else "companion_sha256"] = (
                "2" * 32 if companion == "wrong_owner" else "0" * 64
            )
            owner_path.write_text(json.dumps(owner))
        elif companion == "missing_owner":
            owner_path.unlink()
        retain_companions()

    def report(index, *, phase="complete", token=None):
        Path(tasks[index]["report_path"]).write_text("\n".join([
            policy.RESOURCE_REPORT_SCHEMA, str(7000 + index), phase,
            str(MIB), str(MIB), tokens[index] if token is None else token,
        ]) + "\n")

    def start(specs, index):
        vector = dict(item.split("=", 1) for item in specs[index][1])
        tokens[index] = vector[policy.RESOURCE_TOKEN_ENV]
        pid = 7000 + index
        pids[pid] = index
        events.append(("start", index))
        monkeypatch.setenv(handoff.RESOURCE_TOKEN_ENV, tokens[index])
        if index == 0:
            case.publish()
            if dump:
                publish_companion()
        elif index == 1:
            assert case.sidecar.is_file() and case.seal.is_file()
            monkeypatch.setenv(handoff.ENV_SEAL, vector[handoff.ENV_SEAL])
            case.output.write_bytes(b"synthetic published packed object")
            handoff.publish_handoff_result(str(case.request))
            if failure == "token":
                case.rewrite(case.receipt, {"backend_token": "f" * 64})
            elif failure == "missing_receipt":
                case.receipt.unlink()
            elif failure == "seal":
                case.rewrite(case.seal, {"frontend_token": "f" * 64})
            elif failure == "output":
                case.output.write_bytes(b"changed after publication")
        else:
            assert not case.sidecar.exists() and not case.seal.exists()
            assert ("handoff-return", str(case.sidecar)) in events
            if dump:
                assert ("companion", str(case.sidecar)) in events
        report(index)
        return pid

    def poll(pid):
        index = pids[pid]
        events.append(("poll", index))
        return 7 if failure == "exit" and index == 1 else 0

    def reap(pid):
        index = pids[pid]
        events.append(("reap", index))
        if index == 1:
            # Mutate only after the first report read. The real post-exit
            # reader must reject these rather than reuse its pre-poll result.
            if failure == "missing_report":
                Path(tasks[index]["report_path"]).unlink()
            elif failure == "stale_report":
                report(index, token="0" * 64)
            elif failure == "live_report":
                report(index, phase="allocated")

    original_read = policy.read_worker_resource

    def read(path, pid, token=""):
        index = pids[pid]
        assert token == tokens[index]
        result = original_read(path, pid, token)
        events.append(("report", index, result))
        return result

    original_retire = handoff.retire_handoff_task

    def retire(task, token):
        assert events[-1] == ("report", 1, ("complete", MIB, MIB))
        assert ("reap", 1) in events
        events.append(("handoff-enter", 1))
        result = original_retire(task, token)
        events.append(("handoff-return", result))
        return result

    if failure in ("sidecar_unlink", "seal_unlink"):
        original_unlink = os.unlink
        refused_path = str(case.sidecar if failure == "sidecar_unlink" else case.seal)

        def unlink(path, *args, **kwargs):
            if str(path) == refused_path:
                raise OSError("sealed input unlink refused")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(handoff.os, "unlink", unlink)
    monkeypatch.setattr(pool, "_start_resource_worker", start)
    monkeypatch.setattr(pool, "_poll_resource_worker", poll)
    monkeypatch.setattr(pool, "_retire_resource_worker", reap)
    monkeypatch.setattr(pool, "_stop_resource_worker", lambda pid: events.append(("stop", pids[pid])))
    monkeypatch.setattr(policy, "read_worker_resource", read)
    monkeypatch.setattr(handoff, "retire_handoff_task", retire)
    return {
        "tasks": tasks, "events": events, "observations": observations,
        "companion": companion_path, "owner": owner_path, "dump": dump_path,
        "saved_companions": saved_companions, "decoy": decoy,
    }


def _run(state):
    pool.run_resource_worker_processes(
        ["mock"] * len(state["tasks"]), state["tasks"], 1, 512 * MIB,
        observations=state["observations"],
    )


def test_pool_retires_real_companions_after_sealed_input_before_successor(sealed_case, monkeypatch):
    state = _scenario(sealed_case, monkeypatch)
    _run(state)
    events = state["events"]
    retired = events.index(("handoff-return", str(sealed_case.sidecar)))
    companion = events.index(("companion", str(sealed_case.sidecar)))
    assert events.index(("reap", 1)) < retired < companion
    assert companion < events.index(("observation", 1)) < events.index(("start", 2))
    assert not state["companion"].exists() and not state["owner"].exists()
    assert not (state["dump"] / "indexed-capture-failed").exists()
    assert sealed_case.request.is_file() and sealed_case.receipt.is_file() and sealed_case.output.is_file()
    assert state["decoy"].read_bytes() == b"not owned by the sealed request"
    assert len(state["observations"]) == 3


@pytest.mark.parametrize("failure", (
    "exit", "missing_report", "stale_report", "live_report", "token",
    "missing_receipt", "seal", "output", "sidecar_unlink", "seal_unlink",
))
def test_pool_failure_never_retires_companions_or_releases_successor(sealed_case, monkeypatch, failure):
    state = _scenario(sealed_case, monkeypatch, failure=failure)
    if failure == "exit":
        error = subprocess.CalledProcessError
    elif failure in ("missing_report", "stale_report", "live_report"):
        error = policy.WorkerMemoryError
    elif failure == "missing_receipt":
        # Missing required input is a sealed handoff validation failure,
        # unlike an OS error retiring an already validated input.
        error = handoff.HandoffError
    elif failure in ("sidecar_unlink", "seal_unlink"):
        error = OSError
    else:
        error = ValueError
    with pytest.raises(error) as raised:
        _run(state)
    if failure == "missing_receipt":
        assert str(raised.value) == "indexed handoff: not a regular file: " + str(sealed_case.receipt)
        assert ("handoff-enter", 1) in state["events"]
        assert not any(event[0] == "handoff-return" for event in state["events"])
        assert sealed_case.request.is_file() and sealed_case.output.is_file()
        assert not sealed_case.receipt.exists()
    assert not any(event[0] == "companion" for event in state["events"])
    assert ("start", 2) not in state["events"]
    assert len(state["observations"]) == 1
    for path, raw in state["saved_companions"].items():
        assert path.read_bytes() == raw
    assert sealed_case.seal.is_file()
    assert sealed_case.sidecar.exists() == (failure != "seal_unlink")
    assert not (state["dump"] / "indexed-capture-failed").exists()
    assert state["decoy"].read_bytes() == b"not owned by the sealed request"


@pytest.mark.parametrize("companion", (
    "poison", "foreign_context", "wrong_owner", "wrong_hash", "missing_owner", "collision",
))
def test_pool_preserves_unowned_or_poisoned_companions(sealed_case, monkeypatch, companion):
    state = _scenario(sealed_case, monkeypatch, companion=companion)
    _run(state)
    assert ("companion", str(sealed_case.sidecar)) in state["events"]
    for path, raw in state["saved_companions"].items():
        assert path.read_bytes() == raw
    assert state["owner"].exists() == (companion not in ("missing_owner", "collision"))
    assert (state["dump"] / "indexed-capture-failed").exists() == (companion != "missing_owner")
    assert not sealed_case.sidecar.exists() and not sealed_case.seal.exists()
    assert sealed_case.output.is_file() and sealed_case.receipt.is_file()
    assert len(state["observations"]) == 3, "optional diagnostics cannot change compiler success"


def _fresh_no_dump_probe(whitespace):
    assert RECEIPTS_MODULE not in sys.modules
    case = handoff_fixtures.HandoffContractTests(methodName="runTest")
    case.setUp()
    try:
        with pytest.MonkeyPatch.context() as monkeypatch:
            state = _scenario(case, monkeypatch, dump=False)
            if whitespace:
                monkeypatch.setenv("PCC_DEBUG_SELF_IR_DUMP_DIR", " \t ")
            _run(state)
            assert len(state["observations"]) == 3
            assert not any(event[0] == "companion" for event in state["events"])
            assert RECEIPTS_MODULE not in sys.modules
    finally:
        case.doCleanups()


@pytest.mark.parametrize("whitespace", (False, True))
def test_fresh_no_dump_pool_never_imports_receipt_module(monkeypatch, whitespace):
    monkeypatch.delenv("PCC_DEBUG_SELF_IR_DUMP_DIR", raising=False)
    monkeypatch.delenv("PCC_DEBUG_SELF_IR_INVOCATION", raising=False)
    script = (
        "from tests.python.test_handoff_receipt_retirement import _fresh_no_dump_probe\n"
        "_fresh_no_dump_probe(" + repr(whitespace) + ")\n"
        "print('HANDOFF_NO_DUMP_IMPORT_OK')\n"
    )
    result = run_process_group_timeout(
        [sys.executable, "-B", "-c", script], cwd=str(Path(__file__).resolve().parents[2]),
        env=dict(os.environ), timeout=30,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "HANDOFF_NO_DUMP_IMPORT_OK\n", "")
