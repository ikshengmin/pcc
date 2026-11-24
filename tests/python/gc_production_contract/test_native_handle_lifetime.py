"""Native files flush on reclamation and remain open while reachable."""
from __future__ import annotations

import os
import subprocess

import pytest


def _program(data_path: str) -> str:
    return (
        "import gc\n"
        f"PATH = {data_path!r}\n"
        "\n"
        "def drop_unclosed_writer():\n"
        "    f = open(PATH, 'w', encoding='utf-8')\n"
        "    f.write('alpha')\n"
        "    f = None\n"
        "    gc.collect()\n"
        "\n"
        "def live_handle_collect():\n"
        "    f = open(PATH, 'w', encoding='utf-8')\n"
        "    f.write('one')\n"
        "    gc.collect()\n"
        "    f.write('two')\n"
        "    f.close()\n"
        "\n"
        "def main():\n"
        "    drop_unclosed_writer()\n"
        "    with open(PATH, 'r', encoding='utf-8') as r:\n"
        "        print(r.read())\n"
        "    live_handle_collect()\n"
        "    with open(PATH, 'r', encoding='utf-8') as r:\n"
        "        print(r.read())\n"
        "\n"
        "main()\n"
    )


@pytest.fixture(scope="module")
def _native_handle_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_native_handle")
    data = tmp / "native-handle.txt"
    src = tmp / "native_handle.py"
    src.write_text(_program(str(data)), encoding="utf-8")
    exe = tmp / "native_handle_bin"
    env = os.environ.copy()
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
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
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


def test_native_file_handle_lifetime(_native_handle_exe):
    env = os.environ.copy()
    run = subprocess.run(
        [_native_handle_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert run.returncode == 0, (
        f"rc={run.returncode}: {run.stderr.strip()[:200]}"
    )
    assert run.stdout.splitlines()[:2] == ["alpha", "onetwo"], run.stdout
