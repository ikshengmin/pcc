"""Frozen full-sweep strong-liveness oracle, for host tests only.

Copied verbatim from pcc/backend/x86_64_register_copies.py::_dead_moves at
1738a85dcc813bd1a4d4403324fad5f8c8c914e6 (source Git blob
955f5a5b40da48b02a94a5638306f6ef00e4020a). Do not update with the optimized
solver: its out-or-in convergence condition is the independent baseline.
"""
from __future__ import annotations


def _dead_moves(
    count: int,
    reads_of: list[int],
    writes_of: list[int],
    droppable: list[int],
    labels: dict[str, int],
    ends: list[int],
    end_targets: list[str],
    end_falls: list[int],
):
    """Per-line 1 for each move nothing reads (self-moves included), or None
    when a jump target is not a label of this text."""
    starts: list[int] = [0]
    for label_line in labels.values():
        starts.append(label_line)
    for end in ends:
        if end + 1 < count:
            starts.append(end + 1)
    starts.sort()
    block_first: list[int] = []
    for start in starts:
        if start < count and (not block_first or block_first[len(block_first) - 1] != start):
            block_first.append(start)
    block_count = len(block_first)
    if not block_count:
        return None
    block_last: list[int] = []
    block_id = 0
    while block_id < block_count:
        block_last.append(block_first[block_id + 1] - 1 if block_id + 1 < block_count else count - 1)
        block_id += 1
    line_block = [0] * count
    block_id = 0
    while block_id < block_count:
        line_index = block_first[block_id]
        while line_index <= block_last[block_id]:
            line_block[line_index] = block_id
            line_index += 1
        block_id += 1
    successors: list[list[int]] = []
    falls = [1] * block_count
    block_id = 0
    while block_id < block_count:
        successors.append([])
        block_id += 1
    end_index = 0
    while end_index < len(ends):
        owner = line_block[ends[end_index]]
        target = end_targets[end_index]
        if target:
            target_line = labels.get(target, -1)
            if target_line < 0:
                return None
            successors[owner].append(line_block[target_line])
        falls[owner] = end_falls[end_index]
        end_index += 1
    block_id = 0
    while block_id + 1 < block_count:
        if falls[block_id]:
            successors[block_id].append(block_id + 1)
        block_id += 1

    # Strong liveness: a droppable move contributes its reads only while what
    # it writes is live, so moves that only feed each other (a byte write
    # merging into a register nothing else reads) stay dead.  Iterating from
    # empty sets reaches the least fixed point.
    live_in = [0] * block_count
    live_out = [0] * block_count
    changed = True
    while changed:
        changed = False
        block_id = block_count - 1
        while block_id >= 0:
            out = 0
            block_successors = successors[block_id]
            successor_index = 0
            while successor_index < len(block_successors):
                out |= live_in[block_successors[successor_index]]
                successor_index += 1
            live = out
            line_index = block_last[block_id]
            first = block_first[block_id]
            while line_index >= first:
                kind = droppable[line_index]
                if kind != 2:
                    writes = writes_of[line_index]
                    if not (kind == 1 and not writes & live):
                        live = (live & ~writes) | reads_of[line_index]
                line_index -= 1
            if out != live_out[block_id] or live != live_in[block_id]:
                live_out[block_id] = out
                live_in[block_id] = live
                changed = True
            block_id -= 1

    dead = [0] * count
    block_id = 0
    while block_id < block_count:
        live = live_out[block_id]
        line_index = block_last[block_id]
        first = block_first[block_id]
        while line_index >= first:
            kind = droppable[line_index]
            if kind == 2:
                dead[line_index] = 1
            else:
                writes = writes_of[line_index]
                if kind == 1 and not writes & live:
                    dead[line_index] = 1
                else:
                    live = (live & ~writes) | reads_of[line_index]
            line_index -= 1
        block_id += 1
    return dead
