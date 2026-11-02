"""Keep actual Rust tool obligations serial within the six-worker loadgroup run.

Native differential nodes already share this group. Metadata checks (including
negative originals) and helpers that delegate to them must join it too: six
independent rustc processes can exceed the unchanged process-tree RSS limit.
These source-level regressions never import the inspected tests or run tools.
The existing guard still owns timeout/RSS failures; serialization is no waiver.
"""
import ast
from pathlib import Path
import tomllib


GROUP = 'rust-native-reference'
# Exact callable identities, not function-name/reference-word heuristics. Owned
# native linking/execution shares the group with the independent external oracle.
RESOURCE_CALLS = {
    'pcc.frontends.rust.check_rust_source',
    'pcc.frontends.rust.compile_rust_checked',
    'pcc.frontends.rust.external_check.check_rust_source',
    'pcc.backend.elf_x86_64.link_static_executable',
    'subprocess.run', 'subprocess.Popen',
    'subprocess.check_call', 'subprocess.check_output',
}


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return parent + '.' + node.attr if parent else ''
    return ''


def _imports(nodes):
    result = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                result[alias.asname or alias.name.split('.')[0]] = (
                    alias.name if alias.asname else alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                result[alias.asname or alias.name] = node.module + '.' + alias.name
    return result


def _resource_callers(trees):
    """Follow the current tests' direct calls, import aliases and helper exports.

    Nested callback bodies are conservatively attributed to their enclosing
    function, as are guarded/conditionally reachable calls. This is a scheduling
    check, not a claim to prove Python's arbitrary dynamic call graph or whether
    a mocked adapter really launches a compiler on a particular branch.
    """
    definitions = {module + '.' + node.name: (module, node)
                   for module, tree in trees.items() for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    imports = {module: _imports(tree.body) for module, tree in trees.items()}

    def resolve(raw, module, aliases):
        head, dot, tail = raw.partition('.')
        if head in aliases:
            raw = aliases[head] + (dot + tail if dot else '')
        elif module + '.' + head in definitions:
            raw = module + '.' + raw
        seen = set()
        while raw not in seen:
            seen.add(raw)
            for owner, bindings in imports.items():
                if raw.startswith(owner + '.'):
                    head, dot, tail = raw[len(owner) + 1:].partition('.')
                    if head in bindings:
                        raw = bindings[head] + (dot + tail if dot else '')
                        break
            else:
                break
        return raw

    edges, resource = {}, set()
    for key, (module, function) in definitions.items():
        body = [child for stmt in function.body for child in ast.walk(stmt)]
        aliases = {**imports[module], **_imports(body)}
        calls = {resolve(_dotted(node.func), module, aliases)
                 for node in body if isinstance(node, ast.Call)}
        edges[key] = calls & definitions.keys()
        if calls & RESOURCE_CALLS:
            resource.add(key)
    while True:
        added = {key for key, calls in edges.items() if calls & resource} - resource
        if not added:
            return definitions, resource
        resource.update(added)


def _sources():
    return {'tests.rust.' + path.stem: ast.parse(path.read_text(), filename=str(path))
            for path in Path(__file__).parent.glob('*.py')}


def _groups(function):
    result = []
    for decorator in function.decorator_list:
        if (isinstance(decorator, ast.Call) and
                _dotted(decorator.func) == 'pytest.mark.xdist_group'):
            value = next((kw.value for kw in decorator.keywords if kw.arg == 'name'),
                         decorator.args[0] if decorator.args else None)
            result.append(ast.literal_eval(value))
    return result


def test_actual_tool_and_native_callers_share_one_group_including_delegates():
    definitions, resource = _resource_callers(_sources())
    test_callers = {key for key in resource
                    if definitions[key][1].name.startswith('test_')}
    assert test_callers  # An accidentally empty source inventory cannot pass.
    missing = {key: _groups(definitions[key][1]) for key in sorted(test_callers)
               if _groups(definitions[key][1]) != [GROUP]}
    # Extra groups are not equivalent: loadgroup combines multiple group names.
    assert not missing, missing


def test_negative_metadata_and_conditional_certificate_delegations_are_covered():
    _, resource = _resource_callers(_sources())
    required = {
        'test_rust_supertraits.test_genuine_rustc_rejects_the_same_invalid_obligations',
        'test_rust_trait_where_contracts.test_actual_rustc_rejects_exact_where_contract_negatives',
        'test_rust_trait_where_contracts.test_actual_same_input_check_precedes_owned_where_lowering',
        'test_rust_certified_references.test_borrow_invalid_original_is_rejected_in_external_phase',
        'test_rust_patterns.test_original_match_fixture_full_width_owned_execution',
        'test_rust_shared_loans.test_original_shared_loan_program_arm64_macho_cross_only',
        'test_rust_method_reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution',
        'test_rust_core_attributes.test_actual_rustc_rejects_ordinary_stability_and_literal_suffix_controls',
    }
    assert {'tests.rust.' + key for key in required} <= resource


def test_call_audit_follows_aliases_without_matching_reference_like_names():
    trees = {
        'example.adapter': ast.parse('''
from pcc.frontends.rust import compile_rust_checked as check
def rejected_original():
    return check(original)
'''),
        'example.export': ast.parse('''
from example.adapter import rejected_original as indirect
'''),
        'example.cases': ast.parse('''
from example import export as wrapper
import subprocess as process
def test_indirect():
    return wrapper.indirect()
def test_nested():
    def callback():
        process.run(command)
    return callback()
def test_reference_rustc_native_in_name_only():
    return "subprocess.run(command)"
def cycle_one():
    cycle_two()
def cycle_two():
    cycle_one()
'''),
    }
    _, resource = _resource_callers(trees)
    assert resource == {'example.adapter.rejected_original',
                        'example.cases.test_indirect', 'example.cases.test_nested'}


def test_repository_retains_six_loadgroup_workers():
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / 'pyproject.toml').read_text())
    assert config['tool']['pytest']['ini_options']['addopts'] == (
        "-n 6 --dist=loadgroup -m 'not integration'")
