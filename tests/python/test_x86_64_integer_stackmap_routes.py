"""Owned x64 direct emission uses integer routes, retaining the text oracle."""
from dataclasses import replace

import pytest

from pcc.backend import self_backend_emit as emit
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend import self_backend_x86_64_linux as x86
from pcc.backend.self_backend_indexed_codec import (
    decode_indexed_module_file, encode_indexed_module_file,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.target_objects import emit_indexed_assembly, emit_x86_64_streamed_module
from tests.python.test_stackmap_global_index import source_text

pytestmark = pytest.mark.usefixtures("precise_stackmap_tables")


@pytest.fixture
def sidecar(tmp_path):
    p = tmp_path / "routes.pidx"
    encode_indexed_module_file(
        str(p), parse_self_backend_module(
            source_text(shape="derived", triple="x86_64-linux-gnu", functions=3),
        ),
    )
    return p


@pytest.mark.parametrize("streamed", [False, True])
def test_exact_owned_x64_routes_match_text_index(sidecar, monkeypatch, streamed):
    def run():
        module = decode_indexed_module_file(str(sidecar))
        if streamed:
            return emit_x86_64_streamed_module(module).encode_object(None)
        return emit_indexed_assembly(module)

    actual = run()
    original = x86.emit_function_blocks
    observed = []

    def legacy(func, **kwargs):
        observed.append(kwargs.pop("_indexed_x86_routes", False))
        return original(func, **kwargs)

    monkeypatch.setattr(x86, "emit_function_blocks", legacy)
    assert run() == actual
    assert observed and all(observed)


def test_owned_integer_route_index_preserves_alias_and_repeated_rows():
    module = parse_self_backend_module("""
target triple = "x86_64-unknown-linux-gnu"
define void @probe() {
entry:
  br label %.1
.1:
  ret void
}
""")
    func = module.functions[0]
    kernel = get_indexed_function_kernel(func)
    label_a, label_b = ".Lroute_a", ".Lroute_b"
    plan = maps.FunctionStackMapPlan(
        "probe", 1, 16, ".Lend",
        (
            maps.PlannedSafepoint(1, label_a, 0, (),
                                  reloads=(maps.PlannedManagedReload(-8, -16),)),
            maps.PlannedSafepoint(2, label_b, 0, ()),
        ),
        ((".1", label_a), ("%.1", label_b)),
        ((".1", 0, label_a), ("%.1", 0, label_b)),
        (("%.1", label_a, True), (".1", label_b, False)),
        "x86_64-linux",
    )
    expected = plan.build_line_index()
    actual = emit._x86_block_id_route_indexes(plan, kernel)
    block_id = kernel.block_id(".1")
    assert block_id >= 0
    for text_index, id_index in zip(expected, actual):
        assert id_index[block_id] == emit._block_line_index_get(text_index, ".1")


def test_custom_plan_index_keeps_original_text_owner(sidecar, monkeypatch):
    calls = []
    original = maps.FunctionStackMapPlan.build_line_index

    def custom(self):
        calls.append(self.function_name)
        return original(self)

    monkeypatch.setattr(maps.FunctionStackMapPlan, "build_line_index", custom)
    emit_x86_64_streamed_module(decode_indexed_module_file(str(sidecar))).encode_object(None)
    assert calls


def test_unknown_route_still_checks_missing_reload_before_discard():
    module = parse_self_backend_module("""
target triple = "x86_64-unknown-linux-gnu"
define void @probe() {
entry:
  ret void
}
""")
    kernel = get_indexed_function_kernel(module.functions[0])
    plan = maps.FunctionStackMapPlan(
        "probe", 1, 0, ".Lend", (), (), (("missing", 0, ".Lunknown"),),
        (), "x86_64-linux",
    )
    with pytest.raises(KeyError) as text:
        plan.build_line_index()
    with pytest.raises(KeyError) as numeric:
        emit._x86_block_id_route_indexes(plan, kernel)
    assert numeric.value.args == text.value.args
