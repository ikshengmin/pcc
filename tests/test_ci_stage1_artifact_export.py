"""Tiny, host-only export fixtures; no real compiler/runtime is built or packed."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile

import pytest

from scripts import ci_stage1_artifact_export as exporter
from scripts import ci_stage1_bundle as bundle


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
SOURCE = "b" * 64
# Independent producer contract, not a copy of the exporter's acceptance policy.
RUNTIME_CONFIG = {"threads": True, "refcount": "atomic"}
SHARED = "build/platform-qualification/shared/"
RUNTIME = "build/worker-prior-gate/runtime/libpy_runtime_pcc_py.a"
STAGE_RECEIPT = "build/worker-prior-gate/stage1.json"
AUXILIARY = ("canary.py", "canary1", "stage1.log", "stage1.resources.jsonl",
             "canary1-compile.log", "canary1-compile.resources.jsonl",
             "canary1-run.log", "canary1-run.resources.jsonl")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _native_header(platform_id="linux-x86_64"):
    if platform_id.startswith("linux"):
        data = bytearray(128)
        data[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
        struct.pack_into("<HHIQQQIHHHHHH", data, 16,
                         2, 183 if platform_id.endswith("aarch64") else 62, 1,
                         0x400078, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        struct.pack_into("<IIQQQQQQ", data, 64, 1, 5, 0, 0x400000, 0, 128, 128, 4096)
    elif platform_id == "macos-arm64":
        data = bytearray(64)
        struct.pack_into("<IIIIIIII", data, 0, 0xfeedfacf, 0x100000c, 0, 2, 0, 0, 0, 0)
    else:
        data = bytearray(512)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 128)
        data[128:132] = b"PE\0\0"
        struct.pack_into("<HH", data, 132, 0x8664, 1)
        struct.pack_into("<HHH", data, 148, 240, 2, 0x20b)
    return bytes(data)


@pytest.fixture(autouse=True)
def lease_env(monkeypatch):
    for key, value in {"GITHUB_REPOSITORY": "owner/pcc", "GITHUB_WORKFLOW_REF": "owner/pcc/.github/workflows/pcc1-package-parity.yml@refs/heads/master",
                       "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": COMMIT}.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    compiler = tmp_path / "build/platform-qualification/shared/pcc1"
    runtime = tmp_path / "build/worker-prior-gate/runtime/libpy_runtime_pcc_py.a"
    compiler.parent.mkdir(parents=True)
    runtime.parent.mkdir(parents=True)
    compiler.write_bytes(_native_header())
    compiler.chmod(0o755)
    hashes = {}
    for suffix in ("", ".provenance.json", ".capi_syms"):
        path = Path(str(runtime) + suffix)
        payload = ("tiny already-admitted runtime fixture " + suffix + "\n").encode()
        path.write_bytes(payload)
        path.chmod(0o644)
        hashes[suffix] = _sha(payload)
    source = {"commit": COMMIT, "source_sha256": SOURCE, "clean_commit": True}
    stage_path = tmp_path / "build/worker-prior-gate/stage1.json"
    shared_path = compiler.parent / "receipt.json"
    stage = {
        "phase": "stage1", "status": "PASS", "source": source,
        "compiler": {"path": str(compiler), "sha256": _sha(compiler.read_bytes())},
        "runtime": {"path": str(runtime), "target": exporter.TARGET,
                    "config": RUNTIME_CONFIG.copy(), "hashes": hashes},
    }
    shared = {
        "source": source, "compiler_path": str(compiler), "target": exporter.TARGET,
        "runtime": {"path": str(runtime), "sha256": hashes[""],
                    "provenance_sha256": hashes[".provenance.json"],
                    "capi_inventory_sha256": hashes[".capi_syms"]},
        "stages": [{"stage": 1, "sha256": stage["compiler"]["sha256"]}],
        "qualified": False, "fixed_point": False,
    }
    for name in AUXILIARY:
        path = compiler.parent / name
        path.write_bytes(b"" if name.endswith((".log", ".resources.jsonl"))
                         else ("tiny canary fixture " + name).encode())
    (compiler.parent / "canary1").write_bytes(_native_header())
    (compiler.parent / "canary1").chmod(0o755)
    (compiler.parent / "canary1-run.log").write_bytes(b"synthetic canary output\n")
    shared["stages"][0]["canary"] = {
        key: _sha((compiler.parent / name).read_bytes())
        for name, key in (("canary.py", "source_sha256"), ("canary1", "executable_sha256"),
                          ("canary1-run.log", "output_sha256"))
    }
    _save(stage_path, stage)
    _save(shared_path, shared)
    # The fixture stands in for the existing workflow admission. Export must
    # never execute this placeholder compiler or start another build/probe.
    def forbidden(*args, **kwargs):
        raise AssertionError("export must not invoke a subprocess")
    for name in ("run", "Popen", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("system", "fork", "forkpty", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(exporter, "_admit_runtime", lambda root, archive, target: None)
    return tmp_path, runtime, compiler, stage_path, shared_path


def _export(admitted, **kwargs):
    root, runtime, *_ = admitted
    return exporter.export_bundle(root, runtime, kwargs.get("source", SOURCE), kwargs.get("commit", COMMIT), platform_id="linux-x86_64")


def _assert_unpublished(admitted):
    root = admitted[0]
    assert not (root / exporter.OUTPUT_DIRECTORY).exists()
    assert not list((root / "build").glob("stage1-export.*"))


def test_export_preserves_exact_bytes_modes_receipts_and_whitelist(admitted, monkeypatch):
    from pcc.frontends.python.owned_runtime_build import runtime_build_config

    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "atomic")
    assert runtime_build_config() == RUNTIME_CONFIG
    root, runtime, compiler, stage_path, shared_path = admitted
    omitted = compiler.parent / "huge-intermediate.pco"
    omitted.write_bytes(b"excluded intermediate")
    (compiler.parent / "pcc2").write_bytes(b"excluded later-stage compiler")
    _save(root / "build/platform-qualification/matrix-receipt.json", {"not": "a bundle claim"})
    inputs = {
        SHARED + "pcc1": compiler,
        **{RUNTIME + suffix: Path(str(runtime) + suffix)
           for suffix in ("", ".provenance.json", ".capi_syms")},
        STAGE_RECEIPT: stage_path,
        SHARED + "receipt.json": shared_path,
        **{SHARED + name: compiler.parent / name for name in AUXILIARY},
    }
    expected = {name: path.read_bytes() for name, path in inputs.items()}
    original_read_bytes = Path.read_bytes

    def no_bulk_payload_read(path):
        assert path not in (compiler, runtime), "large payloads must stream"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", no_bulk_payload_read)
    result = _export(admitted)
    directory = root / exporter.OUTPUT_DIRECTORY
    assert set(path.name for path in directory.iterdir()) == {exporter.ARCHIVE_NAME, "manifest.json"}
    assert set(result["files"]) == set(inputs)
    assert result["schema"] == "pcc.ci-stage1-bundle.v2"
    assert result["claim"] == "Stage1 identity only; not five-GC, prior, wheel or fixed-point qualification"
    assert result["source"] == {"commit": COMMIT, "source_sha256": SOURCE, "clean_commit": True}
    assert result["target"] == exporter.TARGET and result["runtime_config"] == RUNTIME_CONFIG
    manifest_bytes = (directory / "manifest.json").read_bytes()
    assert json.loads(manifest_bytes) == result
    with tarfile.open(directory / exporter.ARCHIVE_NAME, "r:gz") as archive:
        assert set(archive.getnames()) == set(inputs) | {"manifest.json"}
        assert len(archive.getmembers()) == len(inputs) + 1
        for name, path in inputs.items():
            member = archive.getmember(name)
            assert member.isfile()
            assert archive.extractfile(member).read() == expected[name]
            assert member.mode == result["files"][name]["mode"] == path.stat().st_mode & 0o7777
            assert member.size == result["files"][name]["size"] == len(expected[name])
            assert result["files"][name]["sha256"] == _sha(expected[name])
            assert result["files"][name]["source_path"] == str(path)
        assert archive.extractfile("manifest.json").read() == manifest_bytes
    # No strip, chmod or content rewrite of original payloads or receipts.
    assert compiler.stat().st_mode & 0o7777 == 0o755
    assert all(original_read_bytes(path) == expected[name] for name, path in inputs.items())


@pytest.mark.parametrize("receipt_index", (3, 4))
@pytest.mark.parametrize("field,value", (("commit", "c" * 40), ("source_sha256", "d" * 64),
                                        ("clean_commit", False)))
def test_export_rejects_receipts_from_a_different_or_dirty_source(admitted, receipt_index, field, value):
    path = admitted[receipt_index]
    record = json.loads(path.read_text())
    record["source"][field] = value
    _save(path, record)
    with pytest.raises(ValueError, match="source differs"):
        _export(admitted)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("override", ({"source": "f" * 64}, {"commit": "e" * 40},
                                     {"source": "bad"}, {"commit": "bad"}))
def test_export_rejects_wrong_admission_source_inputs(admitted, override):
    with pytest.raises(ValueError, match="source"):
        _export(admitted, **override)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("field,value", (("status", "FAIL"), ("status", "RUNNING"), ("phase", "pcc1")))
def test_export_requires_terminal_stage1_pass(admitted, field, value):
    path = admitted[3]
    record = json.loads(path.read_text())
    record[field] = value
    _save(path, record)
    with pytest.raises(ValueError, match="PASS Stage1"):
        _export(admitted)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("member", ("pcc1", "runtime", "provenance", "capi", "stage", "shared") + AUXILIARY)
def test_export_rejects_missing_whitelisted_files(admitted, member):
    _, runtime, compiler, stage, shared = admitted
    path = {"pcc1": compiler, "runtime": runtime, "provenance": Path(str(runtime) + ".provenance.json"),
            "capi": Path(str(runtime) + ".capi_syms"), "stage": stage, "shared": shared,
            **{name: compiler.parent / name for name in AUXILIARY}}[member]
    path.unlink()
    with pytest.raises(FileNotFoundError):
        _export(admitted)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("member", ("pcc1", "", ".provenance.json", ".capi_syms"))
def test_export_rejects_changed_payload_hash_and_removes_partial_tar(admitted, member):
    path = admitted[2] if member == "pcc1" else Path(str(admitted[1]) + member)
    data = path.read_bytes()
    path.write_bytes(data[:-1] + b"X")
    with pytest.raises(ValueError, match="hash or size mismatch"):
        _export(admitted)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("name", ("canary.py", "canary1", "canary1-run.log"))
def test_export_rejects_canary_bytes_that_differ_from_producer_hashes(admitted, name):
    path = admitted[2].parent / name
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash or size mismatch"):
        _export(admitted)
    _assert_unpublished(admitted)


def test_export_rechecks_log_bytes_after_capturing_their_manifest_hash(admitted, monkeypatch):
    path = admitted[2].parent / "stage1.log"
    path.write_bytes(b"original")
    original_stat = path.stat()
    original_add = tarfile.TarFile.addfile

    def changed(archive, header, *args, **kwargs):
        if header.name == SHARED + "pcc1":
            path.write_bytes(b"modified")  # same length, different bytes
            # Keep this a hash-specific oracle despite the earlier metadata
            # stability guard; unchanged metadata must not admit changed bytes.
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return original_add(archive, header, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "addfile", changed)
    with pytest.raises(ValueError, match="hash or size mismatch"):
        _export(admitted)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("member", ("compiler", "runtime"))
def test_export_rejects_disagreeing_receipt_hashes(admitted, member):
    path = admitted[4]
    record = json.loads(path.read_text())
    if member == "compiler":
        record["stages"][0]["sha256"] = "0" * 64
    else:
        record["runtime"]["sha256"] = "0" * 64
    _save(path, record)
    with pytest.raises(ValueError, match="hash differs"):
        _export(admitted)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("field,value", (("path", "/other/runtime.a"), ("target", "aarch64-unknown-linux-gnu"),
                                        ("config", {"threads": False, "refcount": "atomic"})))
def test_export_rejects_wrong_runtime_identity(admitted, field, value):
    path = admitted[3]
    record = json.loads(path.read_text())
    record["runtime"][field] = value
    _save(path, record)
    with pytest.raises(ValueError, match="runtime identity differs"):
        _export(admitted)
    _assert_unpublished(admitted)


def test_export_rejects_a_runtime_other_than_the_admitted_path(admitted):
    with pytest.raises(ValueError, match="runtime path differs from admission"):
        exporter.export_bundle(admitted[0], admitted[1].with_name("other.a"), SOURCE, COMMIT)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("index", (2, 3, 4))
def test_export_rejects_symlink_inputs(admitted, index):
    path = admitted[index]
    replacement = path.with_name(path.name + ".original")
    path.rename(replacement)
    path.symlink_to(replacement)
    with pytest.raises(ValueError, match="regular, non-symlink"):
        _export(admitted)
    _assert_unpublished(admitted)


def test_export_rejects_nonexecutable_compiler_without_chmod(admitted):
    admitted[2].chmod(0o644)
    with pytest.raises(ValueError, match="not executable"):
        _export(admitted)
    assert admitted[2].stat().st_mode & 0o7777 == 0o644
    _assert_unpublished(admitted)


def test_failed_tar_write_does_not_publish_any_bundle(admitted, monkeypatch):
    def failed(*args, **kwargs):
        raise OSError("fixture disk write failure")
    monkeypatch.setattr(tarfile.TarFile, "addfile", failed)
    with pytest.raises(OSError, match="fixture disk write failure"):
        _export(admitted)
    _assert_unpublished(admitted)


def test_export_never_overwrites_an_existing_export(admitted):
    destination = admitted[0] / exporter.OUTPUT_DIRECTORY
    destination.mkdir()
    existing = destination / "keep"
    existing.write_bytes(b"previous export")
    with pytest.raises(ValueError, match="existing export directory"):
        _export(admitted)
    assert existing.read_bytes() == b"previous export"
    assert list(destination.iterdir()) == [existing]


def test_cli_uses_admission_arguments_and_reports_actual_fixture_size(admitted, monkeypatch, capsys):
    monkeypatch.setattr(exporter, "ROOT", admitted[0])
    monkeypatch.setattr(exporter.sys, "platform", "linux")
    monkeypatch.setattr(bundle.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("GITHUB_SHA", COMMIT)
    exporter.main(["--runtime-archive", str(admitted[1]), "--source-sha256", SOURCE])
    record = json.loads(capsys.readouterr().out)
    directory = admitted[0] / exporter.OUTPUT_DIRECTORY
    manifest = json.loads((directory / "manifest.json").read_text())
    assert record["input_bytes"] == sum(row["size"] for row in manifest["files"].values())
    assert record["archive_bytes"] == (directory / exporter.ARCHIVE_NAME).stat().st_size
    assert record["claim"] == manifest["claim"]


@pytest.mark.parametrize("system,machine", (("linux", "aarch64"), ("darwin", "arm64"), ("win32", "AMD64")))
def test_cli_rejects_explicit_platform_different_from_native_host(admitted, monkeypatch, system, machine):
    monkeypatch.setattr(exporter.sys, "platform", system)
    monkeypatch.setattr(bundle.platform, "machine", lambda: machine)
    with pytest.raises(ValueError, match="platform differs from native host"):
        exporter.main(["--platform", "linux-x86_64", "--runtime-archive", str(admitted[1]), "--source-sha256", SOURCE])
    _assert_unpublished(admitted)


def test_exported_relocated_tree_passes_the_real_platform_consumer(tmp_path, monkeypatch):
    """Run real producer/consumer orchestration with tiny, never-executed ELF.

    Checkout discovery and external execution are modeled as in the existing
    platform-admission tests. Actual runtime provenance, receipt, ELF, canary,
    log/resource discovery and relocation checks remain unchanged.
    """
    from pcc.frontends.python import owned_runtime_build
    from scripts import bootstrap_platform as producer
    from scripts import verify_pcc1_platform as consumer
    from tests.python.test_pcc1_platform_admission import _executable, _runtime_fixture

    root = tmp_path / "core"
    (root / "scripts").mkdir(parents=True)
    (root / "pcc").mkdir()
    (root / "pcc/__main__.py").write_text("# Synthetic compiler source; never compiled\n")
    for name in ("bootstrap_platform.py", "platform_process_watchdog.py"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    runtime_directory = root / "build/worker-prior-gate/runtime"
    runtime_directory.parent.mkdir(parents=True)
    runtime = _runtime_fixture(root, runtime_directory)
    monkeypatch.setattr(consumer, "CORE_ROOT", root)
    monkeypatch.setattr(producer, "ROOT", root)
    monkeypatch.setattr(consumer, "_host_source", lambda path: Path(path))
    # As in the admission tests, the producer's host is modeled as the target.
    from pcc.frontends.python import pipeline_targets
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: consumer.TARGET)
    for name, value in {"PCC_GC_BACKEND": "0", "PCC_WITH_THREADS": "1", "PCC_REFCOUNT_KIND": "atomic"}.items():
        monkeypatch.setenv(name, value)

    def git(command, **kwargs):
        assert command[:7] == ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--"]
        assert tuple(command[7:]) == consumer.SOURCE_INPUTS
        assert Path(kwargs["cwd"]) == root
        names = sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                       if path.is_file() and path.relative_to(root).parts[0] in consumer.SOURCE_INPUTS)
        return b"\0".join(name.encode() for name in names) + b"\0"

    monkeypatch.setattr(producer.subprocess, "check_output", git)
    monkeypatch.setattr(producer, "source_identity", lambda *args: {
        "commit": COMMIT, "clean_commit": True, **producer._source_snapshot(),
    })
    calls = []

    def modeled_execution(command, **kwargs):
        calls.append(command)
        log = Path(kwargs["log_path"])
        if "-o" in command:
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(_executable())
            output.chmod(0o755)
            content = ""
        else:
            content = "0\n" + str(2**80 + 42) + "\n"
        log.write_text(content)
        # The existing consumer accepts empty logs/samples; don't manufacture
        # observations just to satisfy a packaging nonempty-file check.
        log.with_suffix(".resources.jsonl").write_bytes(b"")
        return {"seconds": 1.0, "peak_tree_rss": 1, "log": str(log),
                "command": command, "launched_command": command}

    monkeypatch.setattr(producer, "run", modeled_execution)
    shared_directory = root / SHARED
    shared = producer.run_chain("0", shared_directory, stage_limit=1,
                                runtime_archive=runtime, lock_held=True)
    assert len(calls) == 3
    _save(root / STAGE_RECEIPT, {
        "phase": "stage1", "status": "PASS", "source": shared["source"],
        "compiler": {"path": shared["compiler_path"], "sha256": shared["stages"][0]["sha256"]},
        "runtime": {"path": str(runtime), "target": exporter.TARGET,
                    "config": shared["configuration"]["runtime"].copy(),
                    "hashes": {suffix: shared["runtime"][key] for suffix, key in (
                        ("", "sha256"), (".provenance.json", "provenance_sha256"),
                        (".capi_syms", "capi_inventory_sha256"))}},
    })
    original_receipt = (shared_directory / "receipt.json").read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("export or admission attempted native execution/provisioning")

    monkeypatch.setattr(producer, "run", forbidden)
    monkeypatch.setattr(producer.subprocess, "Popen", forbidden)
    monkeypatch.setattr(owned_runtime_build, "ensure_target_runtime", forbidden)
    exporter.export_bundle(root, runtime, shared["source"]["source_sha256"], COMMIT, platform_id="linux-x86_64")
    relocated = tmp_path / "downloaded"
    relocated.mkdir()
    with tarfile.open(root / exporter.OUTPUT_DIRECTORY / exporter.ARCHIVE_NAME) as archive:
        archive.extractall(relocated, filter="data")
    # Make the original absolute artifact paths unavailable. The source
    # checkout stays unchanged and complete for this modeled source closure.
    shared_directory.rename(shared_directory.with_name("original-shared-withheld"))
    runtime_directory.rename(runtime_directory.with_name("original-runtime-withheld"))
    receipt = relocated / SHARED / "receipt.json"
    assert receipt.read_bytes() == original_receipt
    assert json.loads(original_receipt)["compiler_path"] == str(shared_directory / "pcc1")
    assert not (shared_directory / "pcc1").exists() and not runtime.exists()
    admitted = consumer.verify_platform_compiler(
        receipt, compiler=relocated / SHARED / "pcc1", runtime_archive=relocated / RUNTIME,
        source_root=root, gc_backend=0, threads=True, refcount="atomic",
    )
    assert admitted["compiler"]["path"] == str(relocated / SHARED / "pcc1")
    assert "five-GC, fixed point and application benchmark unproven" in admitted["qualification"]
    assert all(str(relocated / SHARED / name) in admitted["files"] for name in AUXILIARY)
    assert receipt.read_bytes() == original_receipt


def test_workflow_packages_stage1_without_another_native_build():
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    assert "scripts/ci_stage1_artifact_export.py --platform" in workflow
    assert "scripts/ci_stage1_artifact_export.py" in workflow
    assert "tests/test_ci_stage1_artifact_export.py" in workflow
    assert "tests/test_ci_stage1_bundle.py" in workflow
    assert "build/stage1-native-export/stage1-native.tar.gz" in workflow
    assert "build/stage1-native-export/manifest.json" in workflow
    assert "github.run_attempt" in workflow


def test_export_rejects_stale_current_runtime_admission(admitted, monkeypatch):
    calls = []
    def stale(root, archive, target):
        calls.append((root, archive, target))
        raise ValueError("explicit test runtime is stale for current codegen")
    monkeypatch.setattr(exporter, "_admit_runtime", stale)
    with pytest.raises(ValueError, match="stale for current codegen"):
        _export(admitted)
    assert calls == [(admitted[0], admitted[1], exporter.TARGET)]
    _assert_unpublished(admitted)


def test_workflow_retains_original_host_regressions_and_default_workers():
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    job = workflow.split("  other-platform-native-wheel:\n", 1)[1].split("  pcc1-package-parity:\n", 1)[0]
    host = job.split("      - name: Verify host Stage2 export-retention", 1)[1].split("      #", 1)[0]
    host_tests = (
        "tests/python/test_stage2_export_capture.py",
        "tests/python/test_aarch64_call_target_reuse.py",
        "tests/python/test_owned_elf_assembler_dispatch.py",
        "tests/python/test_elf_arm_branch_slot_defaults.py",
        "tests/python/test_gc_phase_attribution.py",
        "tests/python/test_gc_phase_counter_widths.py",
        "tests/test_ci_native_object_regressions.py",
    )
    host_command = host.split("        run: ", 1)[1].splitlines()[0]
    host_args = shlex.split(host_command.split("${{", 1)[0], comments=True)
    pytest_command = ["uv", "run", "--frozen", "--no-sync",
                      "python", "-m", "pytest", "-x", "-q"]
    assert host_args[:len(pytest_command)] == pytest_command
    # Require every host regression once, in order, while allowing new tests
    # between them. Match command arguments rather than contiguous text.
    assert tuple(arg for arg in host_args[len(pytest_command):] if arg in host_tests) == host_tests
    linux_only = (
        "tests/test_ci_stage1_artifact_export.py",
        "tests/python/test_gc4_copy_page_probe_native.py",
        "tests/python/test_gc_relocation_membership_lifecycle.py",
        "tests/python/test_gc_remembered_membership_lifecycle.py",
        "tests/python/test_gc_remembered_membership_native.py",
        "tests/python/test_owned_runtime_pass_prefix.py",
        "tests/python/test_pipeline_ir_split.py",
        "tests/python/test_pipeline_ir_frame_maps.py",
    )
    linux_condition = host_command.split("${{ ", 1)[1]
    linux_prefix = "matrix.platform == 'linux-x86_64' && '"
    linux_suffix = "' || '' }}"
    assert linux_condition.startswith(linux_prefix)
    assert linux_condition.endswith(linux_suffix)
    linux_args = shlex.split(linux_condition[len(linux_prefix):-len(linux_suffix)])
    # Keep the required regressions exactly once and in order while allowing
    # other short Linux-only checks in the same existing step.
    assert tuple(arg for arg in linux_args if arg in linux_only) == linux_only
    assert "python -m pytest -x -q" in host and "-n0" not in host and "addopts" not in host
    for path in (
        "scripts/ci_stage1_artifact_export.py",
        "tests/test_ci_stage1_artifact_export.py",
        "tests/python/test_gc_remembered_membership_lifecycle.py",
        "tests/python/test_gc_remembered_membership_native.py",
        "tests/python/test_owned_runtime_pass_prefix.py",
        "tests/python/test_pipeline_ir_split.py",
        "tests/python/test_pipeline_ir_frame_maps.py",
    ):
        assert workflow.count('      - "' + path + '"') == 2


def _stat_fields(info, **changes):
    from types import SimpleNamespace
    values = {key: getattr(info, key) for key in
              ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")}
    return SimpleNamespace(**(values | changes))


def _as_windows_product(admitted):
    """Synthetic PE payloads; only filesystem/archive operations are real."""
    root, runtime, compiler, stage_path, shared_path = admitted
    stage = json.loads(stage_path.read_text())
    shared = json.loads(shared_path.read_text())
    compiler = compiler.rename(compiler.with_suffix(".exe"))
    canary = (compiler.parent / "canary1").rename(compiler.parent / "canary1.exe")
    for path in (compiler, canary):
        path.write_bytes(_native_header("windows-x86_64"))
        path.chmod(0o777)
    stage["compiler"] = {"path": str(compiler), "sha256": _sha(compiler.read_bytes())}
    stage["runtime"]["target"] = bundle.PLATFORMS["windows-x86_64"]["target"]
    shared["compiler_path"] = str(compiler)
    shared["target"] = stage["runtime"]["target"]
    shared["stages"][0]["sha256"] = stage["compiler"]["sha256"]
    shared["stages"][0]["canary"]["executable_sha256"] = _sha(canary.read_bytes())
    _save(stage_path, stage)
    _save(shared_path, shared)
    return root, runtime, compiler, stage_path, shared_path


def _windows_fd_modes(monkeypatch, paths, mutation=None):
    """Model CPython's filename-independent Windows fstat, not native execution."""
    original = os.fstat
    identities = {(p.stat().st_dev, p.stat().st_ino) for p in paths}
    calls = {}

    def fstat(fd):
        info = original(fd)
        identity = (info.st_dev, info.st_ino)
        if identity not in identities:
            return info
        calls[identity] = calls.get(identity, 0) + 1
        info = _stat_fields(info, st_mode=0o100666)
        return mutation(info, calls[identity]) if mutation else info

    monkeypatch.setattr(exporter.os, "fstat", fstat)
    return calls


@pytest.mark.parametrize("platform_id", tuple(bundle.PLATFORMS))
def test_opened_identity_binds_stat_channels_without_masking_modes(platform_id):
    from types import SimpleNamespace
    path = SimpleNamespace(st_dev=7, st_ino=11, st_mode=0o100777,
                           st_size=512, st_mtime_ns=123456789)
    exporter._require_opened_identity(path, path, "fixture", platform_id)
    fd = _stat_fields(path, st_mode=0o100666)
    if platform_id == "windows-x86_64":
        exporter._require_opened_identity(path, fd, "fixture", platform_id)
    else:
        with pytest.raises(ValueError, match="opened file differs"):
            exporter._require_opened_identity(path, fd, "fixture", platform_id)
    for key, value in (("st_dev", 8), ("st_ino", 12), ("st_mode", 0o040777),
                       ("st_size", 513), ("st_mtime_ns", 123456790)):
        with pytest.raises(ValueError, match="opened file differs"):
            exporter._require_opened_identity(path, _stat_fields(path, **{key: value}),
                                              "fixture", platform_id)


def test_windows_path_and_fd_mode_channels_roundtrip_without_rewriting(admitted, monkeypatch):
    admitted = _as_windows_product(admitted)
    root, runtime, compiler, *_ = admitted
    canary = compiler.parent / "canary1.exe"
    calls = _windows_fd_modes(monkeypatch, (compiler, canary))
    manifest = exporter.export_bundle(root, runtime, SOURCE, COMMIT,
                                      platform_id="windows-x86_64")
    originals = {name: (root / name).read_bytes() for name in manifest["files"]}
    directory = root / exporter.OUTPUT_DIRECTORY
    with tarfile.open(directory / exporter.ARCHIVE_NAME) as archive:
        for path in (compiler, canary):
            name = path.relative_to(root).as_posix()
            assert path.stat().st_mode & 0o7777 == manifest["files"][name]["mode"] == 0o777
            assert archive.getmember(name).mode == 0o777
            assert archive.extractfile(name).read() == originals[name]
    assert len(calls) == 2 and set(calls.values()) == {4}
    for name in originals:
        (root / name).unlink()
    actual = bundle.import_bundle(root, directory, COMMIT, "windows-x86_64",
                                  bundle.workflow_lease())
    assert actual == manifest
    assert all((root / name).read_bytes() == data for name, data in originals.items())


@pytest.mark.parametrize("platform_id", ("linux-x86_64", "windows-x86_64"))
@pytest.mark.parametrize("change", ("mode", "mtime", "replacement", "size", "hash", "symlink"))
def test_export_still_rejects_path_changes_before_packing(admitted, monkeypatch, platform_id, change):
    if platform_id == "windows-x86_64":
        admitted = _as_windows_product(admitted)
    root, runtime, compiler, *_ = admitted
    target = compiler.parent / ("canary1.exe" if platform_id == "windows-x86_64" else "canary1")
    if platform_id == "windows-x86_64":
        _windows_fd_modes(monkeypatch, (compiler, target))

    def mutate(*args):
        original = target.stat()
        if change == "mode":
            target.chmod(0o444)
        elif change == "mtime":
            os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000))
        elif change in ("replacement", "symlink"):
            moved = target.with_name(target.name + ".original")
            target.rename(moved)
            if change == "symlink":
                target.symlink_to(moved)
            else:
                target.write_bytes(moved.read_bytes())
                target.chmod(original.st_mode & 0o7777)
                os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
        else:
            content = target.read_bytes()
            target.write_bytes(content + b"extra" if change == "size" else content[:-1] + bytes([content[-1] ^ 1]))
            # Same-length/content mutation must still fail the receipt hash,
            # even if the publisher preserved the recorded mtime.
            os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))

    monkeypatch.setattr(exporter, "_admit_runtime", mutate)
    with pytest.raises(ValueError, match="changed|hash or size mismatch|regular, non-symlink"):
        exporter.export_bundle(root, runtime, SOURCE, COMMIT, platform_id=platform_id)
    _assert_unpublished(admitted)


@pytest.mark.parametrize("call", (2, 3, 4))
@pytest.mark.parametrize("field,value", (("st_mode", 0o100444), ("st_mode", 0o040666),
                                         ("st_ino", -1), ("st_mtime_ns", -1)))
def test_windows_fd_changes_rejected_during_admission_and_packing(admitted, monkeypatch, call, field, value):
    admitted = _as_windows_product(admitted)
    root, runtime, compiler, *_ = admitted
    canary = compiler.parent / "canary1.exe"

    def mutate(info, number):
        return _stat_fields(info, **{field: value}) if number == call else info

    _windows_fd_modes(monkeypatch, (canary,), mutate)
    with pytest.raises(ValueError, match="changed|opened file differs"):
        exporter.export_bundle(root, runtime, SOURCE, COMMIT, platform_id="windows-x86_64")
    _assert_unpublished(admitted)
