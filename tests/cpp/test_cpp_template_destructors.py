"""Template D1 ownership: authored C++17 CI obligations, execution UNRUN.

N4659 [temp.inst], [class.dtor], [basic.def.odr], [stmt.jump]/2 and
[stmt.return]/3. https://timsong-cpp.github.io/cppwp/n4659/temp.inst
https://timsong-cpp.github.io/cppwp/n4659/class.dtor

The exact DynArray destructor below is TinyXML2 11.0.0 tinyxml2.h:213-217,
revision 9148bdf719e997d1f474be6bcc7943881046dba1, Lee Thomason, Zlib.
See projects/tinyxml2-11.0.0/{LICENSE.txt,pcc-fixture.json}. The pointer-field
scaffold is PCC-authored, not original DynArray: its dependent array field,
reference operators and allocating new/delete remain independent boundaries.
The unchanged whole-project obligation remains test_cpp_projects.py's
test_cpp_tinyxml2_upstream_tests_explicit_host_pcc_project. Its actual first
failure must be retained separately; none of these excerpt tests is a pass
for original DynArray, xmltest, native pcc1 or the entire project.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.class_template_members import MethodInstance, MethodPattern
from pcc.frontends.cpp.templates import Pattern
from pcc.frontends.cpp.types import PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TRACE_CLASS = ('template<class T>struct Box{T*p;int digit;'
               '~Box(){*p=*p*10+digit;}};')
ORIGINAL_EMPTY_D1 = 'template<class T>struct Box{~Box(){}};'
ORIGINAL_SPACED_LEGACY_D1 = 'template<class T> struct Box { ~Box(){} };'


def traced(body, expected):
    return TRACE_CLASS + 'int cpp_test_entry(){int trace=0;' + body + (
        'if(trace!=' + str(expected) + ')return 1;return 42;}')


SINGLE_PROGRAMS = (
    ('reverse-order', traced('{Box<int>a{&trace,1};Box<int>b{&trace,2};}', 21)),
    ('nested-scopes', traced('{Box<int>a{&trace,1};{Box<int>b{&trace,2};}}', 21)),
    ('const-object', traced('{const Box<int>a{&trace,4};}', 4)),
    ('unconstructed-branch', traced('if(false){Box<int>a{&trace,1};}', 0)),
    ('for-continue-retains-init', traced(
        'for(Box<int>init{&trace,9};trace<11;){Box<int>body{&trace,1};continue;}', 119)),
    ('for-break-cleans-body-first', traced(
        'for(Box<int>init{&trace,9};true;){Box<int>body{&trace,2};break;}', 29)),
    ('return-value-before-cleanup', TRACE_CLASS +
     'int work(int*p){Box<int>b{p,2};*p=1;return *p;}'
     'int cpp_test_entry(){int trace=0;int value=work(&trace);'
     'if(value!=1||trace!=12)return 1;return 42;}'),
    ('c1-and-early-d1-return',
     'template<class T>struct Box{T*p;Box(T*p):p(p){}'
     '~Box(){if(*p==0){*p=42;return;}*p=1;}};'
     'int cpp_test_entry(){int value=0;{Box<int>b(&value);}return value;}'),
    ('later-private-helper',
     'template<class T>class Box{public:Box(T*p):p(p){}~Box(){store();}'
     'private:T*p;void store(){*p=42;}};'
     'int cpp_test_entry(){int value=0;{Box<int>b(&value);}return value;}'),
    ('distinct-specializations',
     'template<class T,int N>struct Box{T*p;~Box(){*p+=N;}};'
     'int cpp_test_entry(){int n=0;long m=0;'
     '{Box<int,20>a{&n};Box<long,22>b{&m};}return n+int(m);}'),
    ('unused-delete-body',
     'template<class T>struct Box{T*p;~Box(){delete[]p;}int value(){return 42;}};'
     'int cpp_test_entry(){Box<int>*p=nullptr;if(p==nullptr)return 42;return 1;}'),
)

SHARED_CLASS = 'template<class T>struct Box{T*p;int n;~Box(){*p+=n;}};'
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in SINGLE_PROGRAMS) + (
    ('retained-original-unused-template-d1', (
        ('original.cpp', ORIGINAL_EMPTY_D1),
        ('entry.cpp', 'int cpp_test_entry(){return 42;}'),
    )),
    ('retained-spaced-legacy-template-d1', (
        ('original.cpp', ORIGINAL_SPACED_LEGACY_D1),
        ('entry.cpp', 'int cpp_test_entry(){return 42;}'),
    )),
    ('two-tu-d1-odr', (
        ('helper.cpp', SHARED_CLASS + 'void helper(int*p){Box<int>b{p,17};}'),
        ('entry.cpp', SHARED_CLASS + 'void helper(int*);'
         'int cpp_test_entry(){int n=0;helper(&n);{Box<int>b{&n,25};}return n;}'),
    )),
)

DYNARRAY_DESTRUCTOR = (
    '    ~DynArray() {\r\n'
    '        if ( _mem != _pool ) {\r\n'
    '            delete [] _mem;\r\n'
    '        }\r\n'
    '    }\r\n'
)
DYNARRAY_SCAFFOLD = ('template<class T>struct DynArray{T*_mem;T*_pool;' +
                     DYNARRAY_DESTRUCTOR + '};')


def patterns(node):
    node = getattr(node, 'node', node)
    if not isinstance(node, Pattern):
        return
    yield node
    for child in node.children:
        yield from patterns(child)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_template_d1_originals_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_primary_d1_is_a_typed_method_pattern_without_an_instance_or_body_emission():
    parser = CppParser()
    tree = parser.parse('template<class T>struct Box{~Box(){*p=42;}private:T*p;};', 'primary.cpp')
    owner = parser.scope.names['Box'].dtype
    record, = owner.methods
    assert isinstance(record, MethodPattern)
    assert record.symbol is owner.method_definition.destructor
    assert record.symbol.destructor and record.symbol.dtype == 'void'
    assert record.symbol.access == 'public' and record.symbol.parameters == ()
    assert record.this.dtype == PointerType(owner.injected)
    assert record.body.kind == 'block'
    assert all(token.coord.file == 'primary.cpp' for token in record.tokens)
    assert all(any(token is original for original in parser.cursor.tokens) for token in record.tokens)
    assert not parser.instance_registry.entries
    assert not any(isinstance(item, c_ast.FuncDef) for item in tree.ext)


def test_original_rejected_template_d1_is_now_a_primary_pattern():
    # Exact former negative input, with no inserted observer or source edits.
    parser = CppParser()
    tree = parser.parse(ORIGINAL_EMPTY_D1, 'original.cpp')
    owner = parser.scope.names['Box'].dtype
    record, = owner.methods
    assert isinstance(record, MethodPattern)
    assert record.symbol is owner.method_definition.destructor
    assert record.symbol.destructor and record.body.kind == 'block'
    assert record.body.children == []
    assert not parser.instance_registry.entries
    assert not tree.ext


def test_complete_layout_does_not_request_a_d1_body():
    parser = CppParser()
    tree = parser.parse('template<class T>struct Box{T value;~Box(){value=value%2;}};'
                        'int read(Box<double>*p){return int(p->value);}')
    instance = next(entry.value for entry in parser.instance_registry.entries.values()
                    if getattr(entry.value, 'template_origin', None) is parser.scope.names['Box'])
    destructor = instance.definition.destructor
    assert isinstance(destructor.class_template_instance, MethodInstance)
    assert not destructor.defined
    assert not any(isinstance(item, c_ast.FuncDef) and item.decl.name == destructor.c_name
                   for item in tree.ext)
    assert parser.instance_registry.get((destructor.class_template_instance.record.symbol,
                                         destructor.class_template_instance.arguments)) is None


def test_used_d1_has_concrete_this_d1_identity_and_external_odr_definition():
    parser = CppParser()
    tree = parser.parse('namespace N{template<class T>struct Box{T*p;~Box(){*p=42;}};}'
                        'int cpp_test_entry(){int n=0;{N::Box<int>b{&n};}return n;}')
    function = next(node for node in tree.ext if isinstance(node, c_ast.FuncDef)
                    and node.decl.name == '_ZN1N3BoxIiED1Ev')
    entry = next(entry for entry in parser.instance_registry.entries.values()
                 if isinstance(entry.context, MethodInstance))
    context = entry.context
    assert context.symbol.class_owner.definition.destructor is context.symbol
    assert context.this.dtype == PointerType(context.symbol.class_owner)
    assert context.record.symbol.destructor
    assert entry.key == (context.record.symbol, context.arguments)
    assert function.decl.definition_linkage == 'external_odr' and not function.decl.storage
    assert context.symbol.defined and context.symbol.closed_destruction_body
    assert parser.lifetimes.frames == []
    assert parser.current_function is parser.member_function is parser.this_symbol is None


def test_original_dynarray_d1_retains_typed_dependent_delete_and_source_bytes():
    original = Path(__file__).parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.h'
    assert DYNARRAY_DESTRUCTOR.encode() in original.read_bytes()
    parser = CppParser()
    tree = parser.parse(DYNARRAY_SCAFFOLD, 'excerpt.cpp')
    record, = parser.scope.names['DynArray'].dtype.methods
    deletion, = (node for node in patterns(record.body) if node.kind == 'delete')
    assert deletion.value == (False, True)
    assert deletion.coord.file == 'excerpt.cpp'
    operand, = deletion.children
    assert operand.dtype == PointerType(parser.scope.names['DynArray'].dtype.parameters[0])
    assert operand.symbol is parser.scope.names['DynArray'].dtype.member_scope.names['_mem']
    assert not any(isinstance(node, c_ast.FuncDef) for node in tree.ext)


@pytest.mark.parametrize('operation,flags', (
    ('delete p', (False, False)),
    ('delete [] p', (False, True)),
    ('::delete p', (True, False)),
    ('::delete [] p', (True, True)),
))
def test_dependent_delete_keeps_operation_and_operand_identity(operation, flags):
    parser = CppParser()
    parser.parse('template<class T>struct Box{T*p;~Box(){' + operation + ';}};')
    owner = parser.scope.names['Box'].dtype
    record, = owner.methods
    deletion, = (node for node in patterns(record.body) if node.kind == 'delete')
    assert deletion.value == flags
    assert deletion.children[0].symbol is owner.member_scope.names['p']


INVALID = (
    ('wrong-name', 'template<class T>struct Box{~Wrong(){}};', 'cpp.invalid-destructor'),
    ('duplicate-body', 'template<class T>struct Box{~Box(){}~Box(){}};', 'cpp.redeclaration'),
    ('duplicate-declaration', 'template<class T>struct Box{~Box();~Box();};', 'cpp.redeclaration'),
    ('parameter', 'template<class T>struct Box{~Box(int){}};', 'cpp.destructor-parameters'),
    ('cv', 'template<class T>struct Box{~Box()const{}};', 'cpp.destructor-cv'),
    ('value-return', 'template<class T>struct Box{~Box(){return 1;}};', 'cpp.destructor-return-value'),
    ('void-return-expression', 'void f();template<class T>struct Box{~Box(){return f();}};',
     'cpp.destructor-return-value'),
    ('definition-point-name', 'template<class T>struct Box{~Box(){missing=1;}};', 'cpp.undeclared'),
    ('definition-point-delete-operand', 'template<class T>struct Box{~Box(){delete[]1;}};',
     'cpp.invalid-delete-operand'),
    ('private-d1', 'template<class T>class Box{~Box(){}};int f(){Box<int>b;return 0;}',
     'cpp.member-access'),
)

BOUNDARIES = (
    ('missing-used-definition', 'template<class T>struct Box{~Box();};int f(){Box<int>b;return 0;}',
     'cpp.template-definition'),
    ('used-dependent-invalid', 'template<class T>struct Box{T n;~Box(){n=n%2;}};'
     'int f(){Box<double>b{0.0};return 0;}', 'cpp.invalid-operands'),
    ('used-dependent-delete', DYNARRAY_SCAFFOLD +
     'int f(){DynArray<int>b{nullptr,nullptr};return 0;}', 'cpp.unsupported.allocation'),
    ('explicit-d1-without-proof', 'template<class T>struct Box{~Box(){}};'
     'void f(Box<int>*p){p->~Box();}', 'cpp.unsupported.dependent-destruction'),
    ('nested-template-d1-without-proof', 'template<class T>struct Box{~Box(){}};'
     'struct Outer{Box<int>member;~Outer(){}};', 'cpp.unsupported.destructor-subobjects'),
    ('class-local', 'struct L{int n;};template<class T>struct Box{~Box(){L local;}};',
     'cpp.unsupported.class-templates'),
    ('dependent-class-local', 'struct L{int n;};template<class T>struct Box{~Box(){T local;}};'
     'int f(){Box<L>b;return 0;}', 'cpp.unsupported.member-templates'),
    ('dependent-array-local', 'typedef int A[2];template<class T>struct Box{~Box(){T local;}};'
     'int f(){Box<A>b;return 0;}', 'cpp.unsupported.member-templates'),
    ('dependent-reference-local', 'using R=int&;template<class T>struct Box{~Box(){T local;}};'
     'int f(){Box<R>b;return 0;}', 'cpp.unsupported.member-templates'),
    ('defaulted-d1', 'template<class T>struct Box{~Box()=default;};', 'cpp.unsupported.member-templates'),
    ('exception-contract', 'template<class T>struct Box{~Box()noexcept{}};', 'cpp.unsupported.exceptions'),
)


@pytest.mark.parametrize('name,source,code', INVALID + BOUNDARIES,
                         ids=[row[0] for row in INVALID + BOUNDARIES])
def test_template_d1_invalid_or_unowned_stops_before_ir(name, source, code, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unowned D1 reached IR generation')

    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name+'.cpp', source),))
    assert error.value.code == code


def test_private_d1_access_fails_before_requesting_its_definition():
    parser = CppParser()
    with pytest.raises(CppDiagnostic) as error:
        parser.parse('template<class T>class Box{T*p;~Box(){delete[]p;}};'
                     'int f(){Box<int>b;return 0;}')
    assert error.value.code == 'cpp.member-access'
    assert not any(isinstance(entry.context, MethodInstance)
                   for entry in parser.instance_registry.entries.values())


def test_used_dependent_delete_fails_at_the_preserved_expression_not_as_empty_d1():
    parser = CppParser()
    with pytest.raises(CppDiagnostic) as error:
        parser.parse('template<class T>struct Box{T*p;~Box(){\n'
                     'delete [] p;\n}};int f(){Box<int>b{nullptr};return 0;}', 'delete.cpp')
    assert error.value.code == 'cpp.unsupported.allocation'
    assert error.value.coord.file == 'delete.cpp' and error.value.coord.line == 2
    entry, = (entry for entry in parser.instance_registry.entries.values()
              if isinstance(entry.context, MethodInstance))
    assert not entry.value.defined
    assert not getattr(entry.value, 'closed_destruction_body', False)


def test_dependent_delete_substitutes_operand_constraints_before_unowned_allocation():
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse('template<class T>struct Box{T p;~Box(){delete p;}};'
                          'int f(){Box<int>b{0};return 0;}')
    assert error.value.code == 'cpp.invalid-delete-operand'
