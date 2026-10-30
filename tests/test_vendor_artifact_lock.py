"""Postgres and Readline share portable, mutually exclusive build locks."""

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import file_lock


_VENDORS = ("postgres", "readline")


def _load_vendor(vendor):
    path = Path(__file__).parent / "integration" / ("test_" + vendor + ".py")
    spec = importlib.util.spec_from_file_location("_pcc_vendor_lock_" + vendor, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock(module, vendor, path, monkeypatch):
    if vendor == "postgres":
        return module._file_lock(path)
    monkeypatch.setattr(module, "READLINE_CONFIG_LOCK", path)
    return module._readline_build_lock()


@pytest.mark.parametrize("vendor", _VENDORS)
def test_vendor_helpers_import_without_fcntl(monkeypatch, vendor):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    module = _load_vendor(vendor)
    assert callable(getattr(module, "_" + vendor + "_build_lock"))


@pytest.mark.parametrize("vendor", _VENDORS)
def test_vendor_lock_uses_shared_owner_and_preserves_yield(monkeypatch, tmp_path, vendor):
    module = _load_vendor(vendor)
    path = tmp_path / "provision.lock"
    events = []

    @contextmanager
    def owner(lock_path):
        events.append(("acquire", lock_path))
        try:
            yield object()
        finally:
            events.append(("release", lock_path))

    monkeypatch.setattr(module, "exclusive_file_lock", owner)
    with _lock(module, vendor, path, monkeypatch) as value:
        assert value is None
        assert events == [("acquire", path)]
    assert events == [("acquire", path), ("release", path)]


def test_postgres_build_lock_keeps_its_config_path(monkeypatch):
    postgres = _load_vendor("postgres")
    events = []

    @contextmanager
    def owner(path):
        events.append(("acquire", path))
        try:
            yield
        finally:
            events.append(("release", path))

    monkeypatch.setattr(postgres, "_file_lock", owner)
    with postgres._postgres_build_lock() as value:
        assert value is None
        assert events == [("acquire", postgres.POSTGRES_CONFIG_LOCK)]
    assert events == [
        ("acquire", postgres.POSTGRES_CONFIG_LOCK),
        ("release", postgres.POSTGRES_CONFIG_LOCK),
    ]


def test_postgres_and_readline_keep_the_same_readline_lock_path():
    postgres = _load_vendor("postgres")
    readline = _load_vendor("readline")
    assert postgres.READLINE_BUILD_LOCK == readline.READLINE_CONFIG_LOCK


@pytest.mark.parametrize("vendor", _VENDORS)
def test_vendor_lock_propagates_acquisition_failure(monkeypatch, tmp_path, vendor):
    module = _load_vendor(vendor)
    error = OSError("lock acquisition failed")

    @contextmanager
    def unavailable(_path):
        raise error
        yield  # pragma: no cover - defines the context-manager protocol

    monkeypatch.setattr(module, "exclusive_file_lock", unavailable)
    with pytest.raises(OSError) as raised:
        with _lock(module, vendor, tmp_path / "provision.lock", monkeypatch):
            pytest.fail("body entered without owning the lock")
    assert raised.value is error


@pytest.mark.parametrize("vendor", _VENDORS)
@pytest.mark.parametrize("raise_in_body", (False, True))
def test_vendor_lock_excludes_other_process_then_releases(monkeypatch, tmp_path, vendor, raise_in_body):
    module = _load_vendor(vendor)
    path = tmp_path / "nested" / "provision.lock"
    repo = Path(__file__).resolve().parents[1]
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
            [sys.executable, "-B", "-c", code, str(path)],
            cwd=repo, env=env, capture_output=True, text=True,
            timeout=10, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    error = RuntimeError("body failure")
    try:
        with _lock(module, vendor, path, monkeypatch):
            assert probe() == "0"
            if raise_in_body:
                raise error
    except RuntimeError as raised:
        assert raise_in_body and raised is error
    assert probe() == "1"


@pytest.mark.parametrize("vendor", _VENDORS)
def test_vendor_lock_uses_windows_owner_without_fcntl(monkeypatch, tmp_path, vendor):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    module = _load_vendor(vendor)
    calls = []

    def locking(fd, operation, length):
        assert os.fstat(fd).st_size >= 1
        calls.append((fd, operation, length))

    monkeypatch.setitem(
        sys.modules, "msvcrt",
        SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3, locking=locking),
    )
    # Change only the shared owner's view, not the process-wide platform.
    monkeypatch.setattr(file_lock, "os", SimpleNamespace(name="nt"))
    with _lock(module, vendor, tmp_path / "provision.lock", monkeypatch):
        assert len(calls) == 1 and calls[0][1:] == (1, 1)
        fd = calls[0][0]
    assert calls == [(fd, 1, 1), (fd, 3, 1)]
    with pytest.raises(OSError):
        os.fstat(fd)
