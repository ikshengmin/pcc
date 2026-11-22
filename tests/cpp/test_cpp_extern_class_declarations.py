"""Declaration-only class objects, not static lifetime or foreign ABI support.

C++17 [basic.def], [basic.link], [dcl.link]. Positive declarations do not
construct, destroy, allocate or define objects. These are new test obligations;
no native-pcc1 or project-completion claim is made by this suite.
"""
import shutil
import subprocess

import pytest
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, ir
from pcc.frontends.c.passes.context import PassContext
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ConstType, unqualified
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

TARGET = 'x86_64-unknown-linux-gnu'
ORIGINAL_TAG = 'struct Tag{explicit Tag()=default;};extern const Tag tag;'
VALID = (
    ('original_tag', ORIGINAL_TAG),
    ('repeat_cv', 'struct S{int n;};extern const S value;extern S const value;'),
    ('alias', 'struct S{int n;};using Alias=S;extern Alias value;extern S value;'),
    ('namespace_reopen', 'namespace n{struct S{int n;};extern S value;}'
     'namespace n{extern S value;}'),
    ('private_special_members', 'class S{S();~S();};extern S value;'),
    ('implicitly_deleted_constructor', 'struct S{S()=default;const int n;};extern S value;'),
    ('abstract', 'struct S{virtual int f()=0;};extern S value;'),
    ('cpp_linkage_single', 'struct S{int n;};extern "C++" S value;'),
    ('cpp_linkage_block', 'struct S{int n;};extern "C++"{extern S value;}'),
)
INVALID = (
    ('cv_conflict', 'struct S{};extern S value;extern const S value;', 'cpp.conflicting-object-type'),
    ('nominal_conflict', 'struct S{};struct T{};extern S value;extern T value;', 'cpp.conflicting-object-type'),
    ('scalar_conflict', 'struct S{};extern S value;extern int value;', 'cpp.conflicting-object-type'),
    ('function_conflict', 'struct S{};extern S value;int value();', 'cpp.redeclaration'),
)
# These legal or unsupported forms must not acquire object lifetime support as
# a side effect of accepting pure external declarations. Specific old contracts
# are retained; broader parser boundaries are separately explicit below.
UNSUPPORTED = (
    ('incomplete_class', 'struct F;extern F value;', 'cpp.unsupported.static-class-lifetime'),
    ('union', 'union U{int x;};extern U value;', 'cpp.unsupported.static-class-lifetime'),
    ('specialization', 'template<class T>struct Box{T x;};extern Box<int> value;',
     'cpp.unsupported.static-class-lifetime'),
    ('extern_copy_initializer', 'struct S{int x;};extern S origin;extern S target=origin;',
     'cpp.unsupported.static-class-lifetime'),
    ('definition', 'struct S{};S value;', 'cpp.unsupported.static-class-lifetime'),
    ('extern_initializer', 'struct S{};extern S value{};', 'cpp.unsupported.static-class-lifetime'),
    ('later_definition', 'struct S{};extern S value;S value;', 'cpp.unsupported.static-class-lifetime'),
    ('c_linkage', 'struct S{};extern "C" S value;', 'cpp.unsupported.static-class-lifetime'),
    ('c_linkage_block', 'struct S{};extern "C"{extern S value;}', 'cpp.unsupported.static-class-lifetime'),
    ('c_linkage_after_cpp', 'struct S{};extern S value;extern "C" S value;', 'cpp.unsupported.static-class-lifetime'),
    ('internal_namespace', 'struct S{};namespace{extern S value;}', 'cpp.unsupported.static-class-lifetime'),
    ('volatile', 'struct S{};extern volatile S value;', 'cpp.unsupported.volatile'),
)
OTHER_BOUNDARIES = (
    ('struct S{};inline extern S value;', 'cpp.unsupported.inline-declarations'),
    ('struct S{};thread_local extern S value;', 'cpp.unsupported.syntax'),
    ('template<class T>extern T value;', 'cpp.unsupported.variable-templates'),
    ('struct S{};void f(){extern S value;}', 'cpp.unsupported.syntax'),
)


def _parse(source):
    parser = CppParser(target_triple=TARGET)
    return parser, parser.parse(source, 'extern-class.cpp')


def _objects(parser):
    return [s for s in parser.namespace_objects
            if getattr(unqualified(s.dtype), 'cpp_class_type', False)]


@pytest.mark.parametrize('name,source', VALID, ids=[row[0] for row in VALID])
def test_declaration_has_identity_but_no_initializer_storage_or_lifetime(name, source):
    parser, ast = _parse(source)
    objects = _objects(parser)
    assert len(objects) == 1
    symbol = objects[0]
    assert symbol.namespace_object and not symbol.defined
    assert not symbol.internal_linkage and symbol.language_linkage == 'C++'
    assert not symbol.initialization_complete
    assert symbol.constant_fact is None
    declarations = [n for n in ast.ext if isinstance(n, c_ast.Decl) and n.name == symbol.c_name]
    assert declarations == [symbol.object_declaration]
    assert declarations[0].init is None and declarations[0].storage == ['extern']
    assert not parser.instance_registry.entries
    # Existing class metadata can be present; a declaration must not request
    # generated constructor/destructor functions through the shared worklist.
    definition = unqualified(symbol.dtype).definition
    special = list(definition.constructors.overloads)
    if definition.destructor is not None:
        special.append(definition.destructor)
    assert not any(isinstance(n, c_ast.FuncDef) and n.decl.name in {s.c_name for s in special}
                   for n in ast.ext)


def test_alias_redeclaration_keeps_exact_canonical_cv_nominal_identity():
    parser, _ = _parse('struct S{int n;};using Alias=S;extern const Alias value;extern const S value;')
    symbol = parser.scope.names['value']
    assert isinstance(symbol.dtype, ConstType)
    assert unqualified(symbol.dtype) is parser.scope.names['S'].dtype
    assert len(_objects(parser)) == 1


def test_same_spelling_in_distinct_namespaces_does_not_merge_entities():
    parser, _ = _parse('struct S{};namespace a{extern S value;}namespace b{extern S value;}')
    a = parser.scope.names['a'].namespace_scope.names['value']
    b = parser.scope.names['b'].namespace_scope.names['value']
    assert a is not b and a.c_name != b.c_name
    assert unqualified(a.dtype) is unqualified(b.dtype)
    assert len(_objects(parser)) == 2


@pytest.mark.parametrize('name,source', VALID[:4], ids=[row[0] for row in VALID[:4]])
def test_unused_extern_declaration_emits_no_ir_global_or_definition(owned_only, name, source):
    parser, ast = _parse(source)
    symbol = _objects(parser)[0]
    generator = CCodeGenerator(translation_unit_name=name+'.cpp',
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    generator.generate_code(ast)
    assert generator.module.get_global(symbol.c_name) is None
    assert not any(isinstance(g, ir.GlobalVariable)
                   for g in generator.module.globals.values())
    assert not any(isinstance(g, ir.Function) and g.blocks
                   for g in generator.module.globals.values())


# Separate ODR-use probes preserve every original declaration source above.
# They require a genuine external symbol, unlike an unused declaration.
ADDRESS_USES = (
    'const Tag* address(){return &tag;}',
    'const S* address(){return &value;}',
    'S* address(){return &value;}',
    'n::S* address(){return &n::value;}',
)


@pytest.mark.parametrize('name,source,use',
                         [(name, source, use) for (name, source), use in zip(VALID[:4], ADDRESS_USES)],
                         ids=[row[0] for row in VALID[:4]])
def test_odr_used_address_has_typed_external_ir_global(owned_only, name, source, use):
    parser, ast = _parse(source + use)
    symbol = _objects(parser)[0]
    address = parser.scope.names['address'].overloads[0]
    definitions = [n for n in ast.ext if isinstance(n, c_ast.FuncDef)
                   and n.decl.name == address.c_name]
    assert len(definitions) == 1
    returns = [n for n in definitions[0].body.block_items if isinstance(n, c_ast.Return)]
    assert len(returns) == 1
    assert isinstance(returns[0].expr, c_ast.UnaryOp) and returns[0].expr.op == '&'
    assert isinstance(returns[0].expr.expr, c_ast.ID)
    assert returns[0].expr.expr.name == symbol.c_name
    generator = CCodeGenerator(translation_unit_name=name+'-address.cpp',
                              pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    generator.generate_code(ast)
    value = generator.module.get_global(symbol.c_name)
    assert isinstance(value, ir.GlobalVariable)
    assert value.initializer is None and value.linkage == 'external'
    assert isinstance(value.value_type, (ir.LiteralStructType, ir.IdentifiedStructType))
    function = generator.module.get_global(address.c_name)
    assert isinstance(function, ir.Function) and function.blocks
    assert function.function_type.return_type == value.type
    assert not symbol.defined and not symbol.initialization_complete
    assert not parser.instance_registry.entries


@pytest.mark.parametrize('name,source,code', INVALID + UNSUPPORTED,
                         ids=[row[0] for row in INVALID + UNSUPPORTED])
def test_boundary_diagnostic_precedes_object_emission(monkeypatch, name, source, code):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('rejected class object reached object emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name+'.cpp', source),))
    assert caught.value.code == code


@pytest.mark.parametrize('source,code', OTHER_BOUNDARIES)
def test_inline_tls_dependent_and_block_objects_keep_existing_boundary(source, code):
    with pytest.raises(CppDiagnostic) as caught:
        _parse(source)
    assert caught.value.code == code


@pytest.mark.integration
@pytest.mark.parametrize('name,source', VALID, ids=[row[0] for row in VALID])
@pytest.mark.pcc_gate(probe=lambda: shutil.which('g++') is not None)
def test_identical_positive_source_real_gcc17(tmp_path, name, source):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.fail('G++ reference unavailable; no replacement')
    path = tmp_path / (name+'.cpp')
    path.write_bytes(source.encode())
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=10)
    assert path.read_bytes() == source.encode()
    assert result.returncode == 0, result.stderr


def test_original_tag_separate_tu_needs_no_definition_or_constructor(owned_only):
    # Keep the original declaration TU byte-for-byte; the entry is a distinct
    # TU, not a rewritten declaration or an invented definition for tag.
    units = (('tag.cpp', ORIGINAL_TAG), ('entry.cpp', 'int cpp_test_entry(){return 42;}'))
    artifact = compile_host_cpp(units)
    assert artifact.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'
    assert len(artifact.objects) == 2 and len(artifact.ir) == 2
    assert artifact.executable[:4] == b'\x7fELF'


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
@pytest.mark.pcc_gate(probe=lambda: shutil.which('g++') is not None)
def test_identical_conflicting_redeclaration_real_gcc17(tmp_path, name, source, code):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.fail('G++ reference unavailable; no replacement')
    path = tmp_path / (name+'.cpp')
    path.write_bytes(source.encode())
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=10)
    assert path.read_bytes() == source.encode()
    assert result.returncode == 1 and result.stdout == b''
    assert b'error:' in result.stderr and b'internal compiler error' not in result.stderr.lower()


@pytest.mark.integration
@pytest.mark.parametrize('name,source,use',
                         [(name, source, use) for (name, source), use in zip(VALID[:4], ADDRESS_USES)],
                         ids=[row[0] for row in VALID[:4]])
@pytest.mark.pcc_gate(probe=lambda: shutil.which('g++') is not None)
def test_identical_odr_address_probe_real_gcc17(tmp_path, name, source, use):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.fail('G++ reference unavailable; no replacement')
    text = source + use
    path = tmp_path / (name+'-address.cpp')
    path.write_bytes(text.encode())
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=10)
    assert path.read_bytes() == text.encode()
    assert result.returncode == 0, result.stderr
