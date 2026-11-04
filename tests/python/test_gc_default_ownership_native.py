"""Run actual default headers, values and lifecycle without count-mode flags."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from tests.owned_c_execution import compile_owned_c_with_runtime

REPO = Path(os.environ.get('PCC_DEFAULT_SOURCE_ROOT', str(Path(__file__).resolve().parents[2])))


def default_environment(backend):
    env = dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend))
    env.pop('PCC_GC_TRACING_ONLY', None)
    env.pop('PCC_GC4_TRACING_ONLY', None)
    return env


@pytest.fixture(scope='module')
def default_header_binary(tmp_path_factory, pcc_runtime_archive):
    source = Path(__file__).with_name('fixtures') / 'gc_default_ownership_native.c'
    binary = tmp_path_factory.mktemp('default_gc_header') / 'probe'
    compile_owned_c_with_runtime(source, binary, runtime_archive=pcc_runtime_archive,
                                 cpp_args=('-I', str(REPO/'pcc/runtime/src')))
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group('default_gc_header')
@pytest.mark.parametrize('backend', range(5))
def test_default_collector_owns_expected_header_protocol(default_header_binary, backend):
    run = subprocess.run([str(default_header_binary), str(backend)], capture_output=True,
                         text=True, timeout=20, env=default_environment(backend))
    delta = int(backend == 0)
    expected = f'DEFAULT_OWNERSHIP_OK backend={backend} initial=1 retained={1+delta} incremented={1+2*delta} balanced=1 frozen=1\n'
    assert (run.returncode, run.stdout, run.stderr) == (0, expected, ''), run


@pytest.fixture(scope='module', params=['values', 'lifecycle'])
def default_lifecycle_program(request, tmp_path_factory, pcc_runtime_archive):
    import importlib.util
    from pcc.frontends.python.pipeline import compile_python
    # Reuse the existing semantic scenarios rather than replacing their checks.
    path = REPO/'tests/python/test_gc4_tracing_only.py'
    spec = importlib.util.spec_from_file_location('default_gc_scenarios', path)
    scenarios = importlib.util.module_from_spec(spec); spec.loader.exec_module(scenarios)
    program = scenarios.VALUES if request.param == 'values' else scenarios.LIFECYCLE
    if request.param == 'values':
        program += '''\ndef identity_and_exception():
    import gc
    item = [1 << 155]
    alias = item
    ident = id(item)
    gc.collect()
    try:
        raise ValueError(item[0])
    except ValueError as exc:
        print("identity-exception", alias is item, id(alias) == ident, exc.args[0] == 1 << 155)
identity_and_exception()
'''
    reference = subprocess.run([sys.executable, '-c', program], capture_output=True,
                               text=True, timeout=20)
    assert reference.returncode == 0 and reference.stderr == '', reference
    directory = tmp_path_factory.mktemp('default_gc_' + request.param)
    source = directory/'program.py'; source.write_text(program)
    binary = directory/'program'
    compile_python(str(source), str(binary), backend='self', runtime_archive=str(pcc_runtime_archive))
    return binary, reference.stdout


@pytest.mark.integration
@pytest.mark.xdist_group('default_gc_lifecycle')
@pytest.mark.parametrize('backend', range(5))
def test_default_values_identity_exceptions_and_lifecycle_match_reference(default_lifecycle_program, backend):
    binary, expected = default_lifecycle_program
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                         env=default_environment(backend))
    assert (run.returncode, run.stdout, run.stderr) == (0, expected, ''), (backend, run)
