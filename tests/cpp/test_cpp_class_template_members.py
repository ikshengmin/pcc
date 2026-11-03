"""Typed member Patterns: N4659 [temp.res], [temp.inst], [class.mfct].

Unused definitions still undergo definition-point checking. Only odr-used
bodies substitute dependent operations; original tokens are never replayed
per specialization. These obligations do not claim DynArray array/D1 support.
"""
import pytest
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.members import mangle_member
from pcc.frontends.cpp.constructors import _complete_name
from pcc.frontends.cpp.types import PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


RETAINED_DOT_CASES = (
    ('aggregate-getter', 'template<class T>struct Box{T value;T get(){return value;}};int cpp_test_entry(){Box<int>b{42};return b.get();}'),
    ('later-private-helper', 'template<class T>class Box{public:T get(){return helper();}private:static T helper(){return 42;}};int cpp_test_entry(){Box<int>b;return b.get();}'),
    ('parameter-shadowing', 'template<class T>struct Box{T value;void set(T value){this->value=value;}T get(){return value;}};int cpp_test_entry(){Box<int>b{0};b.set(42);return b.get();}'),
    ('const-overload', 'template<class T>struct Box{T value;T get(){return 1;}T get()const{return value;}};int cpp_test_entry(){const Box<int>b{42};return b.get();}'),
    ('mutable-const-body', 'template<class T>struct Box{mutable T value;T get()const{value=42;return value;}};int cpp_test_entry(){const Box<int>b{0};return b.get();}'),
    ('recursive-body', 'template<class T>struct Box{T sum(T n){if(n==0)return 0;return n+sum(n-1);}};int cpp_test_entry(){Box<int>b;return b.sum(8)+6;}'),
    ('pointer-comparison-body', 'template<class T>struct Box{T*value;int get(){if(value==nullptr)return 1;return *value;}};int cpp_test_entry(){int n=42;Box<int>b{&n};return b.get();}'),
    ('pointer-field-dereference', 'template<class T>struct Box{T*value;T get(){return *value;}void clear(){value=nullptr;}};int cpp_test_entry(){int n=42;Box<int>b{&n};int answer=b.get();b.clear();if(b.value!=nullptr)return 1;return answer;}'),
    ('constructor-later-member', 'template<class T>struct Box{Box(T value):value(value){}T get()const{return value;}T value;};int cpp_test_entry(){Box<int>b(42);return b.get();}'),
    ('constructor-const-member', 'template<class T>struct Box{const T value;Box(T n):value(n){}T get()const{return value;}};int cpp_test_entry(){const Box<int>b(42);return b.get();}'),
    ('constructor-value-initialized-field', 'template<class T>struct Box{T value;Box():value(){}T get(){return value;}};int cpp_test_entry(){Box<int>b;return b.get()+42;}'),
    ('constructor-declaration-order', 'int state=0;int next(){state=state+1;return state;}template<class T>struct Box{T first;T second;Box():second(next()),first(next()){}T get(){return first*20+second;}};int cpp_test_entry(){Box<int>b;return b.get()+20;}'),
    ('unused-dependent-invalid-for-double', 'template<class T>struct Box{T value;T remainder(T a,T b){return a%b;}T get(){return value;}};int cpp_test_entry(){Box<double>b{42.0};return b.get();}'),
    ('used-dependent-integral-operation', 'template<class T>struct Box{T remainder(T a,T b){return a%b;}};int cpp_test_entry(){Box<int>b;return b.remainder(84,43)+1;}'),
    ('definition-point-overload', 'int choose(int n){return 42;}template<class T>struct Box{int get(){return choose(1);}};int choose(long n){return 1;}int cpp_test_entry(){Box<int>b;return b.get();}'),
    ('namespace-static-method', 'namespace tinyxml2{template<class T,int N>struct Box{static T get(){return N;}};}int cpp_test_entry(){tinyxml2::Box<int,42>b;return b.get();}'),
)
RETAINED_PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in RETAINED_DOT_CASES) + (
    ('retained-unused-member-original', (
        ('original.cpp', 'template<class T> struct Box { T f(){return 0;} };'),
        ('entry.cpp', 'int cpp_test_entry(){return 42;}'),
    )),
    ('cross-tu-method-and-c1', (
        ('provider.cpp', 'namespace N{template<class T>struct Box{T value;Box(T n):value(n){}T get()const{return value;}};}int read(N::Box<int>*p){return p->get();}'),
        ('entry.cpp', 'namespace N{template<class T>struct Box{T value;Box(T n):value(n){}T get()const{return value;}};}int read(N::Box<int>*);int cpp_test_entry(){N::Box<int>b(21);return read(&b)+b.get();}'),
    )),
)

# Independently authored pointer-receiver controls; the exact original dot
# sources above are retained and now exercise the shared address owner.
CASES = (
    ('aggregate-getter', 'template<class T>struct Box{T value;T get(){return value;}};int cpp_test_entry(){Box<int>b{42};return (&b)->get();}'),
    ('later-private-helper', 'template<class T>class Box{public:T get(){return helper();}private:static T helper(){return 42;}};int cpp_test_entry(){Box<int>b;return (&b)->get();}'),
    ('parameter-shadowing', 'template<class T>struct Box{T value;void set(T value){this->value=value;}T get(){return value;}};int cpp_test_entry(){Box<int>b{0};(&b)->set(42);return (&b)->get();}'),
    ('const-overload', 'template<class T>struct Box{T value;T get(){return 1;}T get()const{return value;}};int cpp_test_entry(){const Box<int>b{42};return (&b)->get();}'),
    ('mutable-const-body', 'template<class T>struct Box{mutable T value;T get()const{value=42;return value;}};int cpp_test_entry(){const Box<int>b{0};return (&b)->get();}'),
    ('recursive-body', 'template<class T>struct Box{T sum(T n){if(n==0)return 0;return n+sum(n-1);}};int cpp_test_entry(){Box<int>b;return (&b)->sum(8)+6;}'),
    ('pointer-comparison-body', 'template<class T>struct Box{T*value;int get(){if(value==nullptr)return 1;return *value;}};int cpp_test_entry(){int n=42;Box<int>b{&n};return (&b)->get();}'),
    ('pointer-field-dereference', 'template<class T>struct Box{T*value;T get(){return *value;}void clear(){value=nullptr;}};int cpp_test_entry(){int n=42;Box<int>b{&n};int answer=(&b)->get();(&b)->clear();if(b.value!=nullptr)return 1;return answer;}'),
    ('constructor-later-member', 'template<class T>struct Box{Box(T value):value(value){}T get()const{return value;}T value;};int cpp_test_entry(){Box<int>b(42);return (&b)->get();}'),
    ('constructor-const-member', 'template<class T>struct Box{const T value;Box(T n):value(n){}T get()const{return value;}};int cpp_test_entry(){const Box<int>b(42);return (&b)->get();}'),
    ('constructor-value-initialized-field', 'template<class T>struct Box{T value;Box():value(){}T get(){return value;}};int cpp_test_entry(){Box<int>b;return (&b)->get()+42;}'),
    ('constructor-declaration-order', 'int state=0;int next(){state=state+1;return state;}template<class T>struct Box{T first;T second;Box():second(next()),first(next()){}T get(){return first*20+second;}};int cpp_test_entry(){Box<int>b;return (&b)->get()+20;}'),
    ('unused-dependent-invalid-for-double', 'template<class T>struct Box{T value;T remainder(T a,T b){return a%b;}T get(){return value;}};int cpp_test_entry(){Box<double>b{42.0};return (&b)->get();}'),
    ('used-dependent-integral-operation', 'template<class T>struct Box{T remainder(T a,T b){return a%b;}};int cpp_test_entry(){Box<int>b;return (&b)->remainder(84,43)+1;}'),
    ('definition-point-overload', 'int choose(int n){return 42;}template<class T>struct Box{int get(){return choose(1);}};int choose(long n){return 1;}int cpp_test_entry(){Box<int>b;return (&b)->get();}'),
    ('namespace-static-method', dict(RETAINED_DOT_CASES)['namespace-static-method']),
    ('constructor-calls-later-private-helper', 'template<class T>class Box{T value;public:Box(T n):value(seed(n)){}T get(){return value;}private:static T seed(T n){return n+1;}};int cpp_test_entry(){Box<int>b(41);return (&b)->get();}'),
    ('nondependent-explicit-scoped-enum-field', 'enum class E{A=1};template<class T>struct Box{E value;int f(){return bool(value);}};int cpp_test_entry(){Box<int>b{E::A};if((&b)->f())return 42;return 1;}'),
    ('tu-local-argument-pointer-only', 'namespace{struct Hidden{int n;};}template<class T>struct Box{T value;int f(){return 42;}};int cpp_test_entry(){Box<Hidden*>*p=nullptr;if(p==nullptr)return 42;return 1;}'),
    ('dependent-explicit-scoped-enum-bool', 'enum class E{A=1};template<class T>struct Box{T*p;int f(){return bool(*p);}};int cpp_test_entry(){E e=E::A;Box<E>b{&e};if((&b)->f())return 42;return 1;}'),
    ('dependent-pointer-condition', 'template<class T>struct Box{T*p;int f(){if(*p)return 42;return 1;}};int cpp_test_entry(){int n=1;Box<int>b{&n};return (&b)->f();}'),
    ('dependent-direct-nullptr-bool', 'template<class T>struct Box{T f(){T b(nullptr);return b;}};int g(Box<bool>*p){return p->f();}int cpp_test_entry(){Box<bool>b;if(g(&b))return 1;return 42;}'),
    ('nullptr-contextual-condition', 'template<class T>struct Box{int f(){if(nullptr)return 1;return 42;}};int cpp_test_entry(){Box<int>b;return (&b)->f();}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in CASES) + (
    ('retained-unused-member-original', dict(RETAINED_PROGRAMS)['retained-unused-member-original']),
    ('cross-tu-pointer-method-and-c1', (
        ('provider.cpp', 'namespace N{template<class T>struct Box{T value;Box(T n):value(n){}T get()const{return value;}};}int read(N::Box<int>*p){return p->get();}'),
        ('entry.cpp', 'namespace N{template<class T>struct Box{T value;Box(T n):value(n){}T get()const{return value;}};}int read(N::Box<int>*);int cpp_test_entry(){N::Box<int>b(21);return read(&b)+(&b)->get();}'),
    )),
)


@pytest.mark.parametrize('name,source', RETAINED_DOT_CASES[:-1], ids=[row[0] for row in RETAINED_DOT_CASES[:-1]])
def test_original_dot_programs_use_shared_address_owner(name, source):
    units = ((name+'.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_original_cross_tu_dot_entry_uses_shared_address_owner():
    units = dict(RETAINED_PROGRAMS)['cross-tu-method-and-c1']
    assert compile_host_cpp(units) == compile_host_cpp(units)



@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_member_pattern_original_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_unused_definition_is_typed_without_emitting_a_body():
    parser = CppParser()
    ast = parser.parse('template<class T> struct Box { T f(){return 0;} };')
    record = parser.scope.names['Box'].dtype.methods[0]
    assert record.body.kind == 'block'
    assert record.body.children[0].kind == 'return'
    assert not any(isinstance(item, c_ast.FuncDef) for item in ast.ext)
    assert not parser.instance_registry.entries


def test_only_odr_used_member_gets_external_odr_definition():
    parser = CppParser()
    ast = parser.parse(dict(CASES)['unused-dependent-invalid-for-double'])
    definitions = [item for item in ast.ext if isinstance(item, c_ast.FuncDef)]
    names = [item.decl.name for item in definitions]
    assert '_ZN3BoxIdE3getEv' in names
    assert not any('9remainder' in name for name in names)
    selected = next(item for item in definitions if item.decl.name == '_ZN3BoxIdE3getEv')
    assert selected.decl.definition_linkage == 'external_odr'
    assert 'static' not in selected.decl.storage


@pytest.mark.parametrize('namespace,expected', (
    ('', ('_ZN3BoxIiE1fEi', '_ZNK3BoxIiE1cEi', '_ZN3BoxIiEC1Ei', '_ZN3BoxIiE4selfEPS0_S1_')),
    ('N', ('_ZN1N3BoxIiE1fEi', '_ZNK1N3BoxIiE1cEi', '_ZN1N3BoxIiEC1Ei', '_ZN1N3BoxIiE4selfEPS1_S2_')),
))
def test_member_owner_encoding_and_substitution_order(namespace, expected):
    parser = CppParser()
    declaration = 'template<class T>struct Box{T value;};'
    parser.parse(('namespace N{'+declaration+'}' if namespace else declaration) +
                 'using B='+('N::' if namespace else '')+'Box<int>;')
    owner = parser.scope.names['B'].dtype
    assert (mangle_member(owner, 'f', ('int',)), mangle_member(owner, 'c', ('int',), True),
            _complete_name(owner, ('int',)),
            mangle_member(owner, 'self', (PointerType(owner), PointerType(owner)))) == expected


@pytest.mark.parametrize('source,code', (
    ('template<class T>struct Box{T f(){return missing();}};', 'cpp.undeclared'),
    ('template<class T>struct Box{int f(){return 1%2.0;}};', 'cpp.invalid-operands'),
    ('template<class T>struct Box{T f(){return missing;}};', 'cpp.undeclared'),
    ('template<class T>struct Box{T value;T f()const{value=42;return value;}};', 'cpp.const-assignment'),
    ('template<class T>struct Box{static T f(){return value;}T value;};', 'cpp.nonstatic-member-without-object'),
    ('template<class T>struct Box{T f(T a,T b){return a%b;}};int f(){Box<double>b;return b.f(1.0,2.0);}', 'cpp.invalid-operands'),
    ('template<class T>struct Box{T f(T a,T b){return a%b;}};int f(){Box<double>b;return (&b)->f(1.0,2.0);}', 'cpp.invalid-operands'),
    ('template<class T>class Box{T f(){return 42;}};int f(){Box<int>b;return b.f();}', 'cpp.member-access'),
    ('template<class T>struct Box{const int n;Box(){}};', 'cpp.const-initializer'),
    ('template<class T>struct Box{T n;Box():n(1),n(2){}};', 'cpp.duplicate-member-initializer'),
    ('template<class T>struct Box{Box():missing(1){}};', 'cpp.invalid-member-initializer'),
    ('template<class T>struct Box{Box():Box(1){}};', 'cpp.unsupported.delegating-constructors'),
    ('int choose(void*);namespace N{struct Tag{};int choose(Tag*);}template<class T>struct Box{int f(T p){return choose(p);}};', 'cpp.unsupported.dependent-lookup'),
    ('enum class E{A};template<class T>struct Box{bool same(E a,E b){return a==b;}};int f(Box<int>*p,E e){return p->same(e,e);}', 'cpp.unsupported.enum-templates'),
    ('template<class T>struct Box{int f(){bool b=nullptr;return 42;}};', 'cpp.invalid-conversion'),
    ('template<class T>struct Box{T f(){T b=nullptr;return b;}};int g(Box<bool>*p){return p->f();}', 'cpp.invalid-conversion'),
    ('template<class T>struct Box{bool f(){return nullptr;}};', 'cpp.invalid-conversion'),
    ('template<class T>struct Box{int f(){T x;return 42;}};int g(Box<const int>*p){return p->f();}', 'cpp.const-initializer'),
    ('using P=int*const;template<class T>struct Box{int f(){T x;return 42;}};int g(Box<P>*p){return p->f();}', 'cpp.const-initializer'),
    ('enum class E{A};template<class T>struct Box{T*p;int f(){if(*p)return 42;return 0;}};int g(Box<E>*p){return p->f();}', 'cpp.invalid-conversion'),
    ('using R=int&;template<class T>struct Box{int f(R x){return x+1;}};', 'cpp.unsupported.references'),
    ('enum class E{A};template<class T>struct Box{E value;int f(){if(value)return 42;return 0;}};', 'cpp.invalid-conversion'),
    ('enum class E{A};E value();template<class T>struct Box{int f(){if(value())return 42;return 0;}};', 'cpp.unsupported.enum-templates'),
    ('namespace{struct Hidden{int n;};}template<class T>struct Box{int f(){return 42;}};int f(Box<Hidden*>*p){return p->f();}', 'cpp.unsupported.member-templates'),
    ('namespace{struct Hidden{int n;};}template<class T>struct Wrap{T value;};template<class T>struct Box{int f(){return 42;}};int f(Box<Wrap<const Hidden*>*>*p){return p->f();}', 'cpp.unsupported.member-templates'),
    ('typedef int A[2];template<class T>struct Box{A f();};', 'cpp.unsupported.array-templates'),
    ('typedef int A[2];template<class T>struct Box{A* f();};', 'cpp.unsupported.array-templates'),
    ('template<class T>struct Box{Box(Box);};', 'cpp.invalid-constructor'),
    ('template<class T>struct Box{Box(const Box);};', 'cpp.invalid-constructor'),
    ('namespace{struct Hidden{int n;};}template<class T>struct Box{Hidden*f(){return nullptr;}};int g(Box<int>*p){p->f();return 42;}', 'cpp.unsupported.member-templates'),
))
def test_definition_and_instantiation_boundaries(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


TU_LOCAL_PROGRAM = (
    ('a.cpp', 'namespace{struct Hidden{int n;};}template<class T>struct Box{T p;T advance(){return ++p;}};int left(){Hidden a[2];Box<Hidden*>b{a};return (&b)->advance()==&a[1];}'),
    ('b.cpp', 'namespace{struct Hidden{long long n;};}template<class T>struct Box{T p;T advance(){return ++p;}};int left();int cpp_test_entry(){Hidden a[2];Box<Hidden*>b{a};if(left()!=1)return 1;if((&b)->advance()!=&a[1])return 2;return 42;}'),
)


@pytest.mark.parametrize('filename,source', TU_LOCAL_PROGRAM)
def test_tu_local_specialization_cannot_become_external_odr(filename, source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, filename)
    assert error.value.code == 'cpp.unsupported.member-templates'
