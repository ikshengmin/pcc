"""Small host-IR checks for the terminal method-dispatch boundary.

These checks do not build or execute a native compiler or runtime.
"""

from pathlib import Path
import re

import pytest

from pcc.backend.self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect as arm64_indirect,
)
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_x86_64_linux import (
    _aggregate_returned_indirect as x86_indirect,
)
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


CASES = (
    ("int-bytes", 'def run(value: int):\n    return value.to_bytes(2, "little", signed=True)\n',
     ("py_int_to_bytes_args", "pcc_gc_foreign_lease_acquire")),
    ("int-binding-error", 'def run(value: int):\n    return value.to_bytes(1, length=1)\n',
     ("py_raise",)),
    ("dynamic-clear", 'def run(value):\n    value.clear()\n    return value\n',
     ("py_obj_clear",)),
    ("dynamic-integer", 'def run(value):\n    return value.bit_length()\n',
     ("py_int_bit_length",)),
    ("dynamic-method", 'def run(value, arg):\n    return value.unknown(arg)\n',
     ("py_obj_load_method", "py_obj_call_method")),
    ("dynamic-keyword", 'def run(value, arg):\n    return value.unknown(named=arg)\n',
     ("py_obj_getattr", "py_obj_call")),
    ("bytearray-clear", 'def run(value: bytearray):\n    value.clear()\n    return value\n',
     ("py_bytearray_clear",)),
    ("bytes-count", 'def run(value: bytes):\n    return value.count(b"x")\n',
     ("py_bytes_count",)),
)


def _lower(source, triple):
    module = infer_module(parse_and_lift(source, "method_dispatch.py", "method_dispatch"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._target_triple = triple
    codegen._skip_program_main = True
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    return str(codegen.generate(module))


@pytest.mark.parametrize("triple, indirect", [
    ("x86_64-unknown-linux-gnu", x86_indirect),
    ("arm64-apple-darwin", arm64_indirect),
], ids=["x86_64", "arm64-darwin"])
@pytest.mark.parametrize("case, source, helpers", CASES, ids=[case[0] for case in CASES])
def test_terminal_method_dispatch_keeps_native_protocol(
    case, source, helpers, triple, indirect, tmp_path,
):
    text = _lower(source, triple)
    (tmp_path / "method_dispatch.ll").write_text(text, encoding="utf-8")
    body = re.search(r"^define[^\n]*@user_method_dispatch_run\([^\n]*\{\n(.*?)^\}",
                     text, re.M | re.S)
    assert body is not None, case
    assert "strict.nolib.stub" not in body[1]
    assert not re.search(r"\bcall\b[^\n]*@py_cpy_", body[1])
    for helper in helpers:
        emitted = re.findall(r"\bcall\b[^\n]*@([\w.]+)\(", body[1])
        assert helper in emitted, (case, helper, emitted)
    prepared = prepare_module_for_target(
        'target triple = "' + triple + '"\n' + text,
        aggregate_returned_indirect=indirect,
    )
    assert prepared.triple == triple


def test_terminal_fallback_reuses_the_original_attribute(monkeypatch):
    from pcc.frontends.python.codegen.method_call_fallback_lowering import (
        MethodCallFallbackLoweringMixin,
    )

    original = MethodCallFallbackLoweringMixin._emit_method_call_fallback
    assert L1CodeGen._emit_method_call_fallback is original
    calls = []

    def tracked(self, expr, attr):
        assert attr is expr.func
        calls.append(attr.name)
        return original(self, expr, attr)

    monkeypatch.setattr(MethodCallFallbackLoweringMixin, "_emit_method_call_fallback", tracked)
    text = _lower('def run(value: int):\n    return value.to_bytes()\n',
                  "x86_64-unknown-linux-gnu")
    assert calls == ["to_bytes"]
    assert "@py_int_to_bytes_args(" in text


def test_earlier_container_dispatch_does_not_enter_terminal_fallback(monkeypatch):
    from pcc.frontends.python.codegen.method_call_fallback_lowering import (
        MethodCallFallbackLoweringMixin,
    )

    def unexpected(self, expr, attr):
        raise AssertionError("early method reached terminal dispatch: " + attr.name)

    monkeypatch.setattr(MethodCallFallbackLoweringMixin, "_emit_method_call_fallback", unexpected)
    text = _lower('def run():\n    values = [1]\n    values.append(2)\n    return values\n',
                  "x86_64-unknown-linux-gnu")
    assert "strict.nolib.stub" not in text
    assert "@py_list_append" in text


def test_terminal_fallback_is_in_the_closed_world_host_schema():
    from pcc.frontends.python.codegen.host_contract import (
        PROBE_POLICY_CONTEXTUAL_MIXIN,
        per_module_probe_policy,
    )
    from pcc.frontends.python.pipeline_context import build_closed_world_context

    root = Path(__file__).resolve().parents[2]
    modules = [
        "pcc.frontends.python.codegen." + name
        for name in ("layer1", "layer1_mixins", "method_call_fallback_lowering")
    ]
    paths = [str(root / (name.replace(".", "/") + ".py")) for name in modules]
    _parsed, exports, _derived = build_closed_world_context(paths, modules)
    assert per_module_probe_policy(modules[-1]) == PROBE_POLICY_CONTEXTUAL_MIXIN
    for module_name, class_name in (
        (modules[-1], "MethodCallFallbackLoweringMixin"),
        (modules[1], "L1CodeGenMixinStack"),
        (modules[0], "L1CodeGen"),
    ):
        methods = exports[module_name][class_name]["methods"]
        matching = [method for method in methods
                    if method["name"] == "_emit_method_call_fallback"]
        assert len(matching) == 1, (module_name, class_name)
        assert [arg["name"] for arg in matching[0]["call_sig"]] == ["self", "expr", "attr"]
