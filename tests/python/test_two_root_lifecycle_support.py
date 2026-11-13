"""PEP 442 collection death is distinct from the error-edge owner contract.

See docs/project-intent.md (Python semantics/shared graph) and PEP 442's
disposal sequence. These focused models are not native execution receipts.
"""

import ast
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.python import owned_regression_support as owned
from tests.python import two_root_lifecycle_support as lifecycle


ROOT = Path(__file__).resolve().parents[2]


def _runtime_postcondition(omit=None):
    source = ROOT / "pcc/runtime/py/py_cleanup_runtime.py"
    helper = next(node for node in ast.parse(source.read_text()).body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "py_cleanup_two_roots_preserving_exception")
    helper = copy.deepcopy(helper)
    helper.decorator_list = []
    helper.returns = None
    for argument in helper.args.args:
        argument.annotation = None
    calls = [node for node in helper.body if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call)]
    if omit is not None:
        if omit == "restore":
            selected = [node for node in calls
                        if node.value.func.id == "py_tls_exc_swap_slot"][-1]
        else:
            selected = next(node for node in calls
                            if node.value.func.id == "pcc_gc_store_root"
                            and node.value.args[0].id == omit)
        helper.body.remove(selected)
    original = object()
    tls = [original]
    first, second = [object()], [object()]
    frames = []

    def swap(slot):
        tls[0], slot[0] = slot[0], tls[0]

    namespace = {
        "C_POINTER_SIZE": 8,
        "stack_alloc": lambda size: [None],
        "null": lambda: None,
        "store_ptr": lambda slot, offset, value: slot.__setitem__(0, value),
        "global_addr": lambda name: name,
        "pcc_gc_frame_enter_lifo": lambda mapping, slot: frames.append(slot),
        "pcc_gc_frame_leave_lifo": lambda slot: frames.remove(slot),
        "py_tls_exc_swap_slot": swap,
        "py_clear_exception": lambda: tls.__setitem__(0, None),
        "pcc_gc_store_root": lambda slot, value: slot.__setitem__(0, value),
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(source), "exec"), namespace)
    namespace[helper.name](first, second)
    # Same native ABI observation: slots first, then selecting TLS identity.
    assert frames == []
    return 1 if first[0] is not None or second[0] is not None else (0 if tls[0] is original else 2)


def test_real_two_root_body_has_pre_exit_slot_and_tls_postconditions():
    assert _runtime_postcondition() == 0


@pytest.mark.parametrize("omit, status", [("first", 1), ("second", 1), ("restore", 2)])
def test_pre_exit_postcondition_detects_missing_clear_or_tls_restore(omit, status):
    assert _runtime_postcondition(omit) == status


def test_native_probe_observes_postconditions_before_caller_retirement():
    text = Path(lifecycle.__file__).with_name("two_root_cleanup_slots.ll.in").read_text()
    checks = (
        "call void @py_cleanup_two_roots_preserving_exception(ptr %first, ptr %second)",
        "%first_after = load ptr, ptr %first",
        "%second_after = load ptr, ptr %second",
        "%first_empty = icmp eq ptr %first_after, null",
        "%second_empty = icmp eq ptr %second_after, null",
        "%slots_empty = and i1 %first_empty, %second_empty",
        "%pending = call ptr @py_current_exception()",
        "%selected = load ptr, ptr %saved",
        "%same_exception = icmp eq ptr %pending, %selected",
        "%exception_status = select i1 %same_exception, i64 0, i64 2",
        "%status = select i1 %slots_empty, i64 %exception_status, i64 1",
        "call void @py_clear_exception()",
        "call void @pcc_gc_frame_leave(ptr %roots)",
        "ret i64 %status",
    )
    offsets = [text.index(check) for check in checks]
    assert offsets == sorted(offsets)


EDGE_IR = '''@map = internal constant i32 1
define ptr @user_case_probe(ptr %callee) {
entry:
  %call.args.operand.0 = alloca ptr
  %call.args.item.operand.0 = alloca ptr
  call void @pcc_gc_frame_enter_lifo_local(ptr @map, ptr %call.args.operand.0)
  call void @pcc_gc_frame_enter_lifo_local(ptr @map, ptr %call.args.item.operand.0)
  %value = call ptr () @user_case_fail()
  %pending = call i64 () @py_err_occurred()
  %failed = icmp ne i64 %pending, 0
  br i1 %failed, label %cleanup, label %done
cleanup:
  call void @py_cleanup_two_roots_preserving_exception(ptr %call.args.operand.0, ptr %call.args.item.operand.0)
  call void @pcc_gc_frame_leave_lifo_local(ptr %call.args.item.operand.0)
  call void @pcc_gc_frame_leave_lifo_local(ptr %call.args.operand.0)
  br label %done
done:
  ret ptr null
}
'''


@pytest.mark.parametrize("mutation", [
    None, "helper", "pending-edge", "root-map", "wrong-leave", "extra-leave", "inline-tls",
])
def test_executable_ir_checker_follows_actual_pending_error_to_two_roots(monkeypatch, mutation):
    from tests.python import test_owned_cleanup_helper_ir as stackmaps

    # Isolate CFG checks. Real dumps still undergo the unmocked stackmap check.
    monkeypatch.setattr(stackmaps, "verify_stackmaps", lambda text: None)
    text = EDGE_IR
    if mutation == "helper":
        text = text.replace("@py_cleanup_two_roots_preserving_exception", "@missing_cleanup")
    elif mutation == "pending-edge":
        text = text.replace("label %cleanup, label %done", "label %done, label %cleanup")
    elif mutation == "root-map":
        text = text.replace("constant i32 1", "constant i32 2")
    elif mutation == "wrong-leave":
        text = text.replace("@pcc_gc_frame_leave_lifo_local", "@pcc_gc_frame_leave_lifo")
    elif mutation == "extra-leave":
        text = text.replace("  br label %done", "  call void @pcc_gc_frame_leave_lifo(ptr %call.args.operand.0)\n  br label %done")
    elif mutation == "inline-tls":
        text = text.replace("  br label %done", "  call void @py_tls_exc_swap_slot(ptr %call.args.operand.0)\n  br label %done")
    if mutation is None:
        lifecycle.assert_compiler_two_root_error_edge([text])
    else:
        with pytest.raises(AssertionError):
            lifecycle.assert_compiler_two_root_error_edge([text])


def test_executable_ir_root_map_cannot_be_borrowed_from_another_module(monkeypatch):
    from tests.python import test_owned_cleanup_helper_ir as stackmaps

    monkeypatch.setattr(stackmaps, "verify_stackmaps", lambda text: None)
    wrong_program_map = EDGE_IR.replace("constant i32 1", "constant i32 2")
    with pytest.raises(AssertionError):
        lifecycle.assert_compiler_two_root_error_edge([
            "@map = internal constant i32 1\n", wrong_program_map,
        ])
    with pytest.raises(AssertionError):
        lifecycle.assert_compiler_two_root_error_edge([EDGE_IR, EDGE_IR])


@pytest.mark.parametrize("dump_written", [True, False])
def test_ir_receipt_is_from_one_executable_compile_and_missing_dump_fails(
    tmp_path, monkeypatch, dump_written,
):
    from scripts import owned_image_linkage

    calls, executions = [], []
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"selected runtime")
    oracle = {"returncode": 0, "stdout": "OK\n", "stderr": ""}

    def reference(program, expected, directory, *, arguments=()):
        assert arguments == ("cyclic-reference",)
        source = directory / "program.py"
        source.write_text(program)
        return source, oracle

    def compiler(source, output, **options):
        calls.append((source, output, options))
        assert "emit_llvm_only" not in options
        dump = Path(os.environ["PCC_DEBUG_SELF_IR_DUMP_DIR"])
        assert list(dump.iterdir()) == []
        if dump_written:
            (dump / "self_backend_input_0.ll").write_text("actual executable input")
        Path(output).write_bytes(b"native image")

    def execution(directory, label, command, environment):
        executions.append(environment["PCC_GC_BACKEND"])
        return oracle

    monkeypatch.setattr(owned, "assert_reference_program", reference)
    monkeypatch.setattr(owned, "_record_execution", execution)
    monkeypatch.setattr(owned_image_linkage, "inspect_owned_image", lambda image: {
        "libpython": False, "binary_sha256": "test-image-hash",
    })
    seen = []
    kwargs = dict(reference_arguments=("cyclic-reference",), emitted_ir_check=seen.append)
    before = os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR")
    if dump_written:
        owned.assert_owned_program("pass\n", "OK\n", tmp_path, compiler, "pcc1", archive,
                                   SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err="")), **kwargs)
        assert seen == [["actual executable input"]]
        assert executions == ["0", "1", "2", "3", "4"]
        receipt = json.loads((tmp_path / "ownership-regression.json").read_text())
        assert receipt["gc_backends"] == list(range(5))
        assert set(receipt["executable_input_ir"]) == {"self_backend_input_0.ll"}
    else:
        with pytest.raises(AssertionError, match="no self-backend IR receipt"):
            owned.assert_owned_program("pass\n", "OK\n", tmp_path, compiler, "pcc1", archive,
                                       SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err="")), **kwargs)
        assert executions == []
    assert len(calls) == 1
    assert os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR") == before


@pytest.mark.parametrize("objects, accepted", [
    ((), True), (("fixture.o",), True), ((Path("fixture.obj"),), True),
    ("fixture.o", False), (("-bad.o",), False), (("@args.obj",), False), (("fixture.a",), False),
])
def test_explicit_native_fixture_forwards_only_typed_objects_with_original_budget(
    tmp_path, objects, accepted,
):
    # Extract the real fixture closure, with a recorder at its process boundary.
    tree = ast.parse((ROOT / "tests/python/conftest.py").read_text())
    fixture = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "python_program_compiler")
    function = next(node for node in fixture.body if isinstance(node, ast.FunctionDef)
                    and node.name == "compile_program")
    invocations = []
    output = tmp_path / "native.out"

    def run(command, **options):
        invocations.append((command, options))
        output.write_bytes(b"native")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    namespace = {"compiler": "selected-pcc1", "os": os, "Path": Path,
                 "run_process_group_timeout": run}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "native-fixture", "exec"), namespace)
    compile_program = namespace["compile_program"]
    arguments = dict(backend="self", runtime_archive="selected.a", object_inputs=objects)
    if accepted:
        compile_program("source.py", str(output), **arguments)
        command, options = invocations[0]
        assert command == ["selected-pcc1", "--backend", "self", "--ir-scaffold", "on",
                           "source.py", "-o", str(output)] + [part for path in objects
                                                             for part in ("--link-arg", os.fspath(path))]
        assert options["timeout"] == 400
        assert options["env"]["PCC_HOST_PYTHON"] == "/usr/bin/false"
    else:
        with pytest.raises((AssertionError, TypeError)):
            compile_program("source.py", str(output), **arguments)
        assert invocations == []
    with pytest.raises(TypeError):
        compile_program("source.py", str(output), backend="self", runtime_archive="selected.a",
                        link_args=("fixture.o",))
