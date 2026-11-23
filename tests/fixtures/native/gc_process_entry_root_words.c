/* Simulate one or two startup frames with deterministic machine words.
 * Set up capture before constructing the application frame, as a startup
 * constructor can register the process thread before main is called. */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "py_runtime.h"

extern int64_t pcc_thread_snapshot_stopped_world_owner(void);
extern int64_t pcc_thread_visit_conservative_callback_words(
    void *visitor, void *context, void *caller);
extern void pcc_thread_enable_conservative_roots(void);
extern int64_t pcc_process_call_main(
    void *entry, int64_t argc, void *argv, void *envp, void *apple);

#define STARTUP_OUTER 0x7316288ab5ca015bULL
#define STARTUP_INNER 0x3844dbb9ba73832dULL
#define APPLICATION_ROOT 0x2bb30e98e2c2613fULL
#define WORKER_CALLER 0x58f1016ba16642edULL

static void visit_word(void *word, void *context) {
    uint64_t value = (uintptr_t)word;
    unsigned *mask = context;
    if (value == STARTUP_OUTER) *mask |= 1;
    if (value == STARTUP_INNER) *mask |= 2;
    if (value == APPLICATION_ROOT) *mask |= 4;
    if (value == WORKER_CALLER) *mask |= 8;
}

static int measure(void *caller) {
    unsigned snapshot = 0, refreshed = 0;
    if (pcc_stop_the_world() != 0) return 10;
    if (pcc_thread_snapshot_stopped_world_owner() != 1) return 11;
    if (pcc_thread_visit_conservative_words((void *)visit_word, &snapshot) < 0)
        return 12;
    if (pcc_thread_visit_conservative_callback_words(
            (void *)visit_word, &refreshed, caller) < 0) return 13;
    if (pcc_resume_world() != 0) return 14;
    printf("snapshot=%u callback=%u\n", snapshot, refreshed);
    return 0;
}

static void *worker_stop(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0) return (void *)(uintptr_t)23;
    int status = measure(NULL);
    pcc_thread_unregister_current();
    return (void *)(uintptr_t)status;
}

static int run_raw_thread(void *(*entry)(void *)) {
    pthread_t thread;
    void *result = NULL;
    if (pthread_create(&thread, NULL, entry, NULL)) return 19;
    /* This publishes our snapshot before the raw join. The worker may then
       own a stop and visit this thread's frozen words as peer roots. */
    int64_t blocking = pcc_thread_blocking_enter(__builtin_frame_address(0));
    int joined = pthread_join(thread, &result);
    pcc_thread_blocking_leave(blocking);
    return joined == 0 ? (int)(uintptr_t)result : 22;
}

__attribute__((noinline))
static int application(int argc, void *argv, void *envp, void *apple) {
    (void)argv; (void)envp; (void)apple;
    volatile uint64_t live[2] = {APPLICATION_ROOT, 0};
    int status = argc == 1 ? run_raw_thread(worker_stop) : measure((void *)live);
    return live[0] == APPLICATION_ROOT ? status : 15;
}

__attribute__((noinline))
static int startup_inner(void) {
    volatile uint64_t residue[2] = {STARTUP_INNER, 0};
    int status = (int)pcc_process_call_main((void *)application, 0, NULL, NULL, NULL);
    return residue[0] == STARTUP_INNER ? status : 16;
}

__attribute__((noinline))
static int worker_caller(void) {
    volatile uint64_t live[2] = {WORKER_CALLER, 0};
    if (pcc_current_thread_id() <= 0) return 21;
    int status = measure((void *)live);
    pcc_thread_unregister_current();
    return live[0] == WORKER_CALLER ? status : 17;
}

static void *worker_entry(void *unused) {
    (void)unused;
    return (void *)(uintptr_t)worker_caller();
}

int main(int argc, char **argv, char **envp) {
    if (!argv || !envp || !envp[0]) return 24;
    if (pcc_current_thread_id() <= 0) return 18;
    pcc_thread_enable_conservative_roots();
    if (argc == 3) return run_raw_thread(worker_entry);
    int peer = argc == 2 && strcmp(argv[1], "peer") == 0;
    volatile uint64_t residue[2] = {STARTUP_OUTER, 0};
    int status = argc == 2 && !peer ? startup_inner() :
        (int)pcc_process_call_main((void *)application, peer, NULL, NULL, NULL);
    return residue[0] == STARTUP_OUTER ? status : 20;
}
