"""Outline the slow paths that force a frame onto a function's fast exits.

LLVM's code generator shrink-wraps: a runtime helper such as
``pcc_gc_release_known`` returns from its null, tagged and immortal checks
without saving a register or setting up a frame, and builds the frame only
on the path that calls or needs stack memory. pcc's AArch64 allocator gives
a value one register for the whole function, so the argument a slow path
keeps across a call sits in a callee-saved register from the first
instruction, and the prologue runs on every exit (frame setup and
argument copies were about 4800 instructions per handler request on paths
that made no call and touched no stack slot).

This pass gets the same effect in the IR, for every target: it keeps the
blocks that need no frame and moves the rest of the function into internal
functions entered by tail calls. What stays is a body of loads, stores,
atomics, arithmetic and tail calls, which the AArch64 backend emits without
a frame (its sibling calls are a plain branch).

* A block is heavy when it holds an ``alloca``, an ``invoke``, or a call that
  is not in tail position: a tail call is the instruction before a ``ret``
  of its own result (or ``ret void`` after a void call), calls a function
  directly with at most eight pointer, ``i32`` or ``i64`` arguments, and
  returns the function's own type. A few pure intrinsics are light; every
  other intrinsic (memory copies, thread-local addresses, frame addresses)
  is heavy. A block that only branches to a block returning a constant,
  an argument or a phi is first given that return, so a call before it
  becomes a tail call.
* The kept region is what entry reaches through light blocks outside any
  cycle that holds a heavy block. Each edge leaving it enters a slow
  path. That entry must lie on no cycle; when it does, the edge's source
  leaves the kept region and the cut moves one block earlier. The kept
  region must hold a ``ret`` reached through a block that reads or writes
  memory: an exit that only tests an argument (``free(NULL)``) is not a
  fast path, and outlining would put every real call behind one more
  branch.
* Each slow path is outlined as the blocks its entry reaches, copied.
  Because the entry is on no cycle, every definition the copy uses either
  dominates the entry (a parameter, passed at the cut) or is copied and
  still dominates its uses. The entry's phis become parameters too, each
  cut passing its own incoming value. ``i1``, ``i8`` and ``i16`` values are
  widened to ``i64`` across the call and truncated back.
* The outlined functions are ``noinline``: the definition inliner must not
  put a slow path back.
* Only AArch64 modules are outlined: their backend emits a function whose
  calls are all sibling calls without a frame; the x86-64 backends make no
  sibling calls yet.
* The function must have more than 64 instructions (smaller ones are
  cross-module inline candidates, which an internal reference would end),
  a return type of ``void``, ``ptr``, ``i32`` or ``i64``, and each slow
  path at most eight parameters. At most four slow paths and 4000 copied
  instructions are made per function.

The kept region and the slow paths may share blocks (a slow path that
rejoins the fast code copies the rest of the function). Removing the
blocks no longer reachable leaves every kept block's phis with entries for
kept predecessors only.
"""

from __future__ import annotations

import re

from .ir_mutator import Argument, BasicBlock, Function, Instruction, MutableModule

_LABEL_RE = re.compile(r"label\s+%([\w.$-]+)")
_LOCAL_RE = re.compile(r"%([\w.$-]+)")
_PHI_RE = re.compile(r"^\s*%(?P<name>[\w.$-]+)\s*=\s*phi\s+(?P<ty>\S+)\s+(?P<rest>.*?)\s*$")
_INCOMING_RE = re.compile(r"\[\s*(?P<value>[^,\]]+?)\s*,\s*%(?P<label>[\w.$-]+)\s*\]")
_CALL_RE = re.compile(
    r"^\s*(?:%(?P<result>[\w.$-]+)\s*=\s*)?(?:tail\s+|notail\s+)?call\s+(?P<ret>\S+)\s+"
    r"(?:\((?P<fty>[^)]*)\)\s+)?(?P<callee>[@%][\w.$-]+)\((?P<args>.*)\)\s*(?:#\d+\s*)?$"
)
_RET_RE = re.compile(r"^\s*ret\s+(?P<ty>\S+)(?:\s+(?P<value>\S+))?\s*$")
_UNCOND_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<target>[\w.$-]+)\s*$")

_MIN_INSTRUCTIONS = 64
_MAX_PARAMETERS = 8
_MAX_SLOW_PATHS = 4
_MAX_COPIED_INSTRUCTIONS = 4000
_RETURN_TYPES = ("void", "ptr", "i32", "i64")
_REGISTER_TYPES = ("ptr", "i32", "i64")
_NARROW_TYPES = ("i1", "i8", "i16")
_LIGHT_INTRINSICS = (
    "llvm.ctpop.", "llvm.ctlz.", "llvm.cttz.", "llvm.bswap.", "llvm.expect.",
    "llvm.assume", "llvm.smax.", "llvm.smin.", "llvm.umax.", "llvm.umin.",
    "llvm.abs.", "llvm.trap",
)
_BINARY_OPCODES = (
    "add", "sub", "mul", "and", "or", "xor", "shl", "lshr", "ashr",
    "udiv", "sdiv", "urem", "srem",
)
_CAST_OPCODES = ("zext", "sext", "trunc", "bitcast", "ptrtoint", "inttoptr", "freeze")


def _split_top(text: str) -> list[str]:
    """Split at commas outside brackets, braces and parentheses."""
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        ch = text[index]
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _return_type(fn: Function) -> str:
    """The return type spelled in the define line, or "" when unusual."""
    header = fn.header_line
    at = header.find("@" + fn.name + "(")
    if at < 0:
        return ""
    words = header[:at].split()
    if not words or "{" in header[:at] or "[" in header[:at]:
        return ""
    return words[-1]


def _successors(block: BasicBlock) -> list[str]:
    term = block.terminator
    if term is None:
        return []
    return _LABEL_RE.findall(term.text)


def _value_uses(inst: Instruction) -> list[str]:
    """SSA values an instruction reads (labels and phi blocks excluded)."""
    text = inst.text
    if inst.opcode == "phi":
        out: list[str] = []
        for match in _INCOMING_RE.finditer(text):
            value = match.group("value").strip()
            if value.startswith("%"):
                out.append(value[1:])
        return out
    body = text
    if inst.result_name is not None:
        body = text.split("=", 1)[1]
    body = _LABEL_RE.sub("", body)
    return _LOCAL_RE.findall(body)


def _phi_incoming(inst: Instruction) -> list[tuple[str, str]]:
    return [(m.group("value").strip(), m.group("label")) for m in _INCOMING_RE.finditer(inst.text)]


def _result_type(inst: Instruction) -> str:
    """The type of an instruction's result for the shapes outlining passes
    across a call, or "" when it is not one of them."""
    text = inst.text.strip()
    if inst.result_name is None or "=" not in text:
        return ""
    body = text.split("=", 1)[1].strip()
    op = inst.opcode
    words = body.split()
    if op == "phi":
        match = _PHI_RE.match(inst.text)
        return match.group("ty") if match else ""
    if op == "load":
        rest = body[len("load"):].strip()
        for prefix in ("atomic ", "volatile "):
            if rest.startswith(prefix):
                rest = rest[len(prefix):].strip()
        return rest.split(",", 1)[0].strip()
    if op in ("icmp", "fcmp"):
        return "i1"
    if op in _BINARY_OPCODES:
        index = 1
        while index < len(words) and words[index] in ("nuw", "nsw", "exact", "disjoint"):
            index += 1
        return words[index] if index < len(words) else ""
    if op in _CAST_OPCODES:
        if op == "freeze":
            return words[1] if len(words) > 1 else ""
        if " to " not in body:
            return ""
        return body.rsplit(" to ", 1)[1].split(",", 1)[0].strip()
    if op == "getelementptr":
        return "ptr"
    if op == "select":
        operands = _split_top(body[len("select"):])
        if len(operands) < 2:
            return ""
        return operands[1].split()[0]
    if op == "call":
        match = _CALL_RE.match(inst.text)
        return match.group("ret") if match else ""
    if op == "atomicrmw":
        operands = _split_top(body[len("atomicrmw"):])
        if len(operands) < 2:
            return ""
        return operands[1].split()[0]
    if op == "extractvalue":
        rest = body[len("extractvalue"):].strip()
        if not rest.startswith("{"):
            return ""
        close = rest.find("}")
        fields = [part.strip() for part in rest[1:close].split(",")]
        index_text = rest.rsplit(",", 1)[1].strip()
        if not index_text.isdigit() or int(index_text) >= len(fields):
            return ""
        return fields[int(index_text)]
    return ""


def _call_arguments(match) -> list[tuple[str, str]] | None:
    """(type, value) of each call argument, or None for an unusual one."""
    out: list[tuple[str, str]] = []
    for part in _split_top(match.group("args")):
        pieces = part.split()
        if len(pieces) < 2:
            return None
        out.append((pieces[0], pieces[-1]))
    return out


def _is_light_call(block: BasicBlock, index: int, return_type: str) -> bool:
    inst = block.instructions[index]
    match = _CALL_RE.match(inst.text)
    if match is None:
        return False
    callee = match.group("callee")
    if callee.startswith("@llvm."):
        name = callee[1:]
        for prefix in _LIGHT_INTRINSICS:
            if name.startswith(prefix):
                return True
        return False
    if not callee.startswith("@") or index + 2 != len(block.instructions):
        return False
    ret = _RET_RE.match(block.instructions[index + 1].text)
    if ret is None:
        return False
    call_type = match.group("ret")
    if call_type != return_type:
        return False
    if call_type == "void":
        if ret.group("ty") != "void":
            return False
    elif ret.group("value") != "%" + str(match.group("result")):
        return False
    arguments = _call_arguments(match)
    if arguments is None or len(arguments) > _MAX_PARAMETERS:
        return False
    for ty, _value in arguments:
        if ty not in _REGISTER_TYPES:
            return False
    return True


def _is_heavy(block: BasicBlock, return_type: str) -> bool:
    index = 0
    while index < len(block.instructions):
        inst = block.instructions[index]
        if inst.opcode in ("alloca", "invoke", "landingpad", "va_arg"):
            return True
        if inst.opcode == "call" and not _is_light_call(block, index, return_type):
            return True
        index += 1
    return False


def _return_into_calls(fn: Function) -> bool:
    """Give a block ending in a call and ``br label %T`` the return of a
    ``T`` that only returns, so the call becomes a tail call."""
    by_name = {block.name: block for block in fn.blocks}
    changed = False
    for block in fn.blocks:
        term = block.terminator
        if term is None or len(block.instructions) < 2:
            continue
        if block.instructions[-2].opcode != "call":
            continue
        branch = _UNCOND_BR_RE.match(term.text)
        if branch is None:
            continue
        target = by_name.get(branch.group("target"))
        if target is None or target is block:
            continue
        phis = [inst for inst in target.instructions if inst.opcode == "phi"]
        rest = [inst for inst in target.instructions if inst.opcode != "phi"]
        if len(rest) != 1:
            continue
        ret = _RET_RE.match(rest[0].text)
        if ret is None:
            continue
        ty = ret.group("ty")
        value = ret.group("value")
        if ty == "void":
            if phis:
                continue
            block.instructions[-1] = Instruction.from_text("  ret void\n")
            changed = True
            continue
        if value is None:
            continue
        returned = value
        phi_value = None
        for phi in phis:
            if "%" + str(phi.result_name) == value:
                for incoming, label in _phi_incoming(phi):
                    if label == block.name:
                        phi_value = incoming
        if len(phis) > 1 or (phis and phi_value is None):
            continue
        if phi_value is not None:
            returned = phi_value
        block.instructions[-1] = Instruction.from_text("  ret " + ty + " " + returned + "\n")
        for phi in phis:
            _remove_phi_entry(phi, block.name)
        changed = True
    return changed


def _remove_phi_entry(phi: Instruction, label: str) -> None:
    match = _PHI_RE.match(phi.text)
    if match is None:
        return
    kept = [(value, pred) for value, pred in _phi_incoming(phi) if pred != label]
    entries = ", ".join("[ " + value + ", %" + pred + " ]" for value, pred in kept)
    phi.text = "  %" + match.group("name") + " = phi " + match.group("ty") + " " + entries + "\n"


def _keep_phi_entries(phi: Instruction, labels: set[str]) -> None:
    match = _PHI_RE.match(phi.text)
    if match is None:
        return
    kept = [(value, pred) for value, pred in _phi_incoming(phi) if pred in labels]
    entries = ", ".join("[ " + value + ", %" + pred + " ]" for value, pred in kept)
    phi.text = "  %" + match.group("name") + " = phi " + match.group("ty") + " " + entries + "\n"


def _cyclic_components(names: list[str], successors: dict[str, list[str]]) -> dict[str, int]:
    """Map each block on a cycle to its strongly connected component id
    (iterative Tarjan); blocks on no cycle are absent."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    component: dict[str, int] = {}
    counter = 0
    component_id = 0
    for root in names:
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child = work.pop()
            if child == 0:
                index_of[node] = counter
                low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            successors_of = successors.get(node, [])
            descended = False
            while child < len(successors_of):
                nxt = successors_of[child]
                child += 1
                if nxt not in index_of:
                    work.append((node, child))
                    work.append((nxt, 0))
                    descended = True
                    break
                if nxt in on_stack and index_of[nxt] < low[node]:
                    low[node] = index_of[nxt]
            if descended:
                continue
            if work:
                parent = work[-1][0]
                if low[node] < low[parent]:
                    low[parent] = low[node]
            if low[node] == index_of[node]:
                members: list[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    members.append(top)
                    if top == node:
                        break
                self_loop = node in successors.get(node, [])
                if len(members) > 1 or self_loop:
                    for member in members:
                        component[member] = component_id
                component_id += 1
    return component


def _reachable(start: str, excluded: set[str], successors: dict[str, list[str]]) -> set[str]:
    """Blocks reachable from ``start`` through blocks outside ``excluded``."""
    seen: set[str] = set()
    work = [start]
    while work:
        name = work.pop()
        if name in seen or name in excluded:
            continue
        seen.add(name)
        for nxt in successors.get(name, []):
            if nxt not in seen:
                work.append(nxt)
    return seen


_WORK_OPCODES = ("load", "store", "atomicrmw", "cmpxchg", "call")


def _has_working_fast_exit(entry: str, kept: set[str], by_name, successors) -> bool:
    """Whether a kept ``ret`` is reached through a block that reads or
    writes memory. A function whose only kept exit is an argument test
    (``free(NULL)``) would send every real call through the slow path
    behind one more branch."""
    seen: set[str] = set()
    work = [(entry, False)]
    while work:
        name, worked = work.pop()
        block = by_name[name]
        if not worked:
            for inst in block.instructions:
                if inst.opcode in _WORK_OPCODES:
                    worked = True
        key = name + ("/1" if worked else "/0")
        if key in seen:
            continue
        seen.add(key)
        term = block.terminator
        if worked and term is not None and term.opcode == "ret":
            return True
        for nxt in successors.get(name, []):
            if nxt in kept:
                work.append((nxt, worked))
    return False


def _outline_function(fn: Function, taken: set[str], min_instructions: int) -> list[Function]:
    return_type = _return_type(fn)
    if return_type not in _RETURN_TYPES or not fn.blocks:
        return []
    if "personality" in fn.trailing or "personality" in fn.header_line:
        return []
    for arg in fn.args:
        if not arg.name or arg.ty == "...":
            return []
    count = 0
    for block in fn.blocks:
        count += len(block.instructions)
    if count <= min_instructions:
        return []
    names = [block.name for block in fn.blocks]
    by_name = {block.name: block for block in fn.blocks}
    successors: dict[str, list[str]] = {}
    predecessors: dict[str, list[str]] = {name: [] for name in names}
    for block in fn.blocks:
        targets = _successors(block)
        successors[block.name] = targets
        for target in targets:
            if target in predecessors and block.name not in predecessors[target]:
                predecessors[target].append(block.name)
    heavy = {block.name for block in fn.blocks if _is_heavy(block, return_type)}
    if not heavy:
        return []
    components = _cyclic_components(names, successors)
    tainted_components = {components[name] for name in heavy if name in components}
    excluded: set[str] = set(heavy)
    for name in names:
        if name in components and components[name] in tainted_components:
            excluded.add(name)
    entry = names[0]
    kept: set[str] = set()
    while True:
        if entry in excluded:
            return []
        kept = _reachable(entry, excluded, successors)
        moved = False
        for name in sorted(kept):
            for target in successors.get(name, []):
                if target not in kept and target in components:
                    # A slow path entered on a cycle: cut one block earlier.
                    for source in predecessors.get(target, []):
                        if source in kept and source not in excluded:
                            excluded.add(source)
                            moved = True
        if not moved:
            break
    seeds: list[str] = []
    for name in names:
        if name not in kept:
            continue
        for target in successors.get(name, []):
            if target not in kept and target not in seeds:
                seeds.append(target)
    if not seeds or len(seeds) > _MAX_SLOW_PATHS:
        return []
    if not _has_working_fast_exit(entry, kept, by_name, successors):
        return []
    definitions: dict[str, Instruction] = {}
    for block in fn.blocks:
        for inst in block.instructions:
            if inst.result_name is not None:
                definitions[inst.result_name] = inst
    argument_types = {arg.name: arg.ty for arg in fn.args}
    plans = []
    copied = 0
    for seed in seeds:
        region = _reachable(seed, set(), successors)
        if seed in components:
            return []
        defined: set[str] = set()
        for name in region:
            for inst in by_name[name].instructions:
                if inst.result_name is not None:
                    defined.add(inst.result_name)
                copied += 1
        seed_phis = [inst for inst in by_name[seed].instructions if inst.opcode == "phi"]
        live: set[str] = set()
        for name in region:
            for inst in by_name[name].instructions:
                if inst.opcode == "phi":
                    if name == seed:
                        continue
                    for value, pred in _phi_incoming(inst):
                        if pred in region and value.startswith("%") and value[1:] not in defined:
                            live.add(value[1:])
                    continue
                for value in _value_uses(inst):
                    if value not in defined:
                        live.add(value)
        for phi in seed_phis:
            live.discard(str(phi.result_name))
        # The function's own arguments come first, in their order, so a cut
        # that needs nothing else forwards its registers unchanged.
        parameters: list[tuple[str, str]] = []
        for arg in fn.args:
            if arg.name in live:
                parameters.append((arg.name, arg.ty))
        for value in sorted(live):
            if value in argument_types:
                continue
            if value not in definitions:
                return []
            parameters.append((value, _result_type(definitions[value])))
        for phi in seed_phis:
            match = _PHI_RE.match(phi.text)
            if match is None:
                return []
            parameters.append((str(phi.result_name), match.group("ty")))
        if len(parameters) > _MAX_PARAMETERS:
            return []
        for _value, ty in parameters:
            if ty not in _REGISTER_TYPES and ty not in _NARROW_TYPES:
                return []
        plans.append((seed, region, seed_phis, parameters))
    if copied > _MAX_COPIED_INSTRUCTIONS:
        return []
    # Every copy is taken before any kept terminator is redirected: a slow
    # path that rejoins the kept code copies kept blocks, and must copy
    # their original branches, not another cut's stub.
    outlined: list[Function] = []
    callees: list[str] = []
    for number, plan in enumerate(plans):
        seed, region, seed_phis, parameters = plan
        name = fn.name + ".slow." + str(number)
        suffix = number
        while name in taken:
            suffix += 1
            name = fn.name + ".slow." + str(suffix)
        taken.add(name)
        callees.append(name)
        outlined.append(_outlined_function(fn, name, return_type, seed, region, seed_phis,
                                           parameters, by_name, names))
    stubs: list[BasicBlock] = []
    for number, plan in enumerate(plans):
        seed, region, seed_phis, parameters = plan
        name = callees[number]
        for source in predecessors.get(seed, []):
            if source not in kept:
                continue
            stub = _stub_block(fn, name, return_type, seed, source, seed_phis, parameters,
                               len(stubs))
            stubs.append(stub)
            term = by_name[source].terminator
            assert term is not None
            term.text = re.sub(r"label\s+%" + re.escape(seed) + r"(?![\w.$-])",
                               "label %" + stub.name, term.text)
    kept_blocks = [block for block in fn.blocks if block.name in kept]
    present = {block.name for block in kept_blocks}
    for block in kept_blocks:
        for inst in block.instructions:
            if inst.opcode == "phi":
                _keep_phi_entries(inst, present)
    fn.blocks = kept_blocks + stubs
    return outlined


def _outlined_function(fn: Function, name: str, return_type: str, seed: str,
                       region: set[str], seed_phis: list[Instruction],
                       parameters: list[tuple[str, str]], by_name, names: list[str]) -> Function:
    args: list[Argument] = []
    widened: list[Instruction] = []
    for value, ty in parameters:
        if ty in _NARROW_TYPES:
            args.append(Argument("i64", value + ".wide"))
            widened.append(Instruction.from_text(
                "  %" + value + " = trunc i64 %" + value + ".wide to " + ty + "\n"))
        else:
            args.append(Argument(ty, value))
    arg_text = ", ".join(arg.serialize() for arg in args)
    # noinline: the runtime's object phase runs the definition inliner again
    # on modules that import cross-module definitions, and a slow path small
    # enough for it would be inlined straight back into its fast function.
    trailing = " noinline" + fn.trailing
    header = "define internal " + return_type + " @" + name + "(" + arg_text + ")" + trailing + "{\n"
    blocks: list[BasicBlock] = []
    if widened:
        entry_name = seed + ".slow.entry"
        widened.append(Instruction.from_text("  br label %" + seed + "\n"))
        blocks.append(BasicBlock(entry_name, entry_name + ":\n", widened))
    seed_block = by_name[seed]
    seed_copy = [Instruction.from_text(inst.text) for inst in seed_block.instructions
                 if inst.opcode != "phi"]
    blocks.append(BasicBlock(seed, seed + ":\n", seed_copy))
    for block_name in names:
        if block_name == seed or block_name not in region:
            continue
        block = by_name[block_name]
        copy = [Instruction.from_text(inst.text) for inst in block.instructions]
        for inst in copy:
            if inst.opcode == "phi":
                _keep_phi_entries(inst, region)
        blocks.append(BasicBlock(block_name, block_name + ":\n", copy))
    return Function(header_line=header, name=name, args=args, trailing=trailing,
                    blocks=blocks, footer_line="}\n")


def _stub_block(fn: Function, callee: str, return_type: str, seed: str, source: str,
                seed_phis: list[Instruction], parameters: list[tuple[str, str]],
                number: int) -> BasicBlock:
    edge_values: dict[str, str] = {}
    for phi in seed_phis:
        for value, pred in _phi_incoming(phi):
            if pred == source:
                edge_values[str(phi.result_name)] = value
    label = seed + ".slow.call." + str(number)
    instructions: list[Instruction] = []
    types: list[str] = []
    arguments: list[str] = []
    widen = 0
    for value, ty in parameters:
        spelled = edge_values.get(value, "%" + value)
        if ty in _NARROW_TYPES:
            wide = "%slow.wide." + str(number) + "." + str(widen)
            widen += 1
            instructions.append(Instruction.from_text(
                "  " + wide + " = zext " + ty + " " + spelled + " to i64\n"))
            types.append("i64")
            arguments.append("i64 " + wide)
        else:
            types.append(ty)
            arguments.append(ty + " " + spelled)
    call = "call " + return_type + " (" + ", ".join(types) + ") @" + callee + "(" + ", ".join(arguments) + ")"
    if return_type == "void":
        instructions.append(Instruction.from_text("  " + call + "\n"))
        instructions.append(Instruction.from_text("  ret void\n"))
    else:
        result = "%slow.result." + str(number)
        instructions.append(Instruction.from_text("  " + result + " = " + call + "\n"))
        instructions.append(Instruction.from_text("  ret " + return_type + " " + result + "\n"))
    return BasicBlock(label, label + ":\n", instructions)


def outline_slow_paths_text(ir_text: str) -> tuple[str, bool]:
    """Outline frame-forcing slow paths of every eligible definition."""
    return outline_slow_paths_sized(ir_text, _MIN_INSTRUCTIONS)


_TRIPLE_RE = re.compile(r'^target triple = "(?P<triple>[^"]*)"', re.M)
# Targets whose backend runs a function with only sibling calls without a
# frame. Elsewhere (the x86-64 backends make no sibling call) an outlined
# slow path would cost a call and return while the fast path kept its
# frame.
_OUTLINING_ARCHITECTURES = ("aarch64", "arm64")


def outline_slow_paths_sized(text: str, min_instructions: int) -> tuple[str, bool]:
    """outline_slow_paths_text with an explicit size floor (tests use 0)."""
    triple = _TRIPLE_RE.search(text)
    if triple is None or triple.group("triple").split("-", 1)[0] not in _OUTLINING_ARCHITECTURES:
        return text, False
    module = MutableModule.parse(text)
    taken = {fn.name for fn in module.functions}
    for line in module.declarations:
        match = re.search(r"@([\w.$-]+)\(", line)
        if match:
            taken.add(match.group(1))
    added: list[Function] = []
    returned = False
    for fn in list(module.functions):
        # A call that only branches to a return gets that return first, in
        # every function: it becomes a tail call (LLVM's tail duplication of
        # returns into call blocks).
        if _return_type(fn) in _RETURN_TYPES and _return_into_calls(fn):
            returned = True
        added.extend(_outline_function(fn, taken, min_instructions))
    if not added and not returned:
        return text, False
    module.functions.extend(added)
    return module.serialize(), True
