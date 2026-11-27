"""Production carrier contracts using only real outer-factory grants.
The active-body substitute supplies tiny real semantic input, not grant logic.
"""
from types import SimpleNamespace
import weakref
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from pcc.backend import self_backend_aarch64_darwin as darwin
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend import indexed_phase_timing as timing
from pcc.backend.self_backend_value_arena import CompilerInt4, CompilerIntArena, CompilerRecordSpanArena
from pcc.backend.self_backend_aarch64_fragments import EMISSION_DIRECTIVE_TEXT_SECTION
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_owned_stack as stack
from tests.python import test_aarch64_w_self_move as wself


def populate(sink, items):
    for op, tokens in items:
        line = o._line(op, tokens, [])
        word = int.from_bytes(o.enc.assemble_text(line).code, 'little')
        family = o.enc.EMITTED_INSTRUCTION_MOVE if op == 'mov' else o.enc.EMITTED_INSTRUCTION_SCALAR
        sem.append_semantic_instruction(sink.fragments, sink.fragments.semantics, sink.module_span,
            CompilerInt4(word, family, 0, -1), sem.semantic_opcode_id(op), sem.FORM_CANONICAL, tokens)


def factory(patch, items, action, timer=None):
    retained = []
    def active(prepared, optimize, **kwargs):
        sink = kwargs['native_sink']; cap = sink._span_owner_transfer
        if timer is None:
            assert cap is not None and cap.valid and cap.owner is sink.fragments.spans, 'real factory grant oracle'
        retained.append(cap)
        populate(sink, items)
        action(sink, cap)
        return []
    patch.setattr(darwin, '_emit_prepared_aarch64_darwin_lines_active', active)
    darwin._emit_prepared_aarch64_darwin_lines(SimpleNamespace(functions=[]), native_undefined=[], phase_timing=timer)
    for cap in retained:
        if cap is not None: assert not cap.valid and cap.owner is None, 'factory final revoke oracle'


def ids(owner, span):
    cursor = CompilerIntArena()
    try:
        owner.start_cursor(span, cursor)
        return stack.read_cursor(owner, cursor)
    finally: cursor.close()


@pytest.mark.parametrize('repeat', [False, True])
def test_old_owner_snapshot_and_duplicate_occurrences(monkeypatch, repeat):
    def action(sink, cap):
        f, s, span = sink.fragments, sink.fragments.semantics, sink.module_span
        old = f.spans; cursor = CompilerIntArena()
        if repeat: f.extend_fragment(span, span)
        before = ids(old, span); count = len(f.records) // 4
        old.start_cursor(span, cursor); first = old.next_record(cursor)
        try:
            result = p._run_owned_stack(f, s, span, 1, cap)
            assert f.spans is not old and cap.owner is None and not cap.valid, 'one-shot commit oracle'
            assert ids(old, span) == before and [first] + stack.read_cursor(old, cursor) == before
            after = ids(f.spans, result)
            assert any(rid >= count for rid in after)
            assert [f.records.get4_unchecked(rid).first for rid in after] == [0xf81f83a9, 0xaa0903ea] * (2 if repeat else 1), 'fixed output word oracle'
            with pytest.raises(RuntimeError, match='^compiler record span handle is stale$'): f.spans.length(span)
            old.append(span, before[0]); assert ids(f.spans, result) == after
            f.extend_fragment(result, result); assert ids(f.spans, result) == after * 2
            s.require_complete(f.records, len(f.symbol_names))
        finally: cursor.close(); old.close()
    factory(monkeypatch, stack.items(1, 'basic'), action)


@pytest.mark.parametrize('width', ['w', 'x'])
def test_independent_live_x_memory_and_flags(monkeypatch, width):
    def action(sink, cap):
        f, s, span = sink.fragments, sink.fragments.semantics, sink.module_span
        old = f.spans
        before = [f.records.get4_unchecked(rid).first for rid in ids(old, span)]
        try:
            result = p._run_owned_stack(f, s, span, 1, cap)
            after = [f.records.get4_unchecked(rid).first for rid in ids(f.spans, result)]
            a, b = wself.execute(before), wself.execute(after)
            assert (b[0][1], b[1], b[2], b[3]) == (a[0][1], a[1], a[2], a[3]), 'independent memory flags oracle'
            assert b[0][1] == (1 if width == 'w' else 0x100000001)
        finally: old.close()
    factory(monkeypatch, wself.sequence(1, width), action)


@pytest.mark.parametrize('fault', ['build', 'scratch_close', 'double_cleanup'])
def test_failures_preserve_owner_and_primary(monkeypatch, fault):
    def action(sink, cap):
        f, s, span = sink.fragments, sink.fragments.semantics, sink.module_span
        old = f.spans; before = ids(old, span); new = []; scratch = []
        primary = MemoryError('transfer primary'); secondary = RuntimeError('cleanup secondary')
        append = CompilerRecordSpanArena.append_ids; close = CompilerRecordSpanArena.close; passclose = p._Pass.close
        # The new owner's root is built from the output IDs as one run.
        def failappend(owner, handle, output, *checked):
            if owner is not old:
                if owner not in new: new.append(owner)
                if fault != 'scratch_close': raise primary
            return append(owner, handle, output, *checked)
        def failclose(pass_):
            scratch.extend((pass_.ids, pass_.output)); passclose(pass_)
            if fault == 'scratch_close': raise primary
        def cleanup(owner):
            close(owner)
            if owner is not old and fault == 'double_cleanup': raise secondary
        with monkeypatch.context() as patch:
            patch.setattr(CompilerRecordSpanArena, 'append_ids', failappend)
            patch.setattr(CompilerRecordSpanArena, 'close', cleanup)
            patch.setattr(p._Pass, 'close', failclose)
            with pytest.raises(MemoryError) as caught: p._run_owned_stack(f, s, span, 1, cap)
        assert caught.value is primary, 'failure primary oracle'
        assert f.spans is old and ids(old, span) == before and not old.closed, 'failed transfer old-root oracle'
        assert cap.owner is None and not cap.valid
        assert new and all(owner.closed for owner in new)
        assert scratch and all(arena._closed for arena in scratch)
        s.require_complete(f.records, len(f.symbol_names))
    factory(monkeypatch, stack.items(1, 'basic'), action)


@pytest.mark.parametrize('route', ['public', 'nochange', 'mode2', 'native_gate'])
def test_nontransfer_routes(monkeypatch, route):
    mode = 2 if route == 'mode2' else 1
    def action(sink, cap):
        f, s, span = sink.fragments, sink.fragments.semantics, sink.module_span
        old = f.spans; calls = []
        if route == 'native_gate':
            def fallback(fr, se, sp, selected): calls.append(selected); fr.records._address = 0; return sp
            monkeypatch.setattr(p, '_run', fallback); f.records._address = 1
        try:
            if route == 'public': result = p.forward_adjacent_stack_store_load(f, s, span)
            else: result = p._run_owned_stack(f, s, span, mode, cap)
            assert f.spans is old and ids(old, result), 'fallback owner oracle'
            if route != 'public': assert cap.owner is None and not cap.valid
            if route == 'native_gate': assert calls == [1]
        finally: f.records._address = 0
    factory(monkeypatch, stack.items(mode, 'offset' if route == 'nochange' else 'basic'), action)


def test_full_finish_drops_capability_strong_reference(monkeypatch):
    seen = []
    run = p._run_owned_stack
    def observe(f, s, span, mode, transfer=None):
        ref = weakref.ref(f.spans)
        result = run(f, s, span, mode, transfer)
        if mode == 1:
            assert transfer is not None and transfer.owner is None
            assert ref() is None, 'retired owner must not remain strongly retained'
            seen.append(mode)
        return result
    monkeypatch.setattr(p, '_run_owned_stack', observe)
    def action(sink, cap):
        # Directive must precede text. Existing pair has no labels, so adding
        # section now is too late; start a new empty span and prepend it.
        previous = sink.module_span
        sink.module_span = sink.fragments.new_fragment()
        sink.append_code_directive(EMISSION_DIRECTIVE_TEXT_SECTION)
        sink.fragments.extend_fragment(sink.module_span, previous)
        sink.finish_optimized_instructions([], [])
    factory(monkeypatch, stack.items(1, 'basic'), action)
    assert seen == [1], 'full finish actual transfer oracle'


def test_public_sink_constructor_does_not_grant(monkeypatch):
    def action(sink, cap):
        public = darwin._NativeAArch64Emission(optimize=True, owned_stack_windows=True)
        try: assert public._span_owner_transfer is None, 'public constructor no grant oracle'
        finally: public.close()
    factory(monkeypatch, [], action)


@pytest.mark.parametrize('phase', ['typed_validate', 'typed_peephole_validate', 'typed_forward_adjacent_stack_store_load'])
def test_postbegin_timer_change_permanently_revokes(monkeypatch, phase):
    timer = timing.ModulePhaseTiming('span-transfer'); seen = []
    def action(sink, cap):
        assert cap is not None and cap.valid
        original = timer.start
        def event(*args):
            if args[1] == phase and args[3] == 'begin': timer.start = lambda: 0
        monkeypatch.setattr(timing, '_write_event', event)
        # Stop after the real 14-driver; no need to run unrelated finalizers.
        def stop(*args, **kwargs):
            seen.append((cap.valid, cap.owner)); timer.start = original
            raise RuntimeError('after fourteen')
        from pcc.backend import self_backend_aarch64_typed_cfg as cfg
        monkeypatch.setattr(cfg, 'run_aarch64_typed_cfg_passes', stop)
        old = sink.fragments.spans
        with pytest.raises(RuntimeError, match='^after fourteen$'): sink.finish_optimized_instructions([], [], timer)
        assert sink.fragments.spans is old and not cap.valid and cap.owner is None, 'permanent timer revoke oracle'
    factory(monkeypatch, stack.items(1, 'basic'), action, timer)
    assert seen == [(False, None)]


@pytest.mark.parametrize('phase', ['typed_validate', 'typed_peephole_validate'])
def test_begin_revokes_before_validation_can_restore_timer(monkeypatch, phase):
    timer = timing.ModulePhaseTiming('temporary-noncanonical')
    seen = []
    def action(sink, cap):
        original_start = timer.start
        complete = sem.AArch64InstructionSemantics.require_complete
        pending = [False]
        def event(*args):
            if args[1] == phase and args[3] == 'begin':
                timer.start = lambda: 0; pending[0] = True
        def validate(owner, *args):
            if pending[0]:
                pending[0] = False
                assert cap.owner is None and not cap.valid, 'revoke before validation restoration oracle'
                seen.append(phase)
                timer.start = original_start
            return complete(owner, *args)
        monkeypatch.setattr(timing, '_write_event', event)
        monkeypatch.setattr(sem.AArch64InstructionSemantics, 'require_complete', validate)
        from pcc.backend import self_backend_aarch64_typed_cfg as cfg
        def stop(*args, **kwargs): raise RuntimeError('after fourteen')
        monkeypatch.setattr(cfg, 'run_aarch64_typed_cfg_passes', stop)
        old = sink.fragments.spans
        with pytest.raises(RuntimeError, match='^after fourteen$'): sink.finish_optimized_instructions([], [], timer)
        assert sink.fragments.spans is old and not cap.valid and cap.owner is None
    factory(monkeypatch, stack.items(1, 'basic'), action, timer)
    assert seen == [phase]


def test_private_peephole_boundary_retires_history_keeps_old_cursor(monkeypatch):
    seen = []
    from pcc.backend import self_backend_aarch64_typed_cfg as cfg
    original = p._run_aarch64_typed_peepholes
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        f = args[0]; old = f.spans
        cursor = CompilerIntArena(); old.start_cursor(result, cursor)
        before = ids(old, result)
        # Add an independent history run; the latest checked root is unchanged.
        extra = old.new_span(); history = CompilerIntArena()
        try:
            history.extend_list(before); old.append_ids(extra, history, True)
        finally: history.close()
        seen.append((f, old, result, cursor, before))
        return result
    monkeypatch.setattr(p, '_run_aarch64_typed_peepholes', observe)
    def stop(f, s, span, *args, **kwargs):
        _, old, old_span, cursor, before = seen[0]
        try:
            assert f.spans is not old, 'boundary history must retire exactly once'
            assert ids(f.spans, span) == before
            assert f.spans.runs._length == len(before)
            assert f.spans.is_checked_run(span)
            assert ids(old, old_span) == before
            assert stack.read_cursor(old, cursor) == before
        finally: cursor.close(); old.close()
        raise RuntimeError('after boundary')
    monkeypatch.setattr(cfg, 'run_aarch64_typed_cfg_passes', stop)
    def action(sink, cap):
        previous = sink.module_span; sink.module_span = sink.fragments.new_fragment()
        sink.append_code_directive(EMISSION_DIRECTIVE_TEXT_SECTION)
        sink.fragments.extend_fragment(sink.module_span, previous)
        with pytest.raises(RuntimeError, match='^after boundary$'):
            sink.finish_optimized_instructions([], [])
    factory(monkeypatch, stack.items(1, 'basic'), action)


def test_private_boundary_public_constructor_has_no_permission(monkeypatch):
    def action(sink, cap):
        public = darwin._NativeAArch64Emission(optimize=True, owned_stack_windows=True)
        try:
            assert public._peephole_boundary_retirement is None
            assert sink._peephole_boundary_retirement.valid
        finally: public.close()
    factory(monkeypatch, [], action)


@pytest.mark.parametrize('damage', ['bad_scalar', 'allocation', 'no_history'])
def test_private_boundary_failure_and_fallback_preserve_owner(monkeypatch, damage):
    def action(sink, initial):
        f = sink.fragments; s = f.semantics
        before = f.spans
        result = p._run_owned_stack(f, s, sink.module_span, 1, initial)
        old = f.spans; current = ids(old, result)
        cap = sink._peephole_boundary_retirement
        try:
            if damage != 'no_history':
                scratch = CompilerIntArena()
                try:
                    scratch.extend_list(current); old.append_ids(old.new_span(), scratch, True)
                finally: scratch.close()
            if damage == 'bad_scalar':
                class Bad(int):
                    def __lt__(self, other): raise RuntimeError('subclass comparison')
                root = old._root(result); node = old.nodes.get3_unchecked(root.first)
                bad = Bad(current[0])
                old.runs._values[node.second] = bad
            if damage == 'allocation':
                def fail(self): raise MemoryError('new owner failed')
                with monkeypatch.context() as patch:
                    patch.setattr(CompilerRecordSpanArena, '__init__', fail)
                    with pytest.raises(MemoryError, match='new owner failed'):
                        p._retire_owned_peephole_span(f, result, cap, None)
            else:
                assert p._retire_owned_peephole_span(f, result, cap, None) == result
            assert f.spans is old and not cap.valid and cap.fragments is None
            if damage == 'bad_scalar':
                assert old.runs._values[node.second] is bad
                old.runs._values[node.second] = current[0]
            assert ids(old, result) == current
        finally:
            before.close()
    factory(monkeypatch, stack.items(1, 'basic'), action)


def test_private_boundary_timer_mutation_then_restoration_revokes(monkeypatch):
    timer = timing.ModulePhaseTiming('boundary-revoke'); observed = []
    from pcc.backend import self_backend_aarch64_typed_cfg as cfg
    def action(sink, initial):
        cap = sink._peephole_boundary_retirement
        saved = timer.start
        def event(*args):
            if args[1] == 'typed_fold_zero_store_source':
                if args[3] == 'begin': timer.start = lambda: 0
                if args[3] == 'complete': timer.start = saved
        monkeypatch.setattr(timing, '_write_event', event)
        original = p._run_aarch64_typed_peepholes
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append(args[0].spans)
            return result
        monkeypatch.setattr(p, '_run_aarch64_typed_peepholes', observe)
        def stop(f, *args, **kwargs):
            assert f.spans is observed[0] and not cap.valid and cap.fragments is None
            raise RuntimeError('after fourteen')
        monkeypatch.setattr(cfg, 'run_aarch64_typed_cfg_passes', stop)
        with pytest.raises(RuntimeError, match='^after fourteen$'):
            sink.finish_optimized_instructions([], [], timer)
    factory(monkeypatch, stack.items(1, 'basic'), action, timer)
