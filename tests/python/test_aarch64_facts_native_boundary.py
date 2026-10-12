"""Real PCC pruning/typing source controls for normal CI; no native execution."""

import ast
import re
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / "pcc/backend/self_backend_aarch64_darwin_regalloc.py"


def test_current_facts_host_references_are_pruned_by_real_import_scanner():
    from pcc.frontends.python import pipeline_import_scan as scan
    source = SOURCE.read_text()
    native = scan._without_inactive_implementation_imports(source)
    host = scan._without_inactive_implementation_imports(source, implementation="cpython")
    assert "_HOST_FACTS_STRUCTURE_OBSERVER" not in native
    assert "_facts_observer" not in native and "_facts_sample" not in native
    assert "_HOST_FACTS_STRUCTURE_OBSERVER" in host
    assert "_facts_observer.row" in host
    assert "cProfile" not in native and "host_assembly_profile" not in native


def test_current_guarded_suites_type_and_prune_in_real_native_ir():
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    source = SOURCE.read_text()
    tree = ast.parse(source)
    facts = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_function_level_facts")
    # Use the exact current guarded suites, including the observed business
    # loop and dynamic host callbacks; do not replace them with a toy guard.
    guarded = []
    for node in facts.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "facts_observe" for target in node.targets)):
            guarded.append(node)
        elif (isinstance(node, ast.If)
                and ast.unparse(node.test) == "sys.implementation.name == 'cpython'"):
            guarded.append(ast.Import(names=[ast.alias(name="sys")]))
            guarded.append(node)
        elif isinstance(node, ast.For):
            guarded.extend(child for child in node.body if isinstance(child, ast.If)
                           and isinstance(child.test, ast.Name) and child.test.id == "facts_observe")
    probe = ast.parse("def native_probe():\n    return 17\n")
    probe.body[0].body = guarded + probe.body[0].body
    text = ast.unparse(ast.fix_missing_locations(probe))
    module = infer_module(parse_and_lift(text, "facts_native_boundary.py", "facts_native_boundary"))
    generator = L1CodeGen(module, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    generator._strict_no_libpython = True
    result = str(generator.generate())
    match = re.search(r"^define[^\n]*@user_facts_native_boundary_native_probe\([^\n]*\) \{", result, re.MULTILINE)
    assert match is not None
    body = result[match.start():].split("\n}\n", 1)[0]
    assert "_facts_observer" not in body
    assert "_HOST_FACTS_STRUCTURE_OBSERVER" not in body
    assert "@py_cpy_" not in body
    assert "inttoptr i64 35 to ptr" in body
