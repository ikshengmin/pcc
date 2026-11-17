"""Optional memory observations cannot affect compilation or expose inputs."""
import pytest

from pcc.frontends.python import pipeline_memory as memory


@pytest.mark.parametrize('flag', [None, '', '0', 'true'])
def test_disabled_does_not_read_counters(monkeypatch, capsys, flag):
    if flag is None:
        monkeypatch.delenv('PCC_PROFILE_COORDINATOR_MEMORY', raising=False)
    else:
        monkeypatch.setenv('PCC_PROFILE_COORDINATOR_MEMORY', flag)
    calls = []
    def counter():
        calls.append('read')
        return 1
    monkeypatch.setattr(memory, '_live_requested', counter)
    memory.coordinator_memory_phase('exports-before')
    assert calls == []
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('phase', ['exports-before', 'exports-returned', 'manifests-ready'])
def test_only_four_read_only_counters_in_order(monkeypatch, capsys, phase):
    monkeypatch.setenv('PCC_PROFILE_COORDINATOR_MEMORY', '1')
    calls = []
    for name, value in [('_live_requested', 10), ('_live_usable', 16), ('_mapped', 65536), ('_reclaimable_raw', 0)]:
        def counter(name=name, value=value):
            calls.append(name)
            return value
        monkeypatch.setattr(memory, name, counter)
    memory.coordinator_memory_phase(phase)
    assert calls == ['_live_requested', '_live_usable', '_mapped', '_reclaimable_raw']
    assert capsys.readouterr().err == ('pcc coordinator memory phase=' + phase
        + ' live_requested_bytes=10 live_usable_bytes=16 mapped_bytes=65536 reclaimable_raw_slab_bytes=0\n')


def test_untrusted_phase_is_not_logged(monkeypatch, capsys):
    monkeypatch.setenv('PCC_PROFILE_COORDINATOR_MEMORY', '1')
    memory.coordinator_memory_phase('/private/input.py secret=abcd')
    assert capsys.readouterr().err == ''


def test_host_counters_are_unavailable_not_zero(monkeypatch, capsys):
    monkeypatch.setenv('PCC_PROFILE_COORDINATOR_MEMORY', '1')
    memory.coordinator_memory_phase('exports-returned')
    assert capsys.readouterr().err == 'pcc coordinator memory phase=exports-returned status=unavailable\n'


@pytest.mark.parametrize('failure', [RuntimeError('counter'), MemoryError()])
def test_counter_failure_does_not_replace_active_error(monkeypatch, failure):
    monkeypatch.setenv('PCC_PROFILE_COORDINATOR_MEMORY', '1')
    def fail():
        raise failure
    monkeypatch.setattr(memory, '_live_requested', fail)
    original = ValueError('original')
    with pytest.raises(ValueError) as caught:
        try:
            raise original
        finally:
            memory.coordinator_memory_phase('exports-returned')
    assert caught.value is original


def test_output_failure_is_nonfatal(monkeypatch):
    monkeypatch.setenv('PCC_PROFILE_COORDINATOR_MEMORY', '1')
    class BrokenOutput:
        def write(self, text):
            raise BrokenPipeError()
        def flush(self):
            raise BrokenPipeError()
    monkeypatch.setattr(memory.sys, 'stderr', BrokenOutput())
    memory.coordinator_memory_phase('manifests-ready')


@pytest.mark.parametrize('fail_at,expected', [
    ('exports', ['exports-before']),
    ('manifest', ['exports-before', 'exports-returned']),
    ('workers', ['exports-before', 'exports-returned', 'manifests-ready']),
])
def test_pipeline_observation_order_preserves_original_failure(tmp_path, monkeypatch, fail_at, expected):
    from pcc.frontends.python import pipeline_frontend_parallel as parallel
    observations = []
    monkeypatch.setattr(parallel, 'coordinator_memory_phase', observations.append)
    original = RuntimeError('stop-' + fail_at)
    def exports(*args, **kwargs):
        if fail_at == 'exports':
            raise original
        return 'exports.json'
    def manifest(*args, **kwargs):
        if fail_at == 'manifest':
            raise original
    def workers(*args, **kwargs):
        raise original
    with pytest.raises(RuntimeError) as caught:
        parallel.compile_parallel_uncached(
            [str(tmp_path / 'entry.py')], ['entry'], jobs=1,
            entry_module='entry', sibling_inits=(), ir_scaffold_mode='on', verbose=False,
            can_spawn_worker=lambda: True, worker_command_prefix=lambda: ['pcc1'],
            chunk_count_for_workers=lambda *_args: 1, codegen_chunks=lambda *_args: [[0]],
            ast_wire_enabled=lambda: False, build_shared_exports_callback=exports,
            write_manifest=manifest, shell_quote_arg=str, worker_arg='--worker',
            worker_env_prefix=lambda: 'PCC_PY_FRONTEND_JOBS=1',
            join_strings=lambda values, sep: sep.join(values), run_worker_commands=workers,
            profiled_gc_collect=lambda *_args, **_kwargs: None,
            read_worker_ir=lambda *_args: '', profile_begin=lambda _profile: 0,
            profile_end=lambda *_args: None, profile_counter=lambda *_args: None,
            pipeline_error=RuntimeError,
        )
    assert caught.value is original
    assert observations == expected


@pytest.mark.parametrize('cancel', [KeyboardInterrupt(), SystemExit(7)])
@pytest.mark.parametrize('active_error', [False, True])
def test_cancellation_always_propagates(monkeypatch, cancel, active_error):
    monkeypatch.setenv('PCC_PROFILE_COORDINATOR_MEMORY', '1')
    def interrupt():
        raise cancel
    monkeypatch.setattr(memory, '_live_requested', interrupt)
    with pytest.raises(type(cancel)) as caught:
        if active_error:
            try:
                raise ValueError('original')
            finally:
                memory.coordinator_memory_phase('exports-returned')
        else:
            memory.coordinator_memory_phase('exports-returned')
    assert caught.value is cancel
