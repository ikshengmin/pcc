"""Owned POSIX file locking for native Python's standard fcntl module."""
from __future__ import annotations

import sys
from pcc.extern import c_obj, extern

if sys.platform == "win32":
    raise ImportError("No module named 'fcntl'")

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8
_native_flock = extern("py_fcntl_flock", (c_obj, c_obj, c_obj), c_obj)


def flock(fd, operation):
    """Lock the open file description; close/process exit releases its lock."""
    descriptor = fd
    if not isinstance(descriptor, int):
        try:
            method = fd.fileno
        except AttributeError:
            raise TypeError("argument must be an int, or have a fileno() method")
        descriptor = method()
        if not isinstance(descriptor, int):
            raise TypeError("fileno() returned a non-integer")
    # Keep the original object alive through the raw operation. A temporary
    # open(path) passed directly to flock must not close after fileno returns.
    error = _native_flock(descriptor, operation, fd)
    if error is not None:
        raise error
    return None


def fcntl(fd, cmd: int, arg=0):
    raise NotImplementedError("fcntl.fcntl awaits an F_* extern binding")


def ioctl(fd, request: int, arg=0, mutate_flag: bool = True):
    raise NotImplementedError("fcntl.ioctl awaits a TIOC* extern binding")
