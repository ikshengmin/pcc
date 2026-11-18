"""Compare the bounded walk to the real, unmodified indexed wire, not a model."""
from dataclasses import replace
import random

import pytest

from pcc.backend import self_backend_indexed_codec as codec
from pcc.backend.self_backend_ir import TypeParseContext
from pcc.backend.self_backend_parse import parse_self_backend_module


def _parsed():
    module = parse_self_backend_module(
        'target triple = "x86_64-unknown-linux-gnu"\n'
        '%Pair = type { i32, i32 }\n'
        '@seed = internal constant i32 7, align 4\n'
        'define i64 @example(i64 %arg) {\nentry:\n'
        ' %sum = add nsw i64 %arg, 1\n ret i64 %sum\n}\n'
    )
    for f in module.functions:
        codec.get_indexed_function_kernel(f)
    return module


def _exact_boundary(tmp_path, monkeypatch, module):
    ordinary = tmp_path / "ordinary.pidx"
    bounded = tmp_path / "bounded.pidx"
    rejected = tmp_path / "rejected.pidx"
    codec.encode_indexed_module_file(str(ordinary), module)
    expected = ordinary.read_bytes()
    with monkeypatch.context() as patch:
        from pcc.backend import self_backend_indexed_budget as precise

        original = precise.count_plain_wire
        refinements = []

        def traced(*args):
            refinements.append(True)
            return original(*args)

        patch.setattr(precise, "count_plain_wire", traced)
        detail = {}
        codec.encode_indexed_module_file(
            str(bounded), module, max_bytes=len(expected), diagnostic=detail,
        )
        assert bounded.read_bytes() == expected
        assert detail["stage"] == "codec-write"
        assert len(refinements) == 1

        def materialized(*args, **kwargs):
            pytest.fail("over-budget input reached wire materialization")

        patch.setattr(codec, "_function_to_wire", materialized)
        patch.setattr(codec, "_global_to_wire", materialized)
        patch.setattr(codec.json, "dumps", materialized)
        patch.setattr(codec.CompilerIntArena, "get_unchecked", materialized)
        with pytest.raises(ValueError, match="exceeds the size limit"):
            codec.encode_indexed_module_file(
                str(rejected), module, max_bytes=len(expected) - 1, diagnostic=detail,
            )
        assert detail["stage"] == "codec-preflight"
        assert detail["observed_bytes"] > len(expected) - 1
        assert not rejected.exists()
        assert len(refinements) == 2
    return len(expected)


@pytest.mark.parametrize("with_context", [False, True])
def test_exact_empty_header_and_decimal_length_transitions(tmp_path, monkeypatch, with_context):
    context = TypeParseContext() if with_context else None
    # Cross a power of ten in the decimal header-length line.
    for triple in ("", "a" * 813, "a" * 814, "a" * 820, "a" * 9850):
        _exact_boundary(tmp_path, monkeypatch, codec.ParsedModule(triple, (), (), context))


def test_whole_wire_with_raw_and_tagged_payloads(tmp_path, monkeypatch):
    m = _parsed()
    f = m.functions[0]
    k = f.indexed_kernel
    strings = ("", 'a"\\b', "\t\n\0\x1b\x7f", "\u2603", "\ud800", "\U0010ffff")
    ty = codec.TypeDesc("s\u2603", name="\udfff", fields=(
        codec.TypeDesc("ptr", pointee=codec.TypeDesc("int", width=64)),
        codec.TypeDesc("array", count=12, elem=codec.TypeDesc("int", width=8)),
    ))
    m.type_context.named_type_bodies.update(zip(strings, reversed(strings)))
    m = replace(m, globals_=tuple(
        replace(m.globals_[0], name=text, type=ty, initializer=strings[-i - 1],
                alignment=i, is_internal=i % 2 == 0, trailing_attributes=strings[:i])
        for i, text in enumerate(strings)
    ))
    rng = random.Random(442080)
    atoms = [*strings, None, False, True, -1, -(1 << 63), (1 << 64) - 1,
             -0.0, 2.5e35, float("inf"), float("-inf"), float("nan"), ty]

    def value(depth):
        if not depth or rng.randrange(3) == 0:
            return rng.choice(atoms)
        items = [value(depth - 1) for _ in range(rng.randrange(4))]
        return rng.choice((items, tuple(items), dict(enumerate(items))))

    for linkage in ("", "external", "internal", "external_odr"):
        f.definition_linkage = linkage
        f.is_global = linkage != "internal"
        f.is_vararg = not f.is_vararg
        k.first_duplicate_definition_value_id = -99
        k.instruction_use_total = (1 << 64) - 1
        k.call_texts = list(strings)
        k.types = (ty,)
        k.instruction_arithmetic_flag_values = {0: (), 99: strings}
        k.cold_instruction_data = ([], (), {}, *atoms, *(value(4) for _ in range(30)))
        _exact_boundary(tmp_path, monkeypatch, m)


@pytest.mark.parametrize("remaining", [431198, 2 * 1024 * 1024])
def test_remaining_budget_is_an_actual_wire_boundary(tmp_path, monkeypatch, remaining):
    # Synthetic valid wire shape, not the missing CI inputs. Include real arena
    # bytes; use a large cold text to put the *ordinary serializer* on the edge.
    m = _parsed()
    k = m.functions[0].indexed_kernel
    k.call_texts = [""]
    ordinary = tmp_path / "size.pidx"

    def measured():
        codec.encode_indexed_module_file(str(ordinary), m)
        return ordinary.stat().st_size

    k.call_texts[0] = "x" * (remaining - measured())
    # The decimal length prefix can itself gain a few digits after padding.
    extra = measured() - remaining
    if extra:
        assert extra > 0
        k.call_texts[0] = k.call_texts[0][:-extra]
    assert measured() == remaining
    with pytest.raises(ValueError, match="size limit"):
        codec._bounded_capture_budget(m, remaining)
    assert _exact_boundary(tmp_path, monkeypatch, m) == remaining


def test_fallback_does_not_revisit_subclass_callbacks(tmp_path, monkeypatch):
    class Text(str):
        def __len__(self):
            markers.append("len")
            return super().__len__()

    markers = []
    m = _parsed()
    m.functions[0].indexed_kernel.call_texts = [Text("callee")]
    original = codec._bounded_capture_budget
    fallback = []

    def tracked(*args):
        fallback.append(True)
        return original(*args)

    monkeypatch.setattr(codec, "_bounded_capture_budget", tracked)
    codec.encode_indexed_module_file(str(tmp_path / "fallback.pidx"), m, max_bytes=2097152)
    assert fallback == [True]
    assert markers == ["len"]


@pytest.mark.parametrize("error_type", [ValueError, LookupError])
def test_refinement_never_reclassifies_a_callback_error(tmp_path, monkeypatch, error_type):
    from pcc.backend import self_backend_indexed_budget as precise

    error = error_type("indexed module diagnostic exceeds the size limit")
    calls = []

    class Explosive(str):
        def __len__(self):
            calls.append("name")
            raise error

    def unexpected(*args):
        pytest.fail("only the original walk's own size rejection may be refined")

    monkeypatch.setattr(precise, "count_plain_wire", unexpected)
    m = _parsed()
    f = m.functions[0]
    f.name = Explosive("name")
    f.indexed_kernel.instruction_arithmetic_flag_values = {0: ["nsw"] * 20000}
    with pytest.raises(error_type) as caught:
        codec.encode_indexed_module_file(str(tmp_path / "no.pidx"), m, max_bytes=16000)
    assert caught.value is error and calls == ["name"]
    assert not (tmp_path / "no.pidx").exists()


def test_rejected_refinement_keeps_first_observed_counter(tmp_path):
    diagnostic = {}
    with pytest.raises(ValueError, match="size limit"):
        codec.encode_indexed_module_file(
            str(tmp_path / "no.pidx"), _parsed(), max_bytes=16, diagnostic=diagnostic,
        )
    assert diagnostic == {
        "stage": "codec-preflight", "remaining_bytes": 16, "observed_bytes": 4096,
    }
    assert not (tmp_path / "no.pidx").exists()


def test_refines_plain_producer_state_not_callback_bearing_dicts(tmp_path):
    module = codec.ParsedModule("x", (), (), None)
    reference = tmp_path / "plain.pidx"
    codec.encode_indexed_module_file(str(reference), module)
    data = reference.read_bytes()
    state = {"invocation": "a" * 32, "index": 0, "stage": "snapshot-destination",
             "remaining_bytes": -1, "observed_bytes": -1, "copied_bytes": -1,
             "inventory_count": -1}
    bounded = tmp_path / "fits.pidx"
    codec.encode_indexed_module_file(str(bounded), module, max_bytes=len(data), diagnostic=state)
    assert bounded.read_bytes() == data and state["observed_bytes"] == len(data)

    events = []

    class OldObservation:
        def __del__(self):
            events.append("released")

    class AliasedKey:
        def __hash__(self):
            return hash("observed_bytes")

        def __eq__(self, other):
            events.append(other)
            return other == "observed_bytes"

    for diagnostic, expected in [({"observed_bytes": OldObservation()}, "released"),
                                 ({AliasedKey(): -1}, "observed_bytes")]:
        events.clear()
        rejected = tmp_path / "no.pidx"
        with pytest.raises(ValueError, match="size limit"):
            codec.encode_indexed_module_file(str(rejected), module,
                                             max_bytes=len(data), diagnostic=diagnostic)
        assert events == [expected] and not rejected.exists()


def test_refinement_does_not_call_an_exotic_value_a_second_time(tmp_path):
    markers = []

    class Name(str):
        def __len__(self):
            markers.append("len")
            return super().__len__()

    m = _parsed()
    f = m.functions[0]
    f.name = Name(f.name)
    f.indexed_kernel.call_texts = ["x" * 50000]
    ordinary = tmp_path / "ordinary.pidx"
    codec.encode_indexed_module_file(str(ordinary), m)
    budget = ordinary.stat().st_size
    for direct in (True, False):
        markers.clear()
        state = {}
        with pytest.raises(ValueError, match="size limit"):
            if direct:
                codec._bounded_capture_budget(m, budget, state)
            else:
                codec.encode_indexed_module_file(str(tmp_path / "no.pidx"), m,
                                                 max_bytes=budget, diagnostic=state)
        assert state["observed_bytes"] > budget and markers == ["len"]
    assert not (tmp_path / "no.pidx").exists()
