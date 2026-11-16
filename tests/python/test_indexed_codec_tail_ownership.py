"""Private decoder publication rollback, independent explicit ownership oracle."""
import json
import traceback
import pytest
from pcc.ir import ir
from pcc.backend import self_backend_indexed_codec as codec
from pcc.backend.self_backend_value_arena import CompilerIntArena

_FIELDS = (
    'block_name_index', 'block_facts', 'call_arg_scalars', 'call_scalars',
    'instruction_facts', 'instruction_kind_ids', 'instruction_metadata',
    'instruction_record_dest_ids', 'instruction_record_scalars',
    'gep_index_scalars', 'gep_scalars', 'type_field_ids', 'type_scalars',
    'terminator_case_scalars', 'terminator_scalars', 'block_phi_facts',
    'phi_incoming_scalars', 'phi_scalars', 'error_edge_scalars',
    'error_edge_spans', 'error_landing_scalars', 'value_scalars',
    'definition_positions', 'used_value_ids', 'slot_scalars',
    'block_layout_ids', 'instruction_overflow_use_ids', 'value_name_index',
)


def _wire(tmp_path, monkeypatch):
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_CAPTURE', '1')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_EMIT', '1')
    source = ir.Module(name='private_decoder_tail')
    source.triple = 'aarch64-unknown-linux-gnu'
    for name in ('first', 'second'):
        function = ir.Function(source, ir.FunctionType(ir.VoidType(), []), name=name)
        ir.IRBuilder(function.append_basic_block('entry')).ret_void()
    module = source.direct_indexed_module()
    path = tmp_path / 'input.pidx'
    codec.encode_indexed_module_file(str(path), module)
    return module, path


def _rewrite(path, kind):
    with path.open('rb') as stream:
        magic = stream.readline(); size = int(stream.readline())
        header = json.loads(stream.read(size)); payload = stream.read()
    if kind == 'named-types':
        header['named_type_bodies'] = []
    elif kind == 'named-type-name':
        header['named_type_bodies'] = {'not-percent': 'i64'}
    elif kind == 'triple':
        header['triple'] = []
    elif kind == 'source-debug':
        header['schema'] = codec._DEBUG_SCHEMA
        header['source_debug'] = []
    elif kind == 'globals':
        header['globals'] = [None]
    else:
        raise AssertionError(kind)
    raw = json.dumps(header, sort_keys=True).encode()
    path.write_bytes(magic + str(len(raw)).encode() + b'\n' + raw + payload)


def _observe(monkeypatch):
    owners, kernels, attempts = [], [], []
    original_init = CompilerIntArena.__init__
    original_close = CompilerIntArena.close
    original_get = codec.get_indexed_function_kernel
    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs); owners.append(self)
    def close(self):
        attempts.append(self); original_close(self)
    def get(function):
        kernel = original_get(function); kernels.append(kernel); return kernel
    monkeypatch.setattr(CompilerIntArena, '__init__', init)
    monkeypatch.setattr(CompilerIntArena, 'close', close)
    monkeypatch.setattr(codec, 'get_indexed_function_kernel', get)
    return owners, kernels, attempts, original_close


@pytest.mark.parametrize('kind', ['named-types', 'named-type-name', 'triple', 'globals', 'source-debug'])
def test_real_malformed_tail_closes_all_unpublished_kernels(tmp_path, monkeypatch, kind):
    source, path = _wire(tmp_path, monkeypatch)
    _rewrite(path, kind)
    owners, kernels, attempts, real_close = _observe(monkeypatch)
    try:
        with pytest.raises(ValueError):
            codec.decode_indexed_module_file(str(path))
        assert len(kernels) == 2
        expected = [getattr(k, field) for k in kernels for field in _FIELDS]
        assert len({id(a) for a in expected}) == 56
        assert all(a._closed for a in owners)
        assert all(sum(a is observed for observed in attempts) == 1 for a in expected)
        assert all(not getattr(f.indexed_kernel, field)._closed
                   for f in source.functions for field in _FIELDS)
    finally:
        for owner in owners: real_close(owner)
        for function in source.functions: function.indexed_kernel.close_native_tables()


@pytest.mark.parametrize('failure_type', [ValueError, KeyboardInterrupt, SystemExit, MemoryError])
@pytest.mark.parametrize('close_at', [None, 0, 27, 28, 55])
def test_publication_failure_preserves_primary_and_attempts_each_owner_once(
    tmp_path, monkeypatch, failure_type, close_at,
):
    source, path = _wire(tmp_path, monkeypatch)
    owners, kernels, attempts, real_close = _observe(monkeypatch)
    primary = failure_type('module publication rejected')
    cause = ValueError('original chained reason')
    completed = []
    failed = []
    def publish(**kwargs):
        completed.extend(getattr(k, field) for k in kernels for field in _FIELDS)
        assert len(completed) == 56
        def close(arena):
            attempts.append(arena)
            if close_at is not None and arena is completed[close_at]:
                failed.append(arena)
                raise KeyboardInterrupt('secondary close failure')
            real_close(arena)
        monkeypatch.setattr(CompilerIntArena, 'close', close)
        raise primary from cause
    monkeypatch.setattr(codec, 'ParsedModule', publish)
    try:
        with pytest.raises(failure_type) as caught:
            codec.decode_indexed_module_file(str(path))
        assert caught.value is primary
        assert primary.__cause__ is cause and primary.__suppress_context__
        assert primary.__context__ is None
        frames = traceback.extract_tb(primary.__traceback__)
        assert frames[-1].name == 'publish'
        assert not any(f.name == '_close_private_decoded_kernel' for f in frames)
        assert all(sum(a is observed for observed in attempts) == 1 for a in completed)
        assert [a._closed for a in completed] == [i != close_at for i in range(56)]
        assert len(failed) == (0 if close_at is None else 1)
        assert all(not getattr(f.indexed_kernel, field)._closed
                   for f in source.functions for field in _FIELDS)
    finally:
        monkeypatch.setattr(CompilerIntArena, 'close', real_close)
        for owner in owners: real_close(owner)
        for function in source.functions: function.indexed_kernel.close_native_tables()


def test_success_transfers_owners_without_cleanup(tmp_path, monkeypatch):
    source, path = _wire(tmp_path, monkeypatch)
    owners, kernels, attempts, real_close = _observe(monkeypatch)
    restored = None
    try:
        restored = codec.decode_indexed_module_file(str(path))
        assert len(restored.functions) == len(kernels) == 2
        expected = [getattr(k, field) for k in kernels for field in _FIELDS]
        assert {id(a) for a in owners if not a._closed} == {id(a) for a in expected}
        assert all(not any(a is observed for observed in attempts) for a in expected)
        # Name-index capacities are deliberately reconstructed, not wire state.
        # Check the public complete transport rather than private spare capacity.
        roundtrip = tmp_path / 'roundtrip.pidx'
        codec.encode_indexed_module_file(str(roundtrip), restored)
        assert roundtrip.read_bytes() == path.read_bytes()
    finally:
        for owner in owners: real_close(owner)
        for function in source.functions: function.indexed_kernel.close_native_tables()
