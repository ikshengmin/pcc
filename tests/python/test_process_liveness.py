"""Platform contract tests plus a real, bounded, owned child lifecycle."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.python import process_liveness as probe


class FakeWindows:
    WAIT_TIMEOUT = 258
    WAIT_OBJECT_0 = 0

    def __init__(self, state=258, open_error=None, wait_error=None):
        self.state, self.open_error, self.wait_error = state, open_error, wait_error
        self.calls = []

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("open", access, inherit, pid))
        if self.open_error:
            raise self.open_error
        return 123

    def WaitForSingleObject(self, handle, timeout):
        self.calls.append(("wait", handle, timeout))
        if self.wait_error:
            raise self.wait_error
        return self.state

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))


def test_windows_live_probe_only_requests_synchronize_and_never_signals():
    api = FakeWindows()
    assert probe._require_windows_process_running(42, api) is None
    assert api.calls == [("open", 0x100000, False, 42), ("wait", 123, 0), ("close", 123)]


def test_windows_exited_handle_is_absent_and_closed():
    api = FakeWindows(state=0)
    with pytest.raises(ProcessLookupError):
        probe._require_windows_process_running(42, api)
    assert api.calls[-1] == ("close", 123)


@pytest.mark.parametrize("code", [87, 5, 6])
def test_windows_open_errors_do_not_treat_denial_as_retirement(code):
    error = OSError("open failed")
    error.winerror = code
    api = FakeWindows(open_error=error)
    with pytest.raises(OSError) as caught:
        probe._require_windows_process_running(42, api)
    if code == 87:
        assert isinstance(caught.value, ProcessLookupError)
        assert caught.value.__cause__ is error
    else:
        assert caught.value is error
    assert api.calls == [("open", 0x100000, False, 42)]


def test_windows_wait_error_propagates_and_closes_handle():
    error = OSError("wait failed")
    api = FakeWindows(wait_error=error)
    with pytest.raises(OSError) as caught:
        probe._require_windows_process_running(42, api)
    assert caught.value is error
    assert api.calls[-1] == ("close", 123)


@pytest.mark.parametrize("state", [128, 0xffffffff, 999])
def test_windows_unexpected_wait_result_fails_closed(state):
    api = FakeWindows(state=state)
    with pytest.raises(OSError, match="unexpected process wait result"):
        probe._require_windows_process_running(42, api)
    assert api.calls[-1] == ("close", 123)


@pytest.mark.parametrize("pid", [0, -1, True, 0x100000000])
def test_windows_invalid_ids_do_not_enter_api(pid):
    api = FakeWindows()
    with pytest.raises(ValueError):
        probe._require_windows_process_running(pid, api)
    assert api.calls == []


def test_posix_preserves_signal_zero_and_original_exception(monkeypatch):
    calls = []
    failure = PermissionError("denied")
    def kill(pid, signal):
        calls.append((pid, signal))
        raise failure
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="posix", kill=kill))
    with pytest.raises(PermissionError) as caught:
        probe.require_process_running(42)
    assert caught.value is failure and calls == [(42, 0)]


def test_real_owned_child_survives_probes_and_then_retires():
    child = subprocess.Popen([sys.executable, "-c", "import sys;sys.stdin.readline()"], stdin=subprocess.PIPE)
    try:
        probe.require_process_running(child.pid)
        probe.require_process_running(child.pid)
        assert child.poll() is None
        child.communicate(b"finish\n", timeout=5)
        assert child.returncode == 0
        with pytest.raises(ProcessLookupError):
            probe.require_process_running(child.pid)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
