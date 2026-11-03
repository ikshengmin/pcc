"""Python data model 3.2.4: arbitrary integers remain live and reclaimable.

The body models check scratch-to-managed ownership. Native checks below use
the real allocator and collector rather than accepting numerical output alone.
"""

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.runtime.py import py_abi_constants as abi


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_int_core.py"


class BigintMemory:
    def __init__(self, tracing=True, initialized=True, fail_managed=False, fail_register=False):
        self.memory = {}
        self.next_address = 4096
        self.events = []
        self.tracing = tracing
        self.fail_managed = fail_managed
        self.fail_register = fail_register
        self.globals = {
            "pcc_gc_config_initialized": int(initialized),
            "pcc_gc_backend4_no_refcounts": int(tracing and initialized),
        }
        self.ns = dict(vars(abi))
        self.ns.update(
            malloc=self.scratch,
            free=lambda obj: self.events.append(("free", obj)),
            null=lambda: 0,
            ptr_is_null=lambda obj: obj == 0,
            global_addr=lambda name: name,
            load_i32=self.load,
            store_i32=self.store,
            store_i64=self.store,
            tag_int=lambda value: (value << 1) | 1,
            pcc_gc_alloc=self.managed,
            pcc_gc_pointer_register=self.register,
            pcc_gc_config_ensure=self.configure,
            c_abi_export=lambda name: lambda fn: fn,
        )
        names = {
            "py_bigint_alloc", "py_bigint_from_i64", "py_bigint_to_pyobject",
            "py_int_new_heap", "_publish_heap_bigint", "_bigint_fits_tagged",
            "_bigint_tagged_fit_value", "_load_u32", "_store_u32",
        }
        functions = [node for node in ast.parse(SOURCE.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), self.ns)

    def load(self, obj, offset):
        return self.globals[obj] if isinstance(obj, str) else self.memory.get(obj + offset, 0)

    def store(self, obj, offset, value):
        self.memory[obj + offset] = value

    def scratch(self, size):
        obj = self.next_address
        self.next_address += size + 64
        self.events.append(("scratch", obj, size))
        return obj

    def managed(self, size, tag, flags):
        self.events.append(("managed", size, tag, flags))
        if self.fail_managed:
            return 0
        obj = self.next_address
        self.next_address += size + 64
        self.store(obj, abi.PYOBJECTHEADER_REFCOUNT_OFFSET, 1)
        self.store(obj, abi.PYOBJECTHEADER_TYPE_TAG_OFFSET, tag)
        self.store(obj, abi.PYOBJECTHEADER_FLAGS_OFFSET, 32 | abi.PY_FLAG_GC_MALLOC_ALLOC)
        return obj

    def register(self, obj):
        self.events.append(("register", obj))
        return -1 if self.fail_register else 0

    def configure(self):
        self.events.append(("configure",))
        self.globals["pcc_gc_config_initialized"] = 1
        self.globals["pcc_gc_backend4_no_refcounts"] = int(self.tracing)
        return 4 if self.tracing else 0

    def value(self, obj):
        if obj & 1:
            return obj >> 1
        count = self.load(obj, abi.PYINTOBJECT_NDIGITS_OFFSET)
        value = sum(self.load(obj, abi.PYINTOBJECT_DIGITS_OFFSET + i * 4) << (32 * i)
                    for i in range(count))
        return value * self.load(obj, abi.PYINTOBJECT_SIGN_OFFSET)


@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("initialized", [False, True])
def test_normalized_scratch_is_published_with_managed_header_and_exact_extent(sign, initialized):
    model = BigintMemory(initialized=initialized)
    scratch = model.ns["py_bigint_alloc"](100)
    model.store(scratch, abi.PYINTOBJECT_SIGN_OFFSET, sign)
    model.store(scratch, abi.PYINTOBJECT_NDIGITS_OFFSET, 4)
    model.store(scratch, abi.PYINTOBJECT_DIGITS_OFFSET, 19)
    model.store(scratch, abi.PYINTOBJECT_DIGITS_OFFSET + 12, 1)
    result = model.ns["py_bigint_to_pyobject"](scratch)
    assert result != scratch
    assert model.value(result) == sign * ((1 << 96) + 19)
    assert model.load(result, abi.PYOBJECTHEADER_REFCOUNT_OFFSET) == 1
    assert model.load(result, abi.PYOBJECTHEADER_TYPE_TAG_OFFSET) == abi.PY_TYPE_INT
    assert model.load(result, abi.PYOBJECTHEADER_FLAGS_OFFSET) == 32 | abi.PY_FLAG_GC_MALLOC_ALLOC
    assert ("managed", abi.PYINTOBJECT_DIGITS_OFFSET + 16, abi.PY_TYPE_INT, 0) in model.events
    assert model.events[-1] == ("free", scratch)
    assert not any(event[0] == "register" for event in model.events)
    assert (("configure",) in model.events) is not initialized


@pytest.mark.parametrize("value", [0, -7, 1 << 62, -(1 << 63)])
def test_explicit_heap_integer_preserves_heap_identity_even_for_small_values(value):
    model = BigintMemory()
    result = model.ns["py_int_new_heap"](value)
    assert result != 0 and result & 1 == 0
    assert model.value(result) == value
    assert sum(event[0] == "managed" for event in model.events) == 1
    assert sum(event[0] == "free" for event in model.events) == 1


def test_tagged_result_discards_scratch_without_managed_allocation():
    model = BigintMemory(initialized=False)
    scratch = model.ns["py_bigint_from_i64"](-17)
    result = model.ns["py_bigint_to_pyobject"](scratch)
    assert model.value(result) == -17 and result & 1
    assert [event[0] for event in model.events] == ["scratch", "free"]


def test_managed_allocation_failure_consumes_the_private_scratch():
    model = BigintMemory(fail_managed=True)
    assert model.ns["py_int_new_heap"](1 << 62) == 0
    assert [event[0] for event in model.events] == ["scratch", "managed", "free"]


@pytest.mark.parametrize("fail_register", [False, True])
def test_counted_publication_retains_its_existing_raw_allocation(fail_register):
    model = BigintMemory(tracing=False, fail_register=fail_register)
    scratch = model.ns["py_bigint_from_i64"](1 << 62)
    result = model.ns["py_bigint_to_pyobject"](scratch)
    assert result == (0 if fail_register else scratch)
    assert model.load(scratch, abi.PYOBJECTHEADER_FLAGS_OFFSET) == abi.PY_FLAG_GC_MALLOC_ALLOC
    assert [event[0] for event in model.events] == ["scratch", "register"] + (["free"] if fail_register else [])


VALUES = '''
def values():
    first = (1 << 120) + 19
    keep = [first, -first, first * first]
    for index in range(6):
        gc.collect()
    assert keep[0] is first
    print(keep)
'''

PROGRAM = '''import gc
from pcc.extern import extern, c_int64
probe = extern("bigint_publication_probe", (), c_int64)
remaining = extern("bigint_remaining", (), c_int64)
scrub = extern("bigint_clear_dead_stack", (), c_int64)
''' + VALUES + '''
def main():
    gc.disable()
    print(probe())
    for index in range(6):
        scrub()
        gc.collect()
    print(remaining())
    gc.enable()
    values()
main()
'''


@pytest.mark.integration
def test_published_bigints_enter_tracing_and_are_reclaimed(tmp_path, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    reference = subprocess.run(
        [sys.executable, "-c", "import gc\n" + VALUES + "\nvalues()"],
        capture_output=True, text=True, timeout=20,
    )
    assert reference.returncode == 0 and reference.stderr == "", reference
    fixture = Path(__file__).with_name("fixtures") / "gc_bigint_publication.c"
    harness = (fixture.read_text()
               .replace("@SIGN@", str(abi.PYINTOBJECT_SIGN_OFFSET))
               .replace("@NDIGITS@", str(abi.PYINTOBJECT_NDIGITS_OFFSET))
               .replace("@DIGITS@", str(abi.PYINTOBJECT_DIGITS_OFFSET)))
    target = host_target_triple()
    helper = tmp_path / "bigint.o"
    helper.write_bytes(emit_owned_object(_c_ir(harness, target), target))
    source = tmp_path / "bigint.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "bigint"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    for backend, tracing in ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1)):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                     PCC_GC4_TRACING_ONLY=str(tracing)),
        )
        expected = ("31" if tracing else "5") + "\n0\n" + reference.stdout
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), (backend, tracing)
