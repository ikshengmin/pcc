"""The production generator factory uses the owned bulk frame constructor."""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = '''import gc
import weakref

events = []

class Marker:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append("del")

def values(marker):
    captured = marker
    def observe():
        return captured.value
    try:
        yield (marker, observe)
        marker.value += 1
        yield captured
    finally:
        events.append("closed")

def main():
    marker = Marker(41)
    watch = weakref.ref(marker)
    iterator = values(marker)
    del marker
    first = next(iterator)
    gc.collect()
    assert first[0] is watch() and first[1]() == 41
    second = next(iterator)
    assert second is first[0] and first[1]() == 42
    iterator.close()
    assert events == ["closed"]
    del first
    del second
    del iterator
    gc.collect()
    assert watch() is None and events == ["closed", "del"]
    print("BULK_FRAME_DEFAULT_OK")

main()
'''


def test_default_bulk_factory_and_explicit_legacy_control_emit_distinct_ir(tmp_path, monkeypatch):
    source = tmp_path / 'factory.py'
    source.write_text(PROGRAM)
    observed = []
    for disabled in (None, '1', '0'):
        if disabled is None:
            monkeypatch.delenv('PCC_DISABLE_BULK_GENERATOR_FRAME_INIT', raising=False)
        else:
            monkeypatch.setenv('PCC_DISABLE_BULK_GENERATOR_FRAME_INIT', disabled)
        output = tmp_path / ('factory-' + str(disabled) + '.ll')
        compile_python(str(source), str(output), backend='self', libpython_mode='off',
                       ir_scaffold_mode='on', emit_llvm_only=True)
        observed.append(len(re.findall(r'call[^\n]*@py_gen_(?:inline_)?frame_new\(', output.read_text())))
    assert observed[0] > 0 and observed[0] == observed[2]
    assert observed[1] == 0


@pytest.mark.integration
def test_default_factory_preserves_closures_aliases_and_finalization(tmp_path, pcc_runtime_archive, monkeypatch):
    monkeypatch.delenv('PCC_DISABLE_BULK_GENERATOR_FRAME_INIT', raising=False)
    source = tmp_path / 'factory.py'
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, 'BULK_FRAME_DEFAULT_OK\n', '')
    binary = tmp_path / 'factory'
    compile_python(str(source), str(binary), backend='self', libpython_mode='off',
                   ir_scaffold_mode='on', runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        environment = dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='/nonexistent')
        environment.pop('PCC_DISABLE_BULK_GENERATOR_FRAME_INIT', None)
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20, env=environment)
        (tmp_path / f'gc{backend}.stdout').write_text(run.stdout)
        (tmp_path / f'gc{backend}.stderr').write_text(run.stderr)
        assert (run.returncode, run.stdout, run.stderr) == (0, reference.stdout, ''), backend
