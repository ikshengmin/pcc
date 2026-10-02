"""Compile and execute C regressions through the public owned PCC API."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from pcc import build


def compile_and_run_owned_c(source: str, *, timeout: int = 30):
    """Keep semantic assertions independent of an external LLVM/cc toolchain."""
    with tempfile.TemporaryDirectory(prefix="pcc_owned_c_test_") as tmpdir:
        source_path = Path(tmpdir) / "program.c"
        source_path.write_text(source, encoding="utf-8")
        # Host PCC owns preprocessing, object emission and linking. Any child
        # process during build would bypass this regression's ownership proof.
        with patch(
            "subprocess.Popen",
            side_effect=AssertionError("owned C fixture invoked a build subprocess"),
        ):
            artifact = build(
                source_path,
                kind="exe",
                backend="self",
                optimize=0,
                out_dir=Path(tmpdir) / "output",
                use_compile_cache=False,
            )
        assert artifact.backend == "self"
        return subprocess.run(
            [artifact.output_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
