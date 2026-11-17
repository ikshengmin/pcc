from __future__ import annotations

import json

import pytest

from pcc.diagnostics.compile_observability import ObservabilityOptions, ObservedCompileError, observed_compile
from pcc.diagnostics import DiagnosticSpan
from pcc.frontends.python.codegen.errors import CodegenDiagnosticError


@pytest.mark.parametrize('fmt', ['text', 'json', 'sarif'])
def test_formatting_failure_preserves_original_error_and_requested_format(monkeypatch, fmt):
    import pcc.diagnostics.compile_observability as reporting
    original = RuntimeError('original compiler failure "quoted"')

    def compile_fn():
        raise original

    def broken_formatter(*args, **kwargs):
        raise AttributeError('DiagnosticSeverity has no value')

    monkeypatch.setattr(reporting, 'emit_diagnostics', broken_formatter)
    with pytest.raises(ObservedCompileError) as caught:
        observed_compile(compile_fn, options=ObservabilityOptions(diagnostic_format=fmt))
    assert caught.value.__cause__ is original
    assert 'diagnostic formatting failed: AttributeError' in caught.value.formatted
    if fmt == 'json':
        payload = json.loads(caught.value.formatted)
        assert payload['has_errors'] is True
        assert payload['diagnostics'][0]['message'] == str(original)
        assert payload['diagnostics'][0]['severity'] == 'error'
    elif fmt == 'sarif':
        payload = json.loads(caught.value.formatted)
        result = payload['runs'][0]['results'][0]
        assert result['message']['text'] == str(original)
        assert result['level'] == 'error'
    else:
        assert str(original) in caught.value.formatted


def test_diagnostic_formatter_process_control_exception_is_not_swallowed(monkeypatch):
    import pcc.diagnostics.compile_observability as reporting
    def compile_fn():
        raise ValueError('bad source')
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(reporting, 'emit_diagnostics', interrupted)
    with pytest.raises(KeyboardInterrupt):
        observed_compile(compile_fn, options=ObservabilityOptions())


@pytest.mark.parametrize('fmt', ['text', 'json', 'sarif'])
def test_emit_asm_invalid_python_reports_compiler_error_when_formatter_breaks(tmp_path, fmt):
    import subprocess
    import sys
    source = tmp_path / 'invalid.py'
    source.write_text('def broken(:\n', encoding='utf-8')
    script = '''
import sys
import pcc.diagnostics.compile_observability as reporting
from pcc.driver.cli_core import cli_main
def broken_formatter(*args, **kwargs):
    raise AttributeError('DiagnosticSeverity has no value')
reporting.emit_diagnostics = broken_formatter
raise SystemExit(cli_main(sys.argv[1:]))
'''
    run = subprocess.run(
        [sys.executable, '-c', script, str(source), '--backend', 'self',
         '--emit-asm', str(tmp_path / 'output.s'), '--diagnostic-format', fmt],
        capture_output=True, text=True, timeout=20,
    )
    assert run.returncode == 1, (run.stdout, run.stderr)
    assert 'diagnostic formatting failed: AttributeError' in run.stderr
    assert 'Traceback' not in run.stderr
    assert not (tmp_path / 'output.s').exists()
    if fmt != 'text':
        # The CLI adds its normal error prefix; the diagnostic payload remains
        # machine-readable, including the original input location.
        payload = json.loads(run.stderr[run.stderr.index('{'):])
        if fmt == 'json':
            assert payload['diagnostics'][0]['span']['file'] == str(source)
        else:
            result = payload['runs'][0]['results'][0]
            assert result['locations'][0]['physicalLocation']['artifactLocation']['uri'] == str(source)


@pytest.mark.parametrize('fmt', ['text', 'json', 'sarif'])
def test_reporting_fallback_preserves_original_diagnostic_fields(fmt):
    from pcc.diagnostics import Diagnostic
    from pcc.diagnostics.compile_observability import _format_reporting_failure
    diagnostic = Diagnostic(
        code='PCC-TEST-001', message='original message', phase='test',
        span=DiagnosticSpan('input.py', 3, 4, 5, 6),
        notes=('first note', 'second note'), metadata={'original': ['data', 42]},
        suggested_fix='repair source', fallback_reason='original reason',
        docs='docs/compiler.md',
    )
    rendered = _format_reporting_failure(diagnostic, AttributeError('value'), fmt)
    if fmt == 'text':
        assert rendered.startswith('input.py:3:4: error: PCC-TEST-001: [test] original message')
        for original in (*diagnostic.notes, diagnostic.suggested_fix,
                         diagnostic.fallback_reason, diagnostic.docs):
            assert original in rendered
    else:
        payload = json.loads(rendered)
        if fmt == 'json':
            entry = payload['diagnostics'][0]
        else:
            result = payload['runs'][0]['results'][0]
            entry = result['properties']
            assert result['locations'][0]['physicalLocation']['region'] == {
                'startLine': 3, 'startColumn': 4, 'endLine': 5, 'endColumn': 6,
            }
        expected = diagnostic.to_json()
        expected['notes'].append('diagnostic formatting failed: AttributeError')
        assert entry == expected


def test_observed_compile_writes_profile_json(tmp_path):
    profile = tmp_path / "profile.json"
    def compile_fn(src, out):
        return f"{src}->{out}"
    result = observed_compile(
        compile_fn,
        "a.py", "a.out",
        options=ObservabilityOptions(profile_json=str(profile), phase="unit"),
    )
    assert result == "a.py->a.out"
    data = json.loads(profile.read_text(encoding="utf-8"))
    assert data["schema"] == "pcc.profile.v1"
    assert data["events"][0]["name"] == "unit"


def test_observed_compile_formats_json_error():
    def compile_fn():
        raise RuntimeError("boom")
    with pytest.raises(ObservedCompileError) as caught:
        observed_compile(
            compile_fn,
            options=ObservabilityOptions(diagnostic_format="json", phase="unit"),
        )
    data = json.loads(caught.value.formatted)
    assert data["diagnostics"][0]["code"] == "PCC-PY-COMPILE-001"
    assert data["diagnostics"][0]["phase"] == "unit"


def test_observed_compile_includes_input_path_span_in_json_error(tmp_path):
    src = tmp_path / "bad.py"
    src.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    def compile_fn(path):
        raise RuntimeError("boom")

    with pytest.raises(ObservedCompileError) as caught:
        observed_compile(
            compile_fn,
            str(src),
            options=ObservabilityOptions(diagnostic_format="json", phase="unit"),
        )

    data = json.loads(caught.value.formatted)
    span = data["diagnostics"][0]["span"]
    assert span == {
        "file": str(src),
        "line": 0,
        "col": 0,
        "end_line": 0,
        "end_col": 0,
    }


def test_observed_compile_prefers_codegen_diagnostic_span():
    precise = DiagnosticSpan("pkg/codegen.py", 7, 3, 7, 12)

    def compile_fn(path):
        raise CodegenDiagnosticError(
            "unsupported expression",
            precise,
            "ScaffoldUnsupportedError",
        )

    with pytest.raises(ObservedCompileError) as caught:
        observed_compile(
            compile_fn,
            "fallback.py",
            options=ObservabilityOptions(diagnostic_format="json", phase="codegen"),
        )

    diagnostic = json.loads(caught.value.formatted)["diagnostics"][0]
    assert diagnostic["span"] == precise.to_json()
    assert "exception_type=ScaffoldUnsupportedError" in diagnostic["notes"]
