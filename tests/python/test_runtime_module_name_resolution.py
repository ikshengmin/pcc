"""Runtime modules must not compile an unresolved name into a runtime NameError.

An application module keeps Python's semantics: a name the compiler cannot
bind is looked up in the module namespace when the line runs, and raises
``NameError`` there.  Runtime modules (``__pcc_runtime_port__`` and
``__pcc_freestanding__``) have no module namespace, so that lookup could only
ever fail.  It used to compile anyway: ``pcc_gc_backend4_fragmentation_score``
called a helper its module never imported, allocated a ``NameError`` object on
every call and returned a wrong score, and the backend-4 relocation selector
then picked that exception object instead of the object under test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).absolute().parents[2]

_BODY = '''
from pcc import i64
from pcc.extern import c_abi_export


@c_abi_export("probe_name_resolution_helper")
def helper() -> i64:
    return 41


@c_abi_export("probe_name_resolution")
def probe_name_resolution() -> i64:
    return {callee}() + 1
'''


def _emit_llvm(tmp_path: Path, directive: str, callee: str):
    src = tmp_path / "name_resolution_probe.py"
    src.write_text(
        (directive + "\n" if directive else "") + _BODY.format(callee=callee),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pcc",
            "--python-library",
            f"--emit-llvm={tmp_path / 'probe.ll'}",
            str(src),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
    )


@pytest.mark.parametrize(
    "directive", ["__pcc_runtime_port__ = True", "__pcc_freestanding__ = True"]
)
def test_runtime_module_rejects_an_unresolved_name(tmp_path, directive):
    result = _emit_llvm(tmp_path, directive, "helper_that_was_never_imported")
    assert result.returncode != 0, result.stdout + result.stderr
    assert (
        "name 'helper_that_was_never_imported' is not defined" in result.stderr
    ), result.stderr


@pytest.mark.parametrize(
    "directive", ["__pcc_runtime_port__ = True", "__pcc_freestanding__ = True"]
)
def test_runtime_module_accepts_a_resolved_name(tmp_path, directive):
    result = _emit_llvm(tmp_path, directive, "helper")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe_name_resolution" in (tmp_path / "probe.ll").read_text(
        encoding="utf-8"
    )


def test_application_module_still_defers_the_lookup_to_run_time(tmp_path):
    # CPython only raises NameError if the line runs; an application module
    # must keep compiling.
    result = _emit_llvm(tmp_path, "", "helper_that_was_never_imported")
    assert result.returncode == 0, result.stdout + result.stderr
