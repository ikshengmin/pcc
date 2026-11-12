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

    def run(command, *, cwd, env, log_path, timeout, rss_limit, native=False, cancel=None,
            observe_stage1=False):
        with lock:
            events.append((list(command), dict(env), rss_limit, native, timeout))
        assert observe_stage1 == (Path(log_path).name == "stage1.log" and not native)
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
    # Exercise mocked provisioning only after replacing platform.run.
    # The real guard and the enclosing launcher environment remain unchanged.
    monkeypatch.delenv("PCC_TEST_NO_NATIVE_PROVISIONING", raising=False)
    for backend in ("0", "1"):
        assert platform.run_from_shared(backend, tmp_path / ("gc" + backend))["qualified"]
    assert sum("pcc.frontends.python.owned_runtime_build" in event[0] for event in events) == 1


@pytest.mark.parametrize("stage_limit", (1, 2, 3))
@pytest.mark.parametrize("stage1_timeout", (None, 7200))
def test_stage1_deadline_does_not_extend_runtime_later_stages_or_canaries(
    tmp_path, monkeypatch, stage_limit, stage1_timeout,
):
    events = _fake_platform(tmp_path, monkeypatch)
    receipt = platform.run_matrix(
        ("0", "1", "2", "3", "4"), tmp_path / "matrix",
        stage_limit=stage_limit, timeout=6000, stage1_timeout=stage1_timeout,
    )
    assert receipt["gc_backends"] == ["0", "1", "2", "3", "4"]
    assert receipt["aggregate_rss_limit"] == 17179869184
    assert receipt["parallel_chains"] == 2
    host_builds = canary_runs = 0
    for command, _environment, _rss, native, deadline in events:
        if "--ir-scaffold=on" in command and "-m" in command:
            host_builds += 1
            assert not native
            assert deadline == (6000 if stage1_timeout is None else stage1_timeout)
        elif "-o" in command or "pcc.frontends.python.owned_runtime_build" in command:
            assert deadline == 6000
        else:
            canary_runs += 1
            assert native and deadline == 60
    assert host_builds == 1
    assert canary_runs == 1 + 5 * stage_limit


def test_stage1_deadline_survives_run_chain_lock_recursion(tmp_path, monkeypatch):
    events = _fake_platform(tmp_path, monkeypatch)
    platform.run_chain("0", tmp_path / "chain", stage_limit=1,
                       timeout=6000, stage1_timeout=7200)
    assert [event[4] for event in events] == [6000, 7200, 6000, 60]


def test_same_integration_run_peers_wait_and_reuse_the_common_build(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    events = _fake_platform(tmp_path, monkeypatch)
    modeled_run = platform.run
    # The model is installed first. Keep real subprocess execution forbidden
    # while locally allowing this test's modeled shared preparation only.
    external_calls = []

    def forbidden_execution(*args, **kwargs):
        external_calls.append((args, kwargs))
        raise AssertionError("shared-queue model must not execute a real child")

    for name in ("Popen", "run", "check_output"):
        monkeypatch.setattr(platform.subprocess, name, forbidden_execution)
    monkeypatch.delenv("PCC_TEST_NO_NATIVE_PROVISIONING", raising=False)
    started = threading.Event()
    second_queued = threading.Event()
    release = threading.Event()
    original = platform.run_chain
    original_admission = platform._same_run_admission
    admissions = []
    admission_lock = threading.Lock()

    @contextmanager
    def admission(*args, **kwargs):
        with admission_lock:
            admissions.append((args, kwargs))
            if len(admissions) == 2:
                second_queued.set()
        with original_admission(*args, **kwargs):
            yield

    def run_chain(*args, **kwargs):
        assert platform.run is modeled_run
        if kwargs.get("shared") is not None and args[0] == "0":
            started.set()
            assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(platform, "_same_run_admission", admission)
    monkeypatch.setattr(platform, "run_chain", run_chain)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(platform.run_from_shared, "0", tmp_path / "gc0", run_id="same-session")
        try:
            assert started.wait(3)
            second = pool.submit(platform.run_from_shared, "1", tmp_path / "gc1", run_id="same-session")
            assert second_queued.wait(3)
            assert not second.done()
        finally:
            release.set()
        assert first.result()["qualified"] and second.result()["qualified"]
    assert len(admissions) == 2 and platform.run is modeled_run
    assert sum("pcc.frontends.python.owned_runtime_build" in event[0] for event in events) == 1
    assert sum("--ir-scaffold=on" in event[0] and "-m" in event[0] for event in events) == 1
    assert external_calls == []


def test_same_run_guard_still_rejects_unadmitted_inputs_with_a_mocked_runner(tmp_path, monkeypatch):
    events = _fake_platform(tmp_path, monkeypatch)
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")

    def forbidden_execution(*args, **kwargs):
        raise AssertionError("no-provision guard must reject before real execution")

    monkeypatch.setattr(platform.subprocess, "Popen", forbidden_execution)
    with pytest.raises(RuntimeError, match="automatic rebuild is disabled"):
        platform.run_from_shared("0", tmp_path / "gc0", run_id="same-session")
    assert events == []
    assert not (tmp_path / "shared" / "receipt.json").exists()
    assert not (tmp_path / "gc0" / "receipt.json").exists()


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


@pytest.mark.parametrize("capture", [None, "0", "1"])
@pytest.mark.parametrize("gc_backend", range(5))
def test_stage_environment_enables_capture_for_direct_output(monkeypatch, capture, gc_backend):
    if capture is None:
        monkeypatch.delenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", raising=False)
    else:
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", capture)
    environment = platform._stage_environment(gc_backend)
    assert environment["PCC_DIRECT_INDEXED_KERNEL_EMIT"] == "1"
    assert environment.get("PCC_DIRECT_INDEXED_KERNEL_CAPTURE") == "1"


@pytest.mark.parametrize("target", [
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
])
def test_stage_environment_drives_real_indexed_worker(tmp_path, monkeypatch, target):
    from pcc.frontends.python import pipeline, pipeline_targets

    # Start from the clean CI environment, rather than a developer shell that
    # already enables capture. Keep the harness's actual stage settings.
    for name in tuple(platform.os.environ):
        if name.startswith(("PCC_DIRECT_INDEXED_", "PCC_TEXT_INDEXED_")):
            monkeypatch.delenv(name)
    for name, value in platform._stage_environment(0).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: target)
    source = tmp_path / "probe.py"
    source.write_text("def answer(value: int) -> int:\n    return value + 2\nprint(answer(40))\n")
    manifest = tmp_path / "worker.manifest"
    result = tmp_path / "result.tsv"
    pipeline._write_python_frontend_worker_manifest(
        str(manifest), str(result), str(tmp_path), "", "", [str(source)],
        ["probe"], [0], entry_module="probe", sibling_inits=(),
        ir_scaffold_mode="on", verbose=False,
    )
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    object_path = tmp_path / "module_0.direct.pco"
    assert object_path.stat().st_size > 0
    assert "\tPCO\t" + str(object_path) in result.read_text()
