"""Bytes literals are static immortal objects, as string literals are.

Python language reference 2.4.1 (bytes literals) and 3.1 (an immutable
object's identity is implementation-defined for equal literals): a
``bytes`` literal names an immutable value; CPython keeps it in the code
object's constants. pcc built a new bytes object with ``py_bytes_new`` on
every evaluation, then published, leased and released it: the gateway's
request parser evaluates b"\\r\\n", b" ", b":" and b"HTTP/1.1" per request.
A literal now lowers to a module-static object whose header carries
``PY_FLAG_IMMORTAL``, pooled per value and registered at module start like
string, float and big-int literals, so no allocation or GC protocol
surrounds it. The native program uses literals as call operands (one
followed by a raising operand), method receivers and arguments, comparison
and containment operands, dict keys, defaults, concatenation inputs and
thread arguments while every collector runs. Its parameters are annotated as
the gateway's are: a method call on an untyped bytes receiver
(``block.split(...)`` with ``block`` unannotated) raises AttributeError on
HEAD as well, a separate gap.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

PROGRAM = '''import threading


def explode(n):
    if n > 2:
        raise ValueError("boom " + str(n))
    return n


def pair(left: bytes, right: bytes) -> bytes:
    return left + b"|" + right


def head(block: bytes, sep: bytes = b"\\r\\n") -> bytes:
    lines: list[bytes] = block.split(sep)
    first: bytes = lines[0]
    parts: list[bytes] = first.split(b" ")
    if b"\\t" in first or len(parts) != 3:
        return b"bad"
    if parts[2] != b"HTTP/1.1" and parts[2] != b"HTTP/1.0":
        return b"version"
    return parts[1]


def churn(n):
    out = []
    i = 0
    while i < n:
        out.append([i, str(i).encode(), {b"k": i}])
        i += 1
    return len(out)


def worker(results: list, index: int):
    total = 0
    for k in range(3000):
        line: bytes = b"Host: example" if k % 2 == 0 else b"Accept: */*"
        colon = line.find(b":")
        rest: bytes = line[colon + 1:]
        total += colon + len(rest.strip(b" "))
    results[index] = total


def main():
    lines = []
    block = b"GET /index HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n"
    for n in range(5):
        try:
            lines.append(pair(b"alpha", b"beta" + str(explode(n)).encode()))
        except ValueError as exc:
            lines.append(b"caught " + str(exc).encode())
        lines.append(head(block))
        lines.append(head(b"GET / HTTP/2\\r\\n"))
        churn(300)
    table = {b"a": b"one", b"b": b"two"}
    lines.append(table.get(b"a", b"missing") + table.get(b"zz", b"missing"))
    items = [b"same", b"same", b"other"]
    lines.append(str(items[0] == items[1]).encode() + b" " + str(len(set(items))).encode())
    lines.append(str(hash(b"xyz") == hash(b"x" + b"yz")).encode())
    lines.append(bytes([value for value in b"abc"]) + b"!")
    lines.append(b"-".join([b"p", b"q", b"r"]))
    results = [0, 0, 0, 0]
    threads = [threading.Thread(target=worker, args=(results, i)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines.append(str(results).encode())
    churn(800)
    print(b"\\n".join(lines).decode())


main()
'''


def _ir_text() -> str:
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    typed = infer_module(parse_and_lift(PROGRAM, "bytes_lits.py", "bytes_lits"))
    codegen = L1CodeGen(typed, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    return str(codegen.generate(typed))


def test_bytes_literals_lower_to_registered_static_immortal_objects():
    text = _ir_text()
    # No literal is built at run time any more.
    assert re.search(r"call [^\n]*@py_bytes_new\(", text) is None
    # Header: one owner, PY_TYPE_BYTES (17), PY_FLAG_IMMORTAL (1), length.
    sep = re.search(
        r"@\.pybytes\.obj\.\d+ = internal global \{ i64, i32, i32, i64, \[3 x i8\] \} "
        r"\{ i64 1, i32 17, i32 1, i64 2, \[3 x i8\] c\"\\0D\\0A\\00\" \}", text)
    assert sep is not None
    # One object per value: b"same" appears twice in the source.
    assert text.count('c"\\73\\61\\6D\\65\\00"') == 1
    # Registered once at module start, as string literals are.
    init = text.split("define internal void @_pcc_py_static_literals_", 1)[1].split("\n}", 1)[0]
    assert re.search(r"@pcc_gc_pointer_register_static_literal\(ptr @\.pybytes\.obj\.\d+\)", init)
    protocol = re.findall(
        r"@pcc_gc_(?:pin|unpin|release|release_known)\(ptr @\.pybytes\.obj\.\d+\)", text,
    )
    assert protocol == [], protocol[:5]


def test_bytes_literal_programs_match_cpython_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "bytes_lits.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "bytes_lits"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
