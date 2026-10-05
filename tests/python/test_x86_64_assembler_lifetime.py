"""Bounded x86 assembly line splitting preserves the owned parser contract."""

from dataclasses import FrozenInstanceError, replace
import re
import builtins
import weakref

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.backend import x86_64_asm_driver as assembler
from pcc.backend.x86_64_encode import X86EncodeError


LINE_ENDINGS = (
    "\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e",
    "\x85", "\u2028", "\u2029",
)
LINE_ENDING_NAMES = (
    "lf", "cr", "crlf", "vt", "ff", "file-separator", "group-separator",
    "record-separator", "next-line", "line-separator", "paragraph-separator",
)
VALID_LINES = (
    ".intel_syntax noprefix",
    ".data",
    ".globl payload",
    ".type payload, @object",
    "payload:",
    "  .byte 1, 2, 255",
    "  .short 4660",
    "  .long -1",
    "  .quad probe+8",
    ".size payload, .-payload",
    ".text",
    ".globl probe",
    ".type probe, @function",
    ".p2align 4, 0x90",
    "probe:",
    "  mov eax, 42",
    "  jmp .Ldone",
    ".Ldone:",
    "  ret",
    ".size probe, .-probe",
    '.section .note.GNU-stack,"",@progbits',
)


def _mixed_lines(lines):
    # Include leading and adjacent boundaries: the parser skips empty lines.
    result = ["\r\n", "\u2028"]
    for index, line in enumerate(lines):
        ending = LINE_ENDINGS[index % len(LINE_ENDINGS)]
        result.extend((line, ending))
        if index % 3 == 0:
            result.append(ending)
    return "".join(result)


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 32])
def test_line_iterator_matches_splitlines_empty_adjacent_and_trailing(
    monkeypatch, chunk_size,
):
    monkeypatch.setattr(assembler, "_ASSEMBLY_LINE_CHUNK_CHARS", chunk_size)
    cases = [
        "", "one", " \t\x00", "\r\r\n", "\r\n\r", "\r\n\n",
        "".join(LINE_ENDINGS),
        "alpha" + "".join(LINE_ENDINGS) + "omega",
    ]
    for ending in LINE_ENDINGS:
        cases.extend((
            ending,
            ending * 2,
            "one" + ending,
            ending + "one",
            "one" + ending + "two",
            "one" + ending * 2 + "two" + ending * 2,
            "  one\t " + ending + " two\x00 ",
        ))
    for text in cases:
        assert list(assembler._iter_assembly_lines(text)) == text.splitlines(), repr(text)


@pytest.mark.parametrize("ending", LINE_ENDINGS, ids=LINE_ENDING_NAMES)
def test_line_endings_at_and_across_chunk_boundaries(monkeypatch, ending):
    chunk_size = 8
    monkeypatch.setattr(assembler, "_ASSEMBLY_LINE_CHUNK_CHARS", chunk_size)
    for prefix_length in (0, 1, 7, 8, 9, 15, 16, 17):
        for suffix in ("", "tail", ending, "tail" + ending):
            text = "x" * prefix_length + ending + suffix
            assert list(assembler._iter_assembly_lines(text)) == text.splitlines(), (
                prefix_length, repr(ending), repr(suffix),
            )


def test_long_line_spans_multiple_chunks_without_content_changes():
    width = assembler._ASSEMBLY_LINE_CHUNK_CHARS
    long_line = " \t" + "x" * (width * 3 + 19) + "\x00\t "
    text = "head\r\n" + long_line + "\u2028" + long_line + "\r\n"
    assert list(assembler._iter_assembly_lines(text)) == ["head", long_line, long_line]
    assert list(assembler._iter_assembly_lines(long_line)) == [long_line]


class _CRLFOnlyText(str):
    """Host simulation of a runtime whose splitlines recognizes CR/LF only."""

    def __getitem__(self, key):
        # Preserve the simulated behavior for both chunks and line[-1], which
        # the iterator can use to query that runtime's separator semantics.
        return type(self)(super().__getitem__(key))

    def splitlines(self, keepends=False):
        result = []
        position = 0
        for match in re.finditer(r"\r\n|\r|\n", self):
            end = match.end() if keepends else match.start()
            result.append(self[position:end])
            position = match.end()
        if position < len(self):
            result.append(self[position:])
        return result


@pytest.mark.parametrize(
    "character", LINE_ENDINGS[3:], ids=LINE_ENDING_NAMES[3:],
)
def test_host_simulated_crlf_splitter_keeps_other_boundary_characters(
    monkeypatch, character,
):
    """This simulation checks compatibility, and makes no native-run claim."""
    monkeypatch.setattr(assembler, "_ASSEMBLY_LINE_CHUNK_CHARS", 8)
    for prefix_length in (7, 15):
        for suffix in ("", "tail", "tail\r\nend\n"):
            text = _CRLFOnlyText("x" * prefix_length + character + suffix)
            expected = text.splitlines()
            assert character in expected[0]
            assert list(assembler._iter_assembly_lines(text)) == expected


class _MeasuredAssemblyText(str):
    """Observe eager splitting while preserving observation through slices."""

    def __new__(cls, value, split_lengths):
        result = super().__new__(cls, value)
        result.split_lengths = split_lengths
        return result

    def __getitem__(self, key):
        result = super().__getitem__(key)
        if isinstance(key, slice):
            return type(self)(result, self.split_lengths)
        return result

    def splitlines(self, keepends=False):
        self.split_lengths.append(len(self))
        return super().splitlines(keepends)


def _many_instruction_lines():
    # Keep the public-parser regression runnable against the original driver,
    # which has neither the new helper nor its chunk-size constant.
    width = getattr(assembler, "_ASSEMBLY_LINE_CHUNK_CHARS", 65536)
    count = (width * 3) // len("  nop\r\n") + 1
    text = ".intel_syntax noprefix\r\n.text\r\nprobe:\r\n"
    text += "  nop\r\n" * count
    text += "  ret\r\n.size probe, .-probe\r\n"
    return text, count


def test_public_parser_never_eagerly_splits_the_whole_assembly():
    text, count = _many_instruction_lines()
    split_lengths = []
    measured = _MeasuredAssemblyText(text, split_lengths)
    plans, order, symbols = assembler._parse_file(measured)
    # This reaches the actual parser. The previous asm_text.splitlines()
    # implementation records one call of len(text), failing the bound.
    assert split_lengths
    bound = getattr(assembler, "_ASSEMBLY_LINE_CHUNK_CHARS", 65536) + 1
    assert max(split_lengths) <= bound
    assert sum(split_lengths) <= len(text) * 2
    assert len(split_lengths) > 1
    assert order == [".text"]
    instructions = [entry for entry in plans[".text"].entries
                    if isinstance(entry, assembler._Instruction)]
    assert len(instructions) == count + 1
    assert instructions[-1].text == "ret"
    assert "probe" in symbols
    assert (plans, order, symbols) == assembler._parse_file(text)


def test_line_iterator_does_not_split_later_chunks_before_consumption():
    text, _count = _many_instruction_lines()
    split_lengths = []
    iterator = assembler._iter_assembly_lines(_MeasuredAssemblyText(text, split_lengths))
    assert iter(iterator) is iterator
    assert split_lengths == []
    assert next(iterator) == ".intel_syntax noprefix"
    assert split_lengths
    assert sum(split_lengths) < len(text)
    assert max(split_lengths) <= assembler._ASSEMBLY_LINE_CHUNK_CHARS + 1


@pytest.mark.parametrize("chunk_size", [1, 7, 64])
def test_mixed_separators_preserve_parsed_records_and_complete_object_bytes(
    monkeypatch, chunk_size,
):
    normal = "\n".join(VALID_LINES) + "\n"
    expected_parse = assembler._parse_file(normal)
    expected_object = elf.emit_relocatable(assembler.assemble_file(normal))
    monkeypatch.setattr(assembler, "_ASSEMBLY_LINE_CHUNK_CHARS", chunk_size)
    mixed = _mixed_lines(VALID_LINES)
    assert assembler._parse_file(mixed) == expected_parse
    actual_object = elf.emit_relocatable(assembler.assemble_file(mixed))
    assert actual_object == expected_object
    obj = elf.parse_relocatable(actual_object)
    data = next(section for section in obj.sections if section.name == ".data")
    assert data.data[:9] == b"\x01\x02\xff\x34\x12\xff\xff\xff\xff"
    assert len(data.relocations) == 1


@pytest.mark.parametrize("lines,expected", [
    ((".text", "probe:", "  ret"),
     "owned x86 assembly must begin with .intel_syntax noprefix"),
    ((".intel_syntax noprefix", ".data", "  nop"),
     "instruction outside .text: 'nop'"),
    ((".intel_syntax noprefix", ".text", ".p2align 17"),
     ".p2align outside finite range: '.p2align 17'"),
    ((".intel_syntax noprefix", ".data", ".byte 256"),
     ".byte value 256 does not fit 8 bits"),
    ((".intel_syntax noprefix", ".text", ".unknown 7"),
     "directive not proven: '.unknown 7'"),
    ((".intel_syntax noprefix", ".text", "probe:", "  nop", "probe:", "  ret"),
     "duplicate assembly label 'probe'"),
])
def test_invalid_mixed_separators_preserve_exact_exception_and_diagnostic(
    monkeypatch, lines, expected,
):
    monkeypatch.setattr(assembler, "_ASSEMBLY_LINE_CHUNK_CHARS", 7)
    errors = []
    for text in ("\n".join(lines) + "\n", _mixed_lines(lines)):
        with pytest.raises(X86EncodeError) as caught:
            assembler.assemble_file(text)
        errors.append((type(caught.value), str(caught.value)))
    assert errors == [(X86EncodeError, expected), (X86EncodeError, expected)]


@pytest.mark.parametrize("record_type,field,value,changed", [
    (assembler._Instruction, "text", "nop", "ret"),
    (assembler._Label, "name", "probe", "other"),
])
def test_compact_frozen_records_preserve_value_and_immutability(
    record_type, field, value, changed,
):
    record = record_type(value)
    equivalent = record_type(**{field: value})
    assert record == equivalent
    assert hash(record) == hash(equivalent)
    assert record != record_type(changed)
    assert replace(record, **{field: changed}) == record_type(changed)
    assert not hasattr(record, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(record, field, changed)
    assert getattr(record, field) == value


def test_compact_symbol_metadata_preserves_mutable_fields_and_equality():
    record = assembler._SymbolMeta()
    assert record == assembler._SymbolMeta(False, elf.STT_NOTYPE, None)
    assert not hasattr(record, "__dict__")
    record.global_ = True
    record.type = elf.STT_FUNC
    record.size = 6
    assert record == assembler._SymbolMeta(True, elf.STT_FUNC, 6)
    assert replace(record, size=9) == assembler._SymbolMeta(True, elf.STT_FUNC, 9)
    with pytest.raises(TypeError):
        hash(record)
    with pytest.raises(AttributeError):
        record.unplanned_field = 1


def test_owned_encoding_releases_prior_instruction_records(monkeypatch):
    text = '''.intel_syntax noprefix
.text
.globl probe
.type probe, @function
probe:
  call external
  jmp .Ldone
.Ldone:
  ret
.size probe, .-probe
.data
payload:
  .quad probe
'''
    expected = elf.emit_relocatable(assembler.assemble_file(text))
    references = []
    class ObservedInstruction(assembler._Instruction):
        pass
    parse = assembler._parse_file
    def observed_parse(text):
        plans, order, symbols = parse(text)
        for plan in plans.values():
            for index, entry in enumerate(plan.entries):
                if isinstance(entry, assembler._Instruction):
                    record = ObservedInstruction(entry.text)
                    references.append(weakref.ref(record))
                    plan.entries[index] = record
        return plans, order, symbols
    encode = assembler.encode_instruction
    calls = 0
    def observed_encode(*args, **kwargs):
        nonlocal calls
        # The first complete pass measures all instructions. In the second
        # pass, already-encoded records have no remaining consumer.
        index = calls - len(references)
        if index > 0:
            assert all(reference() is None for reference in references[:index])
        calls += 1
        return encode(*args, **kwargs)
    monkeypatch.setattr(assembler, '_parse_file', observed_parse)
    monkeypatch.setattr(assembler, 'encode_instruction', observed_encode)
    actual = elf.emit_relocatable(assembler.assemble_file(text))
    assert calls == len(references) * 2
    assert all(reference() is None for reference in references)
    assert actual == expected


def test_owned_encoding_releases_copied_stack_map_payload(monkeypatch):
    from pcc.backend import x86_64_asm_driver as assembler
    from pcc.backend import self_backend_precise_stackmaps as maps
    from pcc.backend import self_backend_x86_64_linux as emitter
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.target_objects import emit_indexed_assembly
    from pcc.backend.elf_x86_64 import emit_relocatable
    from tests.python.test_precise_stackmap_abi import _target_final_stackmap_ir

    plans = []
    text = emit_indexed_assembly(
        parse_self_backend_module(_target_final_stackmap_ir('x86_64-unknown-linux-gnu')),
        stack_map_plans_out=plans,
    )
    keywords = {'function_symbol': emitter._asm_symbol, 'block_label': emitter._block_label}
    expected = emit_relocatable(assembler.assemble_file_with_stack_maps(text, plans, **keywords))
    released = []
    payload_sizes = []
    class TrackedPayload(bytes):
        def __del__(self):
            released.append(True)
    build = maps.build_x86_64_stack_map_payload
    def tracked_build(*args, **kwargs):
        packed, relocations = build(*args, **kwargs)
        payload_sizes.append(len(packed))
        return TrackedPayload(packed), relocations
    make_object = assembler.ElfObject
    def checked_object(*args, **kwargs):
        # Only copied immutable section bytes are needed for object validation;
        # the original packed bytes and the consumed _Data must be retired.
        assert released == [True]
        return make_object(*args, **kwargs)
    def checked_bytes(value):
        if isinstance(value, bytearray) and payload_sizes and len(value) == payload_sizes[0]:
            assert released == [True], 'packed payload overlaps its final section copy'
        return bytes(value)
    monkeypatch.setattr(assembler, 'bytes', checked_bytes, raising=False)
    monkeypatch.setattr(maps, 'build_x86_64_stack_map_payload', tracked_build)
    monkeypatch.setattr(assembler, 'ElfObject', checked_object)
    actual = emit_relocatable(assembler.assemble_file_with_stack_maps(text, plans, **keywords))
    assert actual == expected


def test_compact_pending_relocation_preserves_frozen_value_contract():
    record = assembler._PendingRelocation('.text', 4, 'target', 2, -4)
    assert record == assembler._PendingRelocation('.text', 4, 'target', 2, -4)
    assert hash(record) == hash(assembler._PendingRelocation('.text', 4, 'target', 2, -4))
    assert replace(record, addend=8) == assembler._PendingRelocation('.text', 4, 'target', 2, 8)
    assert not hasattr(record, '__dict__')
    with pytest.raises(FrozenInstanceError):
        record.offset = 9
    assert record.offset == 4


def test_undefined_symbol_discovery_does_not_copy_all_defined_names(monkeypatch):
    text = '.intel_syntax noprefix\n.text\n.globl probe\n.type probe, @function\nprobe:\n'
    text += ''.join('.Llabel_' + str(index) + ':\n' for index in range(1024))
    text += '  call external_z\n  ret\n.size probe, .-probe\n.type external_a, @function\n'
    expected = elf.emit_relocatable(assembler.assemble_file(text))
    def bounded_set(values=()):
        # Publication should allocate for the few undefined names, never copy
        # the complete parser metadata or measured-label dictionary into a set.
        if isinstance(values, dict) and len(values) >= 128:
            raise AssertionError('copied complete defined-symbol map of ' + str(len(values)))
        return builtins.set(values)
    monkeypatch.setattr(assembler, 'set', bounded_set, raising=False)
    obj = assembler.assemble_file(text)
    assert [symbol.name for symbol in obj.symbols if symbol.name and symbol.section_index == 0] == [
        'external_a', 'external_z',
    ]
    assert elf.emit_relocatable(obj) == expected
