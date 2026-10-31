"""Non-inline pointer-receiver members, not general member/lifetime support.

Original C++17 sources for N4659 [class.mfct], [over.match.funcs], [class.access]
and Itanium 3.1.2.1/5.1.5. Dot-call sources below remain valid-but-unsupported:
the existing shared C address operator can pre-read aggregates. No zero-init,
array-wrapper, fake builtin or source rewrite supplies their missing semantics.

Native pointer inputs are supplied by an independent foreign ABI fixture.
PCC compiles each original C++ caller/method normally. The fixture supplies
storage/values, not member semantics; no C++ constructor/allocation claim.
"""
import json
import os
import re
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (Execution, compile_host_cpp, compile_reference_cpp,
                                      write_host_executable)
from tests.cpp.test_cpp_class_execution import record_execution

DEFERRED_DOT_CASES = (('formerly-unsupported-original-member-declaration', 'struct S{int method();};int S::method(){return 42;}int cpp_test_entry(){S s{};return s.method();}'), ('field-read', 'struct S{int x;int get();};int S::get(){return x;}int cpp_test_entry(){S s{42};return s.get();}'), ('mutating-member', 'struct S{int x;void add(int n);int get()const;};void S::add(int n){x+=n;}int S::get()const{return x;}int cpp_test_entry(){S s{17};s.add(25);return s.get();}'), ('const-object', 'struct S{int x;int get()const;};int S::get()const{return x;}int cpp_test_entry(){const S s{42};return s.get();}'), ('cv-overload-mutable-preferred', 'struct S{int get();int get()const;};int S::get(){return 42;}int S::get()const{return 1;}int cpp_test_entry(){S s{};return s.get();}'), ('cv-overload-const-only-viable', 'struct S{int get();int get()const;};int S::get(){return 1;}int S::get()const{return 42;}int cpp_test_entry(){const S s{};return s.get();}'), ('explicit-parameter-overloads', 'struct S{int f(int);int f(long);};int S::f(int x){return x+17;}int S::f(long x){return 1;}int cpp_test_entry(){S s{};return s.f(25);}'), ('private-data-inside-member', 'class S{int x;public:void set(int n);int get()const;};void S::set(int n){x=n;}int S::get()const{return x;}int cpp_test_entry(){S s;s.set(42);return s.get();}'), ('private-method-called-from-member', 'class S{int hidden(int);public:int get();};int S::hidden(int x){return x;}int S::get(){return hidden(42);}int cpp_test_entry(){S s;return s.get();}'), ('mutable-in-const-method', 'struct S{mutable int x;int bump()const;};int S::bump()const{return ++x;}int cpp_test_entry(){const S s{41};return s.bump();}'), ('parameter-hides-member', 'struct S{int x;int f(int x);};int S::f(int x){return x+this->x;}int cpp_test_entry(){S s{17};return s.f(25);}'), ('local-hides-member-qualified-retains-object', 'struct S{int x;int f();};int S::f(){int x=25;return x+S::x;}int cpp_test_entry(){S s{17};return s.f();}'), ('namespace-qualified-definition', 'namespace n{struct S{int x;int f();};}int n::S::f(){return x;}int cpp_test_entry(){n::S s{42};return s.f();}'), ('namespace-local-definition', 'namespace n{struct S{int x;int f();};int S::f(){return x;}}int cpp_test_entry(){n::S s{42};return s.f();}'), ('class-parameter-type-lookup', 'struct S{using I=int;I x;int f(I);};int S::f(I n){return x+n;}int cpp_test_entry(){S s{17};return s.f(25);}'), ('return-this-and-chain', 'struct S{int x;S*self();int get()const;};S*S::self(){return this;}int S::get()const{return x;}int cpp_test_entry(){S s{42};return s.self()->get();}'), ('dot-object-effect-once', 'struct S{int x;int get();};int S::get(){return x;}S*pick(S*p,int*n){*n+=1;return p;}int cpp_test_entry(){S s{42};int n=0;int r=(*pick(&s,&n)).get();if(n!=1)return 1;return r;}'), ('member-through-nested-subobject', 'struct S{int x;int get();};int S::get(){return x;}struct Outer{S s;};int cpp_test_entry(){Outer o{{42}};return o.s.get();}'), ('member-recursion', 'struct S{int sum(int);};int S::sum(int n){if(n==0)return 0;return n+sum(n-1);}int cpp_test_entry(){S s{};return s.sum(8)+6;}'), ('member-main-is-not-program-main', 'struct S{int main(int);};int S::main(int x){return x;}int cpp_test_entry(){S s{};return s.main(42);}'), ('extern-c-block-does-not-change-member-linkage', 'extern "C"{struct S{int f(int);};}int S::f(int x){return x;}int cpp_test_entry(){S s{};return s.f(42);}'), ('member-enum-lookup', 'struct S{enum class E{A=17};int f(E);};int S::f(E e){return static_cast<int>(e)+25;}int cpp_test_entry(){S s{};return s.f(S::E::A);}'), ('array-parameter-adjustment', 'struct S{int f(int a[2]);};int S::f(int a[2]){return a[0]+a[1];}int cpp_test_entry(){S s{};int a[2]={17,25};return s.f(a);}'), ('unnamed-definition-slot', 'struct S{int f(int,int);};int S::f(int,int n){return n;}int cpp_test_entry(){S s{};return s.f(7,42);}'), ('this-shifts-register-and-stack-arguments', 'struct S{int x;int f(int,int,int,int,int,int,int,int);};int S::f(int a,int b,int c,int d,int e,int f,int g,int h){return x+a+b+c+d+e+f+g+h;}int cpp_test_entry(){S s{6};return s.f(1,2,3,4,5,6,7,8);}'), ('const-pointer-parameter-and-class-substitution', 'struct S{int x;int f(const S*)const;};int S::f(const S*p)const{return x+p->x;}int cpp_test_entry(){const S a{17};S b{25};return a.f(&b);}'), ('namespace-class-substitution', 'namespace n{struct S{int x;int f(S*);};int S::f(S*p){return x+p->x;}}int cpp_test_entry(){n::S a{17};n::S b{25};return a.f(&b);}'), ('ordinary-template-coexists', 'struct S{int f();};template<class T>T identity(T x){return x;}int S::f(){return identity(42);}int cpp_test_entry(){S s{};return s.f();}'), ('full-width-integer-parameter-and-return', 'struct S{unsigned long long x;unsigned long long f(unsigned long long)const;};unsigned long long S::f(unsigned long long n)const{return x+n;}int cpp_test_entry(){const S s{4294967296ULL};if(s.f(8589934592ULL)!=12884901888ULL)return 1;return 42;}'), ('floating-parameter-separate-from-this-register', 'struct S{double x;double f(double)const;};double S::f(double n)const{return x+n;}int cpp_test_entry(){const S s{17.0};return int(s.f(25.0));}'), ('private-return-alias-access-is-deferred-until-owner-known', 'class S{using I=int;public:I f();};S::I S::f(){return 42;}int cpp_test_entry(){S s;return s.f();}'), ('method-lookup-suppresses-adl', 'namespace n{struct Tag{};int f(Tag*){return 1;}}struct S{int f(n::Tag*);int get(n::Tag*);};int S::f(n::Tag*p){return 42;}int S::get(n::Tag*p){return f(p);}int cpp_test_entry(){S s{};n::Tag t{};return s.get(&t);}'))

INVALID = (('class S{using I=int;};S::I unrelated(){return 0;}', 'cpp.member-access'), ('class S{using I=int;};S::I unrelated=0;', 'cpp.member-access'), ('class S{using I=int;public:I f();};S::I S::f(){return 42;}S::I unrelated(){return 0;}', 'cpp.member-access'), ('struct S{int f();int f();};', 'cpp.redeclaration'), ('struct S{int x;int x();};', 'cpp.redeclaration'), ('struct S{int f();};long S::f(){return 0;}', 'cpp.conflicting-return-type'), ('struct S{int f(int);};int S::f(long x){return 0;}', 'cpp.no-matching-member'), ('struct S{};int S::f(){return 0;}', 'cpp.no-matching-member'), ('struct S{int f()const;};int S::f(){return 0;}', 'cpp.no-matching-member'), ('struct S{int f();};int S::f(){return 0;}int S::f(){return 1;}', 'cpp.redefinition'), ('struct S{int f();};int S::f();', 'cpp.member-definition-required'), ('namespace n{struct S{int f();};}namespace other{int n::S::f(){return 0;}}', 'cpp.member-definition-namespace'), ('struct S{int x;int f()const;};int S::f()const{x=1;return 0;}', 'cpp.const-assignment'), ('struct S{int f();};int S::f(){return 0;}int f(){const S s{};return s.f();}', 'cpp.no-viable-overload'), ('class S{int f();};int S::f(){return 0;}int f(){S s;return s.f();}', 'cpp.member-access'), ('class S{int f(int);public:int f(long);};int f(){S s;return s.f(1);}', 'cpp.member-access'), ('struct S{int f();};int f(){return S::f();}', 'cpp.nonstatic-member-without-object'), ('int f(){return this;}', 'cpp.this-outside-member'), ('struct S{int f();};int S::f(){this=nullptr;return 0;}', 'cpp.not-lvalue'), ('struct S{int f();};int S::f(){return missing;}', 'cpp.undeclared'), ('struct S{int f(int);};int f(){S s;return s.f();}', 'cpp.no-viable-overload'), ('struct S{int f(int)const;int f(long);};int f(){S s;return s.f(1);}', 'cpp.ambiguous-overload'), ('struct S{int S();};', 'cpp.invalid-constructor'), ('struct S{mutable int f();};', 'cpp.invalid-mutable'), ('struct A{int x;};struct B{int f();};int B::f(){return A::x;}', 'cpp.nonstatic-member-without-object'))

UNSUPPORTED = ('struct S{int f(){return 42;}};', 'struct S{int f()volatile;};', 'struct S{int f()&;};', 'struct S{static int f();};', 'struct S{virtual int f();};', 'struct S{S();};', 'struct S{~S();};', 'struct S{int f();};int g(){S s;return s.f;}', 'struct S{int f();};template<class T>int g(T n){S s{};return s.f();}', 'struct S{S f();};')

ORIGINAL_MEMBER_DECLARATION = 'struct S{int method();};'

@pytest.mark.integration
def test_method_exports_match_independent_cpp17_reference(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import parse_relocatable
    source = ('struct S{int f(S*);int f(const S*)const;};'
              'int S::f(S*p){return 0;}int S::f(const S*p)const{return 0;}'
              'namespace n{struct S{int f(S*);};int S::f(S*p){return 0;}}'
              'int cpp_test_entry(){return 0;}')
    artifact = compile_host_cpp((('exports.cpp', source),))
    unit, output = tmp_path / 'exports.cpp', tmp_path / 'reference.s'
    unit.write_text(source)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', '-S', str(unit), '-o', str(output)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r'^\s*\.globl\s+(_Z\w+)', output.read_text(), re.MULTILINE))
    owned = {s.name for s in parse_relocatable(artifact.objects[0]).symbols if s.binding == 1 and s.section_index and s.name.startswith('_Z')}
    assert owned == reference
    assert len(owned) == 4
    (tmp_path / 'owned.o').write_bytes(artifact.objects[0])
    (tmp_path / 'exports.json').write_text(json.dumps({'owned': sorted(owned), 'reference': sorted(reference)}, indent=2))


def test_original_member_declaration_is_a_real_implicit_object_prototype():
    from pcc.frontends.c.ast import c_ast
    ast = CppParser().parse(ORIGINAL_MEMBER_DECLARATION)
    assert len(ast.ext) == 2
    record, function = ast.ext
    assert isinstance(record.type, c_ast.Struct)
    assert function.name == "_ZN1S6methodEv"
    assert isinstance(function.type, c_ast.FuncDecl)
    assert len(function.type.args.params) == 1
    assert isinstance(function.type.args.params[0].type, c_ast.PtrDecl)

# name, exact class declaration, exact out-of-class bodies, caller body,
# independent reference object initializer, independent owned object bytes.
POINTER_CASES = (
    ('receiver-name-cannot-be-hidden-by-this-temporary',
     'struct S{int x;int get();};', 'int S::get(){return x;}',
     'S*this_argument=fixture();return this_argument->get();', '{42}', '.quad 42'),
    ('argument-name-cannot-be-hidden-by-this-temporary',
     'struct S{int x;int add(int);};', 'int S::add(int n){return x+n;}',
     'int this_argument=25;return fixture()->add(this_argument);', '{17}', '.quad 17'),
    ('nested-member-captures-have-independent-temporaries',
     'struct S{int x;int get();int add(int);};',
     'int S::get(){return 25;}int S::add(int n){return x+n;}',
     'return fixture()->add(fixture()->get());', '{17}', '.quad 17'),
    ('read-field', 'struct S{int x;int get();};', 'int S::get(){return x;}',
     'return fixture()->get();', '{42}', '.quad 42'),
    ('explicit-this', 'struct S{int x;int get();};', 'int S::get(){return this->x;}',
     'return fixture()->get();', '{42}', '.quad 42'),
    ('modify-then-read', 'struct S{int x;void add(int);int get()const;};',
     'void S::add(int n){x+=n;}int S::get()const{return x;}',
     'S*p=fixture();p->add(25);return p->get();', '{17}', '.quad 17'),
    ('const-receiver-overload', 'struct S{int f();int f()const;};',
     'int S::f(){return 1;}int S::f()const{return 42;}',
     'const S*p=fixture();return p->f();', '{}', '.quad 0'),
    ('mutable-receiver-overload', 'struct S{int f();int f()const;};',
     'int S::f(){return 42;}int S::f()const{return 1;}',
     'return fixture()->f();', '{}', '.quad 0'),
    ('explicit-overload', 'struct S{int f(int);int f(long);};',
     'int S::f(int n){return n;}int S::f(long n){return 1;}',
     'return fixture()->f(42);', '{}', '.quad 0'),
    ('private-field', 'class S{int x;public:void set(int);int get()const;};',
     'void S::set(int n){x=n;}int S::get()const{return x;}',
     'S*p=fixture();p->set(42);return p->get();', '{}', '.quad 0'),
    ('private-method-implicit-this', 'class S{int hidden(int);public:int get();};',
     'int S::hidden(int n){return n;}int S::get(){return hidden(42);}',
     'return fixture()->get();', '{}', '.quad 0'),
    ('mutable-field-through-const', 'struct S{mutable int x;int bump()const;};',
     'int S::bump()const{return ++x;}',
     'const S*p=fixture();return p->bump();', '{41}', '.quad 41'),
    ('parameter-shadow', 'struct S{int x;int f(int);};',
     'int S::f(int x){return this->x+x;}',
     'return fixture()->f(25);', '{17}', '.quad 17'),
    ('recursive-member', 'struct S{int sum(int);};',
     'int S::sum(int n){if(n==0)return 0;return n+sum(n-1);}',
     'return fixture()->sum(8)+6;', '{}', '.quad 0'),
    ('return-this-and-chain', 'struct S{int x;S*self();int get()const;};',
     'S*S::self(){return this;}int S::get()const{return x;}',
     'return fixture()->self()->get();', '{42}', '.quad 42'),
    ('object-before-argument-once', 'struct S{int x;int f(int);};',
     'int S::f(int n){return x+n;}extern "C" S*fixture();'
     'S*pick(int*n){*n=*n*10+1;return fixture();}int arg(int*n){*n=*n*10+2;return 25;}',
     'int n=0;int r=pick(&n)->f(arg(&n));if(n!=12)return 1;return r;', '{17}', '.quad 17'),
    ('this-plus-eight-explicit-arguments', 'struct S{int x;int f(int,int,int,int,int,int,int,int);};',
     'int S::f(int a,int b,int c,int d,int e,int f,int g,int h){return x+a+b+c+d+e+f+g+h;}',
     'return fixture()->f(1,2,3,4,5,6,7,8);', '{6}', '.quad 6'),
    ('wide-parameter-return', 'struct S{unsigned long long x;unsigned long long f(unsigned long long)const;};',
     'unsigned long long S::f(unsigned long long n)const{return x+n;}',
     'if(fixture()->f(8589934592ULL)!=12884901888ULL)return 1;return 42;', '{4294967296ULL}', '.quad 4294967296'),
    ('floating-argument-register', 'struct S{double x;double f(double)const;};',
     'double S::f(double n)const{return x+n;}',
     'return int(fixture()->f(25.0));', '{17.0}', '.quad 4625478292286210048'),
    ('private-return-type-alias', 'class S{using I=int;public:I f();};',
     'S::I S::f(){return 42;}', 'return fixture()->f();', '{}', '.quad 0'),
    ('ordinary-template-in-member', 'struct S{int f();};',
     'template<class T>T identity(T x){return x;}int S::f(){return identity(42);}',
     'return fixture()->f();', '{}', '.quad 0'),
    ('member-main', 'struct S{int main(int);};', 'int S::main(int n){return n;}',
     'return fixture()->main(42);', '{}', '.quad 0'),
    ('class-parameter-alias', 'struct S{using I=int;int x;int f(I);};',
     'int S::f(I n){return x+n;}', 'return fixture()->f(25);', '{17}', '.quad 17'),
)


def fixture_source(case):
    name, header, methods, body, initializer, data = case
    source = header + methods + 'extern "C" S*fixture();int cpp_test_entry(){' + body + '}'
    reference_provider = header + 'extern "C" S*fixture(){static S value' + initializer + ';return &value;}'
    assembly = ('.intel_syntax noprefix\n.data\n.p2align 3\npcc_member_fixture_record:\n' + data +
                '\n.text\n.globl fixture\nfixture:\nlea rax, pcc_member_fixture_record[rip]\nret\n')
    return source, reference_provider, assembly


@pytest.mark.parametrize('case', POINTER_CASES, ids=[row[0] for row in POINTER_CASES])
def test_pointer_receivers_compile_deterministically_without_aggregate_preread(case):
    source, provider, assembly = fixture_source(case)
    first = compile_host_cpp(((case[0]+'.cpp', source),), owned_support_assembly=(assembly,))
    assert first == compile_host_cpp(((case[0]+'.cpp', source),), owned_support_assembly=(assembly,))
    assert not re.search(r'\bload\s+(?:%"[^"]+"|%[\w.$]+)\s*,', '\n'.join(first.ir))


@pytest.mark.parametrize('name,source', DEFERRED_DOT_CASES, ids=[row[0] for row in DEFERRED_DOT_CASES])
def test_exact_original_dot_positive_intent_remains_explicitly_deferred(name, source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == 'cpp.unsupported.member-object-address'


@pytest.mark.parametrize('source,code', INVALID)
def test_member_rules_still_reject_before_address_capability(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid member semantics reached emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid-member.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('source', UNSUPPORTED)
def test_other_member_dependencies_remain_explicit(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code.startswith('cpp.unsupported.')


@pytest.mark.parametrize('case', POINTER_CASES, ids=[row[0] for row in POINTER_CASES])
@pytest.mark.integration
def test_pointer_receiver_original_source_executes_with_foreign_object_fixture(case, tmp_path, monkeypatch):
    source, provider, assembly = fixture_source(case)
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned member compilation started external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(((case[0]+'.cpp', source),), owned_support_assembly=(assembly,))
    binary = tmp_path/'owned'
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack('<i',42), b'')
    record_execution(binary, expected)
    (tmp_path/'provider.s').write_text(assembly)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    reference = compile_reference_cpp(((case[0]+'.cpp',source),('provider.cpp',provider)),tmp_path/'reference',compiler=compiler)
    record_execution(reference, expected)


@pytest.mark.parametrize('source', (
    'struct S{int x;void set(int);};void S::set(int n){x=n;}void f(S*p){p->set(42);}',
    'struct S{int x;int f();int g();};int S::f(){return g();}int S::g(){return x;}',
))
def test_public_member_pipeline_does_not_load_object_to_form_this(source):
    from tests.cpp.test_cpp_public_driver import compile_cpp
    ir = compile_cpp(source, 'pointer-member-public.cpp', use_system_cpp=False)[0][1]
    assert not re.search(r'\bload\s+(?:%"[^"]+"|%[\w.$]+)\s*,', ir)


@pytest.mark.integration
def test_pointer_method_this_and_stack_slots_observed_by_independent_assembly(tmp_path):
    header='struct S{int x;int check(int,int,int,int,int,int,int,int)const;};'
    case=('stack-observer',header,'','return fixture()->check(1,2,3,4,5,6,7,8);','{6}','.quad 6')
    source, provider, assembly=fixture_source(case)
    observer=('.intel_syntax noprefix\n.text\n.globl _ZNK1S5checkEiiiiiiii\n'
              '_ZNK1S5checkEiiiiiiii:\nmov eax,dword ptr [rdi]\nadd eax,esi\nadd eax,edx\n'
              'add eax,ecx\nadd eax,r8d\nadd eax,r9d\nadd eax,dword ptr [rsp+8]\n'
              'add eax,dword ptr [rsp+16]\nadd eax,dword ptr [rsp+24]\nret\n')
    artifact=compile_host_cpp((('caller.cpp',source),),owned_support_assembly=(assembly,observer))
    assert artifact==compile_host_cpp((('caller.cpp',source),),owned_support_assembly=(assembly,observer))
    binary=tmp_path/'owned';write_host_executable(artifact,binary)
    expected=Execution(42,struct.pack('<i',42),b'');record_execution(binary,expected)
    (tmp_path/'observer.s').write_text(observer);(tmp_path/'provider.s').write_text(assembly)
    body=header+'int S::check(int a,int b,int c,int d,int e,int f,int g,int h)const{return x+a+b+c+d+e+f+g+h;}'
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'));assert compiler
    reference=compile_reference_cpp((('caller.cpp',source),('body.cpp',body),('provider.cpp',provider)),tmp_path/'reference',compiler=compiler)
    record_execution(reference,expected)


@pytest.mark.integration
def test_pointer_member_calls_between_independent_cpp_translation_units(tmp_path):
    header='struct S{int x;int add(int)const;};'
    case=('two-units',header,'','return fixture()->add(25);','{17}','.quad 17')
    source,provider,assembly=fixture_source(case)
    units=(('caller.cpp',source),('implementation.cpp',header+'int S::add(int n)const{return x+n;}'))
    artifact=compile_host_cpp(units,owned_support_assembly=(assembly,))
    assert artifact==compile_host_cpp(units,owned_support_assembly=(assembly,))
    binary=tmp_path/'owned';write_host_executable(artifact,binary)
    expected=Execution(42,struct.pack('<i',42),b'');record_execution(binary,expected)
    (tmp_path/'provider.s').write_text(assembly)
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'));assert compiler
    reference=compile_reference_cpp(units+(('provider.cpp',provider),),tmp_path/'reference',compiler=compiler)
    record_execution(reference,expected)
