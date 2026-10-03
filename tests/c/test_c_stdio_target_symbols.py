"""Implicit C streams use the selected platform ABI, before object emission."""

import subprocess

import pytest

from pcc.backend import macho_spec
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
from pcc.frontends.c.parse.c_parser import CParser


STREAMS = {"stdin": "__stdinp", "stdout": "__stdoutp", "stderr": "__stderrp"}
TARGETS = (
    ("arm64-apple-darwin", True),
    ("aarch64-apple-macosx14.0.0", True),
    ("x86_64-unknown-linux-gnu", False),
    ("aarch64-unknown-linux-gnu", False),
    ("x86_64-pc-windows-msvc", False),
)


@pytest.fixture(autouse=True)
def forbid_external_compilation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external compiler process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def _module(source, target="arm64-apple-darwin"):
    generator = CCodeGenerator()
    generator.module.triple = target
    generator.generate_code(CParser().parse(source))
    return generator.module


@pytest.mark.parametrize("target, darwin", TARGETS)
@pytest.mark.parametrize("stream", STREAMS)
def test_implicit_stream_uses_target_symbol(target, darwin, stream):
    module = _module("void *read_stream(void) { return " + stream + "; }", target)
    expected = STREAMS[stream] if darwin else stream
    assert expected in module.globals
    if expected != stream:
        assert stream not in module.globals


def test_owned_macho_relocations_name_darwin_stream_globals():
    source = "\n".join("void *read_" + name + "(void) { return " + name + "; }" for name in STREAMS)
    module = _module(source)
    data = emit_owned_object(str(module), str(module.triple))
    obj = macho_spec.parse_object(data)
    undefined = {symbol["name"] for symbol in obj.symbols()
                 if symbol["n_type"] & macho_spec.N_TYPE == macho_spec.N_UNDF}
    assert undefined == {"_" + name for name in STREAMS.values()}


@pytest.mark.parametrize("stream", STREAMS)
def test_explicit_stream_declaration_preserves_c_symbol(stream):
    module = _module("extern void *" + stream + "; void *read_stream(void) { return " + stream + "; }")
    assert stream in module.globals
    assert STREAMS[stream] not in module.globals


@pytest.mark.parametrize("stream", STREAMS)
def test_local_stream_binding_is_not_an_external_global(stream):
    module = _module("void *read_stream(void *" + stream + ") { return " + stream + "; }")
    assert stream not in module.globals
    assert STREAMS[stream] not in module.globals


@pytest.mark.parametrize("symbol", STREAMS.values())
def test_explicit_darwin_abi_name_is_not_remapped(symbol):
    module = _module("void *read_stream(void) { return " + symbol + "; }")
    assert symbol in module.globals
    assert set(module.globals) == {"read_stream", symbol}
