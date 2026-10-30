"""Executed local imports keep their owned binding across suspension."""
import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


PROGRAM = '''import gc
import import_source

def module_generator():
    import import_source as local_module
    yield local_module
    yield local_module

def value_generator():
    from import_source import payload as held
    yield held
    yield held

def metadata_generator():
    from pcc.unsafe import atomic_load_i64
    yield 1
    yield 2

def main():
    modules = module_generator()
    values = value_generator()
    original = import_source.payload
    print(next(modules) is import_source)
    print(next(values) is original)
    import_source.payload = ["replacement"]
    gc.collect()
    print(next(modules) is import_source)
    print(next(values) is original)
    modules.close()
    values.close()
    print(list(metadata_generator()))
main()
'''

EXPECTED = "True\nTrue\nTrue\nTrue\n[1, 2]\n"


def test_local_imports_survive_suspend_collection_and_rebinding(tmp_path, pcc_runtime_archive):
    (tmp_path / "import_source.py").write_text('payload = ["original"]\n')
    source = tmp_path / "import_consumer.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, EXPECTED, "")
    output = tmp_path / "import_consumer"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, ""), gc
