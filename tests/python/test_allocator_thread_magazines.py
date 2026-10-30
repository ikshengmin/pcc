"""Execute local-cache, cross-thread free and standalone-C boundaries."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_raw_magazines_preserve_remote_free_accounting_and_trim(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "raw_magazines"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/allocator_raw_magazines.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    for repeat in range(2):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="0"),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "RAW_MAGAZINES_OK\n", "",
        ), repeat


def test_object_magazines_preserve_accounting_remote_free_and_thread_exit(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "object_magazines"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/allocator_thread_magazines.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    for repeat in range(2):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(
                os.environ,
                PATH="/nonexistent",
                PCC_GC_BACKEND="0",
                # The final phase intentionally frees one cell twice and
                # checks the diagnostic count and re-credit without aborting.
                PCC_GC_REFCOUNT_PROVENANCE_PROBE="0",
            ),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "OBJECT_MAGAZINES_OK\n", "",
        ), repeat


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="the existing freestanding-libc link-map gate targets Darwin/ELF",
)
def test_standalone_c_object_allocation_does_not_import_thread_kernel(
    tmp_path, pcc_runtime_archive, monkeypatch,
):
    from pcc.driver.cli_core import cli_main

    source = tmp_path / "standalone.c"
    binary = tmp_path / "standalone"
    link_map = tmp_path / "standalone.map"
    source.write_text('''typedef unsigned long size_t;
void *malloc(size_t);
void free(void *);
void *memcpy(void *, const void *, size_t);
size_t strlen(const char *);
void *pcc_allocator_alloc_object(long long);
int main(void) {
    /* No thread registration: constructor reservation/early cleanup must
       keep working without the Python thread kernel in a standalone C image. */
    void *reserved = pcc_allocator_alloc_object(64);
    if (!reserved) return 12;
    free(reserved);
    char *p = malloc(16);
    if (!p) return 10;
    memcpy(p, "pcc-python", 11);
    int ok = strlen(p) == 10 && p[4] == 'p';
    free(p);
    return ok ? 0 : 11;
}
''')
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(pcc_runtime_archive))
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    map_option = "-Wl,-map," if sys.platform == "darwin" else "-Wl,-Map,"
    result = cli_main([
        "--backend=self", "--freestanding-libc",
        "--link-arg=" + map_option + str(link_map),
        "-o", str(binary), str(source),
    ])
    assert result == 0
    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=15,
        env=dict(os.environ, PATH="/nonexistent"),
    )
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")
    assert "freestanding_allocator.o)" in link_map.read_text()
    assert "freestanding_thread_kernel_pthread.o)" not in link_map.read_text()
