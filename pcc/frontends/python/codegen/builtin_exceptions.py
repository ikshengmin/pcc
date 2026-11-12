"""Shared builtin exception tag metadata for Python lowering."""

from __future__ import annotations

BUILTIN_EXC_TAG = {
    "BaseException": 0,
    "Exception": 1,
    "ValueError": 2,
    "TypeError": 3,
    "KeyError": 4,
    "IndexError": 5,
    "AttributeError": 6,
    "SyntaxError": 49,
    "RuntimeError": 7,
    "StopIteration": 8,
    "ZeroDivisionError": 9,
    "NameError": 10,
    "NotImplementedError": 11,
    "ArithmeticError": 12,
    "LookupError": 13,
    "OSError": 14,
    "IOError": 14,
    "EnvironmentError": 14,
    "OverflowError": 15,
    "AssertionError": 16,
    "ReferenceError": 18,
    "MemoryError": 19,
    "FileNotFoundError": 34,
    "FileExistsError": 35,
    "IsADirectoryError": 37,
    "NotADirectoryError": 38,
    "PermissionError": 36,
    "BrokenPipeError": 45,
    "ConnectionError": 44,
    "ConnectionAbortedError": 46,
    "ConnectionRefusedError": 47,
    "ConnectionResetError": 48,
    "BlockingIOError": 43,
    "ChildProcessError": 40,
    "InterruptedError": 42,
    "TimeoutError": 41,
    # Every builtin exception has its own tag and runtime parent, so a
    # handler for a subclass (``except FileNotFoundError``) no longer catches
    # its siblings and ``type(e).__name__`` names the real class.
    "UnicodeError": 57,
    "UnicodeDecodeError": 58,
    "UnicodeEncodeError": 59,
    "RecursionError": 56,
    "ImportError": 20,
    "ModuleNotFoundError": 21,
    "EOFError": 52,
    "SystemExit": 53,
    "KeyboardInterrupt": 54,
    "GeneratorExit": 55,
    "StopAsyncIteration": 17,
    "ProcessLookupError": 39,
    "IndentationError": 50,
    "TabError": 51,
    "UnicodeTranslateError": 60,
    "FloatingPointError": 61,
    "BufferError": 62,
    "UnboundLocalError": 63,
    "SystemError": 64,
    # Each warning class has its own identity. The runtime parent table makes
    # the subclasses match Warning without making sibling handlers match.
    "Warning": 22,
    "UserWarning": 23,
    "DeprecationWarning": 24,
    "PendingDeprecationWarning": 25,
    "SyntaxWarning": 26,
    "RuntimeWarning": 27,
    "FutureWarning": 28,
    "ImportWarning": 29,
    "UnicodeWarning": 30,
    "BytesWarning": 31,
    "EncodingWarning": 32,
    "ResourceWarning": 33,
}


def builtin_exc_tag_or_missing(name: str) -> int:
    if name in BUILTIN_EXC_TAG:
        return BUILTIN_EXC_TAG[name]
    return -1


def builtin_exception_uses_module_lookup(host, name: str) -> bool:
    """Use the live namespace for ordinary Python globals with builtin fallback.

    Local storage still has its own UnboundLocalError contract. Raw ABI,
    foreign and value-payload globals do not publish ordinary module objects.
    """
    if name not in BUILTIN_EXC_TAG:
        return False
    entry = host._module_globals.get(name)
    if entry is None:
        return False
    local = host.env.get(name)
    if local is not None and local[0] is not entry[0]:
        return False
    if (getattr(host, "_freestanding_module", False)
            or getattr(host, "_runtime_port_module", False)
            or getattr(host, "_module_has_c_abi_export", False)
            or getattr(host, "_cpy_module_flags", {}).get(name, False)
            or host._is_valueclass_payload_type(entry[1])):
        return False
    return True
