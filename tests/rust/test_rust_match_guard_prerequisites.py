"""Target-matched metadata needs an audited compiler, not a Darwin host.

These tests exercise the real availability helper and source gate expressions.
The placeholder files model availability only: no compiler is executed and no
source-certification token is created. The original negative cases still run
their actual authenticated rustc check in the ordinary language lane.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.rust import certification_support


def _guard_source():
    path = Path(__file__).with_name('test_rust_match_guards.py')
    return ast.parse(path.read_text(), filename=str(path))


def _gate_expression(tree, function_name):
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == function_name)
    gates = [mark for mark in function.decorator_list if isinstance(mark, ast.Call)
             and ast.unparse(mark.func) == 'pytest.mark.pcc_gate']
    assert len(gates) == 1
    gate = gates[0]
    assert not gate.args and len(gate.keywords) == 1
    assert gate.keywords[0].arg == 'unavailable'
    return gate.keywords[0].value


@pytest.mark.parametrize('host', ['linux', 'darwin', 'win32'])
@pytest.mark.parametrize('layout', [
    'no-receipt', 'missing-receipt', 'no-compiler', 'no-core', 'no-std', 'complete',
])
def test_metadata_and_owned_execution_keep_distinct_prerequisites(
        host, layout, tmp_path, monkeypatch):
    monkeypatch.delenv('PCC_RUSTC_INSTALL_RECEIPT', raising=False)
    receipt = tmp_path / 'installation.json'
    root = tmp_path / 'toolchain'
    if layout != 'no-receipt':
        monkeypatch.setenv('PCC_RUSTC_INSTALL_RECEIPT', str(receipt))
    if layout not in {'no-receipt', 'missing-receipt'}:
        receipt.write_text(json.dumps({'new_prefix': str(root)}))
        libraries = root / 'lib/rustlib/aarch64-apple-darwin/lib'
        libraries.mkdir(parents=True)
        if layout != 'no-compiler':
            (root / 'bin').mkdir()
            (root / 'bin/rustc').write_text('availability placeholder, not executable')
        for library in ('core', 'std'):
            if layout != 'no-' + library:
                (libraries / ('lib' + library + '-placeholder.rlib')).write_bytes(b'')

    missing = certification_support.darwin_check_prerequisite()
    assert (missing is None) == (layout == 'complete')
    tree = _guard_source()
    lane = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'DARWIN_LANE'
                        for target in node.targets))
    scope = {'__builtins__': {}, 'sys': SimpleNamespace(platform=host),
             'darwin_check_prerequisite': certification_support.darwin_check_prerequisite}
    scope['DARWIN_LANE'] = eval(compile(ast.Expression(lane), '<owned-gate>', 'eval'), scope)

    metadata = _gate_expression(tree, 'test_actual_check_rejects_guard_borrow_errors')
    owned = _gate_expression(tree, 'test_checked_program_runs_with_owned_compile_assemble_and_link')
    assert eval(compile(ast.Expression(metadata), '<metadata-gate>', 'eval'), scope) == missing
    expected_owned = missing or (
        None if host == 'darwin' else 'owned execution here is the arm64 Darwin lane')
    assert eval(compile(ast.Expression(owned), '<owned-gate>', 'eval'), scope) == expected_owned


def test_metadata_negative_cases_keep_the_authenticated_target_and_error_oracle():
    tree = _guard_source()
    cases = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'BORROW_ERRORS'
                         for target in node.targets))
    assert [code for _, code in cases] == ['E0507', 'E0506', 'E0594']
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'test_actual_check_rejects_guard_borrow_errors')
    calls = [node for statement in function.body for node in ast.walk(statement)
             if isinstance(node, ast.Call)]
    checks = [node for node in calls if ast.unparse(node.func) == 'certify_source']
    assert len(checks) == 1 and ast.unparse(checks[0].args[0]) == 'source'
    assert {kw.arg: ast.literal_eval(kw.value) for kw in checks[0].keywords} == {
        'target_triple': 'aarch64-apple-darwin'}
    raises = [node for node in calls if ast.unparse(node.func) == 'pytest.raises']
    assert len(raises) == 1 and ast.unparse(raises[0].args[0]) == 'RustExternalCheckError'
    pattern = next(kw.value for kw in raises[0].keywords if kw.arg == 'match')
    for _, code in cases:
        assert eval(compile(ast.Expression(pattern), '<error-oracle>', 'eval'),
                    {'__builtins__': {}, 'code': code}) == 'error\\[' + code + '\\]'
    groups = [mark for mark in function.decorator_list if isinstance(mark, ast.Call)
              and ast.unparse(mark.func) == 'pytest.mark.xdist_group']
    assert len(groups) == 1
    assert {kw.arg: ast.literal_eval(kw.value) for kw in groups[0].keywords} == {
        'name': 'rust-native-reference'}
