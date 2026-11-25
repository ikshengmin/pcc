"""Compact receipt wire preserves every scalar and enforces its byte budget."""
import pytest

from pcc.backend import self_backend_indexed_codec as codec
from pcc.backend.self_backend_parse import parse_self_backend_module


def test_compact_receipt_roundtrip_and_boundary(tmp_path):
    module = parse_self_backend_module(
        'target triple = "x86_64-unknown-linux-gnu"\n'
        'define i64 @example(i64 %arg) {\nentry:\n'
        ' %sum = add i64 %arg, 9223372036854775807\n ret i64 %sum\n}\n'
    )
    for f in module.functions:
        codec.get_indexed_function_kernel(f)
    raw = tmp_path / 'raw.pidx'
    compact = tmp_path / 'compact.pidx'
    codec.encode_indexed_module_file(str(raw), module)
    codec.encode_indexed_module_file(str(compact), module, max_bytes=raw.stat().st_size, compact=True)
    assert compact.stat().st_size < raw.stat().st_size
    decoded = codec.decode_indexed_module_file(str(compact), max_bytes=compact.stat().st_size)
    reconstructed = tmp_path / 'reconstructed.pidx'
    codec.encode_indexed_module_file(str(reconstructed), decoded)
    assert reconstructed.read_bytes() == raw.read_bytes()
    with pytest.raises(ValueError, match="size limit"):
        codec.encode_indexed_module_file(str(tmp_path / 'reject.pidx'), module,
                                         max_bytes=compact.stat().st_size - 1, compact=True)
    with pytest.raises(ValueError, match="size limit"):
        codec.decode_indexed_module_file(str(compact), max_bytes=compact.stat().st_size - 1)


def test_signed_arena_codec_boundaries():
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    values = [-(1<<63), -2147483648, -65, -64, -1, 0, 63, 64, 2147483647, (1<<63)-1]
    a = CompilerIntArena(len(values))
    for value in values:
        a.append(value)
    raw = a.pack_sleb_bytes()
    assert len(raw) == a.sleb_size()
    b = CompilerIntArena(len(values))
    b.read_sleb_bytes(raw, len(values))
    assert b.diagnostic_values() == values
    for bad, count in [(b"\x80", 1), (b"\x80\x00", 1), (b"\x00\x00", 1)]:
        with pytest.raises(ValueError):
            CompilerIntArena(count).read_sleb_bytes(bad, count)


@pytest.mark.integration
def test_native_compact_arena_signed_roundtrip(tmp_path, python_program_compiler, pcc_runtime_archive):
    import subprocess
    from pathlib import Path
    from pcc.backend import self_backend_value_arena as arena_module

    source = tmp_path / "arena.py"
    binary = tmp_path / "arena"
    source.write_text(Path(arena_module.__file__).read_text().split(
        "class CompilerRecordSpanArena:", 1)[0] + """
a = CompilerIntArena(8)
assert a.uses_native_storage
a.append(-9223372036854775808)
a.append(-2147483648)
a.append(-65)
a.append(-1)
a.append(0)
a.append(64)
a.append(2147483647)
a.append(9223372036854775807)
raw = a.pack_sleb_bytes()
assert len(raw) == a.sleb_size()
b = CompilerIntArena(8)
b.read_sleb_bytes(raw, 8)
i = 0
while i < 8:
    assert a.get_unchecked(i) == b.get_unchecked(i)
    i += 1
a.close()
b.close()
print("compact-ok")
""")
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    result = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout) == (0, b"compact-ok\n"), result.stderr


def test_compact_inventory_and_payload_must_match(tmp_path):
    import json
    module = parse_self_backend_module(
        'target triple = "x86_64-unknown-linux-gnu"\n'
        'define i64 @f() {\nentry:\n ret i64 1\n}\n'
    )
    for f in module.functions:
        codec.get_indexed_function_kernel(f)
    valid = tmp_path / "valid.pidx"
    codec.encode_indexed_module_file(str(valid), module, max_bytes=2097152, compact=True)
    with valid.open("rb") as stream:
        magic = stream.readline()
        size = int(stream.readline())
        header = json.loads(stream.read(size))
        payload = stream.read()
    def check(header, payload):
        header = json.dumps(header).encode()
        dest = tmp_path / "incorrect.pidx"
        dest.write_bytes(magic + str(len(header)).encode() + b"\n" + header + payload)
        with pytest.raises(ValueError):
            codec.decode_indexed_module_file(str(dest), max_bytes=2097152)
    check(header, payload[:-1])
    check(header, payload + b"\0")
    header["functions"][0]["arenas"][0] = [0, 1]
    check(header, payload)
