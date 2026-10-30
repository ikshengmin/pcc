"""Owned native programs either implement POSIX providers or reject the target."""

import os
import subprocess
import sys

import pytest

from tests.cli_support import run_cli


@pytest.mark.integration
@pytest.mark.parametrize("provider", ("pwd", "signal"))
def test_owned_native_posix_provider_target_contract(tmp_path, provider):
    unsupported = sys.platform == "win32"
    if provider == "pwd":
        uid = 0 if unsupported else os.getuid()
        source = "import pwd\nprint(pwd.getpwuid(" + str(uid) + ").pw_name)\n"
        diagnostic = "pwd native lookup currently owns only Darwin and Linux ABIs"
    else:
        source = "import signal\nprint(signal.Signals(signal.SIGTERM).name)\n"
        diagnostic = "pcc.stdlib.signal currently owns only Darwin and Linux ABIs"
    src = tmp_path / (provider + "_target.py")
    src.write_text(source)
    exe = tmp_path / (provider + (".exe" if os.name == "nt" else ""))
    built = run_cli(
        ["--backend", "self", "--python-libpython=off", "--ir-scaffold=on",
         str(src), "-o", str(exe)],
        timeout=120,
    )
    if unsupported and built.returncode != 0:
        # A missing symbol, generic linker error or host fallback is not the
        # explicit unsupported-target boundary this test requires.
        assert diagnostic in built.stdout + built.stderr
        assert not exe.exists()
        return
    assert built.returncode == 0, built.stdout + built.stderr
    env = os.environ.copy()
    env["PCC_HOST_PYTHON"] = str(tmp_path / "no-host-python")
    env["PATH"] = str(tmp_path / "no-host-tools")
    actual = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=20)
    if unsupported:
        assert actual.returncode != 0
        assert diagnostic in actual.stdout + actual.stderr
    else:
        expected = subprocess.run(
            [sys.executable, str(src)], capture_output=True, text=True, timeout=20,
        )
        assert expected.returncode == 0, expected.stderr
        assert actual.returncode == 0, actual.stderr
        assert actual.stdout == expected.stdout
