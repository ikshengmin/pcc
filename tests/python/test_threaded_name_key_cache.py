"""Published name keys remain alive across races, buffer reuse and collection."""
import os
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime


PROBE = r'''
#include "py_runtime.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern PyObject *pcc_cached_name_key(const char *text);
struct Worker { char *name; PyObject *key; int failed; };
static void *worker(void *raw) {
    struct Worker *state = (struct Worker *)raw;
    if (pcc_current_thread_id() <= 0) { state->failed = 1; return NULL; }
    for (int i = 0; i < 300; i++) {
        PyObject *key = pcc_cached_name_key(state->name);
        if (!key || strcmp(py_str_utf8(key), state->name) != 0) { state->failed = 2; break; }
        if (state->key && state->key != key) { state->failed = 3; break; }
        state->key = key;
        pcc_thread_safepoint();
    }
    pcc_thread_unregister_current();
    return NULL;
}
int main(int argc, char **argv) {
    if (sizeof(pthread_t) != sizeof(void *)) return 21;
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1])) != 0) return 1;
    if (pcc_current_thread_id() <= 0) return 2;
    char *names = (char *)malloc(8192);
    if (!names) return 3;
    strcpy(names, "alpha");
    strcpy(names + 32, "parallel_name");
    strcpy(names + 4096, "collision");
    PyObject *first = pcc_cached_name_key(names);
    if (!first || strcmp(py_str_utf8(first), "alpha") != 0) return 4;
    if (pcc_cached_name_key(names) != first) return 5;
    if (pcc_cached_name_key(names + 4096) != NULL) return 6;
    strcpy(names, "beta");
    if (pcc_cached_name_key(names) != NULL) return 7;
    if (strcmp(py_str_utf8(first), "alpha") != 0) return 8;
    strcpy(names, "alpha");
    if (pcc_cached_name_key(names) != first) return 9;
    pthread_t threads[4];
    struct Worker states[4];
    memset(states, 0, sizeof(states));
    for (int i = 0; i < 4; i++) {
        states[i].name = names + 32;
        if (pthread_create(&threads[i], NULL, worker, &states[i]) != 0) return 10;
    }
    int64_t entered = pcc_thread_blocking_enter(__builtin_frame_address(0));
    for (int i = 0; i < 4; i++) {
        if (pthread_join(threads[i], NULL) != 0) return 20;
    }
    pcc_thread_blocking_leave(entered);
    for (int i = 0; i < 4; i++) {
        if (states[i].failed || !states[i].key || states[i].key != states[0].key) return 11;
    }
    pcc_gc_collect(1);
    if (pcc_cached_name_key(names + 32) != states[0].key) return 12;
    if (strcmp(py_str_utf8(first), "alpha") != 0) return 13;
    /* Published entries compare this opaque address, never its old bytes. */
    free(names);
    puts("THREADED_NAME_CACHE_OK");
    return 0;
}
'''


def test_owned_c_cache_race_buffer_reuse_and_root_lifetime(tmp_path, pcc_runtime_archive):
    source = tmp_path / "name_cache.c"
    source.write_text(PROBE)
    binary = tmp_path / "name_cache"
    compile_owned_c_with_runtime(source, binary, runtime_archive=pcc_runtime_archive)
    for gc in range(5):
        result = subprocess.run([str(binary), str(gc)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PATH="/nonexistent"))
        (tmp_path / (str(gc) + ".stdout")).write_text(result.stdout)
        (tmp_path / (str(gc) + ".stderr")).write_text(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, "THREADED_NAME_CACHE_OK\n", ""), gc
