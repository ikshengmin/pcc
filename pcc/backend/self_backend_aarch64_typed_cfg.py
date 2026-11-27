"""Exact module-event CFG rewrites over producer-owned operand provenance.

Event indices, including blanks and directives, intentionally define the old
6000/200000 eligibility guards. This is not a machine-code range optimizer.
"""
from __future__ import annotations

from .self_backend_aarch64_fragments import EMISSION_RECORD_LABEL
from .self_backend_aarch64_semantics import (
    TOKEN_IMMEDIATE, TOKEN_REGISTER, TOKEN_SYMBOL, OPCODE_NAMES,
    REGISTER_WSP, REGISTER_XZR, REGISTER_WZR,
)


# The context supplies validated record IDs and transactional typed emission.
# No instruction is rendered or parsed by these passes.
def _branch(ctx, record_id):
    # Cached for records older than the pass (TypedPassContext.decoded_limit).
    if 0 <= record_id < ctx.decoded_limit:
        cache = ctx.branches
        if record_id in cache:
            return cache[record_id]
        decoded = _decode_branch(ctx, record_id)
        cache[record_id] = decoded
        return decoded
    return _decode_branch(ctx, record_id)


def _decode_branch(ctx, record_id):
    opcode = ctx.opcode(record_id)
    # opcode() has validated this record; only branch operands are read.
    if opcode != "b" and not opcode.startswith("b.") and opcode not in ("cbz", "cbnz", "tbz", "tbnz"):
        return None
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
    if opcode in ("tbz", "tbnz") and len(tokens) == 3:
        reg, bit, target = tokens
        if (reg[0] == TOKEN_REGISTER and (0 <= reg[1] <= 30 or 32 <= reg[1] <= 62)
                and bit[0] == TOKEN_IMMEDIATE
                and target[0] == TOKEN_SYMBOL and target[2:] == (0, 0)):
            return opcode, target[1]
    return None


def _local(ctx, record_id):
    if 0 <= record_id < ctx.decoded_limit:
        cache = ctx.local_labels
        if record_id in cache:
            return cache[record_id]
        # The shared context has already validated this immutable record;
        # only the family lane is needed to rule out a label.
        family = ctx.fragments.records.get_unchecked(record_id * 4 + 1)
        decoded = _decode_local(ctx, record_id) if family == EMISSION_RECORD_LABEL else None
        cache[record_id] = decoded
        return decoded
    return _decode_local(ctx, record_id)


def _decode_local(ctx, record_id):
    label = ctx.label(record_id)
    return label if label is not None and ctx.symbol(label).startswith("L_") else None


def _function(ctx, record_id):
    label = ctx.label(record_id)
    if label is None:
        return False
    return not ctx.symbol(label).startswith((" ", ".", "L_"))


def _boundary(ctx, record_id):
    if 0 <= record_id < ctx.decoded_limit:
        cache = ctx.boundaries
        if record_id in cache:
            return cache[record_id]
        decoded = _decode_boundary(ctx, record_id)
        cache[record_id] = decoded
        return decoded
    return _decode_boundary(ctx, record_id)


def _decode_boundary(ctx, record_id):
    return _local(ctx, record_id) is not None or _function(ctx, record_id) or ctx.is_directive(record_id)


def _retarget(ctx, record_id, target, opcode=None):
    tokens = ctx.operands(record_id)
    tokens[-1] = (TOKEN_SYMBOL, target, 0, 0)
    return ctx.emit(ctx.opcode(record_id) if opcode is None else opcode, tokens)


# Every candidate is selected from producer provenance, not decoded
# machine words. Unknown/future direct branches remain in the set via the
# same spelling predicate as _decode_branch.
_CFG_BRANCH_OPCODE_IDS = frozenset(
    index for index, name in enumerate(OPCODE_NAMES)
    if name == "b" or name.startswith("b.") or name in ("cbz", "cbnz", "tbz", "tbnz")
)


def _host_cfg_candidates(ctx, records):
    """Ordered event/branch positions for the immutable compact host owner."""
    headers = ctx.semantics.headers
    if ctx.decoded_limit == 0 or headers._address != 0:
        return None
    from .self_backend_aarch64_fragments import AArch64EmissionFragments
    from .self_backend_aarch64_semantics import AArch64InstructionSemantics
    if (type(headers._values) is not list
            or type(ctx.fragments) is not AArch64EmissionFragments
            or type(ctx.semantics) is not AArch64InstructionSemantics):
        return None
    # Non-instruction event headers have opcode zero. This is a superset
    # of local labels; leave the existing label-family/name tests intact.
    events = []
    branches = []
    raw = headers._values
    for index, record_id in enumerate(records):
        opcode = raw[record_id * 4]
        if opcode == 0:
            events.append(index)
        elif opcode in _CFG_BRANCH_OPCODE_IDS:
            branches.append(index)
    return events, branches


def _host_static_metadata(metadata_labels) -> bool:
    # Public proof iterables can execute caller code during iteration or
    # hashing. Only the producer's integer metadata inventory (or the
    # empty default tuple) can skip those dense record visits.
    if type(metadata_labels) is not set and type(metadata_labels) is not tuple:
        return False
    for label in metadata_labels:
        if type(label) is not int:
            return False
    return True


def thread_trampoline_branches_records(ctx, records, metadata_labels=()):
    trampolines = {}
    label_index = {}
    for index, record_id in enumerate(records):
        label = _local(ctx, record_id)
        if label is None:
            continue
        label_index[label] = index
        if ctx.symbol(label).startswith("L_pcc_smap"):
            continue
        body = []
        following = index + 1
        while following < len(records) and not _boundary(ctx, records[following]):
            if not ctx.is_empty(records[following]):
                body.append(records[following])
                # Only a single nonempty instruction can be a trampoline.
                if len(body) > 1:
                    break
            following += 1
        if len(body) == 1:
            branch = _branch(ctx, body[0])
            if branch is not None and branch[0] == "b" and branch[1] != label:
                trampolines[label] = branch[1]
    if not trampolines:
        return records
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
        limit = 6000 if opcode in ("cbz", "cbnz", "tbz", "tbnz") else (200000 if opcode.startswith("b.") else 0)
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
        if label is not None and label in trampolines and label not in referenced:
            # Match the text owner's short circuit: only a removable trampoline
            # needs fallthrough proof, not every retained empty event.
            previous = len(out) - 1
            while previous >= 0 and ctx.is_empty(out[previous]):
                previous -= 1
            falls_through = previous < 0 or not (
                ctx.opcode(out[previous]) in ("b", "br") or ctx.opcode(out[previous]).startswith("ret")
            )
            if not falls_through:
                index += 1
                while index < len(rewritten) and not _boundary(ctx, rewritten[index]):
                    index += 1
                continue
        out.append(rewritten[index])
        index += 1
    return out


def _thread_trampoline_branches_host(ctx, records, metadata_labels, candidates):
    trampolines = {}
    label_index = {}
    event_positions, branch_positions = candidates
    for index in event_positions:
        record_id = records[index]
        label = _local(ctx, record_id)
        if label is None:
            continue
        label_index[label] = index
        if ctx.symbol(label).startswith("L_pcc_smap"):
            continue
        body = []
        following = index + 1
        while following < len(records) and not _boundary(ctx, records[following]):
            if not ctx.is_empty(records[following]):
                body.append(records[following])
                # Only a single nonempty instruction can be a trampoline.
                if len(body) > 1:
                    break
            following += 1
        if len(body) == 1:
            branch = _branch(ctx, body[0])
            if branch is not None and branch[0] == "b" and branch[1] != label:
                trampolines[label] = branch[1]
    if not trampolines:
        return records
    rewritten = list(records)
    for index in branch_positions:
        record_id = records[index]
        branch = _branch(ctx, record_id)
        if branch is None:
            continue
        opcode, target = branch
        resolved = target
        seen = set()
        while resolved in trampolines and resolved not in seen:
            seen.add(resolved)
            resolved = trampolines[resolved]
        limit = 6000 if opcode in ("cbz", "cbnz", "tbz", "tbnz") else (200000 if opcode.startswith("b.") else 0)
        if not (resolved == target or (limit and
                (resolved not in label_index or abs(label_index[resolved] - index) > limit))):
            rewritten[index] = _retarget(ctx, record_id, resolved)
    referenced = set(metadata_labels)
    for index in branch_positions:
        branch = _branch(ctx, rewritten[index])
        if branch is not None:
            referenced.add(branch[1])
    out = []
    cursor = 0
    for index in event_positions:
        if index < cursor:
            continue
        label = _local(ctx, rewritten[index])
        if label is not None and label in trampolines and label not in referenced:
            # Copy the preceding run at the next removable candidate, not
            # every retained event individually. Skipped bodies retain the
            # original boundary and fallthrough proofs.
            out.extend(rewritten[cursor:index])
            cursor = index
            previous = len(out) - 1
            while previous >= 0 and ctx.is_empty(out[previous]):
                previous -= 1
            falls_through = previous < 0 or not (
                ctx.opcode(out[previous]) in ("b", "br") or ctx.opcode(out[previous]).startswith("ret")
            )
            if not falls_through:
                cursor = index + 1
                while cursor < len(rewritten) and not _boundary(ctx, rewritten[cursor]):
                    cursor += 1
    out.extend(rewritten[cursor:])
    return out


_INVERSE = {
    "eq": "ne", "ne": "eq", "lt": "ge", "le": "gt", "gt": "le", "ge": "lt",
    "lo": "hs", "ls": "hi", "hi": "ls", "hs": "lo", "mi": "pl", "pl": "mi",
    "vs": "vc", "vc": "vs",
}


def fold_cond_branch_to_fallthrough_records(
    ctx, records, eligible_edges, *, source_edge_index=None,
):
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
                if source_edge_index is not None:
                    error, success = source_edge_index.get(label, (None, None))
                else:
                    error = None
                    success = None
                    # Caller-owned iterables can have observable callbacks;
                    # retain their original search order. Only the producer-
                    # owned integer proof table uses the indexed path.
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
                elif opcode in ("tbz", "tbnz"):
                    inverse = "tbnz" if opcode == "tbz" else "tbz"
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


def _fold_cond_branch_to_fallthrough_host(ctx, records, source_edge_index, candidates):
    # Only label/directive and direct-branch events can affect the source
    # proof or rewrite. Keep physical event indices for distance checks.
    events, branches = candidates
    label_index = {}
    for index in events:
        label = _local(ctx, records[index])
        if label is not None:
            label_index[label] = index
    out = []
    cursor = 0
    error = None
    success = None
    for index in sorted(events + branches):
        if index < cursor:
            continue
        record_id = records[index]
        label = _local(ctx, record_id)
        if label is not None:
            if not ctx.symbol(label).startswith("L_pcc_smap"):
                error, success = source_edge_index.get(label, (None, None))
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
                elif opcode in ("tbz", "tbnz"):
                    inverse = "tbnz" if opcode == "tbz" else "tbz"
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
                    out.extend(records[cursor:index])
                    out.append(_retarget(ctx, record_id, other, inverse))
                    cursor = index + 2
    out.extend(records[cursor:])
    return out


def drop_fallthrough_uncond_branches_records(ctx, records):
    # Empty events and plain local labels emit no code; stack-map labels,
    # function labels, directives and instructions end the search.
    out = []
    for index, record_id in enumerate(records):
        branch = _branch(ctx, record_id)
        if branch is not None and branch[0] == "b" and ctx.symbol(branch[1]).startswith("L_"):
            following = index + 1
            found = False
            scanning = True
            while scanning and following < len(records):
                if ctx.is_empty(records[following]):
                    following += 1
                    continue
                label = _local(ctx, records[following])
                if label == branch[1]:
                    found = True
                    scanning = False
                elif label is None or ctx.symbol(label).startswith("L_pcc_smap"):
                    scanning = False
                else:
                    following += 1
            if found:
                continue
        out.append(record_id)
    return out


def _is_call(opcode):
    return opcode == "bl" or opcode == "blr" or opcode.startswith("blra")


def drop_loop_separator_nops_records(ctx, records):
    """Typed ``_drop_loop_separator_nops``: the loop record's ``nop`` goes
    when an ordinary non-call instruction precedes it."""
    out = []
    previous_is_instruction = False
    for index, record_id in enumerate(records):
        if ctx.is_empty(record_id):
            out.append(record_id)
            continue
        label = ctx.label(record_id)
        opcode = "" if label is not None or ctx.is_directive(record_id) else ctx.opcode(record_id)
        if opcode == "nop" and previous_is_instruction:
            following = index + 1
            while following < len(records) and ctx.is_empty(records[following]):
                following += 1
            if following < len(records):
                next_label = ctx.label(records[following])
                if next_label is not None:
                    name = ctx.symbol(next_label)
                    if name.startswith("L_pcc_smap_") and not name.startswith("L_pcc_smap_end_"):
                        continue
        out.append(record_id)
        previous_is_instruction = opcode != "" and not _is_call(opcode)
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


def _drop_unreferenced_empty_local_labels_host(ctx, records, metadata_labels, candidates):
    events, branches = candidates
    referenced = set(metadata_labels)
    for index in branches:
        branch = _branch(ctx, records[index])
        if branch is not None:
            referenced.add(branch[1])
    out = []
    cursor = 0
    for index in events:
        label = _local(ctx, records[index])
        if label is not None and label not in referenced and not ctx.symbol(label).startswith("L_pcc_smap"):
            following = index + 1
            while following < len(records) and ctx.is_empty(records[following]):
                following += 1
            if following < len(records) and (_local(ctx, records[following]) is not None or _function(ctx, records[following])):
                out.extend(records[cursor:index])
                cursor = index + 1
    out.extend(records[cursor:])
    return out


def thread_trampoline_branches(fragments, semantics, span, metadata_labels=()):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    # Public proof and owner callbacks can mutate the original span's nodes
    # without changing its root identity. Rebuild from the captured output IDs.
    records = ctx.read_span(span)
    candidates = _host_cfg_candidates(ctx, records)
    if candidates is None or not _host_static_metadata(metadata_labels):
        return ctx.write_span(thread_trampoline_branches_records(ctx, records, metadata_labels))
    return ctx.write_span(_thread_trampoline_branches_host(ctx, records, metadata_labels, candidates))


def fold_cond_branch_to_fallthrough(
    fragments, semantics, span, eligible_edges, *, source_edge_index=None,
):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    # Public proof and owner callbacks can mutate the original span's nodes
    # without changing its root identity. Rebuild from the captured output IDs.
    records = ctx.read_span(span)
    # The indexed proof is producer-owned. A public iterable may run
    # callbacks and must retain the original dense search order.
    if type(source_edge_index) is dict:
        candidates = _host_cfg_candidates(ctx, records)
        if candidates is not None:
            return ctx.write_span(_fold_cond_branch_to_fallthrough_host(
                ctx, records, source_edge_index, candidates,
            ))
    return ctx.write_span(fold_cond_branch_to_fallthrough_records(
        ctx, records, eligible_edges, source_edge_index=source_edge_index,
    ))


def drop_fallthrough_uncond_branches(fragments, semantics, span):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    ctx.validate_span(span)
    return fragments.drop_fallthrough_branches(span)


_NOP_WORD = 0xD503201F


def drop_loop_separator_nops(fragments, semantics, span):
    """``drop_loop_separator_nops_records`` over the raw module records.

    Every record is one event; only a NOP word asks for the previous
    instruction's opcode, so the scan costs one record read per event
    instead of a validated opcode and label lookup for each.
    """
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    from .self_backend_aarch64_fragments import EMISSION_RECORD_EMPTY, EMISSION_RECORD_LABEL
    ctx = TypedPassContext(fragments, semantics)
    records = ctx.read_span(span)
    raw = fragments.records
    names = fragments.symbol_names
    out = []
    previous = -1
    count = len(records)
    index = 0
    while index < count:
        record_id = records[index]
        code = raw.get4_unchecked(record_id)
        if code.second == EMISSION_RECORD_EMPTY:
            out.append(record_id)
            index += 1
            continue
        if code.second >= 0 and code.first == _NOP_WORD and previous >= 0:
            prior = raw.get4_unchecked(previous)
            if prior.second >= 0 and not _is_call(ctx.opcode(previous)):
                following = index + 1
                scanning = following < count
                while scanning:
                    # A lane is read from a named record, never from the call.
                    following_code = raw.get4_unchecked(records[following])
                    if following_code.second == EMISSION_RECORD_EMPTY:
                        following += 1
                        scanning = following < count
                    else:
                        scanning = False
                if following < count:
                    after = raw.get4_unchecked(records[following])
                    if after.second == EMISSION_RECORD_LABEL:
                        name = names[after.first]
                        if name.startswith("L_pcc_smap_") and not name.startswith("L_pcc_smap_end_"):
                            # The previous instruction stays the previous one.
                            index += 1
                            continue
        out.append(record_id)
        previous = record_id
        index += 1
    return ctx.write_span(out)


def drop_unreferenced_empty_local_labels(fragments, semantics, span, metadata_labels=()):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    # Public proof and owner callbacks can mutate the original span's nodes
    # without changing its root identity. Rebuild from the captured output IDs.
    records = ctx.read_span(span)
    candidates = _host_cfg_candidates(ctx, records)
    if candidates is None or not _host_static_metadata(metadata_labels):
        return ctx.write_span(drop_unreferenced_empty_local_labels_records(ctx, records, metadata_labels))
    return ctx.write_span(_drop_unreferenced_empty_local_labels_host(
        ctx, records, metadata_labels, candidates,
    ))


def run_aarch64_typed_cfg_passes(
    fragments, semantics, span, metadata_labels, cold_fallthrough_edges,
    *, phase_timing=None, _midpoint_retirement=None,
):
    """Final six passes in the original order, over the whole module, then
    the loop-separator ``nop`` cleanup, which needs the final instructions.

    Proof inputs retain their established module-union ownership and ordering.
    Missing names cannot match emitted labels; do not mutate the symbol table
    merely to record unused proof inputs.
    """
    from .self_backend_aarch64_typed_tlv import elide_dead_tlv_resolver_saves
    from .self_backend_aarch64_typed_pair import pair_adjacent_aarch64_64bit_memory_ops
    from .self_backend_aarch64_typed_peepholes import (
        _observe_peephole_boundary_permission, _retire_owned_peephole_span,
    )
    try:
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            started = phase_timing.begin_backend_finalize("typed_cfg_proofs")
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        metadata_ids = set()
        for name in metadata_labels:
            if name in fragments.symbol_ids:
                metadata_ids.add(fragments.symbol_ids[name])
        source_edge_index = {}
        for source, error, success in cold_fallthrough_edges:
            source_id = fragments.symbol_ids[source] if source in fragments.symbol_ids else -1
            error_id = fragments.symbol_ids[error] if error in fragments.symbol_ids else -1
            success_id = fragments.symbol_ids[success] if success in fragments.symbol_ids else -1
            # The previous ordered search chose the first matching source, even
            # when its target was missing. Preserve that proof exactly, rather
            # than substituting a later and possibly different proof.
            if source_id not in source_edge_index:
                source_edge_index[source_id] = (error_id, success_id)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            phase_timing.complete_backend_finalize("typed_cfg_proofs", started)
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            started = phase_timing.begin_backend_finalize("typed_thread_trampoline_branches")
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        result = thread_trampoline_branches(fragments, semantics, span, metadata_ids)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            phase_timing.complete_backend_finalize("typed_thread_trampoline_branches", started)
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            started = phase_timing.begin_backend_finalize("typed_fold_cond_branch_to_fallthrough")
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        result = fold_cond_branch_to_fallthrough(
            fragments, semantics, result, (), source_edge_index=source_edge_index,
        )
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            phase_timing.complete_backend_finalize("typed_fold_cond_branch_to_fallthrough", started)
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            started = phase_timing.begin_backend_finalize("typed_drop_fallthrough_uncond_branches")
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        result = drop_fallthrough_uncond_branches(fragments, semantics, result)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            phase_timing.complete_backend_finalize("typed_drop_fallthrough_uncond_branches", started)
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            started = phase_timing.begin_backend_finalize("typed_drop_unreferenced_empty_local_labels")
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        result = drop_unreferenced_empty_local_labels(fragments, semantics, result, metadata_ids)
        if phase_timing is not None:
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
            phase_timing.complete_backend_finalize("typed_drop_unreferenced_empty_local_labels", started)
            if _midpoint_retirement is not None:
                _observe_peephole_boundary_permission(_midpoint_retirement, phase_timing)
        if _midpoint_retirement is not None:
            result = _retire_owned_peephole_span(
                fragments, result, _midpoint_retirement, phase_timing, -2,
            )
    finally:
        if _midpoint_retirement is not None:
            _midpoint_retirement.revoke()
    if phase_timing is not None:
        started = phase_timing.begin_backend_finalize("typed_elide_dead_tlv_resolver_saves")
    result = elide_dead_tlv_resolver_saves(fragments, semantics, result)
    if phase_timing is not None:
        phase_timing.complete_backend_finalize("typed_elide_dead_tlv_resolver_saves", started)
    if phase_timing is not None:
        started = phase_timing.begin_backend_finalize("typed_pair_adjacent_aarch64_64bit_memory_ops")
    result = pair_adjacent_aarch64_64bit_memory_ops(fragments, semantics, result, enabled=True)
    if phase_timing is not None:
        phase_timing.complete_backend_finalize("typed_pair_adjacent_aarch64_64bit_memory_ops", started)
    if phase_timing is not None:
        started = phase_timing.begin_backend_finalize("typed_drop_loop_separator_nops")
    result = drop_loop_separator_nops(fragments, semantics, result)
    if phase_timing is not None:
        phase_timing.complete_backend_finalize("typed_drop_loop_separator_nops", started)
    return result
