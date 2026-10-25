"""User-defined replace names must not select the dataclasses helper."""
import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


PROGRAM = """def replace(value):
    return value + 10


def _replace(value):
    return value + 20


def main():
    print(replace(1), _replace(3))

main()
"""


def test_replace_resolves_user_definitions(tmp_path, pcc_runtime_archive):
    source = tmp_path / "replace_names.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "11 23\n", "")
    output = tmp_path / "replace_names"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
