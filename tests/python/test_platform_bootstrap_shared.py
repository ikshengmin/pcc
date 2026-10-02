"""Qualification scheduling and immutable common-input contracts."""

import json
from pathlib import Path
import threading

import pytest

from scripts import bootstrap_platform as platform


def _fake_platform(tmp_path, monkeypatch):
    from pcc.frontends.python import pipeline_targets

    monkeypatch.setattr(platform, "ROOT", tmp_path)
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(platform, "source_identity", lambda _dirty=False: {"commit": "frozen", "source_sha256": "a" * 64, "clean_commit": True})
    monkeypatch.setattr(platform, "build_configuration", lambda target: {"target": target, "threads": False})
    monkeypatch.setattr(platform, "dependency_receipt", lambda _path: {"format": "ELF"})
    events = []
    lock = threading.Lock()

    def run(command, *, cwd, env, log_path, timeout, rss_limit, native=False, cancel=None):
        with lock:
            events.append((list(command), dict(env), rss_limit, native))
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if "pcc.frontends.python.owned_runtime_build" in command:
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"immutable runtime")
            Path(str(output) + ".provenance.json").write_text('{}')
        elif "-o" in command:
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(b"\x7fELF immutable compiler")
        elif log_path.name.startswith("canary"):
            log_path.write_text(env["PCC_GC_BACKEND"] + "\n" + str(2 ** 80 + 42) + "\n")
        return {"seconds": 1, "peak_tree_rss": 10, "command": list(command), "log": str(log_path)}

    monkeypatch.setattr(platform, "run", run)
    return events


def test_all_gc_chains_build_runtime_and_stage1_once_and_verify_each_gc(tmp_path, monkeypatch):
    events = _fake_platform(tmp_path, monkeypatch)
    receipt = platform.run_matrix(("0", "1", "2", "3", "4"), tmp_path / "matrix")
    runtime = [event for event in events if "pcc.frontends.python.owned_runtime_build" in event[0]]
    host_stage = [event for event in events if "pcc" in event[0] and "-m" in event[0] and "--ir-scaffold=on" in event[0]]
    assert len(runtime) == len(host_stage) == 1
    assert receipt["parallel_chains"] * receipt["jobs_per_chain"] <= 4
    assert receipt["qualified"]
    for chain in receipt["chains"]:
        assert [stage["stage"] for stage in chain["stages"]] == [1, 2, 3]
        assert chain["stages"][0]["reused"]
        assert chain["stages"][0]["sha256"] == receipt["shared"]["stages"][0]["sha256"]
        assert chain["worker_jobs"] * receipt["parallel_chains"] <= 4
        assert chain["tree_rss_limit"] * receipt["parallel_chains"] <= receipt["aggregate_rss_limit"]
        assert chain["stages"][0]["canary"]["run"]["command"]
        log = Path(chain["stages"][0]["canary"]["run"]["log"])
        assert log.read_text().splitlines()[0] == chain["gc"]
    saved = json.loads((tmp_path / "matrix" / "matrix-receipt.json").read_text())
    assert saved["source"] == receipt["source"]


def test_separate_gc_gates_reuse_one_shared_preparation(tmp_path, monkeypatch):
    events = _fake_platform(tmp_path, monkeypatch)
    for backend in ("0", "1"):
        assert platform.run_from_shared(backend, tmp_path / ("gc" + backend))["qualified"]
    assert sum("pcc.frontends.python.owned_runtime_build" in event[0] for event in events) == 1


def test_same_integration_run_peers_wait_and_reuse_the_common_build(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    events = _fake_platform(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()
    original = platform.run_chain

    def run_chain(*args, **kwargs):
        if kwargs.get("shared") is not None and args[0] == "0":
            started.set()
            assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(platform, "run_chain", run_chain)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(platform.run_from_shared, "0", tmp_path / "gc0", run_id="same-session")
        assert started.wait(3)
        second = pool.submit(platform.run_from_shared, "1", tmp_path / "gc1", run_id="same-session")
        try:
            assert not second.done()
        finally:
            release.set()
        assert first.result()["qualified"] and second.result()["qualified"]
    assert sum("pcc.frontends.python.owned_runtime_build" in event[0] for event in events) == 1


def test_peer_queue_preserves_external_build_contention_rejection(tmp_path, monkeypatch):
    _fake_platform(tmp_path, monkeypatch)
    with platform.performance_lock():
        with pytest.raises(OSError):
            platform.run_from_shared("0", tmp_path / "gc0", run_id="a-different-session")
    assert not (tmp_path / "gc0" / "receipt.json").exists()


def test_shared_input_drift_is_rejected_before_native_stage(tmp_path, monkeypatch):
    events = _fake_platform(tmp_path, monkeypatch)
    shared = platform.run_chain("0", tmp_path / "shared", stage_limit=1)
    Path(shared["compiler_path"]).write_bytes(b"tampered")
    count = len(events)
    with pytest.raises(RuntimeError, match="shared Stage1 identity changed"):
        platform.run_chain("1", tmp_path / "gc1", shared=shared)
    assert len(events) == count


def test_pe_fixed_point_does_not_ignore_metadata_bytes():
    import struct

    image = bytearray(512)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 64)
    image[64:68] = b"PE\0\0"
    other = bytearray(image)
    other[64 + 8] = 1
    assert platform.normalized_image(bytes(image)) != platform.normalized_image(bytes(other))
