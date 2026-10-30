from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_subprocess_check_output_and_check_call_no_libpython(tmp_path: Path):
    src = tmp_path / "prog.py"
    src.write_text(
        "import subprocess\n"
        "def main():\n"
        "    out = subprocess.check_output(['/bin/echo', 'hello'])\n"
        "    print(out.decode().strip())\n"
        "    text = subprocess.check_output(['/bin/echo', 'world'], text=True)\n"
        "    print(text.strip())\n"
        "    print(subprocess.check_call(['/bin/echo', 'check']))\n"
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["hello", "world", "check", "0"]


def test_subprocess_check_true_preserves_called_process_error_fields_no_libpython(
    tmp_path: Path,
):
    src = tmp_path / "check_failure.py"
    src.write_text(
        "import subprocess\n"
        "def main():\n"
        "    try:\n"
        "        subprocess.run(['/bin/sh', '-c', 'exit 7'], check=True)\n"
        "    except subprocess.CalledProcessError as exc:\n"
        "        print('run', exc.returncode, exc.cmd[-1], "
        "exc.output is None, exc.stderr is None)\n"
        "    try:\n"
        "        subprocess.check_call(['/bin/sh', '-c', 'exit 9'])\n"
        "    except subprocess.CalledProcessError as exc:\n"
        "        print('check_call', exc.returncode, exc.cmd[-1], "
        "exc.output is None, exc.stderr is None)\n"
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "check_failure"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "run 7 exit 7 True True",
        "check_call 9 exit 9 True True",
    ]


def test_discarded_run_computed_capture_starred_argv_and_timeout_native(tmp_path, pcc_runtime_archive):
    import sys

    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "computed_run.py"
    source.write_text(
        "import subprocess as process\n"
        "def invoke(make_cmd: list[str], verbose: bool, seconds: int) -> None:\n"
        "    process.run(['/bin/sh', '-c', 'printf visible', *make_cmd],\n"
        "                check=True, capture_output=not verbose)\n"
        "    process.run(make_cmd, check=True, timeout=seconds)\n"
        "def main():\n"
        "    invoke(['/bin/sh', '-c', 'true'], False, 2)\n"
        "    invoke(['/bin/sh', '-c', 'true'], True, 2)\n"
        "    print('NATIVE_SUBPROCESS_KWARGS_OK')\n"
        "main()\n"
    )
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=15)
    assert (expected.returncode, expected.stdout, expected.stderr) == (
        0, "visibleNATIVE_SUBPROCESS_KWARGS_OK\n", "",
    )
    binary = tmp_path / "computed_run"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, expected.stdout, "",
        ), backend
