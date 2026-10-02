"""Except clauses use the evaluated exception class, including module attrs."""
import os
import subprocess
import sys
import pytest
from pcc.frontends.python.pipeline import compile_python_multi


CELL_HANDLER_SOURCE = '''def outer():
    SelectedError = ValueError
    def invoke(marker=None):
        try:
            raise ValueError("cell")
        except SelectedError:
            return 42
    return invoke()
def main():
    assert outer() == 42
    classes = [ValueError, TypeError]
    try:
        raise TypeError("index")
    except classes[0]:
        raise AssertionError("wrong exception class selected")
    except classes[1]:
        pass
    print("EXCEPTION_CLASS_CELL_AND_INDEX_OK")
main()
'''


@pytest.mark.parametrize("target", ("arm64-apple-darwin", "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc"))
def test_exception_class_cell_and_index_reach_owned_objects(target):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(parse_and_lift(CELL_HANDLER_SOURCE, "<exception-class-cell>", "exception_class_cell"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    text = codegen.generate(module)
    has_unavailable_function = "strict.nolib.stub:" in text
    assert not has_unavailable_function
    assert len(emit_owned_object(text, target)) > 0


@pytest.mark.integration
def test_exception_class_cell_and_index_execute_all_collectors(tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler):
    source = tmp_path / "exception_class_cell.py"
    source.write_text(CELL_HANDLER_SOURCE)
    binary = tmp_path / "exception_class_cell"
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off", ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20, env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_GC_REFCOUNT_PROVENANCE_PROBE="2"))
        assert result.returncode == 0 and result.stdout == "EXCEPTION_CLASS_CELL_AND_INDEX_OK\n" and result.stderr == "", (backend, result.returncode, result.stdout, result.stderr)


def test_module_and_local_exception_class_selection(tmp_path, pcc_runtime_archive):
    errors = tmp_path / 'error_types.py'
    errors.write_text('''class First(Exception):
    pass
class Second(Exception):
    pass
class FailingConstructor:
    def __init__(self):
        raise First("constructor")
def trigger(first):
    if first:
        raise First("first")
    raise Second("second")
''', encoding='utf-8')
    source = tmp_path / 'error_use.py'
    source.write_text('''import error_types
def main():
    for first in [True, False]:
        try:
            error_types.trigger(first)
        except error_types.First:
            print("first")
        except error_types.Second:
            print("second")
    selected = error_types.Second
    try:
        error_types.trigger(False)
    except selected:
        print("selected")
    try:
        error_types.FailingConstructor()
    except error_types.First:
        print("constructor")
main()
''', encoding='utf-8')
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=10)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / 'error_use'
    compile_python_multi([str(errors), str(source)], str(binary),
                         module_names=['error_types', 'error_use'], entry_module='error_use',
                         backend='self', libpython_mode='off', runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, PCC_GC_BACKEND=str(gc)))
        assert result.returncode == 0, f'GC{gc}: {result.stderr}'
        assert result.stdout == expected.stdout, f'GC{gc}: {result.stdout}'
