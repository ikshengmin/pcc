"""GNU __attribute__ is parsed and decided, never silently erased.

GCC manual, "Attribute Syntax", "Common Type Attributes", "Common Variable
Attributes" and "Common Function Attributes". The owned path used to define
__attribute__ away, so packed, aligned and mode were dropped without notice.
The parser now keeps every attribute; c_gnu_attributes honors aligned, packed
and integer mode, ignores attributes without semantic effect here, and reports
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
    "constructor": "__attribute__((constructor)) static void init(void) {} int main(void) { return 0; }",
    "section": "int x __attribute__((section(\".data.x\"))) = 1; int main(void) { return x; }",
    "weak": "int w __attribute__((weak)) = 1; int main(void) { return w; }",
    "aligned-typedef": "typedef int aint __attribute__((aligned(16))); aint g; int main(void) { return 0; }",
    "packed-bit-fields": "struct S { int a:3; int b:7; } __attribute__((packed)); struct S s; int main(void) { return s.a; }",
    "packed-enum": "enum __attribute__((packed)) E { A, B }; int main(void) { return 0; }",
    "unknown": "int x __attribute__((not_a_gcc_attribute)); int main(void) { return 0; }",
    "mode-on-pointer": "int *p __attribute__((mode(SI))); int main(void) { return 0; }",
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
