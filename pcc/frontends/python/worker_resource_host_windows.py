"""CPython-only Windows worker RSS reader; never part of the native closure."""

import os


def _open_worker_resource_fd(path, _flags):
    """Return one noninheritable read fd that permits atomic replacement.

    Before open_osfhandle succeeds we own the HANDLE; afterward the fd owns it.
    This callback is used only by builtin open, which owns the returned fd and
    closes it on stream-construction failure or normal context-manager exit.
    """
    import ctypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int32
    # GENERIC_READ; FILE_SHARE_READ | WRITE | DELETE; NULL security attributes
    # (noninheritable); OPEN_EXISTING; FILE_ATTRIBUTE_NORMAL; no template.
    handle = create_file(path, 0x80000000, 0x00000007, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        # Preserve the conversion exception even if best-effort cleanup fails.
        try:
            close_handle(handle)
        except BaseException:
            pass
        raise


def open_worker_resource(path):
    return open(path, "r", encoding="utf-8", opener=_open_worker_resource_fd)
