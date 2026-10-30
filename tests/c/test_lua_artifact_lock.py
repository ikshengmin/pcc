"""Lua artifact locking stays portable without weakening mutual exclusion."""

from contextlib import ExitStack, contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import file_lock


def _load_lua():
    path = Path(__file__).with_name("test_lua.py")
    spec = importlib.util.spec_from_file_location("_pcc_lua_lock_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lua_helpers_import_without_fcntl(monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    lua = _load_lua()
    assert lua.LUA_CPP_ARGS == ("-DLUA_USE_JUMPTABLE=0", "-DLUA_NOBUILTIN")
    assert callable(lua._artifact_lock)
    assert callable(lua._release_artifact_lock)


def test_lua_artifact_lock_uses_shared_owner_and_closes_once(monkeypatch, tmp_path):
    lua = _load_lua()
    events = []

    @contextmanager
    def owner(path):
        events.append(("acquire", Path(path)))
        try:
            yield object()
        finally:
            events.append(("release", Path(path)))

    monkeypatch.setattr(lua, "_test_cache_root", lambda: str(tmp_path))
    monkeypatch.setattr(lua, "exclusive_file_lock", owner)
    lock = lua._artifact_lock("shared")
    assert events == [("acquire", tmp_path / "shared.lock")]
    lua._release_artifact_lock(lock)
    lua._release_artifact_lock(lock)
    assert events == [
        ("acquire", tmp_path / "shared.lock"),
        ("release", tmp_path / "shared.lock"),
    ]


def test_lua_artifact_lock_closes_stack_when_acquisition_raises(monkeypatch, tmp_path):
    lua = _load_lua()
    closed = []
    error = OSError("lock acquisition failed")

    class TrackedStack(ExitStack):
        def __init__(self):
            super().__init__()
            self.callback(closed.append, "closed")

    @contextmanager
    def unavailable(_path):
        raise error
        yield  # pragma: no cover - defines the context-manager protocol

    monkeypatch.setattr(lua, "_test_cache_root", lambda: str(tmp_path))
    monkeypatch.setattr(lua, "ExitStack", TrackedStack)
    monkeypatch.setattr(lua, "exclusive_file_lock", unavailable)
    with pytest.raises(OSError) as raised:
        lua._artifact_lock("shared")
    assert raised.value is error
    assert closed == ["closed"]


def test_lua_artifact_release_closes_file_when_unlock_raises(monkeypatch, tmp_path):
    lua = _load_lua()
    streams = []
    error = OSError("lock release failed")

    @contextmanager
    def owner(path):
        with open(path, "a+b") as stream:
            streams.append(stream)
            yield stream
            raise error

    monkeypatch.setattr(lua, "_test_cache_root", lambda: str(tmp_path))
    monkeypatch.setattr(lua, "exclusive_file_lock", owner)
    lock = lua._artifact_lock("shared")
    assert not streams[0].closed
    with pytest.raises(OSError) as raised:
        lua._release_artifact_lock(lock)
    assert raised.value is error
    assert streams[0].closed
    lua._release_artifact_lock(lock)


def test_lua_artifact_lock_excludes_other_process_then_releases(monkeypatch, tmp_path):
    lua = _load_lua()
    monkeypatch.setattr(lua, "_test_cache_root", lambda: str(tmp_path))
    lock_path = tmp_path / "shared.lock"
    repo = Path(__file__).resolve().parents[2]
    code = (
        "from scripts.file_lock import try_exclusive_file_lock\n"
        "import sys\n"
        "with try_exclusive_file_lock(sys.argv[1]) as acquired:\n"
        "    print(int(acquired))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)

    def probe():
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(lock_path)],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    lock = lua._artifact_lock("shared")
    try:
        assert probe() == "0"
    finally:
        lua._release_artifact_lock(lock)
    assert probe() == "1"


def test_lua_artifact_lock_uses_windows_owner_without_fcntl(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    lua = _load_lua()
    calls = []

    def locking(fd, operation, length):
        assert os.fstat(fd).st_size >= 1
        calls.append((fd, operation, length))

    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3, locking=locking),
    )
    # Replace this owner's platform view, not the process-wide os.name.
    monkeypatch.setattr(file_lock, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(lua, "_test_cache_root", lambda: str(tmp_path))
    lock = lua._artifact_lock("shared")
    assert len(calls) == 1 and calls[0][1:] == (1, 1)
    fd = calls[0][0]
    lua._release_artifact_lock(lock)
    assert calls == [(fd, 1, 1), (fd, 3, 1)]
    with pytest.raises(OSError):
        os.fstat(fd)
