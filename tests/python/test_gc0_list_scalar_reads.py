"""Native scalar list reads preserve elements during replacement and growth."""
import os
from pathlib import Path
import re
from tests.python.process_timeout import run_process_group_timeout

from pcc.frontends.python.codegen.runtime_abi import RUNTIME_FUNCTION_ATTRS
from tests.owned_c_execution import compile_owned_c_with_runtime


def test_scalar_getters_keep_runtime_effects_visible():
    for name in ("py_list_get_i64", "py_list_get_i64_nonnegative"):
        assert not RUNTIME_FUNCTION_ATTRS.get(name, frozenset()) & {
            "readnone", "readonly", "willreturn", "nounwind",
        }


def test_native_gc0_scalar_replace_and_growth(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "list_get_scalar_concurrency.c"
    source.write_text((Path(__file__).resolve().parents[2] / "tests/fixtures/native/list_get_scalar_concurrency.c").read_text())
    output = tmp_path / "list_get_scalar_concurrency"
    compile_owned_c_with_runtime(source, output, runtime_archive=threaded_pcc_runtime_archive)
    for getter in ("0", "1"):
        for growth in ("0", "1"):
            for probe in ("0", "2"):
                result = run_process_group_timeout(
                    [str(output), getter, growth], timeout=45,
                    env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="0", PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe),
                )
                assert (result.returncode, result.stdout, result.stderr) == (
                    0, "LIST_GET_SCALAR_CONCURRENCY_OK\n", "",
                ), (getter, growth, probe, result.returncode, result.stdout, result.stderr)


def test_compiled_scalar_consumer_keeps_effects_and_runs_on_five_gcs(
    tmp_path, threaded_pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "scalar_consumer.py"
    source.write_text('''def sum_ints(values: list[int]) -> int:
    total: int = 0
    for item in values:
        total = total + item
    return total

print(sum_ints([1, 2, 3, 4]))
''')
    ir = tmp_path / "scalar_consumer.ll"
    compile_python(str(source), str(ir), emit_llvm_only=True, backend="self",
                   libpython_mode="off", ir_scaffold_mode="on")
    text = ir.read_text()
    body = re.search(r"(?ms)^define[^\n]*@user_[^\n]*sum_ints\(.*?^}", text).group(0)
    assert "@py_list_get_i64_nonnegative" in body
    for name in ("py_list_get_i64", "py_list_get_i64_nonnegative"):
        declaration = next(line for line in text.splitlines()
                           if line.startswith("declare ") and "@" + name + "(" in line)
        assert not any(attr in declaration for attr in
                       ("readonly", "readnone", "willreturn", "nounwind"))
    binary = tmp_path / "scalar_consumer"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        run = run_process_group_timeout(
            [str(binary)], timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (run.returncode, run.stdout, run.stderr) == (0, "10\n", ""), backend
