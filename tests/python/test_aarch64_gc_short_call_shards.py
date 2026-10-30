"""Owned GC admission survives frontend IR sharding without admitting overrides."""

from types import SimpleNamespace

import pytest

from pcc.backend.self_backend_aarch64_gc_calls import (
    GC_ABI_EXCLUSION_PREFIX,
    GC_ABI_MARKER,
    gc_short_call_lines,
)
from pcc.backend.self_backend_kernel import TYPE_KIND_PTR
from pcc.backend.self_backend_module_symbols import prepare_module_symbols
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.frontends.python.pipeline_ir_split import (
    split_python_ir_module_for_pass_shards,
    split_self_backend_ir_module_for_object_shards,
)


TARGET = "arm64-apple-darwin"
MARKER = "@" + GC_ABI_MARKER + " = internal constant i8 1"
ROUTES = (("pass",), ("object",), ("pass", "object"), ("object", "pass"))
SPLITTERS = {
    "pass": split_python_ir_module_for_pass_shards,
    "object": split_self_backend_ir_module_for_object_shards,
}


def _shards(text, route):
    shards = [text]
    for stage, kind in enumerate(route):
        next_shards = []
        for index, shard in enumerate(shards):
            # The first of two stages separates data from a multi-function
            # shard. The second really splits those functions again, rather
            # than only returning already-single-function inputs unchanged.
            limit = 1 if stage == len(route) - 1 else 1_000_000_000
            next_shards.extend(SPLITTERS[kind](
                shard,
                export_prefix="__" + kind + str(stage) + "_" + str(index) + "_",
                shard_bytes=limit,
            ))
        shards = next_shards
    return shards


def _symbols(text):
    parsed = parse_self_backend_module(text)
    return prepare_module_symbols(
        text, list(parsed.globals_), list(parsed.functions), TARGET,
    )


def _load_expansion(text, callee="pcc_gc_load_ptr"):
    return gc_short_call_lines(
        SimpleNamespace(name="probe"), callee, 0, TYPE_KIND_PTR,
        (TYPE_KIND_PTR, TYPE_KIND_PTR), _symbols(text),
    )


@pytest.fixture(scope="module")
def frontend_ir(tmp_path_factory):
    from pcc.frontends.python.pipeline import compile_python

    directory = tmp_path_factory.mktemp("gc_short_frontend")
    source = directory / "gc_short_frontend.py"
    output = directory / "gc_short_frontend.ll"
    source.write_text(
        "state = [1]\n"
        "def first(value):\n"
        "    return value\n"
        "def second(value):\n"
        "    return first(value)\n"
    )
    compile_python(
        str(source), str(output), emit_llvm_only=True,
        backend="self", libpython_mode="off", target_triple=TARGET,
    )
    return output.read_text()


@pytest.mark.parametrize("route", ROUTES)
def test_frontend_opt_in_remains_local_and_defined_in_every_shard(frontend_ir, route):
    original = _symbols(frontend_ir)
    assert GC_ABI_MARKER in original.internal_symbols
    assert GC_ABI_MARKER in original.defined_symbols
    shards = _shards(frontend_ir, route)
    function_shards = [shard for shard in shards if "define " in shard]
    assert len(function_shards) >= 2
    for shard in shards:
        symbols = _symbols(shard)
        assert GC_ABI_MARKER in symbols.internal_symbols
        assert GC_ABI_MARKER in symbols.defined_symbols
        assert _load_expansion(shard) is not None


def _override_module(marker=MARKER):
    return '''target triple = "arm64-apple-darwin"
''' + marker + '''
@state = internal global i64 0
declare ptr @pcc_gc_load_borrowed_ptr(ptr, ptr)
define ptr @pcc_gc_load_ptr(ptr %owner, ptr %slot) {
entry:
  ret ptr inttoptr (i64 23 to ptr)
}
define ptr @first(ptr %slot) {
entry:
  %value = call ptr @pcc_gc_load_ptr(ptr null, ptr %slot)
  ret ptr %value
}
define ptr @second(ptr %slot) {
entry:
  %value = call ptr @pcc_gc_load_borrowed_ptr(ptr null, ptr %slot)
  ret ptr %value
}
'''


@pytest.mark.parametrize("route", ROUTES)
def test_sibling_definition_keeps_original_local_callee_exclusion(route):
    shards = _shards(_override_module(), route)
    first = next(shard for shard in shards if "define ptr @first(" in shard)
    second = next(shard for shard in shards if "define ptr @second(" in shard)
    assert "pcc_gc_load_ptr" not in _symbols(first).defined_symbols
    exclusion = GC_ABI_EXCLUSION_PREFIX + "pcc_gc_load_ptr"
    for shard in shards:
        assert shard.count("@" + exclusion + " = internal constant i8 1") == 1
        assert _load_expansion(shard) is None
    # An override of one helper must not disable other owned runtime imports.
    assert _load_expansion(second, "pcc_gc_load_borrowed_ptr") is not None


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("marker", (
    "",
    "@" + GC_ABI_MARKER + " = external constant i8",
    "@" + GC_ABI_MARKER + " = constant i8 1",
    "@" + GC_ABI_MARKER + " = internal global i8 1",
))
def test_sharding_does_not_infer_opt_in_from_gc_names(route, marker):
    for shard in _shards(_override_module(marker), route):
        assert _load_expansion(shard, "pcc_gc_load_borrowed_ptr") is None


@pytest.mark.parametrize("route", ROUTES)
def test_unrelated_mutable_global_keeps_one_definition(route):
    shards = _shards(_override_module(), route)
    definitions = [
        line for shard in shards for line in shard.splitlines()
        if line.startswith("@") and "state = global i64 0" in line
    ]
    assert len(definitions) == 1
