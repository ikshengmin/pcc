"""Tiny host ELF linker contracts for function JUMP26 veneers; no ELF execution."""
from dataclasses import replace
import struct

import pytest

from pcc.backend import elf_aarch64_relocations as arm
from pcc.backend import elf_x86_64 as elf
from pcc.backend.owned_elf_inputs import ElfInputStore
from tests.python.test_aarch64_elf_call_veneers import (
    B, BL, RET, NOP, BASE, FAR, _words, _branch_target, _object, _link,
)


def _jump(target=FAR, **kwargs):
    return _object(target, kind=282, word=B, **kwargs)


def _assert_jump_veneer(image, offset, place, target):
    assert struct.unpack_from("<I", image, offset)[0] & 0xFC000000 == B
    veneer = _branch_target(image, offset, place) - (place - offset)
    assert struct.unpack_from("<IIQ", image, veneer) == (0x58000050, 0xD61F0200, target)
    # Both B and BR preserve incoming LR; neither encoding has the link bit.
    assert image[offset + 4:offset + 8] == _words(RET)


@pytest.mark.parametrize("delta", [-(1 << 27), (1 << 27) - 4, -4, 0, 4])
def test_jump_direct_boundaries_keep_b_and_unused_slots(delta):
    place = BASE + 0x1018
    image, offset, entry = _link(_jump(place + delta))
    assert entry == place and _branch_target(image, offset, entry) == place + delta
    assert struct.unpack_from("<I", image, offset)[0] & 0xFC000000 == B
    assert image[0x1008:0x1018] == bytes(16)


@pytest.mark.parametrize("delta", [-(1 << 27) - 4, 1 << 27])
@pytest.mark.parametrize("addend", [-12, 0, 20])
def test_jump_overflow_uses_effective_target_and_preserves_lr(delta, addend):
    target = BASE + 0x1018 + delta
    image, offset, place = _link(_jump(target - addend, addend=addend))
    _assert_jump_veneer(image, offset, place, target)
    assert len(image) == 0x1038


@pytest.mark.parametrize("word", [BL, NOP, 0, 0xD63F0200])
def test_far_jump_rejects_non_b_before_rescuing(word):
    with pytest.raises(elf.ElfError, match="JUMP26 relocation requires a B instruction"):
        _link(_object(kind=282, word=word))


@pytest.mark.parametrize("target,addend", [(FAR + 1, 0), (FAR, 2)])
def test_far_jump_misalignment_is_not_rescued(target, addend):
    with pytest.raises(elf.ElfError, match="unaligned AArch64 branch target"):
        _link(_jump(target, addend=addend))


@pytest.mark.parametrize("target", [-4, 1 << 64])
def test_far_jump_unsigned_address_bounds_are_not_rescued(target):
    with pytest.raises(elf.ElfError, match="outside unsigned 64-bit range"):
        _link(_jump(target))


@pytest.mark.parametrize("shape", ["tuple", "packed", "staged"])
def test_jump_actual_input_routes_match_tiny_retained_image(tmp_path, shape):
    obj = _jump(addend=12)
    expected, offset, place = _link(obj)
    if shape == "tuple":
        actual = elf._link_static_executable_image([obj], base_address=BASE)
    elif shape == "packed":
        payload = struct.pack("<QQq", 0, (2 << 32) | 282, 12)
        section = replace(obj.sections[0], relocations=elf._PackedElfRelocations(payload))
        actual = elf._link_static_executable_image([replace(obj, sections=(section,))], base_address=BASE)
    else:
        path = tmp_path / "jump.o"
        path.write_bytes(elf.emit_relocatable(obj))
        store = ElfInputStore("aarch64-unknown-linux-gnu")
        staged = store.stage(str(path))
        actual = elf._link_static_executable_image([staged], base_address=BASE, _input_store=store)
        assert store.current is None and store.current_index == -1
        store.verify()
    assert bytes(actual) == expected
    _assert_jump_veneer(actual, offset, place, FAR + 12)


def test_jump_undefined_reference_uses_resolved_function_type():
    caller = _jump()
    caller = replace(caller, symbols=caller.symbols[:2] + (
        elf.ElfSymbol("target", elf.SHN_UNDEF, 0, 0, 1, elf.STT_NOTYPE),))
    provider = elf.ElfObject((), (elf.ElfSymbol.null(),
        elf.ElfSymbol("target", elf.SHN_ABS, FAR, 0, 1, elf.STT_FUNC)), elf.EM_AARCH64)
    image = elf.link_static_executable([caller, provider], base_address=BASE)
    _assert_jump_veneer(image, 0x1018, BASE + 0x1018, FAR)


@pytest.mark.parametrize("symbol_type", [elf.STT_NOTYPE, elf.STT_SECTION, elf.STT_OBJECT])
def test_jump_nonfunction_target_keeps_range_failure(symbol_type):
    with pytest.raises(elf.ElfError, match="signed_bits=26"):
        _link(_jump(symbol_type=symbol_type))


def test_jump_unresolved_weak_function_keeps_range_failure():
    obj = _jump(0)
    obj = replace(obj, symbols=obj.symbols[:2] + (
        elf.ElfSymbol("target", elf.SHN_UNDEF, 0, 0, elf.STB_WEAK, elf.STT_FUNC),))
    with pytest.raises(elf.ElfError, match="signed_bits=26"):
        _link(obj)


def test_jump_and_call_share_only_identical_target_addend_slots():
    obj = _jump()
    section = replace(obj.sections[0], data=_words(B, BL, B, RET), relocations=(
        elf.ElfRelocation(0, 2, 282, 0), elf.ElfRelocation(4, 2, 283, 0),
        elf.ElfRelocation(8, 2, 282, 8)))
    image, offset, place = _link(replace(obj, sections=(section,)))
    targets = [_branch_target(image, offset + i * 4, place + i * 4) for i in range(3)]
    assert targets[0] == targets[1] != targets[2]
    assert [struct.unpack_from("<I", image, offset + i * 4)[0] & 0xFC000000
            for i in range(3)] == [B, BL, B]
    assert [struct.unpack_from("<Q", image, address - BASE + 8)[0]
            for address in targets] == [FAR, FAR, FAR + 8]


def test_jump_only_staged_section_is_not_certified_irrelevant(tmp_path):
    path = tmp_path / "jump-only.o"
    path.write_bytes(elf.emit_relocatable(_jump()))
    store = ElfInputStore("aarch64-unknown-linux-gnu")
    staged = store.stage(str(path))
    assert store.sources[0].classification_proof.section_bits == (False,)
    image = elf._link_static_executable_image([staged], base_address=BASE, _input_store=store)
    _assert_jump_veneer(image, 0x1018, BASE + 0x1018, FAR)


def test_jump_first_error_precedes_later_bad_symbol_resolution():
    obj = _jump(FAR + 1)
    section = replace(obj.sections[0], data=_words(B, B, RET), relocations=(
        elf.ElfRelocation(0, 2, 282), elf.ElfRelocation(4, 3, 282)))
    obj = replace(obj, sections=(section, elf.ElfSection(".comment", 1, 0, 1, b"x")),
        symbols=obj.symbols + (elf.ElfSymbol("later", 2, 0, 0, 1, elf.STT_FUNC),))
    with pytest.raises(elf.ElfError, match="unaligned AArch64 branch target") as caught:
        _link(obj)
    assert "relocation_offset=0" in str(caught.value) and "symbol='target'" in str(caught.value)


def test_jump_can_select_reachable_suffix_after_unreachable_prefix():
    image = bytearray(_words(B, RET) + bytes(24))
    arm.apply_branch26_with_veneer(image, 0, 282, FAR, BASE, (-(1 << 27) - 8, 16))
    _assert_jump_veneer(image, 0, BASE, FAR)


def test_jump_unreachable_boundaries_keep_error_and_original_bytes():
    events = []
    class Kind(int):
        def __eq__(self, value):
            events.append(("equal", value))
            return int(self) == value
    class Opaque:
        @property
        def type(self):
            events.append("type")
            return Kind(282)
    assert not elf._aarch64_veneer_branch(Opaque().type, False)
    assert events == ["type", ("equal", 283)]
    events.clear()
    assert elf._aarch64_veneer_branch(Kind(283), True)
    assert events == [("equal", 283)]
    # Actual generic application: a supported custom CALL comparison changes
    # its owning field only after reservation. Dispatch must still be CALL.
    obj = _object()
    row = obj.sections[0].relocations[0]
    armed = [False]
    class MutatingCall(int):
        __hash__ = int.__hash__
        def __eq__(self, value):
            if armed[0] and value == 283:
                object.__setattr__(row, "type", 282)
            return int(self) == value
    class Timing:
        def begin(self, phase):
            if phase == "elf_image_resolve_relocate":
                armed[0] = True
            return 0
        def complete(self, phase, started):
            pass
    object.__setattr__(row, "type", MutatingCall(283))
    linked = elf._link_static_executable_image([obj], base_address=BASE,
                                               coordinator_timing=Timing())
    assert row.type == 282
    assert struct.unpack_from("<I", linked, 0x1018)[0] & 0xFC000000 == BL
    assert struct.unpack_from("<IIQ", linked, 0x1008) == (0x58000050, 0xD61F0200, FAR)
    image = bytearray(_words(B, RET))
    before = bytes(image)
    with pytest.raises(ValueError, match="signed_bits=26"):
        arm.apply_branch26_with_veneer(image, 0, 282, FAR, BASE,
                                      (-(1 << 27) - 8, 1 << 27))
    assert bytes(image) == before


def test_saved_failure_displacement_is_scalar_only_not_a_large_image():
    place, target = 10839612, 359764376
    assert (target - place) // 4 == 87231191
    image = bytearray(_words(B, RET) + bytes(28))
    # P is 4 mod 8, so offset20 gives the required aligned virtual slot.
    # The real failing image size is not an allocation request here.
    with pytest.raises(ValueError, match="invalid AArch64 reserved veneer slot"):
        arm.apply_branch26_with_veneer(image, 0, 282, target, place, (16,))
    # Use a tiny image whose file/virtual bases both preserve ELF 8-byte alignment.
    image = bytearray(_words(NOP, B, RET) + bytes(28))
    arm.apply_branch26_with_veneer(image, 4, 282, target, place, (24,))
    _assert_jump_veneer(image, 4, place, target)
