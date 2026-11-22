/* A cooperative owned child: only sample the work between the two markers. */
#include <stdint.h>
#include <unistd.h>
#include "py_runtime.h"

static volatile uint64_t work;

int main(void) {
    char go = 0;
    if (write(1, "READY\n", 6) != 6 || read(0, &go, 1) != 1 || go != 'G') return 1;
    int64_t until = pcc_runtime_monotonic_us() + 80000;
    do {
        for (int i = 0; i < 4096; ++i) work = work * 17 + 1;
    } while (pcc_runtime_monotonic_us() < until);
    return write(1, "DONE\n", 5) == 5 ? 0 : 2;
}
