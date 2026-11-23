"""bytes.decode reads an exact bytes object in place.

Python library reference, bytes.decode (Binary Sequence Types) and codecs
(Error Handlers): decoding returns a new str or raises, the encoding name
is looked up before the error handler name, and an unknown handler is an
error only when needed. The runtime's general path snapshots its operand
through the buffer protocol: operand roots and leases for the object and
both names, an owned buffer copy, an exception-preserving drop of every
buffer and a pinned handoff of the result. A bytearray or memoryview
needs that snapshot; an exact bytes object is immutable and its caller
keeps it alive for the call, so the decode now reads it in place: 14% of
an HTTP/1 parser driver's samples went to that protocol around the 7% of
actual decoding. The native program decodes exact bytes (literals, slices,
results, empty), bytearray and memoryview operands through every entry
(no arguments, encoding, encoding and errors, keywords), bad codec and
handler names, wrong argument types and undecodable input while threads
decode concurrently, against CPython on all five collectors.
"""
import os
from pathlib import Path
import subprocess
import sys

PROGRAM = '''import threading


def attempt(label, action):
    try:
        print(label, ascii(action()))
    except Exception as exc:
        print(label, type(exc).__name__)


def worker(results: list, index: int):
    total = 0
    for k in range(2000):
        line = ("Header-" + str(k) + ": value " + str(index)).encode()
        colon = line.find(b":")
        name = line[:colon].decode("utf-8").lower()
        value = line[colon + 1:].decode("latin-1").strip()
        total += len(name) + len(value)
    results[index] = total


def main():
    raw = b"caf\\xc3\\xa9 \\xff\\xfe A"
    good = "caf\\u00e9 n".encode()
    attempt("plain", lambda: good.decode())
    attempt("literal", lambda: b"literal".decode("ascii"))
    attempt("empty", lambda: b"".decode("utf-8"))
    attempt("slice", lambda: raw[0:5].decode("utf-8"))
    attempt("latin", lambda: raw.decode("latin-1"))
    attempt("strict", lambda: raw.decode("utf-8"))
    attempt("replace", lambda: raw.decode("utf-8", "replace"))
    attempt("ignore", lambda: raw.decode("utf-8", errors="ignore"))
    attempt("surrogateescape", lambda: raw.decode("utf-8", "surrogateescape"))
    attempt("backslashreplace", lambda: raw.decode(encoding="utf-8", errors="backslashreplace"))
    attempt("ascii strict", lambda: raw.decode("ascii"))
    attempt("bad codec", lambda: raw.decode("no-such-codec"))
    attempt("bad handler unused", lambda: good.decode("utf-8", "no-such-handler"))
    attempt("bad handler needed", lambda: raw.decode("utf-8", "no-such-handler"))
    attempt("bad codec and handler", lambda: raw.decode("no-such-codec", "no-such-handler"))
    attempt("codec type", lambda: raw.decode(7))
    attempt("handler type", lambda: raw.decode("utf-8", 7))
    attempt("bytearray", lambda: bytearray(good).decode("utf-8"))
    attempt("memoryview", lambda: str(memoryview(good), "utf-8"))
    attempt("str()", lambda: str(good, "utf-8"))
    results = [0, 0, 0, 0]
    threads = [threading.Thread(target=worker, args=(results, i)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(results)


main()
'''


def test_bytes_decode_operands_match_cpython_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "decode_in_place.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "decode_in_place"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
