"""Preserve actual compiler failure text without relaxing closure execution."""
from types import SimpleNamespace
import pytest
from tests.python import test_fallback_baseline as baseline

@pytest.mark.parametrize('mode', ['off','on'])
@pytest.mark.parametrize('saved', [None,'original'])
def test_multi_error_retained_and_compile_cached(monkeypatch,mode,saved):
    calls=[];error='PyPipelineError: original failure\nTraceback: original compiler frame'
    if saved is None:
        monkeypatch.delenv('PCC_IR_SCAFFOLD',raising=False)
        monkeypatch.delenv('PCC_PYTHON_IR_PASSES',raising=False)
    else:
        monkeypatch.setenv('PCC_IR_SCAFFOLD',saved)
        monkeypatch.setenv('PCC_PYTHON_IR_PASSES',saved)
    def compile(srcs,mods):
        calls.append((list(srcs),list(mods),baseline.os.environ.get('PCC_IR_SCAFFOLD'),baseline.os.environ.get('PCC_PYTHON_IR_PASSES')))
        return False,'',error
    monkeypatch.setattr(baseline,'_load_closure_probe',lambda:SimpleNamespace(_try_full_multi_compile=compile))
    row=baseline._ClosureCompileResults(['real.py'],['real'],ir_scaffold_mode=mode)
    assert row['multi_error'] is error
    assert row['multi_ok'] is False and row['total_fallbacks'] is None
    assert len(calls)==1
    assert calls[0]==(['real.py'],['real'],'on' if mode=='on' else None,'off')
    with pytest.raises(AssertionError,match='original failure'):
        baseline.test_closure_multi_file_compile_succeeds(row)
    assert len(calls)==1
    assert baseline.os.environ.get('PCC_IR_SCAFFOLD')==saved
    assert baseline.os.environ.get('PCC_PYTHON_IR_PASSES')==saved


def test_success_does_not_recompile_or_change_assertion(monkeypatch):
    calls=[]
    def compile(srcs,mods):calls.append(1);return True,'define void @ok() {}\n',''
    monkeypatch.setattr(baseline,'_load_closure_probe',lambda:SimpleNamespace(_try_full_multi_compile=compile))
    row=baseline._ClosureCompileResults(['real.py'],['real'],ir_scaffold_mode='off')
    baseline.test_closure_multi_file_compile_succeeds(row)
    assert row['multi_error']=='' and row['ir_lines']==1 and len(calls)==1


def test_real_compiler_missing_input_failure_reaches_original_assertion(tmp_path):
    # Real production probe/compiler, no fake success, runtime build or native run.
    missing=tmp_path/'missing_actual_closure_input.py'
    row=baseline._ClosureCompileResults([str(missing)],['missing_actual_closure_input'],ir_scaffold_mode='off')
    assert row['multi_ok'] is False
    assert 'missing_actual_closure_input.py' in row['multi_error']
    assert 'Traceback' in row['multi_error']
    with pytest.raises(AssertionError,match='missing_actual_closure_input.py'):
        baseline.test_closure_multi_file_compile_succeeds(row)
    assert row['total_fallbacks'] is None


@pytest.mark.parametrize('mode',['off','on'])
def test_unexpected_probe_exception_propagates_and_restores_env(monkeypatch,mode):
    error=RuntimeError('unexpected probe failure')
    monkeypatch.setenv('PCC_IR_SCAFFOLD','before-mode')
    monkeypatch.setenv('PCC_PYTHON_IR_PASSES','before-passes')
    def compile(srcs,mods):raise error
    monkeypatch.setattr(baseline,'_load_closure_probe',lambda:SimpleNamespace(_try_full_multi_compile=compile))
    row=baseline._ClosureCompileResults(['real.py'],['real'],ir_scaffold_mode=mode)
    with pytest.raises(RuntimeError) as observed:_=row['multi_ok']
    assert observed.value is error and 'multi_ok' not in row and 'multi_error' not in row
    assert baseline.os.environ['PCC_IR_SCAFFOLD']=='before-mode'
    assert baseline.os.environ['PCC_PYTHON_IR_PASSES']=='before-passes'
