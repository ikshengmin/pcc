"""Jump threading and block merging over textual IR (owned, LLVM-independent).

Upstream reference:

- ``llvm/lib/Transforms/Scalar/JumpThreading.cpp`` threads an edge whose
  branch condition is known on that edge (``ThreadEdge``), and
- ``llvm/lib/Transforms/Utils/BasicBlockUtils.cpp``
  (``MergeBlockIntoPredecessor``) folds a block into its single
  unconditional predecessor.

Why it exists: the frontend lowers a short-circuit ``and``/``or`` into a
phi of constants, and an inlined predicate returns that boolean through a
zext and a single-entry phi before its caller compares it to zero and
branches. The self backend materializes each such boolean in a register
and branches on it again: the GC0 fast path of ``pcc_gc_store_root`` spent
most of its instructions there. Every runtime helper chain has the shape.

Subset implemented:

1. Merge a block into its single predecessor when that predecessor ends in
   an unconditional branch to it; the merged block's single-entry phis
   become their incoming values.
2. Thread an edge ``P -> B`` when ``B`` holds only phis and side-effect-free
   integer operations ending in ``br i1``, and the phi values ``P``
   supplies decide that branch: ``P`` branches straight to the chosen
   successor ``D``. Each phi of ``D`` takes, for ``P``, the value it took
   for ``B`` on that path. A value ``B`` defines may be used only inside
   ``B`` or by the phis of ``B``'s successors for ``B``'s edge, so no use is
   left without a dominating definition. An edge is skipped when ``P``
   already reaches ``D`` (duplicate phi entries), when ``P``'s terminator
   is not a plain ``br`` or names ``B`` twice, or when any value the
   decision or ``D``'s phis need is not a known constant. A phi entry that
   is ``P``'s own branch condition is known on the edge (1 on ``P``'s true
   edge, 0 on its false edge; ``ComputeValueKnownInPredecessors``): the
   frontend's short-circuit merges take the deciding compare itself.
3. Remove a block left without predecessors, and its phi entries.

Every block keeps exactly one phi entry per predecessor. The predecessor,
successor and use maps are kept up to date with each change, and a block
worklist revisits only the blocks a change touched, so a large inlined
runtime function costs a few passes over its instructions. The number of
changes per function is bounded (a constant decision that loops forever
would otherwise thread around its cycle).
"""

from __future__ import annotations

import re

from .ir_mutator import BasicBlock, Function, Instruction, MutableModule
from .text_tokens import replace_local_names

_LABEL_TOKEN_RE = re.compile(r"label\s+%([\w.$-]+)")
_UNCOND_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<target>[\w.$-]+)\s*(?:,.*)?$")
_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+(?P<cond>[^,]+?)\s*,\s*label\s+%(?P<true>[\w.$-]+)\s*,"
    r"\s*label\s+%(?P<false>[\w.$-]+)\s*(?:,.*)?$"
)
_PHI_RE = re.compile(r"^\s*%(?P<name>[\w.$-]+)\s*=\s*phi\s+(?P<ty>\S+)\s+(?P<rest>.*?)\s*$")
_INCOMING_RE = re.compile(r"\[\s*(?P<value>[^,\]]+?)\s*,\s*%(?P<label>[\w.$-]+)\s*\]")
_INT_TYPE_RE = re.compile(r"^i(\d+)$")
_INT_LITERAL_RE = re.compile(r"^-?\d+$")

# Operations whose result depends only on their operands (no memory, no
# control, no undefined behaviour for constant operands).
_PURE_OPCODES = frozenset((
    "phi", "icmp", "zext", "sext", "trunc", "and", "or", "xor", "add", "sub", "select",
))
_CHANGES_PER_BLOCK = 4
_EXTRA_CHANGES = 64


def _width(ty: str) -> int | None:
    if ty == "ptr":
        return 64
    match = _INT_TYPE_RE.match(ty)
    if match is None:
        return None
    return int(match.group(1))


def _mask(value: int, width: int) -> int:
    return value & ((1 << width) - 1)


def _signed(value: int, width: int) -> int:
    value = _mask(value, width)
    if value >= 1 << (width - 1):
        return value - (1 << width)
    return value


def _literal(spelling: str, env: dict[str, int], width: int) -> int | None:
    spelling = spelling.strip()
    if spelling.startswith("%"):
        known = env.get(spelling[1:])
        return None if known is None else _mask(known, width)
    if spelling == "true":
        return 1
    if spelling == "false" or spelling == "null" or spelling == "zeroinitializer":
        return 0
    if _INT_LITERAL_RE.match(spelling):
        return _mask(int(spelling), width)
    return None


def _spell(value: int, ty: str) -> str | None:
    width = _width(ty)
    if width is None:
        return None
    if ty == "ptr":
        return "null" if value == 0 else None
    if width == 1:
        return "1" if value & 1 else "0"
    return str(_signed(value, width))


def _split_operands(text: str) -> list[str]:
    """Top-level comma split (no nested aggregates appear in the pure ops)."""
    return [part.strip() for part in text.split(",")]


def _evaluate(inst: Instruction, env: dict[str, int]) -> int | None:
    """The constant result of a pure instruction, or None when unknown."""
    text = inst.text.strip()
    body = text.split("=", 1)[1].strip() if "=" in text else text
    op = inst.opcode
    try:
        if op == "icmp":
            _icmp, pred, rest = body.split(None, 2)
            ty, operands = rest.split(None, 1)
            width = _width(ty)
            if width is None:
                return None
            left, right = _split_operands(operands)[:2]
            a = _literal(left, env, width)
            b = _literal(right, env, width)
            if a is None or b is None:
                return None
            sa, sb = _signed(a, width), _signed(b, width)
            result = {
                "eq": a == b, "ne": a != b,
                "ugt": a > b, "uge": a >= b, "ult": a < b, "ule": a <= b,
                "sgt": sa > sb, "sge": sa >= sb, "slt": sa < sb, "sle": sa <= sb,
            }.get(pred)
            return None if result is None else int(result)
        if op in ("zext", "sext", "trunc"):
            # <op> <ty> <value> to <ty2>
            _opname, rest = body.split(None, 1)
            source, target = rest.split(" to ", 1)
            ty, value = source.split(None, 1)
            width = _width(ty)
            target_width = _width(target.strip())
            if width is None or target_width is None:
                return None
            v = _literal(value, env, width)
            if v is None:
                return None
            if op == "sext":
                return _mask(_signed(v, width), target_width)
            return _mask(v, target_width)
        if op in ("and", "or", "xor", "add", "sub"):
            _opname, rest = body.split(None, 1)
            ty, operands = rest.split(None, 1)
            width = _width(ty)
            if width is None or ty == "ptr":
                return None
            left, right = _split_operands(operands)[:2]
            a = _literal(left, env, width)
            b = _literal(right, env, width)
            if a is None or b is None:
                return None
            if op == "and":
                return a & b
            if op == "or":
                return a | b
            if op == "xor":
                return _mask(a ^ b, width)
            if op == "add":
                return _mask(a + b, width)
            return _mask(a - b, width)
        if op == "select":
            _opname, rest = body.split(None, 1)
            cond, first, second = _split_operands(rest)[:3]
            cond_ty, cond_value = cond.split(None, 1)
            if cond_ty != "i1":
                return None
            c = _literal(cond_value, env, 1)
            if c is None:
                return None
            chosen = first if c else second
            ty, value = chosen.split(None, 1)
            width = _width(ty)
            if width is None or ty == "ptr":
                return None
            return _literal(value, env, width)
    except ValueError:
        return None
    return None


def _phi_parts(inst: Instruction):
    """``(name, type, [(value, label)])``, or None unless the entries are
    the whole operand list (a constant expression with commas, metadata or
    fast-math flags are not rewritten)."""
    match = _PHI_RE.match(inst.text.rstrip("\n"))
    if match is None:
        return None
    rest = match.group("rest")
    if _INCOMING_RE.sub("", rest).replace(",", "").strip():
        return None
    return match.group("name"), match.group("ty"), [
        (m.group("value").strip(), m.group("label")) for m in _INCOMING_RE.finditer(rest)
    ]


def _phi_text(inst: Instruction, name: str, ty: str, incoming) -> str:
    indent = inst.text[: len(inst.text) - len(inst.text.lstrip())]
    entries = ", ".join(f"[{value}, %{label}]" for value, label in incoming)
    return f"{indent}%{name} = phi {ty} {entries}\n"


def _phis(block: BasicBlock) -> list[Instruction]:
    return [inst for inst in block.instructions if inst.opcode == "phi"]


def _set_phi_incoming(inst: Instruction, transform) -> bool:
    parts = _phi_parts(inst)
    if parts is None:
        return False
    name, ty, incoming = parts
    inst.text = _phi_text(inst, name, ty, transform(incoming))
    return True


class _State:
    """One function's blocks, edges and value users, updated per change."""

    def __init__(self, fn: Function):
        self.fn = fn
        self.entry = fn.blocks[0].name
        self.blocks: dict[str, BasicBlock] = {block.name: block for block in fn.blocks}
        self.preds: dict[str, list[str]] = {block.name: [] for block in fn.blocks}
        self.succs: dict[str, list[str]] = {}
        self.home: dict[int, str] = {}
        self.users: dict[str, list[Instruction]] = {}
        self.removed: set[str] = set()
        for block in fn.blocks:
            term = block.terminator
            targets = _LABEL_TOKEN_RE.findall(term.text) if term is not None else []
            self.succs[block.name] = targets
            for target in targets:
                if target in self.preds:
                    self.preds[target].append(block.name)
            for inst in block.instructions:
                self.home[id(inst)] = block.name
                for name in inst.operand_names():
                    self.users.setdefault(name, []).append(inst)

    def remove(self, block: BasicBlock) -> None:
        self.removed.add(block.name)

    def retarget(self, pred: BasicBlock, old: str, new: str) -> None:
        term = pred.terminator
        term.text = replace_local_names(term.text, {old: "%" + new})
        self.succs[pred.name] = _LABEL_TOKEN_RE.findall(term.text)
        while pred.name in self.preds[old]:
            self.preds[old].remove(pred.name)
        self.preds[new].extend(pred.name for name in self.succs[pred.name] if name == new)

    def add_use(self, value: str, inst: Instruction) -> None:
        if value.startswith("%"):
            self.users.setdefault(value[1:], []).append(inst)

    def uses_confined(self, block: BasicBlock, defined: set[str], successors: set[str]) -> bool:
        """Values defined in ``block`` are used only in it or by its
        successors' phis on the edge from ``block``."""
        for name in defined:
            for inst in self.users.get(name, ()):
                home = self.home.get(id(inst))
                if home == block.name or home in self.removed:
                    continue
                if inst.opcode != "phi" or home not in successors:
                    return False
                parts = _phi_parts(inst)
                if parts is None:
                    return False
                for value, label in parts[2]:
                    if value.startswith("%") and value[1:] in defined and label != block.name:
                        return False
        return True

    # -- merge ---------------------------------------------------------------

    def merge_into_predecessor(self, block: BasicBlock) -> list[str]:
        if block.name == self.entry:
            return []
        preds = self.preds[block.name]
        if len(preds) != 1 or preds[0] == block.name:
            return []
        pred = self.blocks[preds[0]]
        term = pred.terminator
        if term is None or _UNCOND_BR_RE.match(term.text.rstrip("\n")) is None:
            return []
        replacements: dict[str, str] = {}
        for inst in _phis(block):
            parts = _phi_parts(inst)
            if parts is None or len(parts[2]) != 1 or parts[2][0][1] != pred.name:
                return []
            replacements[parts[0]] = parts[2][0][0]
        if any(value.startswith("%") and value[1:] in replacements for value in replacements.values()):
            return []
        body = [inst for inst in block.instructions if inst.opcode != "phi"]
        pred.instructions = pred.instructions[:-1] + body
        for inst in body:
            self.home[id(inst)] = pred.name
        self.remove(block)
        successors = self.succs.pop(block.name)
        self.succs[pred.name] = successors
        relabel = {block.name: "%" + pred.name}
        for successor_name in dict.fromkeys(successors):
            successor = self.blocks.get(successor_name)
            if successor is None:
                continue
            for inst in _phis(successor):
                inst.text = replace_local_names(inst.text, relabel)
            self.preds[successor_name] = [
                pred.name if name == block.name else name for name in self.preds[successor_name]
            ]
        for name, value in replacements.items():
            for inst in self.users.get(name, ()):
                if self.home.get(id(inst)) in self.removed:
                    continue
                inst.text = replace_local_names(inst.text, {name: value})
                self.add_use(value, inst)
        return [pred.name, *successors]

    # -- forwarders ----------------------------------------------------------

    def bypass_forwarder(self, block: BasicBlock) -> list[str]:
        """Fold a block holding only phis and ``br label %S`` into ``S``.

        Each predecessor ``X`` of the forwarder ``F`` branches to ``S``
        instead; every phi of ``S`` takes, for ``X``, the value it took for
        ``F`` (read through ``F``'s phis for ``X``). This is how a nested
        short-circuit's inner merge reaches the outer one, which threading
        then decides per edge. A value defined outside ``F`` that ``S``
        received on ``F``'s edge dominates ``F``, hence each ``X``.
        """
        if block.name == self.entry or not block.instructions:
            return []
        branch = _UNCOND_BR_RE.match(block.terminator.text.rstrip("\n"))
        if branch is None:
            return []
        if any(inst.opcode != "phi" for inst in block.instructions[:-1]):
            return []
        successor_name = branch.group("target")
        successor = self.blocks.get(successor_name)
        if successor is None or successor is block or successor_name == self.entry:
            return []
        preds = self.preds[block.name]
        if not preds or block.name in preds or len(set(preds)) != len(preds):
            return []
        successor_preds = set(self.preds[successor_name])
        if any(pred in successor_preds for pred in preds):
            return []
        own: dict[str, dict[str, str]] = {}
        for inst in block.instructions[:-1]:
            parts = _phi_parts(inst)
            if parts is None:
                return []
            name, _ty, incoming = parts
            by_pred = {label: value for value, label in incoming}
            if len(by_pred) != len(incoming) or set(by_pred) != set(preds):
                return []
            own[name] = by_pred
        if own and not self.uses_confined(block, set(own), {successor_name}):
            return []
        plans: list[tuple[Instruction, list[tuple[str, str]]]] = []
        for inst in _phis(successor):
            parts = _phi_parts(inst)
            if parts is None:
                return []
            through = [value for value, label in parts[2] if label == block.name]
            if len(through) != 1:
                return []
            value = through[0]
            entries = []
            for pred in preds:
                if value.startswith("%") and value[1:] in own:
                    entries.append((own[value[1:]][pred], pred))
                else:
                    entries.append((value, pred))
            plans.append((inst, entries))
        for inst, entries in plans:
            _set_phi_incoming(
                inst,
                lambda incoming, add=entries: [(v, l) for v, l in incoming if l != block.name] + add,
            )
            for value, _label in entries:
                self.add_use(value, inst)
        moved = list(preds)
        for pred in moved:
            self.retarget(self.blocks[pred], block.name, successor_name)
        while block.name in self.preds[successor_name]:
            self.preds[successor_name].remove(block.name)
        self.remove(block)
        return [successor_name, *moved]

    # -- threading -----------------------------------------------------------

    def thread(self, block: BasicBlock) -> list[str]:
        if block.name == self.entry or not block.instructions:
            return []
        branch = _COND_BR_RE.match(block.terminator.text.rstrip("\n"))
        if branch is None:
            return []
        true_target, false_target = branch.group("true"), branch.group("false")
        if true_target == false_target or block.name in (true_target, false_target):
            return []
        cond = branch.group("cond").strip()
        body = block.instructions[:-1]
        if any(inst.opcode not in _PURE_OPCODES for inst in body):
            return []
        defined = {inst.result_name for inst in body if inst.result_name}
        if not cond.startswith("%") or cond[1:] not in defined:
            return []
        phis = [inst for inst in body if inst.opcode == "phi"]
        if not phis or not self.uses_confined(block, defined, {true_target, false_target}):
            return []
        # A phi's incoming value is used at the end of its predecessor. A
        # loop header carrying its own value (``%x = phi [.., %x, %latch]``)
        # dominated that latch only while every entry passed through it; a
        # threaded edge into the loop body would bypass it.
        for inst in phis:
            parts = _phi_parts(inst)
            if parts is None:
                return []
            for value, _label in parts[2]:
                if value.startswith("%") and value[1:] in defined:
                    return []
        affected: list[str] = []
        for pred_name in list(dict.fromkeys(self.preds[block.name])):
            if self.preds[block.name].count(pred_name) != 1 or pred_name == block.name:
                continue
            pred = self.blocks[pred_name]
            pred_term = pred.terminator
            if pred_term is None or pred_term.opcode != "br":
                continue
            if self.succs[pred_name].count(block.name) != 1:
                continue
            # A predecessor that branches on a value decided it on each of
            # its edges (LLVM ComputeValueKnownInPredecessors): a short-
            # circuit merge takes the deciding compare itself from that edge.
            edge_known: dict[str, int] = {}
            pred_branch = _COND_BR_RE.match(pred_term.text.rstrip("\n"))
            if pred_branch is not None:
                pred_cond = pred_branch.group("cond").strip()
                if pred_cond.startswith("%"):
                    edge_known[pred_cond[1:]] = 1 if pred_branch.group("true") == block.name else 0
            env: dict[str, int] | None = {}
            for inst in phis:
                parts = _phi_parts(inst)
                if parts is None:
                    env = None
                    break
                name, ty, incoming = parts
                values = [value for value, label in incoming if label == pred_name]
                if len(values) != 1:
                    env = None
                    break
                width = _width(ty)
                known = None if width is None else _literal(values[0], edge_known, width)
                if known is not None:
                    env[name] = known
            if env is None:
                continue
            for inst in body:
                if inst.opcode == "phi" or inst.result_name is None:
                    continue
                value = _evaluate(inst, env)
                if value is not None:
                    env[inst.result_name] = value
            decision = env.get(cond[1:])
            if decision is None:
                continue
            target_name = true_target if decision else false_target
            target = self.blocks.get(target_name)
            if target is None or pred_name in self.preds[target_name]:
                continue
            # Each phi of the target takes, for pred, the value it took for
            # this block on that path.
            new_entries: list[tuple[Instruction, str]] = []
            for inst in _phis(target):
                parts = _phi_parts(inst)
                if parts is None:
                    new_entries = None
                    break
                _name, ty, incoming = parts
                from_block = [value for value, label in incoming if label == block.name]
                if len(from_block) != 1:
                    new_entries = None
                    break
                value = from_block[0]
                if value.startswith("%") and value[1:] in defined:
                    known = env.get(value[1:])
                    spelled = None if known is None else _spell(known, ty)
                    if spelled is None:
                        new_entries = None
                        break
                    value = spelled
                new_entries.append((inst, value))
            if new_entries is None:
                continue
            for inst, value in new_entries:
                _set_phi_incoming(inst, lambda incoming, v=value: incoming + [(v, pred_name)])
                self.add_use(value, inst)
            self.retarget(pred, block.name, target_name)
            for inst in phis:
                _set_phi_incoming(inst, lambda incoming: [(v, l) for v, l in incoming if l != pred_name])
            affected.extend((pred_name, target_name))
        if not affected:
            return []
        affected.append(block.name)
        if not self.preds[block.name]:
            self.remove_unreachable(block)
            affected.extend(self.succs.get(block.name, []))
        return affected

    def remove_unreachable_blocks(self) -> list[str]:
        """Drop blocks a decided edge left unreachable (a loop the entry
        decides never to enter keeps its back-edge), and their phi entries.
        Returns the reachable blocks that lost a predecessor."""
        reachable = _reachable(self)
        touched: list[str] = []
        for name, block in list(self.blocks.items()):
            if name not in reachable and name not in self.removed:
                touched.extend(other for other in self.succs.get(name, []) if other in reachable)
                self.remove_unreachable(block)
        return touched

    def remove_unreachable(self, block: BasicBlock) -> None:
        self.remove(block)
        for successor_name in dict.fromkeys(self.succs.get(block.name, [])):
            successor = self.blocks.get(successor_name)
            if successor is None or successor is block:
                continue
            for inst in _phis(successor):
                _set_phi_incoming(inst, lambda incoming: [(v, l) for v, l in incoming if l != block.name])
            self.preds[successor_name] = [name for name in self.preds[successor_name] if name != block.name]


def _reachable(state: "_State") -> set[str]:
    seen: set[str] = set()
    stack = [state.entry]
    while stack:
        name = stack.pop()
        if name in seen or name in state.removed:
            continue
        seen.add(name)
        stack.extend(state.succs.get(name, []))
    return seen


def jump_thread_function(fn: Function) -> bool:
    if not fn.blocks:
        return False
    for block in fn.blocks:
        for inst in block.instructions:
            if inst.opcode == "phi" and _phi_parts(inst) is None:
                return False
    state = _State(fn)
    budget = _CHANGES_PER_BLOCK * len(fn.blocks) + _EXTRA_CHANGES
    work = [block.name for block in reversed(fn.blocks)]
    queued = set(work)
    changed = False
    while budget > 0:
        if not work:
            if not changed:
                break
            # A decided edge can leave a dead cycle that still names its
            # successors as predecessors; drop it and revisit them.
            work = [name for name in dict.fromkeys(state.remove_unreachable_blocks())
                    if name not in state.removed]
            if not work:
                break
            queued = set(work)
        name = work.pop()
        queued.discard(name)
        if name in state.removed:
            continue
        block = state.blocks[name]
        affected = (state.merge_into_predecessor(block)
                    or state.bypass_forwarder(block)
                    or state.thread(block))
        if not affected:
            continue
        changed = True
        budget -= 1
        for other in affected:
            if other not in state.removed and other not in queued:
                work.append(other)
                queued.add(other)
    if changed:
        state.remove_unreachable_blocks()
        fn.blocks = [block for block in fn.blocks if block.name not in state.removed]
    return changed


def jump_thread_text(ir_text: str) -> tuple[str, bool]:
    """Thread constant-decided branches and merge straight-line blocks."""
    if "phi " not in ir_text and "br label" not in ir_text:
        return ir_text, False
    module = MutableModule.parse(ir_text)
    changed = False
    for fn in module.functions:
        if jump_thread_function(fn):
            changed = True
    if not changed:
        return ir_text, False
    return module.serialize(), True
