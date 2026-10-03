"""Run corpus product programs through the existing owned C emission APIs.

External reference compilers stay in their separate corpus helpers. This path
preserves the original translation units, dialect/options and child outcomes.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from tests.native_provisioning import require_native_provisioning_allowed


def run_owned_c_corpus(
    evaluator, units, *, base_dir=None, include_dirs=None, cpp_args=None,
    timeout=20, optimize=True, link_args=None,
):
    if evaluator.backend != "self":
        raise RuntimeError("corpus product execution requires the owned self backend")
    if evaluator.is_cross:
        raise RuntimeError("corpus product execution requires the host target")
    if not os.environ.get("PCC_RUNTIME_ARCHIVE"):
        require_native_provisioning_allowed()
    level = evaluator._normalize_opt_level(optimize)
    compiled = evaluator.compile_translation_units(
        units, base_dir=base_dir, use_system_cpp=False,
        include_dirs=include_dirs, cpp_args=cpp_args,
        frontend_opt_level=level,
    )
    with tempfile.TemporaryDirectory(prefix="pcc_owned_c_corpus_") as temporary:
        executable = Path(temporary) / ("program.exe" if os.name == "nt" else "program")
        evaluator.emit_executable(compiled, str(executable), optimize=level,
                                  link_args=link_args)
        return subprocess.run(
            [str(executable)], cwd=base_dir or os.getcwd(), timeout=timeout,
            capture_output=True, text=True,
        )
