"""Helpers that several modules used to copy now have one owner each.

Each pair of copies had identical bodies that resolved the same globals, so
the copies could only drift apart. The callers now import the owner's
definition; these checks keep a copy from coming back.
"""

from __future__ import annotations

import pytest


def test_byte_data_emitter_is_shared_by_both_data_emitters():
    from pcc.backend import (
        self_backend_aarch64_darwin_data,
        self_backend_literals,
        self_backend_x86_64_linux_data,
    )

    owner = self_backend_literals.emit_byte_data
    assert self_backend_aarch64_darwin_data.emit_byte_data is owner
    assert self_backend_x86_64_linux_data.emit_byte_data is owner
    assert owner(b"") == []
    assert owner(bytes(range(17))) == [
        "  .byte " + ", ".join(str(i) for i in range(16)),
        "  .byte 16",
    ]


def test_hoist_cache_key_has_one_owner():
    from pcc.frontends.python.codegen import hoist_free_names, hoist_lowering

    assert hoist_lowering._hoist_cache_key4 is hoist_free_names._hoist_cache_key4


def test_metal_helpers_have_one_owner():
    from pcc.kernel_ir import metal_launch, metal_package, metal_source_runtime

    assert metal_source_runtime._c_identifier is metal_launch._c_identifier
    assert metal_source_runtime._sha256_file is metal_package._sha256_file
    assert metal_launch._c_identifier("9a-b.c") == "_9a_b_c"
    assert metal_launch._c_identifier("") == "pcc_metal_kernel"


def test_scalar_type_classifier_has_one_owner():
    from pcc.frontends.c.passes import ast_utils, ssa_adce, ssa_dse

    for owner in (ssa_adce._SSAADCETransformer, ssa_dse._SSADSERewriter):
        assert "_is_scalar_type_name" not in vars(owner)
    assert ast_utils.is_scalar_type_name("const unsigned int")
    for rejected in ("", "int *", "char [4]", "struct point", "union u", "enum e", "int (*)(void)"):
        assert not ast_utils.is_scalar_type_name(rejected), rejected


@pytest.mark.parametrize(
    "module,name",
    [
        ("pcc.frontends.c.passes.escape_analysis", "EscapeAnalysisPass"),
        ("pcc.frontends.c.passes.nsw_inference", "NSWInferencePass"),
        ("pcc.frontends.c.passes.propagation", "SROAPass"),
    ],
)
def test_function_analysis_passes_share_one_driver(module, name):
    import importlib

    from pcc.frontends.c.passes.base import FunctionAnalysisPass

    cls = getattr(importlib.import_module(module), name)
    assert issubclass(cls, FunctionAnalysisPass)
    assert "run" not in vars(cls)
    assert "_analyze_function" in vars(cls)


def test_function_analysis_driver_visits_each_definition_once():
    from pcc.frontends.c.parse.c_parse_driver import parse
    from pcc.frontends.c.passes.base import FunctionAnalysisPass
    from pcc.frontends.c.passes.context import PassContext

    seen = []

    class Recorder(FunctionAnalysisPass):
        name = "recorder"

        def _analyze_function(self, funcdef, ctx):
            seen.append(funcdef.decl.name)

    ast = parse("int g;\nint f(void) { return 1; }\nint h(int x);\nint k(int x) { return x; }\n")
    assert Recorder().run(ast, PassContext()) is None
    assert seen == ["f", "k"]
    assert Recorder().run(None, PassContext()) is None
