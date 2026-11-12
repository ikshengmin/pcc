"""C++17 cv void* recovery: N4659 [expr.static.cast]/13, [expr.const]/2.

https://timsong-cpp.github.io/cppwp/n4659/expr.static.cast
https://timsong-cpp.github.io/cppwp/n4659/expr.const
https://timsong-cpp.github.io/cppwp/n4659/basic.compound

Original source obligations; all locally UNRUN. Recover only correctly typed,
aligned live objects in execution cases. These are not integer-pointer casts,
base adjustment, implicit void* recovery, volatile access, or lifetime creation.
Existing composite-placement original sources and their oracles stay unchanged.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.parser import Expression
from pcc.frontends.cpp.static_casts import convert_void_pointer
from pcc.frontends.cpp.types import ArrayType, ConstType, FunctionType, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


CASES = (
    ('scalar-roundtrip-write', 'int cpp_test_entry(){int n=1;void*storage=&n;'
     'int*p=static_cast<int*>(storage);if(p!=&n)return 1;*p=42;return n;}'),
    ('class-roundtrip', 'struct O{int n;};int cpp_test_entry(){O object{42};void*storage=&object;'
     'O*p=static_cast<O*>(storage);if(p!=&object)return 1;return p->n;}'),
    ('pointer-interconvertible-first-member', 'struct O{int n;};int cpp_test_entry(){O object{42};'
     'void*storage=&object.n;O*p=static_cast<O*>(storage);if(p!=&object)return 1;return p->n;}'),
    ('add-pointee-const', 'int choose(int*p){return 1;}int choose(const int*p){return *p;}'
     'int cpp_test_entry(){int n=42;void*storage=&n;return choose(static_cast<const int*>(storage));}'),
    ('retain-pointee-const', 'int cpp_test_entry(){const int n=42;const void*storage=&n;'
     'const int*p=static_cast<const int*>(storage);if(p!=&n)return 1;return *p;}'),
    ('top-level-pointer-const-is-not-pointee-const', 'int cpp_test_entry(){int n=42;void*const storage=&n;'
     'int*p=static_cast<int*>(storage);return *p;}'),
    ('typed-null-void-pointer', 'int cpp_test_entry(){void*storage=nullptr;'
     'int*p=static_cast<int*>(storage);if(p==nullptr)return 42;return 1;}'),
    ('typed-null-const-void-pointer', 'int cpp_test_entry(){const void*storage=nullptr;'
     'const int*p=static_cast<const int*>(storage);if(p==nullptr)return 42;return 1;}'),
    ('incomplete-object-pointee', 'struct O;O*recover(void*p){return static_cast<O*>(p);}'
     'int cpp_test_entry(){if(recover(nullptr)==nullptr)return 42;return 1;}'),
    ('qualified-class-alias', 'namespace N{struct O{int n;};using Alias=O;}'
     'int cpp_test_entry(){N::O object{42};void*storage=&object;return static_cast<N::Alias*>(storage)->n;}'),
    ('nominal-enum-object', 'enum class E{Answer=42};int cpp_test_entry(){E n=E::Answer;void*storage=&n;'
     'E*p=static_cast<E*>(storage);return static_cast<int>(*p);}'),
    ('pointer-object-pointee', 'int cpp_test_entry(){int n=42;int*value=&n;void*storage=&value;'
     'int**p=static_cast<int**>(storage);if(p!=&value)return 1;return **p;}'),
    ('const-pointer-object-pointee', 'int cpp_test_entry(){int n=42;int*const value=&n;const void*storage=&value;'
     'int*const*p=static_cast<int*const*>(storage);if(p!=&value)return 1;return **p;}'),
    ('const-terminal-is-distinct-from-const-pointer-object',
     'int cpp_test_entry(){const int n=42;const int*value=&n;void*storage=&value;'
     'const int**p=static_cast<const int**>(storage);return **p;}'),
    ('spelled-array-pointer-target', 'int cpp_test_entry(){int values[2]={17,25};void*storage=&values;'
     'int(*p)[2]=static_cast<int(*)[2]>(storage);if(p!=&values)return 1;return (*p)[0]+(*p)[1];}'),
    ('const-array-object-target', 'int cpp_test_entry(){const int values[2]={17,25};const void*storage=&values;'
     'const int(*p)[2]=static_cast<const int(*)[2]>(storage);return (*p)[0]+(*p)[1];}'),
    ('aliased-array-pointer-target', 'using Row=int[2];int cpp_test_entry(){Row values={17,25};void*storage=&values;'
     'Row*p=static_cast<Row*>(storage);return (*p)[0]+(*p)[1];}'),
    ('discarded-array-glvalue-does-not-fetch-pointer-sized-data',
     'int cpp_test_entry(){int a[1]={42};void*p=&a;*static_cast<int(*)[1]>(p);return a[0];}'),
    ('direct-array-cast-keeps-non-pointer-sized-row-stride',
     'int cpp_test_entry(){int rows[2][3]={{1,2,3},{17,20,5}};void*storage=&rows[0];'
     'return static_cast<int(*)[3]>(storage)[1][0]+static_cast<int(*)[3]>(storage)[1][1]+'
     'static_cast<int(*)[3]>(storage)[1][2];}'),
    ('array-cast-operand-evaluated-once-even-when-dereference-is-discarded',
     'int calls=0;void*choose(void*p){calls+=1;return p;}'
     'int cpp_test_entry(){int a[1]={42};*static_cast<int(*)[1]>(choose(&a));'
     'if(calls!=1)return 1;return a[0];}'),
    ('nested-pointer-containing-array-target',
     'int cpp_test_entry(){int row[2]={17,25};int(*view)[2]=&row;void*storage=&view;'
     'int(**p)[2]=static_cast<int(**)[2]>(storage);return (**p)[0]+(**p)[1];}'),
    ('operand-evaluated-once', 'int calls=0;void*choose(void*p){calls+=1;return p;}'
     'int cpp_test_entry(){int n=42;int*p=static_cast<int*>(choose(&n));if(calls!=1)return 1;return *p;}'),
    ('unevaluated-operand-is-not-run', 'int calls=0;void*choose(void*p){calls+=1;return p;}'
     'int cpp_test_entry(){int n=42;if(sizeof(static_cast<int*>(choose(&n)))!=sizeof(int*))return 1;'
     'if(calls!=0)return 2;return 42;}'),
    ('default-argument-keeps-bound-cast', 'int n=42;void*storage=&n;'
     'int read(int*p=static_cast<int*>(storage)){return *p;}int cpp_test_entry(){return read();}'),
    ('extended-float-pointer-needs-no-value-lowering',
     'long double*recover(void*p){return static_cast<long double*>(p);}'
     'int cpp_test_entry(){if(recover(nullptr)==nullptr)return 42;return 1;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    ('cross-tu-incomplete-class-pointer-recovery', (
        ('provider.cpp', 'struct O;O*recover(void*p){return static_cast<O*>(p);}'),
        ('entry.cpp', 'struct O{int n;};O*recover(void*);int cpp_test_entry(){O o{42};return recover(&o)->n;}'),
    )),
)

INVALID = (
    ('remove-pointee-const', 'int*f(const void*p){return static_cast<int*>(p);}', 'cpp.invalid-conversion'),
    ('nested-const-does-not-qualify-pointer-object',
     'const int**f(const void*p){return static_cast<const int**>(p);}', 'cpp.invalid-conversion'),
    ('remove-array-object-const',
     'void f(const void*p){static_cast<int(*)[2]>(p);}', 'cpp.invalid-conversion'),
    ('implicit-recovery-is-still-invalid', 'int*f(void*p){return p;}', 'cpp.invalid-conversion'),
    ('cast-result-is-not-an-lvalue',
     'void f(void*p,int*q){static_cast<int*>(p)=q;}', 'cpp.not-lvalue'),
    ('private-target-alias', 'class Owner{using Hidden=int;};'
     'int*f(void*p){return static_cast<Owner::Hidden*>(p);}', 'cpp.member-access'),
    ('unrelated-object-pointers', 'int*f(double*p){return static_cast<int*>(p);}', 'cpp.unsupported.static-cast'),
    ('arithmetic-to-pointer', 'int*f(){return static_cast<int*>(42);}', 'cpp.unsupported.static-cast'),
    ('pointer-to-arithmetic', 'int f(void*p){return static_cast<int>(p);}', 'cpp.unsupported.static-cast'),
    ('void-to-function-pointer', 'using F=int();void f(void*p){static_cast<F*>(p);}', 'cpp.unsupported.function-pointers'),
)

DEFERRED = (
    ('namespace-recovery-needs-dynamic-initialization',
     'int n;void*p=&n;int*q=static_cast<int*>(p);', 'cpp.unsupported.static-initialization-analysis'),
    ('c-style-pointer-recovery', 'int*f(void*p){return (int*)p;}', 'cpp.unsupported.c-style-cast'),
    ('identity-pointer-static-cast', 'int*f(int*p){return static_cast<int*>(p);}', 'cpp.unsupported.static-cast'),
    ('object-pointer-to-void-static-cast', 'void*f(int*p){return static_cast<void*>(p);}', 'cpp.unsupported.static-cast'),
    ('nullptr-static-cast', 'int*f(){return static_cast<int*>(nullptr);}', 'cpp.unsupported.static-cast'),
    ('zero-null-pointer-conversion', 'int*f(){return static_cast<int*>(0);}', 'cpp.unsupported.static-cast'),
    ('reference-static-cast', 'void f(int&n){static_cast<int&>(n);}', 'cpp.unsupported.static-cast'),
    ('volatile-object-pointer', 'void f(void*p){static_cast<volatile int*>(p);}', 'cpp.unsupported.volatile'),
    ('volatile-void-pointer', 'void f(volatile void*p){static_cast<volatile int*>(p);}', 'cpp.unsupported.volatile'),
    ('dependent-pointer-target', 'template<class T>void f(void*p){static_cast<T*>(p);}', 'cpp.unsupported.pointer-templates'),
    # Bases parse now; class-pointer static_cast keeps its own boundary.
    ('base-upcast', 'struct B{};struct D:B{};B*f(D*p){return static_cast<B*>(p);}', 'cpp.unsupported.static-cast'),
    ('base-downcast', 'struct B{};struct D:B{};D*f(B*p){return static_cast<D*>(p);}', 'cpp.unsupported.static-cast'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_static_void_pointer_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED,
                         ids=[row[0] for row in INVALID + DEFERRED])
def test_static_void_pointer_diagnostics_precede_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unsupported static_cast reached emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name + '.cpp', source),))
    assert caught.value.code == code


def test_pointer_recovery_preserves_one_operand_and_prvalue_type_without_address_proof():
    parser = CppParser()
    parser.parse('')
    coord = Coord('cast.cpp', 1, 1)
    operand = Expression(c_ast.FuncCall(c_ast.ID('choose', coord), None, coord), PointerType('void'))
    target = ConstType(PointerType(ConstType('int')))
    result = convert_void_pointer(parser, target, operand, coord)
    assert result.dtype == PointerType(ConstType('int')) and not result.lvalue
    assert isinstance(result.node, c_ast.Cast) and result.node.expr is operand.node
    assert result.static_address is None and not result.null_pointer
    assert result.constant_fact.status == 'NOT_CONSTANT'


def test_complete_cast_type_id_retains_array_shape_and_prvalue_pointer_const_rules():
    parser = CppParser()
    parser.parse('void*p=nullptr;using Row=decltype(static_cast<const int(*)[2]>(p));'
                 'using Plain=decltype(static_cast<int*const>(p));')
    assert parser.scope.names['Row'].dtype == PointerType(ArrayType(ConstType('int'), 2))
    assert parser.scope.names['Plain'].dtype == PointerType('int')


def test_function_pointer_shape_is_not_admitted_through_semantic_helper():
    parser = CppParser()
    parser.parse('')
    coord = Coord('cast.cpp', 1, 1)
    source = Expression(c_ast.ID('storage', coord), PointerType('void'))
    with pytest.raises(CppDiagnostic) as caught:
        convert_void_pointer(parser, PointerType(FunctionType('int', ())), source, coord)
    assert caught.value.code == 'cpp.unsupported.function-pointers'


def test_array_containing_target_restores_shape_before_any_consumer_once():
    parser = CppParser()
    parser.parse('')
    coord = Coord('array-cast.cpp', 1, 1)
    operand = Expression(c_ast.FuncCall(c_ast.ID('choose', coord), None, coord), PointerType('void'))
    target = PointerType(PointerType(ArrayType(ConstType('int'), 3)))
    result = convert_void_pointer(parser, target, operand, coord)
    assert result.dtype == target and not result.lvalue
    assert isinstance(result.node, c_ast.StmtExpr)
    temporary, value = result.node.stmt.block_items
    assert isinstance(temporary, c_ast.Decl) and isinstance(temporary.init, c_ast.Cast)
    assert temporary.init.expr is operand.node
    assert isinstance(temporary.type, c_ast.PtrDecl)
    assert isinstance(temporary.type.type, c_ast.PtrDecl)
    assert isinstance(temporary.type.type.type, c_ast.ArrayDecl)
    assert temporary.type.type.type.dim.value == '3'
    assert isinstance(value, c_ast.ID) and value.name == temporary.name
    assert result.static_address is None and not result.null_pointer
    assert result.constant_fact.status == 'NOT_CONSTANT'


def test_discarded_array_cast_lowers_to_array_address_not_a_pointee_load(owned_only, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, ir

    name = 'discarded-array-glvalue-does-not-fetch-pointer-sized-data'
    source = dict(CASES)[name]
    units = ((name + '.cpp', source),)
    # Warm the real per-class dispatch cache before installing the observer.
    # Patching codegen_UnaryOp alone cannot replace its already cached callable.
    unobserved = compile_host_cpp(units)
    original = CCodeGenerator.codegen
    observed = []

    def observe_array_dereference(generator, node):
        result = original(generator, node)
        if (isinstance(node, c_ast.UnaryOp) and node.op == '*'
                and isinstance(node.expr, c_ast.StmtExpr)):
            items = node.expr.stmt.block_items
            if (items and isinstance(items[0], c_ast.Decl)
                    and items[0].name.startswith('__pcc_cpp_static_cast_')):
                value, address = result
                observed.append((value, address))
        return result

    # Observe the actual dispatch entrypoint without changing the cache or the
    # production handler; the complete emitted artifact must remain identical.
    monkeypatch.setattr(CCodeGenerator, 'codegen', observe_array_dereference)
    assert compile_host_cpp(units) == unobserved
    assert len(observed) == 1
    value, address = observed[0]
    # C's real array-dereference no-load branch returns the address twice.
    # Erased void** instead produces a loaded i8* and a distinct address.
    assert value is address
    assert isinstance(value.type, ir.PointerType)
    assert isinstance(value.type.pointee, ir.ArrayType)
    assert value.type.pointee.count == 1
    assert value.type.pointee.element == ir.IntType(32)
