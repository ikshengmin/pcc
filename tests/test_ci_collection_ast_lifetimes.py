"""Exercise source-model initialization without retaining discarded source ASTs."""

from __future__ import annotations

import ast
import gc
from pathlib import Path
from types import CodeType, SimpleNamespace
import types
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _initialize(stem, first_name, runtime, source_override=None, helpers=()):
    """Run the unchanged fixture prelude, without importing compiler owners."""
    path = ROOT / "tests/python" / (stem + ".py")
    fixture = ast.parse(path.read_text())
    start = next(index for index, node in enumerate(fixture.body)
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == first_name
                         for target in node.targets))
    stop = next(index for index in range(start + 1, len(fixture.body))
                if isinstance(fixture.body[index], (ast.FunctionDef, ast.ClassDef)))
    statements = fixture.body[start:stop]
    statements += [node for node in fixture.body
                   if isinstance(node, ast.FunctionDef) and node.name in helpers]
    source = (ROOT / runtime).read_text() if source_override is None else source_override
    references = []
    calls = []

    def parse(text, *args, **kwargs):
        assert text == source
        tree = ast.parse(text, *args, **kwargs)
        references.extend(weakref.ref(node) for node in tree.body)
        calls.append(True)
        return tree

    namespace = {
        "ROOT": ROOT,
        "STUBS": SimpleNamespace(read_text=lambda: source),
        "ast": SimpleNamespace(**{**vars(ast), "parse": parse}),
        "types": types,
    }
    # Encoding and lock preludes read through ROOT. The repr prelude reads
    # through STUBS, allowing missing/duplicate definitions to be exercised.
    code = compile(ast.Module(body=statements, type_ignores=[]), str(path), "exec")
    exec(code, namespace)
    assert calls == [True]
    return namespace, references


@pytest.mark.parametrize("stem,first,runtime,output,filename,function", (
    ("test_str_encode_binding", "_RUNTIME_TREE", "pcc/runtime/py/py_str_accessors.py",
     "_RUNTIME_FUNCTIONS", "py_str_accessors.py", None),
    ("test_pcc_mutex_lock_spin_policy", "_KERNEL", "pcc/runtime/py/freestanding_thread_kernel_pthread.py",
     "_LOCK_CODE", "freestanding_thread_kernel_pthread.py", "pcc_mutex_lock"),
))
def test_compiled_source_models_release_their_input_ast(
    stem, first, runtime, output, filename, function,
):
    namespace, references = _initialize(stem, first, runtime)
    gc.collect()
    assert first not in namespace
    assert references and all(reference() is None for reference in references)
    actual = namespace[output]
    assert isinstance(actual, CodeType)

    # Compare to the original full-source extraction, not a nearby fixture.
    tree = ast.parse((ROOT / runtime).read_text())
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and (function is None or node.name == function)]
    expected = compile(tree, filename, "exec")
    if function is not None:
        expected = next(value for value in expected.co_consts
                        if isinstance(value, CodeType) and value.co_name == function)
    assert actual == expected


def test_repr_model_keeps_only_its_original_source_statements():
    runtime = "pcc/runtime/py/py_obj_stubs.py"
    namespace, references = _initialize("test_str_repr_nonprintable", "_TREE", runtime)
    gc.collect()
    retained = namespace["_TREE"].body
    assert len(retained) == 2
    assert {id(reference()) for reference in references if reference() is not None} == {
        id(node) for node in retained
    }
    tree = ast.parse((ROOT / runtime).read_text())
    table = next(node for node in tree.body
                 if isinstance(getattr(node, "value", None), ast.Call)
                 and getattr(node.value.func, "id", "") == "define_global_i32_array"
                 and node.value.args
                 and getattr(node.value.args[0], "value", None) == "pcc_unicode_nonprintable_ranges")
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_repr_codepoint_printable")
    expected = [node for node in tree.body if node is table or node is function]
    assert [ast.dump(node, include_attributes=True) for node in retained] == [
        ast.dump(node, include_attributes=True) for node in expected
    ]


def test_repr_model_preserves_first_matches_and_deferred_missing_errors():
    source = '''
unrelated = [1, 2, 3]
define_global_i32_array("pcc_unicode_nonprintable_ranges", 0, 1)
def _repr_codepoint_printable(code):
    return 1
define_global_i32_array("pcc_unicode_nonprintable_ranges", 99, 100)
def _repr_codepoint_printable(code):
    return 2
'''
    namespace, _ = _initialize("test_str_repr_nonprintable", "_TREE", "unused",
                               source, ("_table", "_lookup"))
    assert len(namespace["_TREE"].body) == 4
    assert namespace["_table"]() == [0, 1]
    lookup, _ = namespace["_lookup"]()
    assert lookup(0) == 1

    namespace, _ = _initialize("test_str_repr_nonprintable", "_TREE", "unused",
                               "unrelated = 1\n", ("_table", "_lookup"))
    assert namespace["_TREE"].body == []
    with pytest.raises(AssertionError, match="range table not found"):
        namespace["_table"]()
    with pytest.raises(StopIteration):
        namespace["_lookup"]()
