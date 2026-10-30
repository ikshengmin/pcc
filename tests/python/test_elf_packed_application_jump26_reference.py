"""Exact source and tiny ELF controls; no compiler or executable invocation."""
import ast
import hashlib
import struct

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.backend.owned_elf_inputs import ElfInputStore
from tests.elf_packed_application_preimage import (
    ORIGINAL_LINK_IMAGE_SOURCE,
    original_linker as historical_linker,
)
from tests.elf_packed_application_jump26_reference import (
    JUMP26_FORWARD_EDITS, current_jump26_source, original_linker,
    _reference_veneer_branch,
)
from tests.python.test_elf_packed_application import _single
from tests.python.test_elf_packed_link_classification import BASE, TARGET, _outcome

B, BL, RET, NOP = 0x14000000, 0x94000000, 0xD65F03C0, 0xD503201F


def test_exact_forward_layer_roundtrips_to_untouched_historical_literal():
    source = current_jump26_source(ORIGINAL_LINK_IMAGE_SOURCE)
    assert hashlib.sha256(ORIGINAL_LINK_IMAGE_SOURCE.encode()).hexdigest() == (
        "a613a9dee2637ea20e5e464a1a5f5149cf51c42eeaeaa73d23bda54810cba73f")
    assert hashlib.sha256(source.encode()).hexdigest() == (
        "bc41958cd6ce34356bd167ffa3cc7e689a7c71044225f625cc9f4c6749571279")
    for before, after in reversed(JUMP26_FORWARD_EDITS):
        assert source.count(after) == 1
        source = source.replace(after, before)
    assert source == ORIGINAL_LINK_IMAGE_SOURCE


@pytest.mark.parametrize("drift", list(range(6)))
def test_layer_rejects_every_changed_hunk_and_unrelated_drift(drift):
    if drift == 5:
        source = ORIGINAL_LINK_IMAGE_SOURCE + "\n# unrelated drift\n"
    else:
        before, _after = JUMP26_FORWARD_EDITS[drift]
        assert ORIGINAL_LINK_IMAGE_SOURCE.count(before) == 1
        source = ORIGINAL_LINK_IMAGE_SOURCE.replace(before, before.replace("\n", " # drift\n", 1))
    assert source != ORIGINAL_LINK_IMAGE_SOURCE
    with pytest.raises(AssertionError):
        current_jump26_source(source)


def test_reference_keeps_independent_generic_application_projection():
    source = current_jump26_source(ORIGINAL_LINK_IMAGE_SOURCE)
    tree = ast.parse(source)
    loops = [node for node in ast.walk(tree) if isinstance(node, ast.For)
             and isinstance(node.target, ast.Name) and node.target.id == "relocation"
             and ast.dump(node.iter) == ast.dump(ast.parse("section.relocations", mode="eval").body)]
    assert len(loops) == 1
    assert "_arm_packed_application_rows" not in source
    assert "_x86_packed_application_rows" not in source
    assert "_aarch64_veneer_branch" not in source
    assert "branch_slots.setdefault" in source


def _route(tmp_path, obj, shape):
    encoded = elf.emit_relocatable(obj)
    if shape == "tuple":
        return obj, {}, encoded
    if shape == "packed":
        return elf.parse_relocatable(encoded, compact_relocations=True), {}, encoded
    path = tmp_path / "reference.o"
    path.write_bytes(encoded)
    store = ElfInputStore(TARGET)
    return store.stage(str(path)), {"_input_store": store}, encoded


def _check_input(tmp_path, owner, options, encoded):
    if options:
        assert (tmp_path / "reference.o").read_bytes() == encoded
        options["_input_store"].verify()
    else:
        assert elf.emit_relocatable(owner) == encoded


@pytest.mark.parametrize("shape", ["tuple", "packed", "staged"])
@pytest.mark.parametrize("word", [B, BL])
def test_near_jump_matches_exact_guarded_image_not_historical_layout(tmp_path, shape, word):
    obj = _single(282, word=word)
    historical = _outcome(historical_linker(elf), obj)
    assert historical[0] == "image" and len(historical[1]) == 0x1008
    # Independently spell the new two guarded pools and alignment, including
    # the original near-branch rule that preserves the input opcode bits.
    expected = bytearray(historical[1][:0x1000])
    guard = struct.pack("<II", B | 6, NOP) + bytes(16)
    expected += guard + struct.pack("<II", NOP, NOP)
    expected += struct.pack("<II", word | ((-8) & 0x03FFFFFF), RET) + guard
    assert len(expected) == 0x1040
    struct.pack_into("<Q", expected, 24, BASE + 0x1020)
    struct.pack_into("<QQ", expected, 96, len(expected), len(expected))
    owner, options, encoded = _route(tmp_path, obj, shape)
    for link in (original_linker(elf), elf._link_static_executable_image):
        assert _outcome(link, owner, **options) == ("image", bytes(expected))
    _check_input(tmp_path, owner, options, encoded)


@pytest.mark.parametrize("shape", ["tuple", "packed", "staged"])
@pytest.mark.parametrize("word", [B, BL])
def test_far_jump_current_opcode_order_and_full_route_outcome(tmp_path, shape, word):
    target = 1 << 40
    obj = _single(282, value=target, word=word)
    old = _outcome(historical_linker(elf), obj)
    assert old[0] != "image" and "signed_bits=26" in str(old)
    owner, options, encoded = _route(tmp_path, obj, shape)
    reference = _outcome(original_linker(elf), owner, **options)
    assert _outcome(elf._link_static_executable_image, owner, **options) == reference
    if word == B:
        assert reference[0] == "image"
        assert struct.unpack_from("<IIQ", reference[1], 0x1008) == (0x58000050, 0xD61F0200, target)
        assert struct.unpack_from("<I", reference[1], 0x1020)[0] & 0xFC000000 == B
    else:
        assert reference[0] != "image"
        assert "JUMP26 relocation requires a B instruction" in str(reference)
    _check_input(tmp_path, owner, options, encoded)


def test_independent_predicate_preserves_opaque_comparison_order():
    events = []
    class Kind(int):
        def __eq__(self, other):
            events.append(other)
            return int(self) == other
    assert not _reference_veneer_branch(Kind(282), True)
    assert _reference_veneer_branch(Kind(283), True)
    assert events == [283, 283]
    assert _reference_veneer_branch(282, True)
    assert not _reference_veneer_branch(282, False)


def test_generic_reference_preserves_call_snapshot_when_comparison_mutates_kind():
    def run(link):
        obj = _single(283, value=1 << 40, word=BL)
        row = obj.sections[0].relocations[0]
        armed = [False]
        class Kind(int):
            __hash__ = int.__hash__
            def __eq__(self, other):
                if armed[0] and other == 283:
                    object.__setattr__(row, "type", 282)
                return int(self) == other
        class Timing:
            def begin(self, phase):
                if phase == "elf_image_resolve_relocate":
                    armed[0] = True
                return 0
            def complete(self, phase, started):
                pass
        object.__setattr__(row, "type", Kind(283))
        outcome = _outcome(link, obj, coordinator_timing=Timing())
        return outcome, row.type
    before = run(original_linker(elf))
    after = run(elf._link_static_executable_image)
    assert before == after and before[0][0] == "image" and before[1] == 282
    assert struct.unpack_from("<I", before[0][1], 0x1020)[0] & 0xFC000000 == BL
