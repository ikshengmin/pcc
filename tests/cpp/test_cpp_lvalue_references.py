"""Reference identity/direct binding: N4659 [dcl.ref], [dcl.init.ref], [over.ics.ref].

The two StrPair declarations are exact TinyXML2 11.0.0 h:192-193 originals,
Zlib license; source identity is projects/tinyxml2-11.0.0/pcc-fixture.json.
Retaining their declarations is not implementing class copying or assignment.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, qualify_const
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

STRPAIR_DECLARATIONS = ('    StrPair( const StrPair& other );\t// not supported\r\n'
                       '    void operator=( const StrPair& other );\t// not supported, use TransferTo()')
STRPAIR_SOURCE = 'class StrPair{public:StrPair(){}private:\n'+STRPAIR_DECLARATIONS+'\n};'
CASES = (
    ('parameter-alias-write', 'void set(int&n){n=42;}int cpp_test_entry(){int n=7;set(n);return n;}'),
    ('same-object-two-references', 'int sum(int&a,int&b){a=20;b+=22;return a;}int cpp_test_entry(){int n=0;return sum(n,n);}'),
    ('pointer-variable-is-referent', 'void set(int*&p,int*q){p=q;}int cpp_test_entry(){int n=42;int*p=nullptr;set(p,&n);return *p;}'),
    ('floating-reference-storage', 'void add(double&n){n+=0.5;}int cpp_test_entry(){double n=41.5;add(n);return int(n);}'),
    ('wide-reference-storage', 'void add(unsigned long long&n){n+=1ULL;}int cpp_test_entry(){unsigned long long n=4294967295ULL;add(n);if(n==4294967296ULL)return 42;return 1;}'),
    ('reference-not-pointer-overload', 'int f(int&n){return n;}int f(int*p){return *p;}int cpp_test_entry(){int n=21;return f(n)+f(&n);}'),
    ('const-reference-read', 'int read(const int&n){return n;}int cpp_test_entry(){const int n=42;return read(n);}'),
    ('mutable-reference-overload', 'int f(int&n){return n;}int f(const int&n){return 1;}int cpp_test_entry(){int n=42;return f(n);}'),
    ('const-reference-overload', 'int f(int&n){return 1;}int f(const int&n){return n;}int cpp_test_entry(){const int n=42;return f(n);}'),
    ('top-level-const-pointer-reference', 'int read(int*const&p){return *p;}int cpp_test_entry(){int n=42;int*p=&n;return read(p);}'),
    ('reference-to-row-pointer-stride',
     'void advance(int(*&p)[3]){++p;}'
     'int cpp_test_entry(){int a[2][3]={{1,2,3},{42,5,6}};int(*p)[3]=a;advance(p);return (*p)[0];}'),
    ('effectful-address-once', 'int calls=0;int*choose(int*p){calls+=1;return p;}void set(int&n){n=42;}int cpp_test_entry(){int n=0;set(*choose(&n));if(calls!=1)return 1;return n;}'),
    ('effectful-subscript-once', 'void set(int&n){n=42;}int cpp_test_entry(){int a[2]={};int i=0;set(a[i++]);if(i!=1)return 1;return a[0];}'),
    ('prefix-increment-remains-lvalue', 'int read(int&n){return n;}int cpp_test_entry(){int n=41;return read(++n);}'),
    ('method-parameter-alias', 'struct S{void set(int&n){n=42;}};int cpp_test_entry(){S s;S*p=&s;int n=0;p->set(n);return n;}'),
    ('constructor-scalar-reference-parameter',
     'struct S{int n;S(int&);};S::S(int&x):n(x){x+=1;}'
     'int cpp_test_entry(){int n=41;S s(n);if(s.n!=41)return 1;return n;}'),
    ('scalar-default-after-reference', 'void set(int&n,int value=42){n=value;}int cpp_test_entry(){int n=0;set(n);return n;}'),
    ('reference-entity-decltype-and-sizeof', 'int read(int&n){using R=decltype(n);if(sizeof(R)!=sizeof(int))return 1;return n;}int cpp_test_entry(){int n=42;return read(n);}'),
    ('reference-typedef-const-ignored', 'using R=int&;void set(const R n){n=42;}int cpp_test_entry(){int n=0;set(n);return n;}'),
    ('nominal-enum-reference', 'enum E{Answer=42};int read(E&n){return n;}int cpp_test_entry(){E n=Answer;return read(n);}'),
    ('original-private-copy-declarations', STRPAIR_SOURCE+'int cpp_test_entry(){StrPair s;return 42;}'),
)
# Old valid unsupported inputs are retained byte-for-byte as separate TUs.
FORMER_ORIGINALS = (
    ('original-reference-result-prototype', 'int& f();',
     'int value=42;int&f(){return value;}int cpp_test_entry(){return f();}'),
    ('original-reference-definition', 'int f(int &x){return x;}', 'int f(int&);int cpp_test_entry(){int n=42;return f(n);}'),
    ('original-reference-prototype', 'int f(int &p);', 'int f(int&n){return n;}int cpp_test_entry(){int n=42;return f(n);}'),
    ('original-grouped-reference-parameter', 'int function(int (&value));', 'int function(int&n){return n;}int cpp_test_entry(){int n=42;return function(n);}'),
    ('original-reference-typedef', 'typedef int (&Reference);', 'typedef int (&Reference);int read(Reference n){return n;}int cpp_test_entry(){int n=42;return read(n);}'),
    ('original-c-linkage-reference', 'extern "C" int f(int&);', 'extern "C" int f(int&n){return n;}int cpp_test_entry(){int n=42;return f(n);}'),
    ('original-reference-sizeof', 'int f(){return sizeof(int&);}', 'int f();int cpp_test_entry(){return f()+38;}'),
)
PROGRAMS = tuple((name,((name+'.cpp',source),)) for name,source in CASES) + tuple(
    (name,(('original.cpp',source),('entry.cpp',entry))) for name,source,entry in FORMER_ORIGINALS)


@pytest.mark.parametrize('name,units',PROGRAMS,ids=[row[0] for row in PROGRAMS])
def test_reference_original_artifacts_are_stable(owned_only,name,units):
    assert compile_host_cpp(units)==compile_host_cpp(units)


def test_reference_identity_and_cv_are_not_pointer_identity():
    assert ReferenceType('int') != PointerType('int')
    assert ReferenceType('int') != ReferenceType(ConstType('int'))
    assert ReferenceType(PointerType('int')) != ReferenceType(PointerType(ConstType('int')))
    dtype=ReferenceType('int')
    assert qualify_const(dtype) is dtype
    assert len({dtype,ReferenceType('int')})==1


def test_reference_signature_mangling_and_c_projection():
    parser=CppParser()
    tree=parser.parse('int f(int&);int f(const int&);int same(int&,int&);')
    assert [node.name for node in tree.ext]==['_Z1fRi','_Z1fRKi','_Z4sameRiS_']
    assert all(isinstance(node.type.args.params[0].type,c_ast.PtrDecl) for node in tree.ext)
    assert isinstance(parser.scope.names['f'].overloads[0].parameters[0],ReferenceType)
    assert tree.ext[1].type.args.params[0].type.type.quals==['const']


@pytest.mark.parametrize('access',('public','private'))
def test_copy_declarations_have_real_identity_without_copy_execution(access):
    parser=CppParser()
    tree=parser.parse('class StrPair{'+access+':\n'+STRPAIR_DECLARATIONS+'\n};')
    definition=parser.scope.names['StrPair'].dtype.definition
    copy,=definition.constructors.overloads
    assignment,=definition.scope.names['operator='].overloads
    assert copy.c_name=='_ZN7StrPairC1ERKS_'
    assert assignment.c_name=='_ZN7StrPairaSERKS_'
    assert copy.access==assignment.access==access
    assert not copy.defined and not assignment.defined
    assert isinstance(copy.parameters[0],ReferenceType)
    assert isinstance(copy.parameters[0].referent,ConstType)
    assert copy.parameters[0].referent.base is definition.dtype
    assert all(not isinstance(node,c_ast.FuncDef) for node in tree.ext)


@pytest.mark.parametrize('source,code',(
    ('int f(int&); int f(long){return 1;} int g(){int n=0;return f(n=42);}',
     'cpp.unsupported.references'),
    ('int f(int&);int f(int);int g(){int n=0;return f(n=42);}',
     'cpp.ambiguous-overload'),
    ('struct S{int f(int&);int f(long){return 1;}};int g(S*p){int n=0;return p->f(n=42);}',
     'cpp.unsupported.references'),
    ('struct S{static int f(int&);static int f(long){return 1;}};int g(){int n=0;return S::f(n=42);}',
     'cpp.unsupported.references'),
    ('struct S{S(int&);S(long){}};int g(){int n=0;S s(n=42);return 0;}',
     'cpp.unsupported.references'),
    ('using Size=decltype(sizeof(0)); void* operator new(Size,void*) noexcept; struct S{S(){}}; void* operator new(Size,S*&); void f(S*p){new(p) S;}',
     'cpp.unsupported.placement-allocation-overload'),
    ('using Size=decltype(sizeof(0));void* operator new(Size,void*) noexcept;struct S{S(){}};void* operator new(Size,S*&);void f(S*p,S*q){new(p=q) S;}',
     'cpp.unsupported.placement-allocation-overload'),
    ('void f(int&);int g(){f(42);return 0;}','cpp.no-viable-overload'),
    ('void f(int&);int g(){const int n=42;f(n);return 0;}','cpp.no-viable-overload'),
    ('void f(const int&);int g(){f(42);return 0;}','cpp.unsupported.reference-temporaries'),
    ('void f(const int&);void f(long);int g(){f(42);return 0;}','cpp.unsupported.reference-temporaries'),
    ('void f(const int*&);int g(){int n=0;int*p=&n;f(p);return 0;}','cpp.no-viable-overload'),
    ('void f(const int*const&);int g(){int n=0;int*p=&n;f(p);return 0;}','cpp.unsupported.reference-temporaries'),
    ('int f(int&n){n=42;return n;}int f(int);int g(){int n=0;return f(n);}','cpp.ambiguous-overload'),
    ('void f(const int&n){n=42;}','cpp.const-assignment'),
    ('using R=int&;int f(){int n=42;return R(n);}','cpp.unsupported.references'),
    ('int n;int&r=n;','cpp.unsupported.references'),
    ('int g(){int n=0;int&r=n;return r;}','cpp.unsupported.references'),
    ('struct S{int&r;};','cpp.unsupported.references'),
    ('void f(int&,...);','cpp.unsupported.references'),
    ('void f(int&&);','cpp.unsupported.references'),
    ('void f(void&);','cpp.invalid-reference-type'),
    ('void f(int&*);','cpp.invalid-reference-type'),
    ('using A=int&[2];','cpp.invalid-array-element'),
    ('struct S{void operator=(const S&){} };','cpp.unsupported.class-reference-values'),
    ('struct S{static void operator=(int);};','cpp.invalid-assignment-operator'),
    ('struct S{void operator=(int=0);};','cpp.invalid-assignment-operator'),
    ('void operator=(int);','cpp.invalid-assignment-operator'),
    ('int n;void f(int&r=n);','cpp.unsupported.default-argument-context'),
))
def test_invalid_bindings_and_unimplemented_lifetimes_stay_explicit(source,code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code==code
