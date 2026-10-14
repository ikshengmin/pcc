"""Bounded host-only PCO window ownership and final-image parity checks."""

import struct
import sys

import pytest

from pcc.backend import macho_exec, macho_link, macho_obj as obj, macho_spec as spec
from pcc.backend import native_object as native, precise_stackmap as maps


def _budget(limit=1_400_000):
    word = struct.calcsize("P")
    return maps.CompactStackMapBudget(
        limit, 1_000_000, set_header_bytes=sys.getsizeof(set()),
        set_entry_bytes=2 * word, integer_bytes=sys.getsizeof((1 << 64) - 1),
        metadata_entry_bytes=64 * word + sys.getsizeof((0, 0, 0))
        + 3 * sys.getsizeof((1 << 64) - 1) + sys.getsizeof(b""),
        allocation_quantum=2 * word,
    )


def _write_inputs(tmp_path, count=32, relocations=1024):
    paths = []
    for index in range(count):
        name = "_main" if index == 0 else "_unit" + str(index)
        sections = [
            obj.Section(
                "__text", "__TEXT", data=b"\xc0\x03\x5f\xd6",
                flags=obj.TEXT_SECTION_FLAGS, align_log2=2,
                symbols=(obj.TextSymbol(name, 0),),
            ),
            obj.Section(
                "__data", "__DATA", data=b"\0" * (8 * relocations),
                relocations=tuple(obj.Relocation(
                    8 * row, "_main", spec.ARM64_RELOC_UNSIGNED, False, 3,
                ) for row in range(relocations)),
            ),
        ]
        if index == 0:
            payload = maps.encode_stack_map(maps.PreciseStackMap(
                arch=maps.ARCH_AARCH64,
                functions=(maps.FunctionStackMap(
                    function_id=maps.function_id(name), function_address=0,
                    code_size=4, frame_size=0, records=(),
                ),),
            ))
            offset, = maps.function_address_offsets(payload)
            sections.append(obj.Section(
                "__pcc_stackmaps", "__DATA", data=payload,
                relocations=(obj.Relocation(
                    offset, name, spec.ARM64_RELOC_UNSIGNED, False, 3,
                ),),
            ))
        value = native.NativeObject.from_sections(
            sections, undefined=() if index == 0 else ("_main",),
        )
        path = tmp_path / (str(index) + ".pco")
        path.write_bytes(native.encode_native_object(value))
        paths.append(str(path))
    return paths


def test_windowed_preparation_admits_more_than_aggregate_raw_bytes(tmp_path):
    from pcc.backend.macho_host_prepare import prepare_executable_from_paths

    paths = _write_inputs(tmp_path)
    from pathlib import Path
    assert sum(Path(path).stat().st_size for path in paths) > _budget().limit_bytes
    eager = [native.decode_packed_native_object(Path(path).read_bytes()) for path in paths]
    with pytest.raises(macho_link.LinkError, match="Mach-O input stack-map snapshot"):
        macho_exec.prepare_executable_object(
            eager, _consume_inputs=True, _source_view=True, compact_budget=_budget(),
        )
    del eager
    prepared = prepare_executable_from_paths(paths, compact_budget=_budget())
    assert isinstance(prepared, native.OwnedMergedSourceView)
    assert sum(len(rows) for rows in prepared._raw_relocations) == (32 * 1024 + 1) * 12


@pytest.mark.parametrize("change", ("same-size", "longer", "shorter"))
def test_second_window_checks_exact_input_bytes(tmp_path, monkeypatch, change):
    from pathlib import Path
    from pcc.backend import macho_host_prepare as host

    paths = _write_inputs(tmp_path, count=2, relocations=8)
    read = host._read_window
    mutated = False

    def mutate(path, ledger, expected_size=None, expected_digest=None):
        nonlocal mutated
        if expected_digest is not None and not mutated:
            value = Path(path).read_bytes()
            if change == "same-size":
                value = value[:-1] + bytes([value[-1] ^ 1])
            elif change == "longer":
                value += b"x"
            else:
                value = value[:-1]
            Path(path).write_bytes(value)
            mutated = True
        return read(path, ledger, expected_size, expected_digest)

    monkeypatch.setattr(host, "_read_window", mutate)
    with pytest.raises(macho_link.LinkError, match="input (content|size) changed"):
        host.prepare_executable_from_paths(paths, compact_budget=_budget())
    assert mutated


def test_each_packed_owner_retires_before_next_window(tmp_path, monkeypatch):
    import weakref
    from pcc.backend import macho_host_prepare as host

    paths = _write_inputs(tmp_path, count=5, relocations=64)
    owners, reads = [], []
    construct, read = native.PackedNativeObject.__init__, host._read_window

    def tracked(self, *args, **kwargs):
        construct(self, *args, **kwargs)
        owners.append(weakref.ref(self))

    def checked(*args, **kwargs):
        assert all(owner() is None for owner in owners)
        reads.append(args[0])
        return read(*args, **kwargs)

    monkeypatch.setattr(native.PackedNativeObject, "__init__", tracked)
    monkeypatch.setattr(host, "_read_window", checked)
    prepared = host.prepare_executable_from_paths(paths, compact_budget=_budget())
    assert len(reads) == len(paths) * 2
    assert len(owners) == len(paths) * 2
    assert all(owner() is None for owner in owners)
    assert prepared._source_sections == ()
    assert all(type(payload) is bytes for payload in prepared._payloads)
    assert all(type(rows) is bytes for rows in prepared._raw_relocations)


def _malformed_special(section_name, flags, payload):
    def name(value):
        value = value.encode("ascii")
        return native._U32.pack(len(value)) + value

    segment = "__LD" if section_name == "__compact_unwind" else "__DATA"
    return b"".join((
        native._HEADER.pack(native.MAGIC, 2, 1),
        name("_main"), native._SYMBOL.pack(1, 0, native._SYMBOL_EXTERNAL),
        name("__TEXT"), name("__text"),
        native._SECTION.pack(obj.TEXT_SECTION_FLAGS, 2, 0, 4, 0, 0),
        b"\xc0\x03\x5f\xd6",
        name(segment), name(section_name),
        native._SECTION.pack(flags, 3, 0, len(payload), 0, 0), payload,
    ))


@pytest.mark.parametrize("special", ("stackmaps", "mod-init", "unwind"))
def test_malformed_special_payload_owners_are_admitted_before_decoder(
    tmp_path, monkeypatch, special,
):
    from pcc.backend import macho_host_prepare as host

    if special == "stackmaps":
        payload = maps.encode_stack_map(maps.PreciseStackMap(
            arch=maps.ARCH_AARCH64,
            functions=tuple(maps.FunctionStackMap(
                function_id=index + 1, function_address=0, code_size=4,
                frame_size=0, records=(),
            ) for index in range(128)),
        ))
        name, flags = "__pcc_stackmaps", obj.PCC_STACKMAP_SECTION_FLAGS
    elif special == "mod-init":
        name, flags, payload = "__mod_init_func", obj.MOD_INIT_SECTION_FLAGS, b"\0" * 8192
    else:
        name, flags, payload = "__compact_unwind", obj.COMPACT_UNWIND_SECTION_FLAGS, b"\0" * 8192
    path = tmp_path / "malformed.pco"
    path.write_bytes(_malformed_special(name, flags, payload))
    budget = _budget(22_000)
    assert 2 * path.stat().st_size + 4096 < budget.limit_bytes

    def forbidden(*_args, **_kwargs):
        pytest.fail("decoder allocated malformed special-section owners before admission")

    monkeypatch.setattr(native, "decode_packed_native_object", forbidden)
    with pytest.raises(macho_link.LinkError, match="Mach-O PCO decode owners"):
        host.prepare_executable_from_paths([str(path)], compact_budget=budget)


def test_stack_function_descriptor_count_is_not_bounded_by_symbols(tmp_path):
    from pcc.backend import macho_host_prepare as host

    count = 128
    payload = maps.encode_stack_map(maps.PreciseStackMap(
        arch=maps.ARCH_AARCH64,
        functions=tuple(maps.FunctionStackMap(
            function_id=index + 1, function_address=0, code_size=4, frame_size=0, records=(),
        ) for index in range(count)),
    ))
    sections = [
        obj.Section("__text", "__TEXT", data=b"\xc0\x03\x5f\xd6",
                    align_log2=2, flags=obj.TEXT_SECTION_FLAGS,
                    symbols=(obj.TextSymbol("_main", 0),)),
        obj.Section("__pcc_stackmaps", "__DATA", data=payload, align_log2=3,
                    relocations=tuple(obj.Relocation(offset, "_main", spec.ARM64_RELOC_UNSIGNED, False, 3)
                                      for offset in maps.function_address_offsets(payload))),
    ]
    path = tmp_path / "functions.pco"
    path.write_bytes(native.encode_native_object(native.NativeObject.from_sections(sections)))
    ledger = host._Ledger(_budget())
    descriptor = host._read_descriptor(str(path), ledger)
    assert len(descriptor.symbols) == 1
    assert len(descriptor.stack_functions[1]) == count
    assert descriptor.metadata_bytes >= host._model_bytes(descriptor.stack_functions, ledger.budget)
    assert host.prepare_executable_from_paths([str(path)], compact_budget=_budget())


def test_failed_second_pass_drops_traceback_owned_windows(tmp_path, monkeypatch):
    import weakref
    from pcc.backend import macho_host_prepare as host

    paths = _write_inputs(tmp_path, count=3, relocations=256)
    owners = []
    construct = native.PackedNativeObject.__init__

    def tracked(self, *args, **kwargs):
        construct(self, *args, **kwargs)
        owners.append(weakref.ref(self))

    def broken(*_args, **_kwargs):
        raise native.NativeObjectError("deliberate row failure")

    monkeypatch.setattr(native.PackedNativeObject, "__init__", tracked)
    monkeypatch.setattr(host, "_pack_final_link_rows", broken)
    with pytest.raises(macho_link.LinkError, match="deliberate row failure") as error:
        host.prepare_executable_from_paths(paths, compact_budget=_budget())
    assert all(owner() is None for owner in owners)
    pending, seen = [error.value], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        trace = current.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename.endswith("macho_host_prepare.py"):
                assert not any(isinstance(value, (bytes, bytearray, memoryview)) and len(value) > 1024
                               for value in trace.tb_frame.f_locals.values())
            trace = trace.tb_next
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)


def test_detached_sections_retire_before_next_second_pass_read(tmp_path, monkeypatch):
    import weakref
    from pcc.backend import macho_host_prepare as host

    paths = _write_inputs(tmp_path, count=4, relocations=16)
    original_section = native.PackedNativeSection
    original_descriptor, original_read = host._read_descriptor, host._read_window
    groups = []
    second_pass = 0

    class TrackedSection(original_section):
        pass

    def descriptor(*args):
        result = original_descriptor(*args)
        groups.append([weakref.ref(section) for section in result.sections])
        return result

    def read(path, ledger, expected_size=None, expected_digest=None):
        nonlocal second_pass
        if expected_digest is not None:
            for group in groups[:second_pass]:
                assert all(section() is None for section in group)
            second_pass += 1
        return original_read(path, ledger, expected_size, expected_digest)

    monkeypatch.setattr(native, "PackedNativeSection", TrackedSection)
    monkeypatch.setattr(host, "_read_descriptor", descriptor)
    monkeypatch.setattr(host, "_read_window", read)
    host.prepare_executable_from_paths(paths, compact_budget=_budget())
    assert second_pass == len(paths)
    assert all(section() is None for group in groups for section in group)


@pytest.mark.parametrize("phase", ("Mach-O compact relocation join", "Mach-O final section metadata"))
def test_output_failure_releases_owned_rows_and_payloads(tmp_path, monkeypatch, phase):
    from pcc.backend import macho_host_prepare as host

    paths = _write_inputs(tmp_path, count=4, relocations=512)
    check = host._Ledger.check

    def reject(self, extra=0, *, label="Mach-O input window"):
        if label == phase:
            raise maps.PreciseStackMapError("deliberate output failure")
        return check(self, extra, label=label)

    monkeypatch.setattr(host._Ledger, "check", reject)
    with pytest.raises(macho_link.LinkError, match="deliberate output failure") as caught:
        host.prepare_executable_from_paths(paths, compact_budget=_budget())
    seen = set()

    def no_large_buffers(value):
        if id(value) in seen:
            return
        seen.add(id(value))
        if type(value) in (bytes, bytearray, memoryview):
            assert len(value) <= 1024
        elif type(value) in (list, tuple, set, frozenset):
            for item in value:
                no_large_buffers(item)
        elif type(value) is dict:
            for key, item in value.items():
                no_large_buffers(key)
                no_large_buffers(item)
        elif isinstance(value, (obj.Section, macho_link._MergedSection)):
            no_large_buffers(value.data)
            if isinstance(value, macho_link._MergedSection):
                no_large_buffers(value.data_parts)

    pending = [caught.value]
    while pending:
        current = pending.pop()
        trace = current.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename.endswith("macho_host_prepare.py"):
                no_large_buffers(trace.tb_frame.f_locals)
            trace = trace.tb_next
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
