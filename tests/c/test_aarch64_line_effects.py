"""Host-only parity for per-line scratch scans; no native target execution."""
import ast
import inspect

import pytest

from pcc.backend import self_backend_aarch64_darwin as arm


def _reference_effect(line, reg):
    if arm._line_uses_reg_alias(line, reg):
        return 1
    if arm._line_defines_reg_alias(line, reg):
        return 2
    return 0


_LINE_GROUPS = [
    ('overwrite', ['  movz w9, #7', '  mov x9, x10', '  add x9, x10, #8']),
    ('overlap', ['  mov w9, w9', '  add w9, w9, w10', '  ldr x9, [x9]']),
    ('partial', ['  movk w9, #1, lsl #16', '  bfi x9, x10, #2, #3',
                 '  bfxil w9, w10, #2, #3']),
    ('release', ['  stlr w9, [x10]', '  stlrb w9, [x10]', '  stlrh w9, [x10]',
                 '  ldxr x9, [x10]', '  ldaxr w9, [x10]', '  ldxrb w9, [x10]',
                 '  stxr w9, x10, [x11]', '  stlxr w9, x10, [x11]',
                 '  stxrb w11, w9, [x10]', '  stlxrh w11, w9, [x10]']),
    ('stores', ['  str x9, [x10]', '  sturh w9, [x10]', '  stp x9, x10, [x11]']),
    ('addresses', ['  ldr x10, [x9], #8', '  ldp x10, x11, [x9, #16]!',
                   '  str x10, [x9]']),
    ('flags', ['  cmp x9, x10', '  cmn w9, w10', '  tst x9, x10', '  fcmp d9, d10']),
    ('branches', ['  b.eq L_x9', '  cbz w9, L_done', '  tbz x9, #0, L_done',
                  '  br x9', '  blr x9', '  ret x9', '  b.unproved x9']),
    ('comments', ['  add x10, x11, #1 ; x9', '  mov w9, #0 ; x9',
                  '  mov w9, #0 // x9', '  mov w9, #0 ; extra_w9']),
    ('whitespace', ['', ' ', '  ', '  w9', '  mov w9', '  mov  w9, w10',
                    '  mov\tw9, w10', '\tadd x9, x10, #1']),
    ('directives', ['.p2align 2', '.section __TEXT,__text', 'L_x9:', '_x9:',
                    '  .unknown w9', '  unknown w9, x10']),
    ('token_boundaries', ['  add x19, x10, #9', '  add x10, extra_w9, #1',
                         '  add x10, x9_suffix, #1', '  add x10, w９, #1']),
]


@pytest.mark.parametrize('name,lines', _LINE_GROUPS, ids=[r[0] for r in _LINE_GROUPS])
def test_per_line_effect_matches_original_use_before_definition(name, lines):
    for line in lines:
        for reg in ('w9', 'x9', 'w10', 'x10', 'd9', 'xzr', 'w９', 'not a token'):
            assert arm._line_reg_alias_effect(line, reg) == _reference_effect(line, reg)


_READERS = ['_line_uses_reg_alias', '_line_defines_reg_alias', '_line_uses_reg',
            '_line_defines_reg', '_reg_aliases', '_tokens_for_reg_scan']


def _outcome(fn, line, reg):
    try:
        return ('return', fn(line, reg))
    except Exception as exc:
        return ('raise', type(exc), str(exc))


@pytest.mark.parametrize('name', _READERS)
@pytest.mark.parametrize('raises', [False, True], ids=['callback', 'exception'])
def test_replaced_readers_keep_original_calls_and_error_order(monkeypatch, name, raises):
    original = getattr(arm, name)
    events = []
    def reader(*args):
        events.append(args)
        if raises:
            raise ValueError('reader sentinel')
        return original(*args)
    monkeypatch.setattr(arm, name, reader)
    expected = _outcome(_reference_effect, '  add x15, x14, #1', 'x9')
    original_events = list(events)
    assert original_events
    events.clear()
    assert _outcome(arm._line_reg_alias_effect, '  add x15, x14, #1', 'x9') == expected
    assert events == original_events


@pytest.mark.parametrize('argument', ['line', 'reg'])
def test_string_subclasses_use_original_reader_path(argument):
    events = []
    class Observed(str):
        def startswith(self, *args):
            events.append(('startswith', str(self), args))
            return super().startswith(*args)
        def __getitem__(self, key):
            events.append(('getitem', str(self), key))
            return super().__getitem__(key)
    line = Observed('  add x15, x14, #1') if argument == 'line' else '  add x15, x14, #1'
    reg = Observed('x9') if argument == 'reg' else 'x9'
    expected = _reference_effect(line, reg)
    expected_events = list(events)
    assert expected_events
    events.clear()
    assert arm._line_reg_alias_effect(line, reg) == expected
    assert events == expected_events


@pytest.mark.parametrize('scanner', ['_can_drop_zero_mov_after_store', '_scratch_dead_after'])
def test_boundary_callback_reader_change_is_observed_on_same_line(monkeypatch, scanner):
    events = []
    def uses(*args):
        events.append('uses')
        return True
    def defines(*args):
        raise AssertionError('definition must not be read after a use')
    def boundary(line):
        monkeypatch.setattr(arm, '_line_uses_reg_alias', uses)
        monkeypatch.setattr(arm, '_line_defines_reg_alias', defines)
        return None
    monkeypatch.setattr(arm, '_local_label_name', boundary)
    assert not getattr(arm, scanner)(['  movz x9, #7'], 0, 'x9')
    assert events == ['uses']


@pytest.mark.parametrize('line,reg', [(None, 'x9'), ('  add x15, x14, #1', None),
                                    ('  add x15, x14, #1', 9)])
def test_malformed_reader_inputs_retain_original_exception(line, reg):
    assert _outcome(arm._line_reg_alias_effect, line, reg) == _outcome(_reference_effect, line, reg)


_FOLDS = [
    ('_fold_zero_store_source', ['  movz w9, #0', '  str w9, [x10]']),
    ('_fold_mov_store_source', ['  mov w9, w11', '  str w9, [x10]']),
    ('_fold_mov_mov_chain', ['  mov w9, w11', '  mov w19, w9']),
    ('_fold_scratch_def_into_move', ['  add w9, w11, #1', '  mov w19, w9']),
]


@pytest.mark.parametrize('name,prefix', _FOLDS, ids=[r[0] for r in _FOLDS])
def test_full_transform_output_matches_original_scans(monkeypatch, name, prefix):
    candidate = arm._line_reg_alias_effect
    for tail in (['  movz x9, #5'], ['  stlr w9, [x12]'],
                 ['  movk x9, #1, lsl #16'], ['  br x8'],
                 ['  add x15, x14, #1', '  movz x9, #5']):
        lines = prefix + tail + ['  str x19, [x13]']
        before = list(lines)
        monkeypatch.setattr(arm, '_line_reg_alias_effect', _reference_effect)
        expected = getattr(arm, name)(lines)
        monkeypatch.setattr(arm, '_line_reg_alias_effect', candidate)
        assert getattr(arm, name)(lines) == expected
        assert lines == before


_IR_BODIES = [
    ('scalar', 'define i64 @f(i64 %x) {\nentry:\n %a = add i64 %x, 1\n ret i64 %a\n}\n'),
    ('stack', 'define i64 @f(i64 %x) {\nentry:\n %s = alloca i64\n store i64 0, ptr %s\n %a = load i64, ptr %s\n %b = add i64 %x, %a\n ret i64 %b\n}\n'),
    ('branch', 'define i64 @f(i64 %x) {\nentry:\n %q = icmp eq i64 %x, 0\n br i1 %q, label %yes, label %no\nyes:\n ret i64 11\nno:\n ret i64 29\n}\n'),
]


@pytest.mark.parametrize('name,body', _IR_BODIES, ids=[r[0] for r in _IR_BODIES])
def test_full_owned_pidx_to_pco_bytes_match_original_scans(tmp_path, monkeypatch, name, body):
    from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
    from pcc.backend.self_backend_indexed_emit import emit_indexed_module_file
    from pcc.backend.self_backend_parse import parse_self_backend_module
    sidecar = tmp_path / (name + '.pidx')
    ir = 'target triple = "arm64-apple-darwin"\n' + body
    encode_indexed_module_file(str(sidecar), parse_self_backend_module(ir))
    original_input = sidecar.read_bytes()
    baseline, candidate_output = tmp_path / 'baseline.pco', tmp_path / 'candidate.pco'
    candidate = arm._line_reg_alias_effect
    monkeypatch.setattr(arm, '_line_reg_alias_effect', _reference_effect)
    emit_indexed_module_file(str(sidecar), str(baseline), 'PCO', optimize=True)
    monkeypatch.setattr(arm, '_line_reg_alias_effect', candidate)
    emit_indexed_module_file(str(sidecar), str(candidate_output), 'PCO', optimize=True)
    assert candidate_output.read_bytes() == baseline.read_bytes()
    assert sidecar.read_bytes() == original_input
    assert not list(tmp_path.glob('*.tmp'))


def test_canonical_fast_path_has_one_tokenization_per_branch_and_no_cache():
    node = ast.parse(inspect.getsource(arm._line_reg_alias_effect)).body[0]
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == '_tokens_for_reg_scan']
    assert len(calls) == 2  # mutually exclusive no-separator / separator branches
    assert not any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(node))
    assert not any(isinstance(n, ast.Return) and isinstance(n.value, (ast.List, ast.Dict, ast.Tuple))
                   for n in ast.walk(node))
