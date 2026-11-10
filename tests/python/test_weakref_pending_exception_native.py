"""Native-only pending-TLS witness using pcc0 and the owned C/self backend.

The C helper deliberately injects a pending exception before invoking the real
weakref invalidation boundary. This has no CPython reference equivalent. These
cases do not establish pcc1 parity; source-matched runtime provenance is required.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    explicit_owned_runtime,
)


PROGRAM = '''import gc
import weakref
from pcc.extern import c_int64, c_obj, extern
preserve = extern("weakref_invalidate_preserves_pending", (c_obj,), c_int64)
tls_clear = extern("weakref_callback_tls_is_clear", (), c_int64)
gc_backend = extern("pcc_gc_backend", (), c_int64)
events = []
error_refs = []
error_events = []
class Target:
    pass
class CallbackError(Exception):
    pass
def error_disposed(ref):
    assert tls_clear() == 1
    assert ref() is None
    error_events.append("inner")
def raising_weakrefable_error(ref):
    assert tls_clear() == 1
    assert ref() is None
    error_events.append("outer")
    error = CallbackError("callback-local weakrefable error")
    error_refs.append(weakref.ref(error, error_disposed))
    raise error
def successful(ref):
    events.append(tls_clear())
    assert ref() is None
    gc.collect()
    events.append(tls_clear())
    return ["ignored callback result"]
def raising(ref):
    events.append(tls_clear())
    assert ref() is None
    gc.collect()
    events.append(tls_clear())
    raise RuntimeError("discard this weakref callback error")
def main():
    target = Target()
    ref = weakref.ref(target, successful)
    assert preserve(target) == 1
    assert ref() is None
    assert events == [1, 1]
    assert tls_clear() == 1
    events.clear()
    target = Target()
    ref = weakref.ref(target, raising)
    assert preserve(target) == 1
    assert ref() is None
    assert events == [1, 1]
    assert tls_clear() == 1
    events.clear()
    target = Target()
    ref = weakref.ref(target)
    assert preserve(target) == 1
    assert ref() is None
    assert events == []
    assert tls_clear() == 1
    target = Target()
    ref = weakref.ref(target, raising_weakrefable_error)
    assert preserve(target) == 1
    assert ref() is None
    assert tls_clear() == 1
    # GC0 disposes the callback's last error owner inside py_clear_exception,
    # reentering the weakref boundary before the outer sentinel is restored.
    if gc_backend() == 0:
        assert error_events == ["outer", "inner"]
    # Count-free collectors may retire the unowned exception later instead.
    for index in range(4):
        gc.collect()
    assert error_events == ["outer", "inner"]
    assert len(error_refs) == 1 and error_refs[0]() is None
    assert tls_clear() == 1
    print("WEAKREF_PENDING_EXCEPTION_OK")
main()
'''


@pytest.mark.integration
@pytest.mark.parametrize('provenance_probe', ('0', '2'))
def test_weakref_pending_exception_pcc0_native_five_gc(
    provenance_probe, explicit_owned_runtime, tmp_path, capfd,
):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    fixture = Path(__file__).with_name('fixtures') / 'gc_weakref_pending_exception.c'
    helper_source = fixture.read_text()
    target = host_target_triple()
    helper = tmp_path / 'weakref_pending.o'
    helper.write_bytes(emit_owned_object(_c_ir(helper_source, target), target))
    source = tmp_path / 'program.py'
    source.write_text(PROGRAM)
    binary = tmp_path / 'program.out'
    archive = explicit_owned_runtime
    receipt = {
        'status': 'COMPILING',
        'compiler_mode': 'pcc0',
        'backend': 'self',
        'reference': 'Not applicable: native-only TLS injection',
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'helper_source_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
        'runtime_archive': str(archive),
        'runtime_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'provenance_probe': provenance_probe,
        'executions': [],
    }
    receipt_path = tmp_path / 'weakref-pending-regression.json'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    try:
        compile_python(
            str(source), str(binary), backend='self',
            runtime_archive=str(archive), object_inputs=(str(helper),),
        )
    except Exception as error:
        receipt['status'] = 'COMPILE_FAILED'
        receipt['error'] = type(error).__name__ + ': ' + str(error)
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / 'compiler-wrapper.stdout').write_text(captured.out)
        (tmp_path / 'compiler-wrapper.stderr').write_text(captured.err)
    assert binary.is_file(), 'Compiler did not emit an executable'
    receipt['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    receipt['status'] = 'RUNNING'
    for backend in range(5):
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
            tmp_path, f'gc{backend}', [str(binary)], environment,
        )
        receipt['executions'].append({'gc_backend': backend, **result})
        observed = (result['returncode'], result['stdout'], result['stderr'])
        expected = (0, 'WEAKREF_PENDING_EXCEPTION_OK\n', '')
        if observed != expected:
            receipt['status'] = 'NATIVE_EXECUTION_FAILED'
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
        assert observed == expected, (backend, provenance_probe, result)
    receipt['status'] = 'PASS'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
