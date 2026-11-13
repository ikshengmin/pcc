"""Virtual functions of polymorphic classes without base classes.

N4659 [class.virtual], [class.abstract]/2-3, [dcl.fct.spec]/1, [class.mem]/1
(pure-specifier and virt-specifiers), [class.base.init]/13, [class.cdtor]/4
and [dcl.init.aggr]/1; Itanium C++ ABI 2.4 (table pointer at offset 0). An
unqualified call loads its callee from the object's table; a qualified call is
direct. Every program returns 42 from cpp_test_entry.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'
# TinyXML2 MemPool, the first unchanged project class with virtual members
# (tinyxml2.h lines 326-336).
UPSTREAM_MEMPOOL = (b'class MemPool\r\n{\r\npublic:\r\n    MemPool() {}\r\n    virtual ~MemPool() {}\r\n\r\n'
                    b'    virtual size_t ItemSize() const = 0;\r\n    virtual void* Alloc() = 0;\r\n'
                    b'    virtual void Free( void* ) = 0;\r\n    virtual void SetTracked() = 0;\r\n};\r\n')
WIDGET = 'struct W{W(int v);virtual int get() const;int v;};'
CASES = (
    ('pointer-call-uses-table',
     'struct Counter{Counter(int v):value(v){} virtual int get() const {return value;} int value;};'
     'int read(const Counter*c){return c->get();}int cpp_test_entry(){Counter c(42);return read(&c);}'),
    ('reference-call-uses-table',
     'struct Counter{Counter(int v):value(v){} virtual int get(){return value;} int value;};'
     'int cpp_test_entry(){Counter c(40);Counter&r=c;return r.get()+2;}'),
    ('implicit-this-call',
     'struct Shape{Shape(){} virtual int sides() const {return 6;} int twice() const {return sides()*7;}};'
     'int cpp_test_entry(){Shape s;return s.twice();}'),
    ('qualified-call',
     'struct Shape{Shape(){} virtual int sides() const {return 6;} int twice() const {return Shape::sides()*7;}};'
     'int cpp_test_entry(){Shape s;return s.twice();}'),
    # The table pointer is stored before members are initialized.
    ('member-initializer-sees-own-table',
     'struct Init{Init():v(seed()){} virtual int seed() const {return 42;} int v;};'
     'int cpp_test_entry(){Init i;return i.v;}'),
    ('abstract-class-with-virtual-destructor',
     'typedef unsigned long size_t;' + UPSTREAM_MEMPOOL.decode()
     + 'int cpp_test_entry(){return (int)sizeof(MemPool)+34;}'),
    ('virtual-destructor-at-scope-exit',
     'struct Probe{Probe(int*p):out(p){} virtual ~Probe(){*out=42;} int*out;};'
     'int cpp_test_entry(){int r=0;{Probe p(&r);}return r;}'),
    ('virtual-and-nonvirtual-overloads',
     'struct O{O(){} virtual int f(int x){return x;} int f(){return 2;}};'
     'int cpp_test_entry(){O o;return o.f(40)+o.f();}'),
    ('const-object-call',
     'struct K{K(int v):v(v){} virtual int get() const {return v;} int v;};'
     'int cpp_test_entry(){const K k(42);return k.get();}'),
    ('final-virtual-function',
     'struct F{F(){} virtual int f() final {return 42;}};int cpp_test_entry(){F f;return f.f();}'),
    # [dcl.fct.default]/10: defaults come from the static type's declaration.
    ('default-argument-of-virtual-call',
     'struct G{G(){} virtual int f(int x=42){return x;}};int cpp_test_entry(){G g;G*p=&g;return p->f();}'),
    # 16 + 16 + 8 + 2: the pointer precedes and aligns every member.
    ('table-pointer-layout',
     'struct A{A(){} virtual int f(){return 1;} char c;};'
     'struct B{B(){} virtual int f(){return 2;} int i; char c;};'
     'struct E{E(){} virtual void f(){}};'
     'int cpp_test_entry(){return (int)(sizeof(A)+sizeof(B)+sizeof(E))+2;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    # Each unit has its own table; the defining unit's constructor stores it.
    ('out-of-line-members-in-another-unit',
     (('caller.cpp', WIDGET + 'int cpp_test_entry(){W w(21);return w.get();}'),
      ('members.cpp', WIDGET + 'W::W(int x):v(x){}int W::get() const{return v*2;}'))),
)
ABSTRACT = 'struct I{virtual int f()=0;};'
INVALID = (
    ('abstract-local-object', ABSTRACT + 'int cpp_test_entry(){I i;return 0;}', 'cpp.abstract-class'),
    ('abstract-member', ABSTRACT + 'struct H{I i;};', 'cpp.abstract-class'),
    ('abstract-array', ABSTRACT + 'int cpp_test_entry(){I a[2];return 0;}', 'cpp.abstract-class'),
    ('abstract-placement-object',
     'void* operator new(unsigned long, void*p) noexcept;' + ABSTRACT
     + 'int cpp_test_entry(){unsigned char b[16];new(b) I;return 0;}', 'cpp.abstract-class'),
    ('static-virtual-member', 'struct S{virtual static int f();};', 'cpp.invalid-virtual'),
    ('virtual-constructor', 'struct S{virtual S();};', 'cpp.invalid-virtual'),
    ('virtual-data-member', 'struct S{virtual int x;};', 'cpp.invalid-virtual'),
    ('duplicate-virtual', 'struct S{S(){} virtual virtual int f();};', 'cpp.duplicate-virtual-specifier'),
    ('pure-nonvirtual-member', 'struct S{S(){} int f()=0;};', 'cpp.invalid-pure-specifier'),
    ('pure-specifier-on-definition', 'struct S{virtual int f()=0{return 1;}};', 'cpp.invalid-pure-specifier'),
    ('override-without-base', 'struct S{S(){} virtual int f() override {return 1;}};', 'cpp.invalid-override'),
    ('final-nonvirtual-member', 'struct S{S(){} int f() final {return 1;}};', 'cpp.invalid-virt-specifier'),
)
# The implicit and defaulted default-constructor rows moved verbatim to
# tests/cpp/test_cpp_implicit_polymorphic_constructors.py (now positive).
DEFERRED = (
    ('defaulted-virtual-destructor', 'struct D{D(){} virtual ~D()=default;};'),
    ('variadic-virtual-member', 'struct S{S(){} virtual int f(int,...);};'),
    ('virtual-operator-member', 'struct S{S(){} virtual S& operator=(const S&);};'),
)


def _calls(node):
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, c_ast.FuncCall):
            yield current
        pending.extend(child for _name, child in current.children())


def _record(ast, tag):
    for item in ast.ext:
        if (isinstance(item, c_ast.Decl) and isinstance(item.type, c_ast.Struct)
                and item.type.name == tag and item.type.decls is not None):
            return item.type
    raise AssertionError('missing record ' + tag)


def test_tinyxml_mempool_is_unchanged_source():
    root = Path(__file__).resolve().parents[2]
    lines = (root / 'projects/tinyxml2-11.0.0/tinyxml2.h').read_bytes().splitlines(keepends=True)
    assert b''.join(lines[325:336]) == UPSTREAM_MEMPOOL


def test_tinyxml_mempool_declares_its_virtual_members_in_order():
    parser = CppParser(TARGET)
    parser.parse('typedef unsigned long size_t;' + UPSTREAM_MEMPOOL.decode(), 'mempool.cpp')
    definition = parser.scope.names['MemPool'].dtype.definition
    assert [symbol.name for symbol in definition.virtual_functions] == [
        '~MemPool', 'ItemSize', 'Alloc', 'Free', 'SetTracked']
    assert [symbol.method_pure for symbol in definition.virtual_functions] == [False, True, True, True, True]
    assert definition.polymorphic and definition.abstract and not definition.aggregate


def test_table_pointer_is_the_first_field_and_entries_follow_declaration_order():
    parser = CppParser(TARGET)
    ast = parser.parse('struct A{A(){} virtual ~A(){} int x; virtual int f() const {return 1;}'
                       ' virtual int g()=0;};', 'order.cpp')
    owner = parser.scope.names['A'].dtype
    assert [decl.name for decl in _record(ast, owner.c_tag).decls] == ['__pcc_cpp_vptr', 'x']
    table = _record(ast, '__pcc_cpp_vtable_' + owner.c_tag)
    assert [decl.name for decl in table.decls] == ['__pcc_cpp_slot_' + str(index) for index in range(4)]
    storage = next(item for item in ast.ext if isinstance(item, c_ast.Decl)
                   and item.name == '__pcc_cpp_vtable_object_' + owner.c_tag)
    assert storage.storage == ['static']
    # Complete-object then deleting destructor, f, then the pure g.
    values = [expr.name if isinstance(expr, c_ast.ID) else expr.value for expr in storage.init.exprs]
    assert values == ['_ZN1AD1Ev', '0', '_ZNK1A1fEv', '0']


def test_only_unqualified_calls_load_the_table():
    parser = CppParser(TARGET)
    ast = parser.parse('struct S{S(){} virtual int f() const {return 1;}'
                       ' int a() const {return f();} int b() const {return S::f();}};'
                       'int c(const S*s){return s->f();}', 'dispatch.cpp')
    callees = {}
    for item in ast.ext:
        if isinstance(item, c_ast.FuncDef):
            callees[item.decl.name] = [type(call.name).__name__ for call in _calls(item.body)]
    assert callees['_ZNK1S1aEv'] == ['StructRef']
    assert callees['_ZNK1S1bEv'] == ['ID']
    assert callees['_Z1cPK1S'] == ['StructRef']


def test_constructor_stores_the_table_before_member_initialization():
    parser = CppParser(TARGET)
    ast = parser.parse('struct S{S():v(2){} virtual int f(){return v;} int v;};', 'construct.cpp')
    body = next(item.body for item in ast.ext
                if isinstance(item, c_ast.FuncDef) and item.decl.name == '_ZN1SC1Ev')
    targets = [item.lvalue.field.name for item in body.block_items if isinstance(item, c_ast.Assignment)]
    assert targets == ['__pcc_cpp_vptr', 'v']


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_virtual_function_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + tuple(
    (name, source, 'cpp.unsupported.virtual-functions') for name, source in DEFERRED),
    ids=[row[0] for row in INVALID + DEFERRED])
def test_virtual_function_boundaries_precede_ir(owned_only, name, source, code, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or deferred virtual function reached IR generation')
    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name + '.cpp', source),))
    assert caught.value.code == code
