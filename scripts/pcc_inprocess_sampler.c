/* macOS ARM64 diagnostic only. Build with host clang as a profiler; this dylib
 * is never part of pcc1's runtime, linker, or released artifacts. The sampler
 * runs on a separate thread so timer signals cannot bias PCs toward syscall
 * return sites. Samples include the main thread's PC, LR and CPU totals. */
#if !defined(__APPLE__) || !defined(__aarch64__)
#error pcc_inprocess_sampler requires macOS ARM64
#endif
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/thread_info.h>
#include <mach/arm/thread_status.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <ptrauth.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

static int sample_fd = -1;
static pthread_t sampler_thread;
static thread_act_t target_thread;
static _Atomic int stopping;
static int period_us = 2000;
static int started;

static uint64_t executable_slide(void) {
    for (uint32_t index = 0; index < _dyld_image_count(); index++) {
        const struct mach_header *header = _dyld_get_image_header(index);
        if (header != NULL && header->filetype == MH_EXECUTE)
            return (uint64_t)_dyld_get_image_vmaddr_slide(index);
    }
    return UINT64_MAX;
}

static void *sample_main(void *ignored) {
    (void)ignored;
    struct timespec wait_time = {.tv_sec = 0,
                                 .tv_nsec = (long)period_us * 1000L};
    while (!atomic_load_explicit(&stopping, memory_order_relaxed)) {
        nanosleep(&wait_time, NULL);
        thread_basic_info_data_t info;
        mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
        uint64_t record[5] = {0, 0, UINT64_MAX, 0, 0};
        kern_return_t status = thread_info(target_thread, THREAD_BASIC_INFO,
                                           (thread_info_t)&info, &count);
        if (status == KERN_SUCCESS) {
            record[2] = (uint64_t)info.run_state;
            record[3] = (uint64_t)info.user_time.seconds * 1000000 +
                        (uint64_t)info.user_time.microseconds;
            record[4] = (uint64_t)info.system_time.seconds * 1000000 +
                        (uint64_t)info.system_time.microseconds;
            if (info.run_state == TH_STATE_RUNNING &&
                thread_suspend(target_thread) == KERN_SUCCESS) {
                arm_thread_state64_t state;
                count = ARM_THREAD_STATE64_COUNT;
                status = thread_get_state(target_thread, ARM_THREAD_STATE64,
                                          (thread_state_t)&state, &count);
                if (status == KERN_SUCCESS) {
                    record[0] = (uint64_t)state.__pc;
                    record[1] = (uint64_t)ptrauth_strip(
                        (void *)state.__lr, ptrauth_key_return_address);
                }
                thread_resume(target_thread);
            }
        }
        (void)write(sample_fd, record, sizeof(record));
    }
    return NULL;
}

__attribute__((constructor)) static void start_sampling(void) {
    const char *path = getenv("PCC_THREAD_SAMPLE_FILE");
    if (path == NULL || path[0] == '\0') return;
    const char *period = getenv("PCC_THREAD_SAMPLE_US");
    if (period != NULL && period[0] != '\0') {
        char *end = NULL;
        long parsed = strtol(period, &end, 10);
        if (end != period && *end == '\0' && parsed >= 500 && parsed <= 100000)
            period_us = (int)parsed;
    }
    sample_fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (sample_fd < 0) return;
    uint64_t header[4] = {
        UINT64_C(0x5043435448524432), executable_slide(),
        (uint64_t)getpid(), (uint64_t)period_us,
    };
    if (write(sample_fd, header, sizeof(header)) != sizeof(header)) {
        close(sample_fd);
        sample_fd = -1;
        return;
    }
    target_thread = mach_thread_self();
    if (pthread_create(&sampler_thread, NULL, sample_main, NULL) == 0)
        started = 1;
}

__attribute__((destructor)) static void stop_sampling(void) {
    if (sample_fd < 0) return;
    atomic_store_explicit(&stopping, 1, memory_order_relaxed);
    if (started) pthread_join(sampler_thread, NULL);
    mach_port_deallocate(mach_task_self(), target_thread);
    close(sample_fd);
    sample_fd = -1;
}
