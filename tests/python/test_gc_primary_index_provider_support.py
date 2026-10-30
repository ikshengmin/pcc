"""Independent provider/source ABI checks; never execute a GC probe or collector."""

import ast
from pathlib import Path
import shutil

import pytest

from tests.python.gc_primary_index_provider_support import (
    CONSTANTS,
    FUNCTIONS,
    STORAGE,
    canonical_provider_source,
    emit_provider_object,
)

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime"


def test_provider_uses_exact_canonical_functions_constants_and_storage():
    source, receipt = canonical_provider_source(RUNTIME)
    selected = ast.parse(source)
    tracking = ast.parse((RUNTIME / "py/freestanding_gc_tracking.py").read_text())
    state = ast.parse((RUNTIME / "py/freestanding_gc_state.py").read_text())
    functions = {node.name: node for node in tracking.body if isinstance(node, ast.FunctionDef)}
    actual_functions = [node for node in selected.body if isinstance(node, ast.FunctionDef)]
    assert [node.name for node in actual_functions] == list(FUNCTIONS)
    assert [ast.dump(node) for node in actual_functions] == [ast.dump(functions[name]) for name in FUNCTIONS]
    constants = {node.target.id: node for node in tracking.body
                 if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    assert [ast.dump(node) for node in selected.body if isinstance(node, ast.AnnAssign)] == [
        ast.dump(constants[name]) for name in CONSTANTS
    ]
    def storage_nodes(tree):
        return [node for node in tree.body if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "define_global_i64_array"
                and node.value.args[0].value == STORAGE]
    assert [ast.dump(node) for node in storage_nodes(selected)] == [ast.dump(node) for node in storage_nodes(state)]
    assert receipt["storage_bytes"] == 4224
    assert (receipt["shard_count"], receipt["shard_stride"], receipt["primary_offset"]) == (32, 128, 32)
    assert receipt["storage_bytes"] == (receipt["shard_count"] + 1) * receipt["shard_stride"]


@pytest.mark.parametrize("name", FUNCTIONS)
def test_provider_rejects_new_unselected_function_dependencies(tmp_path, name):
    py = tmp_path / "py"
    py.mkdir()
    tracking = (RUNTIME / "py/freestanding_gc_tracking.py").read_text()
    tree = ast.parse(tracking)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    lines = tracking.splitlines(keepends=True)
    lines.insert(function.body[0].lineno - 1, "    unselected_provider_dependency()\n")
    (py / "freestanding_gc_tracking.py").write_text("".join(lines))
    shutil.copyfile(RUNTIME / "py/freestanding_gc_state.py", py / "freestanding_gc_state.py")
    with pytest.raises(ValueError, match="dependencies changed.*unselected_provider_dependency"):
        canonical_provider_source(tmp_path)


def test_provider_rejects_nonzero_canonical_storage(tmp_path):
    py = tmp_path / "py"
    py.mkdir()
    state = (RUNTIME / "py/freestanding_gc_state.py").read_text()
    tree = ast.parse(state)
    storage = next(node for node in tree.body if isinstance(node, ast.Expr)
                   and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == "define_global_i64_array"
                   and node.value.args[0].value == STORAGE)
    storage.value.args[1] = ast.Constant(1)
    (py / "freestanding_gc_state.py").write_text(ast.unparse(tree))
    shutil.copyfile(RUNTIME / "py/freestanding_gc_tracking.py", py / "freestanding_gc_tracking.py")
    with pytest.raises(ValueError, match="storage/layout contract changed"):
        canonical_provider_source(tmp_path)


def test_owned_provider_object_has_exact_independent_abi_closure(tmp_path):
    # Cross-emission is host-only. No linker, generated executable, runtime
    # archive, GC copy/index mutation, thread, reentry or collector is invoked.
    from pcc.backend import elf_x86_64 as elf

    path = emit_provider_object(RUNTIME, tmp_path, "x86_64-unknown-linux-gnu")
    symbols = elf.parse_relocatable(path.read_bytes()).symbols[1:]
    assert {symbol.name for symbol in symbols if symbol.section_index == elf.SHN_UNDEF} == set()
    assert {symbol.name for symbol in symbols
            if symbol.binding != elf.STB_LOCAL and symbol.section_index != elf.SHN_UNDEF} == {
        "pcc_gc_tracked_shard_address", "pcc_gc_tracked_shard_index",
        "pcc_gc_tracked_primary_index_cells", "pcc_gc_tracked_shards",
    }
    storage = next(symbol for symbol in symbols if symbol.name == "pcc_gc_tracked_shards")
    assert storage.size == 4224
    ir = (tmp_path / "primary-index-provider.ll").read_text()
    assert "define external ptr @pcc_gc_tracked_primary_index_cells(ptr " in ir


def test_remembered_membership_fixture_links_canonical_provider_object():
    # Inspect only source wiring. Do not import, compile, link or execute the
    # remembered-membership C harness or its index operations.
    path = RUNTIME.parents[1] / "tests/python/test_gc_remembered_membership_native.py"
    tree = ast.parse(path.read_text())
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "tests.python.gc_primary_index_provider_support"]
    assert len(imports) == 1
    assert [(name.name, name.asname) for name in imports[0].names] == [
        ("emit_provider_object", None)
    ]
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_remembered_membership_real_self_emitted_index_object")
    assignments = {node.targets[0].id: node for node in function.body
                   if isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)}
    provider = assignments["provider"]
    expected = ast.parse(
        "emit_provider_object(source.parent.parent, tmp_path, host_target_triple())",
        mode="eval",
    ).body
    assert ast.dump(provider.value) == ast.dump(expected)
    built = assignments["built"]
    assert ast.unparse(built.value.func) == "subprocess.run"
    argv = built.value.args[0]
    assert isinstance(argv, ast.List)
    assert [ast.unparse(value) for value in argv.elts] == [
        "compiler", "'-std=c11'", "'-fno-builtin'", "str(assembly)",
        "str(provider)", "str(harness)", "'-o'", "str(binary)",
    ]
    assert provider.lineno < built.lineno
