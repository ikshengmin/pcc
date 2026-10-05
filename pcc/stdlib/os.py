"""pcc.stdlib.os — skeleton replacement for ``os`` / ``os.path``.

Minimal surface: env, getcwd, listdir, exists, the ``os.path``
helpers pcc uses (join, basename, dirname, exists). Heavy lifting
delegates to extern libc.
"""

from __future__ import annotations

from pcc.unsafe import (
    load_i32, stack_alloc, store_i32, strlen, sync_file,
)

from pcc.extern import (
    c_int, c_int64, c_obj, c_ptr, c_rawptr, c_str, c_void, extern,
)

_getenv = extern("getenv", (c_str,), c_rawptr)
_setenv = extern("setenv", (c_str, c_str, c_int), c_int)
_getcwd = extern("getcwd", (c_str, c_int64), c_rawptr)
_access = extern("pcc_platform_access", (c_str, c_int64), c_int64)
_getpid = extern("pcc_platform_getpid", (), c_int64)
_fd_integer = extern("py_int_to_i64", (c_obj, c_rawptr), c_int64)
_errno_message = extern("pcc_errno_message_into", (c_int, c_rawptr, c_int64), c_int)
_new_text = extern("py_str_new", (c_rawptr, c_int64), c_obj)
_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
_error_pending = extern("py_err_occurred", (), c_int64)
_walk_scan = extern("py_os_walk_scan", (c_obj, c_obj, c_obj), c_obj)
_walk_prefix = extern("py_os_walk_prefix", (c_obj,), c_obj)
_path_islink_result = extern("py_os_path_islink_result", (c_obj,), c_obj)


# POSIX file-access constants.
F_OK: int = 0
R_OK: int = 4
W_OK: int = 2
X_OK: int = 1

sep: str = "/"
linesep: str = "\n"


def getpid() -> int:
    # The portable owned platform ABI selects the target process primitive.
    # Keep this live on every call, including after a fork.
    return _getpid()


def fsync(fd):
    """Synchronize an integer descriptor or an object exposing fileno()."""
    if isinstance(fd, bool):
        import warnings
        warnings.warn(
            "bool is used as a file descriptor", RuntimeWarning, stacklevel=2,
        )
    descriptor = fd
    if not isinstance(descriptor, int):
        try:
            method = fd.fileno
        except AttributeError:
            raise TypeError("argument must be an int, or have a fileno() method.") from None
        descriptor = method()
        if not isinstance(descriptor, int):
            raise TypeError(type(fd).__name__ + ".fileno() must return an int, not "
                            + type(descriptor).__name__)
    # Convert the integer payload directly, without invoking __index__ or
    # overloaded comparisons. Validate before the platform's C-int narrowing.
    overflow = stack_alloc(4)
    store_i32(overflow, 0, 0)
    number = _fd_integer(descriptor, overflow)
    if load_i32(overflow, 0) or number < -2147483648 or number > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    if number < 0:
        raise ValueError("file descriptor cannot be a negative integer (" + str(number) + ")")
    # fd and descriptor remain ordinary rooted locals until this call exits;
    # a temporary file object must not close between fileno and the syscall.
    status = sync_file(number)
    while status == -4:
        _thread_safepoint()
        if _error_pending():
            raise
        status = sync_file(number)
    if status < 0:
        buffer = stack_alloc(256)
        result = _errno_message(-status, buffer, 256)
        if result < 0:
            raise OSError(-status, "file synchronization failed")
        message = _new_text(buffer, strlen(buffer))
        if message is None:
            raise MemoryError("file synchronization error message allocation failed")
        raise OSError(-status, message)
    return None


def getenv(key: str, default: str = "") -> str:
    # In the self-host runtime, ``_getenv`` returns either a valid
    # C-string pointer or NULL. The pcc→C-string marshalling
    # converts the Python str to a NUL-terminated buffer, and the
    # return value is marshalled back through py_str_new when the
    # pointer is non-NULL. Both conversions are P6C.1 FFI work.
    raise NotImplementedError(
        "os.getenv needs the P6C.1 extern string-return marshalling"
    )


def exists(path: str) -> bool:
    """True if ``path`` exists on disk, via ``access(path, F_OK)``.

    The owned platform ABI follows symlinks and returns failure as a status,
    so a missing or inaccessible path is False rather than an exception.
    """
    return _access(path, F_OK) == 0


class PathLike:
    """Base protocol for objects that provide a filesystem path."""

    def __fspath__(self):
        raise NotImplementedError("PathLike subclasses must define __fspath__")


def fspath(path):
    """Return the filesystem representation of a path-like object."""
    if isinstance(path, (str, bytes)):
        return path
    # Special methods belong to the type, not an instance's attribute dict.
    # Catch only the missing-method lookup: AttributeError raised *inside*
    # __fspath__ is the provider's error and must reach the caller unchanged.
    try:
        path_repr = type(path).__fspath__
    except AttributeError:
        raise TypeError("expected str, bytes or os.PathLike object")
    result = path_repr(path)
    if not isinstance(result, (str, bytes)):
        raise TypeError("__fspath__() must return str or bytes")
    return result


def walk(top, topdown=True, onerror=None, followlinks=False):
    """Yield owned directory rows, retaining the caller's mutable dirnames."""
    # This is an ordinary generator: path conversion and directory access
    # start at first next(), and normal generator frames own all suspended
    # values. No directory stream survives the synchronous scan boundary.
    pending = [(False, fspath(top))]
    while pending:
        emit, value = pending.pop()
        if emit:
            yield value
            continue
        root = value
        try:
            scanned = _walk_scan(root, topdown, followlinks)
            if isinstance(scanned, BaseException):
                raise scanned
            directories, files, children = scanned
        except OSError as error:
            if onerror is not None:
                onerror(error)
            continue
        row = root, directories, files
        if topdown:
            yield row
            if directories:
                prefix = _walk_prefix(root)
                if isinstance(prefix, BaseException):
                    raise prefix
                # Read the identical list exposed in the row. Recheck links
                # only after the caller's pruning, reordering and replacement.
                for child in reversed(directories):
                    child_path = prefix + child
                    if followlinks:
                        pending.append((False, child_path))
                    else:
                        linked = _path_islink_result(child_path)
                        if isinstance(linked, BaseException):
                            raise linked
                        if not linked:
                            pending.append((False, child_path))
        else:
            pending.append((True, row))
            for child in reversed(children):
                pending.append((False, child))


class _path:
    """``os.path`` namespace."""

    @staticmethod
    def join(*parts: str) -> str:
        if not parts:
            return ""
        out = parts[0]
        for p in parts[1:]:
            if not p:
                continue
            if p.startswith("/"):
                out = p
                continue
            if out.endswith("/"):
                out = out + p
            else:
                out = out + "/" + p
        return out

    @staticmethod
    def basename(p: str) -> str:
        i = len(p) - 1
        while i >= 0 and p[i] != "/":
            i = i - 1
        return p[i + 1 :]

    @staticmethod
    def dirname(p: str) -> str:
        i = len(p) - 1
        while i >= 0 and p[i] != "/":
            i = i - 1
        if i < 0:
            return ""
        head = p[: i + 1]
        # CPython strips trailing slashes unless the head is all slashes, so
        # dirname("/a//b") is "/a" and dirname("//") stays "//".
        if head != "/" * len(head):
            head = head.rstrip("/")
        return head

    @staticmethod
    def exists(p: str) -> bool:
        return exists(p)

    @staticmethod
    def splitext(p: str):
        slash = -1
        dot = -1
        i = 0
        while i < len(p):
            if p[i] == "/":
                slash = i
                dot = -1
            elif p[i] == ".":
                dot = i
            i += 1
        if dot <= slash + 1:
            return (p, "")
        return (p[:dot], p[dot:])

    @staticmethod
    def normpath(p: str) -> str:
        absolute = p.startswith("/")
        parts = []
        for part in p.split("/"):
            if part == "" or part == ".":
                continue
            if part == "..":
                if parts and parts[-1] != "..":
                    parts.pop()
                elif not absolute:
                    parts.append(part)
                continue
            parts.append(part)
        out = "/".join(parts)
        if absolute:
            out = "/" + out
        if out == "":
            return "/" if absolute else "."
        return out

    @staticmethod
    def isabs(p: str) -> bool:
        return p.startswith("/")

    @staticmethod
    def commonpath(paths) -> str:
        if not paths:
            raise ValueError("commonpath() arg is an empty sequence")
        split_paths = [p.split("/") for p in paths]
        prefix = []
        i = 0
        while True:
            if i >= len(split_paths[0]):
                break
            part = split_paths[0][i]
            for pieces in split_paths[1:]:
                if i >= len(pieces) or pieces[i] != part:
                    return "/".join(prefix)
            prefix.append(part)
            i += 1
        return "/".join(prefix)


path = _path()
