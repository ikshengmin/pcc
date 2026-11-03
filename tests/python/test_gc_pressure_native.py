"""Python gc control (gc.disable/set_threshold) and rooted integer semantics.

Explicit gate: build a pressure archive with PCC_RUNTIME_GC_STRESS_EVERY=3,
select it through PCC_RUNTIME_ARCHIVE, and set PCC_GC_PRESSURE_NATIVE=1.
This file never silently provisions a pressure runtime from an ordinary one.
"""

import os
from pathlib import Path
import subprocess
import sys

import pytest


VALUES = '''
def live_values():
    first = (1 << 100) + 17
    keep = [first]
    for index in range(24):
        keep.append((1 << 80) + index)
        gate()
    assert keep[0] is first
    return sum(keep)
'''

PROGRAM = '''import gc
from pcc.extern import extern, c_int64, c_void
counter = extern("pcc_gc_pressure_counter", (c_int64,), c_int64)
gate = extern("pcc_gc_safepoint", (), c_void)
no_park = extern("pressure_no_park_probe", (), c_int64)
scratch = extern("pressure_private_scratch_probe", (), c_int64)
''' + VALUES + '''
def main():
    assert counter(7) == 3
    assert scratch() == 1
    value = live_values()
    assert counter(1) > 0 and counter(2) > 0
    assert no_park() == 1
    gate()
    gc.disable()
    before = counter(2)
    held = []
    for index in range(24):
        held.append([index])
    gate()
    assert counter(2) == before
    assert counter(5) > 0 and counter(4) > 0
    gc.enable()
    gate()
    assert counter(2) > before
    threshold = gc.get_threshold()[0]
    gc.set_threshold(0)
    before = counter(2)
    for index in range(24):
        held.append([index])
    gate()
    assert counter(2) == before and counter(5) > 0
    gc.set_threshold(threshold)
    gate()
    assert counter(2) > before
    assert len(held) == 48 and held[23][0] == 23
    print(value)
    print("PRESSURE_GUARDS_OK")
main()
'''


@pytest.fixture(scope='module')
def pressure_binary(tmp_path_factory, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    assert os.environ.get('PCC_RUNTIME_GC_STRESS_EVERY') == '3', 'select the explicit interval-3 pressure archive'
    directory = tmp_path_factory.mktemp('gc_pressure_native')
    reference = subprocess.run(
        [sys.executable, '-c', 'import gc\ndef gate():\n    gc.collect()\n' + VALUES + '\nprint(live_values())'],
        capture_output=True, text=True, timeout=20,
    )
    assert reference.returncode == 0 and reference.stderr == '', reference
    source = directory / 'pressure.py'
    source.write_text(PROGRAM)
    helper = directory / 'guards.o'
    fixture = Path(__file__).with_name('fixtures') / 'gc_pressure_guard.c'
    target = host_target_triple()
    helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
    binary = directory / 'pressure'
    compile_python(str(source), str(binary), backend='self',
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    return binary, reference.stdout + 'PRESSURE_GUARDS_OK\n'


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GC_PRESSURE_NATIVE')
@pytest.mark.xdist_group('gc_pressure_native')
@pytest.mark.parametrize('backend,tracing', [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1)])
def test_pressure_executes_at_legal_gates_on_every_collector(pressure_binary, backend, tracing):
    binary, expected = pressure_binary
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend),
                 PCC_GC4_TRACING_ONLY='0', PCC_GC_TRACING_ONLY=str(tracing)),
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, ''), (backend, tracing)
