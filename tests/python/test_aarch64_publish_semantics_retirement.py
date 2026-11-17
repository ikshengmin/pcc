"""Private publication lifetime; real passes and assembler output, not a speed test."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from pcc.backend import self_backend_aarch64_darwin as b
from pcc.backend import self_backend_aarch64_darwin_mem as producer
from pcc.backend import indexed_phase_timing as timing
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.self_backend_aarch64_semantics import AArch64InstructionSemantics
from pcc.backend.self_backend_aarch64_fragments import EMISSION_DIRECTIVE_TEXT_SECTION


def populate(sink):
    sink.append_code_directive(EMISSION_DIRECTIVE_TEXT_SECTION)
    sink.append_label('_f')
    sink.append(producer.emitted_movewide_instruction_line('movz', 'x0', 7))
    sink.append(producer.emitted_fixed_instruction_line('ret'))
    sink.release_captured_function()


@contextmanager
def fixture(owned=True):
    producer.begin_direct_instruction_capture(capture_semantics=True)
    sink = None
    try:
        sink = b._NativeAArch64Emission(optimize=True, owned_stack_windows=owned)
        populate(sink)
        yield sink
    finally:
        if sink is not None:
            sink.close()
        producer.end_direct_instruction_capture()


def observe_publication(monkeypatch, sink, states, error=None):
    original = b._NativeAArch64Emission.publish_fragment
    def publish(self, span):
        if self is sink and not self.collecting_optimized:
            states.append(self.fragments.semantics.closed)
            if error is not None:
                raise error
        return original(self, span)
    monkeypatch.setattr(b._NativeAArch64Emission, 'publish_fragment', publish)


@pytest.mark.parametrize('owned', [False, True])
def test_real_finalize_publication_lifetime_and_independent_object(monkeypatch, owned):
    expected = assemble_file('.section __TEXT,__text,regular,pure_instructions\n_f:\n  movz x0, #7\n  ret\n')
    with fixture(owned) as sink:
        states = []
        observe_publication(monkeypatch, sink, states)
        sink.finish_optimized_instructions(set(), [])
        assert states == [owned], 'publication lifetime oracle'
        assert sink.fragments.closed
        sink.builder.finalize_text_layout()
        assert sink.builder.finish() == expected, 'assembled object oracle'


@pytest.mark.parametrize('mutate', [False, True])
def test_real_publish_begin_observes_open_owner_then_custom_timer_fallback(monkeypatch, mutate):
    with fixture() as sink:
        seen = []
        timer = timing.ModulePhaseTiming('publish-lifetime')
        if not mutate:
            class CustomTimer(timing.ModulePhaseTiming):
                pass
            timer = CustomTimer('publish-lifetime')
        def event(*args):
            if args[1] == 'typed_publish' and args[3] == 'begin':
                seen.append(sink.fragments.semantics.closed)
                if mutate:
                    timer.start = lambda: 0
        monkeypatch.setattr(timing, '_write_event', event)
        states = []
        observe_publication(monkeypatch, sink, states)
        sink.finish_optimized_instructions(set(), [], phase_timing=timer)
        assert seen == [False], 'begin precedes retirement oracle'
        assert states == [False], 'postbegin fallback oracle'


def test_integer_subclass_finalizer_keeps_old_lifetime(monkeypatch):
    finalized = []
    class Payload(int):
        def __del__(self):
            finalized.append('released')
    with fixture() as sink:
        # A real token field remains valid; no local reference to this payload.
        values = sink.fragments.semantics.tokens._values
        values[0] = Payload(values[0])
        assert not b._owned_publish_semantics_can_retire(sink, None)
        states = []
        original = b._NativeAArch64Emission.publish_fragment
        def publish(self, span):
            if self is sink and not self.collecting_optimized:
                states.append((self.fragments.semantics.closed, list(finalized)))
            return original(self, span)
        monkeypatch.setattr(b._NativeAArch64Emission, 'publish_fragment', publish)
        sink.finish_optimized_instructions(set(), [])
        assert states == [(False, [])], 'payload fallback lifetime oracle'
        assert finalized == ['released']


@pytest.mark.parametrize('owned', [False, True])
def test_publish_failure_keeps_public_diagnostics_but_consumes_private_semantics(monkeypatch, owned):
    primary = RuntimeError('publish primary')
    with fixture(owned) as sink:
        states = []
        observe_publication(monkeypatch, sink, states, primary)
        with pytest.raises(RuntimeError) as caught:
            sink.finish_optimized_instructions(set(), [])
        assert caught.value is primary
        assert states == [owned]
        assert not sink.fragments.closed
        assert sink.fragments.semantics.closed is owned
        if not owned:
            sink.fragments.semantics.require_complete(sink.fragments.records, len(sink.fragments.symbol_names))


def test_private_driver_finally_closes_after_publication_failure(monkeypatch):
    primary = RuntimeError('driver publication primary')
    captured = []
    def active(prepared, optimize, **kwargs):
        sink = kwargs['native_sink']
        captured.append(sink)
        populate(sink)
        states = []
        observe_publication(monkeypatch, sink, states, primary)
        sink.finish_optimized_instructions(set(), [])
    monkeypatch.setattr(b, '_emit_prepared_aarch64_darwin_lines_active', active)
    with pytest.raises(RuntimeError) as caught:
        b._emit_prepared_aarch64_darwin_lines(SimpleNamespace(functions=[]), native_undefined=[])
    assert caught.value is primary
    assert len(captured) == 1
    assert captured[0].fragments.closed
    assert captured[0].fragments.semantics.closed
    assert captured[0].scratch._closed
    assert not b._AARCH64_EMISSION_ACTIVE


def test_retirement_failure_precedes_publication_and_keeps_primary(monkeypatch):
    primary = RuntimeError('retirement primary')
    with fixture() as sink:
        semantics = sink.fragments.semantics
        original = AArch64InstructionSemantics.close
        attempts = []
        def fail_once(self):
            if self is semantics and not attempts:
                attempts.append('close')
                raise primary
            return original(self)
        monkeypatch.setattr(AArch64InstructionSemantics, 'close', fail_once)
        states = []
        observe_publication(monkeypatch, sink, states)
        with pytest.raises(RuntimeError) as caught:
            sink.finish_optimized_instructions(set(), [])
        assert caught.value is primary
        assert attempts == ['close']
        assert states == [], 'failed retirement must not publish oracle'
        assert not semantics.closed
        semantics.require_complete(sink.fragments.records, len(sink.fragments.symbol_names))
    assert semantics.closed
