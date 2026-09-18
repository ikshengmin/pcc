"""Builtin isinstance and the struct provider's integer boundaries.

Two defects this pins, both silent and both reached by the compiler's own
linker on every self-hosted build:

``isinstance`` answered False for every builtin type.  A shadowing guard added
ahead of the builtin paths left ``_compile_time_isinstance`` and
``_emit_builtin_runtime_isinstance`` computed-and-discarded, so
``isinstance("x", str)``, ``isinstance(7, int)``, ``isinstance(b"", bytes)``
and ``isinstance(str, type)`` were all False.  The first thing that surfaced
was ``struct.unpack_from`` raising ``TypeError: Struct() argument 1 must be a
str`` on its own format literal, because ``_cached_struct`` tests
``isinstance(fmt, str)``.

``struct`` decoded 8-byte signed big-endian fields one modulus low whenever the
generic plan walked them: ``">qq"`` returned -17640505755255379698 where
CPython returns 806238318454171918, while ``">q"``, ``">i"``, ``">ii"`` and
every little-endian form were correct.  The sign threshold came from an
if/elif/else over three int64-range literals and one 2**64 literal, and the
comparison against it was narrowed while the subtraction was not.

CPython is the oracle for both: the expected text is produced by running the
same program under the host interpreter, not written by hand.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

_PROGRAM = textwrap.dedent(
    '''\
    import struct


    def describe(value) -> str:
        return (
            "str=" + str(isinstance(value, str))
            + " int=" + str(isinstance(value, int))
            + " bytes=" + str(isinstance(value, bytes))
            + " type=" + str(isinstance(value, type))
        )


    def payload(count: int) -> bytes:
        out = bytearray()
        index = 0
        while index < count:
            out.append((index * 37 + 11) & 0xFF)
            index = index + 1
        return bytes(out)


    def main() -> int:
        print("text   :", describe("abc"))
        print("joined :", describe("a" + "bc"))
        print("number :", describe(7))
        print("raw    :", describe(b"x"))
        print("class  :", describe(str))
        data = payload(64)
        for fmt in (
            "<Q", "<q", ">Q", ">q", ">qq", ">2q",
            "<i", ">i", ">ii", "<h", ">h", ">hh", "<B", "<b", ">b",
        ):
            print(fmt, struct.unpack_from(fmt, data, 0))
        holder = struct.Struct(">q")
        print("prebuilt", holder.unpack_from(data, 0), holder.size)
        print("negative", struct.unpack_from("<q", data, -8))
        try:
            struct.unpack_from("<Q", data, 60)
        except Exception as exc:
            print("short", type(exc).__name__)
        return 0


    main()
    '''
)


def _expected() -> str:
    source = REPO_ROOT / "build" / "isinstance_struct_oracle.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(_PROGRAM, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(source)],
        capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_cpython_oracle_covers_both_defects():
    """The oracle must actually exercise the shapes, not just run."""
    text = _expected()
    assert "str=True" in text, text
    assert "int=True" in text, text
    assert "type=True" in text, text
    # 806238318454171918 is the correct big-endian signed 64-bit value; the
    # defect returned it one 2**64 modulus low.
    assert "806238318454171918" in text, text


@pytest.mark.pcc_gate(probe="self_backend")
def test_native_matches_cpython(tmp_path):
    source = REPO_ROOT / "build" / "isinstance_struct_native.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(_PROGRAM, encoding="utf-8")
    binary = source.with_suffix("")

    build = subprocess.run(
        [
            sys.executable, "-m", "pcc",
            "--backend", "self", "--python-libpython", "off",
            str(source), "-o", str(binary),
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=1800,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=300,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    # "no-libpython function unavailable" here means a `py_cpy_*` call stayed
    # in the IR and the whole function became a fail-closed stub -- that is how
    # `int.from_bytes(..., signed=True)` broke this provider once.
    assert "no-libpython" not in run.stdout, run.stdout
    assert run.stdout == _expected(), (
        "compiled output diverged from CPython\n"
        f"--- native ---\n{run.stdout}\n--- host ---\n{_expected()}"
    )
