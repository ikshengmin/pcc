"""Only exact permanent image origins can omit counted address leases."""

import os
from pathlib import Path
import subprocess

import pytest

from test_foreign_address_leases import Model, _functions


ROOT = Path(__file__).resolve().parents[2]


def test_static_literal_kernel_compiles_with_exact_freestanding_boundaries(tmp_path):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python

    output = tmp_path / "literal_kernel.ll"
    compile_python(
        str(ROOT / "pcc/runtime/py/freestanding_gc_root_operations.py"),
        str(output), emit_llvm_only=True, python_library=True,
        backend="self", libpython_mode="off", target_triple="arm64-apple-darwin",
    )
    ir_text = output.read_text()
    assert "define external i64 @pcc_gc_static_literal_contains" in ir_text
    assert emit_owned_object(ir_text, "arm64-apple-darwin")


class StaticModel(Model):
    def __init__(self, backend=0, capacity=8, probes=4):
        super().__init__(backend)
        self.ns.update(
            STATIC_LITERAL_CAPACITY=capacity, STATIC_LITERAL_PROBES=probes,
            PY_FLAG_IMMORTAL=1, PYOBJECTHEADER_TYPE_TAG_OFFSET=8,
            logical_shift_right_i64=lambda value, count: (value & ((1 << 64) - 1)) >> count,
            pcc_gc_granule_span=lambda value: 1 if value in self.proven else 0,
        )
        _functions(ROOT / "pcc/runtime/py/freestanding_gc_root_operations.py", {
            "_static_literal_probe", "pcc_gc_static_literal_contains",
            "pcc_gc_static_literal_record",
        }, self.ns)

    def record(self, value):
        self.ns["pcc_gc_static_literal_record"](value)

    def contains(self, value):
        return self.ns["pcc_gc_static_literal_contains"](value)


@pytest.mark.parametrize("backend", range(5))
def test_static_literal_lease_needs_no_mode_or_graph_transaction(backend):
    model = StaticModel(backend)
    slot = model.object(4096, flags=1, node=False, proven=False)
    model.ns["pcc_gc_managed_pointer_index_contains"] = lambda value: int(value == 4096)
    model.record(4096)
    model.events.clear()
    assert model.acquire(slot) == 0
    assert model.release(slot, 0) == 0
    assert model.pinned(4096) == 1
    assert model.active() == 0 and model.events == []


def test_heap_immortal_and_interior_addresses_are_not_static_origins():
    model = StaticModel(3)
    heap = model.object(4096, flags=1)
    model.record(4096)
    assert model.contains(4096) == 0
    assert model.acquire(heap) == 1
    assert model.release(heap, 1) == 0
    model.object(8192, flags=1, node=False, proven=False)
    model.object(8224, flags=1, node=False, proven=False)
    model.record(8192)
    model.record(8224)
    assert model.contains(8192) == model.contains(8224) == 1
    assert model.contains(8200) == model.contains(8208) == 0


def test_full_table_preserves_admitted_origins_and_declines_new_entries():
    model = StaticModel(capacity=2, probes=2)
    for value in (4096, 8192, 12288):
        model.object(value, flags=1, node=False, proven=False)
        model.record(value)
    assert model.contains(4096) == model.contains(8192) == 1
    assert model.contains(12288) == 0
    model.record(4096)
    assert model.contains(4096) == 1


@pytest.mark.parametrize("flags,tag", [(0, 4), (1, 5)])
def test_nonimmortal_or_nonscalar_headers_are_not_admitted(flags, tag):
    model = StaticModel()
    model.object(4096, flags=flags, tag=tag, node=False, proven=False)
    model.record(4096)
    assert model.contains(4096) == 0


def test_native_static_origins_survive_collection_and_backend_transitions(
    tmp_path, threaded_pcc_runtime_archive,
):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    binary = tmp_path / "static_literal_leases"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/static_literal_leases.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    # The collector is fixed by the first managed allocation: one run each.
    for backend in range(5):
        result = subprocess.run(
            [str(binary), str(backend)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "STATIC_LITERAL_LEASES_OK\n", "",
        ), backend
