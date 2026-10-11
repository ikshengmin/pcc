"""Pure parser/data controls; no project imports, encoder, linker or native run.

Only whitelisted parser definitions are loaded from source. ELF flags are
fixed data and the encoder exception is a fake boundary. Whole object bytes
and native integration belong to the existing normal CI tests/canary.
"""

import ast
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
from pathlib import Path
import re
import struct

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "pcc/backend/x86_64_asm_driver.py"
BEFORE = Path(__file__).with_name("data") / "x86_64_parse_buffer_before_81776.py.txt"
BEFORE_SHA256 = "e68388f456dcf9036ad0f198b75fe08e09b72e520806b04d457fe5ac57b3ad3f"
PREFIX = ".intel_syntax noprefix\n"

_PARSER_DEFINITIONS = frozenset({
    "_Align", "_Label", "_Data", "_Zero", "_SymbolData", "_SymbolDifference",
    "_Instruction", "_SizeHere", "_SectionPlan", "_SymbolMeta",
    "_parse_int", "_data_items", "_integer_payload", "_symbol_plus_addend",
    "_section_from_directive", "_iter_assembly_lines",
    "_flush_instruction_spans", "_append_instruction_span",
    "_iter_plan_entries", "_instruction_text", "_parse_file",
})
_PARSER_CONSTANTS = frozenset({
    "_SECTION_SPECS", "_ASSEMBLY_LINE_CHUNK_CHARS", "_ASSEMBLY_LINE_BREAKS",
    "_INSTRUCTION_SPAN_BASE", "_INSTRUCTION_SPAN_CODEC",
    "_INSTRUCTION_SPAN_CHUNK_BYTES", "_INSTRUCTION_SPAN_MIN_RUN",
    "_NUMERIC_DATA_BATCH_BYTES",
})


class _ParseError(ValueError):
    """No instruction encoder is imported by the parser-only test boundary."""


def _parser_namespace(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            if node.name in _PARSER_DEFINITIONS:
                selected.append(node)
        elif isinstance(node, ast.Assign):
            if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in _PARSER_CONSTANTS):
                selected.append(node)
    # Deliberately omit every source import, including future annotations.
    namespace = {
        "__name__": __name__, "dataclass": dataclass, "field": field,
        "re": re, "struct": struct, "X86EncodeError": _ParseError,
        "SHF_ALLOC": 2, "SHF_EXECINSTR": 4, "SHF_TLS": 1024, "SHF_WRITE": 1,
        "SHT_NOBITS": 8, "SHT_PROGBITS": 1,
        "STT_FUNC": 2, "STT_NOTYPE": 0, "STT_OBJECT": 1, "STT_TLS": 6,
    }
    isolated = ast.Module(body=selected, type_ignores=[])
    exec(compile(isolated, str(path), "exec"), namespace)
    assert "_measure_sections" not in namespace
    assert "encode_instruction" not in namespace
    assert "_assemble_file" not in namespace
    return namespace


def _assert_frozen_oracle_identity(raw):
    # Git may check this text fixture out as CRLF on Windows. Only that exact
    # newline substitution is ignored; body and all other whitespace stay pinned.
    normalized = raw.replace(b"\r\n", b"\n")
    assert hashlib.sha256(normalized).hexdigest() == BEFORE_SHA256


def _pair():
    _assert_frozen_oracle_identity(BEFORE.read_bytes())
    return _parser_namespace(BEFORE), _parser_namespace(SOURCE)


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_frozen_oracle_identity_accepts_lf_and_crlf(line_ending):
    canonical = BEFORE.read_bytes().replace(b"\r\n", b"\n")
    _assert_frozen_oracle_identity(canonical.replace(b"\n", line_ending))


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("mutation", ["body", "trailing-space", "bare-cr"])
def test_frozen_oracle_identity_rejects_body_and_other_whitespace_changes(line_ending, mutation):
    canonical = BEFORE.read_bytes().replace(b"\r\n", b"\n")
    if mutation == "body":
        changed = canonical.replace(b"pending_data = bytearray()", b"pending_data = bytes()", 1)
    elif mutation == "trailing-space":
        changed = canonical + b" "
    else:
        changed = canonical + b"\r"
    assert changed != canonical
    with pytest.raises(AssertionError):
        _assert_frozen_oracle_identity(changed.replace(b"\n", line_ending))


def _normal(value):
    if isinstance(value, bytearray):
        return ("bytearray", bytes(value))
    if is_dataclass(value):
        return (type(value).__name__, tuple(
            (item.name, _normal(getattr(value, item.name))) for item in fields(value)
        ))
    if isinstance(value, dict):
        return ("dict", tuple((_normal(key), _normal(item))
                              for key, item in value.items()))
    if isinstance(value, list):
        return ("list", tuple(_normal(item) for item in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_normal(item) for item in value))
    return value


_CASES = {
    "empty": PREFIX,
    "empty-section": PREFIX + ".text\n",
    "single-data": PREFIX + ".data\n.byte 7\n",
    "numeric-multichunk": PREFIX + ".data\n" + ".byte 7\n" * 8193,
    "instruction-multichunk": PREFIX + ".text\nprobe:\n" + "  nop\n" * 2053,
    "mixed-records": PREFIX + """.text
probe:
  nop
  nop
  nop
  nop
  nop
.globl probe
.type probe, @function
.size external, 0

  nop
  .byte 1, 2, 3
  .short -1
  .quad external+8
  .long finish-probe
  .float -0.0
  .double 1.25
  .p2align 3
  .zero 2
finish:
  ret
.size probe, .-probe
.data
payload:
  .long 16909060
.section .note.GNU-stack,"",@progbits
""",
    "section-return-without-text-record": PREFIX + """.text
  nop
  nop
  nop
.data
.byte 9
.text
.globl probe
.type probe, @function
.size probe, 6
  nop
  nop
  nop
""",
    "blank-lines-within-batch": PREFIX + ".data\n.byte 1\n\n\r\n.short 2\n",
    "metadata-data-barrier": PREFIX + ".data\n.byte 1\n.globl p\n.byte 2\n",
    "oversized-instruction": PREFIX + ".text\n" + "  nop\n" * 5
        + "mov eax, " + "1" * 65536 + "\n  ret\n",
}
for _count in (1, 2, 3, 4, 5, 6, 1024, 1025, 1026):
    _CASES["run-" + str(_count)] = PREFIX + ".text\n" + "  nop\n" * _count


@pytest.mark.parametrize("name", tuple(_CASES))
@pytest.mark.parametrize("compact", (False, True))
def test_parser_records_match_frozen_before_without_backend_imports(name, compact):
    before, after = _pair()
    text = _CASES[name]
    assert _normal(after["_parse_file"](text, compact_instructions=compact)) == _normal(
        before["_parse_file"](text, compact_instructions=compact)
    )


@pytest.mark.parametrize("numeric_limit", (0, -1, 1, 3, 8, 16, 8192))
@pytest.mark.parametrize("span_limit", (40, 8192))
def test_small_batch_limits_keep_structure_and_payloads(numeric_limit, span_limit):
    before, after = _pair()
    for namespace in (before, after):
        namespace["_NUMERIC_DATA_BATCH_BYTES"] = numeric_limit
        namespace["_INSTRUCTION_SPAN_CHUNK_BYTES"] = span_limit
    text = _CASES["mixed-records"] + ".text\n" + "  nop\n" * 41
    assert _normal(after["_parse_file"](text, compact_instructions=True)) == _normal(
        before["_parse_file"](text, compact_instructions=True)
    )


_ERRORS = (
    "", ".text\n", PREFIX + ".data\n.byte 256,\n",
    PREFIX + ".data\n.short external\n.byte 256\n",
    PREFIX + ".data\n.byte 1\n.float wrong\n",
    PREFIX + ".data\n.byte 1\n.section .unsupported\n",
    PREFIX + ".text\n" + "  nop\n" * 5 + ".p2align 17\n",
    PREFIX + ".text\n" + "  nop\n" * 5 + ".zero -1\n",
    PREFIX + ".section .tbss,\"awT\",@nobits\n.byte 1\n.short external\n",
)


@pytest.mark.parametrize("text", _ERRORS)
def test_first_parse_error_and_scalar_call_order_match_frozen_before(text):
    outcomes = []
    for namespace in _pair():
        scalar_calls = []
        original = namespace["_integer_payload"]
        def observed(value, width, *, owner):
            scalar_calls.append((value, width, owner))
            return original(value, width, owner=owner)
        namespace["_integer_payload"] = observed
        try:
            namespace["_parse_file"](text, compact_instructions=True)
        except Exception as error:
            outcomes.append((type(error).__name__, str(error), scalar_calls))
        else:
            raise AssertionError("invalid source was accepted")
    assert outcomes[0] == outcomes[1]


class _WideOffset(str):
    def find(self, value, start=0, end=None):
        if value == "  nop":
            return 1 << 48
        if end is None:
            return super().find(value, start)
        return super().find(value, start, end)


def test_oversized_span_retains_text_record_without_wrapping():
    before, after = _pair()
    text = _WideOffset(PREFIX + ".text\n  nop")
    old = before["_parse_file"](text, compact_instructions=True)
    new = after["_parse_file"](text, compact_instructions=True)
    assert _normal(new) == _normal(old)
    assert type(new[0][".text"].entries[0]).__name__ == "_Instruction"


class _NoGrowBytearray(bytearray):
    def extend(self, value):
        raise AssertionError("published bytearray must never extend")

    def append(self, value):
        raise AssertionError("published bytearray must never append")

    def __iadd__(self, value):
        raise AssertionError("published bytearray must never grow")


@pytest.mark.parametrize("name", ("single-data", "instruction-multichunk", "mixed-records"))
def test_parser_constructs_completed_buffers_without_bytearray_growth(name):
    before, after = _pair()
    expected = before["_parse_file"](_CASES[name], compact_instructions=True)
    after["bytearray"] = _NoGrowBytearray
    actual = after["_parse_file"](_CASES[name], compact_instructions=True)
    assert _normal(actual) == _normal(expected)
    # The frozen implementation is the negative control for this fake boundary.
    before["bytearray"] = _NoGrowBytearray
    with pytest.raises(AssertionError, match="must never extend"):
        before["_parse_file"](_CASES[name], compact_instructions=True)


def test_published_runs_never_grow_and_pending_tail_stays_private():
    _, namespace = _pair()
    namespace["_INSTRUCTION_SPAN_CHUNK_BYTES"] = 40
    namespace["bytearray"] = _NoGrowBytearray
    entries, pending, published = [], [], []
    for index in range(13):
        namespace["_append_instruction_span"](entries, pending, -index - 1)
        assert len(pending) < 5
        for run, content in published:
            assert bytes(run) == content
        while len(published) < len(entries):
            run = entries[len(published)]
            assert isinstance(run, bytearray) and len(run) == 40
            published.append((run, bytes(run)))
    assert len(entries) == 2 and pending == [-11, -12, -13]
    namespace["_flush_instruction_spans"](entries, pending)
    assert entries[2:] == [-11, -12, -13] and not pending
    assert all(bytes(run) == content for run, content in published)


def test_partial_consume_close_preserves_unconsumed_and_current_buffers():
    outcomes = []
    for namespace in _pair():
        namespace["_INSTRUCTION_SPAN_CHUNK_BYTES"] = 40
        text = PREFIX + ".text\nprobe:\n" + "  nop\n" * 12
        plans, _, _ = namespace["_parse_file"](text, compact_instructions=True)
        section = plans[".text"]
        runs = [entry for entry in section.entries if isinstance(entry, bytearray)]
        originals = [bytes(run) for run in runs]
        iterator = namespace["_iter_plan_entries"](section, consume=True)
        assert type(next(iterator)).__name__ == "_Label"
        assert isinstance(next(iterator), int)
        iterator.close()
        assert [bytes(run) for run in runs] == originals
        assert section.entries[:2] == [None, None]
        outcomes.append((_normal(section), originals))
    assert outcomes[0] == outcomes[1]


def test_full_consume_clears_retained_run_aliases_and_leaves_other_parse_untouched():
    for namespace in _pair():
        namespace["_INSTRUCTION_SPAN_CHUNK_BYTES"] = 40
        text = _CASES["mixed-records"] + ".text\n" + "  nop\n" * 17
        plans, _, _ = namespace["_parse_file"](text, compact_instructions=True)
        other = namespace["_parse_file"](text, compact_instructions=True)
        untouched = _normal(other)
        for section in plans.values():
            runs = [entry for entry in section.entries if isinstance(entry, bytearray)]
            list(namespace["_iter_plan_entries"](section, consume=True))
            assert all(not run for run in runs)
            assert all(entry is None for entry in section.entries)
        assert _normal(other) == untouched


def test_changed_builders_use_literal_bytes_join_and_no_extend_or_inplace_add():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    builders = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in ("_parse_file", "_append_instruction_span", "_flush_instruction_spans")]
    joins = []
    for builder in builders:
        for node in ast.walk(builder):
            if isinstance(node, ast.AugAssign):
                # The byte-count scalar may increase; no buffer may use +=.
                assert isinstance(node.target, ast.Name)
                assert node.target.id == "pending_data_size"
                assert isinstance(node.op, ast.Add)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "extend"
                if node.func.attr == "join":
                    assert isinstance(node.func.value, ast.Constant)
                    assert node.func.value.value == b""
                    joins.append(node)
    assert len(joins) == 2
    parser = next(node for node in builders if node.name == "_parse_file")
    flush = next(node for node in parser.body
                 if isinstance(node, ast.FunctionDef) and node.name == "flush_data")
    assert [argument.arg for argument in flush.args.args] == ["pending"]
    assert ast.unparse(flush.args.args[0].annotation) == "list[bytes]"
    assert not any(isinstance(node, ast.Name) and node.id == "pending_data"
                   for node in ast.walk(flush))
    calls = [node for node in ast.walk(parser)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "flush_data"]
    assert calls
    assert all(len(call.args) == 1 and isinstance(call.args[0], ast.Name)
               and call.args[0].id == "pending_data" for call in calls)
