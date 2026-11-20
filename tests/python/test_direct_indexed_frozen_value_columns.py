"""Direct IR → frozen indexed kernels: publication, retry and text fallback."""
from __future__ import annotations

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_verify import verify_parsed_function
from pcc.ir import ir
from pcc.ir.direct_indexed_kernel import build_direct_indexed_function


_BUILD_COLUMNS = (
    "definition_blocks", "definition_positions", "value_type_ids", "alloca_type_ids",
    "value_last_use_positions", "value_is_used_flags", "used_value_ids_in_order",
)


def _start(monkeypatch, fused="1", direct_only=True):
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1" if direct_only else "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_FUSE_USES", fused)
    monkeypatch.delenv("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", raising=False)
    monkeypatch.delenv("PCC_TEXT_INDEXED_KERNEL_EMIT", raising=False)
    module = ir.Module(name="value_columns")
    module.triple = "x86_64-unknown-linux-gnu"
    i64 = ir.IntType(64)
    fn = ir.Function(module, ir.FunctionType(i64, [i64]), name="sum")
    fn.args[0].name = "x"
    body = ir.IRBuilder(fn.append_basic_block("entry"))
    first = body.add(fn.args[0], fn.args[0], name="first")
    second = body.add(first, fn.args[0], name="second")
    return module, fn, body, second


def _wire(module, path):
    encode_indexed_module_file(str(path), module)
    return path.read_bytes()


@pytest.mark.parametrize("fused", ["1", "0"])
def test_success_releases_seed_columns_without_erasing_frozen_facts(monkeypatch, fused, tmp_path):
    module, fn, body, result = _start(monkeypatch, fused)
    body.ret(result)
    seed = fn._direct_indexed_builder.seed
    assert len(seed.definition_blocks) == 3
    published = module.direct_indexed_module()
    parsed = published.functions[0]
    assert build_direct_indexed_function(fn) is parsed  # a repeat uses the published kernel
    verify_parsed_function(parsed)
    kernel = parsed.indexed_kernel
    x, first, second = [kernel.value_id(name) for name in kernel.value_names]
    assert (x, first, second) == (0, 1, 2)
    assert [kernel.definition_position(i) for i in (x, first, second)] == [-1, 0, 1]
    assert kernel.last_use(0, first) == 1
    assert all(kernel.value_is_used(i) for i in (x, first, second))
    assert _wire(published, tmp_path / "first.pidx") == _wire(
        module.direct_indexed_module(), tmp_path / "cached.pidx",
    )
    assert all(not getattr(seed, name) for name in _BUILD_COLUMNS)


def test_failed_construction_remains_retryable(monkeypatch, tmp_path):
    module, fn, body, result = _start(monkeypatch)
    later = fn.append_basic_block("later")
    body.branch(later)
    seed = fn._direct_indexed_builder.seed
    with pytest.raises(BackendUnavailable, match="block has no terminator"):
        build_direct_indexed_function(fn)
    assert fn._direct_indexed_function_cache is None
    assert len(seed.definition_blocks) == 3
    # Complete the missing terminator normally and compare to a fresh build.
    body.position_at_end(later)
    body.ret(result)
    expected, other, builder, value = _start(monkeypatch)
    complete = other.append_basic_block("later")
    builder.branch(complete)
    builder.position_at_end(complete)
    builder.ret(value)
    assert _wire(module.direct_indexed_module(), tmp_path / "retry.pidx") == _wire(
        expected.direct_indexed_module(), tmp_path / "normal.pidx",
    )


def test_text_fallback_does_not_release_an_unadopted_seed(monkeypatch, tmp_path):
    module, fn, body, result = _start(monkeypatch, direct_only=False)
    # This valid but externally supplied textual record has no direct record ID.
    body.block.append('%extra = add i64 7, 8')
    body.ret(result)
    reference = parse_self_backend_module(str(module))
    seed = fn._direct_indexed_builder.seed
    before = [list(getattr(seed, name)) for name in _BUILD_COLUMNS]
    published = module.direct_indexed_module()
    assert module._direct_indexed_fallback_records == 1
    verify_parsed_function(published.functions[0])
    assert _wire(published, tmp_path / "fallback.pidx") == _wire(reference, tmp_path / "text.pidx")
    assert [list(getattr(seed, name)) for name in _BUILD_COLUMNS] == before


def test_kernel_adoption_failure_leaves_direct_seed_intact_for_text_fallback(monkeypatch, tmp_path):
    module, fn, body, result = _start(monkeypatch, direct_only=False)
    body.ret(result)
    seed = fn._direct_indexed_builder.seed

    def reject(_parsed):
        raise ValueError("adoption refused")

    with monkeypatch.context() as patch:
        # The direct adapter imports the symbol at module load.
        patch.setattr("pcc.ir.direct_indexed_kernel.get_indexed_function_kernel", reject)
        with pytest.raises(BackendUnavailable, match="adoption refused"):
            build_direct_indexed_function(fn)
    assert fn._direct_indexed_function_cache is None
    assert len(seed.definition_blocks) == 3
    assert len(seed.used_value_ids_in_order) == 5
    # A real, later text edit selects the complete textual body on retry.
    body.block.insert(0, "%extra = add i64 7, 8")
    expected = parse_self_backend_module(str(module))
    recovered = module.direct_indexed_module()
    assert module._direct_indexed_fallback_records == 1
    assert _wire(recovered, tmp_path / "recovered.pidx") == _wire(expected, tmp_path / "text.pidx")
