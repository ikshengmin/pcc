"""Source definitions, actual table types and full-translation-unit obligations."""
from copy import deepcopy
import re

import pytest
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.virtual_functions import table_tag

TARGET = 'x86_64-unknown-linux-gnu'
UNSUPPORTED = 'cpp.unsupported.virtual-functions'
BOUND = 'struct S{S(){} virtual int f(int n,...){return n;}};'


@pytest.mark.parametrize('source', [
    BOUND,
    'struct S{S(){} virtual int f(int,...);};int S::f(int renamed,...){return renamed;}',
    ('struct B{virtual int f(int n,...){return n;}};'
     'struct D:B{int f(int k,...) override{return k;}};'),
    ('struct S{virtual int f(int n,...){return n;}'
     'virtual int f(double d,...){return (int)d;}};'),
])
def test_actual_in_tu_variadic_virtual_definition(source):
    parser = CppParser(TARGET)
    parser.parse(source, 'bound.cpp')
    proof = parser.variadic_virtual_obligations
    assert proof.declarations and proof.entries
    for method, shape in proof.entries.values():
        assert proof.bodies[method] == shape


@pytest.mark.parametrize('source,code', [
    ('struct S{S(){} virtual int f(int,...);};', UNSUPPORTED),
    ('struct Other{Other(){} virtual void emit(const char*,...);};', UNSUPPORTED),
    ('struct A{virtual int f(int,...);};struct B{virtual int f(int n,...){return n;}};', UNSUPPORTED),
    ('struct A{virtual int f(int,...);virtual int f(double n,...){return (int)n;}};', UNSUPPORTED),
    ('struct B{virtual int f(int n,...){return n;}};struct D:B{int f(int,...)override;};', UNSUPPORTED),
    ('struct B{virtual int f(int,...)=0;};', UNSUPPORTED),
    ('struct B{virtual int f(int,...);};struct D:B{int f(int)override;};', 'cpp.invalid-override'),
])
def test_source_unknown_final_overrider_is_not_borrowed_from_another(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(source, 'unbound.cpp')
    assert error.value.code == code


def test_unresolved_obligations_diagnose_first_source_declaration():
    source = 'struct S {\n  virtual int first(int,...);\n  virtual int next(int,...);\n};\n'
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(source, 'first.cpp')
    assert error.value.code == UNSUPPORTED
    assert error.value.coord.file == 'first.cpp' and error.value.coord.line == 2


def test_reused_parser_does_not_import_another_translation_units_definition():
    parser = CppParser(TARGET)
    parser.parse(BOUND, 'first.cpp')
    with pytest.raises(CppDiagnostic) as error:
        parser.parse('struct S{virtual int f(int,...);};', 'second.cpp')
    assert error.value.code == UNSUPPORTED and error.value.coord.file == 'second.cpp'


def test_tu_seal_requires_the_actual_declaring_class_slot_even_for_unused_method():
    parser = CppParser(TARGET)
    parser.parse(BOUND, 'missing-slot.cpp')
    proof = parser.variadic_virtual_obligations
    assert len(proof.bodies) == len(proof.entries) == 1
    proof.entries.clear()
    with pytest.raises(CppDiagnostic) as error:
        proof.finish()
    assert error.value.code == UNSUPPORTED


@pytest.mark.parametrize('change', ['receiver', 'fixed', 'ellipsis'])
def test_actual_table_field_must_match_source_prototype(change):
    parser = CppParser(TARGET)
    ast = parser.parse(BOUND, 'altered-slot.cpp')
    owner = parser.scope.lookup('S').dtype
    method = owner.definition.scope.lookup('f').overloads[0]
    table = next(n.type for n in ast.ext if isinstance(n, c_ast.Decl)
                 and isinstance(n.type, c_ast.Struct) and n.type.name == table_tag(owner) and n.type.decls)
    field = deepcopy(table.decls[method.virtual_index])
    function = field.type.type
    if change == 'receiver':
        function.args.params[0], function.args.params[-1] = function.args.params[-1], function.args.params[0]
    elif change == 'fixed':
        function.args.params[1].type.type.names = ['double']
    else:
        function.args.params.pop()
    entry = owner.definition.virtual_entries[method.virtual_index]
    with pytest.raises(CppDiagnostic) as error:
        parser.variadic_virtual_obligations.slot(owner.definition, method.virtual_index, entry, field)
    assert error.value.code == UNSUPPORTED


def test_owned_typed_indirect_call_and_variadic_default_promotions():
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, postprocess_ir_text
    from pcc.frontends.c.passes.context import PassContext

    parser = CppParser(TARGET)
    source = BOUND + 'int observe(S*p,float f,signed char c,unsigned short u){return p->f(42,f,c,u);}'
    ast = parser.parse(source, 'promotions.cpp')
    owner = parser.scope.lookup('S').dtype
    method = owner.definition.scope.lookup('f').overloads[0]
    table = next(n.type for n in ast.ext if isinstance(n, c_ast.Decl)
                 and isinstance(n.type, c_ast.Struct) and n.type.name == table_tag(owner) and n.type.decls)
    field = table.decls[method.virtual_index]
    assert isinstance(field.type, c_ast.PtrDecl)
    assert isinstance(field.type.type.args.params[-1], c_ast.EllipsisParam)
    assert isinstance(field.type.type.args.params[0].type, c_ast.PtrDecl)
    generator = CCodeGenerator(translation_unit_name='promotions.cpp',
                               pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    generator.generate_code(ast)
    ir = postprocess_ir_text(str(generator.module))
    calls = [line for line in ir.splitlines() if re.search(r'\bcall i32 \(ptr, i32, \.\.\.\) %', line)]
    assert len(calls) == 1, ir
    assert re.search(r'%[^()]+\(ptr %[^,]+, i32 42, double %[^,]+, i32 %[^,]+, i32 %[^)]+\)', calls[0])
    assert 'fpext float' in ir and 'sext i8' in ir and 'zext i16' in ir
