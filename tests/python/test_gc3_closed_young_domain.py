"""Only a complete, deliberately narrow executable proof enables young death.

These are source/IR admission tests, not native collector acceptance. In
particular, an explicit runtime archive must not make a native probe look like
an admitted compiler-produced workload.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from pcc.frontends.python.closed_young_domain import (
    closed_young_module_denial,
    closed_young_request_denial,
)
from pcc.frontends.python.py_lift import parse_and_lift


_ENABLE = "@pcc_gc_backend3_enable_closed_young_domain("
_PROGRAM = "def build(n):\n    return str(n) + 'x'\nprint(build(42))\n"


def _module(source, name="closed"):
    return parse_and_lift(source, name + ".py", name)


def _request(**changes):
    request = dict(
        source_count=1, emit_llvm_only=False, python_library=False,
        backend="self", gpu_backend="none", runtime_archive=None,
        object_inputs=(), link_args=(), environment={},
        target_triple="x86_64-unknown-linux-gnu",
    )
    request.update(changes)
    return closed_young_request_denial(**request)


def _enable_calls(text):
    return [line for line in str(text).splitlines() if "call " in line and _ENABLE in line]


@pytest.mark.parametrize("source", [
    _PROGRAM,
    "print(int('123'), float('2.5'), complex(1, 2), bytes([1, 2]))\n",
    "def outer(x):\n    def inner(y=3):\n        return x + y\n    return inner()\nprint(outer(2))\n",
    "class Box:\n    def __init__(self, value):\n        self.value = value\nprint(Box(1).value)\n",
    "f = lambda x: x + 1\nprint(f(2))\n",
    "values = []\nfor i in range(10):\n    values.append(str(i))\nprint(values[-1])\n",
    "if __name__ == '__main__':\n    print('pcc.unsafe is only text')\n",
])
def test_source_proof_accepts_closed_syntax(source):
    assert closed_young_module_denial(_module(source)) == ""


@pytest.mark.parametrize("source,reason", [
    ("import unknown\n", "dependency-closure"),
    ("from . import sibling\n", "dependency-closure"),
    ("def f():\n    from pcc.unsafe import store_ptr\n", "dependency-closure"),
    ("class C:\n    def f(self):\n        import pcc.extern as foreign\n", "dependency-closure"),
    ("if False:\n    import pcc.unsafe\n", "dependency-closure"),
    ("try:\n    pass\nfinally:\n    import hidden\n", "dependency-closure"),
    ("__pcc_runtime_port__ = False\n", "raw runtime marker"),
    ("__pcc_freestanding__ = True\n", "raw runtime marker"),
    ("@c_abi_export('main')\ndef entry():\n    return 0\n", "decorator"),
    ("async def f():\n    return 1\n", "asynchronous"),
    ("f = __import__\n", "dynamic or foreign"),
    ("f = globals\n", "dynamic or foreign"),
    ("eval('1')\n", "dynamic or foreign"),
    ("def f(x=__import__('native')):\n    return x\n", "dynamic or foreign"),
    ("f = lambda x=__import__('native'): x\n", "dynamic or foreign"),
    ("class C(__import__('native').Base):\n    pass\n", "dynamic or foreign"),
    ("x = print.__self__\n", "reflective"),
    ("x = ().__class__.__base__\n", "reflective"),
    ("getattr(print, '__self__')\n", "dynamic or foreign"),
    ("__builtins__['__import__']('native')\n", "dynamic or foreign"),
])
def test_source_proof_denies_hidden_and_foreign_capabilities(source, reason):
    assert reason in closed_young_module_denial(_module(source))


def test_source_proof_denies_unknown_nodes_and_raw_metadata():
    module = _module("def f():\n    return 1\n")
    raw = replace(module, body=(replace(module.body[0], manual_pointer_abi=True),))
    assert "manual pointer ABI" in closed_young_module_denial(raw)
    assert "unverified syntax" in closed_young_module_denial(
        replace(module, body=(SimpleNamespace(),)),
    )
    assert "raw compiler/runtime" in closed_young_module_denial(
        replace(module, name="pcc.user_named_module"),
    )


@pytest.mark.parametrize("changes,reason", [
    ({"source_count": 2}, "dependency-closure"),
    ({"emit_llvm_only": True}, "unknown final link"),
    ({"python_library": True}, "unknown final link"),
    ({"backend": "llvm"}, "backend"),
    ({"gpu_backend": "metal"}, "bridge"),
    ({"object_inputs": ("constructor.o",)}, "external object"),
    ({"link_args": ("-lforeign",)}, "linker inputs"),
    ({"runtime_archive": "foreign.a"}, "archive"),
    ({"environment": {"PCC_RUNTIME_ARCHIVE": "owned.a"}}, "override"),
    ({"environment": {"PCC_RUNTIME_DIR": "/runtime"}}, "override"),
    ({"environment": {"PCC_DEFER_SELF_LINK_PLAN": "plan"}}, "override"),
    ({"environment": {"PCC_RUNTIME_BUILD": "make"}}, "construction"),
    ({"environment": {"PCC_SELF_LINK": "cc"}}, "linker"),
])
def test_request_proof_rejects_open_link_closures(changes, reason):
    assert _request() == ""
    assert reason in _request(**changes)


@pytest.mark.parametrize("target", [
    "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
    "amd64-linux", "arm64-linux-musl",
])
def test_request_proof_accepts_owned_static_elf_targets(target):
    assert _request(target_triple=target) == ""


@pytest.mark.parametrize("target", [
    "arm64-apple-darwin", "aarch64-apple-macosx14.0",
    "x86_64-pc-windows-msvc", "x86_64-pc-windows-gnu",
    "unknown-unknown-unknown", "", "riscv64-unknown-linux-gnu",
    "x86_64-linux-windows-gnu",
])
def test_request_proof_denies_unverified_startup_linkers(target):
    assert "static Linux ELF" in _request(target_triple=target)


def _codegen(source=_PROGRAM, *, proof=False, bridge=False):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(_module(source))
    host = L1CodeGen(module, emit_cpy_main_exitcode=bridge, ir_scaffold_mode="on")
    host._strict_no_libpython = True
    host._target_triple = "x86_64-unknown-linux-gnu"
    if proof:
        host._closed_young_domain_input = module
    return host, module


def test_codegen_defaults_denied_and_consumes_exact_input_proof():
    host, module = _codegen()
    assert not _enable_calls(host.generate(module))
    host._closed_young_domain_input = module
    assert len(_enable_calls(host.generate(module))) == 1
    assert host._closed_young_domain_input is None
    assert not _enable_calls(host.generate(module))
    other = replace(module, name="other")
    host._closed_young_domain_input = module
    assert not _enable_calls(host.generate(other))


@pytest.mark.parametrize("flag,value", [
    ("_strict_no_libpython", False),
    ("emit_cpy_main_exitcode", True),
    ("_python_library", True),
    ("_runtime_port_module", True),
    ("_sibling_module_inits", ("unverified",)),
    ("_skip_program_main", True),
])
def test_codegen_rejects_unproved_lifecycle_domains(flag, value):
    host, module = _codegen(proof=True)
    setattr(host, flag, value)
    assert not _enable_calls(host.generate(module))


@pytest.mark.parametrize("target,publishes_table", [
    ("x86_64-unknown-linux-gnu", False),
    ("arm64-apple-darwin", True),
])
def test_generated_main_admits_after_table_before_first_safepoint(monkeypatch, target, publishes_table):
    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", "1")
    monkeypatch.setenv("PCC_SELF_LINK", "pcc")
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", "1")
    host, module = _codegen(proof=True)
    # Isolate lifecycle ordering with an explicitly injected internal proof.
    # The terminal driver tests below separately deny Darwin admission.
    host._target_triple = target
    text = str(host.generate(module))
    main = text[text.index("define i32 @main("):]
    admission = main.index(_ENABLE)
    if publishes_table:
        assert main.index("@pcc_gc_register_precise_stackmap_table(") < admission
    else:
        # ELF does not yet publish the linked precise table; its registered
        # frame and conservative snapshots remain the actual root sources.
        assert "@pcc_gc_register_precise_stackmap_table(" not in main
    assert admission < main.index("@pcc_thread_safepoint_gate(")
    assert admission < main.index("@py_set_program_args(")


def test_handwritten_main_never_admits(monkeypatch):
    # Exercise the actual lifecycle guard, rather than a raw-ABI declaration
    # that would already have been rejected by the source proof.
    from pcc.ir.compat import ir

    host, _ = _codegen(proof=True)
    fn = ir.Function(host.module, ir.FunctionType(ir.IntType(32), []), "main")
    ir.IRBuilder(fn.append_basic_block("entry")).ret(ir.Constant(ir.IntType(32), 0))
    host._closed_young_domain_enabled = True
    host._emit_program_main([])
    assert not _enable_calls(str(host.module))


@pytest.mark.parametrize("extra,admitted", [
    ({}, True),
    ({"emit_llvm_only": True}, False),
    ({"runtime_archive": "explicit.a"}, False),
    ({"link_args": ("-lforeign",)}, False),
    ({"object_inputs": ("foreign.o",)}, False),
    ({"target_triple": "aarch64-unknown-linux-gnu"}, True),
    ({"target_triple": "arm64-apple-darwin"}, False),
    ({"target_triple": "x86_64-pc-windows-msvc"}, False),
    ({"target_triple": "unknown-unknown-unknown"}, False),
])
def test_terminal_driver_proof_reaches_emitted_main(tmp_path, monkeypatch, extra, admitted):
    from pcc.frontends.python import pipeline

    source = tmp_path / "closed.py"
    source.write_text(_PROGRAM)
    output = tmp_path / "closed"
    captured = []
    for name in ("PCC_RUNTIME_ARCHIVE", "PCC_RUNTIME_DIR", "PCC_DEFER_SELF_LINK_PLAN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PCC_RUNTIME_BUILD", "owned")
    monkeypatch.setenv("PCC_SELF_LINK", "pcc")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "")
    # Stop at the IR-to-link boundary: no object emission or runtime build.
    monkeypatch.setattr(pipeline, "_ensure_runtime", lambda *a, **k: "owned.a")
    monkeypatch.setattr(pipeline, "_explicit_runtime_archive", lambda *a, **k: "explicit.a")
    monkeypatch.setattr(pipeline, "_admit_python_object_inputs", lambda values, *a: values)
    monkeypatch.setattr(pipeline, "_self_backend_skip_ll_temp", lambda: True)
    monkeypatch.setattr(pipeline, "_link_with_self_backend_ir_texts", lambda texts, *a, **k: captured.extend(texts))
    options = dict(target_triple="x86_64-unknown-linux-gnu")
    options.update(extra)
    pipeline.compile_python(
        str(source), str(output), backend="self", ir_scaffold_mode="on",
        **options,
    )
    text = output.read_text() if extra.get("emit_llvm_only") else captured[0]
    assert bool(_enable_calls(text)) is admitted


@pytest.mark.parametrize("host,admitted", [
    ("x86_64-unknown-linux-gnu", True),
    ("aarch64-unknown-linux-gnu", True),
    ("arm64-apple-darwin", False),
    ("x86_64-pc-windows-msvc", False),
    ("unknown-unknown-unknown", False),
])
def test_terminal_driver_resolves_unspecified_target(tmp_path, monkeypatch, host, admitted):
    from pcc.frontends.python import pipeline

    monkeypatch.setattr(pipeline._pipeline_targets, "host_target_triple", lambda: host)
    test_terminal_driver_proof_reaches_emitted_main(
        tmp_path, monkeypatch, {"target_triple": None}, admitted,
    )


def test_multi_source_worker_routes_have_no_proof_issuer():
    from pathlib import Path
    from pcc.frontends.python import pipeline
    from pcc.frontends.python import pipeline_frontend_worker_execution

    text = Path(pipeline.__file__).read_text()
    multi = text[text.index("def compile_python_multi("):]
    assert "_closed_young_domain_input =" not in multi
    worker = Path(pipeline_frontend_worker_execution.__file__).read_text()
    assert "_closed_young_domain_input =" not in worker
