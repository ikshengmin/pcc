"""Real native-parser/host inference differential, not native execution."""

import pytest
from pathlib import Path
import sys

from pcc.frontends.python import type_infer
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_ast import Name, SourceSpan
from pcc.frontends.python.types import TYPE_DYN, TYPE_INT

from .frontend_experiment import (
    REFERENCE_SCOPE,
    SEMANTIC_FIXTURES,
    infer,
    inventory,
    narrowing_fixture,
    scope_implementation,
)
from .scope_record import RecordScope


@pytest.mark.parametrize("name", sorted(SEMANTIC_FIXTURES))
def test_real_frontend_ast_equal(name):
    source = SEMANTIC_FIXTURES[name]
    before = infer(source, REFERENCE_SCOPE)
    after = infer(source, RecordScope)
    assert before == after
    assert repr(before) == repr(after)
    assert type_infer._Scope is REFERENCE_SCOPE


def test_real_narrowing_creates_single_field_scopes_and_reduces_containers():
    source = narrowing_fixture(functions=2, branches=3)
    before, baseline = inventory(source, REFERENCE_SCOPE)
    after, record = inventory(source, RecordScope)
    assert before == after
    # Parameter scopes also bind their function's recursion name; only the
    # six narrowed records have one field. Each condition adds an empty scope.
    assert baseline["single_scopes"] == record["single_scopes"] == 6
    assert baseline["full_environment_copies"] == record["full_environment_copies"] == 0
    assert record["binding_dict_allocations"] < baseline["binding_dict_allocations"]
    assert record["class_set_allocations"] < baseline["class_set_allocations"]
    assert record["retained_local_fields"] == baseline["retained_local_fields"]
    assert record["retained_parent_edges"] == baseline["retained_parent_edges"]


def test_narrowing_that_writes_another_local_spills_instead_of_claiming_savings():
    source = narrowing_fixture(functions=2, branches=3, writes=True)
    before, baseline = inventory(source, REFERENCE_SCOPE)
    after, record = inventory(source, RecordScope)
    assert before == after
    assert baseline["single_scopes"] == record["single_scopes"] == 0
    assert record["binding_dict_allocations"] == record["multi_scopes"] == 9
    # Only empty scopes still save a dict. All written narrowing scopes spill.
    assert baseline["binding_dict_allocations"] - record["binding_dict_allocations"] == 6
    assert record["spill_field_copies"] > 0


def test_exception_restores_reference_scope():
    with pytest.raises(RuntimeError, match="deliberate"):
        with scope_implementation(RecordScope):
            assert type_infer._Scope is RecordScope
            raise RuntimeError("deliberate")
    assert type_infer._Scope is REFERENCE_SCOPE


def test_direct_mapping_access_and_empty_scope_match_reference():
    reference, candidate = REFERENCE_SCOPE(), RecordScope()
    for scope in (reference, candidate):
        assert bool(scope)
        scope.define("C", 1)
        scope.class_objects.add("C")
        child = type(scope)(scope)
        assert child.lookup("C") == 1
        child.define("C", 2)
        assert child.bindings.get("C") == 2
        assert not child.is_class_object("C")


@pytest.mark.parametrize("name", sorted(SEMANTIC_FIXTURES))
def test_owned_ir_builder_emits_identical_text(name):
    source = SEMANTIC_FIXTURES[name]
    before = L1CodeGen(infer(source, REFERENCE_SCOPE)).generate()
    after = L1CodeGen(infer(source, RecordScope)).generate()
    assert before == after
    assert "define " in before


def test_real_name_lookup_detects_false_empty_record_mutant():
    class FalseEmptyRecord(RecordScope):
        def __bool__(self):
            return bool(len(self))

    results = []
    for scope_type in (REFERENCE_SCOPE, RecordScope, FalseEmptyRecord):
        outer = scope_type()
        outer.define("visible", TYPE_INT)
        inner = scope_type(outer)
        expr = Name(SourceSpan("empty_scope.py", 1, 0, 1, 7), TYPE_DYN, "visible")
        # This branch of real _infer_expr does not need an inference context.
        results.append(type_infer._infer_expr(None, inner, expr).ty)
    assert results[:2] == [TYPE_INT, TYPE_INT]
    assert results[2] == TYPE_DYN


@pytest.mark.parametrize("filename", ("export_meta.py", "raw_pointer_types.py"))
def test_actual_compiler_source_inference_matches(filename):
    path = Path(type_infer.__file__).with_name(filename)
    source = path.read_text()
    before = infer(source, REFERENCE_SCOPE)
    after = infer(source, RecordScope)
    assert before == after
    assert repr(before) == repr(after)


@pytest.mark.parametrize("width", (1, 8))
def test_host_live_payload_reference_counts_are_not_elided(width):
    payload = object()
    initial = sys.getrefcount(payload)
    for scope_type in (REFERENCE_SCOPE, RecordScope):
        outer = scope_type()
        outer.define("parent_value", payload)
        inner = scope_type(outer)
        for index in range(width):
            inner.define("field" + str(index), payload)
        assert sys.getrefcount(payload) - initial == width + 1
        del outer, inner
        assert sys.getrefcount(payload) == initial
