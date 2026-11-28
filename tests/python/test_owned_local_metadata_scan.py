"""Owned frozen AST metadata scans preserve the generic source-order oracle."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from pcc.frontends.python import parser
from pcc.frontends.python.codegen import local_bound_lowering as lowering
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_ast import If, Name


_COMPLEX = '''
def probe():
    a = one
    import os.path, sys as sys_alias
    from math import sqrt as root, cos
    from builtins import len as length
    if condition:
        b = two
    else:
        c = three
    try:
        d = four
    except First:
        e = five
    except Second:
        f = six
    else:
        g = seven
    finally:
        h = eight
    with context:
        i = nine
    def nested():
        ignored = nested_value
    class Inner:
        ignored = class_value
    fn = lambda: inner_value
    last = extern(callee)
'''


def _collect():
    return getattr(lowering, "_collect_local_metadata_names", None)


def _host(module, owned, events):
    host = L1CodeGen(module)
    host.current_func_def = module.body[0]
    host._owned_frozen_scope_input = owned
    host._extern_bindings = {"extern": "extern"}

    def marker(value):
        if isinstance(value, Name):
            events.append(value.ident)
            return "metadata"
        events.append(type(value).__name__)
        return None
    host._native_builtin_value_kind_for_expr = marker
    return host


def test_owned_projection_exact_metadata_event_and_import_order(monkeypatch):
    module = parser.parse(_COMPLEX, "metadata.py")
    fd = module.body[0]
    collect = _collect()
    assert collect is not None
    events = []
    generic = _host(module, False, events)
    expected = collect(generic, fd)
    generic_events = list(events)
    assert generic_events == ["Call", "Lambda", "nine", "eight", "seven", "six", "five", "four",
                              "three", "two", "one"]
    assert expected == ["last", "i", "h", "g", "f", "e", "d", "c", "b",
                        "root", "cos", "os", "sys_alias", "a"]
    events.clear()
    owned = _host(module, True, events)
    monkeypatch.setattr(lowering, "_dataclass_field_names",
                        lambda *args: pytest.fail("canonical statement projection must not reflect"))
    assert collect(owned, fd) == expected
    assert events == generic_events


def test_generic_adapter_keeps_custom_getter_and_does_not_read_private_marker(monkeypatch):
    module = parser.parse("def probe():\n    a = source\n", "metadata.py")
    fd = module.body[0]
    events = []

    class Host(SimpleNamespace):
        def __getattr__(self, name):
            if name == "_owned_frozen_scope_input":
                pytest.fail("arbitrary adapter must not observe a private marker lookup")
            raise AttributeError(name)

    host = Host(_extern_bindings={})
    host._native_builtin_value_kind_for_expr = lambda value: events.append("marker") or "metadata"
    original = Name.__getattribute__
    def observed(node, name):
        if name == "ident":
            events.append("ident")
        return original(node, name)
    monkeypatch.setattr(Name, "__getattribute__", observed)
    collect = _collect()
    assert collect is not None
    assert collect(host, fd) == ["a"]
    assert events == ["marker", "ident", "ident", "ident"]


def test_owned_unknown_statement_subclass_retains_generic_reflection(monkeypatch):
    module = parser.parse("def probe():\n    if condition:\n        a = source\n", "metadata.py")
    fd = module.body[0]
    old = fd.body[0]
    events = []

    class _If(If):
        def __getattribute__(self, name):
            if name == "cond":
                events.append("cond")
            return super().__getattribute__(name)

    custom = _If(old.span, old.cond, old.body, old.else_body)
    fd = replace(fd, body=(custom,))
    module = replace(module, body=(fd,))
    host = _host(module, True, events)
    events.clear()
    collect = _collect()
    assert collect is not None
    assert collect(host, fd) == ["a"]
    assert events == ["cond", "source"]


def test_actual_worker_projection_and_injected_control_emit_exact_same_ir(monkeypatch, tmp_path):
    from pcc.frontends.python import pipeline, pipeline_frontend_workers as workers
    for name in ("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
                 "PCC_DIRECT_INDEXED_SIDECAR", "PCC_TEXT_INDEXED_KERNEL_EMIT"):
        monkeypatch.setenv(name, "0")
    source = tmp_path / "probe.py"
    source.write_text('''
class Holder:
    def probe(self, left: str, right: str, enabled: bool) -> str:
        if enabled:
            first = left
        else:
            first = right
        return first

def probe(left: str, right: str, enabled: bool) -> str:
    if enabled:
        first = left
    else:
        first = right
    return first
''')
    manifest = tmp_path / "worker.manifest"
    workers.write_worker_manifest(
        str(manifest), str(tmp_path / "result.tsv"), str(tmp_path), "", "",
        [str(source)], ["probe"], [0], entry_module="probe", sibling_inits=(),
        ir_scaffold_mode="on", verbose=False,
    )
    reflected = []
    original = lowering._dataclass_field_names
    monkeypatch.setattr(lowering, "_dataclass_field_names",
                        lambda node: reflected.append(type(node).__name__) or original(node))
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, (tmp_path / "result.tsv").read_text()
    owned_ir = (tmp_path / "module_0.ll").read_bytes()
    assert reflected == []
    context = pipeline.build_closed_world_context
    monkeypatch.setattr(pipeline, "build_closed_world_context", lambda *args, **kwargs: context(*args, **kwargs))
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, (tmp_path / "result.tsv").read_text()
    assert (tmp_path / "module_0.ll").read_bytes() == owned_ir
    assert reflected
    assert b"strict.nolib.stub:" not in owned_ir


def test_owned_malformed_control_container_uses_legacy_fallback(monkeypatch):
    module = parser.parse("def probe():\n    if condition:\n        a = source\n", "metadata.py")
    fd = module.body[0]
    # Public AST construction can produce list-backed fields even though
    # ordinary parser/wire ownership guarantees tuples. Do not publish part
    # of a control's children before declining that shape.
    odd = replace(fd.body[0], body=list(fd.body[0].body))
    fd = replace(fd, body=(odd,))
    module = replace(module, body=(fd,))
    events = []
    host = _host(module, True, events)
    reflected = []
    original = lowering._dataclass_field_names
    monkeypatch.setattr(lowering, "_dataclass_field_names",
                        lambda node: reflected.append(type(node).__name__) or original(node))
    assert lowering._collect_local_metadata_names(host, fd) == ["a"]
    assert events == ["source"]
    assert reflected[0] == "If"


def test_metadata_callback_error_stops_at_same_reverse_event():
    module = parser.parse("def probe():\n    a = first\n    b = second\n    c = third\n", "metadata.py")
    fd = module.body[0]
    for owned in (False, True):
        events = []
        host = _host(module, owned, events)
        def marker(value):
            events.append(value.ident)
            if value.ident == "second":
                raise ValueError("marker failure")
            return "metadata"
        host._native_builtin_value_kind_for_expr = marker
        with pytest.raises(ValueError, match="marker failure"):
            lowering._collect_local_metadata_names(host, fd)
        assert events == ["third", "second"]
