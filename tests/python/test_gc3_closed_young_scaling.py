"""Native n/4n/16n inputs hold roots and young population constant.

Timing is reported by the probe for repeated measurement. Correctness tests
do not infer performance from one sample or use a noisy wall-clock threshold.
"""
import os
from pathlib import Path
import re
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime


def run_scale_probe(executable, old_count):
    env = dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND='3',
               PCC_GC_MINOR_ALLOC_MAX='16')
    for name in ('LD_PRELOAD', 'LD_LIBRARY_PATH',
                 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH'):
        env.pop(name, None)
    result = subprocess.run([str(executable), str(old_count)], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, (old_count, result.stdout, result.stderr)
    assert result.stderr == ''
    match = re.fullmatch(
        r'YOUNG_SCALE old=(\d+) young=64 freed=(\d+) retained=(\d+) ns=(\d+)\n',
        result.stdout,
    )
    assert match, result.stdout
    count, freed, retained, duration_ns = map(int, match.groups())
    assert count == old_count and freed > 0 and freed + retained == 64
    return {'old': count, 'young': 64, 'freed': freed,
            'retained': retained, 'duration_ns': duration_ns}


def test_native_young_cut_has_fixed_candidates_across_old_heap_scales(
    tmp_path, pcc_runtime_archive,
):
    source = Path(__file__).with_name('fixtures') / 'gc3_closed_young_scaling.c'
    executable = tmp_path / 'young-scale'
    compile_owned_c_with_runtime(source, executable,
                                 runtime_archive=pcc_runtime_archive)
    for old_count in (1024, 4096, 16384):
        run_scale_probe(executable, old_count)
