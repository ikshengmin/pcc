"""Host codegen workers read one module's indexed export view.

A multi-module compile publishes one exports file for its codegen workers.
Each single-module worker reads the dependency closure of its root module
from it (``read_native_exports_wire_for_module``). The coordinator wrote the
indexed file only when the workers were native pcc; host CPython workers
got the full-graph v1 file, so each decoded the whole graph (1.5 of the
2.2 s CPU of a small Stage1 worker, in each of 622 workers) and saw a
different view of the program than pcc1's workers. The coordinator now
writes the indexed file for host workers too. The program below is
compiled by host workers and its output compared with CPython's
(Python Language Reference 5 "The import system").
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from pcc.frontends.python import pipeline_frontend_parallel


FILES = {
    "leaf.py": """
        class Cell:
            def __init__(self, value: int) -> None:
                self.value = value

            def doubled(self) -> int:
                return self.value * 2


        def make(value: int) -> Cell:
            return Cell(value)
    """,
    "middle.py": """
        from leaf import Cell, make


        def total(values: list[int]) -> int:
            result = 0
            for value in values:
                cell: Cell = make(value)
                result += cell.doubled()
            return result
    """,
    "other.py": """
        GREETING = "other module"


        def shout(text: str) -> str:
            return text.upper()
    """,
    "main.py": """
        import middle
        import other

        print(middle.total([1, 2, 3]))
        print(other.shout(other.GREETING))
    """,
}


def test_host_workers_compile_from_the_indexed_export_view(tmp_path, monkeypatch, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python_multi

    paths = []
    for name, source in FILES.items():
        path = tmp_path / name
        path.write_text(textwrap.dedent(source).lstrip())
        paths.append(str(path))
    reference = subprocess.run(
        [sys.executable, "-E", "-B", str(tmp_path / "main.py")],
        capture_output=True, text=True, timeout=30, cwd=tmp_path,
    )
    assert reference.returncode == 0, reference.stderr

    maps = []
    original = pipeline_frontend_parallel.compile_parallel_uncached

    def recording(*args, **kwargs):
        maps.append(kwargs.get("module_dependency_map"))
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_frontend_parallel, "compile_parallel_uncached", recording)
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "2")
    monkeypatch.delenv("PCC_HOST_CODEGEN_BATCH", raising=False)
    binary = tmp_path / "main"
    compile_python_multi(
        paths, str(binary), entry_module="main", backend="self",
        ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive),
    )
    # The export workers filled the map, so the indexed file was published.
    assert len(maps) == 1 and isinstance(maps[0], dict), maps
    assert maps[0].get("main") is not None and "middle" in maps[0]["main"], maps[0]
    assert "other" not in maps[0].get("leaf", ("other",)), maps[0]
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stderr) == (0, ""), ran.stderr[-2000:]
    assert ran.stdout == reference.stdout
