"""Normal-exit destructor rules with observable, original C++17 programs.

N4659 [class.dtor], [stmt.jump]/2, [stmt.return]/3, [stmt.dcl].
https://timsong-cpp.github.io/cppwp/n4659/class.dtor
https://timsong-cpp.github.io/cppwp/n4659/stmt.jump
https://timsong-cpp.github.io/cppwp/n4659/stmt.return

No exception unwinding, deleting/base destructor, inline ODR, arrays or
non-trivial subobject cleanup is claimed by these complete-object programs.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TRACE_CLASS = ('struct T{int*p;int digit;T(int*,int);~T();};'
               'T::T(int*p,int digit):p(p),digit(digit){}'
               'T::~T(){*p=*p*10+digit;}')


def traced(body, expected):
    return TRACE_CLASS + 'int cpp_test_entry(){int trace=0;' + body + (
        'if(trace!=' + str(expected) + ')return 1;return 42;}')


DESTRUCTOR_CASES = (
    ('reverse-declaration-order', traced('{T a(&trace,1);T b(&trace,2);}', 21)),
    ('nested-scope-order', traced('{T a(&trace,1);{T b(&trace,2);}}', 21)),
    ('false-branch-never-constructed', traced('if(false){T a(&trace,1);}', 0)),
    ('single-statement-if-scope', traced('if(true)T a(&trace,1);else T b(&trace,2);', 1)),
    ('single-statement-while-scope', traced('int i=0;while(++i<3)T a(&trace,1);', 11)),
    ('single-statement-for-scope', traced('for(int i=0;i<2;++i)T a(&trace,1);', 11)),
    ('single-statement-do-scope', traced('int i=0;do T a(&trace,1);while(++i<2);', 11)),
    ('while-continue', traced('int i=0;while(i<3){++i;T a(&trace,i);continue;}', 123)),
    ('while-break', traced('int i=0;while(i<3){++i;T a(&trace,i);if(i==2)break;}', 12)),
    ('do-continue', traced('int i=0;do{++i;T a(&trace,i);continue;}while(i<3);', 123)),
    ('for-init-survives-continue', traced('int i=0;for(T init(&trace,9);i<2;++i){T body(&trace,1);continue;}', 119)),
    ('for-init-break-cleanup-once', traced('for(T init(&trace,9);true;){T body(&trace,2);break;}', 29)),
    ('for-init-false-condition', traced('for(T init(&trace,9);false;){T body(&trace,2);}', 9)),
    ('switch-break-inner-only', traced('{T outer(&trace,1);switch(1){case 1:{T inner(&trace,2);break;}default:break;}}', 21)),
    ('switch-continue-through-loop', traced('int i=0;while(i<2){++i;T body(&trace,1);switch(i){case 1:{T inner(&trace,2);continue;}default:break;}}', 211)),
    ('nested-loop-break-keeps-outer', traced('for(int i=0;i<1;++i){T outer(&trace,1);while(true){T inner(&trace,2);break;}T last(&trace,3);}', 231)),
    ('loop-inside-switch-break-target', traced('switch(1){case 1:{T outer(&trace,1);while(true){T inner(&trace,2);break;}T last(&trace,3);break;}}', 231)),
    ('switch-direct-case-object', traced('switch(1){case 1:T inner(&trace,2);break;}', 2)),
    ('switch-no-match-no-cleanup', traced('switch(7){case 1:T inner(&trace,2);break;}', 0)),
    ('switch-no-label-no-cleanup', traced('switch(7){T inner(&trace,2);}', 0)),
    ('return-value-before-cleanup', TRACE_CLASS +
     'int observe(int*p){*p=*p*10+1;return *p;}'
     'int work(int*p){T a(p,2);return observe(p);}'
     'int cpp_test_entry(){int trace=0;int result=work(&trace);if(result!=1||trace!=12)return 1;return 42;}'),
    ('return-conversion-before-cleanup', TRACE_CLASS +
     'bool work(int*p){T a(p,1);return *p;}'
     'int cpp_test_entry(){int trace=0;bool value=work(&trace);if(value||trace!=1)return 1;return 42;}'),
    ('floating-return-survives-cleanup', TRACE_CLASS +
     'double work(int*p){T a(p,1);return 42.0;}'
     'int cpp_test_entry(){int trace=0;double value=work(&trace);if(trace!=1)return 1;return int(value);}'),
    ('return-before-later-initialization', TRACE_CLASS +
     'int work(int*p,bool early){T a(p,1);if(early)return 17;T b(p,2);return 25;}'
     'int cpp_test_entry(){int a=0;int b=0;int result=work(&a,true)+work(&b,false);'
     'if(a!=1||b!=21)return 1;return result;}'),
    ('return-through-for-init', TRACE_CLASS +
     'int work(int*p){for(T init(p,9);true;){T body(p,2);return 42;}}'
     'int cpp_test_entry(){int trace=0;int result=work(&trace);if(trace!=29)return 1;return result;}'),
    ('void-expression-before-cleanup', TRACE_CLASS +
     'void observe(int*p){*p=*p*10+1;}void work(int*p){T a(p,2);return observe(p);}'
     'int cpp_test_entry(){int trace=0;work(&trace);if(trace!=12)return 1;return 42;}'),
    ('void-fallthrough-cleanup', TRACE_CLASS +
     'void work(int*p){T a(p,2);}'
     'int cpp_test_entry(){int trace=0;work(&trace);if(trace!=2)return 1;return 42;}'),
    ('aggregate-with-destructor', 'struct S{int*p;~S();};S::~S(){*p=42;}'
     'int cpp_test_entry(){int n=0;{S s{&n};}return n;}'),
    ('aggregate-for-init-cleanup', 'struct S{int*p;~S();};S::~S(){*p=42;}'
     'int cpp_test_entry(){int n=0;for(S s{&n};false;){}return n;}'),
    ('default-initialized-with-destructor', 'struct S{int*p;~S();};S::~S(){*p=42;}'
     'int cpp_test_entry(){int n=0;{S s;s.p=&n;}return n;}'),
    ('const-object-under-destruction', 'struct S{int*p;int x;S(int*);~S();};'
     'S::S(int*p):p(p),x(1){}S::~S(){x=42;*p=x;}'
     'int cpp_test_entry(){int n=0;{const S s(&n);}return n;}'),
    ('destructor-local-cleanup-after-body', TRACE_CLASS +
     'struct S{int*p;~S();};S::~S(){T local(p,2);*p=*p*10+1;}'
     'int cpp_test_entry(){int trace=0;{S s{&trace};}if(trace!=12)return 1;return 42;}'),
    ('destructor-return-local-cleanup-once', TRACE_CLASS +
     'struct S{int*p;~S();};S::~S(){T local(p,2);*p=*p*10+1;return;}'
     'int cpp_test_entry(){int trace=0;{S s{&trace};}if(trace!=12)return 1;return 42;}'),
    ('namespace-destructor', 'namespace n{struct S{int*p;~S();};}'
     'n::S::~S(){*p=42;}int cpp_test_entry(){int n=0;{n::S s{&n};}return n;}'),
    ('private-destructor-in-owner', 'class S{~S();public:int*p;static void work(int*);};'
     'S::~S(){*p=42;}void S::work(int*p){S s;s.p=p;}'
     'int cpp_test_entry(){int n=0;S::work(&n);return n;}'),
)


@pytest.mark.parametrize('name,source', DESTRUCTOR_CASES, ids=[row[0] for row in DESTRUCTOR_CASES])
def test_destructor_original_programs_compile_deterministically(owned_only, name, source):
    units = ((name+'.cpp', source),)
    first, second = compile_host_cpp(units), compile_host_cpp(units)
    assert first.objects == second.objects
    assert first.executable == second.executable
    assert first.ir == second.ir


INVALID_DESTRUCTORS = (
    ('struct S{S(int*);~S();};int f(){S s(1);return 0;}', 'cpp.no-viable-overload'),
    ('struct S{~S();~S();};', 'cpp.redeclaration'),
    ('struct S{};S::~S(){}', 'cpp.no-matching-member'),
    ('struct S{~S();};S::~S(){}S::~S(){}', 'cpp.redefinition'),
    ('struct S{~Wrong();};', 'cpp.invalid-destructor'),
    ('struct S{~S();};S::~Wrong(){}', 'cpp.invalid-destructor'),
    ('struct S{~S(int);};', 'cpp.destructor-parameters'),
    ('struct S{~S()const;};', 'cpp.destructor-cv'),
    ('struct S{~S();};S::~S(){return 1;}', 'cpp.destructor-return-value'),
    ('void f();struct S{~S();};S::~S(){return f();}', 'cpp.destructor-return-value'),
    ('class S{~S();};int f(){S s;return 0;}', 'cpp.member-access'),
    ('namespace n{struct S{~S();};}namespace wrong{n::S::~S(){}}', 'cpp.member-definition-namespace'),
    ('struct S{~S();};int f(){switch(1){case 1:S s;case 2:return 0;}return 0;}', 'cpp.switch-jump-initialization'),
)


@pytest.mark.parametrize('source,code', INVALID_DESTRUCTORS)
def test_destructor_invalid_rules_fail_before_owned_emission(owned_only, source, code):
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid-destructor.cpp', source),))
    assert error.value.code == code


UNSUPPORTED_DESTRUCTORS = (
    'struct S{~S(){}};',
    'struct S{virtual ~S();};',
    'struct S{~S()noexcept;};',
    'struct S{~S()=default;};',
    'struct S{~S();};S::~S(){throw 1;}',
    'struct S{~S();};int f(){try{S s;}catch(...){return 1;}return 0;}',
    'struct A{};struct S{A a;~S();};',
    'struct S{~S();};struct A{S s;};int f(){A a;return 0;}',
    'struct S{~S();};struct A{S s;};int f(){A a{};return 0;}',
    'struct S{~S();};int f(){S a[2];return 0;}',
    'struct S{~S();};S global;',
    'struct S{~S();};int f(){S a;S b=a;return 0;}',
    'struct S{~S();};int f(){S a; a.~S();return 0;}',
)


@pytest.mark.parametrize('source', UNSUPPORTED_DESTRUCTORS)
def test_unimplemented_destructor_boundaries_are_not_silent(owned_only, source):
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('destructor-boundary.cpp', source),))
    assert error.value.code.startswith('cpp.unsupported.')


def test_original_destructor_declaration_is_real_D1_prototype():
    ast = CppParser().parse('struct S{~S();};')
    assert ast.ext[1].name == '_ZN1SD1Ev'
    assert len(ast.ext[1].type.args.params) == 1
    assert isinstance(ast.ext[1].type.args.params[0].type, c_ast.PtrDecl)


def test_original_constructor_and_destructor_declarations_remain_positive():
    ast = CppParser().parse('struct S{S();~S();};')
    assert [item.name for item in ast.ext[1:]] == ['_ZN1SC1Ev', '_ZN1SD1Ev']
