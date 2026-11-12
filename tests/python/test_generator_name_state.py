"""Constructor-owned generator classification across codegen mixins.

Language reference: execution model / binding of names; simple statements /
import; expressions / yield expressions. Native programs are compared with
CPython, including function-local imports that shadow generator names.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.verify_nolibpython import linked_libraries


pytestmark = pytest.mark.xdist_group(name="generator_name_state")
CODEGEN = Path(__file__).resolve().parents[2] / "pcc/frontends/python/codegen"


def _codegen(source, name="generator_state"):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(parse_and_lift(source, "<generator-state>", name))
    return L1CodeGen(module, emit_cpy_main_exitcode=False)


def test_generator_names_belong_to_each_constructed_host():
    from pcc.frontends.python.codegen.host_contract import L1_CODEGEN_HOST_ATTRS

    first = _codegen("pass\n")
    second = _codegen("pass\n")
    assert "_generator_func_names" in L1_CODEGEN_HOST_ATTRS
    assert first._generator_func_names == set()
    assert second._generator_func_names == set()
    assert first._generator_func_names is not second._generator_func_names
    first._generator_func_names.add("only_first")
    assert second._generator_func_names == set()


def test_module_override_resets_generator_classification():
    codegen = _codegen("def values():\n    yield 1\n")
    codegen.generate(codegen.ast_module)
    assert "values" in codegen._generator_func_names
    replacement = _codegen("def ordinary():\n    return 2\n", "replacement")
    codegen.generate(replacement.ast_module)
    assert codegen._generator_func_names == set()


def test_generator_names_have_no_defensive_default_or_lazy_initialization():
    sites = []
    for path in sorted(CODEGEN.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "hasattr")
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "_generator_func_names"
            ):
                sites.append(f"{path.name}:{node.lineno}: {node.func.id}")
    assert not sites, "Use the constructor-owned generator set:\n" + "\n".join(sites)


PROVIDER = """\
def sequence():
    return [30, 31]


def ordinary():
    yield 40
    yield 41
"""

PROGRAM = """\
def sequence():
    yield 1
    yield 2


def ordinary():
    return [3, 4]


def imported_sequence():
    from provider import sequence
    return list(sequence())


def imported_ordinary():
    from provider import ordinary
    return list(ordinary())


def main():
    print(list(sequence()))
    print(imported_sequence())
    print(list(sequence()))
    print(ordinary())
    print(imported_ordinary())
    print(ordinary())

main()
"""

GC_REPORT = """\
from pcc.extern import c_int64, extern
observed_gc_backend = extern("pcc_gc_backend", (), c_int64)
print("GC", observed_gc_backend())
"""


@pytest.mark.integration
@pytest.mark.parametrize("scaffold", ["off", "on"])
def test_local_imports_restore_generator_classification_in_native_program(
    tmp_path, pcc_runtime_archive, scaffold,
):
    (tmp_path / "provider.py").write_text(PROVIDER)
    source = tmp_path / "program.py"
    source.write_text(PROGRAM)
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stdout + reference.stderr
    assert reference.stdout == "[1, 2]\n[30, 31]\n[1, 2]\n[3, 4]\n[40, 41]\n[3, 4]\n"
    source.write_text(PROGRAM + GC_REPORT)
    binary = tmp_path / "program"
    environment = dict(
        os.environ,
        PCC_NO_AUTO_PCC1="1",
        PCC_RUNTIME_ARCHIVE=str(pcc_runtime_archive),
    )
    # The installed launcher records and execs PCC_TEST_COMPILER when selected;
    # python -m pcc intentionally enters the host dispatcher directly.
    compiler_module = (
        "pcc.driver.cli_launcher" if os.environ.get("PCC_TEST_COMPILER") else "pcc"
    )
    compiled = subprocess.run(
        [sys.executable, "-m", compiler_module, "--backend=self",
         "--ir-scaffold=" + scaffold, str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=90, env=environment,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    libraries = linked_libraries(binary.read_bytes())
    assert libraries
    assert not any("libpython" in name.lower() or "llvm" in name.lower() for name in libraries)
    for collector in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(environment, PCC_GC_BACKEND=str(collector)),
        )
        detail = f"scaffold={scaffold}, GC{collector}: " + result.stdout + result.stderr
        assert result.returncode == 0, detail
        assert result.stderr == "", detail
        assert result.stdout == reference.stdout + f"GC {collector}\n", detail
