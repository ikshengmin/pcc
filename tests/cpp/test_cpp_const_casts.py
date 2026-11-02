"""Pointer cv-only originals: N4659 [expr.const.cast]/1,3,5 and [conv.qual]/1-2.

SetInternedStr is the exact TinyXML2 11.0.0 h:167-170 body, Zlib license;
projects/tinyxml2-11.0.0/pcc-fixture.json records upstream source identity.
This declaration/observer fixture is not an upstream whole-project result.
"""
import hashlib
import pytest
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.const_casts import convert, qualification_shape
from pcc.frontends.cpp.parser import Expression
from pcc.frontends.cpp.types import ArrayType, ConstType, FunctionType, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

INTERNED_DECLARATION = ('    void SetInternedStr( const char* str ) {\r\n'
                       '        Reset();\r\n'
                       '        _start = const_cast<char*>(str);\r\n'
                       '    }')
INTERNED_SHA256 = 'c5e29ed993b1f20451c71eac585802235390107c74facf82f516c3616fefa76d'
INTERNED_PROGRAM = ('int resets=0;class StrPair{public:'+INTERNED_DECLARATION+
                    'void Reset(){resets+=1;}int Read(){return *_start;}private:char*_start;};'
                    'int cpp_test_entry(){StrPair s;StrPair*p=&s;char data[1]={42};'
                    'p->SetInternedStr(data);if(resets!=1)return 1;return p->Read();}')
CASES = (
    ('original-interned-body', INTERNED_PROGRAM),
    ('write-through-const-view-of-mutable-object', 'int cpp_test_entry(){int n=0;const int*p=&n;int*q=const_cast<int*>(p);*q=42;return n;}'),
    ('add-const-without-type-replacement', 'int cpp_test_entry(){int n=42;int*p=&n;const int*q=const_cast<const int*>(p);return *q;}'),
    ('pointer-to-const-pointer-level', 'int cpp_test_entry(){int a=1;int b=42;int*p=&a;int*const*view=&p;int**q=const_cast<int**>(view);*q=&b;return *p;}'),
    ('deep-added-const-levels', 'int cpp_test_entry(){int n=42;int*p=&n;int**pp=&p;const int*const*q=const_cast<const int*const*>(pp);return **q;}'),
    ('typed-null-pointer', 'int cpp_test_entry(){const int*p=nullptr;int*q=const_cast<int*>(p);if(q==nullptr)return 42;return 1;}'),
    ('void-pointee-cv-only', 'int cpp_test_entry(){int n=42;void*p=&n;const void*q=p;void*r=const_cast<void*>(q);if(r==p)return 42;return 1;}'),
    ('incomplete-pointee', 'struct O;O*strip(const O*p){return const_cast<O*>(p);}int cpp_test_entry(){const O*p=nullptr;if(strip(p)==nullptr)return 42;return 1;}'),
    ('nominal-enum-pointee', 'enum E{Answer=42};int cpp_test_entry(){E value=Answer;const E*p=&value;E*q=const_cast<E*>(p);return *q;}'),
    ('array-operand-decays', 'int cpp_test_entry(){int a[1]={42};int*p=const_cast<int*>(a);return *p;}'),
    ('pointer-to-array-shape', 'int cpp_test_entry(){int a[1][2]={{41,2}};const int(*p)[2]=a;int(*q)[2]=const_cast<int(*)[2]>(p);(*q)[0]+=1;return (*q)[0];}'),
    ('effectful-operand-once', 'int calls=0;const int*choose(int*p){calls+=1;return p;}int cpp_test_entry(){int n=42;int*p=const_cast<int*>(choose(&n));if(calls!=1)return 1;return *p;}'),
    ('unevaluated-operand-does-not-run',
     'int calls=0;const int*choose(int*p){calls+=1;return p;}'
     'int cpp_test_entry(){int n=42;if(sizeof(const_cast<int*>(choose(&n)))!=sizeof(int*))return 2;'
     'if(calls!=0)return 1;return 42;}'),
    ('const-member-this-view', 'struct S{int n;S*mutable_view()const{return const_cast<S*>(this);}};int cpp_test_entry(){S s{0};const S*p=&s;S*q=p->mutable_view();q->n=42;return s.n;}'),
    ('default-pointer-evaluated-at-call', 'int n=42;const int*p=&n;int f(int*q=const_cast<int*>(p)){return *q;}int cpp_test_entry(){return f();}'),
)
PROGRAMS = tuple((name,((name+'.cpp',source),)) for name,source in CASES) + (
    ('cross-tu-cv-only-result', (
        ('provider.cpp','int*strip(const int*p){return const_cast<int*>(p);}'),
        ('entry.cpp','int*strip(const int*);int cpp_test_entry(){int n=0;const int*p=&n;*strip(p)=42;return n;}'),
    )),
)


@pytest.mark.parametrize('name,units',PROGRAMS,ids=[row[0] for row in PROGRAMS])
def test_const_cast_original_artifacts_are_byte_stable(owned_only,name,units):
    assert compile_host_cpp(units)==compile_host_cpp(units)


def test_exact_original_interned_body_preserves_bytes():
    assert hashlib.sha256(INTERNED_DECLARATION.encode()).hexdigest()==INTERNED_SHA256
    CppParser().parse(INTERNED_PROGRAM)


def test_full_pointer_array_shape_keeps_bounds_and_every_pointer_level():
    a=PointerType(ArrayType(PointerType(ConstType('int')),3))
    b=PointerType(ArrayType(ConstType(PointerType('int')),3))
    assert qualification_shape(a)==qualification_shape(b)
    assert qualification_shape(a)!=qualification_shape(PointerType(ArrayType(PointerType('int'),4)))
    assert qualification_shape(a)!=qualification_shape(PointerType(ArrayType('int',3)))


def test_pointer_result_is_prvalue_and_drops_only_top_level_cv():
    parser=CppParser()
    parser.parse('')
    coord=Coord('cast.cpp',1,1)
    source=Expression(c_ast.ID('p',coord),PointerType(ConstType('int')),True)
    result=convert(parser,ConstType(PointerType('int')),source,coord)
    assert result.dtype==PointerType('int') and not result.lvalue
    assert result.node.expr is source.node
    assert result.static_address is None
    assert result.constant_fact.status=='UNMODELED'


def test_parsed_prvalue_target_const_is_not_entity_const():
    parser=CppParser()
    parser.parse('const int*p=nullptr;using Result=decltype(const_cast<int*const>(p));')
    assert parser.scope.names['Result'].dtype==PointerType('int')


def test_function_pointer_same_shape_is_still_invalid():
    parser=CppParser()
    parser.parse('')
    coord=Coord('function.cpp',1,1)
    dtype=PointerType(FunctionType('int',()))
    with pytest.raises(CppDiagnostic) as caught:
        convert(parser,dtype,Expression(c_ast.ID('p',coord),dtype),coord)
    assert caught.value.code=='cpp.invalid-const-cast'


@pytest.mark.parametrize('source,code',(
    ('int n;int*p=const_cast<int*>(&n);','cpp.unsupported.static-initialization-analysis'),
    ('int f(int n){return const_cast<int>(n);}','cpp.invalid-const-cast'),
    ('int*f(){return const_cast<int*>(nullptr);}','cpp.invalid-const-cast'),
    ('int*f(){return const_cast<int*>(0);}','cpp.invalid-const-cast'),
    ('void*f(const int*p){return const_cast<void*>(p);}','cpp.invalid-const-cast'),
    ('int*f(const void*p){return const_cast<int*>(p);}','cpp.invalid-const-cast'),
    ('char*f(const int*p){return const_cast<char*>(p);}','cpp.invalid-const-cast'),
    ('int*f(const int**p){return const_cast<int*>(p);}','cpp.invalid-const-cast'),
    ('struct A;struct B;B*f(const A*p){return const_cast<B*>(p);}','cpp.invalid-const-cast'),
    ('enum E{A};int*f(const E*p){return const_cast<int*>(p);}','cpp.invalid-const-cast'),
    ('void f(const int(*p)[2]){const_cast<int(*)[3]>(p);}','cpp.invalid-const-cast'),
    ('using F=int();void f(int*p){const_cast<F*>(p);}','cpp.invalid-const-cast'),
    ('int f(const int*p,int*q){const_cast<int*>(p)=q;return 0;}','cpp.not-lvalue'),
    ('int f(const int&n){return const_cast<int&>(n);}','cpp.unsupported.references'),
    ('int*f(const int*p){return const_cast<volatile int*>(p);}','cpp.unsupported.volatile'),
    ('template<class T>int*f(T x){return const_cast<int*>(x);}','cpp.unsupported.pointer-templates'),
    ('using P=int*;template<class T>int f(T x){const_cast<P>(x);return 0;}','cpp.unsupported.const-cast'),
))
def test_invalid_shapes_and_explicit_cast_boundaries(source,code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code==code
