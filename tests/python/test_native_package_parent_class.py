"""Package imports preserve parent class exports during declaration.

Python Language Reference 7.11 (import bindings), 5.4.2 (submodules), and
8.8 (class definitions). Importing a child loads it while an unaliased
import still binds the parent package; an explicit alias binds the child.

https://docs.python.org/3.15/reference/simple_stmts.html#import
https://docs.python.org/3.15/reference/import.html#submodules
"""

from pathlib import Path
import os
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.pipeline_dependency_closure import (
    _collect_multi_source_relative_closure,
    _collect_relative_module_closure,
)
from pcc.frontends.python.py_ast import Name
from pcc.frontends.python.type_infer import infer_module


PARENT_SOURCE = """class Base:
    def answer(self):
        return 17
"""
CHILD_SOURCE = """class Base:
    def answer(self):
        return 29
"""
IMPORT_CASES = (
    ("import class_package\nimport class_package.child\n", "class_package.Base", 17),
    ("import class_package.child\n", "class_package.Base", 17),
    ("import class_package.child\nimport class_package\n", "class_package.Base", 17),
    ("import class_package\nfrom class_package import child\n", "class_package.Base", 17),
    ("import class_package.child as child\n", "child.Base", 29),
    ("import class_package.child as class_package\n", "class_package.Base", 29),
    ("import class_package.child\n", "class_package.child.Base", 29),
)


def package_sources(root: Path, imports: str, base: str):
    package = root / "class_package"
    package.mkdir()
    parent = package / "__init__.py"
    child = package / "child.py"
    consumer = root / "consumer.py"
    parent.write_text(PARENT_SOURCE, encoding="utf-8")
    child.write_text(CHILD_SOURCE, encoding="utf-8")
    consumer.write_text(
        imports + "class Derived(" + base + "):\n    pass\n"
        "def main():\n    print(Derived().answer())\nmain()\n",
        encoding="utf-8",
    )
    return [str(parent), str(child), str(consumer)]


@pytest.mark.parametrize("statement", (
    "import class_package.child\n",
    "import class_package.child as child\n",
    "from class_package.child import Base\n",
))
def test_leaf_discovery_includes_its_regular_parent_package(tmp_path, statement):
    sources = package_sources(tmp_path, statement, "class_package.Base")
    paths, names = _collect_relative_module_closure(sources[-1])
    assert names.index("class_package") < names.index("class_package.child")
    assert paths[names.index("class_package")] == sources[0]
    expanded_paths, expanded_names = _collect_multi_source_relative_closure(
        [sources[-1]], ["consumer"],
    )
    assert expanded_names.index("class_package") < expanded_names.index("class_package.child")
    assert expanded_paths[expanded_names.index("class_package")] == sources[0]


def test_leaf_discovery_keeps_namespace_ancestors_without_fake_initializers(tmp_path):
    from pcc.frontends.python.pipeline_dependency_closure import _top_level_import_targets

    leaf = tmp_path / "namespace_package" / "regular"
    leaf.mkdir(parents=True)
    (leaf / "__init__.py").write_text("VALUE = 17\n", encoding="utf-8")
    (leaf / "child.py").write_text("VALUE = 29\n", encoding="utf-8")
    targets = _top_level_import_targets(
        str(tmp_path), "import namespace_package.regular.child\n",
        top_level_only=False,
    )
    assert [name for _path, name in targets] == [
        "namespace_package.regular", "namespace_package.regular.child",
    ]


@pytest.mark.parametrize("imports,base,expected", IMPORT_CASES)
def test_child_import_preserves_the_class_binding_during_declaration(
    tmp_path, imports, base, expected,
):
    sources = package_sources(tmp_path, imports, base)
    names = ["class_package", "class_package.child", "consumer"]
    modules, exports, derived = build_closed_world_context(sources, names)
    typed = infer_module(
        modules[-1], external_exports=exports, derived_class_map=derived,
    )
    codegen = L1CodeGen(
        typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on",
    )
    codegen._native_module_exports = exports
    codegen._sibling_module_inits = tuple(names[:-1])
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    codegen.generate(typed)
    parent = codegen.class_lowering.classes["Derived"].bases_ast[0]
    owner = "class_package" if expected == 17 else "class_package.child"
    assert parent.ident == owner + ".Base"


@pytest.mark.parametrize("alias,expected_owner", (
    ("", "class_package"),
    (" as class_package", "class_package.child"),
))
def test_nested_import_prescan_preserves_the_import_binding(
    tmp_path, alias, expected_owner,
):
    sources = package_sources(tmp_path, "", "class_package.Base")
    Path(sources[-1]).write_text(
        "if True:\n    import class_package.child" + alias + "\n",
        encoding="utf-8",
    )
    names = ["class_package", "class_package.child", "consumer"]
    modules, exports, derived = build_closed_world_context(sources, names)
    typed = infer_module(
        modules[-1], external_exports=exports, derived_class_map=derived,
    )
    codegen = L1CodeGen(typed, ir_scaffold_mode="on")
    codegen._native_module_exports = exports
    codegen._sibling_module_inits = tuple(names[:-1])
    codegen._prescan_nested_imports(typed.body[0])
    module, exported = codegen._native_module_expr_export_info(
        Name(None, None, "class_package"), "Base", for_guard=True,
    )
    assert module == expected_owner
    assert exported["kind"] == "class"


@pytest.mark.integration
@pytest.mark.parametrize("imports,base,expected", IMPORT_CASES)
def test_package_parent_class_execution_matches_cpython_on_five_collectors(
    tmp_path, imports, base, expected, pcc_runtime_archive, python_program_compiler,
):
    sources = package_sources(tmp_path, imports, base)
    reference = subprocess.run(
        [sys.executable, "-E", "-s", sources[-1]],
        capture_output=True, text=True, timeout=20,
    )
    assert (reference.returncode, reference.stdout, reference.stderr) == (
        0, str(expected) + "\n", "",
    )
    binary = tmp_path / "package_parent_class"
    python_program_compiler(
        sources[-1], str(binary), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        actual = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (actual.returncode, actual.stdout, actual.stderr) == (
            reference.returncode, reference.stdout, reference.stderr,
        ), backend
