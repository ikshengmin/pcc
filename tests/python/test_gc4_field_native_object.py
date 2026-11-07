"""Native exact-object regression with modeled graph/collector dependencies.

Not a real concurrent collector, stop-admission, survival or performance test.
The helper's plan observation assumes the current x86_64 emitter keeps the
field's plan at rbp-184 at the graph-lock call; incompatible layout fails closed.
"""
import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=(
    None if sys.platform == 'linux' and platform.machine() == 'x86_64'
    else 'owned static ELF harness requires Linux x86_64'
))
def test_gc4_field_native_exact_object(tmp_path):
    from pcc.frontends.python.owned_runtime_build import (
        _build_runtime_module, runtime_build_config, runtime_ir_passes,
    )
    from pcc.frontends.python.pipeline_targets import host_target_triple
    import pcc

    repository = Path(pcc.__file__).resolve().parents[1]
    root = str(repository / 'pcc' / 'runtime')
    objects = tmp_path / 'objects'
    objects.mkdir()
    _build_runtime_module(root, 'py_obj', host_target_triple(), str(objects),
                          runtime_ir_passes(root), runtime_build_config())
    helper = Path(__file__).with_name('gc4_field_native_object_harness.py')
    subprocess.run([sys.executable, str(helper), str(objects / 'py_obj.o'),
                    str(tmp_path / 'native'), str(repository)],
                   check=True, timeout=20)
    report = json.loads((tmp_path / 'native' / 'result.json').read_text())
    assert report['case_count'] == 64
    assert report['positive_exit'] == 0
    assert report['removed_finish_exit'] == 91
    assert report['finish_without_poll_exit'] == 91
