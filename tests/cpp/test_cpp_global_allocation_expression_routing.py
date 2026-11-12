"""Global new/delete expression routing, authored CI obligations, UNRUN.

N4659 [stmt.ambig], [expr.delete], [expr.new]. Declaration lookahead must
not consume global allocation keywords as qualified identifiers. Deferred
delete keeps typed operands; real deallocation remains explicitly unowned.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_template_destructors import patterns


OPERATIONS = (('::delete p', (True, False)), ('::delete [] p', (True, True)))
PROGRAMS = tuple((operation, ((
    'original.cpp',
    'template<class T>struct Box{T*p;~Box(){' + operation + ';}};'
    'int cpp_test_entry(){Box<int>*p=nullptr;if(p==nullptr)return 42;return 1;}',
),)) for operation, _flags in OPERATIONS) + (
    ('qualified-type-declaration', (('original.cpp',
     'namespace N{using Value=int;}'
     'int cpp_test_entry(){::N::Value value=42;return value;}'),)),
)


@pytest.mark.parametrize('operation,flags', OPERATIONS)
def test_global_delete_retains_typed_original_operand_and_flags(operation, flags):
    parser = CppParser()
    # Exact originals from the failing existing D1 operand-identity test.
    tree = parser.parse('template<class T>struct Box{T*p;~Box(){' + operation + ';}};')
    owner = parser.scope.names['Box'].dtype
    record, = owner.methods
    deletion, = (node for node in patterns(record.body) if node.kind == 'delete')
    assert deletion.value == flags
    assert deletion.children[0].symbol is owner.member_scope.names['p']
    assert not parser.instance_registry.entries
    assert not any(isinstance(node, c_ast.FuncDef) for node in tree.ext)


@pytest.mark.parametrize('operation,_flags', OPERATIONS)
def test_global_delete_real_use_calls_the_global_deallocation_function(operation, _flags):
    source = ('template<class T>struct Box{T*p;~Box(){\n' + operation + ';\n}};'
              'int cpp_test_entry(){Box<int>b{nullptr};return 0;}')
    tree = CppParser().parse(source, 'global-delete.cpp')
    expected = '_ZdaPv' if _flags[1] else '_ZdlPvm'
    pending, names = [tree], set()
    while pending:
        node = pending.pop()
        if isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID):
            names.add(node.name.name)
        pending.extend(child for _name, child in node.children())
    assert expected in names


def test_global_new_statement_reaches_the_allocation_owner():
    # Formerly a boundary row; `::new int;` now allocates (and leaks) one int.
    tree = CppParser().parse('void f(){\n::new int;\n}', 'routing.cpp')
    pending, names = [tree], set()
    while pending:
        node = pending.pop()
        if isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID):
            names.add(node.name.name)
        pending.extend(child for _name, child in node.children())
    assert '_Znwm' in names


@pytest.mark.parametrize('operation,code', (
    ('::delete 1', 'cpp.invalid-delete-operand'),
    ('::delete [] 1', 'cpp.invalid-delete-operand'),
))
def test_global_allocation_statements_reach_their_semantic_owner(operation, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse('void f(){\n' + operation + ';\n}', 'routing.cpp')
    assert error.value.code == code
    assert error.value.coord.file == 'routing.cpp' and error.value.coord.line == 2
    assert 'expected an identifier' not in str(error.value)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_global_expression_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_global_expression_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)
