"""Bounded watchdog evidence; no compiler build or generated program required."""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts/run_process_tree_sample.py"
FRONTEND = "--pcc-python-multi-codegen-worker"
BACKEND = "--pcc-self-backend-emit-batch-worker"
FRONTEND_TEXT = (
    "pcc.frontends.python.codegen_worker.v4\n"
    "/tmp/result.tsv\n/tmp/ir\n/tmp/exports\ncodegen\n/tmp/ast\n"
    "main\noff\non\n0\n0\n1\n0\tos.walk\t/source/os.py\n1\n0\n"
)
BACKEND_TEXT = (
    "pcc.self_backend.emit_batch.v1\n"
    "/tmp/os_walk.ll\n/tmp/result.tsv\n/tmp/os_walk.o\npcc\n"
)


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("manifest_evidence_guard", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(TOOL.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TOOL.parent))
    return module


def _manifest(tmp_path, *, frontend=True, index=0, text=None):
    directory = tmp_path / ("pcc_frontends_python_workers_fixture" if frontend else "pcc_py_self_fixture")
    directory.mkdir(mode=0o700, exist_ok=True)
    name = f"worker_{index}.manifest" if frontend else f"self_backend_emit_oversized_{index}.manifest"
    path = directory / name
    path.write_text(text if text is not None else FRONTEND_TEXT if frontend else BACKEND_TEXT)
    return path


def _fake_processes(tool, monkeypatch, tmp_path, paths, *, flag=FRONTEND, argv=None):
    identity = tool._ProcessIdentity(10, 1, (100, 0))
    observed = {pid: identity for pid in range(10, 10 + len(paths))}
    processes = [{"pid": pid, "manifest_paths": [str(path)]}
                 for pid, path in zip(observed, paths)]
    commands = {
        f"/proc/{pid}/cmdline": argv if argv is not None else
        b"/compiler/pcc1\0" + os.fsencode(flag) + b"\0" + os.fsencode(path) + b"\0"
        for pid, path in zip(observed, paths)
    }
    original_open = builtins.open

    def open_command(path, *args, **kwargs):
        if str(path) in commands:
            return io.BytesIO(commands[str(path)])
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(tool, "open", open_command, raising=False)
    monkeypatch.setattr(tool.sys, "platform", "linux")
    monkeypatch.setattr(tool, "_process_identity", observed.get)
    monkeypatch.setattr(tool.tempfile, "gettempdir", lambda: str(tmp_path))
    return processes, observed


@pytest.mark.parametrize("frontend", [False, True])
def test_retains_only_versioned_manifest_text_and_identity(tool, monkeypatch, tmp_path, frontend):
    path = _manifest(tmp_path, frontend=frontend)
    flag = FRONTEND if frontend else BACKEND
    processes, observed = _fake_processes(tool, monkeypatch, tmp_path, [path], flag=flag)
    evidence = tool._capture_worker_manifests(processes, observed)
    row, = evidence["files"]
    assert row["status"] == "captured"
    assert row["worker_flag"] == flag
    assert row["process_start_time"] == [100, 0]
    assert row["text"] == path.read_text()
    assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert row["source_bytes"] == row["retained_bytes"] == evidence["read_bytes"]
    assert row["truncated"] is False
    assert evidence["complete_inventory"] is False
    assert evidence["scope"] == "observed_workers_before_guard_cleanup"


@pytest.mark.parametrize("version", range(1, 6))
def test_all_current_frontend_protocol_headers_are_retained(tool, tmp_path, version):
    text = FRONTEND_TEXT.replace(".v4\n", f".v{version}\n")
    path = _manifest(tmp_path, text=text)
    row = tool._read_worker_manifest(str(path), FRONTEND, tmp_path, 4096)
    assert row["status"] == "captured" and row["text"] == text


@pytest.mark.parametrize("kind", [
    "outside", "relative", "traversal", "directory_symlink", "file_symlink",
    "ancestor_symlink", "hardlink", "fifo", "directory", "public_directory", "header",
])
def test_rejects_unsafe_paths_and_files(tool, tmp_path, kind):
    path = _manifest(tmp_path)
    secret = tmp_path / "private.txt"
    secret.write_text("SECRET MUST NEVER BE RETAINED")
    root = tmp_path
    if kind == "outside":
        path = secret
    elif kind == "relative":
        path = path.relative_to(tmp_path)
    elif kind == "traversal":
        path = path.parent / ".." / path.parent.name / path.name
    elif kind == "directory_symlink":
        real = tmp_path / "elsewhere"
        path.parent.rename(real)
        path.parent.symlink_to(real, target_is_directory=True)
    elif kind == "file_symlink":
        path.unlink()
        path.symlink_to(secret)
    elif kind == "ancestor_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        root = alias
        path = alias / path.relative_to(tmp_path)
    elif kind == "hardlink":
        path.unlink()
        os.link(secret, path)
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "public_directory":
        path.parent.chmod(0o755)
    elif kind == "header":
        path.write_text(secret.read_text())
    row = tool._read_worker_manifest(str(path), FRONTEND, root, 4096)
    assert row["status"] != "captured", row
    assert "text" not in row and "SECRET" not in json.dumps(row)


@pytest.mark.parametrize("wrong_owner", ["directory", "file"])
def test_rejects_other_owners(tool, monkeypatch, tmp_path, wrong_owner):
    path = _manifest(tmp_path)
    original = tool.os.fstat

    def fstat(fd):
        info = original(fd)
        wanted = path.parent.stat() if wrong_owner == "directory" else path.stat()
        if info.st_ino == wanted.st_ino:
            return SimpleNamespace(st_uid=info.st_uid + 1, st_mode=info.st_mode)
        return info

    monkeypatch.setattr(tool.os, "fstat", fstat)
    assert tool._read_worker_manifest(str(path), FRONTEND, tmp_path, 4096)["status"] == "rejected_" + wrong_owner


def test_missing_and_changing_files_do_not_escape(tool, monkeypatch, tmp_path):
    path = _manifest(tmp_path)
    original = tool.os.read

    def read(fd, limit):
        data = original(fd, limit)
        path.write_text(FRONTEND_TEXT + "changed\n")
        return data

    monkeypatch.setattr(tool.os, "read", read)
    row = tool._read_worker_manifest(str(path), FRONTEND, tmp_path, 4096)
    assert row["status"] == "changed_during_read" and "text" not in row
    path.unlink()
    assert tool._read_worker_manifest(str(path), FRONTEND, tmp_path, 4096)["status"] == "unavailable"


@pytest.mark.parametrize("argv", [
    b"pcc1\0--unrelated\0file.manifest\0",
    b"pcc1 --pcc-python-multi-codegen-worker /tmp/private.txt\0",
    b"pcc1\0--pcc-python-multi-codegen-worker\0/tmp/private.txt",
    b"x" * (64 * 1024 + 1) + b"\0",
], ids=["flag_removed", "quoted_process_name", "incomplete_argv", "oversized_argv"])
def test_stale_or_ambiguous_commands_do_not_authorize_reads(tool, monkeypatch, tmp_path, argv):
    path = _manifest(tmp_path)
    processes, observed = _fake_processes(tool, monkeypatch, tmp_path, [path], argv=argv)
    monkeypatch.setattr(tool, "_read_worker_manifest", lambda *_: pytest.fail("untrusted argv"))
    row, = tool._capture_worker_manifests(processes, observed)["files"]
    assert row["status"] in ("not_manifest_worker", "unavailable_argv")


@pytest.mark.parametrize("change_at", [0, 1, 2])
def test_process_identity_revalidated_around_argv_and_contents(tool, monkeypatch, tmp_path, change_at):
    path = _manifest(tmp_path)
    processes, observed = _fake_processes(tool, monkeypatch, tmp_path, [path])
    values = iter([observed[10]] * change_at + [None])
    monkeypatch.setattr(tool, "_process_identity", lambda _: next(values))
    row, = tool._capture_worker_manifests(processes, observed)["files"]
    assert row["status"] != "captured" and "text" not in row


def test_unknown_process_is_never_opened(tool, monkeypatch, tmp_path):
    path = _manifest(tmp_path)
    processes, observed = _fake_processes(tool, monkeypatch, tmp_path, [path])
    monkeypatch.setattr(tool, "open", lambda *_: pytest.fail("unowned process"))
    assert tool._capture_worker_manifests(processes, {})["files"][0]["status"] == "process_unavailable"


def test_file_total_byte_and_count_caps_are_enforced(tool, monkeypatch, tmp_path):
    paths = [_manifest(tmp_path, index=index, text=FRONTEND_TEXT + "a" * (300 * 1024)) for index in range(20)]
    processes, observed = _fake_processes(tool, monkeypatch, tmp_path, paths)
    evidence = tool._capture_worker_manifests(processes, observed)
    assert len(evidence["files"]) == 16 and evidence["file_limit_reached"]
    captured = [row for row in evidence["files"] if row["status"] == "captured"]
    assert len(captured) == 4
    assert all(row["retained_bytes"] == 256 * 1024 and row["truncated"] for row in captured)
    assert evidence["read_bytes"] == evidence["retained_bytes"] == 1024 * 1024
    assert all(row["status"] == "total_byte_limit" for row in evidence["files"][4:])


def test_duplicate_paths_do_not_repeat_contents(tool, monkeypatch, tmp_path):
    path = _manifest(tmp_path)
    processes, observed = _fake_processes(tool, monkeypatch, tmp_path, [path, path])
    evidence = tool._capture_worker_manifests(processes, observed)
    assert evidence["files"][1]["status"] == "duplicate"
    assert evidence["files"][1]["duplicate_of"] == 0
    assert evidence["read_bytes"] == len(path.read_bytes())


def test_unknown_header_reads_still_consume_total_budget(tool, monkeypatch, tmp_path):
    paths = [_manifest(tmp_path, index=i, text="secret" * 50000) for i in range(5)]
    processes, observed = _fake_processes(tool, monkeypatch, tmp_path, paths)
    evidence = tool._capture_worker_manifests(processes, observed)
    assert evidence["read_bytes"] == 1024 * 1024 and evidence["retained_bytes"] == 0
    assert evidence["files"][-1]["status"] == "total_byte_limit"
    assert "secret" not in json.dumps(evidence)


def test_utf8_boundary_is_explicit_and_digest_matches_retained_text(tool, tmp_path):
    path = _manifest(tmp_path, text=FRONTEND_TEXT + "éé")
    limit = len(FRONTEND_TEXT.encode()) + 1
    row = tool._read_worker_manifest(str(path), FRONTEND, tmp_path, limit)
    assert row["text"] == FRONTEND_TEXT and row["truncated"]
    assert row["sha256"] == hashlib.sha256(row["text"].encode()).hexdigest()
    assert row["read_bytes"] == limit


def test_invalid_utf8_at_truncation_boundary_is_rejected(tool, tmp_path):
    path = _manifest(tmp_path)
    path.write_bytes(FRONTEND_TEXT.encode() + b"\xffmore")
    row = tool._read_worker_manifest(str(path), FRONTEND, tmp_path, len(FRONTEND_TEXT.encode()) + 1)
    assert row["status"] == "rejected_encoding" and "text" not in row


def test_darwin_is_explicitly_unsupported_without_argv_probe(tool, monkeypatch):
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    monkeypatch.setattr(tool, "open", lambda *_: pytest.fail("unsupported argv"), raising=False)
    assert tool._capture_worker_manifests([], {})["status"] == "unsupported_platform"


def test_candidates_are_only_known_manifest_workers(tool, monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "open", lambda *_: pytest.fail("unobserved argv"), raising=False)
    evidence = tool._capture_worker_manifests([{"pid": 10, "manifest_paths": []}], {})
    assert evidence["candidate_count"] == 0 and evidence["files"] == []


@pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux"))
@pytest.mark.parametrize("frontend", [False, True])
def test_timeout_retains_manifest_before_worker_removes_workspace(tmp_path, frontend):
    if not sys.platform.startswith("linux"):
        pytest.fail("structured worker argv capture requires Linux")
    private = tmp_path / "temporary"
    private.mkdir()
    path = _manifest(private, frontend=frontend)
    original = path.read_text()
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import os, shutil, signal, sys, time\n"
        "from pathlib import Path\n"
        "manifest = Path(sys.argv[-1])\n"
        "def stop(*args):\n"
        "    shutil.rmtree(manifest.parent)\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "print(os.getpid(), flush=True)\n"
        "while True: time.sleep(0.01)\n"
    )
    result = tmp_path / "result.json"
    command = [
        sys.executable, str(TOOL), "--result", str(result),
        "--samples", str(tmp_path / "samples.tsv"), "--stdout", str(tmp_path / "target.stdout"),
        "--stderr", str(tmp_path / "target.stderr"), "--cwd", str(ROOT),
        "--timeout", "0.6", "--interval", "0.02", "--max-tree-rss-bytes", str(128 * 1024 * 1024),
        "--no-performance-lock", "--", sys.executable, str(worker),
        FRONTEND if frontend else BACKEND, str(path),
    ]
    run = subprocess.run(command, env=dict(os.environ, TMPDIR=str(private)),
                         capture_output=True, text=True, timeout=8)
    assert run.returncode == 124, run.stdout + run.stderr
    receipt = json.loads(result.read_text())
    assert receipt["status"] == "TIMEOUT" and receipt["returncode"] == 0
    assert receipt["timeout_s"] == 0.6 and receipt["max_tree_rss_bytes"] == 128 * 1024 * 1024
    evidence = receipt["worker_manifest_evidence"]
    row, = [row for row in evidence["files"] if row["status"] == "captured"]
    assert row["text"] == original and row["source_path"] == str(path)
    assert row["pid"] == int((tmp_path / "target.stdout").read_text())
    assert not path.parent.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(row["pid"], 0)


def test_capture_exception_does_not_prevent_timeout_cleanup(tool, monkeypatch, tmp_path):
    def fail(*_):
        raise OSError("injected evidence failure")

    monkeypatch.setattr(tool, "_capture_worker_manifests", fail)
    args = tool._parser().parse_args([
        "--result", str(tmp_path / "result.json"), "--samples", str(tmp_path / "samples.tsv"),
        "--stdout", str(tmp_path / "target.stdout"), "--stderr", str(tmp_path / "target.stderr"),
        "--timeout", "0.2", "--interval", "0.02", "--no-performance-lock", "--",
        sys.executable, "-c", "import os,time;print(os.getpid(),flush=True);time.sleep(30)",
    ])
    receipt = tool.run(args)
    assert receipt["status"] == "TIMEOUT" and receipt["returncode"] == -signal.SIGTERM
    assert receipt["worker_manifest_evidence"] == {"status": "capture_error", "error": "OSError"}
    pid = int((tmp_path / "target.stdout").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_ci_uploads_the_receipt_without_new_artifact_globs():
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    assert "build/worker-prior-gate/**/*.json" in workflow
    assert "build/worker-prior-preflight/**/*.json" in workflow
