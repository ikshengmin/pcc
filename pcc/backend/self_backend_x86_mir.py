from __future__ import annotations

"""Opt-in x64 typed machine IR/register planning experiment.

One per-function allocation owner. The target-neutral indexed kernel remains
the source of virtual IDs and immutable payloads; no reconstructed whole
module instruction graph is required. Pinned root/reload/alloca homes are
not allocatable registers. Machine constraints are explicit, not inferred
from post-selection text. Unsupported functions use the whole legacy path.

Explicit host-compiler x64 Linux application mode only; native pcc1
allocator execution and default Bootstrap routes remain unqualified/OFF.
"""

from .self_backend_ir import (
    ParsedFunction,
    PARSED_INSTRUCTION_KIND_ALLOCA as ALLOCA,
    PARSED_INSTRUCTION_KIND_ATOMICRMW as ATOMICRMW,
    PARSED_INSTRUCTION_KIND_BINOP as BINOP,
    PARSED_INSTRUCTION_KIND_CALL as CALL,
    PARSED_INSTRUCTION_KIND_CMPXCHG as CMPXCHG,
    PARSED_INSTRUCTION_KIND_EXTRACTVALUE as EXTRACTVALUE,
    _interned_operands,
    PARSED_INSTRUCTION_KIND_CAST as CAST,
    PARSED_INSTRUCTION_KIND_FENCE as FENCE,
    PARSED_INSTRUCTION_KIND_FREEZE as FREEZE,
    PARSED_INSTRUCTION_KIND_GEP as GEP,
    PARSED_INSTRUCTION_KIND_ICMP as ICMP,
    PARSED_INSTRUCTION_KIND_LOAD as LOAD,
    PARSED_INSTRUCTION_KIND_LOAD_ATOMIC as LOAD_ATOMIC,
    PARSED_INSTRUCTION_KIND_SELECT as SELECT,
    PARSED_INSTRUCTION_KIND_STORE as STORE,
    PARSED_INSTRUCTION_KIND_STORE_ATOMIC as STORE_ATOMIC,
    PARSED_INSTRUCTION_KIND_BR as BR,
    PARSED_INSTRUCTION_KIND_BR_COND as BR_COND,
    PARSED_INSTRUCTION_KIND_RET as RET,
    PARSED_INSTRUCTION_KIND_RET_VOID as RET_VOID,
    PARSED_INSTRUCTION_KIND_SWITCH as SWITCH,
    PARSED_INSTRUCTION_KIND_UNREACHABLE as UNREACHABLE,
)
from .self_backend_kernel import (
    IndexedFunctionKernel,
    TYPE_KIND_INT, TYPE_KIND_PTR, TYPE_KIND_VOID, TYPE_KIND_STRUCT,
    get_indexed_function_kernel,
)
from .self_backend_precise_stackmaps import FunctionStackMapPlan
from .self_backend_value_arena import CompilerInt4
from .self_backend_x86_dynamic_stack import has_checked_dynamic_stack

from pcc.unsafe import free, int_to_ptr, load_i32, malloc, ptr_to_int, store_i32
import struct

# Physical x86 register IDs. rsp/rbp are fixed frame registers; r10/r11
# belong exclusively to lowering scratch and edge-cycle resolution.
CALLER_REGISTERS = (0, 1, 2, 6, 7, 8, 9)
SAVED_REGISTERS = (3, 12, 13, 14, 15)
SYSV_ARG_REGISTERS = (7, 6, 2, 1, 8, 9)
ALL_REGISTERS = CALLER_REGISTERS + SAVED_REGISTERS
CALLER_MASK = sum(1 << register for register in CALLER_REGISTERS)
ALL_REGISTER_MASK = sum(1 << register for register in ALL_REGISTERS)

_OPERATIONS = (
    ALLOCA, ATOMICRMW, BINOP, CALL, CAST, CMPXCHG, EXTRACTVALUE, FENCE, FREEZE, GEP, ICMP,
    LOAD, LOAD_ATOMIC, SELECT, STORE, STORE_ATOMIC,
)
_TERMINATORS = (BR, BR_COND, RET, RET_VOID, SWITCH, UNREACHABLE)


class X86MIRValue:
    """Private typed machine record, immutable under its function owner."""
    __slots__ = ('value_id', 'type_id', 'width', 'pointer', 'home', 'addressable', 'used')

    def __init__(self, value_id: int, type_id: int, width: int, pointer: bool, home: int, addressable: bool, used: bool) -> None:
        self.value_id = value_id
        self.type_id = type_id
        self.width = width
        self.pointer = pointer
        self.home = home
        self.addressable = addressable
        self.used = used


class X86MIRInstruction:
    """Private typed machine record, immutable under its function owner."""
    __slots__ = ('instruction_id', 'kind', 'result', 'inputs', 'clobbers', 'fixed_operands', 'fixed_result', 'local_fixed_clobber', 'volatile')

    def __init__(self, instruction_id: int, kind: int, result: int, inputs: tuple[int, ...], clobbers: int, fixed_operands: tuple[int, ...], fixed_result: int, local_fixed_clobber: bool, volatile: bool) -> None:
        self.instruction_id = instruction_id
        self.kind = kind
        self.result = result
        self.inputs = inputs
        self.clobbers = clobbers
        self.fixed_operands = fixed_operands
        self.fixed_result = fixed_result
        self.local_fixed_clobber = local_fixed_clobber
        self.volatile = volatile


class X86MIRBlock:
    """Private typed machine record, immutable under its function owner."""
    __slots__ = ('block_id', 'instructions', 'phi_defs', 'terminator_kind', 'terminator_inputs', 'successors')

    def __init__(self, block_id: int, instructions: tuple[X86MIRInstruction, ...], phi_defs: tuple[int, ...], terminator_kind: int, terminator_inputs: tuple[int, ...], successors: tuple[int, ...]) -> None:
        self.block_id = block_id
        self.instructions = instructions
        self.phi_defs = phi_defs
        self.terminator_kind = terminator_kind
        self.terminator_inputs = terminator_inputs
        self.successors = successors


class X86MIRFunction:
    """Private typed machine record, immutable under its function owner."""
    __slots__ = ('values', 'blocks', 'arguments', 'edge_uses', 'edge_copies', 'pinned', 'cas_extracts', 'cas_pairs', 'root_offsets')

    def __init__(self, values: tuple[X86MIRValue, ...], blocks: tuple[X86MIRBlock, ...], arguments: tuple[int, ...], edge_uses: dict[tuple[int, int], tuple[int, ...]], edge_copies: dict[tuple[int, int], tuple[tuple[int, int, int], ...]], pinned: tuple[int, ...], cas_extracts: dict[int, tuple[int, int]], cas_pairs: tuple[int, ...], root_offsets: tuple[int, ...]) -> None:
        self.values = values
        self.blocks = blocks
        self.arguments = arguments
        self.edge_uses = edge_uses
        self.edge_copies = edge_copies
        self.pinned = pinned
        self.cas_extracts = cas_extracts
        self.cas_pairs = cas_pairs
        self.root_offsets = root_offsets


class X86MIRAllocation:
    """Private typed machine record, immutable under its function owner."""
    __slots__ = ('registers', 'interference', 'allowed', 'live_in', 'live_out', 'saved', 'instruction_live_after', 'block_body_live')

    def __init__(self, registers: tuple[int, ...], interference: tuple[int, ...], allowed: tuple[int, ...], live_in: tuple[int, ...], live_out: tuple[int, ...], saved: tuple[int, ...], instruction_live_after: tuple[tuple[int | None, ...], ...], block_body_live: tuple[int, ...]) -> None:
        self.registers = registers
        self.interference = interference
        self.allowed = allowed
        self.live_in = live_in
        self.live_out = live_out
        self.saved = saved
        self.instruction_live_after = instruction_live_after
        self.block_body_live = block_body_live


def _scalar_width(kernel: IndexedFunctionKernel, type_id: int) -> int:
    if type_id < 0:
        return -1
    header: CompilerInt4 = kernel.type_header(type_id)
    if header.first == TYPE_KIND_PTR:
        return 8
    if header.first == TYPE_KIND_VOID:
        return 0
    if header.first == TYPE_KIND_INT and header.second in (1, 8, 16, 32, 64):
        return max(1, header.second // 8)
    return -1


def _root_home_offsets(plan: FunctionStackMapPlan) -> set[int]:
    offsets: set[int] = set()
    if plan.packed_records is not None:
        packed = plan.packed_records
        for index in range(len(packed.location_scalars) // 2):
            packed_location = packed.location_scalars.get2_unchecked(index)
            offsets.add(-packed_location.first)
        for index in range(len(packed.reload_scalars) // 3):
            packed_reload = packed.reload_scalars.get3_unchecked(index)
            offsets.add(-packed_reload.first)
            offsets.add(-packed_reload.second)
    else:
        for record in plan.records:
            for location in record.locations:
                offsets.add(-location.offset)
            for reload in record.reloads:
                offsets.add(-reload.source_offset)
                offsets.add(-reload.destination_offset)
    return offsets


def _overlaps(offset: int, width: int, root_offsets: set[int],
              addressable: list[tuple[int, int]]) -> bool:
    low = offset - width
    if root_offsets:
        for root in range(low + 1, offset + 8):
            if root in root_offsets:
                return True
    for begin, end in addressable:
        if low < end and begin < offset:
            return True
    return False


def _machine_constraints(kernel: IndexedFunctionKernel, metadata: CompilerInt4):
    kind = metadata.first
    if kind == CALL:
        if metadata.second < 0:
            return None
        header: CompilerInt4 = kernel.call_header(metadata.second)
        span: CompilerInt4 = kernel.call_span(metadata.second)
        callee = kernel.call_texts[header.second]
        # Intrinsics, aggregate/variadic and indirect ABI forms require an
        # explicit typed machine instruction. Never silently mix allocators.
        if header.third & 3:
            return None
        if callee.startswith("llvm.trap") and span.first==0:
            return 0, (), -1, False
        if callee.startswith("llvm.memset.") and span.first==4:
            # Inline loop/constant fill use fixed RCX and AL/RAX; no
            # ordinary ABI call. Conservatively constrain all operands.
            return (1<<0)|(1<<1), (), -1, True
        if callee.startswith("llvm."):
            return None
        if _scalar_width(kernel, header.first) < 0:
            return None
        for index in range(span.first):
            argument = kernel.call_arg(header.fourth + index)
            if _scalar_width(kernel, argument.first) <= 0 or argument.fourth:
                return None
        fixed = tuple(SYSV_ARG_REGISTERS[index] if index < 6 else -1
                      for index in range(span.first))
        return CALLER_MASK, fixed, (0 if _scalar_width(kernel, header.first) else -1), False
    if kind == BINOP:
        if metadata.second < 0:
            return None
        record: CompilerInt4 = kernel.instruction_record(metadata.second)
        op = kernel.call_texts[record.first]
        if op in ("udiv", "sdiv", "urem", "srem"):
            return (1 << 0) | (1 << 2), (), -1, True
        if op in ("shl", "lshr", "ashr") and record.fourth >= 0:
            return 1 << 1, (), -1, True
        if op not in ("add", "sub", "mul", "and", "or", "xor", "shl", "lshr", "ashr"):
            return None
    if kind == CMPXCHG:
        return 1 << 0, (), -1, True
    if kind == ATOMICRMW:
        # Current xadd/locked alternatives may use the implicit accumulator;
        # reserve it for the entire instruction until lowering specializes.
        return 1 << 0, (), -1, True
    return 0, (), -1, False


def _ephemeral_cas_pairs(kernel: IndexedFunctionKernel, plan: FunctionStackMapPlan):
    """Only immediate scalar extracts from exact CMPXCHG pair results.

    Old value stays in RAX; success stays in FLAGS. No aggregate home
    may escape, cross a suffix/reload, or be consumed by another operation.
    """
    suffixes=set()
    if plan.packed_records is not None:
        packed=plan.packed_records
        for index in range(len(packed.suffix_routes)//3):
            route=packed.suffix_routes.get3_unchecked(index)
            suffixes.add((route.first,route.second))
    else:
        for name,position,_label in plan.instruction_suffix_labels:
            suffixes.add((kernel.block_id(name),position))
    pair_ids=set()
    extracts={}
    approved_uses={}
    for block_id in range(len(kernel.block_names)):
        block=kernel.block_fact(block_id)
        for position in range(block.second):
            ins=block.first+position
            metadata=kernel.instruction_metadata_by_id(ins)
            if metadata.first!=CMPXCHG:continue
            pair=kernel.defined_value_id(block_id,position)
            if pair<0:return None
            data=_interned_operands(kernel.instruction_data_by_id(ins,block_id,position))
            _name,pair_type,_ptr_type,_ptr,value_type,_old,_new,_success,_failure=data
            if (not pair_type.is_struct or len(pair_type.fields)!=2
                    or pair_type.fields[0].describe()!=value_type.describe()
                    or not pair_type.fields[1].is_int or pair_type.fields[1].width!=1
                    or _scalar_width(kernel,kernel.intern_type(value_type)) not in (1,2,4,8)
                    or (block_id,position) in suffixes):
                return None
            pair_ids.add(pair)
            next_pos=position+1
            prior=-1
            while next_pos<block.second:
                next_ins=block.first+next_pos
                next_meta=kernel.instruction_metadata_by_id(next_ins)
                if next_meta.first!=EXTRACTVALUE:break
                projection=_interned_operands(kernel.instruction_data_by_id(next_ins,block_id,next_pos))
                _result,_aggregate,source,indices,result_type,_offset=projection
                if kernel.value_id(source)!=pair:break
                if len(indices)!=1 or indices[0] not in (0,1) or indices[0]<=prior:
                    return None
                component=indices[0]
                if result_type.describe()!=pair_type.fields[component].describe() or (block_id,next_pos) in suffixes:
                    return None
                extracts[next_ins]=(pair,component)
                approved_uses[pair]=approved_uses.get(pair,0)+1
                prior=component
                next_pos+=1
    if not pair_ids:return pair_ids,extracts
    actual_uses={}
    for block_id in range(len(kernel.block_names)):
        phi_fact=kernel.block_phi_fact(block_id)
        for index in range(phi_fact.second):
            phi=kernel.phi_record(phi_fact.first+index)
            for j in range(phi.fourth):
                if kernel.phi_incoming(phi.third+j).first in pair_ids:return None
        block=kernel.block_fact(block_id)
        for position in range(block.second):
            for index in range(kernel.instruction_use_count(block_id,position)):
                value=kernel.instruction_use_id(block_id,position,index)
                if value in pair_ids:
                    if block.first+position not in extracts:return None
                    actual_uses[value]=actual_uses.get(value,0)+1
        for index in range(kernel.terminator_use_count(block_id)):
            if kernel.terminator_use_id(block_id,index) in pair_ids:return None
    if actual_uses!=approved_uses:return None
    return pair_ids,extracts


def build_x86_mir(func: ParsedFunction, plan: FunctionStackMapPlan) -> X86MIRFunction | None:
    """Admit the complete function, or no machine IR at all."""
    kernel = get_indexed_function_kernel(func)
    if (type(func) is not ParsedFunction or type(kernel) is not IndexedFunctionKernel
            or type(plan) is not FunctionStackMapPlan
            or func.is_vararg or func.hidden_sret_slot is not None
            or func.name == "_start" or func.indexed_slot_projection
            or has_checked_dynamic_stack(func, kernel)
            or len(kernel.error_edge_scalars)
            or not (func.ret_type.is_void or func.ret_type.is_ptr or (
                func.ret_type.is_int and func.ret_type.width in (1, 8, 16, 32, 64)))):
        return None
    # Ordinary scalar functions have no aggregate CAS result. Inspect
    # value types once; only actual struct values require the full paired
    # extraction/escape scan. Unsupported FP/vector/other widths fall back
    # before any CFG construction.
    typed=[]
    has_pair_candidate=False
    for value_id in range(len(kernel.value_names)):
        type_id=kernel.value_type_id(value_id)
        if type_id<0:return None
        header=kernel.type_header(type_id)
        pointer=header.first==TYPE_KIND_PTR
        if pointer:width=8
        elif header.first==TYPE_KIND_INT and header.second in (1,8,16,32,64):
            width=max(1,header.second//8)
        elif header.first==TYPE_KIND_STRUCT:
            width=-1;has_pair_candidate=True
        else:return None
        typed.append((type_id,width,pointer))
    pair_ids=set();cas_extracts={}
    if has_pair_candidate:
        cas=_ephemeral_cas_pairs(kernel,plan)
        if cas is None:return None
        pair_ids,cas_extracts=cas
    roots = _root_home_offsets(plan)
    addressable = [(slot.offset - slot.allocated_type.slot_size, slot.offset)
                   for slot in func.alloca_slots.values()]
    values: list[X86MIRValue] = []
    pinned: list[int] = []
    for value_id,(type_id,width,pointer) in enumerate(typed):
        if value_id in pair_ids:
            width=kernel.type_layout(type_id).first
        if width <= 0:
            return None
        home = kernel.value_slot_offset(value_id)
        alloca = kernel.alloca_offset(value_id) >= 0
        used = kernel.value_is_used(value_id)
        values.append(X86MIRValue(value_id, type_id, width, pointer, home, alloca, used))
        overlap=home>=width and _overlaps(home,width,roots,addressable)
        if value_id in pair_ids and overlap:
            return None
        if value_id in pair_ids or alloca or home < width or overlap:
            pinned.append(value_id)
    arguments: list[int] = []
    for arg in func.args:
        value_id = kernel.value_id(arg.name)
        if value_id < 0:
            return None
        arguments.append(value_id)

    blocks: list[X86MIRBlock] = []
    edge_uses: dict[tuple[int, int], tuple[int, ...]] = {}
    pending_edges: dict[tuple[int, int], list[int]] = {}
    pending_copies: dict[tuple[int,int],list[tuple[int,int,int]]] = {}
    block_count = len(kernel.block_names)
    for block_id in range(block_count):
        phi_fact = kernel.block_phi_fact(block_id)
        phi_defs: list[int] = []
        for phi_index in range(phi_fact.second):
            phi: CompilerInt4 = kernel.phi_record(phi_fact.first + phi_index)
            if phi.first < 0 or _scalar_width(kernel, phi.second) <= 0:
                return None
            phi_defs.append(phi.first)
            seen_predecessors=set()
            for incoming_index in range(phi.fourth):
                incoming = kernel.phi_incoming(phi.third + incoming_index)
                if not 0 <= incoming.second < block_count:
                    return None
                edge = (incoming.second, block_id)
                # Legacy edge semantics select the first matching row
                # for duplicate switch/PHI predecessor entries.
                if incoming.second not in seen_predecessors:
                    seen_predecessors.add(incoming.second)
                    pending_copies.setdefault(edge,[]).append((phi.first,phi.second,incoming.first))
                if incoming.first >= 0:
                    pending_edges.setdefault(edge, []).append(incoming.first)
        block_fact: CompilerInt4 = kernel.block_fact(block_id)
        operations: list[X86MIRInstruction] = []
        for position in range(block_fact.second):
            instruction_id = block_fact.first + position
            metadata: CompilerInt4 = kernel.instruction_metadata_by_id(instruction_id)
            if metadata.first not in _OPERATIONS:
                return None
            if metadata.first==EXTRACTVALUE and instruction_id not in cas_extracts:
                return None
            constraints = _machine_constraints(kernel, metadata)
            if constraints is None:
                return None
            clobbers, fixed, result_reg, local = constraints
            result = kernel.defined_value_id(block_id, position)
            if metadata.first==CMPXCHG and result not in pair_ids:return None
            inputs = tuple(kernel.instruction_use_id(block_id, position, index)
                           for index in range(kernel.instruction_use_count(block_id, position)))
            operations.append(X86MIRInstruction(
                instruction_id, metadata.first, result, inputs, clobbers,
                fixed, result_reg, local,
                kernel.instruction_is_volatile_by_id(instruction_id),
            ))
        terminator = kernel.terminator_header(block_id)
        if terminator.first not in _TERMINATORS:
            return None
        term_inputs = tuple(kernel.terminator_use_id(block_id, index)
                            for index in range(kernel.terminator_use_count(block_id)))
        successors = tuple(kernel.terminator_successor_id(block_id, index)
                           for index in range(kernel.terminator_successor_count(block_id)))
        if any(not 0 <= successor < block_count for successor in successors):
            return None
        blocks.append(X86MIRBlock(
            block_id, tuple(operations), tuple(phi_defs), terminator.first,
            term_inputs, successors,
        ))
    for pending_key, pending_values in pending_edges.items():
        if pending_key[1] not in blocks[pending_key[0]].successors:
            return None
        edge_uses[pending_key] = tuple(pending_values)
    edge_copies={}
    for block in blocks:
        for successor in block.successors:
            edge=(block.block_id,successor)
            copies=pending_copies.get(edge,[])
            if len(copies)!=len(blocks[successor].phi_defs):return None
            if copies:edge_copies[edge]=tuple(copies)
    return X86MIRFunction(tuple(values), tuple(blocks), tuple(arguments), edge_uses, edge_copies, tuple(pinned), cas_extracts, tuple(sorted(pair_ids)), tuple(sorted(roots)))


def _mask(values: tuple[int, ...]) -> int:
    result = 0
    for value in values:
        if value >= 0:
            result |= 1 << value
    return result


def _bits(mask: int):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _liveness(mir: X86MIRFunction) -> tuple[list[int], list[int]]:
    count = len(mir.blocks)
    uses = [0] * count
    defs = [0] * count
    predecessors: list[list[int]] = [[] for _ in range(count)]
    for block in mir.blocks:
        defined = _mask(block.phi_defs)
        upward = 0
        for operation in block.instructions:
            upward |= _mask(operation.inputs) & ~defined
            if operation.result >= 0:
                defined |= 1 << operation.result
        upward |= _mask(block.terminator_inputs) & ~defined
        uses[block.block_id] = upward
        defs[block.block_id] = defined
        for successor in block.successors:
            predecessors[successor].append(block.block_id)
    live_in = [0] * count
    live_out = [0] * count
    queued = [True] * count
    pending = list(range(count))
    while pending:
        block_id = pending.pop()
        queued[block_id] = False
        out = 0
        for successor in mir.blocks[block_id].successors:
            out |= live_in[successor] | _mask(mir.edge_uses.get((block_id, successor), ()))
        entered = uses[block_id] | (out & ~defs[block_id])
        live_out[block_id] = out
        if entered == live_in[block_id]:
            continue
        live_in[block_id] = entered
        for predecessor in predecessors[block_id]:
            if not queued[predecessor]:
                queued[predecessor] = True
                pending.append(predecessor)
    return live_in, live_out


# Above this value count a dense n×n ordinal bitset can cost tens of
# megabytes even when the actual graph is sparse. Use exact packed adjacency
# for larger functions; small functions retain the inexpensive dense oracle.
_PACKED_GRAPH_MIN_VALUES = 4096
_PACKED_GRAPH_MAX_BYTES = 128 * 1024 * 1024


class MIRAllocationBudgetExceeded(Exception):
    """A proven compact graph cannot fit its private deterministic bound."""


class _PackedU32:
    """One private fixed-size uint32 arena, no managed edge objects."""
    __slots__ = ("address", "host")

    def __init__(self, count: int) -> None:
        self.address = 0
        self.host = bytearray()
        if count:
            try:
                address = ptr_to_int(malloc(count * 4))
            except NotImplementedError:
                # CPython semantic oracle; packed, unlike list[int].
                self.host = bytearray(count * 4)
                return
            if address == 0:
                raise MemoryError("x64 MIR packed graph allocation failed")
            self.address = address

    def get(self, index: int) -> int:
        if self.address:
            return load_i32(int_to_ptr(self.address), index * 4) & 0xFFFFFFFF
        return struct.unpack_from("<I", self.host, index * 4)[0]

    def set(self, index: int, value: int) -> None:
        if self.address:
            store_i32(int_to_ptr(self.address), index * 4, value)
        else:
            struct.pack_into("<I", self.host, index * 4, value)

    def close(self) -> None:
        if self.address:
            free(int_to_ptr(self.address))
            self.address = 0
        self.host = bytearray()


def _fill_packed_interference(mir: X86MIRFunction, candidate_mask: int,
                              live_in: list[int], live_out: list[int],
                              degree: list[int]):
    """Fill bounded symmetric CSR, then deduplicate each row in place.

    The first scan reserves exact edge-event capacities (duplicates allowed).
    Recompute the same definitions from the fixed point rather than retaining
    an extra edge list; row order is irrelevant to occupied-register unions.
    A reusable generation mark array gives the exact degree for color ranking.
    """
    starts = [0]
    for amount in degree:
        starts.append(starts[-1] + amount)
    arena = _PackedU32(starts[-1])
    cursor = starts[:-1]
    try:
        def append(value: int, live: int) -> None:
            if value < 0 or not candidate_mask & (1 << value):
                return
            others = live & candidate_mask & ~(1 << value)
            pos = cursor[value]
            for other in _bits(others):
                arena.set(pos, other)
                pos += 1
                arena.set(cursor[other], value)
                cursor[other] += 1
            cursor[value] = pos

        for block in mir.blocks:
            live = live_out[block.block_id] | _mask(block.terminator_inputs)
            for operation in reversed(block.instructions):
                dest = operation.result
                append(dest, live)
                if dest >= 0:
                    live &= ~(1 << dest)
                live |= _mask(operation.inputs)
            for phi in block.phi_defs:
                append(phi, live)
        if mir.blocks:
            for argument in mir.arguments:
                append(argument, live_in[0])
        marks = [-1] * len(degree)
        for value in range(len(degree)):
            begin = starts[value]
            end = starts[value + 1]
            if cursor[value] != end:
                raise RuntimeError("x64 MIR packed graph fill mismatch")
            write = begin
            for read in range(begin, end):
                other = arena.get(read)
                if marks[other] == value:
                    continue
                marks[other] = value
                if write != read:
                    arena.set(write, other)
                write += 1
            degree[value] = write - begin
        return arena, starts
    except BaseException:
        arena.close()
        raise


def allocate_x86_mir(mir: X86MIRFunction, *, keep_diagnostics: bool = True,
                     force_packed: bool = False) -> X86MIRAllocation:
    """Color virtual SSA by actual CFG interference and machine clobbers.

    Spill decisions refer to the authoritative frame plan. No second late
    allocator may claim these registers if this path is ultimately emitted.
    """
    count = len(mir.values)
    pinned = set(mir.pinned)
    candidate_mask = 0
    for mir_value in mir.values:
        if mir_value.used and mir_value.value_id not in pinned:
            candidate_mask |= 1 << mir_value.value_id
    live_in, live_out = _liveness(mir)
    packed = force_packed or count >= _PACKED_GRAPH_MIN_VALUES
    if packed and count > 0xFFFFFFFF:
        raise MIRAllocationBudgetExceeded("x64 MIR value IDs exceed uint32")
    interference = [] if packed else [0] * count
    degree = [0] * count if packed else []
    edge_events = 0
    allowed = [ALL_REGISTER_MASK if candidate_mask & (1 << value_id) else 0
               for value_id in range(count)]
    weights = [0] * count
    aliases: list[list[int]] = [[] for _ in range(count)]
    instruction_live_after: list[list[int | None]] = [[None] * len(block.instructions) for block in mir.blocks]
    block_body_live = [0] * len(mir.blocks)

    def interfere(value: int, live: int) -> None:
        nonlocal edge_events
        if value < 0 or not candidate_mask & (1 << value):
            return
        others = live & candidate_mask & ~(1 << value)
        if packed:
            amount = others.bit_count()
            edge_events += amount
            if edge_events * 8 > _PACKED_GRAPH_MAX_BYTES:
                raise MIRAllocationBudgetExceeded("x64 MIR packed interference bound")
            degree[value] += amount
            for other in _bits(others):
                degree[other] += 1
        else:
            interference[value] |= others
            for other in _bits(others):
                interference[other] |= 1 << value

    # Backward edges approximate weighted hot loop ranges for ranking only;
    # correctness is entirely the exact fixed-point live sets.
    loop_depth = [0] * len(mir.blocks)
    for block in mir.blocks:
        for target in block.successors:
            if target <= block.block_id:
                for member in range(target, block.block_id + 1):
                    loop_depth[member] += 1
    for block in mir.blocks:
        weight = 1 << min(3 * loop_depth[block.block_id], 9)
        live = live_out[block.block_id] | _mask(block.terminator_inputs)
        for value in _bits(_mask(block.terminator_inputs)):
            weights[value] += weight
        for position in range(len(block.instructions) - 1, -1, -1):
            operation = block.instructions[position]
            # Only actual pointer retirement needs the full post-operation
            # set. No death means lowering must not inspect or retain it.
            if keep_diagnostics or any(
                    v >= 0 and v not in pinned and mir.values[v].pointer
                    and not live & (1 << v) for v in operation.inputs):
                instruction_live_after[block.block_id][position] = live
            dest = operation.result
            dest_bit = 0 if dest < 0 else 1 << dest
            if operation.clobbers:
                affected = live & ~dest_bit
                if operation.local_fixed_clobber:
                    affected |= _mask(operation.inputs) | dest_bit
                for value in _bits(affected & candidate_mask):
                    allowed[value] &= ~operation.clobbers
            interfere(dest, live)
            if dest >= 0:
                weights[dest] += weight
                # Prefer legal coalescing with a dying input, especially
                # x86's destructive two-address operations.
                for operand in operation.inputs:
                    if operand >= 0 and not live & (1 << operand):
                        aliases[dest].append(operand)
                        aliases[operand].append(dest)
            live &= ~dest_bit
            inputs = _mask(operation.inputs)
            live |= inputs
            for value in _bits(inputs):
                weights[value] += weight
        block_body_live[block.block_id] = live
        for phi in block.phi_defs:
            interfere(phi, live)
            weights[phi] += weight
        live &= ~_mask(block.phi_defs)
    # Arguments are simultaneous entry definitions, not pre-colored ABI
    # aliases. The prologue's true parallel copies select their colors.
    entry_live = live_in[0] if mir.blocks else 0
    for argument in mir.arguments:
        interfere(argument, entry_live)
        weights[argument] += 1

    registers = [-1] * count
    arena = None
    starts: list[int] = []
    if packed:
        arena, starts = _fill_packed_interference(mir, candidate_mask, live_in, live_out, degree)
    else:
        degree = [row.bit_count() for row in interference]
    try:
        order = sorted(_bits(candidate_mask), key=lambda value: (-weights[value], -degree[value], value))
        for value in order:
            occupied = 0
            if arena is not None:
                for index in range(starts[value], starts[value] + degree[value]):
                    register = registers[arena.get(index)]
                    if register >= 0:
                        occupied |= 1 << register
            else:
                for other in _bits(interference[value]):
                    register = registers[other]
                    if register >= 0:
                        occupied |= 1 << register
            choices = allowed[value] & ~occupied
            if not choices:
                continue
            desired = -1
            for alias in aliases[value]:
                register = registers[alias]
                if register >= 0 and choices & (1 << register):
                    desired = register
                    break
            if desired < 0:
                for register in ALL_REGISTERS:
                    if choices & (1 << register):
                        desired = register
                        break
            registers[value] = desired
        if keep_diagnostics and arena is not None:
            # Explicit diagnostic callers retain the dense oracle projection.
            # The production emitter always requests keep_diagnostics=False.
            interference = [0] * count
            for value in range(count):
                row = 0
                for index in range(starts[value], starts[value] + degree[value]):
                    row |= 1 << arena.get(index)
                interference[value] = row
    finally:
        if arena is not None:
            arena.close()
    saved = tuple(register for register in SAVED_REGISTERS if register in registers)
    return X86MIRAllocation(tuple(registers), tuple(interference) if keep_diagnostics else (),
                            tuple(allowed) if keep_diagnostics else (),
                            tuple(live_in) if keep_diagnostics else (), tuple(live_out), saved,
                            tuple(tuple(row) for row in instruction_live_after),
                            tuple(block_body_live))
