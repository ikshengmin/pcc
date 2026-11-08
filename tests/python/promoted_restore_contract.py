"""Test-only proof for promoted restore flags in actual optimized link input.

A true PHI alone is insufficient. Its complete PHI-connected family must
control cleanup of one physical output cell, including error projections.
This deliberately accepts only the observed promotion vocabulary; another
optimizer representation needs its own proof rather than a permissive skip.
"""
from collections import defaultdict

from pcc.backend.self_backend_analysis import (
    instruction_used_values,
    terminator_used_values,
)


class PromotedRestoreOwners:
    def __init__(self, roots):
        self.roots = roots
        self.blocks = {block.name: block for block in roots.blocks}
        self.phis = {phi.dest: (block.name, phi) for block in roots.blocks for phi in block.phis}
        self.successors = {}
        self.predecessors = defaultdict(set)
        self.uses = defaultdict(list)
        projections = {ins.data[0] for _, _, ins in roots.rows
                       if ins.kind == "select" and ins.data[2] in self.phis}
        empty_addresses = {ins.data[4] for _, _, ins in roots.rows
                           if ins.kind == "select" and ins.data[2] in self.phis}
        tracked = self.phis.keys() | projections | empty_addresses
        for block in roots.blocks:
            term = block.terminator
            successors = ()
            if term.kind == "br":
                successors = term.data[:1]
            elif term.kind == "br_cond":
                successors = term.data[1:]
            elif term.kind == "switch":
                successors = (term.data[2],) + tuple(label for _, label in term.data[3])
            self.successors[block.name] = successors
            for label in successors:
                self.predecessors[label].add(block.name)
            for phi in block.phis:
                for incoming in phi.incoming:
                    if incoming.value in tracked:
                        self.uses[incoming.value].append(("phi", block.name, phi, incoming.label))
            for position, ins in enumerate(block.instructions):
                for value in set(instruction_used_values(ins)) & tracked:
                    self.uses[value].append(("instruction", block.name, ins, position))
            for value in set(terminator_used_values(term)) & tracked:
                self.uses[value].append(("terminator", block.name, term, len(block.instructions)))
        self.reachable = self._reachable()
        self.avoiding = {}
        self.queried_uses = defaultdict(set)
        for value, (definition, _) in self.phis.items():
            self.queried_uses[definition].update(
                position if kind == "phi" else block
                for kind, block, _, position in self.uses[value]
            )
        self.owners = {}

    def _reachable(self, avoid=None):
        reached, pending = set(), [self.roots.blocks[0].name]
        while pending:
            name = pending.pop()
            if name != avoid and name not in reached:
                reached.add(name)
                pending.extend(self.successors[name])
        return reached

    def _dominates(self, definition, use):
        assert definition in self.reachable and use in self.reachable, "promoted owner is unreachable"
        if definition not in self.avoiding:
            # Keep only queried violations, never a full CFG copy per PHI.
            self.avoiding[definition] = self._reachable(definition) & self.queried_uses[definition]
        assert use not in self.avoiding[definition], "promoted owner does not dominate its use"

    def _clear(self, ins):
        assert self.roots.called(ins, "pcc_gc_store_root"), "owner must guard root cleanup"
        address, value = self.roots.args(ins)
        assert value == "null", "owner cleanup must clear its root"
        return self.roots.slot(address)

    def _projection(self, block, position, ins):
        assert ins.kind == "select" and ins.data[1].is_ptr, "unsupported promoted owner use"
        output, empty = self.roots.slot(ins.data[3]), self.roots.slot(ins.data[4])
        assert empty != output and empty[1] == 0, "invalid false cleanup projection"
        assert empty == (ins.data[4], 0) and empty[0] in self.roots.allocas, "false projection must be a direct cell"
        allocated, owner = self.roots.allocas[empty[0]]
        assert owner == "entry" and allocated.is_ptr, "false projection needs an entry pointer cell"
        empty_uses = self.uses[empty[0]]
        assert len(empty_uses) == 2, "false projection cell must not escape"
        initializers = [(kind, name, row, pos) for kind, name, row, pos in empty_uses if row is not ins]
        assert len(initializers) == 1, "false projection needs one NULL initializer"
        kind, name, initializer, init_position = initializers[0]
        assert kind == "instruction" and name == "entry" and initializer.kind == "store", (
            "false projection needs one entry NULL initializer"
        )
        assert initializer.data[0].is_ptr and initializer.data[1] == "null", "false projection must be empty"
        assert block != "entry" or init_position < position, "false projection initialization must dominate"
        uses = self.uses[ins.data[0]]
        assert uses, "cleanup projection is unused"
        for kind, use_block, consumer, use_position in uses:
            assert kind == "instruction" and use_block == block and use_position > position, (
                "cleanup projection must dominate its clear"
            )
            assert self.roots.called(consumer, "pcc_gc_store_root"), "cleanup projection escapes"
            assert self.roots.args(consumer) == [ins.data[0], "null"], "wrong projected cleanup"
        return output

    def owner(self, source):
        """Map all SSA uses to one owner, never by similar name or one witness."""
        if source in self.owners:
            return self.owners[source]
        family, pending, outputs, direct = set(), [source], set(), set()
        while pending:
            value = pending.pop()
            if value in family:
                continue
            family.add(value)
            assert value in self.phis, "ownership family contains an unsupported definition"
            definition, phi = self.phis[value]
            assert phi.type.describe() == "i1", "ownership PHI must be i1"
            labels = [incoming.label for incoming in phi.incoming]
            assert len(labels) == len(set(labels)) and set(labels) == self.predecessors[definition], (
                "ownership PHI must cover its actual predecessors exactly"
            )
            for incoming in phi.incoming:
                if incoming.value not in ("0", "1", "undef"):
                    pending.append(incoming.value)
            for kind, block, use, position in self.uses[value]:
                self._dominates(definition, position if kind == "phi" else block)
                if kind == "phi":
                    pending.append(use.dest)
                elif kind == "terminator":
                    assert use.kind == "br_cond" and use.data[0] == value, "unsupported owner terminator"
                    release, continuation = use.data[1:]
                    cleanup = self.blocks[release]
                    assert self.predecessors[release] == {block}, "cleanup must have its ownership guard"
                    assert not cleanup.phis and len(cleanup.instructions) == 1, "unexpected guarded cleanup"
                    assert cleanup.terminator.kind == "br" and cleanup.terminator.data == (continuation,), (
                        "cleanup must join its false successor"
                    )
                    outputs.add(self._clear(cleanup.instructions[0]))
                    direct.add(value)
                else:
                    assert use.kind == "select" and use.data[2] == value, "unsupported promoted owner use"
                    outputs.add(self._projection(block, position, use))
        assert source in direct, "restored owner needs a direct guarded cleanup witness"
        assert len(outputs) == 1, "ownership family must clean exactly one physical output"
        output = next(iter(outputs))
        for value in family:
            self.owners[value] = output
        return output

    def check_join(self, fast, slow, outputs):
        fast_block, slow_block = self.blocks[fast], self.blocks[slow]
        assert fast_block.terminator.kind == slow_block.terminator.kind == "br", "restore arms must join"
        assert fast_block.terminator.data == slow_block.terminator.data, "restore arms need the same join"
        assert len(self.predecessors[fast]) == 1 and self.predecessors[fast] == self.predecessors[slow], (
            "restore arms must share their sole admission guard"
        )
        joined = fast_block.terminator.data[0]
        assert self.predecessors[joined] == {fast, slow}, "restore join has an unexpected predecessor"
        phis = self.blocks[joined].phis
        assert len(phis) == len(outputs), "each missing memory flag needs its own promoted PHI"
        owners = []
        for phi in phis:
            assert phi.type.describe() == "i1", "restore ownership PHI must be i1"
            assert len(phi.incoming) == 2 and {(item.value, item.label) for item in phi.incoming} == {
                ("1", fast), ("1", slow),
            }, "restore owner must be true on both actual incoming edges"
            owners.append(self.owner(phi.dest))
        assert len(owners) == len(set(owners)) and set(owners) == set(outputs), (
            "promoted flags must match missing physical outputs one-to-one"
        )
