"""Builtin compound assignment: target conversions, ordering, once, lvalue.

Original cases for N4659 [expr.ass]/1,7 and [expr.add]/4. The C AST projection
reuses existing lowering, without changing C evaluation order or adding runtime.
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


CASES = (
    ('add', 'int cpp_test_entry(){int x=17;x+=25;return x;}'),
    ('subtract', 'int cpp_test_entry(){int x=67;x-=25;return x;}'),
    ('multiply', 'int cpp_test_entry(){int x=6;x*=7;return x;}'),
    ('divide', 'int cpp_test_entry(){int x=126;x/=3;return x;}'),
    ('remainder', 'int cpp_test_entry(){int x=127;x%=85;return x;}'),
    ('shift-left', 'int cpp_test_entry(){short x=21;x<<=1LL;return x;}'),
    ('shift-right', 'int cpp_test_entry(){int x=84;x>>=1;return x;}'),
    ('bit-and', 'int cpp_test_entry(){int x=255;x&=42;return x;}'),
    ('bit-xor', 'int cpp_test_entry(){int x=127;x^=85;return x;}'),
    ('bit-or', 'int cpp_test_entry(){int x=40;x|=2;return x;}'),
    ('alternative-operators', 'int cpp_test_entry(){int x=255;x and_eq 40;x or_eq 3;x xor_eq 1;return x;}'),
    ('narrow-integer-conversion-after-operation', 'int cpp_test_entry(){unsigned char x=250;x+=48;return x;}'),
    ('floating-result-to-integer', 'int cpp_test_entry(){int x=85;x/=2.0;return x;}'),
    ('floating-lvalue', 'int cpp_test_entry(){double x=21.0;x*=2.0;return int(x);}'),
    ('bool-conversion-after-operation', 'int cpp_test_entry(){bool x=false;x+=2;if(!x)return 1;x^=1;return int(x)+42;}'),
    ('full-width-unsigned', 'int cpp_test_entry(){unsigned long long x=0xfedcba9876543200ULL;'
     'x|=0x10ULL;if(x!=0xfedcba9876543210ULL)return 1;return 42;}'),
    ('rhs-before-lhs-once', 'int*left(int*p,int*log){*log=*log*10+1;return p;}'
     'int right(int*log){*log=*log*10+2;return 25;}'
     'int cpp_test_entry(){int x=17;int log=0;*left(&x,&log)+=right(&log);if(log!=21)return 1;return x;}'),
    ('rhs-modifies-left-before-read', 'int right(int*p){*p=17;return 25;}'
     'int cpp_test_entry(){int x=0;x+=right(&x);return x;}'),
    ('left-effect-occurs-once', 'int*left(int*p,int*count){*count=*count+1;return p;}'
     'int cpp_test_entry(){int x=17;int count=0;*left(&x,&count)+=25;if(count!=1)return 1;return x;}'),
    ('right-associative-chain', 'int cpp_test_entry(){int x=2;int y=10;x+=y+=30;if(y!=40)return 1;return x;}'),
    ('result-is-same-object-lvalue', 'int cpp_test_entry(){int x=17;int*p=&(x+=25);if(p!=&x)return 1;return *p;}'),
    ('result-is-compound-assignment-target', 'int cpp_test_entry(){int x=11;(x+=10)*=2;return x;}'),
    ('result-is-simple-assignment-target', 'int cpp_test_entry(){int x=0;(x+=2)=42;return x;}'),
    ('result-is-prefix-target', 'int cpp_test_entry(){int x=40;++(x+=1);return x;}'),
    ('result-is-postfix-target', 'int cpp_test_entry(){int x=40;(x+=1)++;return x;}'),
    ('array-index-evaluated-once', 'int cpp_test_entry(){int a[2]={17,1};int index=0;a[index++]+=25;'
     'if(index!=1||a[1]!=1)return 1;return a[0];}'),
    ('class-member-lvalue', 'struct R{int x;};int cpp_test_entry(){R r{17};r.x+=25;return r.x;}'),
    ('pointer-forward-and-back', 'int cpp_test_entry(){int a[2]={1,42};int*p=a;p+=2;p-=1;return *p;}'),
    ('row-pointer-scales-complete-array', 'int cpp_test_entry(){int a[2][3]={{1,2,3},{4,5,42}};'
     'int(*p)[3]=a;p+=1;return p[0][2];}'),
    ('complete-class-pointer-zero-offset', 'struct R{int x;};int cpp_test_entry(){R r{42};R*p=&r;p+=0;return p->x;}'),
    ('enum-offset-uses-promotion', 'enum Offset{One=1};int cpp_test_entry(){int a[2]={1,42};int*p=a;p+=One;return *p;}'),
    ('template-replay', 'template<class T>T add(T x,T y){x+=y;return x;}int cpp_test_entry(){return add(17,25);}'),
    ('template-result-lvalue', 'template<class T>T add(T x){(x+=2)*=2;return x;}int cpp_test_entry(){return add(19);}'),
    ('for-step', 'int cpp_test_entry(){int total=0;for(int x=0;x<6;x+=2){total+=14;}return total;}'),
)

INVALID = (
    ('int f(){1+=2;return 0;}', 'cpp.not-lvalue'),
    ('int f(){const int x=1;x+=2;return x;}', 'cpp.const-assignment'),
    ('int f(){int a[2]={};a+=1;return 0;}', 'cpp.array-not-assignable'),
    ('int f(){double x=1.0;x%=2;return 0;}', 'cpp.invalid-operands'),
    ('int f(){int x=1;x<<=2.0;return 0;}', 'cpp.invalid-operands'),
    ('int f(){int x=1;int*p=&x;p*=2;return 0;}', 'cpp.invalid-operands'),
    ('int f(){int x=1;int*p=&x;p+=p;return 0;}', 'cpp.invalid-operands'),
    ('int f(){void*p=nullptr;p+=1;return 0;}', 'cpp.invalid-operands'),
    ('struct R;int f(R*p){p+=1;return 0;}', 'cpp.incomplete-class'),
    ('enum E{A};int f(){E x=A;x+=1;return 0;}', 'cpp.invalid-conversion'),
    ('enum class E{A};int f(){E x=E::A;x|=E::A;return 0;}', 'cpp.invalid-operands'),
    ('void nothing();int f(){int x=1;x+=nothing();return x;}', 'cpp.void-value'),
)

DEFERRED = (
    'struct R{int x;};int f(){R a{};R b{};a+=b;return 0;}',
    'int f(){volatile int x=1;x+=2;return x;}',
    'struct R{int x:3;};int f(){R r{};r.x+=1;return 0;}',
    'int f(){int x=1;((x=2)+=3);return x;}',
)


@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_compound_assignment_compiles_twice_identically(name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID)
def test_compound_assignment_errors_precede_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid compound assignment reached object emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('source', DEFERRED)
def test_compound_assignment_does_not_erase_unimplemented_dependencies(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code.startswith('cpp.unsupported.'), str(error.value)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_compound_assignment_executes_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)
