"""Real lock/owner-receipt controls only; never execute a compiler or inventory."""

from contextlib import contextmanager
import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_TOOLS = (
    "run_pcc_link_ab",
    "run_pcc_compile_ab",
    "pcc_structured_instruction_inventory",
)


def _load(tool):
    path = Path(__file__).resolve().parents[1] / "scripts" / (tool + ".py")
    spec = importlib.util.spec_from_file_location("_portable_lock_" + tool, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _select_path(module, monkeypatch, tmp_path):
    root = tmp_path / "repository"
    path = root / "build" / ".pcc-performance.lock"
    monkeypatch.setattr(module, "REPO_ROOT", root)
    if hasattr(module, "LOCK_PATH"):
        monkeypatch.setattr(module, "LOCK_PATH", path)
    return path


@pytest.mark.parametrize("tool", _TOOLS)
def test_performance_driver_imports_without_fcntl(monkeypatch, tool):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    assert callable(_load(tool)._performance_lock)


@pytest.mark.parametrize("tool", _TOOLS)
def test_performance_driver_direct_script_import_fallback(tmp_path, tool):
    script = Path(__file__).resolve().parents[1] / "scripts" / (tool + ".py")
    code = (
        "import runpy, sys\n"
        "from pathlib import Path\n"
        "sys.modules['fcntl'] = None\n"
        "sys.path.insert(0, str(Path(sys.argv[1]).parent))\n"
        "namespace = runpy.run_path(sys.argv[1], run_name='_lock_import_only')\n"
        "assert callable(namespace['_performance_lock'])\n"
        "assert namespace['exclusive_file_lock'].__module__ == 'file_lock'\n"
        "print('IMPORTED')\n"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    run = subprocess.run(
        [sys.executable, "-B", "-c", code, str(script)], cwd=tmp_path,
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "IMPORTED"


@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("raise_in_body", [False, True])
def test_performance_lock_excludes_real_contender_and_flushes_owner(
        tmp_path, monkeypatch, tool, raise_in_body):
    module = _load(tool)
    path = _select_path(module, monkeypatch, tmp_path)
    fsync_calls = []
    real_fsync = os.fsync
    def fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)
    monkeypatch.setattr(module.os, "fsync", fsync)
    repo = Path(__file__).resolve().parents[1]
    code = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        "source, lock = map(Path, sys.argv[1:])\n"
        "spec = importlib.util.spec_from_file_location('_lock_contender', source)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "module.REPO_ROOT = lock.parent.parent\n"
        "if hasattr(module, 'LOCK_PATH'): module.LOCK_PATH = lock\n"
        "try:\n"
        "    with module._performance_lock(): print('OWNED')\n"
        "except (OSError, RuntimeError): print('BLOCKED')\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo)
    def contender():
        run = subprocess.run(
            [sys.executable, "-B", "-c", code, str(module.__file__), str(path)],
            cwd=repo, env=environment, capture_output=True, text=True, timeout=10,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        return run.stdout.strip()
    failure = RuntimeError("critical-section failure")
    try:
        with module._performance_lock() as value:
            assert value is None
            active_bytes = path.read_bytes()
            active = json.loads(active_bytes)
            assert active["active"] is True and active["pid"] == os.getpid()
            assert active["argv"] == sys.argv and "started_at_utc" in active
            assert "completed_at_utc" not in active
            assert len(fsync_calls) == 1
            assert contender() == "BLOCKED"
            assert path.read_bytes() == active_bytes
            if raise_in_body:
                raise failure
    except RuntimeError as error:
        assert raise_in_body and error is failure
    inactive = json.loads(path.read_bytes())
    assert inactive["active"] is False
    assert inactive["pid"] == active["pid"]
    assert inactive["started_at_utc"] == active["started_at_utc"]
    assert "completed_at_utc" in inactive and len(fsync_calls) == 2
    assert contender() == "OWNED"


@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("error", [BlockingIOError(errno.EAGAIN, "busy"), OSError(errno.EIO, "I/O error")])
def test_performance_lock_preserves_acquisition_error_contract(
        tmp_path, monkeypatch, tool, error):
    module = _load(tool)
    path = _select_path(module, monkeypatch, tmp_path)
    @contextmanager
    def unavailable(actual, *, blocking):
        assert actual == path and blocking is False
        raise error
        yield  # pragma: no cover - context-manager protocol
    monkeypatch.setattr(module, "exclusive_file_lock", unavailable)
    with pytest.raises((OSError, RuntimeError)) as caught:
        with module._performance_lock():
            pytest.fail("entered critical section without acquiring the lock")
    if isinstance(error, BlockingIOError):
        assert isinstance(caught.value, RuntimeError)
        assert caught.value.__cause__ is error
        assert "another" in str(caught.value) and str(path) in str(caught.value)
    else:
        assert caught.value is error
    assert not path.exists()


@pytest.mark.parametrize("tool", _TOOLS)
def test_performance_lock_does_not_relabel_body_blocking_error(tmp_path, monkeypatch, tool):
    module = _load(tool)
    path = _select_path(module, monkeypatch, tmp_path)
    error = BlockingIOError(errno.EAGAIN, "body failure, not lock contention")
    with pytest.raises(BlockingIOError) as caught:
        with module._performance_lock():
            raise error
    assert caught.value is error
    assert json.loads(path.read_bytes())["active"] is False
