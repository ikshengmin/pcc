from __future__ import annotations

import hashlib

import pytest
from pcc.backend import self_backend_indexed_codec as indexed_codec
from pcc.backend import self_backend_indexed_emit as indexed_emit
from pcc.backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_indexed_module,
)
from pcc.backend.self_backend_indexed_codec import (
    _ARENA_FIELDS,
    decode_indexed_module_file,
    encode_indexed_module_file,
)
from pcc.backend.self_backend_indexed_emit import emit_indexed_module_file
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.native_object import encode_native_object_from_sections
from pcc.ir import ir


def _arena_values(kernel, field: str):
    return getattr(kernel, field).diagnostic_values()


def test_indexed_emit_debug_phase_labels_native_memory_counters(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PCC_DEBUG_INDEXED_EMIT", "1")

    indexed_emit._debug_phase("probe")

    assert capsys.readouterr().err == (
        "pcc indexed emit phase=probe rss_bytes=-1 "
        "heap_in_use_bytes=-1 heap_capacity_bytes=-1\n"
    )


def _assert_native_json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _assert_native_json_value(item)
        return
    assert isinstance(value, dict), type(value).__name__
    for key, item in value.items():
        assert isinstance(key, str)
        _assert_native_json_value(item)


def _direct_module(monkeypatch):
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    module = ir.Module(name="codec")
    module.triple = "arm64-apple-darwin23.6.0"
    i64 = ir.IntType(64)
    counter = ir.GlobalVariable(module, i64, "counter")
    counter.linkage = "internal"
    counter.initializer = ir.Constant(i64, 7)
    counter.align = 8
    callee = ir.Function(module, ir.FunctionType(i64, [i64]), name="callee")
    function = ir.Function(module, ir.FunctionType(i64, [i64]), name="run")
    function.args[0].name = "value"
    entry = function.append_basic_block("entry")
    yes = function.append_basic_block("yes")
    no = function.append_basic_block("no")
    builder = ir.IRBuilder(entry)
    slot = builder.alloca(i64, name="slot")
    builder.store(function.args[0], slot)
    loaded = builder.load(slot, name="loaded")
    summed = builder.add(loaded, ir.Constant(i64, 2), name="sum")
    called = builder.call(callee, [summed], name="called")
    ok = builder.icmp_signed(">", called, ir.Constant(i64, 0), name="ok")
    builder.cbranch(ok, yes, no)
    builder.position_at_end(yes)
    builder.ret(called)
    builder.position_at_end(no)
    builder.ret(ir.Constant(i64, 0))
    f64 = ir.DoubleType()
    float_function = ir.Function(
        module,
        ir.FunctionType(f64, [f64]),
        name="float_run",
    )
    float_function.args[0].name = "value"
    float_entry = float_function.append_basic_block("entry")
    float_builder = ir.IRBuilder(float_entry)
    float_sum = float_builder.fadd(
        float_function.args[0],
        ir.Constant(f64, 1.5),
        name="sum",
    )
    float_builder.ret(float_sum)
    return module.direct_indexed_module()


def test_indexed_module_codec_roundtrips_every_scalar_plane_and_assembly(
    tmp_path,
    monkeypatch,
):
    module = _direct_module(monkeypatch)
    path = tmp_path / "module.pidx"
    second = tmp_path / "module-second.pidx"
    host_json_dumps = indexed_codec.json.dumps

    def checked_json_dumps(value, **kwargs):
        _assert_native_json_value(value)
        return host_json_dumps(value, **kwargs)

    monkeypatch.setattr(indexed_codec.json, "dumps", checked_json_dumps)

    encode_indexed_module_file(str(path), module)
    encode_indexed_module_file(str(second), module)
    restored = decode_indexed_module_file(str(path))

    assert path.read_bytes() == second.read_bytes()
    assert restored.triple == module.triple
    assert restored.globals_ == module.globals_
    assert len(restored.functions) == len(module.functions) == 2
    for original_function, restored_function in zip(
        module.functions,
        restored.functions,
    ):
        assert (
            restored_function.name,
            restored_function.ret_type,
            restored_function.args,
            restored_function.is_global,
            restored_function.is_vararg,
        ) == (
            original_function.name,
            original_function.ret_type,
            original_function.args,
            original_function.is_global,
            original_function.is_vararg,
        )

        original_kernel = get_indexed_function_kernel(original_function)
        restored_kernel = get_indexed_function_kernel(restored_function)
        assert restored_kernel.block_names == original_kernel.block_names
        assert restored_kernel.value_names == original_kernel.value_names
        assert restored_kernel.call_texts == original_kernel.call_texts
        assert restored_kernel.types == original_kernel.types
        assert (
            restored_kernel.instruction_arithmetic_flag_values
            == original_kernel.instruction_arithmetic_flag_values
        )
        assert (
            restored_kernel.cold_instruction_data
            == original_kernel.cold_instruction_data
        )
        for _wire_name, kernel_field, _seed_field in _ARENA_FIELDS:
            assert _arena_values(restored_kernel, kernel_field) == _arena_values(
                original_kernel,
                kernel_field,
            )

    original_asm = emit_aarch64_darwin_indexed_module(module, optimize=False)
    restored_asm = emit_aarch64_darwin_indexed_module(restored, optimize=False)
    assert restored_asm == original_asm
    assert hashlib.sha256(restored_asm.encode()).digest() == hashlib.sha256(
        original_asm.encode()
    ).digest()


def test_indexed_module_codec_rejects_truncated_raw_arena_payload(
    tmp_path,
    monkeypatch,
):
    module = _direct_module(monkeypatch)
    path = tmp_path / "module.pidx"
    encode_indexed_module_file(str(path), module)
    raw = path.read_bytes()
    path.write_bytes(raw[:-1])

    with pytest.raises(ValueError, match="size is inconsistent"):
        decode_indexed_module_file(str(path))


def test_indexed_module_codec_requires_the_published_preparation_boundary(
    tmp_path,
    monkeypatch,
):
    module = _direct_module(monkeypatch)
    module.functions[0].indexed_kernel = None

    with pytest.raises(ValueError, match="no published indexed kernel"):
        encode_indexed_module_file(str(tmp_path / "module.pidx"), module)


@pytest.mark.parametrize("optimize", [False, True])
@pytest.mark.parametrize("telemetry", [False, True])
def test_indexed_module_fresh_emit_matches_the_text_assembly_oracle(
    tmp_path,
    monkeypatch,
    optimize,
    telemetry,
):
    if telemetry:
        monkeypatch.setenv("PCC_WORKER_RESOURCE_REPORT", str(tmp_path / "rss"))
        monkeypatch.setenv("PCC_WORKER_RESOURCE_TOKEN", "telemetry-identity")
    else:
        monkeypatch.delenv("PCC_WORKER_RESOURCE_REPORT", raising=False)
    module = _direct_module(monkeypatch)
    sidecar = tmp_path / "module.pidx"
    output = tmp_path / "module.pco"
    encode_indexed_module_file(str(sidecar), module)

    oracle_asm = emit_aarch64_darwin_indexed_module(module, optimize=optimize)
    sections, undefined = assemble_file(oracle_asm)
    expected = encode_native_object_from_sections(
        sections,
        undefined=undefined,
    )

    emit_indexed_module_file(str(sidecar), str(output), "PCO", optimize=optimize)

    assert output.read_bytes() == expected
    if telemetry:
        from pcc.frontends.python.worker_resource_plan import read_worker_resource
        import os
        observed = read_worker_resource(str(tmp_path / "rss"), os.getpid(), "telemetry-identity")
        assert observed is not None and observed[0] == "complete"
    assert not (tmp_path / "module.pco.tmp").exists()


def test_indexed_module_fresh_emit_preserves_the_assembly_lane(
    tmp_path,
    monkeypatch,
):
    module = _direct_module(monkeypatch)
    sidecar = tmp_path / "module.pidx"
    output = tmp_path / "module.s"
    encode_indexed_module_file(str(sidecar), module)
    expected = emit_aarch64_darwin_indexed_module(module, optimize=False)

    emit_indexed_module_file(str(sidecar), str(output), "ASM")

    assert output.read_text(encoding="utf-8") == expected


def test_indexed_restore_closes_temporary_value_columns_after_copying(
    tmp_path, monkeypatch,
):
    module = _direct_module(monkeypatch)
    path = tmp_path / "adopt.pidx"
    encode_indexed_module_file(str(path), module)
    read_arena = indexed_codec._read_host_arena
    restored_arenas = []

    def capture(stream, count):
        arena = read_arena(stream, count)
        restored_arenas.append(arena)
        return arena

    monkeypatch.setattr(indexed_codec, "_read_host_arena", capture)
    restored = decode_indexed_module_file(str(path))
    for index, function in enumerate(restored.functions):
        arenas = {name: restored_arenas[index * len(_ARENA_FIELDS) + offset]
                  for offset, (name, _field, _seed) in enumerate(_ARENA_FIELDS)}
        kernel = get_indexed_function_kernel(function)
        original = get_indexed_function_kernel(module.functions[index])
        for name in ("value_scalars", "definition_positions", "used_value_ids"):
            assert getattr(kernel, name) is not arenas[name]
            assert getattr(kernel, name).diagnostic_values() == getattr(original, name).diagnostic_values()
            with pytest.raises(RuntimeError, match="closed"):
                arenas[name].diagnostic_values()
        assert not kernel.definition_blocks
        assert not kernel.value_type_ids
        kernel.close_native_tables()
        for name in ("value_scalars", "definition_positions", "used_value_ids"):
            with pytest.raises(RuntimeError, match="closed"):
                arenas[name].diagnostic_values()
        kernel.close_native_tables()  # Closing again must not free final storage twice.


@pytest.mark.parametrize("lane", [2, 3, 5])
def test_indexed_restore_rejects_prepared_value_columns(tmp_path, monkeypatch, lane):
    import json
    import struct

    path = tmp_path / "prepared.pidx"
    encode_indexed_module_file(str(path), _direct_module(monkeypatch))
    with path.open("rb") as stream:
        stream.readline()
        size = int(stream.readline())
        header = json.loads(stream.read(size))
        start = stream.tell()
    for name, count in header["functions"][0]["arenas"]:
        if name == "value_scalars":
            break
        start += count * 8
    else:
        raise AssertionError("missing value plane")
    raw = bytearray(path.read_bytes())
    struct.pack_into("<q", raw, start + lane * 8, 0)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="value state is already prepared"):
        decode_indexed_module_file(str(path))


def test_indexed_restore_preserves_boolean_lane_normalization(tmp_path, monkeypatch):
    import json
    import struct

    path = tmp_path / "boolean.pidx"
    encode_indexed_module_file(str(path), _direct_module(monkeypatch))
    with path.open("rb") as stream:
        stream.readline()
        size = int(stream.readline())
        header = json.loads(stream.read(size))
        start = stream.tell()
    for name, count in header["functions"][0]["arenas"]:
        if name == "value_scalars":
            break
        start += count * 8
    raw = bytearray(path.read_bytes())
    struct.pack_into("<q", raw, start + 7 * 8, -7)
    path.write_bytes(raw)
    restored = decode_indexed_module_file(str(path))
    assert get_indexed_function_kernel(restored.functions[0]).value_scalars.get_unchecked(7) == 1


def _executable_direct_module(monkeypatch):
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    module = ir.Module(name="restore_exec")
    module.triple = "arm64-apple-darwin23.6.0"
    i64 = ir.IntType(64)
    helper = ir.Function(module, ir.FunctionType(i64, [i64]), name="helper")
    helper.args[0].name = "value"
    entry = helper.append_basic_block("entry")
    yes = helper.append_basic_block("yes")
    no = helper.append_basic_block("no")
    join = helper.append_basic_block("join")
    builder = ir.IRBuilder(entry)
    slot = builder.alloca(i64, name="slot")
    builder.store(helper.args[0], slot)
    loaded = builder.load(slot, name="loaded")
    condition = builder.icmp_signed(">", loaded, ir.Constant(i64, 39), name="condition")
    builder.cbranch(condition, yes, no)
    builder.position_at_end(yes)
    good = builder.add(loaded, ir.Constant(i64, 2), name="good")
    builder.branch(join)
    builder.position_at_end(no)
    bad = builder.sub(loaded, ir.Constant(i64, 2), name="bad")
    builder.branch(join)
    builder.position_at_end(join)
    result = builder.phi(i64, name="result")
    result.add_incoming(good, yes)
    result.add_incoming(bad, no)
    builder.ret(result)
    main = ir.Function(module, ir.FunctionType(i64, []), name="main")
    builder = ir.IRBuilder(main.append_basic_block("entry"))
    builder.ret(builder.call(helper, [ir.Constant(i64, 40)], name="answer"))
    empty = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name="empty")
    ir.IRBuilder(empty.append_basic_block("entry")).ret_void()
    return module.direct_indexed_module()


def test_indexed_restore_handles_a_zero_value_function(tmp_path, monkeypatch):
    path = tmp_path / "empty.pidx"
    module = _executable_direct_module(monkeypatch)
    encode_indexed_module_file(str(path), module)
    restored = decode_indexed_module_file(str(path))
    empty = next(function for function in restored.functions if function.name == "empty")
    kernel = get_indexed_function_kernel(empty)
    assert len(kernel.value_names) == len(kernel.value_scalars) == 0
    assert emit_aarch64_darwin_indexed_module(restored, optimize=False) == emit_aarch64_darwin_indexed_module(module, optimize=False)


@pytest.mark.integration
def test_restored_value_arenas_survive_native_collection_and_roundtrip(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive,
):
    import os
    import subprocess
    import importlib.util
    from pathlib import Path
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import decode_packed_native_object

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    sidecar = tmp_path / "input.pidx"
    encode_indexed_module_file(str(sidecar), _executable_direct_module(monkeypatch))
    expected_pco = tmp_path / "expected.pco"
    emit_indexed_module_file(str(sidecar), str(expected_pco), "PCO", optimize=False)
    # Use the real compiler package closure, as the production worker does.
    # An unrelated top-level script does not admit private backend imports.
    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "pidx_stage1_tool", repo / "scripts/run_pcc_stage1_build.py",
    )
    stage1_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage1_tool)
    ab = stage1_tool._load_ab_tool()
    snapshot = tmp_path / "source"
    stage1_tool._snapshot_sources(repo, stage1_tool.source_manifest(repo, ab), snapshot, ab)
    source = snapshot / "pcc/backend/restore_native.py"
    source.parent.chmod(0o755)
    source.write_text('''import gc
import sys
from pcc.backend.self_backend_indexed_codec import decode_indexed_module_file, encode_indexed_module_file
from pcc.backend.self_backend_kernel import get_indexed_function_kernel

def main():
    module = decode_indexed_module_file(sys.argv[1])
    gc.collect()
    gc.collect()
    gc.collect()
    assert len(module.functions) == 3
    for function in module.functions:
        kernel = get_indexed_function_kernel(function)
        assert len(kernel.value_scalars) == len(kernel.value_names) * 8
        assert len(kernel.definition_positions) == len(kernel.value_names)
        assert not kernel.definition_blocks
        assert not kernel.value_type_ids
    encode_indexed_module_file(sys.argv[2], module)
    gc.collect()
    for function in module.functions:
        kernel = get_indexed_function_kernel(function)
        kernel.close_native_tables()
        kernel.close_native_tables()
    print("restore-native-ok")
main()
''')
    source.chmod(0o444)
    source.parent.chmod(0o555)
    binary = tmp_path / "restore_native"
    monkeypatch.chdir(snapshot)
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        restored = tmp_path / ("restored-" + str(backend) + ".pidx")
        ran = subprocess.run([str(binary), str(sidecar), str(restored)],
                             capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert ran.returncode == 0, (backend, ran.stdout, ran.stderr)
        assert ran.stdout.strip() == "restore-native-ok"
        assert restored.read_bytes() == sidecar.read_bytes(), backend
        pco = tmp_path / ("restored-" + str(backend) + ".pco")
        emit_indexed_module_file(str(restored), str(pco), "PCO", optimize=False)
        assert pco.read_bytes() == expected_pco.read_bytes(), backend
        executable = tmp_path / ("program-" + str(backend))
        executable.write_bytes(link_executable([decode_packed_native_object(pco.read_bytes())]))
        executable.chmod(0o755)
        executed = subprocess.run([str(executable)], capture_output=True, timeout=10)
        assert executed.returncode == 42, (backend, executed.stderr)


@pytest.mark.parametrize("count", [0, 3])
@pytest.mark.parametrize("read_type", [OSError, ValueError, MemoryError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("close_type", [None, RuntimeError, KeyboardInterrupt])
def test_native_arena_read_failure_releases_private_owner(
    monkeypatch, count, read_type, close_type,
):
    events = []
    original = read_type("read failed")
    cause = RuntimeError("original cause")
    context = LookupError("original context")
    original.__cause__ = cause
    original.__context__ = context
    original.__suppress_context__ = True
    cleanup = close_type("cleanup failed") if close_type is not None else None

    class Arena:
        def __init__(self, capacity):
            events.append(("construct", capacity, self))

        def read_raw_fd(self, fd, size):
            events.append(("read", fd, size, self))
            raise original

        def close(self):
            events.append(("close", self))
            if cleanup is not None:
                raise cleanup

    monkeypatch.setattr(indexed_codec, "CompilerIntArena", Arena)
    with pytest.raises(read_type) as escaped:
        indexed_codec._read_native_arena(71, count)
    assert escaped.value is original
    assert original.__cause__ is cause
    assert original.__context__ is context
    assert original.__suppress_context__ is True
    arena = events[0][2]
    assert events == [("construct", count, arena), ("read", 71, count, arena),
                      ("close", arena)]
    frames = []
    traceback = escaped.value.__traceback__
    while traceback is not None:
        frames.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next
    assert frames[-1] == "read_raw_fd"
    assert "close" not in frames


@pytest.mark.parametrize("failure_type", [MemoryError, KeyboardInterrupt])
def test_native_arena_constructor_failure_has_no_returned_owner(monkeypatch, failure_type):
    events = []
    original = failure_type("constructor failed")

    class Arena:
        def __init__(self, capacity):
            events.append(("construct", capacity))
            raise original

        def read_raw_fd(self, fd, count):
            pytest.fail("a failed constructor cannot supply a readable arena")

        def close(self):
            pytest.fail("the helper does not own a constructor that did not return")

    monkeypatch.setattr(indexed_codec, "CompilerIntArena", Arena)
    with pytest.raises(failure_type) as escaped:
        indexed_codec._read_native_arena(71, 3)
    assert escaped.value is original
    assert events == [("construct", 3)]


@pytest.mark.parametrize("count", [0, 3])
def test_native_arena_read_success_transfers_open_owner(monkeypatch, count):
    events = []

    class Arena:
        def __init__(self, capacity):
            events.append(("construct", capacity, self))

        def read_raw_fd(self, fd, size):
            events.append(("read", fd, size, self))

        def close(self):
            pytest.fail("successful read must transfer the still-open arena")

    monkeypatch.setattr(indexed_codec, "CompilerIntArena", Arena)
    arena = indexed_codec._read_native_arena(71, count)
    assert events == [("construct", count, arena), ("read", 71, count, arena)]


def _native_read_ownership_source():
    import ast
    import inspect
    from pathlib import Path
    from pcc.backend import self_backend_value_arena as arena_module

    # Copy the actual class and helper. Instrument only allocation call names
    # inside the class; typed wrappers retain raw-pointer intrinsic provenance.
    arena = Path(arena_module.__file__).read_text().split(
        "class CompilerRecordSpanArena:", 1,
    )[0]
    prefix, body = arena.split("class CompilerIntArena:", 1)
    observed = body
    for name in ("malloc", "realloc", "free"):
        observed = observed.replace(name + "(", "_read_observed_" + name + "(")
    restored = observed
    for name in ("malloc", "realloc", "free"):
        restored = restored.replace("_read_observed_" + name + "(", name + "(")
    assert restored == body
    # Freeze the negative control against the original helper's normalized
    # AST, excluding only its renamed callable and nonsemantic comments.
    legacy = next(node for node in ast.parse(_NATIVE_READ_OWNERSHIP_DRIVER).body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_legacy_read_native_arena")
    legacy.name = "_read_native_arena"
    expected_legacy = ast.parse(
        "def _read_native_arena(fd: int, count: int) -> CompilerIntArena:\n"
        "    arena = CompilerIntArena(count)\n"
        "    arena.read_raw_fd(fd, count)\n"
        "    return arena\n"
    ).body[0]
    assert ast.dump(legacy, include_attributes=False) == ast.dump(
        expected_legacy, include_attributes=False,
    )
    return (prefix + "class CompilerIntArena:" + observed + "\n"
            + inspect.getsource(indexed_codec._read_native_arena)
            + _NATIVE_READ_OWNERSHIP_DRIVER)


_NATIVE_READ_OWNERSHIP_DRIVER = '''
import os
from pcc.unsafe import malloc as _read_real_malloc
from pcc.unsafe import realloc as _read_real_realloc
from pcc.unsafe import free as _read_real_free

# Each case owns at most one raw allocation. No observer container can grow
# with input. An invalid observer never grants authority to recover a pointer.
_read_track = False
_read_observer_invalid = False
_read_address = 0
_read_allocations = 0
_read_releases = 0
_read_inject_observer_failure = False

def _read_invalidate_observer() -> None:
    global _read_observer_invalid, _read_track
    _read_observer_invalid = True
    _read_track = False

def _read_observed_malloc(size: int) -> c_ptr:
    global _read_address, _read_allocations
    pointer: c_ptr = _read_real_malloc(size)
    if _read_track:
        try:
            address = ptr_to_int(pointer)
            if address != 0:
                assert _read_address == 0
                _read_address = address
                _read_allocations += 1
                if _read_inject_observer_failure:
                    raise RuntimeError("injected allocation observer failure")
        except BaseException:
            # Always return a successful real allocation to its arena owner.
            _read_invalidate_observer()
    return pointer

def _read_observed_realloc(pointer: c_ptr, size: int) -> c_ptr:
    # Constructor capacity equals the requested count, so growth is forbidden
    # for this witness. Invalidate BEFORE realloc can make any address stale.
    if _read_track:
        _read_invalidate_observer()
    return _read_real_realloc(pointer, size)

def _read_observed_free(pointer: c_ptr) -> None:
    global _read_address, _read_releases
    if _read_track:
        try:
            address = ptr_to_int(pointer)
            assert address != 0 and address == _read_address
            # Retire recovery authority before the actual release.
            _read_address = 0
            _read_releases += 1
        except BaseException:
            _read_invalidate_observer()
    _read_real_free(pointer)

def _read_require_valid_observer() -> None:
    assert not _read_observer_invalid, "allocation observer invalid"

def _read_recover_private() -> bool:
    global _read_address
    if _read_observer_invalid:
        return False
    address = _read_address
    _read_address = 0
    if address != 0:
        _read_real_free(int_to_ptr(address))
    return True

def _legacy_read_native_arena(fd: int, count: int) -> CompilerIntArena:
    # Frozen pre-fix negative control, differing only in callable name.
    arena = CompilerIntArena(count)
    arena.read_raw_fd(fd, count)
    return arena

def _read_case(case: str, count: int) -> None:
    global _read_track, _read_allocations, _read_releases
    _read_require_valid_observer()
    assert _read_address == 0
    _read_allocations = 0
    _read_releases = 0
    fd = -1
    if case != "invalid-fd":
        path = os.environ["PCC_READ_COMPLETE"]
        if case == "partial" or case == "legacy":
            path = os.environ["PCC_READ_PARTIAL"]
        elif case == "empty":
            path = os.environ["PCC_READ_EMPTY"]
        fd = os.open(path, os.O_RDONLY)
    returned = None
    failed = False
    _read_track = True
    try:
        try:
            if case == "legacy":
                returned = _legacy_read_native_arena(fd, count)
            else:
                returned = _read_native_arena(fd, count)
        except OSError as error:
            assert str(error) == "compiler arena raw read was truncated"
            failed = True
        _read_require_valid_observer()
        assert _read_allocations == 1
        if case == "success" or case == "zero":
            assert not failed
            assert returned is not None
            assert returned.uses_native_storage
            assert not returned._closed
            assert returned.native_address() == _read_address
            assert len(returned) == count
            assert _read_releases == 0
            if count != 0:
                assert returned.get(0) == 7
                assert returned.get(1) == -11
            returned.close()
            _read_require_valid_observer()
            assert _read_releases == 1 and _read_address == 0
            returned.close()
            assert _read_releases == 1
        else:
            assert failed and returned is None
            if case == "legacy":
                assert _read_releases == 0 and _read_address != 0
                # The valid observer detects the original real allocation leak.
                assert _read_recover_private()
            else:
                assert _read_releases == 1 and _read_address == 0
        print(case + "|ok")
    finally:
        _read_track = False
        try:
            if returned is not None:
                if _read_address != 0:
                    # An earlier assertion may have interrupted success before
                    # owner close. Untracked close would leave a stale address:
                    # revoke recovery authority BEFORE releasing through owner.
                    _read_invalidate_observer()
                returned.close()
            _read_recover_private()
        finally:
            if fd != -1:
                os.close(fd)

def _read_invalid_observer_control() -> None:
    global _read_track, _read_inject_observer_failure
    _read_require_valid_observer()
    assert _read_address == 0
    _read_inject_observer_failure = True
    _read_track = True
    fd = os.open(os.environ["PCC_READ_COMPLETE"], os.O_RDONLY)
    returned = None
    try:
        returned = _read_native_arena(fd, 2)
        assert returned.uses_native_storage and len(returned) == 2
        assert _read_observer_invalid
        rejected = False
        try:
            _read_require_valid_observer()
        except AssertionError:
            rejected = True
        assert rejected
        # Close through the real owner; the saved observer address is now
        # stale. Recovery must still refuse it, without touching freed memory.
        returned.close()
        assert not _read_recover_private()
        print("invalid-observer|rejected")
    finally:
        _read_track = False
        try:
            if returned is not None:
                if _read_address != 0:
                    _read_invalidate_observer()
                returned.close()
        finally:
            os.close(fd)

def main():
    _read_case("legacy", 2)
    _read_case("empty", 1)
    _read_case("partial", 2)
    _read_case("invalid-fd", 1)
    _read_case("success", 2)
    _read_case("zero", 0)
    # Invalid observation is permanent, never reset into trusted evidence.
    _read_invalid_observer_control()

main()
'''


@pytest.mark.integration
def test_native_read_private_ownership_under_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive, request,
):
    import inspect
    import json
    import os
    from pathlib import Path
    import struct
    import subprocess
    import sys
    from pcc.backend import self_backend_value_arena as arena_module

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "native_read_ownership.py"
    binary = tmp_path / "native_read_ownership"
    source.write_text(_native_read_ownership_source())
    complete = tmp_path / "complete.bin"
    partial = tmp_path / "partial.bin"
    empty = tmp_path / "empty.bin"
    payload = struct.pack("<qq", 7, -11)
    complete.write_bytes(payload)
    partial.write_bytes(payload[:9])
    empty.write_bytes(b"")

    def identity(path):
        if path is None:
            return None
        return {"path": os.path.abspath(os.fspath(path)),
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    captured = inspect.getclosurevars(python_program_compiler).nonlocals.get("compiler")
    if not isinstance(captured, (str, os.PathLike)):
        captured = None
    route_path = os.environ.get("PCC_TEST_COMPILER_LOG")
    route_offset = (Path(route_path).stat().st_size
                    if route_path and Path(route_path).is_file() else 0)
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    receipt = {
        "nominal_case": request.node.callspec.params["python_program_compiler"],
        "ownership_boundary": "nominal case is not compiler ownership evidence; inspect callable, captured compiler and route log",
        "compiler_callable": python_program_compiler.__module__ + "." + python_program_compiler.__qualname__,
        "compiler_callable_source": identity(inspect.getsourcefile(python_program_compiler)),
        "captured_compiler_before": identity(captured),
        "host_process": identity(sys.executable),
        "source": identity(source),
        "runtime_archive": identity(pcc_runtime_archive),
        "codec": identity(indexed_codec.__file__),
        "arena": identity(arena_module.__file__),
        "helper_sha256": hashlib.sha256(inspect.getsource(
            indexed_codec._read_native_arena,
        ).encode()).hexdigest(),
        "inputs": [identity(path) for path in (complete, partial, empty)],
        "instrumentation": "exact helper and arena source; bounded single-allocation observers call real unsafe intrinsics",
        "scope": "extracted helper native execution, not full decoder, self-host fixed point, or RSS/performance evidence",
        "returned_args": None,
        "route_records": [],
        "gc_runs": [],
    }
    receipt_path = tmp_path / "native_read_ownership.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        compiled = python_program_compiler(
            str(source), str(binary), backend="self",
            runtime_archive=str(pcc_runtime_archive),
        )
        args = getattr(compiled, "args", None)
        if args is not None:
            receipt["returned_args"] = ([os.fsdecode(value) for value in args]
                                        if isinstance(args, (tuple, list)) else os.fsdecode(args))
    finally:
        receipt["captured_compiler_after"] = identity(captured)
        if route_path and Path(route_path).is_file():
            with Path(route_path).open("rb") as stream:
                stream.seek(route_offset)
                for line in stream:
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as error:
                        receipt["route_records"].append({"unparsed": str(error)})
                        continue
                    if row.get("test") == current_test and row.get("kind") == "compile_python":
                        receipt["route_records"].append(row)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["binary"] = identity(binary)
    expected = ("legacy|ok\nempty|ok\npartial|ok\ninvalid-fd|ok\n"
                "success|ok\nzero|ok\ninvalid-observer|rejected\n")
    for collector in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector),
                     PCC_READ_COMPLETE=str(complete), PCC_READ_PARTIAL=str(partial),
                     PCC_READ_EMPTY=str(empty)),
        )
        (tmp_path / f"read-gc{collector}.stdout").write_text(result.stdout)
        (tmp_path / f"read-gc{collector}.stderr").write_text(result.stderr)
        receipt["gc_runs"].append({"collector": collector, "returncode": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr})
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == ""
        assert result.stdout == expected


# This inventory is checked against both constructors and the wire mapping.
# It is test-only; the production failure path never constructs an owner list.
_PRIVATE_SEED_FIELDS = (
    "records", "args", "block_name_index", "value_name_index",
    "block_facts", "instruction_facts", "instruction_kind_ids",
    "instruction_metadata", "instruction_record_dest_ids",
    "instruction_record_scalars", "gep_index_scalars", "gep_scalars",
    "instruction_overflow_use_ids", "terminator_case_scalars",
    "terminator_scalars", "block_phi_facts", "phi_incoming_scalars",
    "phi_scalars", "error_edge_scalars", "error_edge_spans",
    "error_landing_scalars",
)


def _private_seed_wire():
    return {
        "name": "empty", "block_names": ["entry"], "value_names": ["%x"],
        "instruction_arithmetic_flags": [], "cold_instruction_data": [],
        "call_texts": [], "types": [], "args": [],
        "ret_type": ["void", 0, None, 0, None, "", []],
        "arenas": [[name, 4 if name == "block_facts" else 0]
                   for name, _, _ in _ARENA_FIELDS],
    }


def _capture_private_seed(monkeypatch):
    real = indexed_codec.IndexedFunctionSeed
    captured = []

    def construct(*args):
        seed = real(*args)
        captured.append((seed, {name: getattr(seed, name)
                                for name in _PRIVATE_SEED_FIELDS}))
        return seed

    monkeypatch.setattr(indexed_codec, "IndexedFunctionSeed", construct)
    return captured


@pytest.mark.parametrize("field,bad", [
    ("block_names", [7]), ("block_names", ["entry", 7]),
    ("value_names", [7]), ("value_names", ["%x", 7]),
    ("first_duplicate_definition_value_id", "bad"),
    ("instruction_use_total", "bad"),
    ("instruction_arithmetic_flags", None),
    ("instruction_arithmetic_flags", [[0]]),
    ("instruction_arithmetic_flags", [["bad", []]]),
    ("instruction_arithmetic_flags", [[0, [7]]]),
    ("cold_instruction_data", None),
    ("cold_instruction_data", [{"q": 7}]),
    ("cold_instruction_data", [{"d": [[1]]}]),
    ("call_texts", [7]), ("types", [None]),
    ("types", [["int", "bad", None, 0, None, "", []]]),
    ("args", None), ("args", [["%x"]]), ("args", [["%x", None]]),
    ("args", [[7, ["int", 32, None, 0, None, "", []]]]),
    ("ret_type", None), ("arenas", []),
    ("is_global", 7), ("definition_linkage", "bad"),
    ("definition_linkage", "external"), ("name", 7), ("is_vararg", 7),
])
def test_private_seed_malformed_wire_closes_actual_owners(monkeypatch, field, bad):
    captured = _capture_private_seed(monkeypatch)
    row = _private_seed_wire()
    row[field] = bad
    with pytest.raises(ValueError):
        indexed_codec._seed_from_wire(row)
    assert len(captured) == 1
    seed, originals = captured[0]
    current = [getattr(seed, name) for name in _PRIVATE_SEED_FIELDS]
    assert len({id(arena) for arena in current}) == 21
    assert all(arena._closed for arena in current)
    assert all(arena._closed for arena in originals.values())
    if field not in ("block_names", "value_names"):
        assert seed.value_name_index is not originals["value_name_index"]


@pytest.mark.parametrize("index", [0, len(_ARENA_FIELDS) // 2, len(_ARENA_FIELDS) - 1])
@pytest.mark.parametrize("bad_kind", ["name", "count", "negative", "shape"])
def test_private_seed_inventory_failure_closes_actual_owners(monkeypatch, index, bad_kind):
    captured = _capture_private_seed(monkeypatch)
    row = _private_seed_wire()
    if bad_kind == "name":
        row["arenas"][index][0] = "wrong"
    elif bad_kind == "count":
        row["arenas"][index][1] = "wrong"
    elif bad_kind == "negative":
        row["arenas"][index][1] = -1
    else:
        row["arenas"][index] = []
    with pytest.raises(ValueError, match="arena order"):
        indexed_codec._seed_from_wire(row)
    seed, originals = captured[0]
    assert all(getattr(seed, name)._closed for name in _PRIVATE_SEED_FIELDS)
    assert all(arena._closed for arena in originals.values())


@pytest.mark.parametrize("failure_type", [ValueError, MemoryError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("close_index", [0, 10, 20])
@pytest.mark.parametrize("point", ["wire", "parsed", "inventory_tuple", "return_tuple"])
def test_private_seed_failure_identity_and_independent_close_attempts(
    monkeypatch, failure_type, close_index, point,
):
    # Host fault injection covers close failures and every transaction exit.
    # Native execution coverage below uses ordinary malformed wire validation.
    import builtins
    real_seed = indexed_codec.IndexedFunctionSeed
    arena_type = indexed_codec.CompilerIntArena
    real_close = arena_type.close
    attempts = []
    captured = []
    original = failure_type("original seed failure")
    cause, context = RuntimeError("cause"), LookupError("context")
    original.__cause__, original.__context__ = cause, context
    original.__suppress_context__ = True

    def construct(*args):
        seed = real_seed(*args)
        captured.append(seed)
        return seed

    def close(arena):
        seed = captured[0]
        owners = [getattr(seed, name) for name in _PRIVATE_SEED_FIELDS]
        # Ignore original name-index replacement closes before the failure.
        if not in_failure[0]:
            return real_close(arena)
        index = next(i for i, owner in enumerate(owners) if owner is arena)
        attempts.append(index)
        if index == close_index:
            raise KeyboardInterrupt("cleanup failure")
        real_close(arena)

    in_failure = [False]

    def fail(*args, **kwargs):
        in_failure[0] = True
        raise original

    monkeypatch.setattr(indexed_codec, "IndexedFunctionSeed", construct)
    monkeypatch.setattr(arena_type, "close", close)
    row = _private_seed_wire()
    if point == "wire":
        monkeypatch.setattr(indexed_codec, "_wire_int", fail)
    elif point == "parsed":
        monkeypatch.setattr(indexed_codec, "ParsedFunction", fail)
    elif point == "inventory_tuple":
        def tuple_at_inventory(items):
            if isinstance(items, list) and items and isinstance(items[0], tuple):
                return fail()
            return builtins.tuple(items)

        monkeypatch.setattr(indexed_codec, "tuple", tuple_at_inventory, raising=False)
    else:
        # BUILD_TUPLE cannot be independently monkeypatched in CPython. A
        # trace callback fails at the return line, inside the same try region.
        import sys
        code = indexed_codec._seed_from_wire.__code__
        import inspect
        lines, start = inspect.getsourcelines(indexed_codec._seed_from_wire)
        line = start + next(i for i, text in enumerate(lines)
                            if "return function, tuple(arena_inventory)" in text)

        def trace(frame, event, arg):
            if frame.f_code is code and event == "line" and frame.f_lineno == line:
                fail()
            return trace

        old_trace = sys.gettrace()
        sys.settrace(trace)
    try:
        with pytest.raises(failure_type) as escaped:
            indexed_codec._seed_from_wire(row)
        assert escaped.value is original
        assert original.__cause__ is cause and original.__context__ is context
        assert original.__suppress_context__
        assert attempts == list(range(21))
        owners = [getattr(captured[0], name) for name in _PRIVATE_SEED_FIELDS]
        assert [arena._closed for arena in owners] == [i != close_index for i in range(21)]
    finally:
        if point == "return_tuple":
            sys.settrace(old_trace)
        # Failure injection left exactly one real host owner open.
        for seed in captured:
            for name in _PRIVATE_SEED_FIELDS:
                real_close(getattr(seed, name))


@pytest.mark.parametrize("failure_type", [MemoryError, KeyboardInterrupt])
def test_private_seed_constructor_no_return_has_no_cleanup(monkeypatch, failure_type):
    original = failure_type("constructor did not return")

    def construct(*args):
        raise original

    def forbidden(*args):
        pytest.fail("no seed ownership was acquired")

    monkeypatch.setattr(indexed_codec, "IndexedFunctionSeed", construct)
    monkeypatch.setattr(indexed_codec, "_close_private_seed", forbidden)
    with pytest.raises(failure_type) as escaped:
        indexed_codec._seed_from_wire(_private_seed_wire())
    assert escaped.value is original


def test_private_seed_success_keeps_current_owners_open(monkeypatch, tmp_path):
    # Real decoder payload adoption and byte roundtrip remain separate from
    # the private header transaction's successful ownership transfer.
    module = _executable_direct_module(monkeypatch)
    source, restored_path = tmp_path / "before.pidx", tmp_path / "after.pidx"
    encode_indexed_module_file(str(source), module)
    restored = decode_indexed_module_file(str(source))
    encode_indexed_module_file(str(restored_path), restored)
    assert source.read_bytes() == restored_path.read_bytes()
    captured = _capture_private_seed(monkeypatch)
    function, inventory = indexed_codec._seed_from_wire(_private_seed_wire())
    seed, originals = captured[0]
    assert function.indexed_seed is seed
    assert inventory == tuple((name, 4 if name == "block_facts" else 0)
                              for name, _, _ in _ARENA_FIELDS)
    assert all(not getattr(seed, name)._closed for name in _PRIVATE_SEED_FIELDS)
    assert originals["value_name_index"]._closed
    assert seed.value_name_index is not originals["value_name_index"]
    indexed_codec._close_private_seed(seed)
    for owner_module in (module, restored):
        for func in owner_module.functions:
            get_indexed_function_kernel(func).close_native_tables()


def test_private_seed_cleanup_matches_actual_constructor_ownership():
    import ast
    import inspect
    import textwrap
    from pcc.backend import self_backend_kernel as kernel_module
    from pcc.backend import self_backend_ir as ir_module

    def constructor_owners(cls):
        tree = ast.parse(textwrap.dedent(inspect.getsource(cls.__init__)))
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if value.func.id == "CompilerIntArena":
                for target in targets:
                    assert isinstance(target, ast.Attribute)
                    names.add(target.attr)
            elif value.func.id == "_new_text_id_index":
                assert len(targets) == 1 and isinstance(targets[0], ast.Tuple)
                names.add(targets[0].elts[0].attr)
        return names

    actual = constructor_owners(kernel_module.IndexedFunctionSeed)
    actual |= constructor_owners(ir_module.IndexedCallPlane)
    assert actual == set(_PRIVATE_SEED_FIELDS)
    assert actual == {field for _, _, field in _ARENA_FIELDS if field} | {
        "block_name_index", "value_name_index",
    }
    helper = ast.parse(inspect.getsource(indexed_codec._close_private_seed)).body[0]
    attempts = helper.body
    assert len(attempts) == 21
    fields = []
    for attempt in attempts:
        assert isinstance(attempt, ast.Try)
        assert len(attempt.body) == len(attempt.handlers) == 1
        assert not attempt.orelse and not attempt.finalbody
        assert attempt.handlers[0].type.id == "BaseException"
        assert len(attempt.handlers[0].body) == 1
        assert isinstance(attempt.handlers[0].body[0], ast.Pass)
        call = attempt.body[0].value
        assert call.func.attr == "close" and not call.args and not call.keywords
        assert call.func.value.value.id == "seed"
        fields.append(call.func.value.attr)
    assert tuple(fields) == _PRIVATE_SEED_FIELDS
    transaction = ast.parse(inspect.getsource(indexed_codec._seed_from_wire)).body[0]
    assert transaction.body[-2].value.func.id == "IndexedFunctionSeed"
    guard = transaction.body[-1]
    assert isinstance(guard, ast.Try) and isinstance(guard.body[-1], ast.Return)
    assert guard.handlers[0].type.id == "BaseException"
    assert isinstance(guard.handlers[0].body[-1], ast.Raise)
    assert guard.handlers[0].body[-1].exc is None
    # The transaction cannot replace the nineteen payload owners or adopt a
    # kernel. Only the two name indexes may be replaced by seed methods.
    payload = actual - {"block_name_index", "value_name_index"}
    for node in ast.walk(guard):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            assert node.attr not in payload
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "get_indexed_function_kernel"


@pytest.mark.integration
def test_private_seed_rollback_native_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive, request,
):
    """Real package seed/wire boundary; no full-decoder or native fault-injection claim."""
    import ast
    import importlib.util
    import inspect
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "seed_rollback_stage1_tool", repo / "scripts/run_pcc_stage1_build.py",
    )
    stage1_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage1_tool)
    ab = stage1_tool._load_ab_tool()
    snapshot = tmp_path / "source"
    stage1_tool._snapshot_sources(repo, stage1_tool.source_manifest(repo, ab), snapshot, ab)
    codec_path = snapshot / "pcc/backend/self_backend_indexed_codec.py"
    original = codec_path.read_text()
    fields = [row[2] for row in indexed_codec._ARENA_FIELDS if row[2]]
    fields += ["block_name_index", "value_name_index"]
    assert len(fields) == len(set(fields)) == 21
    # Generated assertions use direct typed fields, not dynamic native getattr.
    native_assertions = "".join(
        "    assert seed." + field + ".uses_native_storage\n"
        "    assert not seed." + field + "._closed\n"
        for field in fields
    )
    closed_assertions = "".join(
        "    assert seed." + field + "._closed\n"
        "    assert seed." + field + ".native_address() == 0\n"
        for field in fields
    )
    observer = (
        "\ndef _seed_rollback_require_native(seed: IndexedFunctionSeed) -> None:\n"
        + native_assertions
        + "\ndef _seed_rollback_require_closed(seed: IndexedFunctionSeed) -> None:\n"
        + closed_assertions
        + '    print("closed-21")\n'
    )
    tree = ast.parse(original)
    close_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_close_private_seed")
    # Insert around the exact helper body, preserving its complete implementation.
    lines = original.splitlines(keepends=True)
    lines.insert(close_node.end_lineno, "    _seed_rollback_require_closed(seed)\n")
    first_body = close_node.body[0].lineno - 1
    if isinstance(close_node.body[0], ast.Expr) and isinstance(
        close_node.body[0].value, ast.Constant,
    ) and isinstance(close_node.body[0].value.value, str):
        first_body = close_node.body[0].end_lineno
    lines.insert(first_body, "    _seed_rollback_require_native(seed)\n")
    observed = "".join(lines)
    constructor = "    seed = IndexedFunctionSeed(len(block_names), len(value_names))\n"
    assert observed.count(constructor) == 1
    observed = observed.replace(constructor, constructor +
        "    _seed_rollback_require_native(seed)\n"
        "    _seed_rollback_initial_index = seed.value_name_index\n", 1)
    close_call = "        _close_private_seed(seed)\n"
    assert observed.count(close_call) == 1
    check_rebuilt = (
        "        if seed.value_name_index_active and seed.value_names:\n"
        "            assert seed.value_name_index is not _seed_rollback_initial_index\n"
        "            assert _seed_rollback_initial_index._closed\n"
    )
    observed = observed.replace(close_call, check_rebuilt + close_call, 1)
    # Reversibility is mandatory: no changes to validation, close or guard bodies.
    restored = observed.replace(check_rebuilt, "", 1).replace(
        "    _seed_rollback_initial_index = seed.value_name_index\n", "", 1,
    ).replace("    _seed_rollback_require_native(seed)\n", "").replace(
        "    _seed_rollback_require_closed(seed)\n", "",
    )
    assert restored == original
    codec_path.chmod(0o644)
    codec_path.write_text(observed + observer)
    codec_path.chmod(0o444)
    inventory = repr([[row[0], 4 if row[0] == "block_facts" else 0]
                      for row in indexed_codec._ARENA_FIELDS])
    driver = '''import gc
from pcc.backend.self_backend_indexed_codec import _seed_from_wire, _close_private_seed
from pcc.backend.self_backend_kernel import IndexedFunctionSeed

def row():
    return {"block_names": ["entry"], "value_names": ["%v"],
            "instruction_arithmetic_flags": [], "cold_instruction_data": [],
            "call_texts": [], "types": [], "args": [],
            "ret_type": ["void", 0, None, 0, None, "", []],
            "arenas": INVENTORY}

def main():
    for case in range(12):
        value = row()
        if case == 0:
            value["block_names"] = [7]
        elif case == 1:
            value["block_names"] = ["entry", 7]
        elif case == 2:
            value["value_names"] = ["%v", 7]
        elif case == 3:
            value["instruction_arithmetic_flags"] = [[0, [7]]]
        elif case == 4:
            value["cold_instruction_data"] = [{"q": 7}]
        elif case == 5:
            value["types"] = [None]
        elif case == 6:
            value["args"] = [["x", None]]
        elif case == 7:
            value["ret_type"] = None
        elif case == 8:
            value["arenas"] = []
        elif case == 9:
            value["definition_linkage"] = "bad"
        elif case == 10:
            value["name"] = 7
        else:
            value["is_vararg"] = 7
        failed = False
        try:
            _seed_from_wire(value)
        except ValueError:
            failed = True
        assert failed
        gc.collect()
        print("malformed|" + str(case))
    function, inventory = _seed_from_wire(row())
    seed_value = function.indexed_seed
    assert isinstance(seed_value, IndexedFunctionSeed)
    seed: IndexedFunctionSeed = seed_value
    assert len(inventory) == 22
    assert not seed.records._closed
    assert seed.records.uses_native_storage
    gc.collect()
    _close_private_seed(seed)
    print("success-transfer|ok")
main()
'''.replace("INVENTORY", inventory)
    source = snapshot / "pcc/backend/seed_rollback_native.py"
    source.parent.chmod(0o755)
    source.write_text(driver)
    source.chmod(0o444)
    source.parent.chmod(0o555)
    binary = tmp_path / "seed_rollback_native"

    def identity(path):
        if path is None:
            return None
        return {"path": os.path.abspath(os.fspath(path)),
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    captured = inspect.getclosurevars(python_program_compiler).nonlocals.get("compiler")
    if not isinstance(captured, (str, os.PathLike)):
        captured = None
    route_path = os.environ.get("PCC_TEST_COMPILER_LOG")
    route_offset = (Path(route_path).stat().st_size
                    if route_path and Path(route_path).is_file() else 0)
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    receipt = {
        "nominal_case": request.node.callspec.params["python_program_compiler"],
        "ownership_boundary": "nominal case is not compiler ownership evidence; inspect callable, captured compiler and route log",
        "compiler_callable": python_program_compiler.__module__ + "." + python_program_compiler.__qualname__,
        "compiler_callable_source": identity(inspect.getsourcefile(python_program_compiler)),
        "captured_compiler_before": identity(captured),
        "host_process": identity(sys.executable),
        "source": identity(source),
        "runtime_archive": identity(pcc_runtime_archive),
        "codec_original": identity(indexed_codec.__file__),
        "codec_instrumented": identity(codec_path),
        "kernel": identity(snapshot / "pcc/backend/self_backend_kernel.py"),
        "arena": identity(snapshot / "pcc/backend/self_backend_value_arena.py"),
        "instrumentation": "reversible native/open and closed assertions around exact package constructor/cleanup; original-index local verifies real value-index replacement; no raw pointer recovery",
        "scope": "full private seed/wire boundary, not full decoder ownership, native close-failure injection, fixed point, RSS or performance",
        "returned_args": None, "route_records": [], "gc_runs": [],
    }
    receipt_path = tmp_path / "seed_rollback_native.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.chdir(snapshot)
    try:
        compiled = python_program_compiler(
            str(source), str(binary), backend="self",
            runtime_archive=str(pcc_runtime_archive),
        )
        args = getattr(compiled, "args", None)
        if args is not None:
            receipt["returned_args"] = ([os.fsdecode(value) for value in args]
                                        if isinstance(args, (tuple, list)) else os.fsdecode(args))
    finally:
        receipt["captured_compiler_after"] = identity(captured)
        if route_path and Path(route_path).is_file():
            with Path(route_path).open("rb") as stream:
                stream.seek(route_offset)
                for line in stream:
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as error:
                        receipt["route_records"].append({"unparsed": str(error)})
                        continue
                    if record.get("test") == current_test and record.get("kind") == "compile_python":
                        receipt["route_records"].append(record)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["binary"] = identity(binary)
    expected = "".join("closed-21\nmalformed|" + str(case) + "\n" for case in range(12))
    expected += "closed-21\nsuccess-transfer|ok\n"
    for collector in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector)),
        )
        (tmp_path / ("seed-gc" + str(collector) + ".stdout")).write_text(result.stdout)
        (tmp_path / ("seed-gc" + str(collector) + ".stderr")).write_text(result.stderr)
        receipt["gc_runs"].append({"collector": collector, "returncode": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr})
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == ""
        assert result.stdout == expected


_PRIVATE_VALUE_COLUMNS = ("value_scalars", "definition_positions", "used_value_ids")


def _private_value_sidecar(tmp_path, monkeypatch, case="valid"):
    import json
    import struct

    path = tmp_path / (case + ".pidx")
    encode_indexed_module_file(str(path), _executable_direct_module(monkeypatch))
    with path.open("rb") as stream:
        magic = stream.readline()
        header_size = int(stream.readline())
        header = json.loads(stream.read(header_size))
        payload = bytearray(stream.read())
    offset = 0
    for row in header["functions"][0]["arenas"]:
        name, count = row
        if case == "short-" + name:
            assert count > 0
            del payload[offset:offset + 8]
            row[1] -= 1
            header["total_scalars"] -= 1
            break
        if name == "value_scalars" and case.startswith("prepared-"):
            struct.pack_into("<q", payload, offset + int(case[-1]) * 8, 0)
            break
        offset += count * 8
    raw = json.dumps(header, sort_keys=True).encode()
    path.write_bytes(magic + str(len(raw)).encode() + b"\n" + raw + payload)
    return path


def _observe_private_value_owners(monkeypatch, failure=None, read_index=None,
                                  close_index=None, kernel_failure=False, cleanup_failure=None):
    real_read = indexed_codec._read_host_arena
    real_seed = indexed_codec._seed_from_wire
    real_close = indexed_codec.CompilerIntArena.close
    captured, reads, attempts, functions = [], [], [], []

    def seed(value):
        result = real_seed(value)
        functions.append(result[0])
        return result

    def read(stream, count):
        index = len(reads)
        if index == read_index:
            raise failure
        arena = real_read(stream, count)
        name = _ARENA_FIELDS[index % len(_ARENA_FIELDS)][0]
        reads.append((name, arena))
        if name in _PRIVATE_VALUE_COLUMNS:
            captured.append(arena)
        return arena

    def close(arena):
        index = next((i for i, owner in enumerate(captured) if owner is arena), None)
        if index is not None:
            attempts.append(index)
            if index == close_index:
                raise cleanup_failure if cleanup_failure is not None else failure
        real_close(arena)

    def kernel(function):
        raise failure

    monkeypatch.setattr(indexed_codec, "_seed_from_wire", seed)
    monkeypatch.setattr(indexed_codec, "_read_host_arena", read)
    monkeypatch.setattr(indexed_codec.CompilerIntArena, "close", close)
    if kernel_failure:
        monkeypatch.setattr(indexed_codec, "get_indexed_function_kernel", kernel)

    def finish():
        # Fault injection may deliberately leave one owner open. Restore the
        # real operation before test teardown, outside all attempt assertions.
        monkeypatch.setattr(indexed_codec.CompilerIntArena, "close", real_close)
        for _, arena in reads:
            real_close(arena)
        for function in functions:
            if function.indexed_seed is not None:
                indexed_codec._close_private_seed(function.indexed_seed)
            if function.indexed_kernel is not None:
                function.indexed_kernel.close_native_tables()

    return captured, reads, attempts, functions, finish


def _assert_private_value_boundary(reads, attempts, captured, functions):
    assert attempts == list(range(len(captured)))
    # Only the three copied columns are owned by the new rollback. The raw
    # payloads already transferred to the seed/kernel must remain untouched.
    for offset, (_, arena) in enumerate(reads):
        if _ARENA_FIELDS[offset % len(_ARENA_FIELDS)][2]:
            assert not arena._closed
    for function in functions:
        owner = function.indexed_kernel or function.indexed_seed
        assert not owner.block_name_index._closed
        assert not owner.value_name_index._closed


@pytest.mark.parametrize("case", ["short-value_scalars", "short-definition_positions",
                                  "prepared-2", "prepared-3", "prepared-5"])
@pytest.mark.parametrize("close_index", [None, 0, 1, 2])
def test_private_value_columns_released_after_malformed_wire(
    tmp_path, monkeypatch, case, close_index,
):
    path = _private_value_sidecar(tmp_path, monkeypatch, case)
    cleanup = KeyboardInterrupt("cleanup must not replace validation")
    captured, reads, attempts, functions, finish = _observe_private_value_owners(
        monkeypatch, failure=cleanup, close_index=close_index,
    )
    try:
        message = "already prepared" if case.startswith("prepared") else "columns are inconsistent"
        with pytest.raises(ValueError, match=message):
            decode_indexed_module_file(str(path))
        assert len(captured) == 3
        _assert_private_value_boundary(reads, attempts, captured, functions)
        assert [owner._closed for owner in captured] == [i != close_index for i in range(3)]
    finally:
        finish()


@pytest.mark.parametrize("failure_type", [MemoryError, OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("point", ["read-0", "read-1", "read-2", "kernel"])
@pytest.mark.parametrize("close_index", [None, 0, 1, 2])
def test_private_value_columns_preserve_read_and_kernel_failures(
    tmp_path, monkeypatch, failure_type, point, close_index,
):
    path = _private_value_sidecar(tmp_path, monkeypatch)
    original = failure_type("original private column failure")
    cause, context = RuntimeError("cause"), LookupError("context")
    original.__cause__, original.__context__ = cause, context
    original.__suppress_context__ = True
    read_offset = len(_ARENA_FIELDS) - 3
    read_index = read_offset + int(point[-1]) if point.startswith("read-") else None
    captured, reads, attempts, functions, finish = _observe_private_value_owners(
        monkeypatch, failure=original, read_index=read_index, kernel_failure=point == "kernel",
        close_index=close_index, cleanup_failure=KeyboardInterrupt("secondary cleanup failure"),
    )
    try:
        with pytest.raises(failure_type) as escaped:
            decode_indexed_module_file(str(path))
        assert escaped.value is original
        assert original.__cause__ is cause and original.__context__ is context
        assert original.__suppress_context__
        expected = 3 if point == "kernel" else int(point[-1])
        assert len(captured) == expected
        _assert_private_value_boundary(reads, attempts, captured, functions)
        assert [owner._closed for owner in captured] == [i != close_index for i in range(expected)]
        origin = escaped.value.__traceback__
        while origin.tb_next is not None:
            origin = origin.tb_next
        assert origin.tb_frame.f_code.co_name == ("kernel" if point == "kernel" else "read")
    finally:
        finish()


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("close_index", [0, 1, 2])
def test_private_value_column_close_failure_is_not_retried(
    tmp_path, monkeypatch, failure_type, close_index,
):
    path = _private_value_sidecar(tmp_path, monkeypatch)
    original = failure_type("first close failed")
    captured, reads, attempts, functions, finish = _observe_private_value_owners(
        monkeypatch, failure=original, close_index=close_index,
    )
    try:
        with pytest.raises(failure_type) as escaped:
            decode_indexed_module_file(str(path))
        assert escaped.value is original
        assert len(captured) == 3
        _assert_private_value_boundary(reads, attempts, captured, functions)
        assert [owner._closed for owner in captured] == [i != close_index for i in range(3)]
        assert functions[0].indexed_kernel is not None
        for name in _PRIVATE_VALUE_COLUMNS:
            assert not getattr(functions[0].indexed_kernel, name)._closed
    finally:
        finish()


def test_private_value_columns_success_keeps_kernel_owners_open(tmp_path, monkeypatch):
    path = _private_value_sidecar(tmp_path, monkeypatch)
    captured, reads, attempts, functions, finish = _observe_private_value_owners(monkeypatch)
    try:
        restored = decode_indexed_module_file(str(path))
        assert len(captured) == len(restored.functions) * 3
        _assert_private_value_boundary(reads, attempts, captured, functions)
        assert all(owner._closed for owner in captured)
        for index, function in enumerate(restored.functions):
            for offset, name in enumerate(_PRIVATE_VALUE_COLUMNS):
                owner = getattr(function.indexed_kernel, name)
                assert owner is not captured[index * 3 + offset]
                assert not owner._closed
    finally:
        finish()


def test_private_value_column_guard_matches_wire_ownership():
    import ast
    import inspect

    assert [name for name, _, field in _ARENA_FIELDS if not field] == list(_PRIVATE_VALUE_COLUMNS)
    assert all(field for _, _, field in _ARENA_FIELDS[:-3])
    helper = ast.parse(inspect.getsource(indexed_codec._close_private_value_columns)).body[0]
    assert [arg.arg for arg in helper.args.args] == list(_PRIVATE_VALUE_COLUMNS)
    assert len(helper.body) == 3
    for name, guard in zip(_PRIVATE_VALUE_COLUMNS, helper.body):
        assert isinstance(guard, ast.Try) and len(guard.handlers) == 1
        assert guard.handlers[0].type.id == "BaseException"
        assert isinstance(guard.handlers[0].body[0], ast.Pass)
        call = guard.body[0].body[0].value
        assert call.func.value.id == name and call.func.attr == "close"
    decoder = ast.parse(inspect.getsource(indexed_codec.decode_indexed_module_file)).body[0]
    rollback = [node for node in ast.walk(decoder) if isinstance(node, ast.Try)
                and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                        and child.func.id == "_close_private_value_columns"
                        for handler in node.handlers for child in ast.walk(handler))]
    assert len(rollback) == 1
    assert isinstance(rollback[0].handlers[0].body[-1], ast.Raise)
    assert rollback[0].handlers[0].body[-1].exc is None


@pytest.mark.parametrize("mutation", ["extra", "renamed", "reordered"])
def test_private_value_inventory_still_rejects_unknown_columns(tmp_path, monkeypatch, mutation):
    import json

    path = _private_value_sidecar(tmp_path, monkeypatch)
    with path.open("rb") as stream:
        magic = stream.readline()
        header = json.loads(stream.read(int(stream.readline())))
        payload = stream.read()
    rows = header["functions"][0]["arenas"]
    if mutation == "extra":
        rows.append(["unexpected_private_column", 0])
    elif mutation == "renamed":
        rows[-1][0] = "unexpected_private_column"
    else:
        rows[-1], rows[-2] = rows[-2], rows[-1]
    raw = json.dumps(header, sort_keys=True).encode()
    path.write_bytes(magic + str(len(raw)).encode() + b"\n" + raw + payload)

    def no_payload_read(*args):
        pytest.fail("invalid inventory reached raw arena acquisition")

    monkeypatch.setattr(indexed_codec, "_read_host_arena", no_payload_read)
    monkeypatch.setattr(indexed_codec, "_read_native_arena", no_payload_read)
    with pytest.raises(ValueError, match="arena (inventory|order) is invalid"):
        decode_indexed_module_file(str(path))


def _native_private_columns_source():
    import ast
    import inspect
    from pathlib import Path
    import textwrap
    from pcc.backend import self_backend_value_arena as arena_module

    original = inspect.getsource(indexed_codec.decode_indexed_module_file)
    tree = ast.parse(original)
    owner_start = next(node for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)
                       and isinstance(node.target, ast.Name) and node.target.id == "value_scalars")
    guard = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                 and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                         and child.func.id == "_close_private_value_columns"
                         for handler in node.handlers for child in ast.walk(handler)))
    segment = textwrap.dedent("".join(original.splitlines(keepends=True)[
        owner_start.lineno - 1:guard.end_lineno
    ]))
    statements = ast.parse(segment).body
    assert len(statements) == 4 and isinstance(statements[-1], ast.Try)
    assert ast.dump(statements[-1], include_attributes=False) == ast.dump(guard, include_attributes=False)
    assert [node.target.id for node in statements[:3]] == list(_PRIVATE_VALUE_COLUMNS)
    replacement = "_columns_observed_read(fd, count, wire_name)"
    assert segment.count("_read_native_arena(fd, count)") == 1
    observed = segment.replace("_read_native_arena(fd, count)", replacement)
    assert observed.replace(replacement, "_read_native_arena(fd, count)") == segment
    # The control uses the exact guard body without its exception handler.
    # It must leave observed owners open on malformed data/read failure, so
    # the checks cannot pass merely because the observer retains references.
    control = textwrap.dedent("".join(segment.splitlines(keepends=True)[
        statements[-1].body[0].lineno - 1:statements[-1].body[-1].end_lineno
    ]))
    control = "".join(segment.splitlines(keepends=True)[:3]) + control
    control = control.replace("_read_native_arena(fd, count)", replacement)
    prelude = '''    seed = function.seed
    native = True
    stream = 0
    inventory = [("value_scalars", counts[0]), ("definition_positions", counts[1]),
                 ("used_value_ids", counts[2])]
'''
    wrapper = (
        "\ndef _columns_restore(fd: int, counts: list[int], function: ColumnFunction) -> None:\n"
        + prelude + textwrap.indent(observed, "    ")
        + "\ndef _columns_unguarded_control(fd: int, counts: list[int], function: ColumnFunction) -> None:\n"
        + prelude + textwrap.indent(control, "    ")
    )
    arena = Path(arena_module.__file__).read_text().split("class CompilerRecordSpanArena:", 1)[0]
    source = (arena + "\n" + inspect.getsource(indexed_codec._read_native_arena)
              + "\n" + inspect.getsource(indexed_codec._close_private_value_columns)
              + _NATIVE_PRIVATE_COLUMNS_BOUNDARY + wrapper + _NATIVE_PRIVATE_COLUMNS_MAIN)
    ast.parse(source)
    return source, segment


_NATIVE_PRIVATE_COLUMNS_BOUNDARY = '''
import os
import sys
import gc

BackendUnavailable = ValueError
_ARENA_FIELDS = (("value_scalars", "value_scalars", ""),
                 ("definition_positions", "definition_positions", ""),
                 ("used_value_ids", "used_value_ids", ""))
_columns_observed: list[CompilerIntArena] = []
_columns_read_failure: str = ""
_columns_kernel_failure: bool = False

# Explicit boundary substitutes. Host tests cover the complete seed/kernel;
# this native fixture executes the unchanged production ownership transaction
# and real arenas without importing the full compiler/backend package closure.
class ColumnSeed:
    def __init__(self, empty: bool) -> None:
        self.value_names: list[str] = [] if empty else ["%x"]
        self.definition_blocks: list[int] = []
        self.definition_positions: list[int] = []
        self.value_type_ids: list[int] = []
        self.alloca_type_ids: list[int] = []
        self.value_is_used_flags: list[bool] = []
        self.value_last_use_positions: list[int] = []
        self.used_value_ids_in_order: list[int] = []

class ColumnKernel:
    def __init__(self, seed: ColumnSeed) -> None:
        self.value_scalars = CompilerIntArena()
        self.definition_positions = CompilerIntArena()
        self.used_value_ids = CompilerIntArena()
        for value_id in range(len(seed.value_names)):
            self.value_scalars.append(seed.definition_blocks[value_id])
            self.value_scalars.append(seed.value_type_ids[value_id])
            self.value_scalars.append(-1)
            self.value_scalars.append(-1)
            self.value_scalars.append(seed.alloca_type_ids[value_id])
            self.value_scalars.append(-1)
            self.value_scalars.append(seed.value_last_use_positions[value_id])
            self.value_scalars.append(1 if seed.value_is_used_flags[value_id] else 0)
            self.definition_positions.append(seed.definition_positions[value_id])
        for value in seed.used_value_ids_in_order:
            self.used_value_ids.append(value)

class ColumnFunction:
    def __init__(self, empty: bool) -> None:
        self.seed = ColumnSeed(empty)
        self.kernel: ColumnKernel | None = None

def get_indexed_function_kernel(function: ColumnFunction) -> ColumnKernel:
    if _columns_kernel_failure:
        raise ValueError("boundary kernel failure")
    kernel = ColumnKernel(function.seed)
    function.kernel = kernel
    return kernel

def _replace_seed_arena(seed: ColumnSeed, seed_field: str, arena: CompilerIntArena) -> None:
    raise AssertionError("seed payload adoption is outside this native boundary")

def _read_host_arena(stream: int, count: int) -> CompilerIntArena:
    raise AssertionError("native private columns must not use the host reader")

def _columns_observed_read(fd: int, count: int, wire_name: str) -> CompilerIntArena:
    if wire_name == _columns_read_failure:
        assert count > 0
        arena = _read_native_arena(-1, count)
    else:
        arena = _read_native_arena(fd, count)
    assert arena.uses_native_storage
    assert not arena._closed
    _columns_observed.append(arena)
    return arena
'''


_NATIVE_PRIVATE_COLUMNS_MAIN = '''
def main():
    global _columns_read_failure, _columns_kernel_failure
    case = sys.argv[1]
    control = case.startswith("control-")
    if control:
        case = case[8:]
    empty = case == "zero"
    function = ColumnFunction(empty)
    counts = [0, 0, 0] if empty else [8, 1, 1]
    if case == "short-value_scalars":
        counts[0] = 7
    elif case == "short-definition_positions":
        counts[1] = 0
    _columns_read_failure = case[5:] if case.startswith("read-") else ""
    _columns_kernel_failure = case == "kernel"
    failed = False
    fd = os.open(sys.argv[2], os.O_RDONLY)
    try:
        if control:
            _columns_unguarded_control(fd, counts, function)
        else:
            _columns_restore(fd, counts, function)
    except OSError:
        assert _columns_read_failure != ""
        failed = True
    except ValueError:
        assert _columns_read_failure == ""
        failed = True
    finally:
        os.close(fd)
    assert failed == (case not in ("valid", "zero"))
    expected = 3
    if _columns_read_failure == "value_scalars":
        expected = 0
    elif _columns_read_failure == "definition_positions":
        expected = 1
    elif _columns_read_failure == "used_value_ids":
        expected = 2
    assert len(_columns_observed) == expected
    gc.collect()
    for arena in _columns_observed:
        if control:
            assert not arena._closed
            assert arena.uses_native_storage
            arena.close()
        else:
            assert arena._closed
            assert arena.native_address() == 0
    if not failed:
        kernel = function.kernel
        assert isinstance(kernel, ColumnKernel)
        assert not kernel.value_scalars._closed
        assert not kernel.definition_positions._closed
        assert not kernel.used_value_ids._closed
        assert kernel.value_scalars.uses_native_storage
        assert kernel.definition_positions.uses_native_storage
        assert kernel.used_value_ids.uses_native_storage
        assert len(kernel.value_scalars) == (0 if empty else 8)
        assert len(kernel.definition_positions) == (0 if empty else 1)
        assert len(kernel.used_value_ids) == (0 if empty else 1)
        if not empty:
            assert kernel.value_scalars.get_unchecked(2) == -1
            assert kernel.value_scalars.get_unchecked(7) == 1
            assert kernel.definition_positions.get_unchecked(0) == 4
            assert kernel.used_value_ids.get_unchecked(0) == 0
        kernel.value_scalars.close()
        kernel.definition_positions.close()
        kernel.used_value_ids.close()
    print("private-columns|" + sys.argv[1] + "|ok")
main()
'''


@pytest.mark.integration
def test_private_value_columns_native_boundary_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive, request,
):
    """Exact production guard and raw readers; explicit seed/kernel substitutes."""
    import inspect
    import json
    import os
    from pathlib import Path
    import struct
    import subprocess
    import sys
    from pcc.backend import self_backend_value_arena as arena_module

    cases = ["short-value_scalars", "short-definition_positions",
             "prepared-2", "prepared-3", "prepared-5",
             "read-value_scalars", "read-definition_positions", "read-used_value_ids",
             "kernel", "valid", "zero", "control-prepared-2", "control-read-used_value_ids"]
    inputs = {}
    for case in cases:
        values = [0, 0, -1, -1, -1, -1, 7, 9]
        positions, used = [4], [0]
        if case == "short-value_scalars":
            values.pop()
        elif case == "short-definition_positions":
            positions.clear()
        elif case == "zero":
            values.clear()
            positions.clear()
            used.clear()
        elif "prepared-" in case:
            values[int(case[-1])] = 0
        path = tmp_path / (case + ".bin")
        path.write_bytes(b"".join(struct.pack("<q", value) for value in values + positions + used))
        inputs[case] = path
    source = tmp_path / "private_columns_native.py"
    source_text, segment = _native_private_columns_source()
    source.write_text(source_text)
    binary = tmp_path / "private_columns_native"

    def identity(path):
        if path is None:
            return None
        return {"path": os.path.abspath(os.fspath(path)),
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    captured = inspect.getclosurevars(python_program_compiler).nonlocals.get("compiler")
    if not isinstance(captured, (str, os.PathLike)):
        captured = None
    route_path = os.environ.get("PCC_TEST_COMPILER_LOG")
    route_offset = (Path(route_path).stat().st_size
                    if route_path and Path(route_path).is_file() else 0)
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    receipt = {
        "nominal_case": request.node.callspec.params["python_program_compiler"],
        "ownership_boundary": "nominal case is not compiler ownership evidence; inspect callable, captured compiler and route log",
        "compiler_callable": python_program_compiler.__module__ + "." + python_program_compiler.__qualname__,
        "compiler_callable_source": identity(inspect.getsourcefile(python_program_compiler)),
        "captured_compiler_before": identity(captured),
        "host_process": identity(sys.executable), "source": identity(source),
        "runtime_archive": identity(pcc_runtime_archive),
        "codec": identity(indexed_codec.__file__), "arena": identity(arena_module.__file__),
        "transaction_sha256": hashlib.sha256(segment.encode()).hexdigest(),
        "inputs": {case: identity(path) for case, path in inputs.items()},
        "instrumentation": "exact production transaction and helper; one reversible read-call observer with real invalid FD; retained real arena objects, no raw-pointer recovery; unguarded-body leak controls",
        "scope": "extracted three-column guard with seed/kernel substitutes, not full decoder, native constructor/close fault injection, fixed point, RSS or performance",
        "returned_args": None, "route_records": [], "gc_runs": [],
    }
    receipt_path = tmp_path / "private_columns_native.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    try:
        compiled = python_program_compiler(
            str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive),
        )
        args = getattr(compiled, "args", None)
        if args is not None:
            receipt["returned_args"] = ([os.fsdecode(value) for value in args]
                                        if isinstance(args, (tuple, list)) else os.fsdecode(args))
    finally:
        receipt["captured_compiler_after"] = identity(captured)
        if route_path and Path(route_path).is_file():
            with Path(route_path).open("rb") as stream:
                stream.seek(route_offset)
                for line in stream:
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as error:
                        receipt["route_records"].append({"unparsed": str(error)})
                        continue
                    if record.get("test") == current_test and record.get("kind") == "compile_python":
                        receipt["route_records"].append(record)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["binary"] = identity(binary)
    for collector in range(5):
        for case in cases:
            result = subprocess.run(
                [str(binary), case, str(inputs[case])], capture_output=True, text=True, timeout=30,
                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector)),
            )
            receipt["gc_runs"].append({"collector": collector, "case": case,
                                       "returncode": result.returncode,
                                       "stdout": result.stdout, "stderr": result.stderr})
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            assert result.returncode == 0, (collector, case, result.stdout, result.stderr)
            assert result.stderr == ""
            assert result.stdout == "private-columns|" + case + "|ok\n"


_KERNEL_PRIVATE_FIELDS = (
    "definition_positions", "type_field_ids", "type_scalars", "value_scalars",
    "used_value_ids", "slot_scalars", "block_layout_ids",
)


def _kernel_private_wire_row(case="valid"):
    row = {
        "name": "private_owner_probe", "block_names": [], "value_names": [],
        "instruction_arithmetic_flags": [], "cold_instruction_data": [],
        "call_texts": [], "types": [], "args": [],
        "ret_type": ["void", 0, None, 0, None, "", []],
        "arenas": [[name, 0] for name, _, _ in _ARENA_FIELDS],
    }
    payload = b""
    if case == "spans":
        next(item for item in row["arenas"] if item[0] == "error_edge_spans")[1] = 1
        payload = b"\0" * 8
    elif case == "nested-type":
        integer = ["int", 64, None, 0, None, "", []]
        row["types"] = [["ptr", 0, integer, 0, None, "", []]]
    elif case == "metadata":
        next(item for item in row["arenas"] if item[0] == "instruction_metadata")[1] = 1
        payload = b"\0" * 8
    return row, payload


def _kernel_private_sidecar(tmp_path, case):
    import json

    row, payload = _kernel_private_wire_row(case)
    header = {"schema": indexed_codec._SCHEMA, "scalar_width": 8,
              "byte_order": "little", "functions": [row], "globals": [],
              "total_scalars": len(payload) // 8}
    raw = json.dumps(header, sort_keys=True).encode()
    path = tmp_path / (case + "-kernel.pidx")
    path.write_bytes(indexed_codec._MAGIC + str(len(raw)).encode() + b"\n" + raw + payload)
    return path


def _observe_kernel_private_acquisitions(monkeypatch, *, fail_at=None,
                                         failure=None, close_at=None):
    from pcc.backend import self_backend_kernel as kernels

    original_init = kernels.CompilerIntArena.__init__
    original_close = kernels.CompilerIntArena.close
    original_constructor = kernels.IndexedFunctionKernel.__init__
    owners, attempts, functions = [], [], []
    active = False

    def initialize(arena, initial_capacity=8):
        if active and len(owners) == fail_at:
            raise failure
        original_init(arena, initial_capacity)
        if active:
            owners.append(arena)

    def close(arena):
        index = next((i for i, owner in enumerate(owners) if owner is arena), None)
        if index is not None:
            attempts.append(index)
            if index == close_at:
                raise KeyboardInterrupt("secondary constructor cleanup")
        original_close(arena)

    def constructor(kernel, function):
        nonlocal active
        functions.append(function)
        active = True
        try:
            return original_constructor(kernel, function)
        finally:
            active = False

    monkeypatch.setattr(kernels.CompilerIntArena, "__init__", initialize)
    monkeypatch.setattr(kernels.CompilerIntArena, "close", close)
    monkeypatch.setattr(kernels.IndexedFunctionKernel, "__init__", constructor)

    def finish():
        monkeypatch.setattr(kernels.CompilerIntArena, "close", original_close)
        for owner in owners:
            original_close(owner)
        for function in functions:
            if function.indexed_kernel is not None:
                function.indexed_kernel.close_native_tables()
            elif function.indexed_seed is not None:
                indexed_codec._close_private_seed(function.indexed_seed)

    return owners, attempts, functions, finish


def _assert_kernel_seed_aliases_open(function):
    seed = function.indexed_seed
    assert seed is not None
    assert function.indexed_kernel is None
    for _, _, field in _ARENA_FIELDS:
        if field:
            assert not getattr(seed, field)._closed
    assert not seed.block_name_index._closed
    assert not seed.value_name_index._closed


@pytest.mark.parametrize("case,count,message", [
    ("nested-type", 3, "indexed call plane omitted a nested type record"),
    ("spans", 7, "indexed inline error-edge spans do not match block count"),
    ("metadata", 7, "indexed function seed metadata count is inconsistent"),
])
@pytest.mark.parametrize("close_at", [None, 0, 1, 2, 3, 4, 5, 6])
def test_kernel_private_owners_real_decoder_failures(tmp_path, monkeypatch, case, count,
                                                     message, close_at):
    from pcc.backend import BackendUnavailable

    path = _kernel_private_sidecar(tmp_path, case)
    owners, attempts, functions, finish = _observe_kernel_private_acquisitions(
        monkeypatch, close_at=close_at,
    )
    try:
        with pytest.raises(BackendUnavailable, match=message):
            decode_indexed_module_file(str(path))
        assert len(owners) == count
        assert attempts == list(range(count))
        assert [owner._closed for owner in owners] == [i != close_at for i in range(count)]
        _assert_kernel_seed_aliases_open(functions[0])
    finally:
        finish()


@pytest.mark.parametrize("failure_type", [MemoryError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("fail_at", range(7))
@pytest.mark.parametrize("close_at", [None, 0])
def test_kernel_private_partial_acquisitions_preserve_exception(
    tmp_path, monkeypatch, failure_type, fail_at, close_at,
):
    path = _kernel_private_sidecar(tmp_path, "valid")
    original = failure_type("constructor acquisition failed")
    cause = ValueError("original cause")
    original.__cause__ = cause
    owners, attempts, functions, finish = _observe_kernel_private_acquisitions(
        monkeypatch, fail_at=fail_at, failure=original, close_at=close_at,
    )
    try:
        with pytest.raises(failure_type) as caught:
            decode_indexed_module_file(str(path))
        assert caught.value is original
        assert caught.value.__cause__ is cause
        assert caught.value.__context__ is None
        assert len(owners) == fail_at
        assert attempts == list(range(fail_at))
        assert [owner._closed for owner in owners] == [i != close_at for i in range(fail_at)]
        _assert_kernel_seed_aliases_open(functions[0])
    finally:
        finish()


def test_kernel_private_zero_length_success_handoff(tmp_path, monkeypatch):
    path = _kernel_private_sidecar(tmp_path, "valid")
    owners, attempts, functions, finish = _observe_kernel_private_acquisitions(monkeypatch)
    try:
        module = decode_indexed_module_file(str(path))
        function = module.functions[0]
        assert function is functions[0]
        assert function.indexed_seed is None
        kernel = function.indexed_kernel
        assert kernel is not None
        assert len(owners) == 7
        assert attempts == []
        for field, owner in zip(_KERNEL_PRIVATE_FIELDS, owners):
            assert getattr(kernel, field) is owner
            assert len(owner) == 0
            assert not owner._closed
        assert get_indexed_function_kernel(function) is kernel
        assert len(owners) == 7
    finally:
        finish()


def test_kernel_private_helper_keeps_unrelated_owners_open(monkeypatch):
    from pcc.backend import self_backend_kernel as kernels

    borrowed = kernels.CompilerIntArena(0)
    private = [kernels.CompilerIntArena(0) for _ in range(7)]
    try:
        kernels._close_private_kernel_tables(*private)
        assert all(owner._closed for owner in private)
        assert not borrowed._closed
    finally:
        borrowed.close()
        for owner in private:
            owner.close()


def test_kernel_private_constructor_guard_ast_contract():
    import ast
    import inspect
    import textwrap
    from pcc.backend import self_backend_kernel as kernels

    constructor = ast.parse(textwrap.dedent(inspect.getsource(kernels.IndexedFunctionKernel.__init__))).body[0]
    guard = next(node for node in constructor.body if isinstance(node, ast.Try))
    assert len(guard.handlers) == 1
    assert isinstance(guard.handlers[0].type, ast.Name)
    assert guard.handlers[0].type.id == "BaseException"
    assert isinstance(guard.handlers[0].body[-1], ast.Raise)
    assert guard.handlers[0].body[-1].exc is None
    initialized = [node.target.id for node in constructor.body if isinstance(node, ast.AnnAssign)]
    assert initialized == ["private_" + field for field in _KERNEL_PRIVATE_FIELDS]
    helper = ast.parse(inspect.getsource(kernels._close_private_kernel_tables)).body[0]
    releases = [node for node in helper.body if isinstance(node, ast.Try)]
    assert len(releases) == 7
    assert all(isinstance(node.handlers[0].type, ast.Name)
               and node.handlers[0].type.id == "BaseException" for node in releases)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "close_native_tables" for node in ast.walk(constructor))


def _native_kernel_private_source():
    """Exact acquisition/rollback syntax; deliberately excludes the kernel body."""
    import ast
    import inspect
    import textwrap
    from pathlib import Path
    from pcc.backend import self_backend_value_arena as arena_module
    from pcc.backend import self_backend_kernel as kernels

    constructor_source = textwrap.dedent(inspect.getsource(kernels.IndexedFunctionKernel.__init__))
    constructor = ast.parse(constructor_source).body[0]
    guard = next(node for node in constructor.body if isinstance(node, ast.Try))
    initializers = [ast.get_source_segment(constructor_source, node)
                    for node in constructor.body if isinstance(node, ast.AnnAssign)]
    assert len(initializers) == 7
    acquisitions = []
    for node in guard.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"private_" + field for field in _KERNEL_PRIVATE_FIELDS}):
            acquisitions.append(ast.get_source_segment(constructor_source, node))
    assert len(acquisitions) == 7
    handler = ast.get_source_segment(constructor_source, guard.handlers[0])
    handler = textwrap.dedent("    " + handler)
    helper = inspect.getsource(kernels._close_private_kernel_tables)
    body = ""
    for index, acquisition in enumerate(acquisitions):
        body += f'if fail_at == {index}:\n    raise ValueError("acquisition boundary")\n'
        body += acquisition + "\n"
        body += "owners.append(private_" + _KERNEL_PRIVATE_FIELDS[index] + ")\n"
        body += "assert private_" + _KERNEL_PRIVATE_FIELDS[index] + ".uses_native_storage\n"
        body += "assert len(private_" + _KERNEL_PRIVATE_FIELDS[index] + ") == 0\n"
    body += 'if fail_at == 7:\n    raise ValueError("validation boundary")\n'
    # Reuse the real arena source/support without importing the full backend
    # package into this isolated native protocol fixture.
    arena = Path(arena_module.__file__).read_text().split("class CompilerRecordSpanArena:", 1)[0]
    source = arena + "\n" + helper + "\n"
    for name, guarded in (("run_guarded", True), ("run_control", False)):
        source += "def " + name + "(fail_at: int) -> None:\n"
        source += "    owners: list[CompilerIntArena] = []\n"
        source += textwrap.indent("\n".join(initializers), "    ") + "\n"
        source += "    try:\n"
        if guarded:
            source += "        try:\n" + textwrap.indent(body, "            ")
            source += textwrap.indent(handler, "        ") + "\n"
        else:
            source += textwrap.indent(body, "        ")
        source += "    except ValueError:\n        pass\n"
        source += "    assert len(owners) == min(fail_at, 7)\n"
        source += "    for owner in owners:\n"
        if guarded:
            source += "        if fail_at < 8:\n            assert owner._closed\n        else:\n            assert not owner._closed\n"
        else:
            source += "        assert not owner._closed\n"
        source += "        owner.close()\n\n"
    source += "def zero_capacity() -> None:\n"
    for field in _KERNEL_PRIVATE_FIELDS:
        source += "    " + field + " = CompilerIntArena(0)\n"
        source += "    assert " + field + ".uses_native_storage\n"
        source += "    assert len(" + field + ") == 0\n"
    source += "    _close_private_kernel_tables(" + ", ".join(_KERNEL_PRIVATE_FIELDS) + ")\n"
    for field in _KERNEL_PRIVATE_FIELDS:
        source += "    assert " + field + "._closed\n"
    source += "def main() -> None:\n    for position in range(9):\n        run_guarded(position)\n        run_control(position)\n    zero_capacity()\n    print('kernel-private-boundary|ok')\nmain()\n"
    ast.parse(source)
    return source, handler, helper


@pytest.mark.integration
def test_kernel_private_acquisition_rollback_native_boundary_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive, request,
):
    """Real native arenas; exact helper/handler, synthetic constructor body."""
    import inspect
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    from pcc.backend import self_backend_kernel as kernels
    from pcc.backend import self_backend_value_arena as arena_module

    source_text, handler, helper = _native_kernel_private_source()
    source = tmp_path / "kernel_private_native.py"
    source.write_text(source_text)
    binary = tmp_path / "kernel_private_native"

    def identity(path):
        if path is None:
            return None
        return {"path": os.path.abspath(os.fspath(path)),
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    captured = inspect.getclosurevars(python_program_compiler).nonlocals.get("compiler")
    if not isinstance(captured, (str, os.PathLike)):
        captured = None
    route_path = os.environ.get("PCC_TEST_COMPILER_LOG")
    route_offset = (Path(route_path).stat().st_size
                    if route_path and Path(route_path).is_file() else 0)
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    receipt = {
        "nominal_case": request.node.callspec.params["python_program_compiler"],
        "ownership_boundary": "nominal fixture is not compiler ownership evidence; inspect callable, captured compiler, route log",
        "compiler_callable": python_program_compiler.__module__ + "." + python_program_compiler.__qualname__,
        "compiler_callable_source": identity(inspect.getsourcefile(python_program_compiler)),
        "captured_compiler_before": identity(captured), "host_process": identity(sys.executable),
        "source": identity(source), "kernel": identity(kernels.__file__),
        "arena": identity(arena_module.__file__), "runtime_archive": identity(pcc_runtime_archive),
        "handler_sha256": hashlib.sha256(handler.encode()).hexdigest(),
        "helper_sha256": hashlib.sha256(helper.encode()).hexdigest(),
        "scope": "native seven-owner acquisition and rollback boundary only; exact production optional locals, acquisitions, handler and helper; synthetic failure body with retained arena observations; no full kernel/decoder or native malloc/close fault injection, fixed point, RSS or performance claim",
        "returned_args": None, "route_records": [], "gc_runs": [],
    }
    receipt_path = tmp_path / "kernel_private_native.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    try:
        compiled = python_program_compiler(
            str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive),
        )
        args = getattr(compiled, "args", None)
        if args is not None:
            receipt["returned_args"] = ([os.fsdecode(value) for value in args]
                                        if isinstance(args, (tuple, list)) else os.fsdecode(args))
    finally:
        receipt["captured_compiler_after"] = identity(captured)
        if route_path and Path(route_path).is_file():
            with Path(route_path).open("rb") as stream:
                stream.seek(route_offset)
                for line in stream:
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as error:
                        receipt["route_records"].append({"unparsed": str(error)})
                        continue
                    if record.get("test") == current_test and record.get("kind") == "compile_python":
                        receipt["route_records"].append(record)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["binary"] = identity(binary)
    for collector in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector)),
        )
        receipt["gc_runs"].append({"collector": collector, "returncode": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr})
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == ""
        assert result.stdout == "kernel-private-boundary|ok\n"


def test_kernel_private_nonempty_decoder_success_handoff(tmp_path, monkeypatch):
    path = _private_value_sidecar(tmp_path, monkeypatch)
    owners, attempts, functions, finish = _observe_kernel_private_acquisitions(monkeypatch)
    try:
        module = decode_indexed_module_file(str(path))
        assert len(owners) == 7 * len(module.functions)
        assert len(functions) == len(module.functions)
        assert attempts == []
        for index, function in enumerate(module.functions):
            kernel = function.indexed_kernel
            assert kernel is not None
            assert function.indexed_seed is None
            for offset, field in enumerate(_KERNEL_PRIVATE_FIELDS):
                owner = owners[index * 7 + offset]
                assert getattr(kernel, field) is owner
                assert not owner._closed
        assert any(len(owner) for owner in owners)
    finally:
        finish()


def test_kernel_private_default_constructor_success(monkeypatch):
    from pcc.backend import self_backend_kernel as kernels

    row, _ = _kernel_private_wire_row()
    function, _ = indexed_codec._seed_from_wire(row)
    old_seed = function.indexed_seed
    function.indexed_seed = None
    try:
        kernel = kernels.IndexedFunctionKernel(function)
        try:
            assert function.indexed_kernel is None
            assert function.indexed_seed is None
            for field in _KERNEL_PRIVATE_FIELDS:
                assert not getattr(kernel, field)._closed
        finally:
            kernel.close_native_tables()
    finally:
        indexed_codec._close_private_seed(old_seed)


@pytest.mark.parametrize("failure_type", [ValueError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("close_at", [None, 0, 6])
def test_kernel_private_default_constructor_failure_scope(monkeypatch, failure_type, close_at):
    from pcc.backend import self_backend_kernel as kernels

    row, _ = _kernel_private_wire_row()
    function, _ = indexed_codec._seed_from_wire(row)
    old_seed = function.indexed_seed
    function.indexed_seed = None
    indexed_codec._close_private_seed(old_seed)
    original = failure_type("late no-seed construction failure")
    cause = ValueError("original cause")
    original.__cause__ = cause
    captured, private, attempts = [], [], []
    real_close = kernels.CompilerIntArena.close

    def fail(kernel):
        captured.append(kernel)
        private.extend(getattr(kernel, field) for field in _KERNEL_PRIVATE_FIELDS)
        raise original

    def close(arena):
        index = next((i for i, owner in enumerate(private) if owner is arena), None)
        if index is not None:
            attempts.append(index)
            if index == close_at:
                raise KeyboardInterrupt("secondary no-seed cleanup")
        real_close(arena)

    monkeypatch.setattr(kernels.IndexedFunctionKernel, "_freeze_value_scalar_columns", fail)
    monkeypatch.setattr(kernels.CompilerIntArena, "close", close)
    try:
        with pytest.raises(failure_type) as caught:
            kernels.IndexedFunctionKernel(function)
        assert caught.value is original
        assert caught.value.__cause__ is cause
        assert caught.value.__context__ is None
        assert attempts == list(range(7))
        assert [owner._closed for owner in private] == [i != close_at for i in range(7)]
        assert function.indexed_kernel is None and function.indexed_seed is None
        kernel = captured[0]
        other_owners = [getattr(kernel, field) for field in kernels.IndexedFunctionKernel.__slots__
                        if isinstance(getattr(kernel, field, None), kernels.CompilerIntArena)
                        and field not in _KERNEL_PRIVATE_FIELDS]
        assert other_owners
        assert all(not owner._closed for owner in other_owners)
    finally:
        monkeypatch.setattr(kernels.CompilerIntArena, "close", real_close)
        for kernel in captured:
            kernel.close_native_tables()


def _block_fact_writer_module(monkeypatch, producer, block_count, version):
    """Real writers for seed and ordinary ParsedFunction construction."""
    from pcc.backend.self_backend_ir import (
        ParsedBlock, ParsedFunction, ParsedInstr, ParsedModule, TypeDesc,
        TypeParseContext,
    )

    if producer == "seed":
        assert block_count > 0
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
        source = ir.Module(name="block_fact_shape")
        source.triple = "arm64-apple-darwin23.6.0"
        function = ir.Function(source, ir.FunctionType(ir.VoidType(), []), name="probe")
        blocks = [function.append_basic_block("b" + str(i)) for i in range(block_count)]
        for i, block in enumerate(blocks):
            builder = ir.IRBuilder(block)
            if i + 1 < block_count:
                builder.branch(blocks[i + 1])
            else:
                builder.ret_void()
        module = source.direct_indexed_module()
    else:
        assert producer == "ordinary"
        blocks = []
        for i in range(block_count):
            terminator = (ParsedInstr("br", ("b" + str(i + 1),))
                          if i + 1 < block_count else ParsedInstr("ret_void", ()))
            blocks.append(ParsedBlock("b" + str(i), terminator=terminator))
        function = ParsedFunction("probe", TypeDesc("void"), [], True, False, blocks)
        assert function.indexed_seed is None
        get_indexed_function_kernel(function)
        module = ParsedModule("arm64-apple-darwin23.6.0", (), (function,), TypeParseContext())
    if version == 2:
        from pcc.backend.self_backend_debug import DebugMetadata
        # Empty original debug metadata is a legitimate v2 transport, not a
        # source-level debug emission claim.
        assert module.type_context is not None
        module.type_context.debug_info = DebugMetadata({})
    elif version == 3:
        module.functions[0].definition_linkage = "external_odr"
    else:
        assert version == 1
    return module


def _block_fact_wire_parts(path):
    import json

    with path.open("rb") as stream:
        assert stream.readline() == indexed_codec._MAGIC
        header = json.loads(stream.read(int(stream.readline())))
        payload = stream.read()
    assert len(payload) == header["total_scalars"] * 8
    return header, payload


def _rewrite_block_fact_count(path, count):
    import json

    header, payload = _block_fact_wire_parts(path)
    offset = 0
    inventory = header["functions"][0]["arenas"]
    for row in inventory:
        if row[0] == "block_facts":
            old_count = row[1]
            old = payload[offset:offset + old_count * 8]
            replacement = old[:count * 8] + b"\0" * max(0, (count - old_count) * 8)
            payload = payload[:offset] + replacement + payload[offset + old_count * 8:]
            row[1] = count
            header["total_scalars"] += count - old_count
            break
        offset += row[1] * 8
    else:
        raise AssertionError("writer omitted block_facts")
    raw = json.dumps(header, sort_keys=True).encode()
    path.write_bytes(indexed_codec._MAGIC + str(len(raw)).encode() + b"\n" + raw + payload)
    checked, checked_payload = _block_fact_wire_parts(path)
    assert checked["total_scalars"] == sum(
        row[1] for function in checked["functions"] for row in function["arenas"]
    )
    assert len(checked_payload) == checked["total_scalars"] * 8


@pytest.mark.parametrize("producer", ["seed", "ordinary"])
@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("count", [0, 3, 4, 7, 9, 12])
def test_block_fact_shape_rejected_before_raw_acquisition(
    tmp_path, monkeypatch, producer, version, count,
):
    module = _block_fact_writer_module(monkeypatch, producer, 2, version)
    path = tmp_path / "block-facts.pidx"
    try:
        encode_indexed_module_file(str(path), module)
        _rewrite_block_fact_count(path, count)
        captured = _capture_private_seed(monkeypatch)

        def forbidden(*args, **kwargs):
            pytest.fail("malformed block facts reached raw acquisition or kernel adoption")

        monkeypatch.setattr(indexed_codec, "_read_host_arena", forbidden)
        monkeypatch.setattr(indexed_codec, "_read_native_arena", forbidden)
        monkeypatch.setattr(indexed_codec, "get_indexed_function_kernel", forbidden)
        with pytest.raises(ValueError, match="^indexed module block fact columns are inconsistent$"):
            decode_indexed_module_file(str(path))
        assert len(captured) == 1
        seed, originals = captured[0]
        assert all(getattr(seed, field)._closed for field in _PRIVATE_SEED_FIELDS)
        assert all(arena._closed for arena in originals.values())
    finally:
        for function in module.functions:
            function.indexed_kernel.close_native_tables()


@pytest.mark.parametrize("producer", ["seed", "ordinary"])
@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("block_count", [1, 3])
def test_block_fact_shape_writer_roundtrip(tmp_path, monkeypatch, producer, version, block_count):
    module = _block_fact_writer_module(monkeypatch, producer, block_count, version)
    path = tmp_path / "block-facts-valid.pidx"
    restored = None
    try:
        encode_indexed_module_file(str(path), module)
        header, _ = _block_fact_wire_parts(path)
        assert header["schema"] == (indexed_codec._SCHEMA, indexed_codec._DEBUG_SCHEMA,
                                    indexed_codec._ODR_SCHEMA)[version - 1]
        restored = decode_indexed_module_file(str(path))
        original = module.functions[0].indexed_kernel
        decoded = restored.functions[0].indexed_kernel
        assert len(decoded.block_names) == block_count
        assert len(decoded.block_facts) == block_count * 4
        for _, field, _ in _ARENA_FIELDS:
            assert _arena_values(decoded, field) == _arena_values(original, field)
        for block_id in range(block_count):
            assert decoded.block_fact(block_id) == original.block_fact(block_id)
    finally:
        for owner in (module, restored):
            if owner is not None:
                for function in owner.functions:
                    function.indexed_kernel.close_native_tables()


def test_block_fact_shape_zero_block_writer_transport(tmp_path, monkeypatch):
    # The writer accepts an explicitly published empty ordinary kernel. This
    # is transport compatibility only, not a valid executable function claim.
    module = _block_fact_writer_module(monkeypatch, "ordinary", 0, 1)
    path = tmp_path / "zero-block-transport.pidx"
    restored = None
    try:
        encode_indexed_module_file(str(path), module)
        restored = decode_indexed_module_file(str(path))
        assert restored.functions[0].indexed_kernel.block_names == []
        assert len(restored.functions[0].indexed_kernel.block_facts) == 0
    finally:
        for owner in (module, restored):
            if owner is not None:
                owner.functions[0].indexed_kernel.close_native_tables()


def _block_fact_shape_guard_source():
    import ast
    import inspect

    source = inspect.getsource(indexed_codec._seed_from_wire)
    function = ast.parse(source).body[0]
    candidates = [node for node in ast.walk(function) if isinstance(node, ast.If)
                  and any(isinstance(child, ast.Constant)
                          and child.value == "indexed module block fact columns are inconsistent"
                          for child in ast.walk(node))]
    assert len(candidates) == 1
    guard = candidates[0]
    # Lock the exact minimal predicate and the inventory insertion ordering.
    expected = ast.parse('if expected_name == "block_facts" and row[1] != len(seed.block_names) * 4:\n'
                         '    raise BackendUnavailable("indexed module block fact columns are inconsistent")').body[0]
    assert ast.dump(guard, include_attributes=False) == ast.dump(expected, include_attributes=False)
    loop = next(node for node in ast.walk(function) if isinstance(node, ast.While)
                and guard in node.body)
    index = loop.body.index(guard)
    assert isinstance(loop.body[index - 1], ast.If)
    assert isinstance(loop.body[index + 1], ast.Expr)
    assert loop.body[index + 1].value.func.attr == "append"
    assert loop.body[index + 1].value.func.value.id == "arena_inventory"
    return ast.get_source_segment(source, guard)


def test_block_fact_shape_guard_inventory_boundary():
    _block_fact_shape_guard_source()


def _native_block_fact_shape_source():
    """Exact guard with typed synthetic inventory, not the full JSON decoder."""
    import ast
    import textwrap

    guard = _block_fact_shape_guard_source()
    # ast.get_source_segment retains the original body indentation; dedent
    # relative to its production inventory-loop column before embedding it.
    guard = textwrap.dedent("            " + guard)
    source = '''BackendUnavailable = ValueError

class WireSeed:
    def __init__(self, count: int) -> None:
        self.block_names: list[str] = []
        for index in range(count):
            self.block_names.append("b")

def check(block_count: int, count: int, expected_name: str, reject: bool) -> None:
    seed = WireSeed(block_count)
    # The real inventory validates the name separately; this typed synthetic
    # row isolates its already-validated integer count. No JSON/seed claim.
    row: list[int] = [0, count]
    reached_acquisition = False
    failed = False
    try:
'''
    source += textwrap.indent(guard, "        ") + "\n"
    source += '''        reached_acquisition = True
    except ValueError as error:
        assert str(error) == "indexed module block fact columns are inconsistent"
        failed = True
    assert failed == reject
    assert reached_acquisition != reject

def main() -> None:
    for blocks in range(4):
        check(blocks, blocks * 4, "block_facts", False)
        check(blocks, blocks * 4 + 1, "block_facts", True)
        check(blocks, blocks * 4 + 4, "block_facts", True)
        if blocks > 0:
            check(blocks, blocks * 4 - 1, "block_facts", True)
            check(blocks, blocks * 4 - 4, "block_facts", True)
        check(blocks, 1, "call_records", False)
    print("block-fact-boundary|ok")
main()
'''
    ast.parse(source)
    return source, guard


@pytest.mark.integration
def test_block_fact_shape_native_boundary_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive, request,
):
    """Exact inventory guard only; synthetic typed seed/count and acquisition marker."""
    import inspect
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    source_text, guard = _native_block_fact_shape_source()
    source = tmp_path / "block_fact_native.py"
    source.write_text(source_text)
    binary = tmp_path / "block_fact_native"

    def identity(path):
        if path is None:
            return None
        return {"path": os.path.abspath(os.fspath(path)),
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    captured = inspect.getclosurevars(python_program_compiler).nonlocals.get("compiler")
    if not isinstance(captured, (str, os.PathLike)):
        captured = None
    route_path = os.environ.get("PCC_TEST_COMPILER_LOG")
    route_offset = (Path(route_path).stat().st_size
                    if route_path and Path(route_path).is_file() else 0)
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    receipt = {
        "nominal_case": request.node.callspec.params["python_program_compiler"],
        "ownership_boundary": "nominal fixture is not compiler ownership evidence; inspect callable, captured compiler, route log",
        "compiler_callable": python_program_compiler.__module__ + "." + python_program_compiler.__qualname__,
        "compiler_callable_source": identity(inspect.getsourcefile(python_program_compiler)),
        "captured_compiler_before": identity(captured), "host_process": identity(sys.executable),
        "source": identity(source), "codec": identity(indexed_codec.__file__), "runtime_archive": identity(pcc_runtime_archive),
        "guard_sha256": hashlib.sha256(guard.encode()).hexdigest(),
        "scope": "exact native block-fact inventory guard only; synthetic typed seed/count row and post-guard acquisition marker; no full JSON/decoder/kernel/native arena access or OOB control, fixed point, RSS or performance claim",
        "returned_args": None, "route_records": [], "gc_runs": [],
    }
    receipt_path = tmp_path / "block_fact_native.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    try:
        compiled = python_program_compiler(
            str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive),
        )
        args = getattr(compiled, "args", None)
        if args is not None:
            receipt["returned_args"] = ([os.fsdecode(value) for value in args]
                                        if isinstance(args, (tuple, list)) else os.fsdecode(args))
    finally:
        receipt["captured_compiler_after"] = identity(captured)
        if route_path and Path(route_path).is_file():
            with Path(route_path).open("rb") as stream:
                stream.seek(route_offset)
                for line in stream:
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as error:
                        receipt["route_records"].append({"unparsed": str(error)})
                        continue
                    if record.get("test") == current_test and record.get("kind") == "compile_python":
                        receipt["route_records"].append(record)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["binary"] = identity(binary)
    for collector in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector)),
        )
        receipt["gc_runs"].append({"collector": collector, "returncode": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr})
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert result.returncode == 0, (collector, result.stdout, result.stderr)
        assert result.stderr == ""
        assert result.stdout == "block-fact-boundary|ok\n"
