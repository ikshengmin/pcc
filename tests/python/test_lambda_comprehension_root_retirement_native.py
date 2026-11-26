"""A native lambda must retire expression roots on success and error."""

import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python
from tests.owned_ir_validation import verify_ir_text
from tests.python.iterator_root_contract import assert_iterator_owner, parsed_root_contract


PROGRAM = '''import gc
events = []
class Token:
    def __init__(self, label):
        self.label = label
    def __del__(self):
        events.append(self.label)
        gc.collect()

def visit(item):
    gc.collect()
    if item.label == 2:
        raise ValueError("callback")
    return item.label + 10

def exercise():
    operations = [
        lambda: [visit(item) for item in iter([Token(1), Token(3)])],
        lambda: [visit(item) for item in iter([Token(4), Token(2), Token(5)])],
    ]
    good = operations[0]
    bad = operations[1]
    for round_index in range(3):
        print(good())
        gc.collect()
        try:
            bad()
        except ValueError as exc:
            print(str(exc))
        gc.collect()

exercise()
gc.collect()
print(sorted(events))
'''


def test_native_lambda_expression_frames_retire_on_success_and_error(tmp_path, pcc_runtime_archive):
    source = tmp_path / "lambda_root_retirement.py"
    source.write_text(PROGRAM)
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=15,
    )
    assert (reference.returncode, reference.stderr) == (0, "")
    assert reference.stdout == (
        "[11, 13]\ncallback\n" * 3 +
        "[1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 5]\n"
    )

    # Inspect the actual native adapter IR as well as its observable runtime
    # output, so an inactive registry cannot hide an unmatched frame leave.
    ir = tmp_path / "lambda_root_retirement.ll"
    compile_python(str(source), str(ir), backend="self", ir_scaffold_mode="on",
                   emit_llvm_only=True)
    module = verify_ir_text(ir.read_text())
    adapters = [fn for fn in module.functions if "__native_lambda_" in fn.name]
    assert len(adapters) == 2
    for adapter in adapters:
        roots = parsed_root_contract(module, adapter)
        owner = next(name for name in roots.allocas if name.startswith("comp.iter.root."))
        assert_iterator_owner(roots, owner, private=False)
        assert not any(ins.kind == "call" and ins.data[2].startswith("py_cpy_")
                       for _, _, ins in roots.rows)
        roots.assert_frame_exits(exact_abi=True)

    executable = tmp_path / "lambda_root_retirement"
    compile_python(str(source), str(executable), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
    assert (ran.returncode, ran.stderr) == (0, "")
    assert ran.stdout == reference.stdout
