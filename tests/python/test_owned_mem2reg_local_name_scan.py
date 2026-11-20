"""The full owned mem2reg uses exact conservative raw local-name tokens."""

import re

import pytest

from pcc.ir.optimization import mem2reg as owned
from tests.owned_ir_validation import verify_ir_text


@pytest.mark.parametrize("text", (
    "",
    "%",
    "%%a %a %a.$ %% %a0 %-p\n",
    '  %answer = call i64 @f(ptr c"%literal", i64 %operand) ; %comment',
    '  %"quoted \\22%inside" = add i64 %source, %source ; %comment',
    '  名前 %é.no_asciiname %%ok %oké %ok_after ; %quoted\n',
))
def test_local_names_match_raw_maximal_token_reference(text):
    # The use classifier/final guard deliberately scan quotes and comments,
    # unlike the operand rewriter. Keep first encounter order and no repeats.
    reference = list(dict.fromkeys(re.findall(r"%([A-Za-z0-9_.$-]+)", text)))
    assert owned._local_names(text) == reference


def test_scanner_does_not_index_literal_gaps():
    class CountedText(str):
        def __getitem__(self, index):
            if isinstance(index, int):
                self.character_reads += 1
            return super().__getitem__(index)

    text = CountedText(
        "  %answer = call i64 @ordinary_compiler_operation(i64 %entry_offset, i64 42)\n"
    )
    text.character_reads = 0
    expected = ["answer", "entry_offset"]
    assert owned._local_names(text) == expected
    # At most one subscript per name byte plus its final non-name byte.
    assert text.character_reads <= sum(len(name) + 1 for name in expected)


DIAMOND = """declare void @observe(ptr)
define i64 @merge(i1 %condition, i64 %incoming) {
entry:
  %slot = alloca i64
  %slot.tail = alloca i64
  call void @observe(ptr %slot.tail)
  br i1 %condition, label %left, label %right
left:
  store i64 41, ptr %slot
  br label %join
right:
  store i64 %incoming, ptr %slot
  br label %join
join:
  %read = load i64, ptr %slot
  %read.next = add i64 %read, %incoming
  ret i64 %read.next
}
"""

DIAMOND_OUT = """declare void @observe(ptr)
define i64 @merge(i1 %condition, i64 %incoming) {
entry:
  %slot.tail = alloca i64
  call void @observe(ptr %slot.tail)
  br i1 %condition, label %left, label %right
left:
  br label %join
right:
  br label %join
join:
  %slot.phi = phi i64 [ 41, %left ], [ %incoming, %right ]
  %read.next = add i64 %slot.phi, %incoming
  ret i64 %read.next
}
"""


def test_complete_pass_preserves_prefix_and_escaped_slot():
    verify_ir_text(DIAMOND)
    assert owned.mem2reg_text(DIAMOND) == (DIAMOND_OUT, True)
    verify_ir_text(DIAMOND_OUT)


@pytest.mark.parametrize(("extra_operand_name", "comment"), (
    ("%answer", "\n  ; retain literal %read"),
    ('%"answer %read"', ""),
))
def test_complete_pass_keeps_conservative_removed_reference_guard(extra_operand_name, comment):
    source = f"""define i64 @guard(i64 %value) {{
entry:
  %slot = alloca i64
  store i64 %value, ptr %slot
  %read = load i64, ptr %slot
  {extra_operand_name} = add i64 %read, 1{comment}
  ret i64 {extra_operand_name}
}}
"""
    verify_ir_text(source)
    assert owned.mem2reg_text(source) == (source, False)
    eligible = source.replace(comment, "") if comment else source.replace(extra_operand_name, "%answer")
    out, changed = owned.mem2reg_text(eligible)
    assert changed and "alloca" not in out
    verify_ir_text(out)
