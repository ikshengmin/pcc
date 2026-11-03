"""First-declaration defaulted default C1 obligations; all locally UNRUN.

N4659 [dcl.fct.def.default]/1,5, [class.ctor]/5-7, [dcl.init]/7-8,
[dcl.init.aggr]/1 and [dcl.init.list]:
https://timsong-cpp.github.io/cppwp/n4659/dcl.fct.def.default
https://timsong-cpp.github.io/cppwp/n4659/class.ctor
https://timsong-cpp.github.io/cppwp/n4659/dcl.init
https://timsong-cpp.github.io/cppwp/n4659/dcl.init.aggr

No indeterminate scalar value is read to observe default initialization.
No-zero obligations inspect the generated initialization plan; value-init
execution also observes interior/tail padding after genuine zero initialization.
Placement reference cases require real external <new>, not an owned header
substitute. Their original TU bytes match; effective include closures differ.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constructors import constructor_call
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_placement_new import DECLARATION


ORIGINAL_NOTHROW_DECLARATION = 'struct nothrow_t { explicit nothrow_t() = default; };'
UNUSED_EXTERN_DMI = ('extern int external_value; '
                     'struct S { S()=default; int n=external_value; }; '
                     'int cpp_test_entry(){return 42;}')
# This separate AST control avoids the unimplemented conditional-expression
# owner. It does not replace the original ternary program or its execution
# obligation below, and it still requires selection without defining the C1.
UNEVALUATED_PLACEMENT_C1_CONTROL = (
    'extern int external_value;struct S{S()=default;int n=external_value;};'
    'int cpp_test_entry(){long raw[4];'
    'if(sizeof(new(raw)S)!=sizeof(S*))return 1;return 42;}'
)

CASES = (
    ('unused-defaulted-c1-does-not-reference-undefined-extern', UNUSED_EXTERN_DMI),
    ('used-defaulted-c1-retains-bound-extern-in-same-tu',
     'extern int external_value;struct S{S()=default;int n=external_value;};'
     'int external_value=21;int cpp_test_entry(){S first;S second;return first.n+second.n;}'),
    ('unused-defaulted-c1-with-call-dmi-does-not-invent-a-call',
     'int unknown();struct S{S()=default;int n=unknown();};int cpp_test_entry(){return 42;}'),
    ('empty-explicit-defaulted-tag', 'struct Tag{explicit Tag()=default;};'
     'int cpp_test_entry(){Tag tag;Tag other{};return 42;}'),
    ('nonexplicit-defaulted-remains-cpp17-aggregate', 'struct S{S()=default;int n;};'
     'int cpp_test_entry(){S s{42};return s.n;}'),
    ('aggregate-copy-list-bypasses-constructor', 'struct S{S()=default;int n;};'
     'int cpp_test_entry(){S s={42};return s.n;}'),
    ('aggregate-braces-bypass-private-defaulted-constructor', 'class S{S()=default;public:int n;};'
     'int cpp_test_entry(){S s{42};return s.n;}'),
    ('aggregate-braces-bypass-deleted-defaulted-constructor', 'struct S{S()=default;const int n;};'
     'int cpp_test_entry(){S s{42};return s.n;}'),
    ('empty-aggregate-braces-bypass-deleted-defaulted-constructor', 'struct S{S()=default;const int n;};'
     'int cpp_test_entry(){S s{};return s.n+42;}'),
    ('nonaggregate-value-init-zeroes-scalar-pointer-and-enum', 'enum class E{Zero=0};'
     'struct S{explicit S()=default;int n;int*p;E e;};'
     'int cpp_test_entry(){S s{};if(s.n!=0||s.p!=nullptr||s.e!=E::Zero)return 1;return 42;}'),
    ('private-data-value-initialization', 'class S{int n;public:S()=default;int read(){return n;}};'
     'int cpp_test_entry(){S s{};return s.read()+42;}'),
    ('default-init-does-not-invent-aggregate-copy', 'struct S{S()=default;int n;};'
     'int cpp_test_entry(){S s;s.n=42;return s.n;}'),
    ('scalar-dmi-default-init', 'struct S{S()=default;int n=42;};'
     'int cpp_test_entry(){S s;return s.n;}'),
    ('value-init-before-declaration-order-dmis',
     'struct S{explicit S()=default;int first=17;int second=first+25;};'
     'int cpp_test_entry(){S s{};return s.second;}'),
    ('bound-dmi-is-not-rebound-at-call', 'int seed=17;'
     'struct S{explicit S()=default;int n=seed+25;};'
     'int cpp_test_entry(){int seed=1;S s;return s.n;}'),
    ('const-default-init-with-dmi', 'struct S{S()=default;const int n=42;};'
     'int cpp_test_entry(){const S s;return s.n;}'),
    ('user-provided-default-constructor-stays-distinct', 'struct S{int n;S(){n=42;}};'
     'int cpp_test_entry(){S s{};return s.n;}'),
    ('member-empty-parens-value-initialize-defaulted-leaf',
     'struct Leaf{explicit Leaf()=default;int n;};struct Owner{Leaf leaf;Owner():leaf(){}};'
     'int cpp_test_entry(){Owner o;return o.leaf.n+42;}'),
    ('member-omission-default-initializes-defaulted-leaf',
     'struct Leaf{explicit Leaf()=default;int n;};struct Owner{Leaf leaf;Owner(){}};'
     'int cpp_test_entry(){Owner o;o.leaf.n=42;return o.leaf.n;}'),
    ('defaulted-leaf-retains-explicit-d1-chain', 'int destroyed=0;'
     'struct Leaf{explicit Leaf()=default;int n;~Leaf(){destroyed+=1;}};'
     'struct Owner{Leaf leaf;Owner():leaf(){}~Owner(){}};'
     'int cpp_test_entry(){{Owner o;if(o.leaf.n!=0)return 1;}return destroyed+41;}'),
)

PLACEMENT_CASES = (
    # This unchanged positive obligation still needs conditional expressions;
    # the separate AST control above must not turn its rejection into a pass.
    ('unevaluated-placement-does-not-define-defaulted-c1',
     'extern int external_value;struct S{S()=default;int n=external_value;};'
     'int cpp_test_entry(){long raw[4];return sizeof(new(raw)S)==sizeof(S*)?42:1;}'),
    ('placement-default-init-then-explicit-write', 'struct S{explicit S()=default;int n;};'
     'int cpp_test_entry(){long raw[4];S*p=new(raw)S;p->n=42;return p->n;}'),
    # This unchanged positive obligation still needs class sizeof. A passing
    # whole-aggregate-zero-store check does not prove its padding execution.
    ('placement-value-init-zeroes-interior-and-tail-padding',
     'struct S{explicit S()=default;char first;int middle;char last;};'
     'int cpp_test_entry(){long raw[8];void*storage=raw;unsigned char*bytes=static_cast<unsigned char*>(storage);'
     'for(unsigned long i=0;i<sizeof(S);i++)bytes[i]=127;S*p=new(storage)S();'
     'if(p->first!=0||p->middle!=0||p->last!=0)return 1;'
     'for(unsigned long i=0;i<sizeof(S);i++)if(bytes[i]!=0)return 2;return 42;}'),
    ('placement-value-init-evaluates-storage-once', 'int calls=0;void*where(void*p){calls+=1;return p;}'
     'struct S{explicit S()=default;int n;};int cpp_test_entry(){long raw[4];'
     'S*p=new(where(raw))S();if(calls!=1||p->n!=0)return 1;p->n=42;return p->n;}'),
    ('placement-value-init-runs-dmi-after-zero-phase',
     'struct S{explicit S()=default;int n=42;int*next;};'
     'int cpp_test_entry(){long raw[4];S*p=new(raw)S();if(p->next!=nullptr)return 1;return p->n;}'),
)

PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    ('used-defaulted-c1-retains-bound-extern-across-tus', (
        ('provider.cpp', 'extern int external_value;struct S{S()=default;int n=external_value;};'
         'int external_value=42;int read(){S s;return s.n;}'),
        ('entry.cpp', 'extern int external_value;struct S{S()=default;int n=external_value;};'
         'int read();int cpp_test_entry(){S s;return read()+s.n-42;}'),
    )),
    ('cross-tu-defaulted-c1-external-odr', (
        ('provider.cpp', 'struct S{explicit S()=default;int n=42;};int read(){S s;return s.n;}'),
        ('entry.cpp', 'struct S{explicit S()=default;int n=42;};int read();'
         'int cpp_test_entry(){S s{};return read()+s.n-42;}'),
    )),
)
PLACEMENT_PROGRAMS = tuple((name, ((name + '.cpp', DECLARATION + source),))
                           for name, source in PLACEMENT_CASES)

INVALID = (
    ('unused-defaulted-c1-still-binds-dmi-names',
     'struct S{S()=default;int n=missing;};', 'cpp.undeclared'),
    ('unevaluated-placement-still-checks-constructor-access',
     DECLARATION + 'class S{S()=default;public:int n;};void f(void*p){sizeof(new(p)S);}',
     'cpp.member-access'),
    ('unevaluated-placement-still-checks-constructor-deletion',
     DECLARATION + 'struct S{S()=default;const int n;};void f(void*p){sizeof(new(p)S);}',
     'cpp.deleted-default-constructor'),
    ('defaulted-nonspecial-constructor', 'struct S{S(int)=default;};', 'cpp.invalid-defaulted-constructor'),
    ('default-arguments-are-not-defaultable', 'struct S{S(int n=0)=default;};', 'cpp.invalid-defaulted-constructor'),
    ('private-default-init', 'class S{S()=default;public:int n;};void f(){S s;}', 'cpp.member-access'),
    ('deleted-default-init', 'struct S{S()=default;const int n;};void f(){S s;}', 'cpp.deleted-default-constructor'),
    ('deleted-nonaggregate-value-init', 'struct S{explicit S()=default;const int n;};void f(){S s{};}',
     'cpp.deleted-default-constructor'),
    ('explicit-copy-list-is-rejected', 'struct S{explicit S()=default;};void f(){S s={};}', 'cpp.explicit-copy-list'),
    ('const-default-init-needs-initialized-fields', 'struct S{S()=default;int n;};void f(){const S s;}',
     'cpp.const-initializer'),
    ('duplicate-defaulted-declaration', 'struct S{S()=default;S()=default;};', 'cpp.redeclaration'),
)

DEFERRED = (
    ('defaulting-after-first-declaration-is-user-provided', 'struct S{S();int n;};S::S()=default;',
     'cpp.unsupported.inline-member-functions'),
    ('defaulted-copy-constructor', 'struct S{S(const S&)=default;int n;};', 'cpp.unsupported.class-initialization'),
    ('defaulted-owner-class-subobject', 'struct M{M(){}};struct S{S()=default;M m;};',
     'cpp.unsupported.constructor-subobjects'),
    ('defaulted-owner-reference-subobject', 'struct S{S()=default;int&n;};', 'cpp.unsupported.references'),
    ('defaulted-base-construction', 'struct B{};struct S:B{S()=default;};', 'cpp.unsupported.classes'),
    ('explicit-exception-specification', 'struct S{S()noexcept=default;};', 'cpp.unsupported.exceptions'),
    ('aggregate-list-with-dmi-retains-existing-owner-gap', 'struct S{S()=default;int n=42;};void f(){S s{};}',
     'cpp.unsupported.default-member-initializer-path'),
    ('unknown-dmi-call-needs-implicit-exception-owner', 'int unknown();struct S{S()=default;int n=unknown();};'
     'void f(){S s;}', 'cpp.unsupported.default-member-initializer-path'),
    ('const-nonaggregate-value-init-stays-explicit', 'struct S{explicit S()=default;int n;};void f(){const S s{};}',
     'cpp.unsupported.const-class-initialization'),
    ('namespace-class-declaration-is-next-new-header-frontier',
     'struct Tag{explicit Tag()=default;};extern const Tag tag;', 'cpp.unsupported.static-class-lifetime'),
)


@pytest.mark.parametrize('name,units', PROGRAMS + PLACEMENT_PROGRAMS,
                         ids=[row[0] for row in PROGRAMS + PLACEMENT_PROGRAMS])
def test_defaulted_c1_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED,
                         ids=[row[0] for row in INVALID + DEFERRED])
def test_defaulted_c1_invalid_and_unowned_forms_fail_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unsupported defaulted C1 reached emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name + '.cpp', source),))
    assert caught.value.code == code


def test_exact_nothrow_declaration_preserves_defaulted_identity():
    parser = CppParser()
    parser.parse('namespace std{' + ORIGINAL_NOTHROW_DECLARATION + '}')
    owner = parser.scope.names['std'].namespace_scope.names['nothrow_t'].dtype
    selected = owner.definition.constructors.overloads[0]
    assert selected.constructor_defaulted and not selected.constructor_user_provided
    assert selected.constructor_defaulted_on_first_declaration
    assert selected.constructor_trivial and not selected.constructor_deleted
    assert selected.constructor_explicit and not owner.definition.aggregate


def test_unused_defaulted_c1_checks_body_but_emits_no_definition_or_instance():
    parser = CppParser()
    ast = parser.parse(UNUSED_EXTERN_DMI)
    pending = parser.scope.names['S'].dtype.definition.defaulted_constructors[0]
    assert pending.function is not None
    assert pending.symbol.closed_construction_body
    assert pending.function.body.block_items[0].rvalue.name == parser.scope.names['external_value'].c_name
    assert not any(isinstance(item, c_ast.FuncDef) and item.decl.name == pending.symbol.c_name
                   for item in ast.ext)
    assert not parser.instance_registry.entries


def test_evaluated_calls_share_one_external_odr_definition_and_original_prototype():
    from pcc.support.instantiation import InstanceState

    parser = CppParser()
    ast = parser.parse('int external_value=21;struct S{S()=default;int n=external_value;};'
                       'int cpp_test_entry(){S first;S second;return first.n+second.n;}')
    pending = parser.scope.names['S'].dtype.definition.defaulted_constructors[0]
    definitions = [item for item in ast.ext
                   if isinstance(item, c_ast.FuncDef) and item.decl.name == pending.symbol.c_name]
    assert definitions == [pending.function]
    assert definitions[0].decl is not pending.declaration
    assert definitions[0].decl.definition_linkage == 'external_odr'
    assert pending.declaration.definition_linkage == 'external'
    entries = list(parser.instance_registry.entries.values())
    assert len(entries) == 1
    assert entries[0].context is pending and entries[0].state is InstanceState.COMPLETE


def test_unevaluated_placement_checks_selected_c1_without_emitting_body():
    parser = CppParser()
    ast = parser.parse(DECLARATION + UNEVALUATED_PLACEMENT_C1_CONTROL)
    selected = parser.scope.names['S'].dtype.definition.constructors.overloads[0]
    assert not any(isinstance(item, c_ast.FuncDef) and item.decl.name == selected.c_name
                   for item in ast.ext)
    assert not parser.instance_registry.entries


def test_defaulted_request_can_join_an_active_shared_worklist_once():
    from pcc.frontends.cpp.default_constructors import emit_definition, request_definition

    parser = CppParser()
    parser.parse('struct S{S()=default;int n=42;};')
    selected = parser.scope.names['S'].dtype.definition.constructors.overloads[0]
    driver = object()
    parser.instance_registry.request(driver, driver)

    def emit(entry):
        if entry.key is driver:
            request_definition(parser, selected)
            request_definition(parser, selected)
            return None
        return emit_definition(parser, entry.context)

    emitted = parser.instance_registry.drain(emit)
    assert emitted == [None, selected.defaulted_definition.function]
    assert len(parser.instance_registry.entries) == 2


def test_lazy_definition_still_requires_shared_external_odr_support(monkeypatch):
    from pcc.backend import definition_linkage

    monkeypatch.setattr(definition_linkage, 'supports_external_odr_function_definitions', lambda target: False)
    source = 'struct S{S()=default;int n=42;};'
    CppParser().parse(source)  # No definition is emitted merely for the class.
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source + 'void f(){S s;}')
    assert caught.value.code == 'cpp.unsupported.inline-member-functions'


def test_defaulted_triviality_deletion_and_aggregate_metadata_are_independent():
    parser = CppParser()
    parser.parse('struct Plain{Plain()=default;int n;};'
                 'struct Dmi{Dmi()=default;int n=42;};'
                 'struct Deleted{Deleted()=default;const int n;};'
                 'struct Provided{Provided(){}};')
    plain, dmi, deleted, provided = [parser.scope.names[name].dtype.definition
                                    for name in ('Plain', 'Dmi', 'Deleted', 'Provided')]
    assert plain.aggregate and plain.constructors.overloads[0].constructor_trivial
    assert dmi.aggregate and not dmi.constructors.overloads[0].constructor_trivial
    assert deleted.aggregate and deleted.constructors.overloads[0].constructor_deleted
    assert deleted.defaulted_constructors[0].function is None
    assert provided.constructors.overloads[0].constructor_user_provided and not provided.aggregate


def test_default_and_value_initialization_share_c1_without_sharing_zero_phase():
    parser = CppParser()
    parser.parse('struct S{explicit S()=default;char first;int middle;char last;};')
    owner = parser.scope.names['S'].dtype
    coord = Coord('initialization.cpp', 1, 1)
    address = c_ast.FuncCall(c_ast.ID('address_once', coord), None, coord)
    default = constructor_call(parser, owner, [], address, coord, initialization_kind='default')
    value = constructor_call(parser, owner, [], address, coord, initialization_kind='value')
    assert isinstance(default, c_ast.FuncCall) and default.args.exprs == [address]
    assert isinstance(value, c_ast.StmtExpr)
    temporary, zero, call = value.stmt.block_items
    assert isinstance(temporary, c_ast.Decl) and temporary.init is address
    assert isinstance(zero, c_ast.Assignment) and zero.op == '='
    assert isinstance(zero.rvalue, c_ast.CompoundLiteral)
    assert isinstance(zero.rvalue.init, c_ast.InitList) and zero.rvalue.init.exprs == []
    assert isinstance(call, c_ast.FuncCall) and call.name.name == default.name.name
    assert call.args.exprs[0].name == temporary.name
    assert owner.definition.defaulted_constructors[0].function.body.block_items == []


def test_user_provided_value_initialization_does_not_get_defaulted_zero_phase():
    parser = CppParser()
    parser.parse('struct S{int n;S(){n=42;}};')
    owner = parser.scope.names['S'].dtype
    coord = Coord('provided.cpp', 1, 1)
    address = c_ast.ID('storage', coord)
    call = constructor_call(parser, owner, [], address, coord, initialization_kind='value')
    assert isinstance(call, c_ast.FuncCall) and call.args.exprs == [address]


def test_value_init_uses_whole_aggregate_zero_store_before_constructor(owned_only, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, ir

    original = CCodeGenerator._safe_store
    aggregate_zero_stores = []

    def observe_store(generator, value, address):
        if (isinstance(value, ir.Constant) and value.value is None
                and hasattr(value.type, 'members')
                and value.type.members == ['first', 'middle', 'last']):
            aggregate_zero_stores.append((value, address))
        return original(generator, value, address)

    monkeypatch.setattr(CCodeGenerator, '_safe_store', observe_store)
    source = ('struct S{explicit S()=default;char first;int middle;char last;};'
              'int cpp_test_entry(){S s{};return s.middle+42;}')
    compile_host_cpp((('whole-zero.cpp', source),))
    assert len(aggregate_zero_stores) == 1
    value, address = aggregate_zero_stores[0]
    assert isinstance(address.type, ir.PointerType) and address.type.pointee == value.type
