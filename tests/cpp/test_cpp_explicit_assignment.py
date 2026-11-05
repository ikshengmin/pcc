"""Explicit assignment-function calls, N4659 [over.ass], [expr.call].

https://timsong-cpp.github.io/cppwp/n4659/over.ass
https://timsong-cpp.github.io/cppwp/n4659/expr.call
User-provided ordinary bodies borrow parameters/results. Explicit call syntax
sequences the receiver before its argument, unlike infix assignment. Infix
class assignment and implicit special members remain gated. Execution UNRUN.
"""

from pathlib import Path

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ConstType, ReferenceType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

# TinyXML2 11.0.0 h:2068-2071,2146-2149; upstream
# 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib. Keep complete original
# definitions, including CRLF and tabs, inside authored class observers.
HANDLE_ASSIGNMENT = ('    XMLHandle& operator=( const XMLHandle& ref )\t\t\t\t\t\t\t{\r\n'
                     '        _node = ref._node;\r\n'
                     '        return *this;\r\n'
                     '    }\r\n')
CONST_HANDLE_ASSIGNMENT = ('    XMLConstHandle& operator=( const XMLConstHandle& ref )\t\t\t\t\t\t\t{\r\n'
                           '        _node = ref._node;\r\n'
                           '        return *this;\r\n'
                           '    }\r\n')
ORIGINAL_VOID = 'struct S{void operator=(const S&){} };'
ORIGINAL_SELF = 'struct S{S&operator=(const S&other){return *this;}};'
ORIGINAL_EXPLICIT = 'struct S{void operator=(int);};void f(S*p){p->operator=(1);}'
COPY_CLASS = 'struct S{int n;S&operator=(const S&other){n=other.n;return *this;}};'
CASES = (
    ('tinyxml-handle-assignment-definition', 'struct XMLNode{int n;};struct XMLHandle{XMLNode*_node;'
     + HANDLE_ASSIGNMENT + '};int cpp_test_entry(){XMLNode a{17};XMLNode b{42};'
     'XMLHandle left{&a};const XMLHandle right{&b};XMLHandle&r=left.operator=(right);'
     'if(&r!=&left||left._node!=&b||right._node!=&b)return 1;return left._node->n;}'),
    ('tinyxml-const-handle-assignment-definition', 'struct XMLNode{int n;};struct XMLConstHandle{const XMLNode*_node;'
     + CONST_HANDLE_ASSIGNMENT + '};int cpp_test_entry(){const XMLNode a{17};const XMLNode b{42};'
     'XMLConstHandle left{&a};const XMLConstHandle right{&b};XMLConstHandle&r=left.operator=(right);'
     'if(&r!=&left||left._node!=&b||right._node!=&b)return 1;return left._node->n;}'),
    ('out-of-class-copy-assignment', 'struct S{int n;S&operator=(const S&);};'
     'S&S::operator=(const S&other){n=other.n;return *this;}'
     'int cpp_test_entry(){S a{17};S b{42};S&r=a.operator=(b);if(&r!=&a)return 1;return r.n;}'),
    ('void-assignment-call-statement', 'struct S{int n;void operator=(const S&other){n=other.n;}};'
     'int cpp_test_entry(){S a{17};S b{42};a.operator=(b);return a.n;}'),
    ('scalar-assignment-overload-result', 'struct S{int n;int operator=(int x){n=x;return n;}};'
     'int cpp_test_entry(){S a{0};int n=a.operator=(42);if(a.n!=42)return 1;return n;}'),
    ('pointer-assignment-result', 'struct S{int n;S*operator=(const S&other){n=other.n;return this;}};'
     'int cpp_test_entry(){S a{17};S b{42};S*p=a.operator=(b);if(p!=&a)return 1;return p->n;}'),
    ('const-qualified-assignment-member', 'struct S{int n;int operator=(int x)const{return n+x;}};'
     'int cpp_test_entry(){const S a{17};return a.operator=(25);}'),
    ('reference-const-overload-ranks', 'struct S{int n;int operator=(S&){return 17;}'
     'int operator=(const S&){return 25;}};int cpp_test_entry(){S a{0};S b{0};const S c{0};'
     'return a.operator=(b)+a.operator=(c);}'),
    ('explicit-receiver-before-argument', 'int trace=0;'+COPY_CLASS+
     'S*receiver(S*p){trace=trace*10+1;return p;}S&argument(S*p){trace=trace*10+2;return *p;}'
     'int cpp_test_entry(){S a{17};S b{42};S&r=receiver(&a)->operator=(argument(&b));'
     'if(trace!=12||&r!=&a)return 1;return a.n;}'),
    ('chained-explicit-calls', COPY_CLASS+'int cpp_test_entry(){S a{1};S b{17};S c{42};'
     'S&r=a.operator=(b).operator=(c);if(&r!=&a||b.n!=17)return 1;return a.n;}'),
    ('self-assignment-borrows-once', COPY_CLASS+'int cpp_test_entry(){S a{42};'
     'S&r=a.operator=(a);if(&r!=&a)return 1;return a.n;}'),
    ('no-reference-parameter-or-result-destruction', 'int destroyed=0;'
     'struct S{int n;~S(){++destroyed;}S&operator=(const S&other){n=other.n;return *this;}};'
     'int cpp_test_entry(){int n=0;{S a{17};S b{42};S&r=a.operator=(b);n=r.n;'
     'if(destroyed!=0)return 1;}if(destroyed!=2)return 2;return n;}'),
    ('discarded-result-static-member-keeps-call', 'int calls=0;struct S{int n;'
     'S&operator=(const S&other){++calls;n=other.n;return *this;}static int answer(){return 42;}};'
     'int cpp_test_entry(){S a{17};S b{42};int n=a.operator=(b).answer();'
     'if(calls!=1||a.n!=42)return 1;return n;}'),
    ('sizeof-explicit-call-does-not-execute', 'int calls=0;struct S{int n;'
     'S&operator=(const S&other){++calls;n=other.n;return *this;}};'
     'int cpp_test_entry(){S a{42};S b{17};if(sizeof(a.operator=(b))!=sizeof(S)||calls!=0)return 1;return a.n;}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in CASES) + (
    ('former-void-assignment-original', (
        ('original.cpp', ORIGINAL_VOID),
        ('entry.cpp', ORIGINAL_VOID+'int cpp_test_entry(){S a{};S b{};a.operator=(b);return 42;}'),
    )),
    ('former-self-assignment-original', (
        ('original.cpp', ORIGINAL_SELF),
        ('entry.cpp', ORIGINAL_SELF+'int cpp_test_entry(){S a{};S b{};S&r=a.operator=(b);if(&r!=&a)return 1;return 42;}'),
    )),
    ('former-explicit-assignment-call-original', (
        ('original.cpp', ORIGINAL_EXPLICIT),
        ('entry.cpp', 'struct S{void operator=(int);};int seen=0;void S::operator=(int x){seen=x;}'
         'void f(S*);int cpp_test_entry(){S s{};f(&s);if(seen!=1)return 1;return 42;}'),
    )),
)


def test_tinyxml_assignment_definitions_keep_complete_original_bytes():
    lines = (Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.h').read_bytes().splitlines(keepends=True)
    assert b''.join(lines[2067:2071]) == HANDLE_ASSIGNMENT.encode()
    assert b''.join(lines[2145:2149]) == CONST_HANDLE_ASSIGNMENT.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_explicit_assignment_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_assignment_definition_keeps_reference_identity_and_member_symbol():
    parser = CppParser()
    parser.parse(COPY_CLASS)
    owner = parser.scope.names['S'].dtype
    method, = owner.definition.scope.names['operator='].overloads
    assert method.defined and method.dtype == ReferenceType(owner)
    assert method.parameters == (ReferenceType(ConstType(owner)),)
    assert method.c_name == '_ZN1SaSERKS_'


INVALID = (
    ('const-receiver', COPY_CLASS+'void f(const S*a,const S*b){S&r=a->operator=(*b);}', 'cpp.no-viable-overload'),
    ('drop-argument-const', 'struct S{void operator=(S&){} };void f(S*a,const S*b){a->operator=(*b);}', 'cpp.no-viable-overload'),
    ('private-assignment', 'class S{void operator=(int){} };void f(S*p){p->operator=(1);}', 'cpp.member-access'),
    ('static-assignment', 'struct S{static void operator=(int);};', 'cpp.invalid-assignment-operator'),
    ('assignment-default-argument', 'struct S{void operator=(int=0);};', 'cpp.invalid-assignment-operator'),
    ('free-assignment', 'void operator=(int);', 'cpp.invalid-assignment-operator'),
    ('two-parameters', 'struct S{void operator=(int,int);};', 'cpp.invalid-assignment-operator'),
    ('zero-parameters', 'struct S{void operator=();};', 'cpp.invalid-assignment-operator'),
    ('reference-operator-data', 'struct S{S&operator=;};', 'cpp.invalid-assignment-operator'),
    ('const-reference-write', 'struct S{int n;void operator=(const S&other){other.n=1;}};', 'cpp.const-assignment'),
)
DEFERRED = (
    ('infix-user-assignment', COPY_CLASS+'void f(S*a,S*b){*a=*b;}', 'cpp.unsupported.class-assignment'),
    ('implicit-copy-assignment', 'struct S{int n;};void f(S*a,S*b){*a=*b;}', 'cpp.unsupported.class-assignment'),
    ('explicit-implicit-copy-only', 'struct S{int n;};int f(S*a,S*b){return a->operator=(*b).n;}', 'cpp.unsupported.class-assignment'),
    ('implicit-copy-beside-scalar-overload', 'struct S{int n;int operator=(int){return 42;}};'
     'int f(S*a,S*b){return a->operator=(*b).n;}', 'cpp.unsupported.class-assignment'),
    ('template-assignment-body', 'template<class T>struct S{void operator=(const S&){}};', 'cpp.unsupported.member-templates'),
    ('specialized-template-assignment-call', 'template<class T>struct S{void operator=(const S&);};'
     'void f(S<int>*p,S<int>*q){p->operator=(*q);}', 'cpp.unsupported.class-operators'),
    ('explicit-call-in-template', 'struct S{void operator=(int);};template<class T>int f(S*p){p->operator=(1);return 0;}', 'cpp.unsupported.pointer-templates'),
    ('class-by-value-assignment-parameter', 'struct S{void operator=(S);};', 'cpp.unsupported.class-value-abi'),
    ('class-value-assignment-result', 'struct S{S operator=(const S&);};', 'cpp.unsupported.class-value-abi'),
    ('discarded-class-reference-result', COPY_CLASS+'void f(S*a,S*b){a->operator=(*b);}', 'cpp.unsupported.class-values'),
    ('temporary-materialization', COPY_CLASS+'void f(S*a){S&r=a->operator=(S{42});}', 'cpp.unsupported.class-initialization'),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_explicit_assignment_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code
