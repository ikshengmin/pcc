"""Exact module-event CFG rewrites over producer-owned operand provenance.

Event indices, including blanks and directives, intentionally define the old
6000/200000 eligibility guards. This is not a machine-code range optimizer.
"""
from __future__ import annotations

from .self_backend_aarch64_semantics import (
    TOKEN_REGISTER, TOKEN_SYMBOL,
    REGISTER_WSP, REGISTER_XZR, REGISTER_WZR,
)


# The context supplies validated record IDs and transactional typed emission.
# No instruction is rendered or parsed by these passes.
def _branch(ctx, record_id):
    opcode = ctx.opcode(record_id)
    tokens = ctx.operands(record_id)
    if opcode == "b" or opcode.startswith("b."):
        if len(tokens) == 1 and tokens[0][0] == TOKEN_SYMBOL and tokens[0][2:] == (0, 0):
            return opcode, tokens[0][1]
    if opcode in ("cbz", "cbnz") and len(tokens) == 2:
        reg, target = tokens
        if (reg[0] == TOKEN_REGISTER and
                (0 <= reg[1] <= 30 or 32 <= reg[1] <= 62 or reg[1] in (REGISTER_WSP, REGISTER_XZR, REGISTER_WZR)) and
                target[0] == TOKEN_SYMBOL and target[2:] == (0, 0)):
            return opcode, target[1]
    return None


def _local(ctx, record_id):
    label = ctx.label(record_id)
    return label if label is not None and ctx.symbol(label).startswith("L_") else None


def _function(ctx, record_id):
    label = ctx.label(record_id)
    if label is None:
        return False
    return not ctx.symbol(label).startswith((" ", ".", "L_"))


def _boundary(ctx, record_id):
    return _local(ctx, record_id) is not None or _function(ctx, record_id) or ctx.is_directive(record_id)


def _retarget(ctx, record_id, target, opcode=None):
    tokens = ctx.operands(record_id)
    tokens[-1] = (TOKEN_SYMBOL, target, 0, 0)
    return ctx.emit(ctx.opcode(record_id) if opcode is None else opcode, tokens)


def thread_trampoline_branches_records(ctx, records, metadata_labels=()):
    trampolines = {}
    for index, record_id in enumerate(records):
        label = _local(ctx, record_id)
        if label is None or ctx.symbol(label).startswith("L_pcc_smap"):
            continue
        body = []
        following = index + 1
        while following < len(records) and not _boundary(ctx, records[following]):
            if not ctx.is_empty(records[following]):
                body.append(records[following])
            following += 1
        if len(body) == 1:
            branch = _branch(ctx, body[0])
            if branch is not None and branch[0] == "b" and branch[1] != label:
                trampolines[label] = branch[1]
    if not trampolines:
        return records
    label_index = {}
    for index, record_id in enumerate(records):
        label = _local(ctx, record_id)
        if label is not None:
            label_index[label] = index
    rewritten = []
    for index, record_id in enumerate(records):
        branch = _branch(ctx, record_id)
        if branch is None:
            rewritten.append(record_id)
            continue
        opcode, target = branch
        resolved = target
        seen = set()
        while resolved in trampolines and resolved not in seen:
            seen.add(resolved)
            resolved = trampolines[resolved]
        limit = 6000 if opcode in ("cbz", "cbnz") else (200000 if opcode.startswith("b.") else 0)
        if (resolved == target or (limit and
                (resolved not in label_index or abs(label_index[resolved] - index) > limit))):
            rewritten.append(record_id)
        else:
            rewritten.append(_retarget(ctx, record_id, resolved))
    referenced = set(metadata_labels)
    for record_id in rewritten:
        branch = _branch(ctx, record_id)
        if branch is not None:
            referenced.add(branch[1])
    out = []
    index = 0
    while index < len(rewritten):
        label = _local(ctx, rewritten[index])
        previous = len(out) - 1
        while previous >= 0 and ctx.is_empty(out[previous]):
            previous -= 1
        falls_through = previous < 0 or not (
            ctx.opcode(out[previous]) in ("b", "br") or ctx.opcode(out[previous]).startswith("ret")
        )
        if label is not None and label in trampolines and label not in referenced and not falls_through:
            index += 1
            while index < len(rewritten) and not _boundary(ctx, rewritten[index]):
                index += 1
            continue
        out.append(rewritten[index])
        index += 1
    return out


_INVERSE = {
    "eq": "ne", "ne": "eq", "lt": "ge", "le": "gt", "gt": "le", "ge": "lt",
    "lo": "hs", "ls": "hi", "hi": "ls", "hs": "lo", "mi": "pl", "pl": "mi",
    "vs": "vc", "vc": "vs",
}


def fold_cond_branch_to_fallthrough_records(ctx, records, eligible_edges):
    label_index = {}
    for index, record_id in enumerate(records):
        label = _local(ctx, record_id)
        if label is not None:
            label_index[label] = index
    out = []
    index = 0
    error = None
    success = None
    while index < len(records):
        record_id = records[index]
        label = _local(ctx, record_id)
        if label is not None:
            if not ctx.symbol(label).startswith("L_pcc_smap"):
                error = None
                success = None
                for source, error_target, success_target in eligible_edges:
                    if label == source:
                        error, success = error_target, success_target
                        break
        elif _function(ctx, record_id):
            error = None
            success = None
        if success is not None and index + 1 < len(records):
            branch = _branch(ctx, record_id)
            alternative = _branch(ctx, records[index + 1])
            if branch is not None and alternative is not None and alternative[0] == "b":
                opcode, target = branch
                inverse = ""
                if opcode in ("cbz", "cbnz"):
                    inverse = "cbnz" if opcode == "cbz" else "cbz"
                elif opcode.startswith("b.") and opcode[2:] in _INVERSE:
                    inverse = "b." + _INVERSE[opcode[2:]]
                following = index + 2
                while following < len(records) and ctx.is_empty(records[following]):
                    following += 1
                other = alternative[1]
                if (inverse and ctx.symbol(target).startswith("L_") and ctx.symbol(other).startswith("L_")
                        and target == success and other == error and other in label_index
                        and abs(label_index[other] - index) <= 200000 and following < len(records)
                        and _local(ctx, records[following]) == target):
                    out.append(_retarget(ctx, record_id, other, inverse))
                    index += 2
                    continue
        out.append(record_id)
        index += 1
    return out


def drop_fallthrough_uncond_branches_records(ctx, records):
    out = []
    for index, record_id in enumerate(records):
        branch = _branch(ctx, record_id)
        if branch is not None and branch[0] == "b" and ctx.symbol(branch[1]).startswith("L_"):
            following = index + 1
            while following < len(records) and ctx.is_empty(records[following]):
                following += 1
            if following < len(records) and _local(ctx, records[following]) == branch[1]:
                continue
        out.append(record_id)
    return out


def drop_unreferenced_empty_local_labels_records(ctx, records, metadata_labels=()):
    referenced = set(metadata_labels)
    for record_id in records:
        branch = _branch(ctx, record_id)
        if branch is not None:
            referenced.add(branch[1])
    out = []
    for index, record_id in enumerate(records):
        label = _local(ctx, record_id)
        if label is not None and label not in referenced and not ctx.symbol(label).startswith("L_pcc_smap"):
            following = index + 1
            while following < len(records) and ctx.is_empty(records[following]):
                following += 1
            if following < len(records) and (_local(ctx, records[following]) is not None or _function(ctx, records[following])):
                continue
        out.append(record_id)
    return out


def thread_trampoline_branches(fragments, semantics, span, metadata_labels=()):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    return ctx.write_span(thread_trampoline_branches_records(ctx, ctx.read_span(span), metadata_labels))


def fold_cond_branch_to_fallthrough(fragments, semantics, span, eligible_edges):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    return ctx.write_span(fold_cond_branch_to_fallthrough_records(ctx, ctx.read_span(span), eligible_edges))


def drop_fallthrough_uncond_branches(fragments, semantics, span):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    ctx.validate_span(span)
    return fragments.drop_fallthrough_branches(span)


def drop_unreferenced_empty_local_labels(fragments, semantics, span, metadata_labels=()):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    return ctx.write_span(drop_unreferenced_empty_local_labels_records(ctx, ctx.read_span(span), metadata_labels))


def run_aarch64_typed_cfg_passes(
    fragments, semantics, span, metadata_labels, cold_fallthrough_edges,
):
    """Final six passes in the original order, over the whole module.

    Proof inputs retain their established module-union ownership and ordering.
    Missing names cannot match emitted labels; do not mutate the symbol table
    merely to record unused proof inputs.
    """
    from .self_backend_aarch64_typed_tlv import elide_dead_tlv_resolver_saves
    from .self_backend_aarch64_typed_pair import pair_adjacent_aarch64_64bit_memory_ops
    metadata_ids = set()
    for name in metadata_labels:
        if name in fragments.symbol_ids:
            metadata_ids.add(fragments.symbol_ids[name])
    edges = []
    for source, error, success in cold_fallthrough_edges:
        source_id = fragments.symbol_ids[source] if source in fragments.symbol_ids else -1
        error_id = fragments.symbol_ids[error] if error in fragments.symbol_ids else -1
        success_id = fragments.symbol_ids[success] if success in fragments.symbol_ids else -1
        edges.append((source_id, error_id, success_id))
    result = thread_trampoline_branches(fragments, semantics, span, metadata_ids)
    result = fold_cond_branch_to_fallthrough(fragments, semantics, result, edges)
    result = drop_fallthrough_uncond_branches(fragments, semantics, result)
    result = drop_unreferenced_empty_local_labels(fragments, semantics, result, metadata_ids)
    result = elide_dead_tlv_resolver_saves(fragments, semantics, result)
    return pair_adjacent_aarch64_64bit_memory_ops(fragments, semantics, result, enabled=True)
