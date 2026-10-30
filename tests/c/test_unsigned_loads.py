import os
import sys

this_dir = os.path.dirname(os.path.abspath(__file__))
# tests/{c,python}/<file>.py -> repo root is two levels up. This used to
# rely on tests/conftest.py's global Path.resolve/dirname shim.
parent_dir = os.path.dirname(os.path.dirname(this_dir))
sys.path.insert(0, parent_dir)

from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
import pytest
from tests.owned_c_execution import compile_and_run_owned_c


def _evaluate(source):
    return CEvaluator().evaluate(source, optimize=False)


@pytest.mark.parametrize("expression", [
    "items[0][0]",
    "pointers[0][0]",
    "**pointers",
])
def test_unsigned_pointer_chains_execute_high_bytes(expression):
    source = '''static unsigned char *items[1];
static signed char *signed_items[1];
int read_byte(unsigned char **pointers, unsigned char *bytes) {
    return ''' + expression + ''';
}
int main(void) {
    unsigned char bytes[1] = {128};
    signed char negative[1] = {-128};
    items[0] = bytes;
    signed_items[0] = negative;
    if (read_byte(items, bytes) != 128) return 1;
    bytes[0] = 255;
    if (read_byte(items, bytes) != 255) return 2;
    if (signed_items[0][0] != -128) return 3;
    return 0;
}
'''
    result = compile_and_run_owned_c(source)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize("operation", [
    "__atomic_load_n(pointers, __ATOMIC_RELAXED)",
    "__atomic_exchange_n(pointers, replacement, __ATOMIC_SEQ_CST)",
])
def test_atomic_pointer_results_keep_pointee_type_in_ir(operation):
    # Pointer atomic emission is a separate existing backend gap. Check the
    # frontend type contract here; the ordinary pointer chains execute above.
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    from pcc.frontends.c.parse.c_parser import CParser

    source = '''int read_byte(unsigned char **pointers, unsigned char *replacement) {
        return (''' + operation + ''')[0];
    }'''
    generator = CCodeGenerator()
    generator.generate_code(CParser().parse(source))
    body = str(generator.module)
    assert "zext i8" in body
    assert "sext i8" not in body


def test_pointer_cast_changes_only_its_own_signedness_view():
    result = compile_and_run_owned_c('''int main(void) {
        unsigned char positive = 128;
        unsigned char *u = &positive;
        signed char negative = -1;
        signed char *s = &negative;
        if (((signed char *)u)[0] != -128) return 1;
        if (u[0] != 128) return 2;
        if (((unsigned char *)s)[0] != 255) return 3;
        if (s[0] != -1) return 4;
        return 0;
    }''')
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_unsigned_char_return_is_zero_extended():
    source = r"""
        typedef unsigned char lu_byte;

        lu_byte f(void) { return 200; }

        int main(void) {
            int x = f();
            return x == 200 ? 0 : x;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_char_loads_from_struct_array_and_pointer():
    source = r"""
        typedef unsigned char lu_byte;

        struct S { lu_byte x; };

        int main(void) {
            struct S s;
            lu_byte a[1];
            lu_byte *p = &s.x;
            int sx, ax, px;

            s.x = 200;
            a[0] = 201;

            sx = s.x;
            ax = a[0];
            px = *p;

            return (sx == 200 && ax == 201 && px == 200) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_cast_to_unsigned_char_pointer_keeps_unsigned_load():
    source = r"""
        #define cast(t, exp) ((t)(exp))
        typedef unsigned char lu_byte;

        int main(void) {
            unsigned long raw = 0;
            lu_byte *tag = cast(lu_byte*, &raw);
            int x;

            *tag = 200;
            x = *cast(lu_byte*, &raw);

            return x == 200 ? 0 : x;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_char_promotes_to_signed_int_for_compare():
    source = r"""
        typedef unsigned char lu_byte;

        int ge(int reg, lu_byte nvarstack) {
            return reg >= nvarstack;
        }

        int main(void) {
            return ge(-1, 1) == 0 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_int_converts_to_signed_long_when_long_can_hold_it():
    source = r"""
        int main(void) {
            unsigned int u = 1U;
            long x = -2;
            return (x < u) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_size_t_still_uses_unsigned_comparison_at_same_rank():
    source = r"""
        #include <stddef.h>

        int main(void) {
            size_t u = 1;
            long x = -2;
            return (x < u) ? 1 : 0;
        }
    """

    assert _evaluate(source) == 0


def test_constexpr_cast_to_size_t_keeps_unsigned_range_in_ternary():
    source = r"""
        #include <limits.h>
        #include <stddef.h>

        typedef union { long long a; int b; void *p; } Node;

        #define cast(t, exp) ((t)(exp))
        #define cast_int(i) cast(int, (i))
        #define cast_sizet(i) cast(size_t, (i))
        #define MAX_SIZET ((size_t)(~(size_t)0))
        #define luaM_limitN(n,t)  ((cast_sizet(n) <= MAX_SIZET/sizeof(t)) ? (n) : cast_int((MAX_SIZET/sizeof(t))))
        #define l_numbits(x) (sizeof(x) * CHAR_BIT)
        #define MAXABITS (l_numbits(int) - 1)
        #define MAXHBITS (MAXABITS - 1)
        enum {
            MAXHSIZE = luaM_limitN(1 << MAXHBITS, Node)
        };

        int main(void) {
            return (MAXHSIZE == (1 << MAXHBITS)) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_char_pointer_arithmetic_uses_positive_offset():
    source = r"""
        typedef unsigned char lu_byte;

        int main(void) {
            int a[256];
            int i;
            lu_byte idx = 200;
            int *p = a;

            for (i = 0; i < 256; i++) {
                a[i] = i;
            }

            return *(p + idx) == 200 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_multidim_unsigned_short_subscript_keeps_unsigned_load():
    source = r"""
        #include <stdint.h>

        uint16_t g[1][1] = {{65535U}};

        int main(void) {
            return g[0][0] == 65535U ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_xor_result_stays_unsigned_for_modulo():
    source = r"""
        typedef unsigned int IdxT;

        int main(void) {
            IdxT lo = 1;
            IdxT up = 1921;
            unsigned int rnd = 3426782842u;
            IdxT r4 = (up - lo) / 4;
            IdxT p = (rnd ^ lo ^ up) % (r4 * 2) + (lo + r4);

            return p == 731u ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_xor_assignment_result_stays_unsigned_for_modulo():
    source = r"""
        int main(void) {
            unsigned int x = 3426782842u;
            unsigned int mod = 960u;

            x ^= 1u;
            x ^= 1921u;

            return (x % mod) == 250u ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_prefix_increment_result_stays_unsigned_for_modulo():
    source = r"""
        int main(void) {
            unsigned int x = 3426781689u;

            return ((++x % 960) == 250u) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_prefix_decrement_result_stays_unsigned_for_modulo():
    source = r"""
        int main(void) {
            unsigned int x = 0u;

            return ((--x % 960) == 255u) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_long_long_subtraction_wraps_without_nuw():
    source = r"""
        static unsigned long long negl(unsigned long long x) {
            return 0 - x;
        }

        int main(void) {
            return negl(1000ULL) == 0xfffffffffffffc18ULL ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_right_shift_result_stays_unsigned_for_modulo():
    source = r"""
        int main(void) {
            unsigned int x = 0x80000000u;

            return (((x >> 31) % 2) == 1u) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_ternary_result_stays_unsigned_for_modulo():
    source = r"""
        int main(void) {
            unsigned int u = 3426781690u;

            return (((1 ? u : 1u) % 960) == 250u) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_ternary_uses_c_rank_before_unsignedness_for_shift_compare_and_divide():
    source = r"""
        static long shifted(int flag) {
            return (flag ? -1L : 2u) >> 1;
        }

        static int compared(int flag) {
            return (flag ? -1L : 2u) > 0;
        }

        static long divided(int lhs_flag, int rhs_flag) {
            return (lhs_flag ? 6L : 2u) / (rhs_flag ? -3L : 1u);
        }

        int main(void) {
            if (shifted(1) != -1L) return 1;
            if (compared(1) != 0) return 2;
            if (divided(1, 1) != -2L) return 3;
            return 0;
        }
    """

    assert _evaluate(source) == 0
