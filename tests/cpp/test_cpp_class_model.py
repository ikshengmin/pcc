"""Original C++17 class Sema/projection rules; not a native pcc1 gate.

Primary rules: WG21 N4659 [class.mem], [class.access], [dcl.init],
[class.default.ctor], [class.copy.assign], and Itanium C++ ABI 2.2/2.4.
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
https://itanium-cxx-abi.github.io/cxx-abi/abi.html
CWG 2394 (accepted defect resolution, February 2019) is applied:
https://cplusplus.github.io/CWG/issues/2394.html
No GCC/Clang test source is copied or attributed as an upstream test here.
"""

from types import SimpleNamespace

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp.classes import (
    ClassDefinition,
    ClassType,
    c_class_type,
    default_initialize,
    is_class_type,
    member_access,
    require_complete,
    empty_list_initialize,
)
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.parser import CppToken, Expression
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.types import ConstType, PointerType, c_type_names, unqualified


COORD = Coord("class-model.cpp", 3, 7)


def define(name="Record", key="struct", fields=()):
    parent = Scope()
    dtype = ClassType(name, (name,), COORD)
    parent.declare(Symbol(name, "type", COORD, dtype=dtype))
    definition = ClassDefinition(dtype, parent, key)
    for field_name, field_type in fields:
        definition.add_member(Symbol(field_name, "member", COORD, dtype=field_type))
    definition.finish()
    return dtype


def project_type(name, dtype, coord):
    qualifiers = ["const"] if isinstance(dtype, ConstType) else []
    plain = unqualified(dtype)
    if isinstance(plain, PointerType):
        return c_ast.PtrDecl(qualifiers, project_type(name, plain.pointee, coord), coord)
    if isinstance(plain, ClassType):
        return c_class_type(name, dtype, coord)
    return c_ast.TypeDecl(name, qualifiers, c_ast.IdentifierType(c_type_names(plain), coord), coord)


def access(dtype, name="value", operator=".", scope=None, lvalue=True):
    parser = SimpleNamespace(scope=Scope() if scope is None else scope)
    expr = Expression(c_ast.ID("object", COORD), dtype, lvalue=lvalue)
    return member_access(parser, expr, operator, CppToken("ID", name, COORD))


def test_class_type_identity_is_nominal_and_const_does_not_complete_it():
    first = ClassType("Record", ("n", "Record"), COORD)
    second = ClassType("Record", ("n", "Record"), COORD)
    assert first != second
    assert len({first, second}) == 2
    assert is_class_type(ConstType(first))
    assert not is_class_type(PointerType(first))
    assert first.c_tag == "__pcc_cpp_class_1n6Record"
    assert not first.complete
    with pytest.raises(CppDiagnostic, match="cpp.incomplete-class"):
        require_complete(ConstType(first), COORD)
    require_complete(PointerType(first), COORD)


def test_class_internal_linkage_is_explicit_not_inferred_from_c_tag():
    scope = Scope()
    public = ClassType("Record", ("data", "Record"), COORD)
    internal = ClassType("Record", ("_GLOBAL__N_1", "Record"), COORD,
                         internal_linkage=True, declaration_scope=scope)
    assert not public.internal_linkage
    assert internal.internal_linkage
    assert internal.declaration_scope is scope
    assert public.declaration_scope is None
    assert not public.complete and not internal.complete


def test_class_completion_keeps_forward_identity_and_ordered_members():
    parent = Scope()
    dtype = ClassType("Node", ("Node",), COORD)
    parent.declare(Symbol("Node", "type", COORD, dtype=dtype))
    definition = ClassDefinition(dtype, parent)
    definition.add_member(Symbol("next", "member", COORD, dtype=PointerType(dtype)))
    definition.add_member(Symbol("value", "member", COORD, dtype="long"))
    assert definition.scope.lookup("Node").dtype is dtype
    assert not dtype.complete
    definition.finish()
    assert dtype.complete
    assert [member.name for member in definition.members] == ["next", "value"]
    assert definition.scope.lookup("value") is definition.members[1]
    with pytest.raises(ValueError, match="completed class"):
        definition.add_member(Symbol("late", "member", COORD))


@pytest.mark.parametrize("field_type", ["void", ConstType("void")])
def test_void_data_members_are_rejected(field_type):
    with pytest.raises(CppDiagnostic, match="cpp.invalid-object-type"):
        define(fields=(("value", field_type),))


def test_self_and_other_incomplete_value_members_are_rejected_but_pointers_are_not():
    dtype = ClassType("Node", ("Node",), COORD)
    definition = ClassDefinition(dtype, Scope())
    with pytest.raises(CppDiagnostic, match="cpp.incomplete-class"):
        definition.add_member(Symbol("self", "member", COORD, dtype=dtype))
    definition.add_member(Symbol("next", "member", COORD, dtype=PointerType(dtype)))
    other = ClassType("Other", ("Other",), COORD)
    with pytest.raises(CppDiagnostic, match="cpp.incomplete-class"):
        definition.add_member(Symbol("other", "member", COORD, dtype=other))


def test_duplicate_members_and_class_redefinitions_are_rejected():
    dtype = ClassType("Record", ("Record",), COORD)
    definition = ClassDefinition(dtype, Scope())
    definition.add_member(Symbol("value", "member", COORD))
    with pytest.raises(CppDiagnostic, match="cpp.redeclaration"):
        definition.add_member(Symbol("value", "member", COORD))
    with pytest.raises(CppDiagnostic, match="cpp.class-redefinition"):
        ClassDefinition(dtype, Scope())


def test_c_projection_uses_struct_references_and_preserves_field_types():
    inner = define("Inner", fields=(("value", "int"),))
    outer = define("Outer", fields=(("tag", "unsigned char"), ("inner", inner),
                                    ("next", PointerType(ConstType(inner)))))
    decl = outer.definition.c_declaration(project_type)
    assert decl.name is None
    assert isinstance(decl.type, c_ast.Struct)
    assert decl.type.name == outer.c_tag
    assert [member.name for member in decl.type.decls] == ["tag", "inner", "next"]
    assert decl.type.decls[1].type.type.name == inner.c_tag
    assert decl.type.decls[1].type.type.decls is None
    assert decl.type.decls[2].type.type.quals == ["const"]
    assert decl.type.decls[2].type.type.type.name == inner.c_tag


def test_empty_class_uses_one_storage_byte_not_a_semantic_data_member():
    dtype = define("Empty")
    decl = dtype.definition.c_declaration(project_type)
    assert len(decl.type.decls) == 1
    assert decl.type.decls[0].type.type.names == ["unsigned", "char"]
    assert dtype.definition.members == []
    with pytest.raises(CppDiagnostic, match="cpp.no-member"):
        access(dtype, "__pcc_cpp_empty_byte")


@pytest.mark.parametrize("key,expected", [("struct", "public"), ("class", "private")])
def test_class_key_controls_default_access_not_type_identity(key, expected):
    dtype = define(key=key, fields=(("value", "int"),))
    assert dtype.definition.members[0].access == expected
    assert dtype.definition.aggregate == (key == "struct")


def test_access_control_is_checked_after_member_lookup():
    dtype = define(key="class", fields=(("value", "int"),))
    with pytest.raises(CppDiagnostic, match="cpp.member-access"):
        access(dtype)
    expression = access(dtype, scope=Scope(dtype.definition.scope))
    assert expression.dtype == "int"
    assert expression.lvalue
    assert isinstance(expression.node, c_ast.StructRef)


def test_missing_member_does_not_fall_back_to_an_enclosing_name():
    dtype = define()
    dtype.definition.scope.parent.declare(Symbol("outside", "value", COORD))
    assert dtype.definition.scope.lookup("outside") is not None
    with pytest.raises(CppDiagnostic, match="cpp.no-member"):
        access(dtype, "outside")


def test_member_type_alias_is_not_an_object_expression():
    dtype = define()
    dtype.definition.scope.declare(Symbol("Alias", "type", COORD))
    with pytest.raises(CppDiagnostic, match="cpp.member-not-value"):
        access(dtype, "Alias")


def test_const_object_and_pointee_qualify_members_but_const_pointer_does_not():
    dtype = define(fields=(("value", "int"),))
    assert access(ConstType(dtype)).dtype == ConstType("int")
    assert access(PointerType(ConstType(dtype)), operator="->").dtype == ConstType("int")
    assert access(ConstType(PointerType(dtype)), operator="->").dtype == "int"
    assert access(PointerType(dtype), operator="->", lvalue=False).lvalue


def test_const_member_pointer_gets_top_level_const_not_pointee_const():
    dtype = define(fields=(("value", PointerType("int")),))
    assert access(ConstType(dtype)).dtype == ConstType(PointerType("int"))


def test_mutable_member_is_modifiable_through_const_object():
    dtype = ClassType("Record", ("Record",), COORD)
    definition = ClassDefinition(dtype, Scope())
    definition.add_member(Symbol("value", "member", COORD), mutable=True)
    definition.finish()
    assert access(ConstType(dtype)).dtype == "int"
    invalid = ClassDefinition(ClassType("Bad", ("Bad",), COORD), Scope())
    with pytest.raises(CppDiagnostic, match="cpp.invalid-mutable"):
        invalid.add_member(Symbol("value", "member", COORD, dtype=ConstType("int")), mutable=True)


@pytest.mark.parametrize("dtype,operator", [("int", "."), ("int", "->"),
                                           (PointerType("int"), "->")])
def test_nonclass_member_access_diagnostics(dtype, operator):
    with pytest.raises(CppDiagnostic, match="cpp.invalid-member-access"):
        access(dtype, operator=operator)


def test_incomplete_and_temporary_member_access_fail_before_projection():
    with pytest.raises(CppDiagnostic, match="cpp.incomplete-class"):
        access(ClassType("Incomplete", ("Incomplete",), COORD))
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.classes"):
        access(define(fields=(("value", "int"),)), lvalue=False)


def test_default_and_empty_list_initialization_preserve_const_member_distinction():
    dtype = define(fields=(("value", ConstType("int")),))
    assert dtype.definition.default_constructor_deleted
    assert dtype.definition.copy_assignment_deleted
    with pytest.raises(CppDiagnostic, match="cpp.deleted-default-constructor"):
        default_initialize(dtype, COORD)
    init = empty_list_initialize(dtype, COORD)
    assert isinstance(init, c_ast.InitList)
    assert init.exprs[0].value == "0"
    private = define("Private", key="class", fields=(("value", ConstType("int")),))
    with pytest.raises(CppDiagnostic, match="cpp.deleted-default-constructor"):
        empty_list_initialize(private, COORD)


def test_recursive_special_member_properties_are_not_width_guesses():
    empty = define("Empty")
    scalar = define("Scalar", fields=(("value", "int"),))
    readonly = define("Readonly", fields=(("value", ConstType("int")),))
    nested = define("Nested", fields=(("value", readonly),))
    empty_nested = define("EmptyNested", fields=(("empty", ConstType(empty)),))
    assert default_initialize(ConstType(empty), COORD) is None
    assert default_initialize(ConstType(empty_nested), COORD) is None
    assert default_initialize(scalar, COORD) is None
    with pytest.raises(CppDiagnostic, match="cpp.const-initializer"):
        default_initialize(ConstType(scalar), COORD)
    assert nested.definition.default_constructor_deleted
    assert nested.definition.copy_assignment_deleted
    assert isinstance(empty_list_initialize(nested, COORD).exprs[0], c_ast.InitList)


def test_each_class_initialization_gets_an_independent_ast():
    dtype = define(fields=(("value", "int"),))
    first = empty_list_initialize(dtype, COORD)
    second = empty_list_initialize(dtype, COORD)
    assert first is not second
    assert first.exprs[0] is not second.exprs[0]
