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
    def observed_parse(text, **kwargs):
        plans, order, symbols = parse(text, **kwargs)
        for plan in plans.values():
            plan.entries = list(assembler._iter_plan_entries(plan))
            for index, entry in enumerate(plan.entries):
                if isinstance(entry, (assembler._Instruction, int)):
                    record = ObservedInstruction(assembler._instruction_text(entry, plan.source_text))
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


def test_temporary_labels_do_not_allocate_default_symbol_metadata():
    count = 4096
    text = '.intel_syntax noprefix\n.text\n.globl probe\n.type probe, @function\nprobe:\n'
    text += ''.join('.Ltemporary_' + str(index) + ':\n' for index in range(count))
    text += '  ret\n.size probe, .-probe\n'
    plans, order, symbols = assembler._parse_file(text)
    # Parser metadata should scale with declarations, while every label must
    # remain available for duplicate checks and final branch/map resolution.
    metadata_count = len(symbols)
    assert metadata_count == 1
    assert set(symbols) == {'probe'}
    labels, sizes = assembler._measure_sections(plans, order, symbols)
    assert len(labels) == count + 1
    assert sizes['.text'] == 1
    assert symbols['probe'] == assembler._SymbolMeta(True, elf.STT_FUNC, 1)
    obj = assembler.assemble_file(text)
    assert [symbol.name for symbol in obj.symbols] == ['', 'probe']


@pytest.mark.parametrize('declarations_first', [True, False])
def test_sparse_label_metadata_preserves_declarations_and_publication(declarations_first):
    declarations = '''.type local_fn, @function
.size local_fn, 1
.globl global_fn
.type global_fn, @function
.size global_fn, 1
.globl entry
.type entry, @function
.size sized_target, 8
.type typed_target, @object
.type external_called, @function
.globl external_declared
.type external_typed, @function
'''
    body = '''.text
local_fn:
  ret
global_fn:
  ret
entry:
  call local_fn
  call global_fn
  call external_called
  jmp .Lend
.Lend:
  ret
.size entry, .-entry
.data
plain_target:
  .quad 0
sized_target:
  .quad 0
typed_target:
  .quad 0
references:
  .quad plain_target
  .quad external_data
'''
    text = '.intel_syntax noprefix\n.text\n'
    text += declarations + body if declarations_first else body + declarations
    _plans, _order, metadata = assembler._parse_file(text)
    assert set(metadata) == {
        'local_fn', 'global_fn', 'entry', 'sized_target', 'typed_target',
        'external_called', 'external_declared', 'external_typed',
    }
    obj = assembler.assemble_file(text)
    symbols = {symbol.name: symbol for symbol in obj.symbols if symbol.name}
    assert set(symbols) == set(metadata) | {'plain_target', 'external_data'}
    assert symbols['local_fn'].binding == elf.STB_LOCAL
    assert symbols['local_fn'].type == elf.STT_FUNC
    assert symbols['local_fn'].size == 1
    assert symbols['global_fn'].binding == elf.STB_GLOBAL
    assert symbols['entry'].size == 21
    assert symbols['sized_target'].size == 8
    for name in ('plain_target', 'sized_target', 'typed_target'):
        assert symbols[name].type == elf.STT_OBJECT
        assert symbols[name].binding == elf.STB_LOCAL
    for name in ('external_called', 'external_declared', 'external_typed', 'external_data'):
        assert symbols[name].section_index == 0
        assert symbols[name].binding == elf.STB_GLOBAL
    assert symbols['external_called'].type == elf.STT_FUNC
    assert symbols['external_typed'].type == elf.STT_FUNC
    text_section = next(section for section in obj.sections if section.name == '.text')
    assert [obj.symbols[relocation.symbol_index].name for relocation in text_section.relocations] == [
        'global_fn', 'external_called',
    ]
    kept = assembler.assemble_file_keeping_labels(text, ('.Lend',))
    assert {symbol.name for symbol in kept.symbols if symbol.name} == set(symbols) | {'.Lend'}
    assert elf.parse_relocatable(elf.emit_relocatable(obj)) == obj


@pytest.mark.parametrize(('body', 'diagnostic'), [
    ('.text\n.Lsame:\n.Lsame:\n', "duplicate assembly label '.Lsame'"),
    ('.text\nsame:\n.data\nsame:\n', "duplicate assembly label 'same'"),
    ('.text\n.size missing, 1\n', "assembly metadata names symbols without definitions: ['missing']"),
    ('.text\n.size missing, .-missing\n', ".size references non-local symbol 'missing'"),
    ('.text\nprobe:\n ret\n.size probe, 1\n.size probe, 2\n', "conflicting .size directives for 'probe'"),
])
def test_sparse_label_metadata_preserves_definition_errors(body, diagnostic):
    with pytest.raises(X86EncodeError) as error:
        assembler.assemble_file('.intel_syntax noprefix\n' + body)
    assert str(error.value) == diagnostic


def test_sizing_memo_preserves_real_pc_branches_relocations_and_pass_reset(monkeypatch):
    text = '''.intel_syntax noprefix
.text
.globl probe
.type probe, @function
probe:
.Lback:
  call external
  call external
  jmp .Lforward
  jmp .Lforward
.Lforward:
  jne .Lback
  jne .Lback
  ret
.size probe, .-probe
'''
    original = assembler.encode_instruction
    calls = []
    def tracked(line, *, pc, labels, section_name, operand_cache=None):
        calls.append((line, pc, bool(labels)))
        return original(
            line, pc=pc, labels=labels, section_name=section_name,
            operand_cache=operand_cache,
        )
    monkeypatch.setattr(assembler, 'encode_instruction', tracked)
    first = assembler.assemble_file(text)
    section = next(section for section in first.sections if section.name == '.text')
    assert section.data.hex() == (
        'e800000000e800000000e905000000e900000000'
        '0f85e6ffffff0f85e0ffffffc3'
    )
    assert [(rel.offset, first.symbols[rel.symbol_index].name, rel.addend)
            for rel in section.relocations] == [(1, 'external', -4), (6, 'external', -4)]
    assert [pc for line, pc, final in calls if final and line == 'jmp .Lforward'] == [10, 15]
    assert [pc for line, pc, final in calls if final and line == 'jne .Lback'] == [20, 26]
    assert len([call for call in calls if not call[2]]) == 4
    assert len([call for call in calls if call[2]]) == 7
    calls.clear()
    second = assembler.assemble_file(text)
    assert elf.emit_relocatable(second) == elf.emit_relocatable(first)
    assert len([call for call in calls if not call[2]]) == 4


def test_sizing_memo_does_not_skip_final_branch_range_validation():
    # Measurement is sparse; final encoding rejects the first jump before any
    # multi-GiB zero payload can be allocated.
    text = '''.intel_syntax noprefix
.text
probe:
  jne .Lfar
  jne .Lfar
  .zero 2147483648
.Lfar:
  ret
'''
    with pytest.raises(X86EncodeError, match='outside rel32 range'):
        assembler.assemble_file(text)


def test_sizing_memo_repeated_invalid_instruction_still_fails_first(monkeypatch):
    original = assembler.encode_instruction
    invalid_calls = []
    def tracked(line, **kwargs):
        if line == 'vzeroupper':
            invalid_calls.append(line)
        return original(line, **kwargs)
    monkeypatch.setattr(assembler, 'encode_instruction', tracked)
    text = '.intel_syntax noprefix\n.text\nprobe:\n ret\n ret\n vzeroupper\n vzeroupper\n'
    for expected_count in (1, 2):
        with pytest.raises(X86EncodeError, match='not proven'):
            assembler.assemble_file(text)
        assert len(invalid_calls) == expected_count


@pytest.mark.parametrize(('entry_limit', 'byte_limit', 'instructions', 'expected_calls'), [
    (2, 1_048_576, ['mov eax, 1', 'mov eax, 2', 'mov eax, 1', 'mov eax, 3', 'mov eax, 2'], 4),
    (4096, 48, ['call α', 'call β', 'call α', 'call γ', 'call α'], 4),
    (4096, 20, ['call α', 'call α', 'ret', 'ret'], 3),
])
def test_sizing_memo_bounds_entries_and_unicode_key_storage(
    monkeypatch, entry_limit, byte_limit, instructions, expected_calls,
):
    monkeypatch.setattr(assembler, '_INSTRUCTION_SIZE_CACHE_MAX_ENTRIES', entry_limit)
    monkeypatch.setattr(assembler, '_INSTRUCTION_SIZE_CACHE_MAX_KEY_BYTES', byte_limit)
    original = assembler.encode_instruction
    calls = []
    def tracked(line, **kwargs):
        calls.append(line)
        return original(line, **kwargs)
    monkeypatch.setattr(assembler, 'encode_instruction', tracked)
    text = '.intel_syntax noprefix\n.text\n' + '\n'.join(instructions) + '\n'
    plans, order, symbols = assembler._parse_file(text, compact_instructions=True)
    _, sizes = assembler._measure_sections(plans, order, symbols)
    assert len(calls) == expected_calls
    expected_size = sum(len(original(line, pc=0, labels={}, section_name='.text').code)
                        for line in instructions)
    assert sizes['.text'] == expected_size


def test_sizing_memo_oversized_miss_does_not_retain_encoded_graph(monkeypatch):
    monkeypatch.setattr(assembler, '_INSTRUCTION_SIZE_CACHE_MAX_KEY_BYTES', 256)
    giant = 'call ' + 'x' * (assembler._INSTRUCTION_SPAN_BASE + 17)
    original = assembler.encode_instruction
    giant_results = []
    giant_calls = []
    def tracked(line, **kwargs):
        if line == 'mov eax, 42':
            assert giant_results and all(ref() is None for ref in giant_results)
        result = original(line, **kwargs)
        if line == giant:
            giant_calls.append(line)
            giant_results.append(weakref.ref(result))
        return result
    monkeypatch.setattr(assembler, 'encode_instruction', tracked)
    text = '.intel_syntax noprefix\n.text\nret\n' + giant + '\nret\nret\n' + giant + '\nret\nmov eax, 42\n'
    plans, order, symbols = assembler._parse_file(text, compact_instructions=True)
    assert any(isinstance(entry, assembler._Instruction) and entry.text == giant
               for entry in plans['.text'].entries)
    _, sizes = assembler._measure_sections(plans, order, symbols)
    assert sizes['.text'] == 19
    assert len(giant_calls) == 2
    assert all(ref() is None for ref in giant_results)


def test_sizing_memo_accepts_zero_length_and_bypasses_string_subclasses(monkeypatch):
    class ObservedText(str):
        strips = 0
        def strip(self, *args):
            self.strips += 1
            return super().strip(*args)
        def __hash__(self):
            raise AssertionError('custom text must not become a memo key')
    special = ObservedText('ret')
    section = assembler._SectionPlan('.text', elf.SHT_PROGBITS, elf.SHF_ALLOC, 1)
    section.entries = [assembler._Instruction(''), assembler._Instruction(''),
                       assembler._Instruction(special), assembler._Instruction(special)]
    original = assembler.encode_instruction
    empty_calls = []
    def tracked(line, **kwargs):
        if not line:
            empty_calls.append(line)
        return original(line, **kwargs)
    monkeypatch.setattr(assembler, 'encode_instruction', tracked)
    _, sizes = assembler._measure_sections({'.text': section}, ['.text'], {})
    assert sizes['.text'] == 2
    assert empty_calls == ['']
    assert special.strips == 2


def test_sizing_memo_owned_native_calls_and_backward_branch_execute(tmp_path):
    import platform
    import subprocess
    import sys

    if sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        pytest.skip('native x86_64 Linux execution required')
    entry = assembler.assemble_file('''.intel_syntax noprefix
.text
.globl _start
.type _start, @function
_start:
  xor edi, edi
  call increment
  call increment
  mov ecx, 2
.Lloop:
  add edi, 1
  sub ecx, 1
  jne .Lloop
  add edi, 38
  mov eax, 60
  syscall
.size _start, .-_start
''')
    function = assembler.assemble_file('''.intel_syntax noprefix
.text
.globl increment
.type increment, @function
increment:
  add edi, 1
  ret
.size increment, .-increment
''')
    image = elf.link_static_executable([entry, function])
    elf.parse_static_executable(image)
    executable = tmp_path / 'sizing-memo-native'
    executable.write_bytes(image)
    executable.chmod(0o755)
    completed = subprocess.run([str(executable)], capture_output=True, timeout=5)
    assert completed.returncode == 42, (completed.returncode, completed.stdout, completed.stderr)


def test_packed_instruction_spans_bound_storage_and_preserve_entry_order():
    text, count = _many_instruction_lines()
    plans, order, symbols = assembler._parse_file(text, compact_instructions=True)
    section = plans['.text']
    runs = [entry for entry in section.entries if isinstance(entry, bytearray)]
    assert runs
    assert all(0 < len(run) <= assembler._INSTRUCTION_SPAN_CHUNK_BYTES for run in runs)
    boxed = [entry for entry in section.entries if isinstance(entry, int)]
    assert len(boxed) < assembler._INSTRUCTION_SPAN_MIN_RUN
    assert sum(map(len, runs)) == (count + 1 - len(boxed)) * 8
    projected = list(assembler._iter_plan_entries(section))
    instructions = [assembler._instruction_text(entry, section.source_text)
                    for entry in projected if isinstance(entry, int)]
    assert instructions == ['nop'] * count + ['ret']
    # Both ordinary records and instructions retain their source order.
    assert isinstance(projected[0], assembler._Label)
    assert isinstance(projected[-1], assembler._SizeHere)
    labels, sizes = assembler._measure_sections(plans, order, symbols)
    assert sizes['.text'] == count + 1
    assert labels['probe'] == ('.text', 0)


@pytest.mark.parametrize('text', ['\n'.join(VALID_LINES), _mixed_lines(VALID_LINES)])
def test_packed_span_object_matches_unpacked_parser_on_same_input(monkeypatch, text):
    actual = elf.emit_relocatable(assembler.assemble_file(text))
    parse = assembler._parse_file
    def unpacked(source, **kwargs):
        return parse(source, compact_instructions=False)
    monkeypatch.setattr(assembler, '_parse_file', unpacked)
    expected = elf.emit_relocatable(assembler.assemble_file(text))
    assert actual == expected


def test_packed_span_runs_retire_during_final_encoding(monkeypatch):
    monkeypatch.setattr(assembler, '_INSTRUCTION_SPAN_CHUNK_BYTES', 40)
    text = '.intel_syntax noprefix\n.text\nprobe:\n' + '  nop\n' * 19 + '  ret\n'
    parse = assembler._parse_file
    measure = assembler._measure_sections
    encode = assembler.encode_instruction
    runs = []
    measured = False
    final_calls = 0
    def tracked_parse(source, **kwargs):
        result = parse(source, **kwargs)
        runs.extend(entry for plan in result[0].values() for entry in plan.entries
                    if isinstance(entry, bytearray))
        return result
    def tracked_measure(*args, **kwargs):
        nonlocal measured
        result = measure(*args, **kwargs)
        assert all(len(run) == 40 for run in runs)
        measured = True
        return result
    def tracked_encode(*args, **kwargs):
        nonlocal final_calls
        if measured:
            assert all(not run for run in runs[:final_calls // 5])
            final_calls += 1
        return encode(*args, **kwargs)
    monkeypatch.setattr(assembler, '_parse_file', tracked_parse)
    monkeypatch.setattr(assembler, '_measure_sections', tracked_measure)
    monkeypatch.setattr(assembler, 'encode_instruction', tracked_encode)
    obj = assembler.assemble_file(text)
    assert final_calls == 20
    assert len(runs) == 4 and all(not run for run in runs)
    assert obj.sections[0].data == b'\x90' * 19 + b'\xc3'


def test_packed_span_corruption_is_rejected():
    section = assembler._SectionPlan('.text', elf.SHT_PROGBITS, elf.SHF_ALLOC, 1)
    section.entries = [bytearray(b'\0')]
    with pytest.raises(X86EncodeError, match='truncated compact instruction span run'):
        list(assembler._iter_plan_entries(section))
    section.entries = [bytearray(assembler._INSTRUCTION_SPAN_CODEC.pack(0))]
    with pytest.raises(X86EncodeError, match='invalid compact instruction record'):
        assembler._measure_sections({'.text': section}, ['.text'], {})


def test_packed_span_signed_width_preserves_boundary():
    entries = []
    assembler._append_instruction_span(entries, -(1 << 63))
    assembler._append_instruction_span(entries, -1)
    assembler._append_instruction_span(entries, -7)
    assembler._append_instruction_span(entries, -8)
    assembler._append_instruction_span(entries, -9)
    assert len(entries) == 1 and isinstance(entries[0], bytearray)
    section = assembler._SectionPlan('.text', elf.SHT_PROGBITS, elf.SHF_ALLOC, 1)
    section.entries = entries
    assert list(assembler._iter_plan_entries(section)) == [-(1 << 63), -1, -7, -8, -9]


def test_packed_span_native_mixed_runs_calls_and_branches_execute(tmp_path, monkeypatch):
    # Exercise packed runs plus boxed short tails in the existing owned-link
    # witness, including external calls and a backward conditional branch.
    monkeypatch.setattr(assembler, '_INSTRUCTION_SPAN_CHUNK_BYTES', 40)
    test_sizing_memo_owned_native_calls_and_backward_branch_execute(tmp_path)


@pytest.mark.parametrize('run_length', [1, 2, 3, 4, 5, 1024, 1025, 1026])
def test_label_dense_span_storage_never_inflates_host_representation(monkeypatch, run_length):
    import sys

    text = '.intel_syntax noprefix\n.text\n'
    for index in range(12):
        text += '.Lblock' + str(index) + ':\n' + '  nop\n' * run_length
    compact, order, _ = assembler._parse_file(text, compact_instructions=True)
    with monkeypatch.context() as context:
        # This is the original compact parser representation, with identical
        # source spans and label objects. It is not the larger text-record path.
        context.setattr(assembler, '_append_instruction_span', lambda entries, span: entries.append(span))
        boxed, boxed_order, _ = assembler._parse_file(text, compact_instructions=True)
    assert order == boxed_order
    def retained(plans):
        return sum(sys.getsizeof(plan.entries) + sum(sys.getsizeof(entry) for entry in plan.entries)
                   for plan in plans.values())
    assert retained(compact) <= retained(boxed)
    for name in order:
        assert list(assembler._iter_plan_entries(compact[name])) == boxed[name].entries
        runs = [entry for entry in compact[name].entries if isinstance(entry, bytearray)]
        if run_length < assembler._INSTRUCTION_SPAN_MIN_RUN:
            assert not runs
            assert retained(compact) == retained(boxed)
        else:
            assert runs and all(len(run) <= 8192 for run in runs)
    assert elf.emit_relocatable(assembler.assemble_file(text))
