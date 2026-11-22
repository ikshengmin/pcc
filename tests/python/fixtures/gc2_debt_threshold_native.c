/* Controlled snapshots for the existing GC2 scheduler, not a new pacer.
 * Seed telemetry only under the real stopped world with auto GC disabled.
 */
#include "py_runtime.h"
#include <stdio.h>

extern int64_t pcc_gc_tracing_debt_threshold(void);
extern int64_t pcc_gc_tracing_budget_from_debt(void);
extern void pcc_gc_tracing_discharge_debt(int64_t);
extern int64_t pcc_gc_tracing_marked_bytes;
extern int64_t pcc_gc_debt_threshold_override;
extern int64_t pcc_gc_debt_bytes;
extern int32_t pcc_gc_pause;
extern int32_t pcc_gc_stepmul;

enum { MIB = 1024 * 1024 };

int main(void) {
    if (pcc_gc_backend() != 2) return 1;
    py_gc_disable();
    if (pcc_stop_the_world() != 0) return 2;
    pcc_gc_debt_threshold_override = 0;
    pcc_gc_tracing_marked_bytes = MIB;
    pcc_gc_pause = 150;
    if (pcc_gc_tracing_debt_threshold() != 4 * MIB) return 3;

    pcc_gc_tracing_marked_bytes = 16 * MIB;
    if (pcc_gc_tracing_debt_threshold() != 8 * MIB) return 4;
    pcc_gc_pause = 100;
    if (pcc_gc_tracing_debt_threshold() != 4 * MIB) return 5;

    pcc_gc_tracing_marked_bytes = 10240001;
    pcc_gc_pause = 250;
    if (pcc_gc_tracing_debt_threshold() != 15360001) return 6;
    pcc_gc_debt_threshold_override = 131072;
    if (pcc_gc_tracing_debt_threshold() != 131072) return 7;

    pcc_gc_stepmul = 200;
    pcc_gc_debt_bytes = 640;
    if (pcc_gc_tracing_budget_from_debt() != 20) return 8;
    pcc_gc_tracing_discharge_debt(0);
    if (pcc_gc_debt_bytes != 640) return 9;
    pcc_gc_tracing_discharge_debt(3);
    if (pcc_gc_debt_bytes != 256) return 10;
    pcc_gc_tracing_discharge_debt(1000);
    if (pcc_gc_debt_bytes != 0 || pcc_gc_tracing_budget_from_debt() != 1)
        return 11;

    if (pcc_resume_world() != 0) return 12;
    puts("GC2_STOCK_DEBT_BASELINE_OK");
    return 0;
}
