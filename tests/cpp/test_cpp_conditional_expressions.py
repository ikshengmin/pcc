"""C++17 conditional obligations and address-of boundary controls.

N4659 [expr.cond], [expr.comma], [conv.lval], [expr.const], [dcl.fct.default]:
https://timsong-cpp.github.io/cppwp/n4659/expr.cond
https://timsong-cpp.github.io/cppwp/n4659/expr.comma
https://timsong-cpp.github.io/cppwp/n4659/conv.lval
https://timsong-cpp.github.io/cppwp/n4659/expr.const
https://timsong-cpp.github.io/cppwp/n4659/dcl.fct.default
https://timsong-cpp.github.io/cppwp/n4659/expr.unary.op
https://timsong-cpp.github.io/cppwp/n4659/expr.prim.id

These tests compile with host pcc0 and exercise its owned emitted native output.
They are not evidence of native pcc1/self-host execution. No class conversion,
materialization, overloaded comma, function/member pointer or throw support is
implied. The existing placement regression is imported without changing bytes.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT
from pcc.frontends.cpp.parser import TokenCursor
from pcc.frontends.cpp.templates import Pattern
from pcc.frontends.cpp.types import ArrayType, ConstType, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_defaulted_default_constructors import PLACEMENT_PROGRAMS
from tests.cpp.test_cpp_owned_execution import owned_only


CASES = (
    ('right-associative', 'int cpp_test_entry(){return false?1:true?42:2;}'),
    ('nested-middle', 'int cpp_test_entry(){return true?false?1:42:2;}'),
    ('logical-precedence', 'int cpp_test_entry(){return false||true?42:1;}'),
    ('assignment-third', 'int cpp_test_entry(){int n=0;false?1:n=42;return n;}'),
    ('full-middle-comma', 'int cpp_test_entry(){int n=0;return true?n=17,n+=25,n:1;}'),
    ('middle-comma-lvalue', 'int cpp_test_entry(){int n=0;int a=42;int b=1;'
     'int*p=&(true?n+=1,a:b);if(n!=1||p!=&a)return 1;return *p;}'),
    ('unselected-middle-effects', 'int cpp_test_entry(){int n=0;int v=false?n++,n++,1:42;return v+n;}'),
    ('condition-before-arm', 'int cpp_test_entry(){int n=0;return ++n?n+=41:n+=100;}'),
    ('condition-once', 'int calls=0;int test(){calls+=1;return 1;}'
     'int cpp_test_entry(){int v=test()?41:0;return v+calls;}'),
    ('unselected-calls', 'int n=0;int bad(){n+=100;return 1;}'
     'int cpp_test_entry(){int a=true?20:bad();int b=false?bad():22;return a+b+n;}'),
    ('unselected-null-dereference', 'int cpp_test_entry(){int*p=nullptr;return p?*p:42;}'),
    ('contextual-nullptr', 'int cpp_test_entry(){return nullptr?1:42;}'),
    ('contextual-floating', 'int cpp_test_entry(){return 0.5?42:1;}'),
    ('contextual-enum', 'enum E{Yes=1};int cpp_test_entry(){return Yes?42:1;}'),
    ('dynamic-bool-type', 'int pick(bool n){if(n)return 42;return 1;}int pick(int){return 2;}'
     'int cpp_test_entry(){bool c=true;return pick(c?true:false);}'),
    ('dynamic-char-type', 'int pick(char n){return int(n);}int pick(int){return 1;}'
     'int cpp_test_entry(){bool c=true;int n=42;return pick(c?char(n):char(1));}'),
    ('arithmetic-common-type', 'int pick(int){return 1;}int pick(double n){return int(n);}'
     'int cpp_test_entry(){return pick(true?42:1.5);}'),
    ('unsigned-common-type', 'int cpp_test_entry(){if((true?-1:1u)>0u)return 42;return 1;}'),
    ('same-enum-prvalues', 'enum class E{Answer=42,Other=1};int pick(E e){return int(e);}int pick(int){return 1;}'
     'int cpp_test_entry(){return pick(true?E::Answer:E::Other);}'),
    ('different-unscoped-enums', 'enum A{First=42};enum B{Second=1};int pick(int n){return n;}int pick(A){return 1;}'
     'int cpp_test_entry(){return pick(true?First:Second);}'),
    ('scalar-lvalue-write', 'int cpp_test_entry(){int a=0;int b=1;(true?a:b)=42;return a;}'),
    ('scalar-reference-bind', 'void put(int&n){n=42;}int cpp_test_entry(){int a=1;int b=0;put(false?a:b);return b;}'),
    ('effectful-selected-address', 'int cpp_test_entry(){int a[2]={1,42};int i=0;int j=1;'
     'int*p=&(false?a[i++]:a[j++]);if(i!=0||j!=2)return 1;return *p;}'),
    ('pointer-object-lvalue', 'int cpp_test_entry(){int n=42;int*p=nullptr;int*q=nullptr;'
     '(false?p:q)=&n;if(p!=nullptr)return 1;return *q;}'),
    ('scalar-cv-union', 'int pick(int&){return 1;}int pick(const int&n){return n;}'
     'int cpp_test_entry(){int a=42;const int b=1;return pick(true?a:b);}'),
    ('class-lvalue-address', 'struct S{int n;};int cpp_test_entry(){S a{1};S b{42};'
     'S*p=&(false?a:b);if(p!=&b)return 1;return p->n;}'),
    ('array-lvalue-size-and-address', 'int cpp_test_entry(){int a[3]={1,2,3};int b[3]={17,20,5};'
     'if(sizeof(true?a:b)!=sizeof(a))return 1;int(*p)[3]=&(false?a:b);return (*p)[0]+(*p)[1]+(*p)[2];}'),
    ('qualified-pointer-composite', 'int pick(int*){return 1;}int pick(const int*p){return *p;}'
     'int cpp_test_entry(){int a=42;const int b=1;return pick(true?&a:&b);}'),
    ('void-pointer-composite', 'int cpp_test_entry(){int n=42;void*p=&n;const int*q=&n;'
     'const void*r=false?p:q;if(r!=q)return 1;return n;}'),
    ('deep-pointer-cv-composite', 'int cpp_test_entry(){int n=42;int*p=&n;const int*q=&n;'
     'const int*const*r=true?&p:&q;return **r;}'),
    ('pointer-nullptr-both-orders', 'int cpp_test_entry(){int n=42;int*p=true?&n:nullptr;'
     'int*q=false?nullptr:&n;return *p+*q-42;}'),
    ('pointer-zero-literal', 'int cpp_test_entry(){int n=42;int*p=true?&n:0;return *p;}'),
    ('nullptr-zero-literal', 'int cpp_test_entry(){int*p=true?nullptr:0;if(p==nullptr)return 42;return 1;}'),
    ('nullptr-both-arms', 'int cpp_test_entry(){int*p=true?nullptr:nullptr;if(p==nullptr)return 42;return 1;}'),
    ('void-selected-effects', 'int n=0;void a(){n+=42;}void b(){n+=100;}'
     'int cpp_test_entry(){true?a():b();return n;}'),
    ('function-template-replay', 'template<class T>T choose(bool c,T a,T b){return c?a:b;}'
     'int cpp_test_entry(){return choose(true,42,1);}'),
    ('class-template-pointer-replay', 'template<class T>struct Box{T*choose(bool c,T*a,T*b){return c?a:b;}};'
     'int cpp_test_entry(){int n=42;int m=1;Box<int>b;return *b.choose(false,&m,&n);}'),
    ('class-template-member-replay', 'template<class T>struct Box{T value;T choose(bool c,T other){return c?value:other;}};'
     'int cpp_test_entry(){Box<int>b{42};return (&b)->choose(true,1);}'),
    ('constant-dead-call', 'int bad(){return 100;}struct S{unsigned char n;};'
     'int cpp_test_entry(){S s{true?42:bad()};return s.n;}'),
    ('constant-dead-divide-zero', 'struct S{unsigned char n;};int cpp_test_entry(){S s{false?1/0:42};return s.n;}'),
    ('constant-dead-overflow', 'struct S{unsigned char n;};int cpp_test_entry(){S s{true?42:2147483647+1};return s.n;}'),
    ('constant-array-bound', 'int cpp_test_entry(){const int n=true?2:1/0;int a[n]={17,25};return a[0]+a[1];}'),
    ('template-selected-constant', 'template<class T>int f(T n){return true?42:n;}int cpp_test_entry(){return f(100);}'),
    ('discarded-uninitialized-comma', 'struct S{unsigned char n;};int cpp_test_entry(){int x;S s{(x,42)};return s.n;}'),
    ('discarded-uninitialized-middle', 'struct S{unsigned char n;};int cpp_test_entry(){int x;S s{true?x,42:1};return s.n;}'),
    ('discarded-uninitialized-lvalues', 'int cpp_test_entry(){int a;int b;true?a:b;(0,a);'
     '(void)(true?a:b);static_cast<void>((0,a));return 42;}'),
    ('uninitialized-nullptr-glvalue', 'using N=decltype(nullptr);int cpp_test_entry(){N a;N b;bool c=true;'
     'N value=(c?a:b);if(value==nullptr)return 42;return 1;}'),
    ('effectful-nullptr-glvalue', 'using N=decltype(nullptr);int calls=0;N*select(N*p){calls+=1;return p;}'
     'int cpp_test_entry(){N a;N b;N value=(true?*select(&a):*select(&b));if(calls!=1||value!=nullptr)return 1;return 42;}'),
    ('local-comma-nullptr-effects', 'int calls=0;int f(){calls+=1;return 0;}'
     'int cpp_test_entry(){int*p=(f(),nullptr);if(calls!=1||p!=nullptr)return 1;return 42;}'),
    ('local-conditional-nullptr-effects', 'int calls=0;int f(){calls+=1;return 1;}'
     'int cpp_test_entry(){int*p=f()?nullptr:nullptr;if(calls!=1||p!=nullptr)return 1;return 42;}'),
    ('nttp-conditional', 'template<int N>int f(){return N;}int cpp_test_entry(){return f<true?42:1>();}'),
    ('nttp-parenthesized', 'template<int N>int f(){return N;}int cpp_test_entry(){return f<(true?42:1)>();}'),
    ('nttp-nested', 'template<int N>int f(){return N;}int cpp_test_entry(){return f<false?1:true?42:2>();}'),
    ('nttp-parenthesized-comparison', 'template<int N>int f(){return N;}int cpp_test_entry(){return f<(3>2)?42:1>();}'),
    ('default-conditional', 'int f(int n=true?42:1){return n;}int cpp_test_entry(){return f();}'),
    ('default-parenthesized', 'int f(int n=(true?42:1)){return n;}int cpp_test_entry(){return f();}'),
    ('default-nested-parentheses', 'int f(int n=false?1:(true?42:2)){return n;}int cpp_test_entry(){return f();}'),
    ('default-middle-comma', 'int f(int n=true?1,42:0,int other=0){return n+other;}int cpp_test_entry(){return f();}'),
    ('default-qualified-comma', 'namespace N{enum E{Answer=42};}'
     'int f(int n=true?N::Answer,42:0,int other=0){return n+other;}int cpp_test_entry(){return f();}'),
    ('default-nested-comma', 'int f(int n=true?false?1,2:3,42:0,int other=0){return n+other;}int cpp_test_entry(){return f();}'),
    ('default-later-parameter', 'int f(int n=true?1,41:0,int other=true?0,1:0){return n+other;}int cpp_test_entry(){return f();}'),
    ('default-explicit-separators', 'int f(int n=true?1,42:0,int other=0){return n+other;}int cpp_test_entry(){return f(40,2);}'),
    ('default-global-qualified-alternative', 'namespace N{int Answer=42;}int f(int n=false?0: ::N::Answer){return n;}'
     'int cpp_test_entry(){return f();}'),
    ('assignment-rhs-before-condition', 'int cpp_test_entry(){int c=0;int a=0;int b=1;'
     '(c?a:b)=(c=1,42);if(b!=1)return 1;return a;}'),
    ('parenthesized-member-address-uses-this',
     'struct S{int x;int*address(){return &(S::x);}};'
     'int cpp_test_entry(){S s{0};int*p=s.address();*p=42;return s.x;}'),
    ('ordinary-object-member-address',
     'struct S{int x;};int cpp_test_entry(){S s{0};int*p=&s.x;*p=42;return s.x;}'),
    ('namespace-qualified-object-address',
     'namespace n{int x;}int cpp_test_entry(){int*p=&n::x;*p=42;return n::x;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)
ORIGINAL_PLACEMENT = tuple(row for row in PLACEMENT_PROGRAMS
                          if row[0] == 'unevaluated-placement-does-not-define-defaulted-c1')

# These are actually ill-formed standard C++17, checked separately by reference.
INVALID = (
    ('void-condition', 'void g();int f(){return g()?42:1;}', 'cpp.void-value'),
    ('scoped-enum-condition', 'enum class E{A};int f(){return E::A?42:1;}', 'cpp.invalid-conversion'),
    ('one-void-arm', 'void g();void f(){true?g():42;}', 'cpp.invalid-operands'),
    ('unrelated-pointers', 'void f(int*p,double*q){true?p:q;}', 'cpp.invalid-operands'),
    ('scoped-enum-integer', 'enum class E{A};void f(){true?E::A:1;}', 'cpp.invalid-operands'),
    ('computed-zero', 'void f(int*p){true?p:(1-1);}', 'cpp.invalid-operands'),
    ('conditional-zero-not-null-literal', 'void f(){int*p=true?0:0;}', 'cpp.invalid-conversion'),
    ('nullptr-nonzero', 'void f(){true?nullptr:1;}', 'cpp.invalid-operands'),
    ('const-lvalue-write', 'void f(){int a=0;const int b=1;(true?a:b)=42;}', 'cpp.const-assignment'),
    ('prvalue-write', 'void f(){int a=0;(true?a:1)=42;}', 'cpp.not-lvalue'),
    ('dead-arm-name-checked', 'int f(){return true?42:missing;}', 'cpp.undeclared'),
    ('missing-colon', 'int f(){return true?42;}', 'cpp.unsupported.syntax'),
    ('omitted-middle-not-standard', 'int f(){return true?:42;}', 'cpp.unsupported.syntax'),
    ('qualified-member-read-needs-object',
     'struct S{int x;};int f(){return S::x;}', 'cpp.nonstatic-member-without-object'),
    ('parenthesized-qualified-member-address-needs-object',
     'struct S{int x;};void f(){&(S::x);}', 'cpp.nonstatic-member-without-object'),
    ('member-array-postfix-needs-object',
     'struct S{int x[2];};void f(){&S::x[0];}', 'cpp.nonstatic-member-without-object'),
    ('member-object-postfix-needs-object',
     'struct H{int x;};struct S{H h;};void f(){&S::h.x;}',
     'cpp.nonstatic-member-without-object'),
    ('member-pointer-postfix-needs-object',
     'struct H{int x;};struct S{H*p;};void f(){&S::p->x;}',
     'cpp.nonstatic-member-without-object'),
    ('prefixed-qualified-member-needs-object',
     'struct S{int x;};void f(){&+S::x;}', 'cpp.nonstatic-member-without-object'),
    ('prefix-increment-member-needs-object',
     'struct S{int x;};void f(){&++S::x;}', 'cpp.nonstatic-member-without-object'),
    ('postfix-increment-member-needs-object',
     'struct S{int x;};void f(){&S::x++;}', 'cpp.nonstatic-member-without-object'),
    ('qualified-member-address-keeps-private-access',
     'class S{int x;};void f(){&S::x;}', 'cpp.member-access'),
)
# These inputs are valid C++17. Rejection documents an ownership gap, never
# language invalidity; their reference lane must accept the exact originals.
DEFERRED = (
    ('class-converting-arm', 'struct S{S(int){}};void f(bool c,S*p){c?*p:42;}', 'cpp.unsupported.class-conversions'),
    ('overloaded-comma-declaration', 'struct S{int n;};int operator,(S,int);void f(S*p){true?(*p,42):1;}', 'cpp.unsupported.syntax'),
    ('class-comma-needs-overload-lookup', 'struct S{int n;};void f(S*p){true?(*p,42):1;}', 'cpp.unsupported.class-operators'),
    ('function-template-pointer', 'template<class T>T*choose(bool c,T*a,T*b){return c?a:b;}', 'cpp.unsupported.pointer-templates'),
    ('function-pointers', 'using F=int();void f(bool c,F*p,F*q){c?p:q;}', 'cpp.unsupported.function-pointers'),
    ('member-pointers', 'struct S{int a;int b;};void f(bool c){c?&S::a:&S::b;}', 'cpp.unsupported.member-pointers'),
    ('qualified-data-member-pointer',
     'struct S{int x;};void f(){&S::x;}', 'cpp.unsupported.member-pointers'),
    ('aliased-qualified-data-member-pointer',
     'namespace n{struct S{int x;};}using Alias=n::S;void f(){&Alias::x;}',
     'cpp.unsupported.member-pointers'),
    ('globally-qualified-data-member-pointer',
     'struct S{int x;};void f(){&::S::x;}', 'cpp.unsupported.member-pointers'),
    ('member-pointer-inside-own-class',
     'struct S{int x;void f(){&S::x;}};', 'cpp.unsupported.member-pointers'),
    ('member-pointer-to-array',
     'struct S{int x[2];};void f(){&S::x;}', 'cpp.unsupported.member-pointers'),
    ('throw-arm', 'int f(bool c){return c?42:throw 1;}', 'cpp.unsupported.exceptions'),
    ('bitfield-lvalue', 'struct S{int a:6;int b:6;};void f(bool c,S*p){(c?p->a:p->b)=21;}', 'cpp.unsupported.classes'),
    ('assignment-result-address', 'void f(bool c){int a=0;int b=0;int*p=&(c?(a=1):(b=2));}', 'cpp.unsupported.references'),
    ('static-comma-nullptr', 'int f();int*p=(f(),nullptr);', 'cpp.unsupported.static-initialization-analysis'),
    ('static-conditional-nullptr', 'int f();int*p=f()?nullptr:nullptr;', 'cpp.unsupported.static-initialization-analysis'),
)


@pytest.mark.parametrize('name,units', PROGRAMS + ORIGINAL_PLACEMENT,
                         ids=[row[0] for row in PROGRAMS + ORIGINAL_PLACEMENT])
def test_conditional_host_pcc0_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED,
                         ids=[row[0] for row in INVALID + DEFERRED])
def test_conditional_rejections_precede_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unsupported conditional reached emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name + '.cpp', source),))
    assert caught.value.code == code


def expression(source, declarations=''):
    parser = CppParser()
    parser.parse(declarations)
    parser.cursor = TokenCursor(parser._tokenize(source, 'conditional-model.cpp'))
    return parser, parser._expression()


def without_casts(node):
    while isinstance(node, c_ast.Cast):
        node = node.expr
    return node


@pytest.mark.parametrize('source,dtype,lvalue,declarations', (
    ('c?true:false', 'bool', False, 'bool c;'),
    ('c?char(42):char(1)', 'char', False, 'bool c;'),
    ('true?1:2L', 'long', False, ''),
    ('true?1:2.0', 'double', False, ''),
    ('true?a:b', 'int', True, 'int a;int b;'),
    ('true?a:b', ConstType('int'), True, 'int a;const int b=1;'),
    ('true?p:q', PointerType('int'), True, 'int*p;int*q;'),
    ('true?p:q', PointerType(ConstType('int')), False, 'int*p;const int*q;'),
    ('true?nullptr:0', 'nullptr', False, ''),
    ('true?p:nullptr', PointerType('int'), False, 'int*p;'),
    ('true?*p:*q', ArrayType('int', 3), True, 'using A=int[3];A*p;A*q;'),
    ('true?a():b()', 'void', False, 'void a();void b();'),
))
def test_conditional_type_and_category(source, dtype, lvalue, declarations):
    _, result = expression(source, declarations)
    assert result.dtype == dtype and result.lvalue is lvalue


@pytest.mark.parametrize('source,value', (
    ('true?42:1/0', 42), ('false?2147483647+1:42', 42),
    ('true?42:read()', 42), ('false?read():42', 42), ('true?-1:1u', 4294967295),
))
def test_selected_arm_constant_facts_preserve_runtime_ast(source, value):
    _, result = expression(source, 'int read();')
    assert result.constant_fact.status == KNOWN and result.constant_fact.value == value
    assert isinstance(without_casts(result.node), c_ast.TernaryOp)
    assert not result.null_pointer


def test_zero_result_is_not_a_null_literal():
    _, result = expression('true?0:0')
    assert result.constant_fact.status == KNOWN and result.constant_fact.value == 0
    assert not result.null_pointer


def test_unknown_condition_identical_arms_are_not_constant():
    _, result = expression('n?42:42', 'int n;')
    assert result.constant_fact.status == NOT_CONSTANT


def test_right_associative_ast():
    _, result = expression('false?1:true?42:2')
    node = without_casts(result.node)
    assert isinstance(node, c_ast.TernaryOp)
    assert isinstance(without_casts(node.iffalse), c_ast.TernaryOp)


def test_unused_class_template_has_typed_pattern_without_emission():
    parser = CppParser()
    tree = parser.parse('template<class T>struct Box{T choose(bool c,T a,T b){return c?a:b;}};')
    record = parser.scope.names['Box'].dtype.methods[0]
    result = record.body.children[0].children[0]
    assert isinstance(result.node, Pattern) and result.node.kind == 'conditional'
    assert len(result.node.children) == 3
    assert not any(isinstance(item, c_ast.FuncDef) for item in tree.ext)
    assert not parser.instance_registry.entries


@pytest.mark.parametrize('source', ('true?42:1/0', 'false?2147483647+1:42'))
def test_pattern_replay_recomputes_selected_constant_fact(source):
    parser = CppParser()
    parser.parse('')
    parser.parsing_template = True
    parser.template_function = object()
    parser.cursor = TokenCursor(parser._tokenize(source, 'conditional-pattern.cpp'))
    original = parser._expression()
    assert isinstance(original.node, Pattern) and original.node.kind == 'conditional'
    parser.template_function = None
    parser.parsing_template = False
    result = parser._instantiate_expression(original, {}, {})
    assert result.constant_fact.status == KNOWN and result.constant_fact.value == 42
    assert isinstance(without_casts(result.node), c_ast.TernaryOp)


def test_enum_identity_and_fact():
    parser, result = expression('true?E::A:E::B', 'enum class E{A=42,B=1};')
    assert result.dtype is parser.scope.names['E'].dtype and not result.lvalue
    assert result.constant_fact.status == KNOWN and result.constant_fact.value == 42


def test_class_lvalue_identity():
    parser, result = expression('true?*p:*q', 'struct S{int n;};S*p;S*q;')
    assert result.dtype is parser.scope.names['S'].dtype and result.lvalue


@pytest.mark.parametrize('source', ('c?true:false', 'c?char(42):char(1)'))
def test_small_scalar_c_projection_has_explicit_result_cast(source):
    _, result = expression(source, 'bool c;')
    assert isinstance(result.node, c_ast.Cast)
    assert isinstance(result.node.expr, c_ast.TernaryOp)


def test_original_placement_does_not_define_defaulted_constructor():
    assert len(ORIGINAL_PLACEMENT) == 1
    parser = CppParser()
    tree = parser.parse(ORIGINAL_PLACEMENT[0][1][0][1])
    selected = parser.scope.names['S'].dtype.definition.constructors.overloads[0]
    assert not any(isinstance(item, c_ast.FuncDef) and item.decl.name == selected.c_name for item in tree.ext)
    assert not parser.instance_registry.entries


@pytest.mark.parametrize('source', ('(x,42)', 'true?x,42:1'))
def test_discarded_scalar_comma_keeps_fact_without_fetch(source):
    parser, result = expression(source, 'int x;')
    assert result.constant_fact.status == KNOWN and result.constant_fact.value == 42
    node = without_casts(result.node)
    sequence = node.iftrue if isinstance(node, c_ast.TernaryOp) else node
    assert isinstance(sequence, c_ast.ExprList)
    discarded = sequence.exprs[0]
    assert isinstance(discarded, c_ast.Cast)
    assert isinstance(discarded.expr, c_ast.UnaryOp) and discarded.expr.op == '&'
    assert isinstance(discarded.expr.expr, c_ast.ID)
    assert discarded.expr.expr.name == parser.scope.names['x'].c_name


def test_effectful_nullptr_lvalue_has_selected_address_plan():
    parser, result = expression('c?*select(p):*select(q)',
                                'using N=decltype(nullptr);bool c;N*select(N*);N*p;N*q;')
    assert result.dtype == 'nullptr' and result.lvalue and result.discarded_known
    selected = without_casts(result.discarded_node)
    assert isinstance(selected, c_ast.TernaryOp)
    assert isinstance(selected.iftrue, c_ast.FuncCall) and isinstance(selected.iffalse, c_ast.FuncCall)
    value = parser._nullptr_rvalue(result)
    assert not value.lvalue and value.dtype == 'nullptr'
    assert isinstance(value.node, c_ast.ExprList)
    assert value.node.exprs[0] is result.discarded_node
    assert isinstance(value.node.exprs[1], c_ast.Cast)
    assert isinstance(value.node.exprs[1].expr, c_ast.Constant) and value.node.exprs[1].expr.value == '0'


def assert_names_only_addressed(node, names, parent=None):
    if isinstance(node, c_ast.ID) and node.name in names:
        assert isinstance(parent, c_ast.UnaryOp) and parent.op == '&' and parent.expr is node
    for _, child in node.children():
        assert_names_only_addressed(child, names, node)


@pytest.mark.parametrize('source', ('true?a:b', '(0,a)', '(void)(true?a:b)', 'static_cast<void>((0,a))'))
def test_discarded_lvalues_and_void_casts_do_not_fetch(source):
    parser, result = expression(source, 'int a;int b;')
    if result.dtype != 'void':
        assert result.lvalue and result.discarded_known
        discarded = parser._discarded_value_node(result)
        assert discarded is result.discarded_node
    else:
        discarded = result.node
    assert_names_only_addressed(discarded, {parser.scope.names[name].c_name for name in ('a', 'b')})


@pytest.mark.parametrize('source', ('(f(),nullptr)', 'f()?nullptr:nullptr'))
def test_effectful_nullptr_is_not_a_static_literal_shortcut(source):
    _, result = expression(source, 'int f();')
    assert result.dtype == 'nullptr' and not result.lvalue and not result.null_pointer
