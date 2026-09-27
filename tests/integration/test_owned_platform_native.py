"""Execute the platform ports through a supplied native compiler.

Set PCC_PLATFORM_TEST_COMPILER to the platform's pcc1/pcc2/pcc3 executable.
These tests deliberately fail when no native compiler is supplied. They do
not bootstrap, use a host compiler, or replace target execution with parsing.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def native_compiler():
    value = os.environ.get("PCC_PLATFORM_TEST_COMPILER", "")
    if not value:
        pytest.fail("set PCC_PLATFORM_TEST_COMPILER to the native platform compiler")
    compiler = Path(value).resolve()
    if not compiler.is_file():
        pytest.fail("native compiler does not exist: " + str(compiler))
    if not (sys.platform.startswith("linux") or sys.platform == "win32"):
        pytest.fail("this gate executes Linux/Windows platform ports")
    return compiler


def compile_run(compiler, root, source, *, suffix=".py", gc="0", expected="", argv=(), extra_env=None):
    from scripts.platform_process_watchdog import run
    root.mkdir(parents=True, exist_ok=True)
    input_path = root / ("program" + suffix)
    output = root / ("program.exe" if os.name == "nt" else "program")
    input_path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("LC_ALL", None)
    environment.update({"PCC_GC_BACKEND": gc, "PCC_SELF_LINK": "pcc", "PCC_SELF_OBJ": "pcc",
                        "PCC_HOST_PYTHON": str(root / "forbidden-python"),
                        "CC": str(root / "forbidden-cc"), "PCC_IR_TO_OBJ_EMITTER": "pcc"})
    if extra_env:
        environment.update(extra_env)
    run([str(compiler), "--backend", "self", str(input_path), "-o", str(output)],
        cwd=root, env=environment, log_path=root / "compile.log", timeout=600,
        rss_limit=8589934592, native=True)
    from scripts.bootstrap_platform import dependency_receipt
    dependency_receipt(output)
    log = root / "run.log"
    run([str(output), *argv], cwd=root, env=environment, log_path=log,
        timeout=30, rss_limit=8589934592, native=True)
    assert log.read_text(encoding="utf-8").replace("\r\n", "\n") == expected
    return output


@pytest.mark.parametrize("gc", ["0", "1", "2", "3", "4"])
def test_native_object_integer_exception_and_gc(native_compiler, tmp_path, gc):
    compile_run(native_compiler, tmp_path, """
        from pcc.unsafe import gc_backend_current
        class Box:
            def __init__(self, value: int):
                self.value = value
        def calculate() -> int:
            item = Box(2 ** 100)
            roots = [item]
            try:
                raise ValueError("roundtrip")
            except ValueError:
                assert roots[0] is item
                return roots[0].value + 42
        print(gc_backend_current())
        print(calculate())
    """, gc=gc, expected=f"{gc}\n{2 ** 100 + 42}\n")


def test_native_c_mixed_aggregate_returns_and_variadic_arguments(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r"""
        #include <stdarg.h>
        struct pair { double real; long long integer; };
        struct large { long long a; long long b; long long c; };
        static struct pair make_pair(double real, long long integer) {
            struct pair p = {real, integer};
            return p;
        }
        static long long collect(int marker, ...) {
            va_list ap;
            va_start(ap, marker);
            struct pair p = va_arg(ap, struct pair);
            struct large q = va_arg(ap, struct large);
            double tail = va_arg(ap, double);
            va_end(ap);
            return (long long)p.real + p.integer + q.a + q.b + q.c + (long long)tail;
        }
        int main(void) {
            struct pair p = make_pair(1.5, 2);
            struct large q = {3, 4, 5};
            return collect(0, p, q, 3.25) == 18 ? 0 : 71;
        }
    """, suffix=".c")


def test_native_c_stack_probe_and_argument_overflow(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r"""
        static long long work(long long a, double b, long long c, double d,
                              long long e, long long f, long long g, long long h,
                              long long i, long long j) {
            volatile unsigned char storage[131072];
            storage[0] = 7;
            storage[131071] = 11;
            return a + (long long)b + c + (long long)d + e + f + g + h + i + j
                   + storage[0] + storage[131071];
        }
        int main(void) {
            return work(1, 2.0, 3, 4.0, 5, 6, 7, 8, 9, 10) == 73 ? 0 : 72;
        }
    """, suffix=".c")


def test_native_c_platform_data_model(native_compiler, tmp_path):
    long_size = 4 if os.name == "nt" else 8
    import platform
    unsigned_char = sys.platform.startswith("linux") and platform.machine().lower() in ("arm64", "aarch64")
    compile_run(native_compiler, tmp_path, f"""
        #include <stddef.h>
        #include <stdint.h>
        #include <limits.h>
        int main(void) {{
            if (sizeof(long) != {long_size}) return 1;
            if (sizeof(void *) != 8 || sizeof(size_t) != 8 || sizeof(intptr_t) != 8) return 2;
            if (((char)255 > 0) != {1 if unsigned_char else 0}) return 3;
            if (sizeof(int64_t) != 8 || sizeof(uint64_t) != 8) return 4;
            if (sizeof(int_least64_t) != 8 || sizeof(uint_fast64_t) != 8) return 5;
            if (sizeof(intmax_t) != 8 || sizeof(uintmax_t) != 8) return 6;
            int64_t large = 4294967296LL;
            if (large + 7 != 4294967303LL) return 7;
            if (LONG_MAX != {2147483647 if long_size == 4 else 9223372036854775807}LL) return 8;
            if (ULONG_MAX != {4294967295 if long_size == 4 else 18446744073709551615}ULL) return 9;
            if (CHAR_MIN != {0 if unsigned_char else -128} || CHAR_MAX != {255 if unsigned_char else 127}) return 10;
            return 0;
        }}
    """, suffix=".c")


def test_native_relpath_validates_before_normalization(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, """
        import os
        def check():
            try:
                os.path.relpath("")
            except ValueError:
                print("empty rejected")
            else:
                raise AssertionError("empty relpath became cwd")
            assert os.path.relpath("one/two", "one") == "two"
            assert os.path.relpath(".", ".") == "."
        check()
    """, expected="empty rejected\n")


def test_native_directory_names_and_cleanup_without_shell(native_compiler, tmp_path):
    directory = tmp_path / "directory with spaces"
    directory.mkdir()
    names = ["alpha.txt", "two words.txt", "unicode-中文.txt"]
    if os.name != "nt":
        names.append("line\nbreak.txt")
    for name in names:
        (directory / name).write_text("value", encoding="utf-8")
    compile_run(native_compiler, tmp_path / "build", """
        import os
        import sys
        import tempfile
        def check():
            entries = os.listdir(sys.argv[1])
            print(len(entries))
            with tempfile.TemporaryDirectory() as root:
                child = os.path.join(root, "nested")
                os.makedirs(child)
                with open(os.path.join(child, "value.txt"), "w") as stream:
                    stream.write("42")
            print(os.path.exists(root))
        check()
    """, argv=[str(directory)], expected=f"{len(names)}\nFalse\n")


def test_native_platform_path_and_environment_contract(native_compiler, tmp_path):
    if os.name != "nt":
        compile_run(native_compiler, tmp_path, """
            import os
            def check():
                assert os.name == "posix"
                assert os.sep == "/"
                assert os.pathsep == ":"
                assert os.path.normpath("/work/../file") == "/file"
                assert os.path.dirname("/work/file") == "/work"
                os.environ["PCC_Mixed_Case"] = "value"
                assert os.environ["PCC_Mixed_Case"] == "value"
                assert os.environ.get("pcc_mixed_case") is None
                print("posix paths ok")
            check()
        """, expected="posix paths ok\n")
        return
    compile_run(native_compiler, tmp_path, r'''
        import os
        import sys
        def check():
            assert os.name == "nt"
            assert os.sep == "\\"
            assert os.pathsep == ";"
            assert os.path.isabs("C:\\work\\file")
            assert os.path.dirname("C:\\work\\file") == "C:\\work"
            assert os.path.basename("C:\\work\\file") == "file"
            assert os.path.normpath("C:\\work\\..\\file") == "C:\\file"
            assert os.path.splitdrive("C:\\work")[0] == "C:"
            os.environ["PCC_Mixed_Case"] = "value"
            assert os.environ["pcc_mixed_case"] == "value"
            print("windows paths ok")
        check()
    ''', expected="windows paths ok\n")
