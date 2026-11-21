/* Link this fixture with just freestanding_exp2_window.o and an ELF entry.
 * It drives the ACTUAL controller with a deterministic clock and visitor.
 * This is a unit test of timer boundaries; it does not execute a collector.
 */
extern long pcc_exp2_window_state;
extern long pcc_exp2_window_first_bad_reason;
extern long pcc_exp2_window_stop_minor(long);
extern long pcc_exp2_window_metric(long);
static long samples[] = {11,1011,2000,7001,8000,8001,9000,9000};
static long results[] = {4,-1,0,3};
static int ticks, visits, unexpected;

long pcc_platform_monotonic_ns(void) {
    if (ticks >= 8) { unexpected=1; return 0; }
    return samples[ticks++];
}
long pcc_thread_world_stop_epoch_held(void) { return 7; }
long pcc_exp2_process_minor_held(long epoch) {
    if (epoch != 7 || visits >= 4 || ticks != 2*visits+1) return -90;
    return results[visits++];
}
long pcc_exp2_process_metric(long i) { unexpected=1; return -999; }

/* The owned linker resolves the entire TU; unrelated entrypoints are never
 * used. Unexpected visits make the test fail rather than faking success. */
int pcc_gc_backend3_closed_young_domain;
long pcc_exp2_exposure_is_revoked(void){ unexpected=1; return 0; }
long pcc_exp2_install_exposure_hook(void *p){ unexpected=1; return 0; }
long pcc_exp2_old_exact_metric(long i){ unexpected=1; return 0; }
long pcc_exp2_own_image_matches_seal(void){ unexpected=1; return 0; }
long pcc_exp2_process_enable(long a,long b){ unexpected=1; return 0; }
long pcc_exp2_process_expose(void *p,long i){ unexpected=1; return 0; }
void pcc_exp2_process_mark(void *p,void *ctx){ unexpected=1; }
long pcc_exp2_process_register_gate(void *p,void *ctx){ unexpected=1; return 0; }
long pcc_exp2_visit_stopped_old_edges(long i,void *p,void *c,void *a){ unexpected=1; return 0; }
long pcc_gc_backend(void){ unexpected=1; return 0; }
long pcc_gc_foreign_lease_count_quiescent(void){ unexpected=1; return 0; }
long pcc_platform_env_snapshot_count(void){ unexpected=1; return 0; }
void *pcc_platform_getenv(void *p){ unexpected=1; return 0; }
long pcc_resume_world(void){ unexpected=1; return 0; }
long pcc_stop_the_world(void){ unexpected=1; return 0; }
long py_program_argc(void){ unexpected=1; return 0; }
void *py_program_argv(long i){ unexpected=1; return 0; }
int py_program_mode(void){ unexpected=1; return 0; }

int main(void) {
    if (pcc_exp2_window_stop_minor(7)!=-1) return 10;
    pcc_exp2_window_state=2;
    if (pcc_exp2_window_stop_minor(0)!=-1 || pcc_exp2_window_stop_minor(8)!=-1) return 11;
    if (ticks || visits || pcc_exp2_window_metric(33)) return 12;
    pcc_exp2_window_first_bad_reason=-3;
    for (int i=0;i<3;i++) {
        if (pcc_exp2_window_stop_minor(7)!=results[i]) return 20+i;
    }
    if (pcc_exp2_window_metric(33)!=3 || pcc_exp2_window_metric(34)!=1) return 23;
    if (pcc_exp2_window_metric(3)!=6002 || pcc_exp2_window_metric(4)!=6) return 24;
    if (pcc_exp2_window_metric(31)!=-3 || pcc_exp2_window_metric(46)!=0) return 25;
    if (pcc_exp2_window_stop_minor(7)!=3) return 26;
    if (pcc_exp2_window_metric(3)!=-1 || pcc_exp2_window_metric(4)!=-1) return 27;
    if (pcc_exp2_window_metric(46)!=1 || pcc_exp2_window_metric(33)!=4 || pcc_exp2_window_metric(34)!=1) return 28;
    if (ticks!=8 || visits!=4 || unexpected!=0) return 29;
    return 0;
}
