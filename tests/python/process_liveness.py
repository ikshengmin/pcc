"""Non-destructive liveness checks for owned fixture process IDs.

Windows os.kill(pid, 0) terminates the target with exit code zero. A process
handle with only SYNCHRONIZE rights can instead be polled without signalling.
This is a point-in-time PID check, not protection against subsequent PID reuse.
"""
import errno
import os


def _require_windows_process_running(pid, api):
    if type(pid) is not int or not 0 < pid <= 0xffffffff:
        raise ValueError("expected a positive Windows process ID")
    try:
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    except OSError as error:
        if getattr(error, "winerror", None) == 87:  # no such positive PID
            raise ProcessLookupError(errno.ESRCH, "process is not running", pid) from error
        raise  # Access denied/unknown failure is not proof of retirement.
    try:
        state = api.WaitForSingleObject(handle, 0)
        if state == api.WAIT_TIMEOUT:
            return
        if state == api.WAIT_OBJECT_0:
            raise ProcessLookupError(errno.ESRCH, "process has exited", pid)
        raise OSError("unexpected process wait result: " + str(state))
    finally:
        api.CloseHandle(handle)


def require_process_running(pid):
    """Return for a live process; raise ProcessLookupError only for absence.

POSIX keeps the original signal-zero permission/error semantics. Windows
uses a zero-timeout process wait and never requests termination rights.
"""
    if os.name != "nt":
        os.kill(pid, 0)
        return
    import _winapi
    _require_windows_process_running(pid, _winapi)
