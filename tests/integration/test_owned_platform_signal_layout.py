"""Native C header/runtime signal-record agreement, including buffer bounds."""

import pytest

from tests.integration.test_owned_platform_native import compile_run, native_compiler

pytestmark = pytest.mark.integration


def test_owned_signal_records_and_mask_bounds(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r'''
        #include <signal.h>
        #include <stddef.h>
        #include <string.h>
        struct guarded_mask { sigset_t mask; unsigned long long guard; };
        struct guarded_action { struct sigaction action; unsigned long long guard; };
        int main(void) {
            if (sizeof(sigset_t) != 128 || sizeof(struct sigaction) != 152) return 1;
            if (offsetof(struct sigaction, sa_mask) != 8) return 2;
            if (offsetof(struct sigaction, sa_flags) != 136) return 3;
            if (offsetof(struct sigaction, sa_restorer) != 144) return 4;
            struct guarded_mask value;
            memset(&value, 0x5a, sizeof(value));
            unsigned long long guard = value.guard;
            if (sigemptyset(&value.mask) != 0 || value.guard != guard) return 5;
            for (int sig = 1; sig <= 64; ++sig) {
                if (sigismember(&value.mask, sig) != 0) return 6;
                if (sigaddset(&value.mask, sig) != 0) return 7;
                if (sigismember(&value.mask, sig) != 1) return 8;
                if (sigdelset(&value.mask, sig) != 0) return 9;
            }
            if (sigaddset(&value.mask, 0) != -1 || sigaddset(&value.mask, 65) != -1) return 10;
            if (sigfillset(&value.mask) || sigismember(&value.mask, 64) != 1) return 11;
            if (value.guard != guard) return 12;
            struct guarded_action previous;
            memset(&previous, 0x5a, sizeof(previous));
            guard = previous.guard;
            if (sigaction(SIGINT, 0, &previous.action) != 0) return 13;
            if (previous.guard != guard) return 14;
            return 0;
        }
    ''', suffix=".c")
