"""Count-free lifecycle and native cleanup-slot witnesses for one error edge.

PEP 442's cyclic-isolate sequence is the reference for collection death:
weak callbacks, finalizers, resurrection check, then disposal. CPython's
reference run alone makes Token cyclic; tracing-native runs remain acyclic.
The original GC0 program separately retains its immediate-death assertions.
"""

import hashlib
import json
import os
from pathlib import Path
import re

from tests.python.owned_regression_support import _record_execution


TRACING_TWO_ROOT_PROGRAM = '''import gc
import sys
import threading
import weakref

REFERENCE_CYCLE = len(sys.argv) > 1 and sys.argv[1] == "cyclic-reference"
events = []
references = []
handled = []
failures = []
selected = ValueError("two-root selected")

class Token:
    def __init__(self):
        if REFERENCE_CYCLE:
            self.cycle = self
    def __del__(self):
        events.append("finalizer")
        gc.collect()

def callback(reference):
    events.append("weakref")
    gc.collect()

def make():
    token = Token()
    references.append(weakref.ref(token, callback))
    return token

def fail():
    raise selected

def consume(*arguments):
    raise AssertionError("entered callee")

def probe(callee):
    return callee(make(), fail())

def produce():
    try:
        try:
            probe(consume)
        except ValueError as caught:
            assert caught is selected
            handled.append(True)
        else:
            raise AssertionError("lost selecting exception")
    except BaseException as error:
        failures.append(str(error))

def main():
    if REFERENCE_CYCLE:
        gc.disable()
    worker = threading.Thread(target=produce)
    worker.start()
    worker.join()
    assert failures == [], failures
    assert handled == [True]
    # No referent was loaded into this observing thread. The producer's
    # stack is gone; its strong operands cannot be diagnostic aliases here.
    gc.collect()
    assert events == ["weakref", "finalizer"], events
    assert len(references) == 1 and references[0]() is None
    # This is a repeat-finalization check, not extra time to satisfy death.
    gc.collect()
    assert events == ["weakref", "finalizer"], events
    assert references[0]() is None
    print("TWO_ROOT_TRACING_CLEANUP_OK")

main()
'''


SLOT_PROBE_PROGRAM = '''from pcc.extern import c_int64, c_obj, extern

probe = extern("test_two_root_cleanup_slots", (c_obj, c_obj), c_int64)

class Owner:
    pass

def main():
    value = Owner()
    selected = ValueError("native slot postcondition")
    status = probe(value, selected)
    assert status == 0, ("two-root cleanup slot status", status)
    print("TWO_ROOT_SLOTS_OK")

main()
'''


def assert_compiler_two_root_error_edge(modules):
    """Verify this compiler's emitted error edge, not a separate host emit.

    The original host verifier also checks private-frame provenance metadata.
    Here the actual selected compiler must emit physical one-slot roots and
    the exact pending-error route to their cleanup and reverse retirement.
    """
    if modules and any(isinstance(module, bytes) for module in modules):
        assert all(isinstance(module, bytes) for module in modules), "mixed executable IR representations"
        from tests.python.two_root_indexed_lifecycle_support import (
            assert_indexed_compiler_two_root_error_edge,
        )

        return assert_indexed_compiler_two_root_error_edge(modules)
    from tests.python.test_owned_cleanup_helper_ir import verify_stackmaps

    functions = []
    for text in modules:
        verify_stackmaps(text)
        for name, body in re.findall(
            r'^define[^\n]*@([\w.]+_probe)\([^\n]*\n(.*?)^}', text, re.M | re.S,
        ):
            if re.search(r'@' + re.escape(name[:-5]) + r'fail\(', body):
                functions.append((text, name, body))
    assert len(functions) == 1
    text, name, body = functions[0]
    prefix = name[:-5]
    assert "@py_list_new(" not in body and "@py_tuple_from_list(" not in body
    blocks = dict(re.findall(r'^([\w.]+):\n(.*?)(?=^[\w.]+:|\Z)', body, re.M | re.S))
    aliases = dict(re.findall(r'(%[\w.]+) = bitcast ptr ([%@][\w.]+) to ptr', body))

    def original(value):
        seen = set()
        while value in aliases:
            assert value not in seen
            seen.add(value)
            value = aliases[value]
        return value

    cleanup_edges = set()
    for block_name, block in blocks.items():
        call = re.search(r'@py_cleanup_two_roots_preserving_exception\(ptr (%[\w.]+), ptr (%[\w.]+)\)', block)
        if call is None:
            continue
        roots = tuple(original(value) for value in call.groups())
        if not (".args.operand." in roots[0] and ".args.item.operand." in roots[1]):
            continue
        assert roots[0] != roots[1]
        suffixes = []
        for root in roots:
            assert re.search(re.escape(root) + r' = alloca ptr\b', body)
            entries = re.findall(r'@pcc_gc_frame_enter_lifo(_local(?:_mapped)?)\(ptr ([%@][\w.]+), ptr (%[\w.]+)\)', body)
            entries = [(suffix, original(mapping)) for suffix, mapping, slot in entries
                       if original(slot) == root]
            assert len(entries) == 1
            suffix, mapping = entries[0]
            assert re.search(r'^' + re.escape(mapping) + r' = internal constant i32 1\s*$', text, re.M)
            suffixes.append(suffix)
        assert suffixes[0] == suffixes[1]
        leave = "pcc_gc_frame_leave_lifo" + suffixes[0]
        leaves = re.findall(
            r'@(pcc_gc_frame_leave_lifo(?:_local_mapped|_mapped|_local)?)\(ptr (%[\w.]+)\)',
            block,
        )
        assert tuple(original(slot) for symbol, slot in leaves) == tuple(reversed(roots))
        assert all(symbol == leave for symbol, slot in leaves)
        assert call.end() < block.index("@" + leave + "(")
        assert "@py_tls_exc_swap_slot(" not in block and "@pcc_gc_store_root(" not in block
        cleanup_edges.add(block_name)
    assert len(cleanup_edges) == 1
    starts = [block_name for block_name, block in blocks.items()
              if re.search(r'\bcall\b[^\n]*@' + re.escape(prefix) + r'fail\(', block)]
    assert len(starts) == 1
    cursor = starts[0]
    visited = set()
    while True:
        assert cursor not in visited
        visited.add(cursor)
        block = blocks[cursor]
        branch = re.search(r'br i1 (%[\w.]+), label %([\w.]+), label %([\w.]+)', block)
        assert branch is not None
        flag = re.search(r'(%[\w.]+) = call i64 \(\) @py_err_occurred\(\)', block)
        if flag is not None:
            assert re.search(re.escape(branch[1]) + r' = icmp ne i64 ' + re.escape(flag[1]) + r', 0', block)
            cursor = branch[2]
            break
        assert re.search(re.escape(branch[1]) + r' = icmp slt i64 %[^,]+, 0', block)
        cursor = branch[3]
    while cursor not in cleanup_edges:
        assert cursor not in visited
        visited.add(cursor)
        branch = re.search(r'br label %([\w.]+)', blocks[cursor])
        assert branch is not None and "br i1 " not in blocks[cursor]
        cursor = branch[1]


def cleanup_slots_ir(target):
    """Expand the named ABI frame without emitting or executing an object."""
    from pcc.runtime.py.py_abi_constants import C_POINTER_SIZE

    template = Path(__file__).with_name("two_root_cleanup_slots.ll.in").read_text()
    # The ABI probe has one named frame: two operand owners and a saved error.
    first_slot, second_slot, exception_slot = range(3)
    for name, value in {
        "SLOT_COUNT": exception_slot + 1,
        "POINTER_SIZE": C_POINTER_SIZE,
        "FIRST_OFFSET": first_slot * C_POINTER_SIZE,
        "SECOND_OFFSET": second_slot * C_POINTER_SIZE,
        "EXCEPTION_OFFSET": exception_slot * C_POINTER_SIZE,
    }.items():
        template = template.replace("__" + name + "__", str(value))
    return f'target triple = "{target}"\n' + template


def assert_two_root_slots_native(compiler, compiler_mode, archive, directory, capfd):
    """Execute a real ABI postcondition before caller frames can disappear.

    This independent probe complements the generated Python error-edge
    verifier. It never substitutes a cleanup implementation or a callback.
    Both arguments stay owned by its Python caller, so it observes the two
    released slots and original TLS exception without requiring destruction.
    """
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from scripts.owned_image_linkage import inspect_owned_image
    from tests.owned_ir_validation import verify_ir_text

    target = host_target_triple()
    ir_text = cleanup_slots_ir(target)
    verify_ir_text(ir_text)
    (directory / "slots.ll").write_text(ir_text, encoding="utf-8", newline="\n")
    helper = directory / "slots.o"
    helper.write_bytes(emit_owned_object(ir_text, target))
    source = directory / "slots.py"
    source.write_text(SLOT_PROBE_PROGRAM)
    binary = directory / "slots.out"
    requested_mode = compiler_mode
    if compiler.__module__ == "tests.pcc1_route":
        compiler_mode = "pcc1-routed"
    receipt = {
        "schema": "pcc.two-root-slots.v1",
        "status": "RUNNING",
        "run_id": os.environ.get("PCC_NATIVE_OBJECT_GATE_RUN_ID", ""),
        "compiler_fixture_parameter": requested_mode,
        "compiler_mode": compiler_mode,
        "backend": "self",
        "target": target,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_path": str(binary.resolve()),
        "runtime_archive": str(archive),
        "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "probe_ir_sha256": hashlib.sha256(ir_text.encode()).hexdigest(),
        "probe_object_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        "probe_object_producer": "host-pcc-owned-object-emitter",
        "gc_backends": list(range(5)),
        "executions": [],
    }
    receipt_path = directory / "slot-postcondition.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        compiler(
            str(source), str(binary), backend="self", ir_scaffold_mode="on",
            runtime_archive=str(archive), object_inputs=(str(helper),),
        )
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    finally:
        captured = capfd.readouterr()
        (directory / "compiler-wrapper.stdout").write_text(captured.out)
        (directory / "compiler-wrapper.stderr").write_text(captured.err)
    assert binary.is_file(), "Compiler did not emit an executable"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == receipt["source_sha256"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == receipt["runtime_sha256"]
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == receipt["probe_object_sha256"]
    try:
        receipt["linkage"] = inspect_owned_image(binary.read_bytes())
    except Exception as error:
        receipt["status"] = "LINKAGE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    receipt["binary_sha256"] = receipt["linkage"]["binary_sha256"]
    for backend in range(5):
        environment = dict(
            os.environ, PCC_GC_BACKEND=str(backend),
            PCC_GC_REFCOUNT_PROVENANCE_PROBE="2",
            PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1",
            PCC_HOST_PYTHON="/nonexistent/host-python",
            PCC_HOST_PCC="/nonexistent/host-pcc", PATH="",
        )
        environment.pop("LC_ALL", None)
        result = _record_execution(directory, f"gc{backend}", [str(binary)], environment)
        receipt["executions"].append({"gc_backend": backend, **result})
        expected = (0, "TWO_ROOT_SLOTS_OK\n", "")
        actual = tuple(result[key] for key in ("returncode", "stdout", "stderr"))
        if actual != expected:
            receipt["status"] = "NATIVE_EXECUTION_FAILED"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert actual == expected, (compiler_mode, backend, result)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == receipt["runtime_sha256"]
    receipt["status"] = "PASS"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
