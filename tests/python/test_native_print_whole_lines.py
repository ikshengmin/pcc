"""A native print() call writes its line in one piece, in CPython's order.

print wrote every separator, digit and repr byte with its own write(), so two
threads printing at once interleaved inside a line ("workerworker 0"), which
CPython's GIL never shows, and an int cost one system call per digit. Each
call now collects its output and writes it once. Text already produced is
written out before a __str__ or __repr__ runs, so a print nested in that code
lands where CPython puts it. Container reprs read the container again for
every element and hold the element, because the element's __repr__ may
mutate the container.
"""
from pathlib import Path
import subprocess
import sys

THREADS_PROGRAM = '''from threading import Thread


def work(n) -> None:
    i = 0
    while i < 300:
        print("worker", n, "line", i, "of", 300)
        i += 1


def main() -> None:
    threads = []
    n = 0
    while n < 4:
        threads.append(Thread(target=work, args=(n,)))
        n += 1
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    print("done")


main()
'''

ORDER_PROGRAM = '''class Loud:
    def __str__(self):
        print("inside str")
        return "loud"

    def __repr__(self):
        print("inside repr")
        return "Loud()"


class Grows:
    def __init__(self, owner):
        self.owner = owner

    def __repr__(self):
        if len(self.owner) < 40:
            self.owner.extend(range(30))
        return "G"


class Drops:
    def __init__(self, owner):
        self.owner = owner

    def __repr__(self):
        self.owner.clear()
        return "D"


def main():
    print("before", Loud(), "after")
    print([1, Loud(), "x"])
    print(repr("it's \\"q\\"\\n\\t\\x01"), -12345, 0, 7 ** 30, -2.5, 1e300)
    print(b"\\x00a'\\xff", bytearray(b"z\\n"), (1,), {"k": "v"}, {3})
    grows = [1]
    grows.append(Grows(grows))
    print(grows)
    table = {}
    table["a"] = Drops(table)
    table["b"] = 2
    print(table)
    print("sep", "end", sep="|", end="!\\n")


main()
'''


def _compile(tmp_path: Path, archive, name: str, program: str):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / (name + ".py")
    source.write_text(program, encoding="utf-8")
    expected = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=60
    )
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / name
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(archive))
    return binary, expected.stdout


def test_concurrent_prints_keep_their_lines_whole(tmp_path: Path, pcc_runtime_archive):
    binary, expected = _compile(tmp_path, pcc_runtime_archive, "whole_lines", THREADS_PROGRAM)
    want = sorted(expected.splitlines())
    for _ in range(5):
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, ran.stderr[-2000:]
        assert sorted(ran.stdout.splitlines()) == want


def test_print_order_and_container_reprs_match_cpython(tmp_path: Path, pcc_runtime_archive):
    binary, expected = _compile(tmp_path, pcc_runtime_archive, "print_order", ORDER_PROGRAM)
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr[-2000:]
    assert ran.stdout == expected
