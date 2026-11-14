"""Simulated reference results through the real elided-literal consumer.

No Go compiler or PCC compilation is invoked by these regression bodies.
"""
import pytest

from tests.go import test_elided_composite_literals as elided


_DIAGNOSTICS = (
    '4:22: missing type in composite literal',
    '4:25: missing type in composite literal',
    '3:28: missing type in composite literal',
    '2:27: invalid composite literal type int',
    '2:20: syntax error: unexpected {',
)
_OLD_SCALAR = './invalid.go:2:27: invalid composite literal type int\n'
_NEW_SCALAR = './invalid.go:2:27: invalid composite literal element type int\n'


def _install_reference_results(monkeypatch, replacements):
    calls = []

    def reference_rejection(units, directory):
        index = len(calls)
        calls.append((units, directory))
        return replacements.get(
            index, (1, '# command-line-arguments\n./invalid.go:' + _DIAGNOSTICS[index] + '\n'))

    monkeypatch.setattr(elided, 'reference_rejection', reference_rejection)
    return calls


def _expected_calls(tmp_path, count):
    return [((('invalid.go', row[0]),), tmp_path / str(index))
            for index, row in enumerate(elided.INVALID[:count])]


@pytest.mark.parametrize('diagnostic', (_OLD_SCALAR, _NEW_SCALAR), ids=('go1.23.6', 'go1.27.3'))
@pytest.mark.parametrize('prefix', ('', './', '.\\'), ids=('bare', 'posix-relative', 'windows-relative'))
def test_known_scalar_wordings_keep_all_five_original_sources(monkeypatch, tmp_path, diagnostic, prefix):
    calls = _install_reference_results(
        monkeypatch, {3: (1, '# command-line-arguments\n' + prefix + diagnostic.removeprefix('./'))})
    elided.test_reference_rejects_the_same_sources_at_the_same_positions(tmp_path)
    assert calls == _expected_calls(tmp_path, 5)


@pytest.mark.parametrize('returncode,stderr', (
    pytest.param(0, _OLD_SCALAR, id='old-success-exit'),
    pytest.param(0, _NEW_SCALAR, id='new-success-exit'),
    pytest.param(2, _NEW_SCALAR, id='wrong-failure-exit'),
    pytest.param(-15, _NEW_SCALAR, id='signal-exit'),
    pytest.param(1, '', id='empty-stderr'),
    pytest.param(1, './invalid.go:2:27: undefined: int\n', id='unrelated-error'),
    pytest.param(1, _OLD_SCALAR.replace(':2:27:', ':12:27:'), id='old-wrong-line-12'),
    pytest.param(1, _NEW_SCALAR.replace(':2:27:', ':12:27:'), id='new-wrong-line-12'),
    pytest.param(1, _OLD_SCALAR.replace(':2:27:', ':2:127:'), id='old-wrong-column-127'),
    pytest.param(1, _NEW_SCALAR.replace(':2:27:', ':2:127:'), id='new-wrong-column-127'),
    pytest.param(1, _OLD_SCALAR.replace('type int', 'type string'), id='old-wrong-type'),
    pytest.param(1, _NEW_SCALAR.replace('type int', 'type string'), id='new-wrong-type'),
    pytest.param(1, _OLD_SCALAR.replace('type int', 'type int64'), id='old-type-int64'),
    pytest.param(1, _NEW_SCALAR.replace('type int', 'type int64'), id='new-type-int64'),
    pytest.param(1, _OLD_SCALAR.replace('type int', 'type'), id='old-missing-type'),
    pytest.param(1, _NEW_SCALAR.replace('type int', 'type'), id='new-missing-type'),
    pytest.param(1, _NEW_SCALAR.replace('element type', 'scalar type'), id='unknown-wording'),
    pytest.param(1, _OLD_SCALAR.rstrip('\n') + ' extra\n', id='old-trailing-text'),
    pytest.param(1, _NEW_SCALAR.rstrip('\n') + ' extra\n', id='new-trailing-text'),
    pytest.param(1, _OLD_SCALAR.replace('invalid.go', 'other.go'), id='old-wrong-file'),
    pytest.param(1, _NEW_SCALAR.replace('invalid.go', 'other.go'), id='new-wrong-file'),
    pytest.param(1, _NEW_SCALAR.replace('invalid.go', 'not-invalid.go'), id='misleading-basename'),
    pytest.param(1, _NEW_SCALAR.removeprefix('./invalid.go:'), id='missing-source-file'),
))
def test_scalar_near_misses_fail_in_the_real_consumer(monkeypatch, tmp_path, returncode, stderr):
    calls = _install_reference_results(monkeypatch, {3: (returncode, stderr)})
    with pytest.raises(AssertionError):
        elided.test_reference_rejects_the_same_sources_at_the_same_positions(tmp_path)
    assert calls == _expected_calls(tmp_path, 4)


@pytest.mark.parametrize('index', (0, 1, 2, 4))
def test_scalar_alternative_does_not_replace_other_reference_diagnostics(monkeypatch, tmp_path, index):
    calls = _install_reference_results(monkeypatch, {index: (1, _NEW_SCALAR)})
    with pytest.raises(AssertionError):
        elided.test_reference_rejects_the_same_sources_at_the_same_positions(tmp_path)
    assert calls == _expected_calls(tmp_path, index + 1)
