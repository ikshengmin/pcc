"""Implicit and defaulted default constructors of polymorphic classes.

N4659 [class.ctor]/4-7, [class.cdtor]/4, [dcl.init]/8.2.
https://timsong-cpp.github.io/cppwp/n4659/class.ctor
A polymorphic class without a user-declared constructor has an implicitly
declared default constructor: public, defaulted on its first declaration, not
user-provided. Like `T() = default;` it constructs the base, stores the class's
own table pointer and then initializes members; value-initialization (`T()`,
`T{}`) zero-initializes the object before calling it. TinyXML2's `XMLVisitor`
(projects/tinyxml2-11.0.0, Zlib) is such a class. Original tests; no external
compiler implementation is copied.

Remaining gap: implicit and inline definitions, and the per-unit table, are
emitted eagerly rather than when odr-used ([basic.def.odr]/3,
[class.ctor]/7), so a table entry's out-of-line virtual function must be
defined in some linked unit even when no object is ever created; the link
then fails naming that function.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

PROGRAMS = (
    # Former DEFERRED rows of test_cpp_virtual_functions, moved verbatim.
    ('implicit-default-constructor',
     'struct V{virtual int f(){return 42;}};int cpp_test_entry(){V v;return v.f();}'),
    ('defaulted-default-constructor',
     'struct D{D()=default; virtual int f(){return 42;}};int cpp_test_entry(){D d;return d.f();}'),
    # The table pointer is this class's: a derived constructor first runs the
    # implicit base constructor (base table), then stores its own.
    ('implicit-base-of-derived-constructor',
     'struct B{virtual int f(){return 1;}};struct D:B{D(){} int f() override {return 42;}};'
     'int g(B*b){return b->f();}int cpp_test_entry(){D d;return g(&d);}'),
    # Value-initialization (`V v{}`) zeroes first; default-initialization of
    # `v.x` would leave it indeterminate.
    ('value-initialization-zeroes-before-the-implicit-constructor',
     'struct V{virtual int f(){return 40;} int x;};'
     'int cpp_test_entry(){V v{};return v.f()+v.x+2;}'),
)


# Former unsupported rows, moved verbatim: test_cpp_class_semantics (and its
# execution twin), test_cpp_destructors, test_cpp_inline_destructors and
# test_cpp_member_functions pinned these as polymorphic classes without
# table-pointer initialization.
MOVED_DECLARATIONS = (
    ('struct S{virtual int method();};', 'int S::method(){return 42;}', 'S s;return s.method();'),
    ('struct S{virtual ~S();};', 'S::~S(){}', 'S s;return 42;'),
    ('struct S{virtual int f();};', 'int S::f(){return 42;}', 'S s;return s.f();'),
)
MOVED_INLINE_DESTRUCTOR = 'struct S{virtual ~S(){}};'


@pytest.mark.parametrize('declaration,definition,body', MOVED_DECLARATIONS, ids=[row[0] for row in MOVED_DECLARATIONS])
def test_former_rows_declare_an_implicit_constructor(declaration, definition, body):
    from pcc.frontends.cpp import CppParser
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse(declaration, 'moved.cpp')
    symbol, = parser.scope.lookup('S').dtype.definition.constructors.overloads
    assert symbol.constructor_implicit and symbol.c_name == '_ZN1SC1Ev'


@pytest.mark.parametrize('split', [False, True], ids=['one-unit', 'header-and-source-units'])
@pytest.mark.parametrize('declaration,definition,body', MOVED_DECLARATIONS, ids=[row[0] for row in MOVED_DECLARATIONS])
def test_former_rows_link_with_their_virtual_definitions(owned_only, declaration, definition, body, split):
    # The TinyXML2 shape: the class in a header, its virtual functions
    # defined in another unit, objects created in the test unit.
    entry = 'int cpp_test_entry(){' + body + '}'
    units = ((('use.cpp', declaration + entry), ('define.cpp', declaration + definition)) if split
             else (('use.cpp', declaration + definition + entry),))
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_former_inline_virtual_destructor_row_compiles(owned_only):
    units = (('moved.cpp', MOVED_INLINE_DESTRUCTOR + 'int cpp_test_entry(){S s;return 42;}'),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('declaration,definition,body', MOVED_DECLARATIONS, ids=[row[0] for row in MOVED_DECLARATIONS])
def test_eagerly_emitted_tables_still_need_their_virtual_definitions(owned_only, declaration, definition, body):
    # Valid C++ (no object is created), but the table is emitted anyway.
    from pcc.backend.elf_x86_64 import ElfError
    with pytest.raises(ElfError, match='undefined static ELF symbols: _ZN1S'):
        compile_host_cpp((('moved.cpp', declaration + 'int cpp_test_entry(){return 42;}'),))


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_polymorphic_classes_without_user_constructors_compile_to_stable_artifacts(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_polymorphic_classes_without_user_constructors_execute_against_real_cpp17_reference(name, source, tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair
    execute_row_pair(tmp_path, monkeypatch, ((name + '.cpp', source),))


def test_the_implicit_constructor_stores_the_table_pointer_in_its_c1():
    from pcc.frontends.cpp import CppParser
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse('struct V{virtual int f(){return 42;}};int g(){V v;return v.f();}', 'v.cpp')
    symbol, = parser.scope.lookup('V').dtype.definition.constructors.overloads
    assert (symbol.constructor_implicit, symbol.constructor_user_provided, symbol.constructor_trivial) == (True, False, False)
    assert symbol.c_name == '_ZN1VC1Ev' and symbol.access == 'public'


@pytest.mark.parametrize('source,code', (
    # Constructing a base that has a constructor from an implicit or
    # defaulted constructor remains the existing base-subobject gap.
    ('struct B{B(){} virtual int f(){return 1;}};struct D:B{};', 'cpp.unsupported.constructor-subobjects'),
    # Class-template specializations have no defaulted-constructor binding.
    ('template<int N>struct T{virtual int f(){return N;}};int g(){T<1> t;return t.f();}',
     'cpp.unsupported.virtual-functions'),
))
def test_remaining_constructor_shapes_stay_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('gap.cpp', source),))
    assert error.value.code == code
