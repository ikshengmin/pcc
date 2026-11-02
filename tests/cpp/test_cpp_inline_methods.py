"""Completed-class inline method tests: N4659 [class.mem], [class.mfct], [basic.def.odr].

The Set excerpt is from TinyXML2 11.0.0, tinyxml2.h:152-159, by Lee Thomason,
under its Zlib license (projects/tinyxml2-11.0.0/pcc-fixture.json provenance).
It is placed in a declaration/observer fixture, not passed off as the upstream
whole project. Its release-mode assertion macro is the original upstream form.
"""
import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

SET_DECLARATION = '    void Set( char* start, char* end, int flags ) {\r\n        TIXMLASSERT( start );\r\n        TIXMLASSERT( end );\r\n        Reset();\r\n        _start  = start;\r\n        _end    = end;\r\n        _flags  = flags | NEEDS_FLUSH;\r\n    }'
SET_DECLARATION_SHA256 = '62b5369e9a0e6fe02a0c9ac083419dcf01b033849b25079a190100a69bec54c6'

SET_PROGRAM = ('#define TIXMLASSERT( x ) do {} while(false)\n'
               'int resets=0;class StrPair{public:enum{NEEDS_FLUSH=0x100};\n'
               + SET_DECLARATION + '\nvoid Reset();'
               'int Inspect(char*a,char*b)const{if(_start!=a||_end!=b)return 1;'
               'if(_flags!=(2|NEEDS_FLUSH))return 2;return 42;}'
               'private:int _flags;char*_start;char*_end;};'
               'void StrPair::Reset(){resets=resets+1;}'
               'int main(){StrPair value;StrPair*p=&value;char bytes[2]={};'
               'p->Set(bytes,bytes+1,2);if(resets!=1)return 3;return p->Inspect(bytes,bytes+1);}')

SINGLE_PROGRAMS = (
    ('later-field', 'struct S{int get(){return n;}int n;};int cpp_test_entry(){S s{42};S*p=&s;return p->get();}'),
    ('const-overload', 'struct S{int get(){return 1;}int get()const{return n;}int n;};int cpp_test_entry(){const S s{42};const S*p=&s;return p->get();}'),
    ('static-no-this', 'struct S{static int sum(int a,int b){return a+b;}};int cpp_test_entry(){return S::sum(20,22);}'),
    ('static-receiver-before-argument', 'int order=0;struct S{static int value(int x){if(order==2&&x==42)return 42;return 7;}};S*receiver(S*p){order=order+1;return p;}int argument(){if(order!=1)return 7;order=order+1;return 42;}int cpp_test_entry(){S s;return receiver(&s)->value(argument());}'),
    ('parameter-shadow', 'struct S{int sum(int n){return this->n+n;}int n;};int cpp_test_entry(){S s{17};S*p=&s;return p->sum(25);}'),
    ('later-unsigned-method', 'struct S{unsigned long long get(){return answer();}unsigned int answer(){return 4294967295U;}};int cpp_test_entry(){S s;S*p=&s;if(p->get()==4294967295ULL)return 42;return 7;}'),
    ('mutable-const-method', 'struct S{int next()const{n+=1;return n;}mutable int n;};int cpp_test_entry(){const S s{41};const S*p=&s;return p->next();}'),
    ('recursive-member', 'struct S{int count(int n){if(n==0)return 0;return 1+count(n-1);}};int cpp_test_entry(){S s;S*p=&s;return p->count(42);}'),
    ('return-before-cleanup', 'int count=0;struct Guard{~Guard();};Guard::~Guard(){count=count+1;}struct S{int get(){Guard g;return count+42;}};int cpp_test_entry(){S s;S*p=&s;int n=p->get();if(count!=1)return 1;return n;}'),
)
CLASS_SOURCE = 'struct S{int n;int get()const{return n;}static int plus(int a,int b){return a+b;}};'
MULTI_PROGRAMS = (
    ('two-tu-inline-methods', (('provider.cpp',CLASS_SOURCE+'int read(){const S s{20};const S*p=&s;return p->get();}'),
                              ('entry.cpp',CLASS_SOURCE+'int read();int cpp_test_entry(){return S::plus(read(),22);}'))),
)
ORIGINAL_METHOD_DECLARATIONS = (
    ('original-nonstatic-definition', 'struct S{int f(){return 42;}};',
     'int cpp_test_entry(){S s;S*p=&s;return p->f();}'),
    ('original-static-definition', 'struct S{static int f(){return 42;}};',
     'int cpp_test_entry(){return S::f();}'),
)
ORIGINAL_PROGRAMS = tuple((name, (('original.cpp', source), ('entry.cpp', source+entry)))
                          for name,source,entry in ORIGINAL_METHOD_DECLARATIONS)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in SINGLE_PROGRAMS) + MULTI_PROGRAMS + ORIGINAL_PROGRAMS


def walk(node):
    yield node
    for _, child in node.children():
        yield from walk(child)


def test_in_class_method_definition_metadata_and_all_prototypes_precede_bodies():
    parser = CppParser()
    tree = parser.parse('struct S{S():n(answer()){} int get(){return n;}static unsigned int answer(){return 42U;}int n;};')
    definition = parser.scope.names['S'].dtype.definition
    prototypes = [item for item in definition.method_declarations if isinstance(item, c_ast.Decl)]
    bodies = [item for item in definition.method_declarations if isinstance(item, c_ast.FuncDef)]
    assert len(prototypes) == len(bodies) == 3
    assert max(tree.ext.index(item) for item in prototypes) < min(tree.ext.index(item) for item in bodies)
    assert all(item.definition_linkage == 'external' for item in prototypes)
    assert all(item.decl.definition_linkage == 'external_odr' and not item.decl.storage and not item.decl.funcspec
               for item in bodies)
    methods = definition.inline_methods
    assert [item.symbol.method_static for item in methods] == [False, True]
    assert len(methods[0].function.decl.type.args.params) == 1
    assert len(methods[1].function.decl.type.args.params) == 0


def test_in_class_method_tokens_and_parameter_identity_are_preserved():
    parser = CppParser()
    parser.parse('struct S{int f(int n){return n+1;}int n;};',filename='original.cpp')
    definition = parser.scope.names['S'].dtype.definition
    pending, = definition.inline_methods
    assert [token.value for token in pending.tokens] == ['{','return','n','+','1',';','}']
    assert all(any(token is original for original in parser.cursor.tokens) for token in pending.tokens)
    assert all(token.coord.file == 'original.cpp' for token in pending.tokens)
    parameter = pending.parameter_scope.names['n']
    assert parameter is not definition.scope.names['n']
    assert any(isinstance(node,c_ast.ID) and node.name == parameter.c_name for node in walk(pending.function.body))
    assert parser.current_function is parser.member_function is parser.this_symbol is None


def test_original_set_body_binds_later_reset_and_private_fields():
    assert hashlib.sha256(SET_DECLARATION.encode()).hexdigest() == SET_DECLARATION_SHA256
    parser = CppParser()
    parser.parse(preprocess_cpp(SET_PROGRAM, target_triple='x86_64-unknown-linux-gnu'))
    definition = parser.scope.names['StrPair'].dtype.definition
    pending = next(item for item in definition.inline_methods if item.symbol.name == 'Set')
    reset = definition.scope.names['Reset'].overloads[0]
    calls = [node for node in walk(pending.function.body) if isinstance(node,c_ast.FuncCall)]
    assert sum(isinstance(node.name,c_ast.ID) and node.name.name == reset.c_name for node in calls) == 1
    fields = [node.field.name for node in walk(pending.function.body) if isinstance(node,c_ast.StructRef)]
    assert fields == ['_start','_end','_flags']


@pytest.mark.parametrize('name,units',PROGRAMS,ids=[item[0] for item in PROGRAMS])
def test_inline_method_original_artifacts_are_byte_stable(owned_only,name,units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', (
    ('struct S{int f() int n;};','cpp.unsupported.syntax'),
    ('struct S{int n};','cpp.unsupported.syntax'),
    ('struct S{int f(){return 42;}}','cpp.unsupported.syntax'),
    ('struct S{int n{42} int other;};','cpp.unsupported.classes'),
    ('struct S{int f()const{n=1;return 0;}int n;};','cpp.const-assignment'),
    ('struct S{static int f(){return this==nullptr;}};','cpp.this-outside-member'),
    ('struct S{static int f(){return n;}int n;};','cpp.nonstatic-member-without-object'),
    ('class S{int f(){return 42;}};int g(S*p){return p->f();}','cpp.member-access'),
    ('struct S{int f(){return 1;}int f(){return 2;}};','cpp.redeclaration'),
    ('struct S{int f(){return 1;}};int S::f(){return 2;}','cpp.redefinition'),
    ('struct S{int f(){return later();}};int later();','cpp.undeclared'),
    ('struct S{static int f()const{return 1;}};','cpp.static-member-cv'),
    ('struct S{int f(){static int n=0;return n;}};','cpp.unsupported.syntax'),
    ('struct S{int f(){thread_local int n=0;return n;}};','cpp.unsupported.syntax'),
    ('struct S{const char*f(){return "identity";}};','cpp.unsupported.syntax'),
    ('struct S{~S(){}};','cpp.unsupported.inline-member-functions'),
    ('struct S{int f()noexcept{return 42;}};','cpp.unsupported.exceptions'),
    ('struct S{int f(int n,...){return n;}};','cpp.unsupported.variadic-functions'),
))
def test_inline_method_invalid_or_unimplemented_originals_remain_explicit(source,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


@pytest.mark.parametrize('name,source,entry', ORIGINAL_METHOD_DECLARATIONS,
                         ids=[item[0] for item in ORIGINAL_METHOD_DECLARATIONS])
def test_exact_formerly_unsupported_inline_method_original_has_definition_metadata(name,source,entry):
    parser = CppParser()
    tree = parser.parse(source)
    pending, = parser.scope.names['S'].dtype.definition.inline_methods
    assert pending.symbol.defined
    assert pending.function.decl.definition_linkage == 'external_odr'
    assert pending.declaration.definition_linkage == 'external'
    assert pending.function in tree.ext


def test_inline_method_body_needs_no_extra_semicolon_before_next_member():
    parser = CppParser()
    tree = parser.parse('struct S{int get(){if(1){return n;}return 0;}int n;public:int other;};')
    definition = parser.scope.names['S'].dtype.definition
    assert [field.name for field in definition.members] == ['n', 'other']
    assert len(definition.inline_methods) == 1
    assert definition.inline_methods[0].function in tree.ext
