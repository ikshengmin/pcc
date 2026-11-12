"""Constructor-owned L1CodeGen state is read directly, never through defaults.

Every attribute below is created by ``Layer1InitMixin._init_l1_state`` for
each code generator. Readers used to reach it through
``getattr(self, name, <default>)`` with defaults that disagreed (``None``,
``()``, ``set()``, ``{}``), and some writers lazily created it again. A
reader that chose ``None`` took a different branch from one that chose an
empty container, so the same missing state could mean two things.

The CPython comparison for programs that exercise this state is the
``constructor_owned_codegen_state`` case in
``test_python_cpython_alignment.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "pcc" / "frontends" / "python"

CONSTRUCTOR_OWNED = {
    "_borrowed_gc_rooted_local_names": set,
    "_codegen_trace_capacity": int,
    "_codegen_trace_ring": list,
    "_cpy_env_flags": dict,
    "_cpy_values": set,
    "_exact_int_env_flags": dict,
    "_except_binding_names": set,
    "_finally_stack": list,
    "_native_module_attr_globals": dict,
    "_native_re_compile_aliases": dict,
    "_native_re_compile_local_aliases": dict,
    "_native_re_static_flag_aliases": dict,
    "_owned_local_flag_allocas": dict,
    "_virtual_literal_dict_expr_bindings": set,
}


def _codegen(source: str, name: str = "constructor_owned"):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(parse_and_lift(source, "<constructor-owned>", name))
    return L1CodeGen(module, emit_cpy_main_exitcode=False)


def test_the_constructor_creates_each_attribute_per_instance():
    from pcc.frontends.python.codegen.host_contract import L1_CODEGEN_HOST_ATTRS

    first = _codegen("pass\n")
    second = _codegen("pass\n")
    for name, kind in sorted(CONSTRUCTOR_OWNED.items()):
        assert name in L1_CODEGEN_HOST_ATTRS, name
        assert type(getattr(first, name)) is kind, name
        if kind is not int:
            assert getattr(first, name) is not getattr(second, name), name


def test_generating_a_module_leaves_the_state_present():
    codegen = _codegen(
        "import re\n"
        "PATTERN = re.compile('a+')\n"
        "def f(n):\n"
        "    out = [i for i in range(n)]\n"
        "    try:\n"
        "        return out\n"
        "    except ValueError as exc:\n"
        "        return str(exc)\n"
        "    finally:\n"
        "        pass\n"
    )
    codegen.generate(codegen.ast_module)
    for name, kind in sorted(CONSTRUCTOR_OWNED.items()):
        assert type(getattr(codegen, name)) is kind, name


def _defensive_sites():
    sites = []
    for path in sorted(FRONTEND.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                # setattr(self, "<name>", value) with a constant name is a
                # plain write (the module reset is written that way).
                and node.func.id in ("getattr", "hasattr", "delattr")
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in CONSTRUCTOR_OWNED
            ):
                rel = path.relative_to(ROOT)
                sites.append(f"{rel}:{node.lineno} {node.func.id}({node.args[1].value!r})")
    return sites


def test_no_reader_invents_a_default_or_initializes_lazily():
    assert _defensive_sites() == []


def test_the_scan_sees_the_frontend():
    """A scan over an empty tree would pass for the wrong reason."""
    files = list(FRONTEND.rglob("*.py"))
    assert len(files) > 150
    reads = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        reads += text.count("self._cpy_values")
    assert reads > 100


@pytest.mark.parametrize("name", sorted(CONSTRUCTOR_OWNED))
def test_every_attribute_is_read_somewhere(name):
    """Each listed name is live state, so dropping it from the list is a choice."""
    hits = 0
    for path in FRONTEND.rglob("*.py"):
        hits += path.read_text(encoding="utf-8").count("." + name)
    assert hits > 1, name
