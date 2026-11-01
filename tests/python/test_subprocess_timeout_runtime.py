from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from pcc1_gate import repo_root

import pytest

import pcc.stdlib.subprocess as pcc_subprocess
from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1


REPO_ROOT = repo_root()


def test_pcc_ir_support_timeout_result_raises_timeout_expired(monkeypatch):
    calls = []

    def fake_timeout(args, capture, timeout_ms):
        calls.append((args, capture, timeout_ms))
        return -124

    monkeypatch.setattr(
        pcc_subprocess,
        "py_subprocess_run_timeout",
        fake_timeout,
    )
    with pytest.raises(pcc_subprocess.TimeoutExpired):
        pcc_subprocess.run(["slow"], check=True, timeout=3)
    assert calls == [(["slow"], 0, 3000)]


def test_native_subprocess_timeout_kills_child_process_group(tmp_path: Path):
    source = tmp_path / "timeout_probe.py"
    executable = tmp_path / "timeout_probe"
    leaked_marker = tmp_path / "leaked.txt"
    source.write_text(
        "import subprocess\n"
        "subprocess.run([\"/bin/sh\", \"-c\", "
        "\"sleep 5; printf leaked > \\\"$1\\\"\", \"sh\", "
        + repr(str(leaked_marker))
        + "], check=True, timeout=1)\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert build.returncode == 0, build.stderr

    started = time.monotonic()
    run = subprocess.run(
        [str(executable)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    elapsed = time.monotonic() - started

    assert run.returncode != 0
    assert elapsed < 4.0
    assert "subprocess.run timed out" in run.stderr
    time.sleep(0.25)
    assert not leaked_marker.exists(), "timed-out subprocess group survived"


def test_native_subprocess_timeout_passes_owned_environment_to_child(tmp_path: Path):
    source = tmp_path / "owned_child_env_probe.py"
    executable = tmp_path / "owned_child_env_probe"
    source.write_text(
        "import os\n"
        "import subprocess\n"
        "os.environ['PCC_OWNED_CHILD_VALUE'] = 'from-owned-table'\n"
        "subprocess.run([\"/bin/sh\", \"-c\", "
        "\"test \\\"$PCC_OWNED_CHILD_VALUE\\\" = from-owned-table\"], "
        "check=True, timeout=2)\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.pop("PCC_OWNED_CHILD_VALUE", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert build.returncode == 0, build.stderr

    run = subprocess.run(
        [str(executable)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert run.returncode == 0, run.stdout + run.stderr
