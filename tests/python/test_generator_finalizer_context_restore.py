"""Exception/finalizer contract from docs/project-intent.md.

The ordinary program has a CPython reference lane and owned pcc0/self native
lanes across all five GC selections (pytest pcc1 routing is recorded if active).
Current native GC1-GC4 selections are count-free; the provenance probe does not
select counted ownership. Joined producer threads retire their stacks before
the unchanged four collections and exact finally-once check on the main thread.
A separate GC0 timing lane requires close before the TypeError handler examines
its chain, proving the changed error-exit
shape rather than merely collecting a generator after the pending error ended.
IR passes follow the shared helper's explicit off setting; this is not default
optimization-pipeline, bootstrap, Mac, or performance qualification. A pcc0 run
does not establish native-pcc1 parity.
"""

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    explicit_owned_runtime,
)


FIXTURE = Path(__file__).with_name('fixtures') / 'generator_finalizer_context_restore.py'
EXPECTED = (0, 'GENERATOR_FINALIZER_CONTEXT_OK\n', '')


def _assert_result(result):
    assert (result['returncode'], result['stdout'], result['stderr']) == EXPECTED, result


def test_generator_finalizer_context_reference(tmp_path):
    """Reference timing is intentionally only eventual finally-once."""
    result = _record_execution(
        tmp_path, 'reference', [sys.executable, str(FIXTURE)], dict(os.environ),
    )
    _assert_result(result)


@pytest.mark.integration
@pytest.mark.parametrize('provenance_probe', ('0', '2'))
def test_generator_finalizer_context_pcc0_owned_native(
    provenance_probe, explicit_owned_runtime, tmp_path, capfd,
):
    from pcc.frontends.python.pipeline import compile_python

    program = FIXTURE.read_text()
    assert program.count('main(False)') == 1
    archive = explicit_owned_runtime
    receipt = {
        'status': 'COMPILING',
        'compiler_mode': (
            'pcc1-routed' if compile_python.__module__ == 'tests.pcc1_route' else 'pcc0'
        ),
        'compiler_entrypoint': compile_python.__module__ + '.' + compile_python.__name__,
        'backend': 'self',
        'python_ir_passes': os.environ.get('PCC_PYTHON_IR_PASSES'),
        'default_optimization_pipeline_qualification': False,
        'runtime_archive': str(archive),
        'runtime_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'provenance_probe': provenance_probe,
        'provenance_probe_selects_gc_mode': False,
        'producer_stack_boundary': (
            'worker identity differs from main; exit and join before four collections'
        ),
        'reference': None,
        'variants': [],
    }
    receipt_path = tmp_path / 'generator-finalizer-context.json'

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')

    reference = _record_execution(
        tmp_path, 'reference', [sys.executable, str(FIXTURE)], dict(os.environ),
    )
    receipt['reference'] = reference
    save()
    _assert_result(reference)

    for label, source_text, backends in (
        ('portable', program, range(5)),
        ('gc0-immediate', program.replace('main(False)', 'main(True)'), (0,)),
    ):
        source = tmp_path / (label + '.py')
        source.write_text(source_text)
        binary = tmp_path / (label + '.out')
        variant = {
            'label': label,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'status': 'COMPILING',
            'executions': [],
        }
        receipt['variants'].append(variant)
        save()
        try:
            compile_python(
                str(source), str(binary), backend='self',
                runtime_archive=str(archive),
            )
        except Exception as error:
            receipt['status'] = variant['status'] = 'COMPILE_FAILED'
            variant['error'] = type(error).__name__ + ': ' + str(error)
            save()
            raise
        finally:
            captured = capfd.readouterr()
            (tmp_path / (label + '-compiler.stdout')).write_text(captured.out)
            (tmp_path / (label + '-compiler.stderr')).write_text(captured.err)
        assert binary.is_file(), 'Compiler did not emit an executable'
        variant['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        receipt['status'] = variant['status'] = 'RUNNING'
        save()
        for backend in backends:
            environment = dict(
                os.environ,
                PCC_GC_BACKEND=str(backend),
                PCC_GC_REFCOUNT_PROVENANCE_PROBE=provenance_probe,
                PCC_TEST_NO_NATIVE_PROVISIONING='1',
                PCC_NO_AUTO_PCC1='1',
                PCC_HOST_PYTHON='/nonexistent/host-python',
                PCC_HOST_PCC='/nonexistent/host-pcc',
                PATH='',
            )
            result = _record_execution(
                tmp_path, f'{label}-gc{backend}', [str(binary)], environment,
            )
            variant['executions'].append({
                'gc_backend': backend,
                'ownership_mode': 'counted' if backend == 0 else 'count-free',
                **result,
            })
            if (result['returncode'], result['stdout'], result['stderr']) != EXPECTED:
                receipt['status'] = variant['status'] = 'NATIVE_EXECUTION_FAILED'
            save()
            _assert_result(result)
        variant['status'] = 'PASS'
        save()
    receipt['status'] = 'PASS'
    save()
