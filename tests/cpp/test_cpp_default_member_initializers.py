"""N4659 [class.mem], [class.base.init], [dcl.init], [stmt.dcl].

Original complete-object scalar/pointer '= expression' cases; not braced DMI,
aggregate/list initialization, inline ODR, subobjects or exception unwinding.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


DMI_CASES = (
    ('implicit-default', 'struct S{int n=42;};int cpp_test_entry(){S s;return s.n;}'),
    ('const-default', 'struct S{const int n=42;};int cpp_test_entry(){const S s;return s.n;}'),
    ('later-member', 'struct S{int n=get();int get()const;};int S::get()const{return 42;}'
     'int cpp_test_entry(){S s;return s.n;}'),
    ('later-static-member', 'struct S{int n=get();static int get();};int S::get(){return 42;}'
     'int cpp_test_entry(){S s;return s.n;}'),
    ('prior-field', 'struct S{int a=17;int b=a+25;};int cpp_test_entry(){S s;return s.b;}'),
    ('this-identity', 'struct S{S*self=this;};int cpp_test_entry(){S s;if(s.self!=&s)return 1;return 42;}'),
    ('constructor-default', 'struct S{int n=42;S();};S::S(){}int cpp_test_entry(){S s;return s.n;}'),
    ('constructor-parameter-not-captured', 'int n=42;struct S{int value=n;S(int);};S::S(int n){}'
     'int cpp_test_entry(){S s(1);return s.value;}'),
    ('member-not-parameter', 'struct S{int n=17;int value=n+25;S(int);};S::S(int n){}'
     'int cpp_test_entry(){S s(1);return s.value;}'),
    ('overridden-no-effect', 'int calls=0;int tick(){++calls;return 1;}'
     'struct S{int n=tick();S(int);};S::S(int v):n(v){}'
     'int cpp_test_entry(){S s(42);if(calls!=0)return 1;return s.n;}'),
    ('declaration-order', 'int calls=0;int tick(){return ++calls;}'
     'struct S{int a=tick();int b;int c=tick();S();};S::S():b(tick()){}'
     'int cpp_test_entry(){S s;if(s.a!=1||s.b!=2||s.c!=3||calls!=3)return 1;return 42;}'),
    ('each-object-once', 'int calls=0;int tick(){return ++calls;}'
     'struct S{int n=tick();};int cpp_test_entry(){S a;S b;if(a.n!=1||b.n!=2||calls!=2)return 1;return 42;}'),
    ('const-constructor-member', 'struct S{const int n=42;S();};S::S(){}'
     'int cpp_test_entry(){S s;return s.n;}'),
    ('pointer-null', 'struct S{int*p=nullptr;};int cpp_test_entry(){S s;if(s.p!=nullptr)return 1;return 42;}'),
    ('namespace-lookup', 'namespace N{int n=42;struct S{int value=n;};}'
     'int cpp_test_entry(){int n=1;N::S s;return s.value;}'),
    ('body-after-defaults', 'struct S{int n=40;S();};S::S(){n+=2;}'
     'int cpp_test_entry(){S s;return s.n;}'),
    ('destructor-observes-initialization', 'int result=0;struct S{int n=42;~S();};S::~S(){result=n;}'
     'int cpp_test_entry(){{S s;}return result;}'),
    ('uninitialized-field-not-zeroed', 'struct S{int a=42;int b;};'
     'int cpp_test_entry(){S s;s.b=0;return s.a+s.b;}'),
)


@pytest.mark.parametrize('name,source', DMI_CASES, ids=[row[0] for row in DMI_CASES])
def test_default_member_initializer_originals_compile_stably(owned_only, name, source):
    units = ((name+'.cpp', source),)
    first, second = compile_host_cpp(units), compile_host_cpp(units)
    assert first.objects == second.objects
    assert first.executable == second.executable
    assert first.ir == second.ir


INVALID_DMI = (
    ('struct S{int*p=1;};', 'cpp.invalid-conversion'),
    ('struct S{int n=missing;};int missing=42;', 'cpp.undeclared'),
    ('struct S{const int a;int b=42;};int f(){S s;return s.b;}', 'cpp.deleted-default-constructor'),
    ('struct S{const int a=42;S();};S::S(){a=1;}', 'cpp.const-assignment'),
    ('struct S{int n=42;};int f(){switch(1){case 1:S s;case 2:return 0;}return 0;}',
     'cpp.switch-jump-initialization'),
)


@pytest.mark.parametrize('source,code', INVALID_DMI)
def test_invalid_default_member_initializers(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, 'invalid-dmi.cpp')
    assert error.value.code == code


UNSUPPORTED_DMI = (
    'struct S{int n{42};};',
    'struct S{int n=42,m=1;};',
    'struct S{int n=42;};int f(){S s{};return s.n;}',
    'struct S{int n=42;};int f(){S s{1};return s.n;}',
    'struct S{int n=42;};int f(){S s;S t=s;return t.n;}',
    'struct S{int n=42;};int f(){S s[2];return 0;}',
    'struct S{int n=42;};S s;',
    'struct S{int n=42;};struct T{S s;};',
    'struct T{};struct S{int n=42;T t;};',
    'struct S{int n=42;};int f(){S s();return 0;}',
)


@pytest.mark.parametrize('source', UNSUPPORTED_DMI)
def test_unimplemented_default_member_paths_fail_closed(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, 'unsupported-dmi.cpp')
    assert error.value.code.startswith('cpp.unsupported.')


def test_original_default_member_initializer_declaration_is_retained():
    original = 'struct S{int x=1;S();};'
    ast = CppParser().parse(original, 'original-dmi.cpp')
    assert ast.ext[1].name == '_ZN1SC1Ev'


def test_original_default_member_initializer_class_declaration_is_retained():
    original = "struct S{int value=7;};"
    ast = CppParser().parse(original, 'original-class-dmi.cpp')
    assert ast.ext


def test_omitted_scalar_member_has_no_invented_initialization_store():
    ast = CppParser().parse('struct S{int a=42;int b;};int f(){S s;return s.a;}')
    fields = []
    class Stores(c_ast.NodeVisitor):
        def visit_Assignment(self, node):
            if isinstance(node.lvalue, c_ast.StructRef):
                fields.append(node.lvalue.field.name)
            self.generic_visit(node)
    Stores().visit(ast)
    assert fields == ['a']
