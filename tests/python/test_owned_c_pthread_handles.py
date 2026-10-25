"""The owned C header must pass full Darwin pthread handles to libSystem."""
import sys

import pytest

from tests.owned_c_execution import compile_and_run_owned_c


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin pthread ABI regression")
def test_native_pthread_handles_join_every_created_worker():
    result = compile_and_run_owned_c(r'''
#include <pthread.h>
#include <stdio.h>
struct Result { int input; int output; };
static void *worker(void *raw) {
    struct Result *result = (struct Result *)raw;
    result->output = result->input * 7;
    return raw;
}
int main(void) {
    if (sizeof(pthread_t) != sizeof(void *)) return 1;
    pthread_t threads[4];
    struct Result results[4];
    for (int i = 0; i < 4; i++) {
        results[i].input = i + 1;
        results[i].output = 0;
        if (pthread_create(&threads[i], NULL, worker, &results[i]) != 0) return 2;
    }
    for (int i = 0; i < 4; i++) {
        void *returned = NULL;
        if (pthread_join(threads[i], &returned) != 0) return 3;
        if (returned != &results[i] || results[i].output != (i + 1) * 7) return 4;
    }
    puts("PTHREAD_HANDLES_OK");
    return 0;
}
''')
    assert (result.returncode, result.stdout, result.stderr) == (0, "PTHREAD_HANDLES_OK\n", "")
