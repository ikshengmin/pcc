"""Fixed call tuples retain argument owners without an intermediate list."""

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.python.test_slot_call_operand_roots import _calls, _emit, _probe_function


@pytest.mark.parametrize("count", (0, 1, 2, 8, 9, 32))
def test_fixed_argument_tuple_has_a_bounded_direct_shape(count):
    arguments = ", ".join("value" for _ in range(count))
    body = _probe_function(_emit("def probe(value):\n    return slot_args_probe(" + arguments + ")\n"))
    if count <= 8:
        assert len(_calls(body, "py_tuple_from_owned_roots")) == (1 if count else 0)
        assert len(_calls(body, "py_tuple_new")) == (0 if count else 1)
        assert not _calls(body, "py_tuple_set_item")
        assert not _calls(body, "py_list_new")
        assert not _calls(body, "py_list_append")
        assert not _calls(body, "py_tuple_from_list")
    else:
        assert len(_calls(body, "py_list_new")) == 1
        assert len(_calls(body, "py_list_append")) == count
        assert len(_calls(body, "py_tuple_from_list")) == 1


def test_tuple_allocation_follows_all_user_argument_evaluation():
    text = _emit("""def first():
    print("first")
    return [1]
def second():
    print("second")
    return [2]
def probe():
    return slot_args_probe(first(), second())
""")
    body = _probe_function(text)
    first = re.search(r"call [^\n]*@user_[^\n(]*_first\(", body)
    second = re.search(r"call [^\n]*@user_[^\n(]*_second\(", body)
    allocation = _calls(body, "py_tuple_from_owned_roots")
    assert first is not None and second is not None and len(allocation) == 1
    assert first.start() < second.start() < body.index(allocation[0])


def test_splat_argument_tuple_keeps_streaming_evaluation():
    body = _probe_function(_emit("def probe(values):\n    return slot_args_probe(1, *values, 2)\n"))
    assert _calls(body, "py_list_extend")
    assert _calls(body, "py_tuple_from_list")


PROGRAM = '''import gc
import weakref
events = []
references = []
class Item:
    def __init__(self, name):
        self.name = name
        references.append(weakref.ref(self))
    def __del__(self):
        events.append("del:" + self.name)
        gc.collect()
def make(name):
    events.append("arg:" + name)
    gc.collect()
    return Item(name)
def keyword():
    events.append("keyword")
    gc.collect()
    assert references[0]() is not None and references[1]() is not None
    return "marker"
def capture(*args, flag=None):
    events.append("call")
    gc.collect()
    return args, flag
class Callable:
    def __call__(self, *args, flag=None):
        return capture(*args, flag=flag)
def invoke(fn):
    events.clear()
    references.clear()
    result = fn(make("a"), make("b"), flag=keyword())
    assert events == ["arg:a", "arg:b", "keyword", "call"]
    assert result[0][0] is references[0]() and result[0][1] is references[1]()
    assert result[1] == "marker"
    del result
    gc.collect()
    assert references[0]() is None and references[1]() is None
    assert events[-2:] == ["del:b", "del:a"]
    assert fn() == ((), None)
    alias = [7]
    result = fn(alias, alias, 1267650600228229401496703205376, "héllo", b"bytes", None, True, -1267650600228229401496703205376)
    assert result[0][0] is alias and result[0][1] is alias
    assert result[0][2:] == (1267650600228229401496703205376, "héllo", b"bytes", None, True, -1267650600228229401496703205376)
    assert fn(0, 1, 2, 3, 4, 5, 6, 7, 8)[0] == (0, 1, 2, 3, 4, 5, 6, 7, 8)
    assert fn(0, *[1, 2], 3)[0] == (0, 1, 2, 3)
def choose():
    return capture
module_result = choose()(1, 2)
assert module_result == ((1, 2), None)
invoke(capture)
invoke(Callable())
print("FIXED_CALL_TUPLE_OK")
'''

PARKING_PROGRAM = '''import gc
import pcc.virtual_thread as vt
events = []
def pause(value):
    events.append("pause")
    vt.yield_now()
    gc.collect()
    return value
def capture(first, second):
    assert first is second
    return first
def worker():
    value = [7]
    fn = capture
    result = fn(value, pause(value))
    result.append(9)
    assert result is value and result == [7, 9]
    assert events == ["pause"]
    return 1
def main():
    task = vt.spawn(worker)
    vt.run(1, 128)
    assert vt.result(task) == 1
    print("FIXED_ARGS_PARKING_OK")
main()
'''


def test_fixed_call_arguments_reference(tmp_path):
    source = tmp_path / "reference.py"
    source.write_text(PROGRAM)
    run = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0 and run.stderr == "" and run.stdout == "FIXED_CALL_TUPLE_OK\n", (run.stdout, run.stderr)


@pytest.mark.integration
@pytest.mark.parametrize("program,expected", ((PROGRAM, "FIXED_CALL_TUPLE_OK"), (PARKING_PROGRAM, "FIXED_ARGS_PARKING_OK")), ids=("lifetime", "parking"))
def test_fixed_call_arguments_native_all_gc(tmp_path, threaded_pcc_runtime_archive, program, expected):
    source = tmp_path / "fixed_call.py"
    source.write_text(program)
    binary = source.with_suffix("")
    compile_python(str(source), str(binary), backend="self", libpython_mode="off", ir_scaffold_mode="on", runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_GC_MINOR_ALLOC_MAX="4096", PATH="/nonexistent"))
        (tmp_path / ("gc" + str(backend) + ".stdout")).write_text(run.stdout)
        (tmp_path / ("gc" + str(backend) + ".stderr")).write_text(run.stderr)
        assert run.returncode == 0 and run.stderr == "" and run.stdout == expected + "\n", (backend, run.returncode, run.stdout, run.stderr)


@pytest.mark.integration
def test_fixed_argument_tuple_allocation_failure_retires_captured_owners(tmp_path, threaded_pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import _run_self_link_command
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.owned_ir_validation import verify_ir_text

    source = tmp_path / "forced_tuple_oom.py"
    source.write_text('''events = []
class Item:
    def __init__(self, label):
        self.label = label
    def __del__(self):
        events.append("drop:" + self.label)
def make(label):
    events.append(label)
    return Item(label)
def target(first, second):
    raise AssertionError("entered target")
def probe(fn):
    return fn(make("a"), make("b"))
def main():
    try:
        probe(target)
    except MemoryError as error:
        assert str(error) == "forced argument tuple failure"
        assert events == ["a", "b", "drop:b", "drop:a"]
    else:
        raise AssertionError("missing failure")
    print("FIXED_TUPLE_OOM_OK")
main()
''')
    target = host_target_triple()
    original = tmp_path / "original.ll"
    compile_python(str(source), str(original), emit_llvm_only=True, backend="self",
                   libpython_mode="off", ir_scaffold_mode="on", target_triple=target)
    text = original.read_text()
    function = re.search(r"^define [^\n]*@user_forced_tuple_oom_probe\([^\n]*\).*?^}", text, re.M | re.S)
    assert function is not None
    body = function.group(0)
    assert len(_calls(body, "py_tuple_from_owned_roots")) == 1
    assert not _calls(body, "py_list_new") and not _calls(body, "py_tuple_from_list")
    # Reject construction before any owner moves, at the caller-side factory boundary.
    # All captured roots and generated error/cleanup code remain production IR.
    replacement = body.replace("@py_tuple_from_owned_roots(", "@force_tuple_new_failure(")
    patched = text[:function.start()] + replacement + text[function.end():]
    patched += "\ndeclare i64 @force_tuple_new_failure(ptr, i64, ptr)\n"
    verify_ir_text(patched)
    (tmp_path / "injected.ll").write_text(patched)
    message = "forced argument tuple failure"
    helper = ('target triple = "' + target + '"\n'
              + '@forced_message = private constant [' + str(len(message) + 1)
              + ' x i8] c"' + message + '\\00"\n'
              + 'declare ptr @py_exc_new(i64, ptr)\ndeclare void @py_raise_owned(ptr)\n'
              + 'define i64 @force_tuple_new_failure(ptr %sources, i64 %count, ptr %output) {\nentry:\n'
              + '  %error = call ptr @py_exc_new(i64 19, ptr @forced_message)\n'
              + '  call void @py_raise_owned(ptr %error)\n  ret i64 -1\n}\n')
    verify_ir_text(helper)
    (tmp_path / "failure.ll").write_text(helper)
    program_object, failure_object = tmp_path / "program.o", tmp_path / "failure.o"
    program_object.write_bytes(emit_owned_object(patched, target))
    failure_object.write_bytes(emit_owned_object(helper, target))
    binary = tmp_path / "forced_tuple_oom"
    _run_self_link_command(["/usr/bin/false"], None, binary, threaded_pcc_runtime_archive,
                           (str(program_object), str(failure_object)), False, needs_libpython=False)
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH="/nonexistent"))
        (tmp_path / ("oom-gc" + str(backend) + ".stdout")).write_text(run.stdout)
        (tmp_path / ("oom-gc" + str(backend) + ".stderr")).write_text(run.stderr)
        assert run.returncode == 0 and run.stderr == "" and run.stdout == "FIXED_TUPLE_OOM_OK\n", (
            backend, run.returncode, run.stdout, run.stderr)
