"""C17 6.7.5 alignment specifiers on objects and members, executed on the host.

`_Alignas(N)`/`_Alignas(type)` (and `alignas`) set the strictest alignment of
the declared object or member: file-scope and static objects carry an explicit
IR alignment that both self backends now emit; a member makes its record
layout-backed so offsets, size and _Alignof follow it; unions round their size.
A zero alignment has no effect; a non-power-of-two, a weaker-than-natural
alignment and an alignment on a bit-field are errors. An over-aligned automatic object gets an
aligned address inside an oversized byte slot (no frame realignment).
"""
import pytest

from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

PROGRAMS = {
    "file-scope-objects": (r'''
        #include <stdint.h>
        char pad1; _Alignas(64) char buf[8]; char pad2; _Alignas(32) char buf2[8];
        int main(void) { return (int)(((uintptr_t)buf) % 64) + (int)(((uintptr_t)buf2) % 32) + 42; }
    ''', 42),
    "static-local-object": (r'''
        #include <stdint.h>
        int main(void) { static char pad; static _Alignas(128) char line[4]; (void)pad;
                         return (int)(((uintptr_t)line) % 128) + 42; }
    ''', 42),
    "type-form": (r'''
        #include <stdint.h>
        _Alignas(double) char d[8];
        int main(void) { return (int)(((uintptr_t)d) % _Alignof(double)) + 42; }
    ''', 42),
    "zero-has-no-effect": (r'''
        _Alignas(0) int x = 42;
        int main(void) { return x; }
    ''', 42),
    "member-layout": (r'''
        #include <stddef.h>
        struct S { char c; _Alignas(16) int x; char d; };
        int main(void) { return (int)offsetof(struct S, x) + (int)sizeof(struct S) + (int)_Alignof(struct S) - 22; }
    ''', 42),
    "member-access": (r'''
        struct S { char c; _Alignas(16) int x; char d; };
        struct S g = {1, 40, 1};
        int main(void) { struct S *p = &g; return p->x + p->c + p->d; }
    ''', 42),
    "nested-over-aligned-record": (r'''
        #include <stddef.h>
        struct In { _Alignas(32) char v; };
        struct Out { char a; struct In in; int z; };
        struct Out o;
        int main(void) { o.in.v = 5; o.z = 5;
                         return (int)offsetof(struct Out, in) + (int)sizeof(struct Out) - 128 + o.in.v + o.z + 32; }
    ''', 42),
    "union-size-and-stride": (r'''
        union U { _Alignas(16) char c; int i; };
        union U arr[3];
        int main(void) { return (int)sizeof(union U) + (int)_Alignof(union U)
                                + (int)((char *)&arr[1] - (char *)&arr[0]) - 6; }
    ''', 42),
    "natural-local-alignment": (r'''
        int main(void) { _Alignas(4) int x = 42; return x; }
    ''', 42),
}

INVALID = {
    "weaker-than-natural": "_Alignas(1) int x; int main(void) { return 0; }",
    "not-a-power-of-two": "_Alignas(3) char x; int main(void) { return 0; }",
    "bit-field": "struct S { _Alignas(8) int a:3; }; int main(void) { return 0; }",
    "weaker-member": "struct S { _Alignas(1) int a; }; struct S s; int main(void) { return 0; }",
}


@pytest.mark.parametrize("name", PROGRAMS)
def test_alignment_specifiers_execute(name):
    source, expected = PROGRAMS[name]
    assert CEvaluator().evaluate(source) == expected


@pytest.mark.parametrize("name", INVALID)
def test_invalid_alignment_specifiers_are_errors(name):
    from pcc.frontends.c.codegen.c_codegen import SemanticError
    with pytest.raises(SemanticError):
        CEvaluator().evaluate(INVALID[name])


def test_over_aligned_automatic_objects_get_an_aligned_address():
    # An oversized byte slot yields the aligned address; recursion gives each
    # activation its own aligned object.
    source = r'''
        #include <stdint.h>
        static int depth(int n) {
            _Alignas(64) char b[4];
            struct { char c; } __attribute__((aligned(32))) s;
            b[0] = (char)n; s.c = 1;
            int misaligned = (int)(((uintptr_t)b) % 64) + (int)(((uintptr_t)&s) % 32);
            return misaligned + (n ? depth(n - 1) : 0) + b[0] - n + s.c;
        }
        int main(void) { return depth(5) + 36; }
    '''
    assert CEvaluator().evaluate(source) == 42
