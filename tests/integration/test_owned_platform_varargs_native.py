"""Execute variadic ABI boundaries through a supplied native port compiler."""

import pytest

from tests.integration.test_owned_platform_native import compile_run, native_compiler

pytestmark = pytest.mark.integration


def test_native_c_varargs_after_named_aggregate_register_exhaustion(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r"""
        #include <stdarg.h>
        struct pair { long long a; long long b; };
        struct hfa { double a; double b; };
        struct large { long long a; long long b; long long c; };
        static int exhausted_gp(long long a, long long b, long long c,
                long long d, long long e, long long f, long long g,
                struct pair named, ...) {
            va_list ap;
            va_start(ap, named);
            long long first = va_arg(ap, long long);
            struct pair second = va_arg(ap, struct pair);
            double third = va_arg(ap, double);
            va_end(ap);
            return a+b+c+d+e+f+g == 28 && named.a == 31 && named.b == 32
                && first == 41 && second.a == 51 && second.b == 52 && third == 61.5;
        }
        static int exhausted_fp(double a, double b, double c, double d,
                double e, double f, double g, struct hfa named, ...) {
            va_list ap, saved;
            va_start(ap, named);
            va_copy(saved, ap);
            double first = va_arg(ap, double);
            long long second = va_arg(ap, long long);
            double again = va_arg(saved, double);
            long long repeated = va_arg(saved, long long);
            va_end(ap);
            va_end(saved);
            return a+b+c+d+e+f+g == 28.0 && named.a == 31.5 && named.b == 32.5
                && first == 41.5 && second == 51 && again == first && repeated == second;
        }
        static int named_indirect_on_stack(long long a, long long b, long long c,
                long long d, long long e, long long f, long long g, long long h,
                struct large named, ...) {
            va_list ap;
            va_start(ap, named);
            long long first = va_arg(ap, long long);
            struct large second = va_arg(ap, struct large);
            long long third = va_arg(ap, long long);
            va_end(ap);
            return a+b+c+d+e+f+g+h == 36 && named.a == 31 && named.b == 32 && named.c == 33
                && first == 41 && second.a == 51 && second.b == 52 && second.c == 53 && third == 61;
        }
        int main(void) {
            struct pair p = {31,32}, q = {51,52};
            struct hfa hf = {31.5,32.5};
            struct large l = {31,32,33}, m = {51,52,53};
            if (!exhausted_gp(1,2,3,4,5,6,7,p,41LL,q,61.5)) return 1;
            if (!exhausted_fp(1.,2.,3.,4.,5.,6.,7.,hf,41.5,51LL)) return 2;
            if (!named_indirect_on_stack(1,2,3,4,5,6,7,8,l,41LL,m,61LL)) return 3;
            return 0;
        }
    """, suffix=".c")


def test_native_c_mixed_vararg_spill_preserves_other_register_bank(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r"""
        #include <stdarg.h>
        struct mixed { double real; long long integer; };
        static int gp_full(long long a, long long b, long long c,
                           long long d, long long e, long long f, ...) {
            va_list ap;
            va_start(ap, f);
            struct mixed first = va_arg(ap, struct mixed);
            double second = va_arg(ap, double);
            long long third = va_arg(ap, long long);
            va_end(ap);
            return a+b+c+d+e+f == 21 && first.real == 31.5 && first.integer == 32
                && second == 41.5 && third == 51;
        }
        static int fp_full(double a, double b, double c, double d,
                           double e, double f, double g, double h, ...) {
            va_list ap;
            va_start(ap, h);
            struct mixed first = va_arg(ap, struct mixed);
            long long second = va_arg(ap, long long);
            double third = va_arg(ap, double);
            va_end(ap);
            return a+b+c+d+e+f+g+h == 36.0 && first.real == 31.5 && first.integer == 32
                && second == 41 && third == 51.5;
        }
        int main(void) {
            struct mixed m = {31.5,32};
            if (!gp_full(1,2,3,4,5,6,m,41.5,51LL)) return 4;
            if (!fp_full(1.,2.,3.,4.,5.,6.,7.,8.,m,41LL,51.5)) return 5;
            return 0;
        }
    """, suffix=".c")


def test_native_c_va_list_parameter_and_copy(native_compiler, tmp_path):
    compile_run(native_compiler, tmp_path, r"""
        #include <stdarg.h>
        struct mixed { double real; long long integer; };
        static int consume(va_list ap) {
            va_list copy;
            va_copy(copy, ap);
            struct mixed first = va_arg(ap, struct mixed);
            long long second = va_arg(ap, long long);
            double third = va_arg(ap, double);
            struct mixed repeated = va_arg(copy, struct mixed);
            long long again = va_arg(copy, long long);
            double tail = va_arg(copy, double);
            va_end(copy);
            return first.real == 1.5 && first.integer == 2 && second == 3 && third == 4.5
                && repeated.real == first.real && repeated.integer == first.integer
                && again == second && tail == third;
        }
        static int pass(int marker, ...) {
            va_list ap;
            va_start(ap, marker);
            int result = consume(ap);
            va_end(ap);
            return result;
        }
        int main(void) {
            struct mixed value = {1.5,2};
            return pass(0, value, 3LL, 4.5) ? 0 : 6;
        }
    """, suffix=".c")
