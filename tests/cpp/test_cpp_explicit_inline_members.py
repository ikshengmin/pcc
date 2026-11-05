"""Explicit inline on ordinary in-class function definitions; execution UNRUN.

N4659 [dcl.fct.spec], [class.mfct], [basic.def.odr]. In-class definitions
already have inline linkage semantics; an explicit specifier retains those
owners and is not permission to admit namespace inline declarations/variables.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'
UPSTREAM_METHOD = ('    inline static bool IsUTF8Continuation( const char p ) {\r\n'
                   '        return ( p & 0x80 ) != 0;\r\n'
                   '    }\r\n')
EXCERPT_OBSERVER = ('struct XMLUtil{\r\n' + UPSTREAM_METHOD + '};'
                    'int cpp_test_entry(){if(XMLUtil::IsUTF8Continuation(0))return 1;'
                    'if(XMLUtil::IsUTF8Continuation(127))return 2;'
                    'if(!XMLUtil::IsUTF8Continuation(-1))return 3;return 42;}')
CASES = (
    ('exact-tinyxml-inline-body', EXCERPT_OBSERVER),
    ('nonstatic-inline-prefix', 'struct S{int n;inline int get(){return n;}};'
     'int cpp_test_entry(){S s{42};return s.get();}'),
    ('inline-static-prefix', 'struct S{inline static int get(){return 42;}};'
     'int cpp_test_entry(){return S::get();}'),
    ('static-inline-prefix', 'struct S{static inline int get(){return 42;}};'
     'int cpp_test_entry(){return S::get();}'),
    ('inline-after-return-type', 'struct S{int inline get(){return 42;}};'
     'int cpp_test_entry(){S s{};return s.get();}'),
    ('interleaved-static-and-inline', 'struct S{int inline static get(){return 42;}};'
     'int cpp_test_entry(){return S::get();}'),
    ('interleaved-integer-specifiers', 'struct S{long inline long get(){return 42LL;}};'
     'int cpp_test_entry(){S s{};return int(s.get());}'),
    ('completed-class-later-member', 'class S{public:inline int get(){return later();}'
     'private:inline int later(){return 42;}};int cpp_test_entry(){S s;return s.get();}'),
    ('const-overload-selection', 'struct S{int n;inline int get(){return 1;}'
     'inline int get()const{return n;}};int cpp_test_entry(){const S s{42};return s.get();}'),
    ('mutable-member-through-const', 'struct S{mutable int n;inline int next()const{return ++n;}};'
     'int cpp_test_entry(){const S s{41};return s.next();}'),
    ('same-object-reference-result', 'struct S{int n;inline int&get(){return n;}};'
     'int cpp_test_entry(){S s{17};s.get()=42;return s.n;}'),
    ('inline-subscript-operator', 'struct S{int n;inline int&operator[](int i){return n;}};'
     'int cpp_test_entry(){S s{17};s[0]=42;return s.n;}'),
    ('default-argument-retained', 'struct S{inline int get(int n=42){return n;}};'
     'int cpp_test_entry(){S s{};return s.get();}'),
    ('receiver-before-argument', 'int trace=0;struct S{inline static int take(int n){'
     'if(trace!=2)return 1;return n;}};S*receiver(S*p){trace=1;return p;}'
     'int argument(){if(trace!=1)return 1;trace=2;return 42;}'
     'int cpp_test_entry(){S s{};return receiver(&s)->take(argument());}'),
)
CLASS_SOURCE = ('struct S{int n;inline int get()const{return n;}'
                'inline static int add(int a,int b){return a+b;}};')
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    ('two-tu-explicit-inline-odr', (
        ('provider.cpp', CLASS_SOURCE + 'int read(){const S s{20};return s.get();}'),
        ('entry.cpp', CLASS_SOURCE + 'int read();int cpp_test_entry(){return S::add(read(),22);}'),
    )),
)
INVALID = (
    ('duplicate-prefix', 'struct S{inline inline int f(){return 0;}};', 'cpp.duplicate-inline-specifier'),
    ('duplicate-across-type', 'struct S{inline int inline f(){return 0;}};', 'cpp.duplicate-inline-specifier'),
    ('duplicate-after-type', 'struct S{int inline inline f(){return 0;}};', 'cpp.duplicate-inline-specifier'),
    ('nonstatic-data-member', 'struct S{inline int n;};', 'cpp.invalid-inline-specifier'),
    ('extern-storage-member', 'struct S{inline extern int f(){return 0;}};', 'cpp.invalid-member-storage'),
    ('duplicate-storage', 'struct S{static inline static int f(){return 0;}};', 'cpp.invalid-storage-specifier'),
    ('mutable-function', 'struct S{mutable inline int f(){return 0;}};', 'cpp.invalid-mutable'),
    ('static-this', 'struct S{inline static int f(){return this==nullptr;}};', 'cpp.this-outside-member'),
    ('const-write', 'struct S{int n;inline int f()const{n=1;return n;}};', 'cpp.const-assignment'),
)
DEFERRED = (
    ('in-class-prototype-only', 'struct S{inline int f();};', 'cpp.unsupported.inline-member-declarations'),
    ('static-prototype-only', 'struct S{static inline int f();};', 'cpp.unsupported.inline-member-declarations'),
    ('inline-static-variable', 'struct S{inline static int n=42;};', 'cpp.unsupported.inline-declarations'),
    ('namespace-inline-function', 'inline int f(){return 42;}', 'cpp.unsupported.inline-declarations'),
    ('out-of-class-inline-definition', 'struct S{int f();};inline int S::f(){return 42;}', 'cpp.unsupported.inline-declarations'),
    ('internal-class-inline-owner', 'namespace{struct S{inline int f(){return 42;}};}', 'cpp.unsupported.class-linkage'),
)


def test_inline_member_tinyxml_method_is_complete_unchanged_source():
    root = Path(__file__).resolve().parents[2]
    lines = (root / 'projects/tinyxml2-11.0.0/tinyxml2.h').read_bytes().splitlines(keepends=True)
    assert b''.join(lines[604:607]) == UPSTREAM_METHOD.encode()


def test_explicit_inline_keeps_original_tokens_and_existing_odr_owner():
    parser = CppParser(TARGET)
    tree = parser.parse('struct S{inline int get(int n){return n+1;}'
                        'static inline int answer(){return 42;}};', 'inline-original.cpp')
    definition = parser.scope.names['S'].dtype.definition
    methods = definition.inline_methods
    assert [method.symbol.explicit_inline for method in methods] == [True, True]
    assert [method.symbol.method_static for method in methods] == [False, True]
    assert [token.value for token in methods[0].tokens] == ['{', 'return', 'n', '+', '1', ';', '}']
    assert all(any(token is original for original in parser.cursor.tokens)
               for method in methods for token in method.tokens)
    assert all(method.function.decl.definition_linkage == 'external_odr'
               and method.function.decl.funcspec == [] for method in methods)
    prototypes = [node for node in definition.method_declarations if isinstance(node, c_ast.Decl)]
    bodies = [node for node in definition.method_declarations if isinstance(node, c_ast.FuncDef)]
    assert max(tree.ext.index(node) for node in prototypes) < min(tree.ext.index(node) for node in bodies)
    assert len(methods[0].function.decl.type.args.params) == 2
    assert len(methods[1].function.decl.type.args.params) == 0


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_explicit_inline_member_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED,
                         ids=[row[0] for row in INVALID + DEFERRED])
def test_explicit_inline_member_boundaries_precede_ir(owned_only, name, source, code, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or deferred inline member reached IR generation')
    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name + '.cpp', source),))
    assert caught.value.code == code
