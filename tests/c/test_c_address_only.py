"""C11 6.5.3.2 address formation shares layout, never a discarded target load.

This checks no-read address semantics, not complete volatile access semantics.
GNU statement expressions are labeled separately from the standard C cases.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess

import pytest

from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
from pcc.frontends.c.codegen.c_declaration_state import CodegenError
from pcc.frontends.c.parse import make_c_parser
from pcc.frontends.c.passes.context import PassContext
from tests.c.c_statement_address_extensions import OUTER_ADDRESS_EXTENSIONS
from tests.c.test_c_aggregate_identity import compile_unit

TARGET = 'x86_64-unknown-linux-gnu'

NO_READ_CASES = (
    'volatile int value;volatile int*probe(void){return &value;}',
    'struct S{int value;};struct S value;struct S*probe(void){return &value;}',
    'struct Inner{volatile unsigned char value;};struct Outer{struct Inner inner;};struct Outer object;volatile unsigned char*probe(void){return &object.inner.value;}',
    'int values[3];int(*probe(void))[3]{return &values;}',
    'int values[2][3];int(*probe(void))[3]{return &values[1];}',
)


@pytest.mark.parametrize('source', NO_READ_CASES)
def test_address_only_never_fetches_named_target(source):
    first = compile_unit(source, 'address.c')
    second = compile_unit(source, 'address.c')
    assert first[1:] == second[1:]
    assert not re.search(r'\bload\b', first[1])


def test_address_of_dereference_reads_pointer_but_not_volatile_referent():
    _, text, _ = compile_unit('volatile int*probe(volatile int*p){return &*p;}', 'deref.c')
    assert not re.search(r'\bload\s+i32\b', text)


PROGRAMS = (
    ('generic-address-anonymous-field-explicit-reset', 'struct S{struct{int x;int y;};int b[3];};int main(void){return _Generic(&(struct S){.x=1,.b={[2]=3}},struct S*:42,default:1);}'),
    ('generic-address-designated-aggregate-scalar-continuation', 'struct S{int a[2];};int main(void){return _Generic(&(struct S){.a=1,2},struct S*:42,default:1);}'),
    ('gnu-generic-address-valid-range-designator', 'int main(void){return _Generic(&(int[3]){[0 ... 1]=0},int(*)[3]:42,default:1);}'),
    ('generic-address-sizeof-object-designator', 'int main(void){int n;return _Generic(&(int[2]){[sizeof(n)/sizeof(int)]=0},int(*)[2]:42,default:1);}'),
    ('generic-address-independent-array-value-literal', 'int main(void){return _Generic(&(int*[1]){(int[2]){[1]=0}},int*(*)[1]:42,default:1);}'),
    ('generic-address-mixed-independent-value-literal', 'struct S{int*p;int a[2];};int main(void){return _Generic(&(struct S){{(int[2]){[1]=0}},{[1]=0}},struct S*:42,default:1);}'),
    ('generic-address-designator-resets-continuation', 'struct S{int a[2];int b[3];};int main(void){return _Generic(&(struct S){.a[0]=1,.b={[2]=3}},struct S*:42,default:1);}'),
    ('generic-address-nested-compound-designator-no-effects', 'int take(int*);int main(void){return _Generic(&(int[1]){take((int[2]){[1]=0})},int(*)[1]:42,default:1);}'),
    ('generic-address-continuation-nested-compound-no-effects', 'int take(int*);struct S{int a[2];};int main(void){return _Generic(&(struct S){.a[0]=1,take((int[2]){[1]=0})},struct S*:42,default:1);}'),
    ('generic-address-designated-scalar-continuation', 'struct S{int a[2];};int main(void){return _Generic(&(struct S){.a[0]=1,2},struct S*:42,default:1);}'),
    ('generic-address-fixed-compound-designator', 'int main(void){return _Generic(&(int[2]){[1]=0},int(*)[2]:42,default:1);}'),
    ('generic-address-fixed-compound-field-designator', 'struct S{int n;};int main(void){return _Generic(&(struct S[2]){[0].n=1},struct S(*)[2]:42,default:1);}'),
    ('generic-address-inferred-compound-custom-layout', 'struct S{char c;unsigned b:1;int a[2];};int main(void){return _Generic(&(struct S[]){[0].a[1]=7},struct S(*)[1]:42,default:1);}'),
    ('generic-address-inferred-compound-member-designator', 'struct S{int n;};int main(void){return _Generic(&(struct S[]){[0].n=1},struct S(*)[1]:42,default:1);}'),
    ('generic-address-inferred-compound-nested-index', 'enum{N=1};int main(void){return _Generic(&(int[][2]){[1][N]=0},int(*)[2][2]:42,default:1);}'),
    ('generic-address-inferred-compound-array', 'int main(void){return _Generic(&(int[]){0,0},int(*)[2]:42,default:1);}'),
    ('generic-address-inferred-compound-rows', 'int main(void){return _Generic(&(int[][2]){1,2,3,4},int(*)[2][2]:42,default:1);}'),
    ('generic-address-inferred-compound-no-effects', 'int side(void);int main(void){return _Generic(&(int[]){side(),0},int(*)[2]:42,default:1);}'),
    ('generic-address-inferred-compound-designator', 'int main(void){return _Generic(&(int[]){[3]=0},int(*)[4]:42,default:1);}'),
    ('generic-address-wide-string-array', 'int main(void){return _Generic(&L"xy",int(*)[3]:42,default:1);}'),
    ('gnu-generic-address-local-const-shadow', 'int n;int main(void){return _Generic(&({const int n=0;n;}),const int*:42,default:1);}'),
    ('generic-address-parenthesized-compound-array', 'int main(void){return _Generic(&((int[3]){0}),int(*)[3]:42,default:1);}'),
    ('generic-address-const-pointer-object', 'int main(void){int*const p=0;return _Generic(&p,int*const*:42,default:1);}'),
    ('generic-address-function-dereference', 'int f(void){return 42;}int main(void){return _Generic(&*f,int(*)(void):42,default:1);}'),
    ('generic-address-string-array', 'int main(void){return _Generic(&"xy",char(*)[3]:42,default:1);}'),
    ('generic-address-compound-array', 'int main(void){return _Generic(&(int[3]){0},int(*)[3]:42,default:1);}'),
    ('gnu-generic-address-local-array-scope', 'int main(void){char a;return _Generic(&({int a[3];a;}),int(*)[3]:42,default:1);}'),
    ('generic-address-array-not-decayed', 'int main(void){int a[3];return _Generic(&a,int(*)[3]:42,default:1);}'),
    ('generic-address-const-object', 'int main(void){const int n=0;return _Generic(&n,const int*:42,default:1);}'),
    ('generic-address-function', 'int f(void){return 42;}int main(void){return _Generic(&f,int(*)(void):42,default:1);}'),
    ('generic-address-selected-array', 'int main(void){int a[3];return _Generic(&_Generic(0,int:a),int(*)[3]:42,default:1);}'),
    ('gnu-whole-vla-scoped-address', 'int main(void){int n=3;int a[n];if(sizeof(*(&({a;})))!=sizeof(a))return 1;return 42;}'),
    ('gnu-unevaluated-local-address-scope', 'int main(void){int x=0;if(sizeof(&({int x=42;x;}))!=sizeof(int*))return 1;return 42;}'),
    ('whole-vla-address-unevaluated', 'int main(void){int n=3;int a[n];if(sizeof(&a)!=sizeof(void*))return 1;return 42;}'),
    ('whole-vla-address-generic', 'int main(void){int n=3;int a[n];if(sizeof(&_Generic(0,int:a))!=sizeof(void*))return 1;return 42;}'),
    ('gnu-query-array-alias-bound-identity', 'enum{N=3};typedef int A[N];int main(void){if(sizeof(*(&({int N=1;A x;_Generic(&x,int(*)[3]:x,default:0);})))!=3*sizeof(int))return 1;return 42;}'),
    ('gnu-query-multilevel-typedef-origin', 'typedef int K;typedef K Saved;int main(void){typedef char K;typedef Saved Again;if(sizeof(*(&({Again x;x;})))!=sizeof(int))return 1;return 42;}'),
    ('query-const-object-address-is-legal', 'int main(void){const int n=42;if(sizeof(&n)!=sizeof(const int*))return 1;return 42;}'),
    ('gnu-query-existing-nominal-typedef', 'typedef struct S{int n;} S;int main(void){if(sizeof(*(&({S x;x;})))!=sizeof(S))return 1;return 42;}'),
    ('gnu-query-initializer-not-evaluated', 'int side(void);int main(void){if(sizeof(&({int x=side();x;}))!=sizeof(int*))return 1;return 42;}'),
    ('gnu-query-local-typedef-shadow', 'typedef char K;int main(void){if(sizeof(*(&({typedef long long K;K x; x;})))!=sizeof(long long))return 1;return 42;}'),
    ('gnu-query-typedef-alias-declaration-binding', 'typedef int K;typedef K Saved;int main(void){if(sizeof(*(&({typedef long long K;Saved x;_Generic((Saved)0,int:x,default:0);})))!=sizeof(int))return 1;return 42;}'),
    ('gnu-query-local-fixed-array', 'int side(void);int main(void){if(sizeof(*(&({int a[3]={side(),0,0};a;})))!=3*sizeof(int))return 1;return 42;}'),
    ('gnu-query-local-vla-pointer-no-bound-evaluation', 'int side(void);int main(void){if(sizeof(&({int n=side();int a[n];a;}))!=sizeof(void*))return 1;return 42;}'),
    ('gnu-query-nested-vla-pointer-no-bound-evaluation', 'int side(void);int main(void){if(sizeof(&({int n=side();int a[2][n];a;}))!=sizeof(void*))return 1;return 42;}'),
    ('gnu-query-captured-vla-extent', 'int main(void){int n=3;int a[n];n=5;if(sizeof(*(&({a;})))!=3*sizeof(int))return 1;return 42;}'),
    ('function-name-array-address', 'int main(void){const char(*p)[5]=&__func__;if(sizeof(*p)!=5)return 1;if((*p)[0]!=109)return 2;return 42;}'),
    ('vla-element-address-once', 'int calls;int index(void){calls++;return 1;}int main(void){int n=3;int a[n];a[1]=42;int*p=&a[index()];if(calls!=1)return 1;return *p;}'),
    ('offsetof-shortcut', 'struct S{int x;char y;};int main(void){if((unsigned long)&((struct S*)0)->y!=sizeof(int))return 1;return 42;}'),
    ('anonymous-nested-field-address', 'struct O{struct{int n;};};int main(void){struct O o={{42}};return *(&o.n);}'),
    ('scalar-volatile-address', 'volatile int n=42;int main(void){volatile int*p=&n;return *p;}'),
    ('nested-field-address', 'struct S{int n;};struct O{struct S s;};int main(void){struct O o={{42}};return *(&o.s.n);}'),
    ('array-shape-and-stride', 'int main(void){int a[3]={1,2,3};int(*p)[3]=&a;if(sizeof(*p)!=sizeof(a))return 1;if((char*)(p+1)-(char*)p!=sizeof(a))return 2;return 42;}'),
    ('index-and-base-once', 'int calls=0;int a[3]={0,42,0};int*base(void){calls++;return a;}int index(void){calls++;return 1;}int main(void){int*p=&base()[index()];if(calls!=2)return 1;return *p;}'),
    ('dereference-base-once', 'int calls=0;int n=42;int*base(void){calls++;return &n;}int main(void){int*p=&*base();if(calls!=1)return 1;return *p;}'),
    ('unsigned-field-direct-address', 'struct S{unsigned char n;};int main(void){struct S s={255};if(*(&s.n)!=255)return 1;return 42;}'),
    ('unsigned-pointer-chain', 'int main(void){unsigned char n=255;unsigned char*p=&n;if(**(&p)!=255)return 1;return 42;}'),
    ('function-address', 'unsigned char f(void){return 255;}int main(void){if((&f)()!=255)return 1;return 42;}'),
    ('function-pointer-address', 'unsigned char f(void){return 255;}int main(void){unsigned char(*p)(void)=f;if((*(&p))()!=255)return 1;return 42;}'),
    ('compound-literal-once', 'int calls=0;int next(void){calls++;return 42;}struct S{int n;};int main(void){struct S*p=&(struct S){next()};if(calls!=1)return 1;return p->n;}'),
    ('generic-selected-lvalue', 'int calls=0;int choose(void){calls++;return 1;}int main(void){int x=42;int*p=&_Generic(choose(),int:x,default:calls);if(calls)return 1;return *p;}'),
    ('gnu-statement-expression-address', 'int calls=0;int main(void){int x=42;int*p=&({calls++;x;});if(calls!=1)return 1;return *p;}'),
    ('prototype-base-address', 'struct S{int n;};struct S*side(void);int*probe(void){return &side()->n;}struct S value={42};int calls;struct S*side(void){calls++;return &value;}int main(void){int*p=probe();if(calls!=1)return 1;return *p;}'),
    ('function-name-element-address', 'int main(void){const char*p=&__func__[1];if(*p!=\'a\')return 1;return 42;}'),
    ('void-comma-value-preserved', 'int calls;void side(void){calls++;}int main(void){int n=(side(),42);if(calls!=1)return 1;return n;}'),
    ('gnu-scoped-floating-value-preserved', 'int f(int c){double d=c?({double x=42.0;x;}):1.0;return (int)d;}int main(void){return f(1);}'),
    ('comma-compound-array-storage', 'int main(void){int*p=&(0,(int[2]){42,0})[0];return *p;}'),
    ('conditional-compound-array-storage', 'int f(int c){return *(&(c?(int[2]){42,0}:(int[2]){1,0})[0]);}int main(void){return f(1);}'),
    ('gnu-unevaluated-outer-scalar-address', 'int main(void){int x=42;if(sizeof(*(&({x;})))!=sizeof(x))return 1;return 42;}'),
    ('binary-compound-array-storage', 'int main(void){int*p=&((int[2]){42,0}+0)[0];return *p;}'),
    ('call-compound-array-storage', 'int read(int*p){return *p;}int main(void){return read((int[2]){42,0});}'),
    ('cast-compound-array-storage', 'int main(void){int*p=(int*)(void*)(int[2]){42,0};return *p;}'),
    ('unsigned-comma-array-decay', 'int main(void){unsigned char a[1]={255};if(*(0,a)!=255)return 1;return 42;}'),
    ('unsigned-conditional-array-decay', 'int f(int c){unsigned char a[1]={255};unsigned char b[1]={1};if(*(c?a:b)!=255)return 1;return 42;}int main(void){return f(1);}'),
    ('gnu-scoped-vla-decay-preserved', 'int main(void){int n=2;int a[n];a[1]=42;int*p=&({a;})[1];return *p;}'),
    ('gnu-omitted-conditional-arm-base', 'int main(void){int a[2]={0,42};int*p=a;int*q=0;return *(&(p?:q)[1]);}'),
    ('assignment-pointer-subscript-base', 'int main(void){int a[2]={0,42};int*p=0;int*q=&(p=a)[1];if(p!=a)return 1;return *q;}'),
    ('assignment-pointer-arrow-base', 'struct S{int n;};int main(void){struct S s={42};struct S*p=0;int*q=&(p=&s)->n;if(p!=&s)return 1;return *q;}'),
    ('comma-array-base-decays', 'int main(void){int a[2]={0,42};int*p=&(0,a)[1];return *p;}'),
    ('conditional-array-base-decays', 'int f(int c){int a[2]={0,42};int b[2]={0,1};return *(&(c?a:b)[1]);}int main(void){return f(1);}'),
    ('gnu-comma-array-scoped-base-decays', 'int main(void){int a[2]={0,42};int*p=&(({int n=0;n;}),a)[1];return *p;}'),
    ('gnu-conditional-array-scoped-index-decays', 'int f(int c){int a[2]={0,42};int b[2]={0,1};return *(&(c?a:b)[({int i=1;i;})]);}int main(void){return f(1);}'),
    ('gnu-generic-scoped-compound-array', 'int main(void){int*p=&_Generic(0,int:(int[2]){({int x=42;x;}),0},default:(int[2]){0,0})[0];return *p;}'),
    ('gnu-scoped-string-index', 'int main(void){char*p=&"xy"[({int i=1;i;})];if(*p!=\'y\')return 1;return 42;}'),
    ('gnu-scoped-compound-array-address', 'int main(void){int*p=&((int[2]){({int x=42;x;}),0})[0];return *p;}'),
    ('gnu-both-scoped-subscript', 'int main(void){int a[2]={0,42};int*p=&({int*q=a;q;})[({int i=1;i;})];return *p;}'),
    ('gnu-scoped-pointer-subscript', 'int main(void){int a[2]={0,42};int*p=&({int*q=a;q;})[1];return *p;}'),
    ('gnu-scoped-pointer-reversed-subscript', 'int main(void){int a[2]={0,42};int*p=&1[({int*q=a;q;})];return *p;}'),
    ('gnu-scoped-pointer-arrow', 'struct S{int n;};int main(void){struct S s={42};struct S*p=&s;int*r=&({struct S*q=p;q;})->n;return *r;}'),
    ('gnu-scoped-array-base', 'int main(void){int a[2]={0,42};int*p=&({a;})[1];return *p;}'),
    ('gnu-scoped-generic-address', 'int main(void){int x=42;int*p=&({int key=0;_Generic(key,int:x,default:0);});return *p;}'),
    ('gnu-function-name-element-address', 'int main(void){const char*p=&__FUNCTION__[1];if(*p!=\'a\')return 1;return 42;}'),
    ('gnu-pretty-name-element-address', 'int main(void){const char*p=&__PRETTY_FUNCTION__[1];if(*p!=\'a\')return 1;return 42;}'),
    ('cancelled-assignment-dereference', 'int main(void){int x=42;int*p=0;int*q=&x;int*r=&*(p=q);if(p!=q)return 1;return *r;}'),
    ('cancelled-function-dereference', 'int f(void){return 42;}int main(void){return (&*f)();}'),
    ('function-pointer-base-address', 'struct S{int n;};struct S value={42};int calls;struct S*side(void){calls++;return &value;}int*probe(struct S*(*fn)(void)){return &fn()->n;}int main(void){int*p=probe(side);if(calls!=1)return 1;return *p;}'),
    ('function-pointer-field-base-address', 'struct S{int n;};struct S value={42};int calls;struct S*side(void){calls++;return &value;}struct F{struct S*(*fn)(void);};int main(void){struct F f={side};int*p=&f.fn()->n;if(calls!=1)return 1;return *p;}'),
    ('string-element-address', 'int main(void){char*p=&"xy"[1];if(*p!=\'y\')return 1;return 42;}'),
    ('wide-string-array-address', 'int main(void){int(*p)[3]=&L"xy";if(sizeof(*p)!=3*sizeof(int))return 1;if((*p)[1]!=L\'y\')return 2;return 42;}'),
    ('string-array-address', 'int main(void){char(*p)[3]=&"xy";if(sizeof(*p)!=3)return 1;if((*p)[1]!=\'y\')return 2;return 42;}'),
    # An array or string used as the final expression *statement* converts
    # inside the GNU value boundary. sizeof of a bare array or of a pointer's
    # array pointee still sees that array. Compare unchanged C bytes to cc.
    ('gnu-stmt-value-fixed-array-size', 'int main(void){int a[3];return sizeof(({a;}))==sizeof(int*)&&sizeof(a)==3*sizeof(int)?42:1;}'),
    ('gnu-stmt-value-row-pointer-size', 'int main(void){int a[5][3];return sizeof(({a;}))==sizeof(int(*)[3])&&sizeof(*({a;}))==3*sizeof(int)&&sizeof(*({&a;}))==sizeof(a)?42:1;}'),
    ('gnu-stmt-value-literals-size', 'int main(void){return sizeof(({"hello";}))==sizeof(char*)&&sizeof(({(int[9]){0};}))==sizeof(int*)&&sizeof("hello")==6?42:1;}'),
    ('gnu-stmt-value-nested-generic-size', 'int main(void){int a[7];return sizeof(({({_Generic(1,int:a,default:0);});}))==sizeof(int*)?42:1;}'),
    ('gnu-stmt-value-captured-vla-size', 'int main(void){int n=7;int a[n];n=3;return sizeof(({a;}))==sizeof(int*)&&sizeof(a)==7*sizeof(int)?42:1;}'),
    ('gnu-stmt-value-query-local-vla-is-unevaluated', 'int ticks;int extent(void){ticks++;return 5;}int main(void){int n=sizeof(({int a[extent()];a;}));return n==sizeof(int*)&&ticks==0?42:1;}'),
    ('gnu-stmt-value-prefix-is-unevaluated', 'int ticks;int main(void){int a[17];int n=sizeof(({ticks++;a;}));return n==sizeof(int*)&&ticks==0?42:1;}'),
    ('gnu-stmt-value-aggregate-is-not-a-pointer', 'struct S{int a[5];int x;};int main(void){struct S s;return sizeof(({s;}))==sizeof(s)?42:1;}'),
)


# Never decide a program's oracle by running it and observing which branch
# succeeds. Only these frozen originals have deliberately different results.
GNU_COMPATIBLE_PROGRAMS = tuple(row for row in PROGRAMS if row[0] not in OUTER_ADDRESS_EXTENSIONS)
EXTENSION_ORIGINALS = tuple(row for row in PROGRAMS if row[0] in OUTER_ADDRESS_EXTENSIONS)
INNER_ADDRESS_CONTROLS = tuple((name + '-inner-address', OUTER_ADDRESS_EXTENSIONS[name][3])
                              for name, _ in EXTENSION_ORIGINALS)


def _check_extension_identity(name, source):
    digest, line, column, _ = OUTER_ADDRESS_EXTENSIONS[name]
    assert hashlib.sha256(source.encode('utf-8')).hexdigest() == digest, name
    assert source.splitlines()[line - 1][column - 1:column + 2] == '&({', name
    return digest, line, column


def test_statement_address_oracles_cover_every_original_once():
    original_names = [name for name, _ in PROGRAMS]
    assert len(set(original_names)) == len(original_names)
    assert len(OUTER_ADDRESS_EXTENSIONS) == len(EXTENSION_ORIGINALS) == len(INNER_ADDRESS_CONTROLS) == 16
    assert {name for name, _ in EXTENSION_ORIGINALS} == set(OUTER_ADDRESS_EXTENSIONS)
    assert len(GNU_COMPATIBLE_PROGRAMS) + len(EXTENSION_ORIGINALS) == len(PROGRAMS)
    assert not set(original_names) & {name for name, _ in INNER_ADDRESS_CONTROLS}
    for name, source in EXTENSION_ORIGINALS:
        _check_extension_identity(name, source)
        assert OUTER_ADDRESS_EXTENSIONS[name][3] != source


# These unchanged positives reached the x86_64 dynamic-stack capability
# boundary in CI. Keep their object/native obligations below; independent IR
# checks qualify only frontend lowering, never backend execution support.
VLA_BACKEND_PREREQUISITE_NAMES = frozenset((
    'gnu-query-captured-vla-extent',
    'gnu-scoped-vla-decay-preserved',
    'gnu-whole-vla-scoped-address',
    'vla-element-address-once',
    'whole-vla-address-generic',
    'whole-vla-address-unevaluated',
))
VLA_IR_PROGRAMS = tuple(row for row in PROGRAMS if row[0] in VLA_BACKEND_PREREQUISITE_NAMES)


@pytest.mark.parametrize('name,source', VLA_IR_PROGRAMS, ids=[row[0] for row in VLA_IR_PROGRAMS])
def test_vla_address_original_frontend_ir_is_stable(name, source):
    texts = []
    for _ in range(2):
        generator = CCodeGenerator(translation_unit_name=name+'.c',
                                  pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
        generator.generate_code(make_c_parser().parse(source))
        texts.append(str(generator.module))
    assert texts[0] == texts[1]


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_address_original_ir_and_object_bytes_are_stable(name, source):
    assert compile_unit(source, name+'.c')[1:] == compile_unit(source, name+'.c')[1:]


@pytest.mark.parametrize('source,message', (
    ('struct S{unsigned n:3;};struct S*side(void);unsigned*bad(void){return &side()->n;}', 'bit.field'),
    ('union U{unsigned n:3;int value;};unsigned*bad(void){return &((union U*)0)->n;}', 'bit.field'),
    ('struct S{union{unsigned n:3;int value;};};unsigned*bad(void){return &((struct S*)0)->n;}', 'bit.field'),
    ('union U{unsigned n:3;int value;};union U*side(void);unsigned*bad(void){return &side()->n;}', 'bit.field'),
    ('struct S{union{unsigned n:3;int value;};};struct S*side(void);unsigned*bad(void){return &side()->n;}', 'bit.field'),
    ('union U{struct{unsigned n:3;};int value;};union U*side(void);unsigned*bad(void){return &side()->n;}', 'bit.field'),
    ('int side(void);int*bad(void){return &(side()+1);}', 'address-only lvalue shape'),
    ('int*bad(void){int n=0;return &(n=42);}', 'address-only lvalue shape'),
    ('int main(void){int n=0;return sizeof(&(n=42));}', 'address-only lvalue shape'),
    ('int side(void);int main(void){return sizeof(&(side()+1));}', 'address-only lvalue shape'),
))
def test_invalid_address_is_not_emitted_after_effectful_fallback(source, message):
    generator = CCodeGenerator(translation_unit_name='invalid.c',
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    with pytest.raises(CodegenError, match=message):
        generator.generate_code(make_c_parser().parse(source))
    assert not re.search(r'\bcall\b[^\n]*@"?side', str(generator.module))



def _compile_address_original(name, source, tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file

    def forbidden(*args, **kwargs):
        raise AssertionError('owned address compilation invoked an external build tool')

    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', forbidden)
        _, ir, obj = compile_unit(source, name+'.c')
        _, ir_again, obj_again = compile_unit(source, name+'.c')
        startup = assemble_file('.intel_syntax noprefix\n.text\n.globl _start\n_start:\ncall main\nmov edi,eax\nmov eax,60\nsyscall\n')
        binary_bytes = link_static_executable([startup, parse_relocatable(obj)], entry='_start')
    assert (ir, obj) == (ir_again, obj_again)
    binary = tmp_path / 'owned'
    binary.write_bytes(binary_bytes)
    binary.chmod(0o700)
    (tmp_path / 'owned.ll').write_text(ir)
    (tmp_path / 'owned.o').write_bytes(obj)
    original = tmp_path / (name+'.c')
    original.write_text(source, encoding='utf-8', newline='')
    return binary, original


def _observe_address_run(executable, label, tmp_path):
    run = subprocess.run([str(executable)], capture_output=True, timeout=10)
    (tmp_path / (label + '.stdout.bin')).write_bytes(run.stdout)
    (tmp_path / (label + '.stderr.bin')).write_bytes(run.stderr)
    return run.returncode, run.stdout, run.stderr


def _compare_address_positive(name, source, tmp_path, monkeypatch, compiler, env=None):
    binary, original = _compile_address_original(name, source, tmp_path, monkeypatch)
    reference = tmp_path / 'reference'
    standard = 'gnu11' if name.startswith('gnu-') else 'c11'
    command = [compiler, '-std='+standard, '-O0', str(original), '-o', str(reference)]
    built = subprocess.run(command, capture_output=True, timeout=30, env=env)
    (tmp_path / 'reference-build.stdout.bin').write_bytes(built.stdout)
    (tmp_path / 'reference-build.stderr.bin').write_bytes(built.stderr)
    assert built.returncode == 0, built.stderr
    observations = [_observe_address_run(executable, label, tmp_path)
                    for label, executable in (('owned', binary), ('reference', reference))]
    (tmp_path / 'observations.json').write_text(json.dumps({
        'reference_command': command,
        'returncodes': [item[0] for item in observations],
        'stdout_hex': [item[1].hex() for item in observations],
        'stderr_hex': [item[2].hex() for item in observations],
    }, indent=2))
    assert observations == [(42, b'', b''), (42, b'', b'')]


@pytest.mark.integration
@pytest.mark.parametrize('name,source', GNU_COMPATIBLE_PROGRAMS, ids=[row[0] for row in GNU_COMPATIBLE_PROGRAMS])
def test_address_originals_match_explicit_real_c_reference(name, source, tmp_path, monkeypatch):
    compiler = shutil.which(os.environ.get('PCC_C_ORACLE', 'cc'))
    assert compiler, 'an explicit real C reference compiler is required'
    _compare_address_positive(name, source, tmp_path, monkeypatch, compiler)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', EXTENSION_ORIGINALS, ids=[row[0] for row in EXTENSION_ORIGINALS])
def test_pcc_outer_statement_address_original_executes(name, source, tmp_path, monkeypatch, request):
    digest, _, _ = _check_extension_identity(name, source)
    binary, _original = _compile_address_original(name, source, tmp_path, monkeypatch)
    observation = _observe_address_run(binary, 'owned', tmp_path)
    request.node.user_properties.extend([('pcc_original_sha256', digest),
                                         ('pcc_original_c', source), ('pcc_owned_returncode', observation[0])])
    assert observation == (42, b'', b'')


@pytest.fixture(scope='module')
def gnu_address_reference():
    # The ordinary cross-compiler positives still use PCC_C_ORACLE as before.
    # For a GNU-specific diagnostic honor that same explicit driver unless
    # an operator supplies a separate GNU reference. Never silently replace
    # an explicitly selected non-GNU compiler or count its failure as GCC.
    configured = os.environ.get('PCC_GNU_C_ORACLE') or os.environ.get('PCC_C_ORACLE', 'cc')
    compiler = shutil.which(configured)
    assert compiler, f'GNU C reference compiler not found: {configured!r}'
    env = dict(os.environ, LC_ALL='C')
    version = subprocess.run([compiler, '-v'], capture_output=True, timeout=10, env=env)
    ident = version.stdout + version.stderr
    assert version.returncode == 0 and re.search(rb'^gcc version [0-9]+\.', ident, re.MULTILINE), (
        'PCC_GNU_C_ORACLE must identify a real GNU GCC driver; '
        'PCC_C_ORACLE still controls the ordinary positive comparison', configured, ident)
    return compiler, ident, env


def _check_gnu_outer_address_rejection(built, original, line, column, reference):
    assert built.returncode == 1 and built.stdout == b'' and not reference.exists(), (
        built.returncode, built.stdout, built.stderr)
    text = built.stderr.decode('utf-8', errors='replace')
    expected = f"{original}:{line}:{column}: error: lvalue required as unary '&' operand"
    rows = text.splitlines()
    assert rows.count(expected) == 1, (expected, text)
    # A matching primary diagnostic plus an ICE, linker complaint, or
    # other unclassified failure is not the promised GNU rejection. Allow
    # optional ordinary GCC source/caret context without fixing its spacing.
    source_line = original.read_text(encoding='utf-8').splitlines()[line - 1]
    for row in rows:
        if row == expected or row == f"{original}: In function 'main':" or not row.strip():
            continue
        context = re.fullmatch(r'\s*([0-9]+)\s*\| (.*)', row)
        if context is not None and int(context.group(1)) == line and context.group(2) == source_line:
            continue
        assert re.fullmatch(r'\s*\|[ ~^]*\^[ ~^]*', row), text


@pytest.mark.parametrize('kind', ('wrong-column', 'wrong-file', 'wrong-error', 'extra-error',
                                  'signal', 'success', 'linked-output', 'unexpected-stdout',
                                  'ice', 'unimplemented', 'stray-linker', 'false-source-snippet'))
def test_gnu_outer_address_rejection_is_not_any_failed_build(kind, tmp_path):
    original = tmp_path / 'original.c'
    reference = tmp_path / 'reference'
    original.write_text('12345678&', encoding='utf-8')
    expected = f"{original}:1:9: error: lvalue required as unary '&' operand"
    diagnostic, code, stdout = expected, 1, b''
    if kind == 'wrong-column':
        diagnostic = diagnostic.replace(':1:9:', ':1:8:')
    elif kind == 'wrong-file':
        diagnostic = diagnostic.replace('original.c', 'other.c')
    elif kind == 'wrong-error':
        diagnostic = f'{original}:1:9: error: unknown type name'
    elif kind == 'extra-error':
        diagnostic += '\ncollect2: error: link failed'
    elif kind == 'ice':
        diagnostic += '\ncc1: internal compiler error: unexpected condition'
    elif kind == 'unimplemented':
        diagnostic += '\ncc1: sorry, unimplemented: unrelated expression'
    elif kind == 'stray-linker':
        diagnostic += '\n/usr/bin/ld: cannot find library'
    elif kind == 'false-source-snippet':
        diagnostic += '\n    1 | different source'
    elif kind == 'signal':
        code = -9
    elif kind == 'success':
        code = 0
    elif kind == 'linked-output':
        reference.write_bytes(b'output')
    elif kind == 'unexpected-stdout':
        stdout = b'unexpected'
    built = subprocess.CompletedProcess(['gcc'], code, stdout, (diagnostic+'\n').encode())
    with pytest.raises(AssertionError):
        _check_gnu_outer_address_rejection(built, original, 1, 9, reference)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', EXTENSION_ORIGINALS, ids=[row[0] for row in EXTENSION_ORIGINALS])
def test_gnu_rejects_frozen_outer_statement_address(name, source, tmp_path, gnu_address_reference, request):
    digest, line, column = _check_extension_identity(name, source)
    compiler, version, env = gnu_address_reference
    original = tmp_path / (name + '.c')
    original.write_bytes(source.encode('utf-8'))
    reference = tmp_path / 'reference'
    command = [compiler, '-std=gnu11', '-O0', str(original), '-o', str(reference)]
    built = subprocess.run(command, capture_output=True, timeout=30, env=env)
    (tmp_path / 'reference-build.stdout.bin').write_bytes(built.stdout)
    (tmp_path / 'reference-build.stderr.bin').write_bytes(built.stderr)
    # Keep the actual original and diagnostic even in CI artifacts that only
    # retain the junit report, not arbitrary pytest temporary directories.
    request.node.user_properties.extend([
        ('gnu_original_sha256', digest), ('gnu_original_c', source),
        ('gnu_driver_version', version.decode('utf-8', errors='replace')),
        ('gnu_command', json.dumps(command)), ('gnu_returncode', built.returncode),
        ('gnu_stdout_hex', built.stdout.hex()), ('gnu_stderr_hex', built.stderr.hex()),
    ])
    _check_gnu_outer_address_rejection(built, original, line, column, reference)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', INNER_ADDRESS_CONTROLS, ids=[row[0] for row in INNER_ADDRESS_CONTROLS])
def test_inner_statement_address_control_matches_same_gnu_driver(name, source, tmp_path, monkeypatch, gnu_address_reference):
    compiler, _version, env = gnu_address_reference
    _compare_address_positive(name, source, tmp_path, monkeypatch, compiler, env=env)


# Retain the exact originally authored whole-VLA pointer program as a legal
# but unsupported variably-modified pointer declaration, not a positive claim.
POINTER_TO_VLA_ORIGINAL = 'int main(void){int n=3;int a[n];int(*p)[n]=&a;if(sizeof(*p)!=sizeof(a))return 1;return 42;}'


BOUNDARY_ORIGINALS = (
    ('generic-unmodeled-completed-scalar-continuation', 'struct S{int a[2];int b[3];};int main(void){return _Generic(&(struct S){.a[0]=1,2,{[2]=3}},struct S*:42,default:1);}', 'c11', 'nested initializer continuation type query'),
    ('generic-unmodeled-array-element', 'int main(void){int a[1];return _Generic(a[0],int:42,default:1);}', 'c11', 'unsupported _Generic controlling expression type inference'),
    ('generic-unmodeled-field-arithmetic', 'struct S{double d;};int main(void){struct S s;return _Generic(s.d+0,double:42,default:1);}', 'c11', 'unsupported _Generic controlling expression type inference'),
    ('generic-unmodeled-field-conditional', 'struct S{double d;};int main(void){struct S s;return _Generic(1?s.d:0,double:42,default:1);}', 'c11', 'unsupported _Generic controlling expression type inference'),
    ('whole-vla-address-stride', 'int main(void){int n=3;int a[n];if((char*)(&a+1)-(char*)&a!=sizeof(a))return 1;return 42;}', 'c11', 'whole VLA object address requires variably modified pointer semantics'),
    ('pointer-to-vla', POINTER_TO_VLA_ORIGINAL, 'c11', "Not a constant expression: identifier 'n'"),
)


@pytest.mark.parametrize('name,source,standard,message', BOUNDARY_ORIGINALS)
def test_legal_boundary_original_retains_explicit_diagnostic(name, source, standard, message):
    with pytest.raises(CodegenError, match=message):
        compile_unit(source, name+'.c')


@pytest.mark.integration
@pytest.mark.parametrize('name,source,standard,message', BOUNDARY_ORIGINALS)
def test_legal_boundary_original_is_valid_real_c_reference(name, source, standard, message, tmp_path):
    original = tmp_path / (name+'.c')
    original.write_text(source, encoding='utf-8', newline='')
    compiler = shutil.which(os.environ.get('PCC_C_ORACLE', 'cc'))
    assert compiler, 'an explicit real C reference compiler is required'
    reference = tmp_path / 'reference'
    command = [compiler, '-std='+standard, '-O0', str(original), '-o', str(reference)]
    built = subprocess.run(command, capture_output=True, timeout=30)
    (tmp_path / 'reference-build.stdout.bin').write_bytes(built.stdout)
    (tmp_path / 'reference-build.stderr.bin').write_bytes(built.stderr)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(reference)], capture_output=True, timeout=10)
    (tmp_path / 'reference.stdout.bin').write_bytes(run.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(run.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({
        'command': command, 'returncode': run.returncode,
        'stdout_hex': run.stdout.hex(), 'stderr_hex': run.stderr.hex(),
    }, indent=2))
    assert (run.returncode, run.stdout, run.stderr) == (42, b'', b'')


@pytest.mark.parametrize('source', (
    'struct S{int*next;int unused;};int*probe(struct S*p){return &({int key=0;_Generic(key,int:*p,default:0);}).next[0];}',
    'struct S{int*next;int unused;};int*probe(struct S*p){return &({({int key=0;_Generic(key,int:*p,default:0);});}).next[0];}',
    'struct S{int*next;int unused;};int*probe(struct S*p){return &({*p;}).next[0];}',
    'struct S{int*next;int unused;};int*probe(struct S*p){return &p[0].next[0];}',
    'struct S{int*next;int unused;};int*probe(struct S*p){return &(*p).next[0];}',
    'struct I{int*next;int unused;};struct O{struct I i;};int*probe(struct O*p){return &p->i.next[0];}',
    'struct S{int n;};struct I{struct S*next;int unused;};struct O{struct I i;};int*probe(struct O*p){return &p->i.next->n;}',
))
def test_pointer_bases_load_required_pointer_without_intermediate_aggregate(source):
    _generator, ir, _object = compile_unit(source, 'nested-pointer-address.c')
    assert not re.search(r'\bload\s+(?:%|\{)', ir)
    assert not re.search(r'\bload\s+i32(?:\s|,)', ir)
    # The pointer parameter slot and selected pointer member are real reads;
    # suppressing both would be an equally incorrect no-load implementation.
    assert len(re.findall(r'\bload\b', ir)) == 2


@pytest.mark.parametrize('source,pointer_loads', (
    ('struct S{int*next;int unused;};int**probe(struct S*p){return &({int key=0;_Generic(key,int:*p,default:0);}).next;}', 1),
    ('struct S{int*next;int unused;};int*probe(struct S*p){return ({int key=0;_Generic(key,int:*p,default:0);}).next;}', 2),
))
def test_scoped_generic_field_address_and_value_preserve_selected_lvalue(source, pointer_loads):
    _generator, ir, _object = compile_unit(source, 'scoped-generic-field.c')
    assert not re.search(r'\bload\s+(?:%|\{)', ir)
    assert not re.search(r'\bload\s+i32(?:\s|,)', ir)
    assert len(re.findall(r'\bload\b', ir)) == pointer_loads


@pytest.mark.parametrize('source', (
    'int main(void){int*p=&((int[2]){({int x=42;x;}),0})[0];return *p;}',
    'int main(void){int*p=&_Generic(0,int:(int[2]){({int x=42;x;}),0},default:(int[2]){0,0})[0];return *p;}',
))
def test_scoped_compound_array_initializer_never_loads_whole_array(source):
    _generator, ir, _object = compile_unit(source, 'compound-array-scope.c')
    assert not re.search(r'\bload\s+\[', ir)


@pytest.mark.parametrize('source', (
    'int main(void){int*p=&((int[2]){42,0}+0)[0];return *p;}',
    'int read(int*p){return *p;}int main(void){return read((int[2]){42,0});}',
    'int main(void){int*p=&(0,(int[2]){42,0})[0];return *p;}',
    'int f(int c){return *(&(c?(int[2]){42,0}:(int[2]){1,0})[0]);}int main(void){return f(1);}',
))
def test_value_context_array_wrappers_never_copy_whole_array(source):
    _generator, ir, _object = compile_unit(source, 'value-context-array.c')
    assert not re.search(r'\bload\s+\[', ir)


def test_unmaterialized_nested_vla_query_never_invents_static_extent():
    source = 'int main(void){int n=3;return sizeof(*(&({int a[2][n];a;})));}'
    with pytest.raises(CodegenError, match='variable array query requires an existing runtime extent'):
        compile_unit(source, 'unmaterialized-vla-query.c')


@pytest.mark.parametrize('source,message', (
    ('int main(void){return sizeof(&({int x=missing;x;}));}', 'missing'),
    ('struct S{struct{int x;int y;};int b[3];};int main(void){return _Generic(&(struct S){.x=1,{[2]=3}},struct S*:42,default:1);}', 'nested initializer continuation type query'),
    ('struct S{int a[2];int b[3];};int main(void){return _Generic(&(struct S){.a=1,{[2]=3}},struct S*:42,default:1);}', 'nested initializer continuation type query'),
    ('int main(void){return _Generic(&(int[3]){[2 ... 1]=0},int(*)[3]:42,default:1);}', 'range is reversed'),
    ('struct S{int a[2];int b[3];};int main(void){return _Generic(&(struct S){.a[0]=1,{[2]=3}},struct S*:42,default:1);}', 'nested initializer continuation type query'),
    ('int take(int*);int main(void){return _Generic(&(int[1]){take((int[2]){[missing]=0})},int(*)[1]:42,default:1);}', 'missing'),
    ('int main(void){return _Generic(&(int[2]){[missing]=0},int(*)[2]:42,default:1);}', 'missing'),
    ('int main(void){int n=1;return _Generic(&(int[2]){[n]=0},int(*)[2]:42,default:1);}', 'integer constant expression'),
    ('struct S{int n;};int main(void){return _Generic(&(struct S[2]){[0].missing=0},struct S(*)[2]:42,default:1);}', 'member designator'),
    ('int main(void){return _Generic(&(int[2]){[2]=0},int(*)[2]:42,default:1);}', 'outside the declared extent'),
    ('int main(void){return _Generic(&(int[1][2]){{[missing]=0}},int(*)[1][2]:42,default:1);}', 'missing'),
    ('int main(void){return _Generic(&(int[]){missing,0},int(*)[2]:42,default:1);}', 'missing'),
    ('int main(void){return _Generic(&(int[2]){missing,0},int(*)[2]:42,default:1);}', 'missing'),
    ('int main(void){return sizeof(&({int a[1];int*p=&a[missing];p;}));}', 'missing'),
    ('int main(void){return sizeof(&({int x;int*p=&_Generic(missing,default:x);p;}));}', 'missing'),
    ('int main(void){return sizeof(&({_Static_assert(0,"no");int x;x;}));}', 'static assertion failed'),
    ('int main(void){return sizeof(&({missing;int x;x;}));}', 'missing'),
    ('int main(void){return sizeof(&({int x;x=42;}));}', 'address-only lvalue shape'),
    ('enum E{A};int main(void){return sizeof(&({A;}));}', 'object or function'),
    ('int A;int main(void){enum E{A};return sizeof(&A);}', 'object or function'),
))
def test_unevaluated_local_queries_still_check_names_and_lvalue_rules(source, message):
    with pytest.raises((CodegenError, ValueError), match=message):
        compile_unit(source, 'invalid-local-query.c')


def test_query_binding_preserves_module_and_emission_identity():
    generator = CCodeGenerator(translation_unit_name='pure-query.c',
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    # Parse one translation unit so Saved retains its parser typedef binding.
    # Only emit the declaration prefix: the query below remains type-only.
    tree = make_c_parser().parse('int side(void);typedef int K;typedef K Saved;'
                                 'int probe(void){return sizeof(&({typedef long long K;Saved x=side();x;}));}')
    query = tree.ext[-1].body.block_items[0].expr.expr
    tree.ext = tree.ext[:-1]
    generator.generate_code(tree)
    before = (str(generator.module), tuple(generator.module.globals), generator._scope_id_counter,
              generator._current_scope_id, generator.env, generator._decl_ast_types, generator._typedef_ast_types)
    dtype = generator._infer_sizeof_operand_ir_type(query)
    assert str(dtype.pointee) == 'i32'
    after = (str(generator.module), tuple(generator.module.globals), generator._scope_id_counter,
             generator._current_scope_id, generator.env, generator._decl_ast_types, generator._typedef_ast_types)
    assert before == after


def test_query_layout_gap_is_reported_before_module_mutation():
    generator = CCodeGenerator(translation_unit_name='pure-layout-query.c',
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    tree = make_c_parser().parse('int f(void){return sizeof(&({int a[(int)sizeof(struct{int n;})];a;}));}')
    query = tree.ext[0].body.block_items[0].expr.expr
    before = (str(generator.module), tuple(generator.module.globals), generator._scope_id_counter)
    with pytest.raises(CodegenError, match='fresh aggregate definitions'):
        generator._infer_sizeof_operand_ir_type(query)
    assert (str(generator.module), tuple(generator.module.globals), generator._scope_id_counter) == before


@pytest.mark.parametrize('source', (
    'int main(void){int a[1];return _Generic(a[0],int:42,default:1);}',
    'struct S{double d;};int main(void){struct S s;return _Generic(s.d+0,double:42,default:1);}',
    'struct S{double d;};int main(void){struct S s;return _Generic(1?s.d:0,double:42,default:1);}',
))
def test_unmodeled_generic_control_does_not_silently_select_default(source):
    # Valid C with a currently unmodeled Generic expression-key shape: default
    # is not permission to substitute a missing controlling type or operand.
    with pytest.raises(CodegenError, match='unsupported _Generic controlling expression type inference'):
        compile_unit(source, 'generic-unmodeled-control.c')


@pytest.mark.parametrize('target,element', (
    ('x86_64-unknown-linux-gnu', 'int'),
    ('aarch64-unknown-linux-gnu', 'unsigned int'),
    ('arm64-apple-darwin', 'int'),
    ('x86_64-pc-windows-msvc', 'unsigned short'),
))
def test_generic_wide_string_address_uses_target_c_integer_identity(target, element):
    generator = CCodeGenerator(translation_unit_name='wide-generic-query.c',
                              pass_ctx=PassContext(opt_level=0, target_triple=target))
    source = 'int f(void){return _Generic(&L"xy",'+element+'(*)[3]:42,default:1);}'
    tree = make_c_parser().parse(source)
    query = tree.ext[0].body.block_items[0].expr
    before = str(generator.module)
    assert generator._select_generic_association(query).value == '42'
    assert str(generator.module) == before


def test_struct_initializer_default_normalization_keeps_recursive_contract():
    generator = CCodeGenerator(translation_unit_name='normalization-contract.c',
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    tree = make_c_parser().parse('struct S{int*p;int a[2];};'
                                 'struct S value={{(int[2]){[1]=0}},{[1]=0}};')
    declaration = tree.ext.pop()
    generator.generate_code(tree)
    dtype = generator._resolve_ast_type(declaration.type)
    implicit = generator._normalize_struct_init_list(declaration.init, dtype)
    explicit = generator._normalize_struct_init_list(declaration.init, dtype, recursive=True)
    shallow = generator._normalize_struct_init_list(declaration.init, dtype, recursive=False)
    # Node inherits object.__repr__; two separately constructed but
    # equivalent InitLists do not have the same address. Compare the owned
    # AST's named, ordered children and scalar attributes instead.
    def ast_text(node):
        out = io.StringIO()
        node.show(out, attrnames=True, nodenames=True, showcoord=True)
        return out.getvalue()

    assert ast_text(implicit) == ast_text(explicit)
    assert shallow.exprs[0] is declaration.init.exprs[0]
    assert shallow.exprs[1] is declaration.init.exprs[1]


def test_struct_initializer_default_groups_brace_elided_array_rows():
    # The original designated-pointer/array fixture can retain its child
    # objects in both modes. Here recursion must actually regroup the same
    # four source constants; a default that silently becomes shallow fails.
    from pcc.frontends.c.ast import c_ast

    generator = CCodeGenerator(translation_unit_name='normalization-rows.c',
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    tree = make_c_parser().parse('struct S{int a[2][2];int tag;};'
                                 'struct S value={{1,2,3,4},5};')
    declaration = tree.ext.pop()
    generator.generate_code(tree)
    dtype = generator._resolve_ast_type(declaration.type)
    source_array, source_tag = declaration.init.exprs
    constants = tuple(source_array.exprs)
    assert [item.value for item in constants] == ['1', '2', '3', '4']
    for options in ({}, {'recursive': True}):
        array, tag = generator._normalize_struct_init_list(declaration.init, dtype, **options).exprs
        assert len(array.exprs) == 2 and all(isinstance(row, c_ast.InitList) for row in array.exprs)
        assert [[item.value for item in row.exprs] for row in array.exprs] == [['1','2'], ['3','4']]
        assert tag.value == '5'
    shallow = generator._normalize_struct_init_list(declaration.init, dtype, recursive=False)
    assert shallow.exprs[0] is source_array and shallow.exprs[1] is source_tag
    assert tuple(source_array.exprs) == constants


@pytest.mark.parametrize('qualifier', ('', 'const '))
def test_ssa_object_is_not_a_designator_integer_constant_expression(qualifier):
    from pcc.frontends.c.passes import PassPipeline
    from pcc.frontends.c.passes.context import AllocStrategy

    source = ('int main(void){'+qualifier+'int n=1;'
              'return _Generic(&(int[2]){[n]=0},int(*)[2]:42,default:1);}')
    tree = make_c_parser().parse(source)
    context = PassContext(opt_level=0, target_triple=TARGET)
    PassPipeline.minimal().run_high_tier(tree, context)
    assert context.get_var('main', 'n').alloc_strategy == AllocStrategy.SSA
    generator = CCodeGenerator(translation_unit_name='ssa-designator.c', pass_ctx=context)
    with pytest.raises(CodegenError, match='integer constant expression'):
        generator.generate_code(tree)
