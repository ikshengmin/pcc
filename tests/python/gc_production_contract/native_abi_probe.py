"""Link an independent C ABI test object into an owned PCC executable."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


RUNTIME_DIR = Path(__file__).resolve().parents[3] / "pcc" / "runtime"


def run_native_abi_probe(
    tmp_path, source, pcc_runtime_archive, python_program_compiler,
    *, readback="", run_env=None, arguments=(), pass_fds=(), include_dirs=(),
):
    # Use an external C ABI object but the same owned executable link as
    # Python applications, not the host C startup and libc.
    runtime = Path(os.environ.get("PCC_RUNTIME_DIR", str(RUNTIME_DIR)))
    src = tmp_path / "probe.c"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    probe = tmp_path / "probe.o"
    build = subprocess.run(
        [
            os.environ.get("CC", "cc"),
            "-std=c11",
            "-ffreestanding",
            "-fno-stack-protector",
            "-fno-ident",
            "-fno-asynchronous-unwind-tables",
            "-fno-unwind-tables",
            *(f"-I{directory}" for directory in include_dirs),
            f"-I{runtime / 'include'}",
            f"-I{runtime / 'src'}",
            f"-I{Path(__file__).resolve().parent}",
            "-c",
            str(src),
            "-o",
            str(probe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    if sys.platform.startswith("linux"):
        # The owned linker accepts code and data from the ABI fixture but
        # not host assembler metadata such as STT_FILE.
        stripped = subprocess.run(
            [os.environ.get("OBJCOPY", "objcopy"), "--strip-debug", str(probe)],
            capture_output=True, text=True, timeout=30,
        )
        assert stripped.returncode == 0, stripped.stdout + stripped.stderr
    signature = "(" + "c_int64," * len(arguments) + ")"
    values = ", ".join(str(value) for value in arguments)
    caller = tmp_path / "caller.py"
    caller.write_text(
        "from pcc.extern import extern, c_int64\n"
        f"probe = extern('pcc_test_gc_probe', {signature}, c_int64)\n"
        f"print(probe({values}))\n" + readback,
        encoding="utf-8",
    )
    exe = tmp_path / "probe"
    python_program_compiler(
        str(caller), str(exe), backend="self",
        runtime_archive=str(pcc_runtime_archive), object_inputs=(str(probe),),
    )
    return subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=60,
        env={**os.environ, **(run_env or {}), "PATH": "/nonexistent"},
        pass_fds=pass_fds,
    )
