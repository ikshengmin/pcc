"""Python reference 6.3.2/6.7/6.10: subscription, bitwise ops and comparisons.

Container int annotations do not bound their values, even in the scaffold
projection used to build pcc1. Keep the original Windows find gate as the
integration qualification of the compiler that exposed this shape.
"""
from __future__ import annotations

import re

import pytest


PROGRAM = '''from pcc.extern import c_abi_typed_export

def overlap(left: dict[int, int], right: dict[str, int]) -> bool:
    return not left[0] & right["r"]

def sequence_checks(items: list[int], pair: tuple[int, int]) -> bool:
    copied = items[0]
    return (copied == pair[0] and items[0] & pair[1] == 0
            and ~items[0] == -pair[0] - 1)

def main() -> None:
    for bit in (0, 62, 63, 64, 127, 4095):
        high = 1 << bit
        left: dict[int, int] = {0: high}
        same: dict[str, int] = {"r": high}
        other: dict[str, int] = {"r": 1 << (bit + 1)}
        assert not overlap(left, same)
        assert overlap(left, other)
        assert sequence_checks([high], (high, 1 << (bit + 1)))
        left[0] = -high
        assert not overlap(left, same)
    print("CONTAINER_BITSETS_OK")

main()
'''


def _mark_standalone_project(directory):
    # This standalone program imports a compiler-consumed declaration to
    # select raw-int scaffold semantics. It is not part of the compiler's own
    # Python package. CI puts --basetemp under the compiler checkout, so give
    # the program the same project boundary it has outside that checkout.
    (directory / "pyproject.toml").write_text(
        '[project]\nname = "container-bitsets"\nversion = "0"\n', encoding="utf-8",
    )


@pytest.fixture
def bitsets_project(tmp_path):
    _mark_standalone_project(tmp_path)
    return tmp_path


def test_bitsets_project_does_not_adopt_an_enclosing_checkout(
    bitsets_project, monkeypatch,
):
    from pcc.frontends.python.pipeline_dependency_closure import (
        _collect_relative_module_closure,
    )

    # Model the hosted --basetemp placement without symlinks or relying on
    # this test's own location. The checkout package is unrelated to the
    # standalone project; sibling files and installed dependencies are not.
    checkout = bitsets_project / "checkout"
    extern = checkout / "pcc" / "extern"
    extern.mkdir(parents=True)
    (extern.parent / "__init__.py").write_text("", encoding="utf-8")
    (extern / "__init__.py").write_text("", encoding="utf-8")
    nested = checkout / "build" / "tests" / "case"
    nested.mkdir(parents=True)
    site = bitsets_project / "package-site"
    site.mkdir()
    (site / "installed_dep.py").write_text("value = 2\n", encoding="utf-8")
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(site))
    monkeypatch.setenv("PCC_ENVIRONMENT", str(bitsets_project / "empty-environment"))

    def modules(root, imports=""):
        entry = root / "program.py"
        entry.write_text(imports + PROGRAM, encoding="utf-8")
        return _collect_relative_module_closure(str(entry))[1]

    # An unmarked nested entry reaches the enclosing compiler package.
    assert "pcc.extern" in modules(nested)
    _mark_standalone_project(nested)
    assert modules(nested) == modules(bitsets_project) == ["program"]
    imports = "from sibling_dep import value\nfrom installed_dep import value as other\n"
    for project in (nested, bitsets_project):
        (project / "sibling_dep.py").write_text("value = 1\n", encoding="utf-8")
        assert set(modules(project, imports)) == {
            "program", "sibling_dep", "installed_dep",
        }


def test_container_bitsets_scaffold_ir_keeps_object_operands(bitsets_project):
    from pcc.frontends.python.pipeline import compile_python

    source = bitsets_project / "container_bitsets.py"
    output = bitsets_project / "container_bitsets.ll"
    source.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(source), str(output), emit_llvm_only=True)
    text = output.read_text(encoding="utf-8")
    match = re.search(
        r"define[^\n]*@[^\n(]*overlap\([^\n]*\).*?\{(.*?)\n\}",
        text, re.S,
    )
    assert match, text
    body = match.group(1)
    assert "@py_int_and" in body, body
    assert "@py_int_to_i64" not in body, body
    assert "@py_obj_to_i64" not in body, body


@pytest.fixture
def bitsets_owned_runtime(request, monkeypatch):
    from tests.python.owned_regression_support import explicit_owned_runtime

    return explicit_owned_runtime.__wrapped__(request, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_container_bitsets_owned_native(
    python_program_compiler, request, bitsets_owned_runtime, bitsets_project, capfd,
):
    from tests.python.owned_regression_support import assert_owned_program

    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM, "CONTAINER_BITSETS_OK\n", bitsets_project,
        python_program_compiler, mode, bitsets_owned_runtime, capfd, backends=None,
    )
