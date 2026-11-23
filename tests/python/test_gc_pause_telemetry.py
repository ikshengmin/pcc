"""The collector's explicit pause count, sum, maximum and histogram agree."""
from __future__ import annotations

import subprocess
import textwrap

from tests.owned_runtime_c_fixture import link_c_harness_bounded


_PROBE = r"""
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

static int64_t histogram_count(void) {
    int64_t count = 0;
    for (int m = PCC_GC_COUNTER_PAUSE_HIST_LT_100US;
         m <= PCC_GC_COUNTER_PAUSE_HIST_GE_10MS; m++) {
        count += pcc_gc_telemetry(m);
    }
    return count;
}

int main(void) {
    py_gc_init();
    /* The counters are process-lifetime now, not reset by a test API. */
    if (pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_COUNT) != 0 ||
        pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_SUM_US) != 0 ||
        pcc_gc_telemetry(PCC_GC_COUNTER_MAX_PAUSE_US) != 0 ||
        histogram_count() != 0) return 3;

    for (int round = 0; round < 5; round++) {
        for (int i = 0; i < 2000; i++) {
            PyObject *o = pcc_gc_alloc(64, PY_TYPE_LIST, 0);
            if (o == 0) return 5;
            pcc_gc_release(o);
        }
        pcc_gc_collect(0);
    }

    int64_t count = pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_COUNT);
    int64_t sum_us = pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_SUM_US);
    int64_t max_us = pcc_gc_telemetry(PCC_GC_COUNTER_MAX_PAUSE_US);
    printf("%d\n", count == 5);
    printf("%d\n", max_us > 0 && sum_us >= max_us);
    printf("%d\n", histogram_count() == count);
    return 0;
}
"""


def test_pause_telemetry_counters_consistent(tmp_path, pcc_runtime_archive):
    src = tmp_path / "pause_probe.c"
    exe = tmp_path / "pause_probe.out"
    src.write_text(textwrap.dedent(_PROBE).lstrip(), encoding="utf-8")
    build = link_c_harness_bounded(src, exe, runtime_archive=pcc_runtime_archive)
    assert build.returncode == 0, build.stdout + build.stderr
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["1", "1", "1"]
