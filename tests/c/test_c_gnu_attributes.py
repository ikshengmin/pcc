"""GNU __attribute__ is parsed and decided, never silently erased.

GCC manual, "Attribute Syntax", "Common Type Attributes", "Common Variable
Attributes" and "Common Function Attributes". The owned path used to define
__attribute__ away, so packed, aligned and mode were dropped without notice.
The parser now keeps every attribute; c_gnu_attributes honors aligned, packed
(records, members, bit-fields) and integer mode, ignores attributes without semantic effect here, and reports
every other attribute as an explicit CodegenError gap.
"""
import os
import shutil
import subprocess

import pytest

from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

PROGRAMS = {
    "packed-record-layout": (r'''
        #include <stddef.h>
        struct P { char c; int i; short s; } __attribute__((packed));
        int main(void) { return (int)sizeof(struct P) * 4 + (int)offsetof(struct P, i) * 2
                                + (int)offsetof(struct P, s) - 1 + (int)_Alignof(struct P) * 6; }
    ''', 7 * 4 + 1 * 2 + 5 - 1 + 6),
    "packed-member-access": (r'''
        struct P { char c; int i; short s; } __attribute__((packed));
        struct P g;
        int main(void) { struct P *p = &g; p->c = 1; p->i = 30; p->s = 11; return p->c + p->i + p->s; }
    ''', 42),
    "packed-tag-position": (r'''
        struct __attribute__((packed)) Q { char c; long l; };
        int main(void) { return (int)sizeof(struct Q) + 33; }
    ''', 42),
    "packed-member": (r'''
        #include <stddef.h>
        struct M { char c; int i __attribute__((packed)); };
        int main(void) { return (int)sizeof(struct M) + (int)offsetof(struct M, i) + 36; }
    ''', 42),
    "packed-union": (r'''
        union __attribute__((packed)) U { short s; char b[3]; };
        int main(void) { return (int)sizeof(union U) * 10 + (int)_Alignof(union U) * 11 + 1; }
    ''', 42),
    "aligned-record": (r'''
        struct A { char c; } __attribute__((aligned(16)));
        struct A arr[2];
        int main(void) { return (int)sizeof(struct A) + (int)_Alignof(struct A)
                                + (int)((char *)&arr[1] - (char *)&arr[0]) - 6; }
    ''', 42),
    "aligned-member": (r'''
        #include <stddef.h>
        struct B { char c; int x __attribute__((aligned(8))); };
        int main(void) { return (int)offsetof(struct B, x) + (int)sizeof(struct B) + 18; }
    ''', 42),
    "aligned-object": (r'''
        #include <stdint.h>
        char pad; int v __attribute__((aligned(64))) = 42;
        int main(void) { return v + (int)((uintptr_t)&v % 64); }
    ''', 42),
    "integer-modes": (r'''
        typedef int qi __attribute__((mode(QI)));
        typedef unsigned int uhi __attribute__((mode(HI)));
        typedef int di __attribute__((__mode__(__DI__)));
        int main(void) { qi a = 127; a = a + 1; uhi b = 65535; b = b + 1; di c = 1; c <<= 40;
                         return (a == -128) + (b == 0) + (sizeof(c) == 8) + (int)(c >> 40) + 38; }
    ''', 42),
    "packed-static-initializers": (r'''
        struct P { char c; int i; short s; } __attribute__((packed));
        struct P g = {1, 0x01020304, 0x0506};
        struct P arr[2] = {{1, 2, 3}, {4, 5, 6}};
        int main(void) {
            unsigned char *b = (unsigned char *)&g;
            int ok = b[0] == 1 && b[1] == 4 && b[2] == 3 && b[4] == 1 && b[5] == 6 && b[6] == 5;
            ok += arr[1].c == 4 && arr[1].i == 5 && arr[1].s == 6 && sizeof arr == 14;
            return ok * 20 + 2;
        }
    ''', 42),
    "packed-bit-field-layout": (r'''
        struct __attribute__((packed)) S70 { int s : 1; int t : 20; };
        struct __attribute__((packed)) S4 { long long a : 59; long long b : 54; char c; long long : 0; };
        struct __attribute__((packed)) Z { char a; int : 0; char b; };
        struct __attribute__((packed)) Q { char a; int b : 3; char x; short y : 15; };
        struct M { char a; int b : 4 __attribute__((packed)); int c : 30 __attribute__((packed)); };
        int main(void) {
            return (sizeof(struct S70) == 3) + (sizeof(struct S4) == 16) + (sizeof(struct Z) == 5)
                 + (sizeof(struct Q) == 5) + (sizeof(struct M) == 6) + (_Alignof(struct M) == 1) + 36;
        }
    ''', 42),
    "packed-bit-field-odd-window": (r'''
        struct __attribute__((packed)) S { int s : 1; int t : 20; } v[2];
        int main(void) {
            v[0].s = -1; v[0].t = -12345; v[1].t = 0x7ffff;
            unsigned char *b = (unsigned char *)v;
            unsigned x0 = b[0] | b[1] << 8 | (unsigned)b[2] << 16;
            unsigned x1 = b[3] | b[4] << 8 | (unsigned)b[5] << 16;
            int ok = x0 == (1u | ((unsigned)(-12345 & 0xfffff) << 1)) && x1 == (0x7ffffu << 1);
            v[0].t += 5; v[1].t--;
            ok += v[0].t == -12340 && v[0].s == -1 && v[1].t == 0x7fffe && v[1].s == 0;
            return ok * 20 + 2;
        }
    ''', 42),
    "packed-bit-field-wide-window": (r'''
        struct __attribute__((packed)) S { long long a : 59; long long b : 54; char c; long long : 0; } s[2];
        int main(void) {
            s[0].a = 0x123456789abcdefLL; s[0].b = -2; s[0].c = 7;
            int ok = s[0].a == 0x123456789abcdefLL && s[0].b == -2 && s[0].c == 7;
            ok += s[1].a == 0 && s[1].b == 0 && s[1].c == 0 && sizeof s == 32;
            return ok * 20 + 2;
        }
    ''', 42),
    "packed-bit-field-initializers": (r'''
        struct __attribute__((packed)) S { int g : 31; int h : 6; };
        struct S a = { 1 };
        static struct S b = { 1, -3 };
        struct N { char c; int n : 30 __attribute__((packed)); } n = { 1, 123456 };
        int main(void) {
            struct S l = { -2, 9 };
            n.n += 1;
            return (a.g == 1) + (a.h == 0) + (b.h == -3) + (l.g == -2) + (l.h == 9)
                 + (n.n == 123457) + (sizeof(struct S) == 5) + (sizeof n == 5) + 34;
        }
    ''', 42),
    "mode-on-typedef-and-enum-types": (r'''
        typedef enum { C = 1, D = 2 } B;
        typedef enum { N = -1, P = 1 } SB;
        enum E { E0, E1 = 0x80 };
        typedef int I;
        typedef unsigned short U;
        struct S { B __attribute__ ((mode (byte))) a; B __attribute__ ((__mode__ (__byte__))) b;
                   SB __attribute__((mode(QI))) c; enum E __attribute__((mode(QI))) e; };
        I __attribute__((mode(HI))) h = 32767;
        U __attribute__((mode(QI))) u = 255;
        int main(void) {
            struct S s = { C, D, N, E1 };
            h = h + 1; u = u + 1;
            return (sizeof(struct S) == 4) + (s.a == C) + (s.b == D) + (s.c == -1) + (s.e == 0x80)
                 + (h == -32768) + (u == 0) + (sizeof h == 2) + 34;
        }
    ''', 42),
    "aligned-typedef-objects-and-members": (r'''
        #include <stddef.h>
        #include <stdint.h>
        typedef int aint __attribute__((aligned(16)));
        typedef aint again;
        struct S { char c; aint x; };
        struct T { char c; again y; };
        char pad; aint g = 40;
        int main(void) {
            char lpad; aint l = 2; (void)lpad;
            return g + l + (int)((uintptr_t)&g % 16) + (int)((uintptr_t)&l % 16)
                 + (offsetof(struct S, x) == 16) + (sizeof(struct S) == 32) + (offsetof(struct T, y) == 16) - 3;
        }
    ''', 42),
    "inert-attributes-in-every-position": (r'''
        static int __attribute__((noinline, noclone)) helper(int x __attribute__((unused))) { return 40; }
        extern int proto(void) __attribute__((pure));
        int proto(void) { return 1; }
        static void *__attribute__((noinline)) self(void *p) { return p; }
        __attribute__((cold)) static int pick(int k) {
            switch (k) { case 0: k = 1; __attribute__((fallthrough)); default: break; }
            return k;
        }
        int main(void) {
            int (*fp)(void) = (int (__attribute__((noinline)) *)(void))proto;
            return helper(0) + fp() + pick(0) + (self(&fp) == &fp) - 1;
        }
    ''', 42),
}

GAPS = {
    "vector-size": "typedef int v4 __attribute__((vector_size(16))); int main(void) { v4 x; (void)x; return 0; }",
    "cleanup": "void f(int *p) {} int main(void) { int x __attribute__((cleanup(f))) = 1; return x; }",
    "section": "int x __attribute__((section(\".data.x\"))) = 1; int main(void) { return x; }",
    "weak": "int w __attribute__((weak)) = 1; int main(void) { return w; }",
    "aligned-typedef-in-sizeof": "typedef int aint __attribute__((aligned(16))); int main(void) { return sizeof(aint); }",
    "aligned-typedef-in-cast": "typedef int aint __attribute__((aligned(16))); int main(void) { return (aint)1; }",
    "aligned-typedef-pointer": "typedef int aint __attribute__((aligned(16))); aint *p; int main(void) { return 0; }",
    "aligned-typedef-parameter": "typedef int aint __attribute__((aligned(16))); int f(aint a) { return a; } int main(void) { return 0; }",
    "packed-enum": "enum __attribute__((packed)) E { A, B }; int main(void) { return 0; }",
    "unknown": "int x __attribute__((not_a_gcc_attribute)); int main(void) { return 0; }",
    "mode-on-pointer": "int *p __attribute__((mode(SI))); int main(void) { return 0; }",
    "mode-on-enum-definition": "enum __attribute__((unused)) { A, B } __attribute__((mode(QI))) e; int main(void) { return e; }",
}


@pytest.mark.parametrize("name", PROGRAMS)
def test_gnu_attribute_programs_execute(name):
    source, expected = PROGRAMS[name]
    assert CEvaluator().evaluate(source) == expected


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_gnu_attributes_are_explicit_gaps(name):
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    with pytest.raises(CodegenError, match="GNU attribute"):
        CEvaluator().evaluate(GAPS[name])


def test_aligned_typedef_that_lowers_alignment_is_an_explicit_gap():
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    with pytest.raises(CodegenError, match="lowers its type's alignment"):
        CEvaluator().evaluate("typedef int u32 __attribute__((aligned(1))); struct S { char c; u32 v; } s;"
                              "int main(void) { return s.v; }")


def test_unused_aligned_typedef_has_no_effect():
    assert CEvaluator().evaluate("typedef int aint __attribute__((aligned(16))); struct S { int x; };"
                                 "int main(void) { return sizeof(struct S) == sizeof(int) ? 42 : 1; }") == 42


def test_packed_bit_field_wider_than_eight_bytes_is_an_explicit_gap():
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    with pytest.raises(CodegenError, match="spanning 9 bytes is not implemented"):
        CEvaluator().evaluate("struct __attribute__((packed)) W { char a : 3; long long b : 64; } w;"
                              "int main(void) { return (int)w.b; }")


def test_attributes_are_kept_on_the_ast_not_erased():
    from pcc.frontends.c.parse import make_c_parser
    from pcc.frontends.c.ast import c_ast
    ast = make_c_parser().parse("struct P { char c; int i; } __attribute__((packed, aligned(4))) p;"
                                "void *__attribute__((noinline)) f(void);")
    decl, function = ast.ext
    assert [a.name for a in decl.type.type.attributes] == ["packed", "aligned"]
    pointer = function.type.type
    assert isinstance(pointer, c_ast.PtrDecl) and [a.name for a in pointer.attributes] == ["noinline"]


def reference_compiler():
    return shutil.which(os.environ.get("PCC_C_REFERENCE", "cc"))


@pytest.mark.integration
@pytest.mark.skipif(reference_compiler() is None, reason="no reference C compiler")
@pytest.mark.parametrize("name", PROGRAMS)
def test_gnu_attribute_programs_match_the_reference_compiler(name, tmp_path):
    source, expected = PROGRAMS[name]
    unit = tmp_path / "original.c"
    unit.write_text(source)
    binary = tmp_path / "reference"
    built = subprocess.run([reference_compiler(), "-std=gnu11", "-O0", "-w", str(unit), "-o", str(binary)],
                           capture_output=True, timeout=60)
    assert built.returncode == 0, built.stderr
    assert subprocess.run([str(binary)], capture_output=True, timeout=30).returncode == expected
