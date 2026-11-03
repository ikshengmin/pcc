"""The owned runtime heals real forwarding edges across an idle transition."""
import os
from pathlib import Path
import subprocess

import pytest

PROGRAM = '''import gc
from pcc.extern import c_obj, c_int64, extern
prepare = extern("prepare_moving_readers", (c_obj,), c_obj)
roundtrip = extern("moving_read_roundtrip", (), c_int64)
install = extern("install_from_hash_callback", (), c_int64)
healed = extern("hash_reader_healed", (), c_int64)
retire = extern("retire_hash_forwarding", (), c_int64)
calls = [0]
class Trigger:
    def __hash__(self):
        calls[0] += 1
        assert install() == 0
        return 13
def main():
    gc.disable()
    trigger = Trigger()
    outer = prepare(trigger)
    assert roundtrip() == 1
    hash(outer)
    assert calls == [1]
    assert healed() == 1
    assert retire() == 1
    print("MOVING_READ_BARRIER_OK")
main()
'''


@pytest.fixture(scope='module')
def moving_read_binary(tmp_path_factory, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from pcc.runtime.py import py_abi_constants as abi
    from tests.python.test_owned_external_tls import _c_ir
    root = tmp_path_factory.mktemp('moving_read_barrier')
    fixture = Path(__file__).with_name('fixtures') / 'gc_moving_read_barrier.c'
    helper = root / 'moving.o'
    target = host_target_triple()
    text = fixture.read_text().replace('@TUPLE_ITEMS@', str(abi.PYTUPLEOBJECT_ITEMS_OFFSET))
    helper.write_bytes(emit_owned_object(_c_ir(text, target), target))
    source = root / 'moving.py'
    source.write_text(PROGRAM)
    binary = root / 'moving'
    compile_python(str(source), str(binary), backend='self', runtime_archive=str(pcc_runtime_archive),
                   object_inputs=(str(helper),))
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group('moving_read_barrier')
@pytest.mark.parametrize('backend', range(5))
def test_idle_to_forwarded_to_retired_native(moving_read_binary, backend):
    result = subprocess.run([str(moving_read_binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend), PCC_GC4_TRACING_ONLY='0', PCC_GC_TRACING_ONLY='0', PCC_GC_MINOR_ALLOC_MAX='1024'))
    assert (result.returncode, result.stdout, result.stderr) == (0, 'MOVING_READ_BARRIER_OK\n', ''), (backend, result)
