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
