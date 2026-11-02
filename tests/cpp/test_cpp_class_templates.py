"""Original bounded class-template obligations, N4659 [temp.class]/[temp.inst].

These are typed aggregate/layout consumers, not a claim that DynArray's array
members, constructors, inline destructor, or allocation are implemented.
"""
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import PointerType, _mangle_type
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

CASES = (
    ('qualified-unevaluated-member-completes-layout', 'template<class T>struct Box{T value;};using B=Box<int>;int f(){return sizeof(B::value);}int cpp_test_entry(){if(f()==sizeof(int))return 42;return 1;}'),
    ('qualified-member-decltype-completes-layout', 'template<class T>struct Box{T value;};using B=Box<const int>;using V=decltype(B::value);int cpp_test_entry(){V value=42;return value;}'),
    ('namespace-reopening-renamed-parameters', 'namespace N{template<class T>struct Box;}namespace N{template<class U>struct Box{U value;};}int cpp_test_entry(){N::Box<int>b{42};return b.value;}'),
    ('two-namespace-specializations', 'namespace N{template<class T>struct Box{T value;};}int read(N::Box<int>*a,N::Box<long>*b){return a->value+b->value;}int cpp_test_entry(){N::Box<int>a{20};N::Box<long>b{22};return read(&a,&b);}'),
    ('associated-type-argument-beats-ordinary-void-pointer', 'namespace N{struct Tag{};} template<class T>struct Box{T*p;}; namespace N{int choose(Box<Tag>*p){return 42;}} int choose(void*p){return 1;} int g(){Box<N::Tag>b{nullptr};return choose(&b);}int cpp_test_entry(){return g();}'),
    ('associated-type-argument-without-layout', 'namespace N{struct Tag{};}template<class T>struct Box{T value;};namespace N{int choose(Box<Tag&>*p){return 42;}}int choose(void*p){return 1;}int cpp_test_entry(){Box<N::Tag&>*p=nullptr;return choose(p);}'),
    ('alias-namespace-is-not-associated', 'template<class T>struct Box{T value;};namespace N{using I=int;int choose(Box<I>*p){return 1;}}int choose(void*p){return 42;}int cpp_test_entry(){Box<N::I>b{0};return choose(&b);}'),
    ('pointer-only-defers-invalid-member-substitution', 'template<class T>struct Box{T*value;};using R=int&;int cpp_test_entry(){Box<R>*p=nullptr;if(p==nullptr)return 42;return 1;}'),
    ('scalar-member', 'template<class T>struct Box{T value;};int cpp_test_entry(){Box<int>b{42};return b.value;}'),
    ('namespace-qualified-aggregate', 'namespace tinyxml2{template<class T>struct Box{T value;};}int cpp_test_entry(){tinyxml2::Box<int>b{42};return b.value;}'),
    ('pointer-member', 'template<class T>struct Box{T*value;};int cpp_test_entry(){int n=42;Box<int>b{&n};return *b.value;}'),
    ('injected-pointer-to-self', 'template<class T>struct Node{T value;Node*next;};int cpp_test_entry(){Node<int>a{42,nullptr};Node<int>b{0,&a};return b.next->value;}'),
    ('definition-point-type-binding', 'using Value=int;namespace N{template<class T>struct Box{Value value;T*unused;};}namespace M{using Value=double;int read(){N::Box<int>b{42,nullptr};return b.value;}}int cpp_test_entry(){return M::read();}'),
    ('mixed-parameter-identity', 'template<class T,int N>struct Box{T value;};int read(Box<int,1>*p){return p->value;}int read(Box<int,2>*p){return p->value+1;}int cpp_test_entry(){Box<int,2>b{41};return read(&b);}'),
    ('converted-integral-key', 'template<class T,unsigned N>struct Box{T value;};using A=Box<int,2>;using B=Box<int,1+1>;int read(A*p){return p->value;}int cpp_test_entry(){B b{42};return read(&b);}'),
    ('pointer-only-does-not-instantiate-void', 'template<class T>struct Box{T value;};int cpp_test_entry(){Box<void>*p=nullptr;if(p==nullptr)return 42;return 1;}'),
    ('forward-before-definition', 'template<class T>struct Box;Box<int>*p=nullptr;template<class U>struct Box{U value;};int cpp_test_entry(){Box<int>b{42};p=&b;return p->value;}'),
    ('const-member-type-argument', 'template<class T>struct Box{T value;};int cpp_test_entry(){Box<const int>b{42};return b.value;}'),
    ('mutable-through-const-object', 'template<class T>struct Box{mutable T value;};int cpp_test_entry(){const Box<int>b{0};b.value=42;return b.value;}'),
    ('private-layout-no-access', 'template<class T>class Box{T value;};int cpp_test_entry(){Box<int>b;return 42;}'),
    ('empty-nominal-classes', 'template<class T>struct Box{};int cpp_test_entry(){Box<int>b;return 42;}'),
)
PROGRAMS = tuple((name,((name+'.cpp',source),)) for name,source in CASES) + (
    ('retained-empty-template-declaration', (
        ('original.cpp','template<class T> struct Box {};'),
        ('entry.cpp','int cpp_test_entry(){return 42;}'),
    )),
    ('cross-tu-nominal-template-identity', (
        ('provider.cpp','namespace N{template<class T,int K>struct Box{T value;};}int read(N::Box<int,2>*p){return p->value;}'),
        ('entry.cpp','namespace N{template<class T,int K>struct Box{T value;};}int read(N::Box<int,2>*);int cpp_test_entry(){N::Box<int,2>b{42};return read(&b);}'),
    )),
)


@pytest.mark.parametrize('name,units',PROGRAMS,ids=[row[0] for row in PROGRAMS])
def test_class_template_original_artifacts_are_byte_stable(owned_only,name,units):
    assert compile_host_cpp(units)==compile_host_cpp(units)


def test_shared_registry_is_the_only_nominal_instance_cache():
    parser=CppParser()
    parser.parse('template<class T,int N>struct Box{T value;};using A=Box<int,2>;using B=Box<int,1+1>;using C=Box<int,3>;')
    a=parser.scope.names['A'].dtype
    b=parser.scope.names['B'].dtype
    c=parser.scope.names['C'].dtype
    assert a is b and a is not c
    assert not a.complete and not c.complete
    assert len(parser.instance_registry.entries)==2
    assert _mangle_type(a,[])=='3BoxIiLi2EE'
    assert _mangle_type(c,[])=='3BoxIiLi3EE'


def test_template_prefix_substitution_is_not_a_spelling_alias():
    parser=CppParser()
    parser.parse('namespace N{template<class T>struct Box{T value;};}using A=N::Box<int>;using B=N::Box<long>;')
    substitutions=[]
    a=parser.scope.names['A'].dtype
    b=parser.scope.names['B'].dtype
    assert _mangle_type(PointerType(a),substitutions)=='PN1N3BoxIiEE'
    # Namespace prefix=0, template prefix=1, Box<int>=2, pointer=3.
    assert _mangle_type(PointerType(b),substitutions)=='PNS0_IlEE'


@pytest.mark.parametrize('source,code',(
    ('namespace N{template<class T>struct Box;} using N::Box; template<class T>struct Box{T value;};','cpp.unsupported.class-templates'),
    ('template<class T>class Box{T value;};using B=Box<int>;int f(){return sizeof(B::value);}','cpp.member-access'),
    ('namespace N{template<class T>struct Box;} namespace M{using N::Box;template<class U>struct Box{U value;};} int f(){N::Box<int>b{42};return b.value;}','cpp.unsupported.class-templates'),
    ('template<class Box>struct Box;','cpp.template-parameter-shadowing'),
    ('template<int Box>struct Box;','cpp.template-parameter-shadowing'),
    ('template<class T>struct Box;template<class Box>struct Box;','cpp.template-parameter-shadowing'),
    ('using R=int&;template<class T>struct Box{R* value;};','cpp.invalid-reference-type'),
    ('template<class T>struct Box{T value;};int f(){Box<void>b;return 0;}','cpp.invalid-object-type'),
    ('template<class T>struct Box{Box value;};int f(){Box<int>b;return 0;}','cpp.incomplete-class'),
    ('template<class T>struct Box;int f(){Box<int>b;return 0;}','cpp.incomplete-class'),
    ('template<class T>struct Box{T* value;}; using R=int&; int f(){Box<R>b{nullptr};return 42;}','cpp.invalid-reference-type'),
    ('template<class T>struct Box{const T** value;};using R=int&;int f(){Box<R>b{nullptr};return 42;}','cpp.invalid-reference-type'),
    ('template<class T>class Box{T value;};int cpp_test_entry(){if(sizeof(Box<int>)==sizeof(int))return 42;return 1;}','cpp.unsupported.sizeof-class-layout'),
    ('template<class T>struct Box{};int cpp_test_entry(){if(sizeof(Box<int>)==1)return 42;return 1;}','cpp.unsupported.sizeof-class-layout'),
    ('template<class T>struct Box{void value;};','cpp.invalid-object-type'),
    ('template<class T>struct Box{Missing value;};','cpp.unsupported.syntax'),
    ('template<class T>struct Box{T value;};Box<void>b;','cpp.unsupported.static-class-lifetime'),
    ('template<class T>struct Box{Box value;};Box<int>b;','cpp.unsupported.static-class-lifetime'),
    ('template<class T>struct Box;Box<int>b;','cpp.unsupported.static-class-lifetime'),
    ('template<class T>struct Box{T value;T value;};','cpp.redeclaration'),
    ('template<class T>struct Box{int T;};','cpp.template-parameter-shadowing'),
    ('template<class T>struct Box{mutable const T value;};','cpp.invalid-mutable'),
    ('template<class T>class Box{T value;};int f(){Box<int>b;return b.value;}','cpp.member-access'),
    ('template<class T>struct Box{T value;};Box<42>b;','cpp.template-argument-kind'),
    ('template<int N>struct Box{};Box<int()>b;','cpp.unsupported.template-type-arguments'),
    ('template<class T,int N>struct Box{};Box<int>b;','cpp.template-argument-count'),
    ('template<class T>struct Box{};Box<int,2>b;','cpp.template-argument-count'),
    ('template<unsigned N>struct Box{};Box<-1>b;','cpp.narrowing-template-argument'),
    ('int n=2;template<int N>struct Box{};Box<n>b;','cpp.nonconstant-template-argument'),
    ('template<class T>struct Box{T f(){return missing();}};','cpp.undeclared'),
    ('template<class T>struct Box{T value=42;};','cpp.unsupported.class-templates'),
    ('template<class T>struct Box{T value[2];};','cpp.unsupported.array-bounds'),
    ('template<class T>struct Box{};template<>struct Box<int>{};','cpp.unsupported.templates'),
    ('template<class T>struct Box{};template<class T>struct Box{};','cpp.class-redefinition'),
    ('template<class T>struct Box{};Box b;','cpp.unsupported.class-templates'),
),ids=lambda item:item if item.startswith('cpp.') else None)
def test_invalid_or_explicitly_unimplemented_class_templates(source,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code==code
