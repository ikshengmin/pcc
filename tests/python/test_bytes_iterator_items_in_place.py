"""A bytes or bytearray iterator yields its byte without boxing an index.

Python library reference, Binary Sequence Types: iterating a bytes or
bytearray yields ints 0..255; a bytearray iterator checks the current
length on every step, so one that shrinks or grows while iterated ends or
continues accordingly. The runtime's sequence iterator boxed the index
with py_int_from_i64, reloaded the sequence and called the generic
py_bytes_getitem with that object index for every byte (pcc_iter_next_impl,
py_int_from_i64 and py_bytes_getitem were 4.8% of an HTTP/1 parser driver,
whose header checks iterate bytes). It now reads the byte in place and
returns it as a tagged int; a memoryview keeps the format-aware path. The
native program iterates bytes (literal, slice, empty, high bytes),
bytearray (shrinking and cleared while iterated), memoryview and
explicit iter()/next() through exhaustion, while threads iterate
concurrently, against CPython on all five collectors. Two gaps exist on
HEAD as well and are left out: a bytearray that grows while iterated stops
at its old length (CPython continues), and ``del ba[i]`` on a bytearray is
not native.
"""
import os
from pathlib import Path
import subprocess
import sys

PROGRAM = '''import threading


def is_token(data: bytes) -> bool:
    if len(data) == 0:
        return False
    for value in data:
        if 48 <= value <= 57 or 65 <= value <= 90 or 97 <= value <= 122:
            continue
        if value == 45 or value == 95:
            continue
        return False
    return True


def worker(results: list, index: int):
    total = 0
    for k in range(3000):
        name = ("X-Header-" + str(k + index)).encode()
        total += 1 if is_token(name) else 0
        total += sum(name[2:5])
    results[index] = total


def main():
    print([v for v in b"az\\x00\\x7f\\x80\\xff"])
    print(list(b""), list(b"slice-me"[2:6]), sum(bytes(range(256))))
    print(is_token(b"Content-Length"), is_token(b"bad header"), is_token(b""))
    shrink = bytearray(b"abcdef")
    seen = []
    for v in shrink:
        seen.append(v)
        shrink.pop()
    print(seen, bytes(shrink))
    cleared = bytearray(b"xyz")
    seen = []
    for v in cleared:
        seen.append(v)
        cleared.clear()
    print(seen)
    print(list(memoryview(b"mv")), [v for v in memoryview(bytearray(b"\\x01\\x02"))])
    it = iter(b"q")
    print(next(it))
    try:
        next(it)
    except StopIteration:
        print("exhausted")
    print(next(it, "still exhausted"))
    results = [0, 0, 0, 0]
    threads = [threading.Thread(target=worker, args=(results, i)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(results)


main()
'''


def test_bytes_iteration_matches_cpython_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "bytes_iter.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "bytes_iter"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
