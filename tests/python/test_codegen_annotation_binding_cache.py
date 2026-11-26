"""Per-codegen source annotation bindings must reuse only frozen current AST."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline_exports as exports
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.host_contract import L1_CODEGEN_HOST_ATTRS
from pcc.frontends.python.py_ast import Module
from pcc.frontends.python.py_lift import parse_and_lift


def _module(source, name='pkg.owner'):
    return parse_and_lift(source, '/tmp/pkg/owner.py', name)


def test_reuses_the_exact_frozen_module_and_invalidates_replacement(monkeypatch):
    first = _module('from pcc.ir.compat import ir\nfrom .sibling import Record as Alias\n')
    original = exports.annotation_module_bindings
    scans = []
    def observed(module):
        scans.append(module)
        return original(module)
    monkeypatch.setattr(exports, 'annotation_module_bindings', observed)
    cache = []
    expected = original(first)
    actual = exports._codegen_annotation_module_bindings(first, cache)
    assert actual == expected and actual['ir'] == 'pcc.ir.ir'
    assert exports._codegen_annotation_module_bindings(first, cache) is actual
    assert scans == [first]
    # Equal but distinct AST must still miss identity cache.
    same_value = replace(first)
    assert same_value == first and same_value is not first
    second = exports._codegen_annotation_module_bindings(same_value, cache)
    assert second == expected and second is not actual
    rebound = _module('from pcc.ir.compat import ir\nir = None\nfrom elsewhere import *\n')
    assert exports._codegen_annotation_module_bindings(rebound, cache) == original(rebound) == {}
    assert scans == [first, same_value, rebound]
    with pytest.raises(FrozenInstanceError):
        first.body = ()


def test_custom_and_mutable_module_adapters_never_reuse_cached_result():
    original = _module('from pcc.ir.compat import ir\n')
    cache = []
    expected = exports._codegen_annotation_module_bindings(original, cache)
    # Public export binding analysis also supports structural AST adapters.
    adapter = SimpleNamespace(name=original.name, body=list(original.body))
    assert exports._codegen_annotation_module_bindings(adapter, cache) == expected
    adapter.body.clear()
    assert exports._codegen_annotation_module_bindings(adapter, cache) == {}
    # Even an exact Module constructed with invalid mutable body storage
    # does not enter the immutable cache path.
    mutable = Module(original.name, list(original.body))
    assert exports._codegen_annotation_module_bindings(mutable, cache) == expected
    mutable.body.clear()
    assert exports._codegen_annotation_module_bindings(mutable, cache) == {}
    assert exports._codegen_annotation_module_bindings(original, cache) is expected


def test_constructor_cache_is_private_and_public_results_are_fresh():
    module = _module('from pcc.ir.compat import ir\n')
    a = L1CodeGen(module, emit_cpy_main_exitcode=False)
    b = L1CodeGen(module, emit_cpy_main_exitcode=False)
    assert '_annotation_module_bindings_cache' in L1_CODEGEN_HOST_ATTRS
    assert a._annotation_module_bindings_cache == b._annotation_module_bindings_cache == []
    assert a._annotation_module_bindings_cache is not b._annotation_module_bindings_cache
    cached = exports._codegen_annotation_module_bindings(a.ast_module, a._annotation_module_bindings_cache)
    public = exports.annotation_module_bindings(a.ast_module)
    public.clear()
    assert cached['ir'] == 'pcc.ir.ir'
    assert exports._codegen_annotation_module_bindings(b.ast_module, b._annotation_module_bindings_cache) is not cached


def test_real_scaffold_consumer_observes_replaced_alias_owner():
    from pcc.frontends.python.py_ast import ClassType, Name, SourceSpan
    module = _module('from pcc.ir.compat import ir\n')
    host = L1CodeGen(module, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    receiver = Name(span=SourceSpan('/tmp/pkg/owner.py', 1, 0, 1, 1),
                    ty=ClassType(name='Function', module='ir'), ident='fn')
    assert not host._scaffold_receiver_has_nonprovider_class(receiver)
    assert host._annotation_module_bindings_cache[0][0] is module
    # Native import rewriting constructs a new frozen Module. A lexical alias
    # that is rebound in that replacement must no longer prove the provider.
    rebound = _module('from pcc.ir.compat import ir\nir = None\n')
    host.ast_module = rebound
    assert host._scaffold_receiver_has_nonprovider_class(receiver)
    assert host._annotation_module_bindings_cache[0][0] is rebound
