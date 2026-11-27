from __future__ import annotations

"""Typed x64 MIR lowering, opt-in experiment.

A function chooses MIR or the entire legacy route. All allocated operands
belong to the MIR graph; the late frame-slot allocator is not run here.
"""

from . import self_backend_x86_64_linux as x64
from . import BackendUnavailable
from .self_backend_ir import TypeDesc, PARSED_INSTRUCTION_KINDS, _interned_operands
from .self_backend_parse import const_int_from_value
from .self_backend_kernel import get_indexed_function_kernel
from .self_backend_x86_mir import (
    X86MIRFunction, X86MIRAllocation, build_x86_mir, allocate_x86_mir,
    _bits, MIRAllocationBudgetExceeded,
)
from .x86_64_register_copies import _GP_NAMES, _REG_BASE, _WIDTH_SLOTS, _IMMEDIATE_RANGES
from .x86_64_frame_registers import saved_area_offset
from .self_backend_x86_frame import fixed_frame_padding, emit_fixed_frame_padding


class MIRUnsupported(Exception):
    pass


class Location:
    __slots__=("kind","payload","width")

    def __init__(self,kind:str,payload:int|str,width:int)->None:
        self.kind=kind
        self.payload=payload
        self.width=width

    def __eq__(self,other)->bool:
        return type(other) is Location and self.kind==other.kind and self.payload==other.payload and self.width==other.width

    def key(self):
        if self.kind in ("reg", "slot", "incoming", "outgoing"):
            return self.kind, self.payload
        return None


def _width(ty: TypeDesc) -> int:
    if ty.is_ptr:
        return 8
    if ty.is_int and ty.width in (1, 8, 16, 32, 64):
        return ty.slot_size
    raise MIRUnsupported("non-scalar machine operand")


_REGISTER_LOCATIONS=tuple(tuple(Location("reg",index,width) for width in (1,2,4,8))
                          for index in range(16))


def _register(index: int, width: int) -> Location:
    return _REGISTER_LOCATIONS[index][_WIDTH_SLOTS[width]]


def _reg_text(location: Location) -> str:
    return _GP_NAMES[int(location.payload)][_WIDTH_SLOTS[location.width]]


def _text(location: Location) -> str:
    if location.kind == "reg":
        return _reg_text(location)
    if location.kind == "imm":
        return str(location.payload)
    size = {1: "BYTE PTR", 2: "WORD PTR", 4: "DWORD PTR", 8: "QWORD PTR"}[location.width]
    if location.kind == "slot":
        return f"{size} [rbp - {location.payload}]"
    if location.kind == "incoming":
        return f"{size} [rbp + {location.payload}]"
    if location.kind == "outgoing":
        return f"{size} [rsp + {location.payload}]"
    raise MIRUnsupported("not an encoded operand")


def _memory(location: Location) -> bool:
    return location.kind in ("slot", "incoming", "outgoing")


def _alias(a: Location, b: Location) -> bool:
    return a.kind in ("reg","slot","incoming","outgoing") and a.kind==b.kind and a.payload==b.payload


def _immediate_ok(location: Location) -> bool:
    lo, hi = _IMMEDIATE_RANGES[location.width]
    return lo <= int(location.payload) <= hi


class Lowerer:
    __slots__=("func","plan","mir","allocation","kernel","pinned","flag_branches",
               "suffix_positions","compact_padding","frame_layout","physical_frame_size","locations",
               "_retire_register_owner","_retire_register_masks")

    def __init__(self, func, plan, mir: X86MIRFunction, allocation: X86MIRAllocation, compact_padding=None, frame_layout=None):
        self.func = func
        self.plan = plan
        self.mir = mir
        self.allocation = allocation
        self.kernel = get_indexed_function_kernel(func)
        self.pinned = set(mir.pinned)
        self.flag_branches: dict[int, str] = {}
        self.suffix_positions=set()
        if plan.packed_records is not None:
            packed=plan.packed_records
            for index in range(len(packed.suffix_routes)//3):
                route=packed.suffix_routes.get3_unchecked(index)
                self.suffix_positions.add((route.first,route.second))
        else:
            for name,position,_label in plan.instruction_suffix_labels:
                self.suffix_positions.add((self.kernel.block_id(name),position))
        self.compact_padding=compact_padding
        self.frame_layout=frame_layout
        self.physical_frame_size=func.frame_size if frame_layout is None else frame_layout.new_size
        # A virtual value has one immutable physical location; every
        # selector use consumes it rather than reconstructing a projection.
        locations=[]
        for fact in mir.values:
            if fact.addressable:
                offset=self.kernel.alloca_offset(fact.value_id)
                if frame_layout is not None:offset=frame_layout.remap(offset)
                locations.append(Location("address",offset,8))
            else:
                register=allocation.registers[fact.value_id]
                if register>=0:locations.append(_register(register,fact.width))
                elif fact.home>=fact.width:locations.append(Location("slot",fact.home,fact.width))
                else:locations.append(None)
        self.locations=tuple(locations)
        # Lazy, function-local physical occupancy index. Retain the exact
        # immutable assignment tuple as the cache owner; small functions and
        # slot-death paths keep the original live-set traversal.
        self._retire_register_owner: tuple[int, ...] | None = None
        self._retire_register_masks: tuple[int, ...] | None = None

    def location(self, value: str, ty: TypeDesc) -> Location:
        width = _width(ty)
        value_id = self.kernel.value_id(value)
        if value_id >= 0:
            fact = self.mir.values[value_id]
            if fact.width != width:
                raise MIRUnsupported("mismatched machine operand width")
            if fact.addressable and not ty.is_ptr:
                raise MIRUnsupported("alloca address as scalar")
            location=self.locations[value_id]
            if location is None:raise MIRUnsupported("missing scalar spill home")
            return location
        if value in ("null", "poison", "undef"):
            return Location("imm", 0, width)
        if value == "true":
            return Location("imm", 1, width)
        if value == "false":
            return Location("imm", 0, width)
        integer = const_int_from_value(value)
        if integer is not None:
            return Location("imm", integer, width)
        if value.startswith("@") and ty.is_ptr:
            return Location("global", value, 8)
        if ty.is_ptr and x64._is_constant_gep_value(value):
            return Location("constant_gep", value, 8)
        if ty.is_ptr and value.startswith("inttoptrconst:"):
            constant=const_int_from_value(value.split(":",1)[1])
            if constant is not None:return Location("imm",constant,8)
        raise MIRUnsupported("unsupported scalar rematerialization " + value)

    def destination(self, value: str, ty: TypeDesc) -> Location | None:
        if value not in self.func.value_slots:
            return None
        return self.location(value, ty)

    def copy(self, dest: Location, source: Location, scratch: int = 10) -> list[str]:
        if dest.width != source.width:
            raise MIRUnsupported("machine copy width mismatch")
        if _alias(dest, source):
            # Both virtual values use the same logical lane; explicit zext
            # below performs a self 32-bit write when upper bits matter.
            return []
        if dest.kind == "reg":
            if source.kind == "address":
                return [f"  lea {_text(dest)}, [rbp - {source.payload}]"]
            if source.kind == "global":
                return x64._materialize_global_address(str(source.payload), _text(dest))
            if source.kind == "constant_gep":
                return x64._materialize_constant_gep(str(source.payload),_text(dest))
            return [f"  mov {_text(dest)}, {_text(source)}"]
        if not _memory(dest):
            raise MIRUnsupported("invalid machine copy destination")
        if source.kind == "reg":
            return [f"  mov {_text(dest)}, {_text(source)}"]
        temporary = _register(scratch, dest.width)
        return self.copy(temporary, source) + self.copy(dest, temporary)

    def parallel(self, assignments: list[tuple[Location, Location]]) -> list[str]:
        # All destinations are physical locations; memory ranges must either
        # be exact aliases or disjoint. A mixed partial overlap uses the
        # stage-all edge buffer rather than an unsafe register copy order.
        spans = []
        for dest, source in assignments:
            for loc in (dest, source):
                if _memory(loc):
                    offset = int(loc.payload)
                    low = offset - loc.width if loc.kind == "slot" else offset
                    high = offset if loc.kind == "slot" else offset + loc.width
                    spans.append((loc.kind, low, high, loc.key()))
        for i, (kind, low, high, key) in enumerate(spans):
            for other_kind, other_low, other_high, other_key in spans[i + 1:]:
                if kind == other_kind and key != other_key and low < other_high and other_low < high:
                    return self.buffered(assignments)
        pending = [(dest, src) for dest, src in assignments if not _alias(dest, src)]
        if len({dest.key() for dest, _source in pending}) != len(pending):
            raise MIRUnsupported("overlapping parallel destinations")
        lines: list[str] = []
        while pending:
            ready = -1
            for i, (dest, _source) in enumerate(pending):
                if not any(j != i and _alias(dest, other_source)
                           for j, (_other_dest, other_source) in enumerate(pending)):
                    ready = i
                    break
            if ready >= 0:
                dest, source = pending.pop(ready)
                lines.extend(self.copy(dest, source))
                continue
            # Park only one cycle source in reserved r11; ordinary
            # memory-to-memory copies use reserved r10. If a second cycle
            # would need r11 before the first is consumed, stage all.
            if any(source.kind == "reg" and source.payload == 11 for _, source in pending):
                return self.buffered(assignments)
            dest, source = pending[0]
            parked = _register(11, source.width)
            lines.extend(self.copy(parked, source))
            pending[0] = (dest, parked)
        return lines

    def buffered(self, assignments: list[tuple[Location, Location]]) -> list[str]:
        # Only PHI/prologue register/frame copies use this fallback.
        if any(dest.kind == "outgoing" or source.kind == "outgoing" for dest, source in assignments):
            raise MIRUnsupported("nested outgoing parallel buffer")
        size = x64._align_to(8 * len(assignments), 16)
        lines = [f"  sub rsp, {size}"] if size else []
        for i, (_dest, source) in enumerate(assignments):
            temp = Location("outgoing", i * 8, source.width)
            lines.extend(self.copy(temp, source))
        for i, (dest, _source) in enumerate(assignments):
            temp = Location("outgoing", i * 8, dest.width)
            lines.extend(self.copy(dest, temp))
        if size:
            lines.append(f"  add rsp, {size}")
        return lines

    def target(self, dest: Location, scratch: int = 10) -> Location:
        return dest if dest.kind == "reg" else _register(scratch, dest.width)

    def right(self, source: Location, lines: list[str], *, mul=False) -> Location:
        if source.kind in ("global", "constant_gep", "address") or (source.kind == "imm" and (mul or not _immediate_ok(source))):
            temporary = _register(11, source.width)
            lines.extend(self.copy(temporary, source))
            return temporary
        return source

    def memory_address(self, pointer: Location, width: int, lines: list[str]) -> str:
        size = {1:"BYTE PTR",2:"WORD PTR",4:"DWORD PTR",8:"QWORD PTR"}[width]
        if pointer.kind == "global" and x64._tls_global(str(pointer.payload)) is None:
            return f"{size} {x64._global_addr(str(pointer.payload))}"
        if pointer.kind == "address":
            return f"{size} [rbp - {pointer.payload}]"
        register = pointer if pointer.kind == "reg" else _register(11, 8)
        if register != pointer:
            lines.extend(self.copy(register, pointer))
        return f"{size} [{_text(register)}]"

    def binop(self, data):
        op, dest_name, ty, lhs, rhs = data
        dest = self.destination(dest_name, ty)
        if dest is None:
            return []
        left, right = self.location(lhs, ty), self.location(rhs, ty)
        if op in ("udiv", "sdiv", "urem", "srem"):
            return self.divide(op, dest_name, ty, left, right, dest)
        if op in ("shl", "lshr", "ashr"):
            result = self.target(dest)
            lines=[]
            if right.kind == "imm":
                count = str(int(right.payload) & 0xff)
            else:
                cl = _register(1, 1)
                # Read the count before a result color can overwrite its
                # last-use source. RCX is excluded by the machine constraint.
                lines.extend(self.copy(cl, Location(right.kind, right.payload, 1)))
                count = "cl"
            lines.extend(self.copy(result, left))
            ins = {"shl":"shl","lshr":"shr","ashr":"sar"}[op]
            lines.append(f"  {ins} {_text(result)}, {count}")
            return lines + self.copy(dest, result)
        if op not in ("add", "sub", "mul", "and", "or", "xor") or (op == "mul" and dest.width < 2):
            raise MIRUnsupported("unsupported integer binop")
        result = self.target(dest)
        if _alias(result, right) and not _alias(result, left):
            if op in ("add", "mul", "and", "or", "xor"):
                left, right = right, left
            else:
                result = _register(10, dest.width)
        lines = self.copy(result, left)
        right = self.right(right, lines, mul=(op == "mul"))
        mnemonic = "imul" if op == "mul" else op
        lines.append(f"  {mnemonic} {_text(result)}, {_text(right)}")
        return lines + self.copy(dest, result)

    def divide(self, op, name, ty, left, right, dest):
        if dest.width not in (4, 8):
            raise MIRUnsupported("narrow divide")
        accumulator = _register(0, dest.width)
        divisor = _register(10, dest.width)
        lines = self.copy(accumulator, left) + self.copy(divisor, right)
        signed = op in ("sdiv", "srem")
        if dest.width == 4:
            lines.extend(("  cdq", "  idiv r10d") if signed else ("  xor edx, edx", "  div r10d"))
        elif signed:
            # Same deterministic overflow exception boundary as legacy.
            overflow = x64._value_label(self.func.name, name, "mir_div_overflow")
            division = x64._value_label(self.func.name, name, "mir_div")
            done = x64._value_label(self.func.name, name, "mir_div_done")
            lines.extend(("  mov r11, -9223372036854775808", "  cmp rax, r11", f"  jne {division}",
                          "  cmp r10, -1", f"  je {overflow}", division+":", "  cqo", "  idiv r10",
                          f"  jmp {done}", overflow+":", "  xor edx, edx", done+":"))
        else:
            lines.extend(("  xor rdx, rdx", "  div r10"))
        result = accumulator if op in ("sdiv","udiv") else _register(2,dest.width)
        return lines + self.copy(dest, result)

    def compare(self, data, fused=False):
        op, name, ty, lhs, rhs = data
        dest = self.destination(name, TypeDesc("int", 1))
        if dest is None:
            return []
        predicate = {"eq":"e","ne":"ne","slt":"l","sle":"le","sgt":"g","sge":"ge",
                     "ult":"b","ule":"be","ugt":"a","uge":"ae"}.get(op)
        if predicate is None:
            raise MIRUnsupported("unsupported compare predicate")
        left,right = self.location(lhs,ty),self.location(rhs,ty)
        lines=[]
        if left.kind!="reg":
            temporary=_register(10,left.width);lines.extend(self.copy(temporary,left));left=temporary
        right=self.right(right,lines)
        lines.append(f"  cmp {_text(left)}, {_text(right)}")
        if fused:
            return lines
        result=self.target(dest)
        lines.append("  set"+predicate+" "+_text(result))
        return lines+self.copy(dest,result)

    def cast(self, data):
        op,name,src_ty,value,dst_ty=data
        dest=self.destination(name,dst_ty)
        if dest is None:return []
        source=self.location(value,src_ty)
        target=self.target(dest)
        if op in ("ptrtoint","inttoptr","bitcast") and source.width==dest.width:
            return self.copy(dest,source)
        if op in ("trunc","ptrtoint") and (src_ty.is_int or src_ty.is_ptr) and dst_ty.is_int and source.width>=dest.width:
            truncated=Location(source.kind,source.payload,dest.width)
            lines=self.copy(target,truncated)
            if dst_ty.width==1:lines.append(f"  and {_text(target)}, 1")
            return lines+self.copy(dest,target)
        if op=="inttoptr" and src_ty.is_int and dst_ty.is_ptr:
            op="zext"
        if op in ("zext","sext") and src_ty.is_int and (dst_ty.is_int or dst_ty.is_ptr) and source.width<=dest.width:
            lines=[]
            if source.kind=="imm":
                number=int(source.payload)
                mask=(1<<src_ty.width)-1;number&=mask
                if op=="sext" and number&(1<<(src_ty.width-1)):number-=1<<src_ty.width
                return self.copy(dest,Location("imm",number,dest.width))
            if src_ty.width==1:
                # i1 signed extension is 0/-1, not a byte sign extension.
                low=Location(target.kind,target.payload,1)
                lines=self.copy(low,source)
                lines.append(f"  and {_text(low)}, 1")
                if op=="sext":
                    lines.append(f"  neg {_text(low)}")
                    if dest.width>1:lines.append(f"  movsx {_text(target)}, {_text(low)}")
                elif dest.width>1:
                    lines.append(f"  movzx {_text(target)}, {_text(low)}")
                return lines+self.copy(dest,target)
            if source.width==dest.width:
                return self.copy(dest,source)
            if op=="zext" and source.width==4 and dest.width==8:
                lines=[f"  mov {_text(Location(target.kind,target.payload,4))}, {_text(source)}"]
            elif op=="sext" and source.width==4 and dest.width==8:
                lines=[f"  movsxd {_text(target)}, {_text(source)}"]
            else:
                lines=[f"  mov{'zx' if op=='zext' else 'sx'} {_text(target)}, {_text(source)}"]
            return lines+self.copy(dest,target)
        raise MIRUnsupported("unsupported cast "+op)

    def gep(self,data):
        name,base_ty,ptr_ty,ptr,indices=data
        dest=self.destination(name,TypeDesc("ptr"))
        if dest is None:return []
        pointer=self.location(ptr,ptr_ty)
        target=self.target(dest,11)
        offset=x64._gep_constant_byte_offset(base_ty,indices)
        if offset is not None:
            if not -2147483648<=offset<=2147483647:raise MIRUnsupported("large GEP displacement")
            if offset==0:return self.copy(dest,pointer)
            lines=[]
            base=pointer if pointer.kind=="reg" else _register(11,8)
            lines.extend(self.copy(base,pointer))
            sign=" + " if offset>0 else " - "
            lines.append(f"  lea {_text(target)}, [{_text(base)}{sign}{abs(offset)}]")
            return lines+self.copy(dest,target)
        if len(indices)!=1:
            raise MIRUnsupported("multi-index variable GEP")
        index_ty,index_name=indices[0]
        index=self.location(index_name,index_ty)
        lines=[]
        base=pointer if pointer.kind=="reg" else _register(11,8)
        lines.extend(self.copy(base,pointer))
        if index.width<8:
            if index.width not in (2,4) or index.kind=="imm":raise MIRUnsupported("narrow variable GEP")
            widened=_register(10,8)
            lines.append(f"  mov{'sxd' if index.width==4 else 'sx'} {_text(widened)}, {_text(index)}")
            index=widened
        elif index.kind!="reg" or index.payload==12:
            widened=_register(10,8);lines.extend(self.copy(widened,index));index=widened
        scale=base_ty.slot_size
        if scale in (1,2,4,8):
            scaled=_text(index) if scale==1 else _text(index)+"*"+str(scale)
            lines.append(f"  lea {_text(target)}, [{_text(base)} + {scaled}]")
        else:
            temporary=_register(10,8)
            if index!=temporary:lines.extend(self.copy(temporary,index))
            if not -2147483648<=scale<=2147483647:raise MIRUnsupported("large GEP scale")
            lines.append(f"  imul r10, r10, {scale}")
            lines.append(f"  lea {_text(target)}, [{_text(base)} + r10]")
        return lines+self.copy(dest,target)

    def loads_stores(self,kind,data):
        load=kind in ("load","load_atomic")
        if load:
            name,ty,ptr_ty,ptr=data[:4]
            dest=self.destination(name,ty)
            if dest is None:
                return []
            pointer=self.location(ptr,ptr_ty)
            target=self.target(dest)
            lines=[]
            address=self.memory_address(pointer,dest.width,lines)
            lines.append(f"  mov {_text(target)}, {address}")
            return lines+self.copy(dest,target)
        ty,value,ptr_ty,ptr=data[:4]
        source=self.location(value,ty)
        pointer=self.location(ptr,ptr_ty)
        lines=[]
        seq=kind=="store_atomic" and data[4]=="seq_cst"
        # xchg writes its register operand: keep the original SSA value.
        if source.kind!="reg" or seq:
            target=_register(10,source.width);lines.extend(self.copy(target,source));source=target
        address=self.memory_address(pointer,source.width,lines)
        lines.append(f"  {'xchg' if seq else 'mov'} {address}, {_text(source)}")
        return lines

    def atomic(self,data):
        dest_name,op,ptr_ty,ptr,ty,value,_ordering=data
        if op not in ("add","sub","xchg"):
            raise MIRUnsupported("atomicrmw operation")
        dest=self.destination(dest_name,ty)
        source=self.location(value,ty)
        pointer=self.location(ptr,ptr_ty)
        temporary=_register(10,source.width)
        lines=self.copy(temporary,source)
        address=self.memory_address(pointer,source.width,lines)
        if op=="sub":lines.append(f"  neg {_text(temporary)}")
        lines.append(f"  {'xchg' if op=='xchg' else 'lock xadd'} {address}, {_text(temporary)}")
        if dest is not None:lines.extend(self.copy(dest,temporary))
        return lines

    def compare_exchange(self,data):
        _name,_pair,ptr_ty,ptr,ty,old,new,_success,_failure=data
        expected=self.location(old,ty)
        desired=self.location(new,ty)
        pointer=self.location(ptr,ptr_ty)
        accumulator=_register(0,expected.width)
        temporary=_register(10,expected.width)
        lines=[]
        address=self.memory_address(pointer,expected.width,lines)
        lines.extend(self.copy(accumulator,expected))
        lines.extend(self.copy(temporary,desired))
        lines.append(f"  lock cmpxchg {address}, {_text(temporary)}")
        # The exact adjacent extracts consume RAX and FLAGS; no physical
        # aggregate store, and no other machine instruction may clobber them.
        return lines

    def extract_cas(self,data,instruction_id):
        name,_aggregate,_source,indices,ty,_offset=data
        _pair,component=self.mir.cas_extracts[instruction_id]
        if tuple(indices)!=(component,):
            raise MIRUnsupported("CAS extraction mismatch")
        dest=self.destination(name,ty)
        if dest is None:return []
        if component==0:
            return self.copy(dest,_register(0,dest.width))
        result=self.target(dest)
        return ["  sete "+_text(result)]+self.copy(dest,result)

    def memory_intrinsic(self,callee,args,operation,live_after):
        if not callee.startswith("llvm.memset.") or len(args)!=4:
            raise MIRUnsupported("unsupported memory intrinsic")
        ptr_ty,ptr=args[0];byte_ty,byte=args[1];size_ty,size=args[2]
        if not ptr_ty.is_ptr or byte_ty.width!=8 or size_ty.width not in (32,64):
            raise MIRUnsupported("memset operand width")
        pointer=self.location(ptr,ptr_ty)
        byte_value=self.location(byte,byte_ty)
        count=self.location(size,size_ty)
        address=_register(11,8)
        lines=self.copy(address,pointer)
        if count.kind=="imm" and byte_value.kind=="imm":
            n=int(count.payload)
            if not 0<=n<=65536:raise MIRUnsupported("memset constant size")
            lines.extend(x64._fill_address_with_byte("r11",n,int(byte_value.payload)))
        else:
            lines.extend(self.copy(_register(1,count.width),count))
            tag=x64._value_label(self.func.name,str(operation.instruction_id),"mir_memset")
            lines.extend(("  test rcx, rcx",f"  je {tag}_end"))
            lines.extend(self.copy(_register(0,1),byte_value))
            lines.extend((tag+"_loop:","  mov BYTE PTR [r11], al","  add r11, 1",
                          "  sub rcx, 1",f"  jne {tag}_loop",tag+"_end:"))
        return lines+self.retire(operation.inputs,live_after)

    def select(self,data):
        name,ty,cond,true_value,false_value=data
        dest=self.destination(name,ty)
        if dest is None:return []
        condition=self.location(cond,TypeDesc("int",1))
        if condition.kind=="imm":
            return self.copy(dest,self.location(true_value if int(condition.payload)&1 else false_value,ty))
        lines=[]
        if condition.kind!="reg":
            temporary=_register(10,1);lines.extend(self.copy(temporary,condition));condition=temporary
        false_label=x64._value_label(self.func.name,name,"mir_select_false")
        done=x64._value_label(self.func.name,name,"mir_select_done")
        lines.extend((f"  test {_text(condition)}, {_text(condition)}",f"  je {false_label}"))
        lines.extend(self.copy(dest,self.location(true_value,ty)))
        lines.extend((f"  jmp {done}",false_label+":"))
        lines.extend(self.copy(dest,self.location(false_value,ty)))
        lines.append(done+":")
        return lines

    def call(self,data,operation,live_after):
        name,ret_ty,callee,indirect,args,_fixed,vararg,alignments=data
        if indirect or vararg or any(alignments):raise MIRUnsupported("unsupported call ABI")
        if callee.startswith("llvm.trap"):
            if args:raise MIRUnsupported("trap args")
            return ["  ud2"]
        if callee.startswith("llvm.memset."):
            return self.memory_intrinsic(callee,args,operation,live_after)
        locations,total_stack,_fp=x64._iter_arg_locations([ty for ty,_ in args])
        stack_size=x64._align_to(total_stack,16)
        lines=[f"  sub rsp, {stack_size}"] if stack_size else []
        assignments=[]
        outgoing_regs=set()
        for (ty,value),(kind,payload) in zip(args,locations,strict=True):
            source=self.location(value,ty)
            if kind=="stack_scalar":
                lines.extend(self.copy(Location("outgoing",int(payload),source.width),source))
            elif kind=="reg":
                register=_REG_BASE[str(payload)]
                outgoing_regs.add(register)
                assignments.append((_register(register,source.width),source))
            else:
                raise MIRUnsupported("unsupported scalar ABI location")
        lines.extend(self.parallel(assignments))
        # Outgoing ABI registers are now the authoritative machine inputs.
        # Never retire a value into a register containing another argument.
        lines.extend(self.retire(operation.inputs,live_after,outgoing_regs))
        target=x64._asm_symbol(callee) if callee in x64._MODULE_SYMBOLS.defined_symbols else callee
        lines.append("  call "+target)
        if stack_size:lines.append(f"  add rsp, {stack_size}")
        if name is not None and not ret_ty.is_void:
            dest=self.destination(name,ret_ty)
            if dest is not None:lines.extend(self.copy(dest,_register(0,dest.width)))
        return lines

    def _register_retirement_masks(self, live_after: int) -> tuple[int, ...] | None:
        if (type(self.mir) is not X86MIRFunction
                or type(self.allocation) is not X86MIRAllocation):
            return None
        values = self.mir.values
        registers = self.allocation.registers
        if (type(values) is not tuple or type(registers) is not tuple
                or len(values) < 1024 or len(registers) != len(values)
                or type(live_after) is not int or live_after < 0
                or live_after.bit_length() > len(values)):
            return None
        if self._retire_register_owner is registers:
            return self._retire_register_masks
        # At most sixteen wide masks, not one per virtual value or home.
        # Construction is paid once for a large function's actual register
        # death site. No liveness, coloring or physical assignment changes.
        masks = [0] * 16
        for value_id, register in enumerate(registers):
            if type(register) is not int or register < -1 or register >= 16:
                return None
            if register >= 0:
                masks[register] |= 1 << value_id
        self._retire_register_owner = registers
        self._retire_register_masks = tuple(masks)
        return self._retire_register_masks

    def retire(self,inputs,live_after,except_registers=None):
        if live_after is None:return []
        dying=[]
        needs_slots=False
        for value_id in inputs:
            if value_id<0 or value_id in self.pinned or live_after&(1<<value_id):continue
            value=self.mir.values[value_id]
            if not value.pointer:continue
            dying.append(value_id)
            if self.allocation.registers[value_id]<0:needs_slots=True
        # Most machine operations do not kill a pointer. No live-set
        # projection, slot overlap list or register membership is needed.
        if not dying:return []
        reserved=0
        if except_registers is not None:
            for register in except_registers:reserved|=1<<register
        occupied_slots=[]
        register_masks = None
        if not needs_slots and type(self) is Lowerer:
            register_masks = Lowerer._register_retirement_masks(self, live_after)
        if register_masks is None:
            for value_id in _bits(live_after):
                value=self.mir.values[value_id]
                reg=self.allocation.registers[value_id]
                if reg>=0:reserved|=1<<reg
                elif needs_slots and value.home>=value.width:occupied_slots.append((value.home-value.width,value.home))
        lines=[];seen=set()
        for value_id in dying:
            value=self.mir.values[value_id]
            reg=self.allocation.registers[value_id]
            if reg>=0:
                if (reserved&(1<<reg) or ("reg",reg) in seen
                        or (register_masks is not None and live_after & register_masks[reg])):continue
                lines.append(f"  mov {_text(_register(reg,4))}, 0");seen.add(("reg",reg))
            elif value.home>=8 and not any(value.home-8<high and low<value.home for low,high in occupied_slots):
                if ("slot",value.home) not in seen:
                    lines.append(f"  mov QWORD PTR [rbp - {value.home}], 0");seen.add(("slot",value.home))
        return lines

    def instruction(self,func,block,kernel,block_id,position,ins,kind_id,data,is_volatile):
        operation=self.mir.blocks[block_id].instructions[position]
        if operation.instruction_id!=ins or operation.kind!=kind_id:
            raise MIRUnsupported("MIR/IR instruction mismatch")
        if isinstance(data,int):data=kernel.instruction_data_by_id(ins,block_id,position)
        data=_interned_operands(data);kind=PARSED_INSTRUCTION_KINDS[kind_id]
        live_after=self.allocation.instruction_live_after[block_id][position]
        if kind=="alloca":return []
        if kind=="call":return self.call(data,operation,live_after)
        if kind=="binop":lines=self.binop(data)
        elif kind=="icmp":
            mir_block=self.mir.blocks[block_id]
            fused=(position+1==len(mir_block.instructions)
                   and operation.result>=0
                   and mir_block.terminator_kind==PARSED_INSTRUCTION_KINDS.index("br_cond")
                   and mir_block.terminator_inputs==(operation.result,)
                   and operation.result not in self.pinned
                   and (block_id,position) not in self.suffix_positions
                   and not (self.allocation.live_out[block_id] & (1<<operation.result)))
            if fused:
                self.flag_branches[block_id]={"eq":"e","ne":"ne","slt":"l","sle":"le","sgt":"g","sge":"ge",
                    "ult":"b","ule":"be","ugt":"a","uge":"ae"}[data[0]]
            lines=self.compare(data,fused=fused)
            if fused:
                # The result lives in FLAGS, not its nominal GPR color.
                if live_after is not None:live_after &= ~(1<<operation.result)
        elif kind=="cast":lines=self.cast(data)
        elif kind=="gep":lines=self.gep(data)
        elif kind in ("load","store","load_atomic","store_atomic"):lines=self.loads_stores(kind,data)
        elif kind=="atomicrmw":lines=self.atomic(data)
        elif kind=="cmpxchg":lines=self.compare_exchange(data)
        elif kind=="extractvalue":lines=self.extract_cas(data,ins)
        elif kind=="select":lines=self.select(data)
        elif kind=="freeze":
            name,ty,value=data;dest=self.destination(name,ty);lines=[] if dest is None else self.copy(dest,self.location(value,ty))
        elif kind=="fence":
            # Same legacy x86-TSO seq_cst fence; other orders require none.
            lines=x64._emit_memory_instruction(func,kind,data)
            if lines is None:raise MIRUnsupported("fence form")
        else:raise MIRUnsupported("machine operation "+kind)
        return lines+self.retire(operation.inputs,live_after)

    def prologue(self):
        from .definition_linkage import checked_function_definition_linkage
        linkage=checked_function_definition_linkage(self.func,"x86_64-unknown-linux-gnu")
        symbol=x64._asm_symbol(self.func.name)
        lines=["",".text",".p2align 4, 0x90"]
        if self.func.is_global:lines.append(".globl "+symbol)
        if linkage=="external_odr":lines.append(".weak "+symbol)
        lines.extend((".type "+symbol+", @function",symbol+":","  push rbp","  mov rbp, rsp"))
        total=self.physical_frame_size+self.func.platform_frame_extra
        if total:lines.append(f"  sub rsp, {total}")
        area=saved_area_offset(self.func.platform_frame_extra)
        for reg in self.allocation.saved:
            disp=0 if reg==3 else area+(reg-12)*8
            lines.append(f"  mov QWORD PTR [rsp + {disp}], {_text(_register(reg,8))}")
        lines.append("  mov QWORD PTR [rsp + 8], 0")
        if self.compact_padding is not None:
            lines.extend(emit_fixed_frame_padding(self.compact_padding))
        else:
            lines.extend(emit_fixed_frame_padding(fixed_frame_padding(self.func,self.plan)))
            # Physical homes retained only as nominal layout until a separate
            # compact-frame proof. Zero complete private qword-only homes when
            # no value sharing the span requires a physical store.
            kept=[]
            omitted=set()
            for value in self.mir.values:
                if value.home<value.width:continue
                if self.allocation.registers[value.value_id]<0:
                    kept.append((value.home-value.width,value.home))
                elif value.width==8:
                    omitted.add(value.home)
            for home in sorted(omitted):
                if not any(home-8<high and low<home for low,high in kept):
                    lines.append(f"  mov QWORD PTR [rbp - {home}], 0")
        locations,_stack,_fp=x64._iter_arg_locations([arg.type for arg in self.func.args])
        assignments=[]
        for arg,(kind,payload) in zip(self.func.args,locations,strict=True):
            dest=self.destination(arg.name,arg.type)
            if dest is None:continue
            if kind=="reg":source=_register(_REG_BASE[str(payload)],dest.width)
            elif kind=="stack_scalar":source=Location("incoming",16+int(payload),dest.width)
            else:raise MIRUnsupported("formal scalar ABI location")
            assignments.append((dest,source))
        lines.extend(self.parallel(assignments))
        return lines

    def epilogue(self):
        total=self.physical_frame_size+self.func.platform_frame_extra
        area=saved_area_offset(self.func.platform_frame_extra)
        lines=[]
        for reg in self.allocation.saved:
            disp=0 if reg==3 else area+(reg-12)*8
            lines.append(f"  mov {_text(_register(reg,8))}, QWORD PTR [rsp + {disp}]")
        if total:lines.append(f"  add rsp, {total}")
        lines.extend(("  pop rbp","  ret"))
        return lines

    def edge(self,source,target):
        src_id=self.kernel.block_id(source);dst_id=self.kernel.block_id(target)
        if src_id<0 or dst_id<0:raise MIRUnsupported("unknown phi edge")
        assignments=[]
        for dest_id,type_id,incoming_ref in self.mir.edge_copies.get((src_id,dst_id),()):
            dest=self.locations[dest_id]
            if dest is None:continue
            ty=self.kernel.type_desc(type_id)
            if incoming_ref>=0:
                incoming=self.locations[incoming_ref]
                if incoming is None or incoming.width!=dest.width:
                    raise MIRUnsupported("invalid indexed phi source")
            else:
                incoming=self.location(self.kernel.phi_incoming_value(incoming_ref),ty)
            assignments.append((dest,incoming))
        lines=self.parallel(assignments)
        lines.extend(self.retire(self.mir.edge_uses.get((src_id,dst_id),()),self.allocation.block_body_live[dst_id]))
        return lines

    def terminator(self,func,block_name,term):
        kind=term.kind;data=term.data
        if kind=="ret_void":return self.epilogue()
        if kind=="ret":
            ty,value=data;source=self.location(value,ty);result=_register(0,source.width)
            lines=self.copy(result,source)
            value_id=self.kernel.value_id(value)
            lines.extend(self.retire((value_id,),0,{0}))
            return lines+self.epilogue()
        if kind=="br":
            target=data[0]
            return self.edge(block_name,target)+["  jmp "+x64._block_label(func.name,target)]
        if kind=="br_cond":
            cond,true_target,false_target=data
            fused=self.flag_branches.get(self.kernel.block_id(block_name),"")
            value=self.location(cond,TypeDesc("int",1))
            if true_target==false_target:
                return self.edge(block_name,true_target)+["  jmp "+x64._block_label(func.name,true_target)]
            if value.kind=="imm" and not fused:
                target=true_target if int(value.payload)&1 else false_target
                return self.edge(block_name,target)+["  jmp "+x64._block_label(func.name,target)]
            lines=[]
            true_moves=self.edge(block_name,true_target)
            false_moves=self.edge(block_name,false_target)
            true_block=x64._block_label(func.name,true_target)
            false_block=x64._block_label(func.name,false_target)
            # Only actual parallel-copy/retirement work requires an edge
            # stub. The other edge runs inline, as in the legacy selector.
            true_label=x64._edge_label(func.name,block_name,true_target) if true_moves else true_block
            if fused:
                lines.append("  j"+fused+" "+true_label)
            else:
                if value.kind!="reg":
                    temporary=_register(10,1);lines.extend(self.copy(temporary,value));value=temporary
                lines.extend((f"  test {_text(value)}, {_text(value)}",f"  jne {true_label}"))
            lines.extend(false_moves)
            lines.append("  jmp "+false_block)
            if true_moves:
                lines.extend(("",true_label+":"))
                lines.extend(true_moves)
                lines.append("  jmp "+true_block)
            return lines
        if kind=="switch":
            ty,name,default,cases=data
            value=self.location(name,ty)
            lines=[]
            if value.kind!="reg":
                temporary=_register(10,value.width);lines.extend(self.copy(temporary,value));value=temporary
            targets=[];moves_by_target={}
            def target_label(target):
                if target not in moves_by_target:
                    targets.append(target)
                    moves_by_target[target]=self.edge(block_name,target)
                return (x64._edge_label(func.name,block_name,target)
                        if moves_by_target[target] else x64._block_label(func.name,target))
            for case,target in cases:
                right=Location("imm",case,value.width);right=self.right(right,lines)
                lines.append(f"  cmp {_text(value)}, {_text(right)}")
                lines.append("  je "+target_label(target))
            lines.append("  jmp "+target_label(default))
            for target in targets:
                moves=moves_by_target[target]
                if not moves:continue
                lines.extend(("",x64._edge_label(func.name,block_name,target)+":"))
                lines.extend(moves)
                lines.append("  jmp "+x64._block_label(func.name,target))
            return lines
        if kind=="unreachable":return ["  ud2"]
        raise MIRUnsupported("unsupported terminator")


def _emit_exact_indexed_mir_blocks(lowerer:Lowerer)->list[str]:
    from .self_backend_emit import _x86_block_id_route_indexes,_record_call_safepoint_at_return
    func=lowerer.func;kernel=lowerer.kernel;plan=lowerer.plan
    entry,suffix,terminator=_x86_block_id_route_indexes(plan,kernel)
    lines=[]
    for block in lowerer.mir.blocks:
        block_id=block.block_id;name=kernel.block_names[block_id]
        if block_id:lines.append("")
        lines.append(x64._block_label(func.name,name)+":")
        block_entry=entry.get(block_id)
        if block_entry is not None:lines.extend(block_entry)
        block_suffix=suffix.get(block_id)
        for position,operation in enumerate(block.instructions):
            instruction_start=len(lines)
            # MIR owns the exact absolute instruction identity and kind;
            # no second metadata/legacy instruction or block projection.
            data=kernel.instruction_data_by_id(operation.instruction_id,block_id,position)
            lines.extend(lowerer.instruction(func,None,kernel,block_id,position,
                operation.instruction_id,operation.kind,data,operation.volatile))
            suffix_start=len(lines)
            if block_suffix is not None and position in block_suffix:
                lines.extend(block_suffix[position])
            _record_call_safepoint_at_return(lines,instruction_start,suffix_start)
        term_prefix=terminator.get(block_id)
        if term_prefix is not None:lines.extend(term_prefix)
        term=kernel.diagnostic_terminator(block_id)
        lines.extend(lowerer.terminator(func,name,term))
    return lines


def _emit_lowered(func,plan,mir,allocation,compact_padding=None,diagnostic_out=None,frame_layout=None):
    lowerer=Lowerer(func,plan,mir,allocation,compact_padding,frame_layout)
    original_blocks,original_map=func.blocks,func.block_map
    try:
        if original_blocks:
            return None
        lines=lowerer.prologue()
        from . import self_backend_emit as shared
        from .self_backend_precise_stackmaps import FunctionStackMapPlan
        from .self_backend_kernel import IndexedFunctionKernel
        exact_plain=(type(plan) is FunctionStackMapPlan and plan.target=="x86_64-linux"
            and plan.packed_records is None and type(lowerer.kernel) is IndexedFunctionKernel
            and shared._ORIGINAL_PLAN_LINE_INDEX is not None
            and getattr(FunctionStackMapPlan,"build_line_index",None) is shared._ORIGINAL_PLAN_LINE_INDEX
            and shared._ORIGINAL_KERNEL_BLOCK_ID is not None
            and getattr(IndexedFunctionKernel,"block_id",None) is shared._ORIGINAL_KERNEL_BLOCK_ID
            and shared._block_line_index_get is shared._ORIGINAL_BLOCK_LINE_GET)
        if exact_plain:
            lines.extend(_emit_exact_indexed_mir_blocks(lowerer))
        else:
            x64._materialize_indexed_control_flow(func,lowerer.kernel)
            lines.extend(shared.emit_function_blocks(
                func,block_label=x64._block_label,
                emit_instruction=x64._emit_instruction,
                emit_terminator=lambda f,b,t:lowerer.terminator(f,b.name,t),
                stack_map_plan=plan,indexed_kernel=lowerer.kernel,
                emit_indexed_instruction=lowerer.instruction,
                _indexed_x86_routes=True,
            ))
        lines=x64._thread_x86_jumps(x64._thread_x86_jumps(lines))
        lines.append(plan.end_label+":")
        lines.append(f".size {x64._asm_symbol(func.name)}, .-{x64._asm_symbol(func.name)}")
        return lines
    except MIRUnsupported as error:
        if diagnostic_out is not None:diagnostic_out["reason"]=str(error)
        return None
    finally:
        func.blocks=original_blocks
        func.block_map=original_map


def emit_x86_mir_function(func,plan,diagnostic_out=None):
    mir=build_x86_mir(func,plan)
    if mir is None:
        if diagnostic_out is not None:diagnostic_out["reason"]="admission"
        return None
    try:
        allocation=allocate_x86_mir(mir,keep_diagnostics=False)
    except MIRAllocationBudgetExceeded as error:
        if diagnostic_out is not None:diagnostic_out["reason"]=str(error)
        return None
    return _emit_lowered(func,plan,mir,allocation,diagnostic_out=diagnostic_out)


def emit_x86_mir_compacted(func,plan,globals_,globals_index,diagnostic_out=None):
    from .self_backend_x86_mir_frame import (
        compact_x86_mir_frame,plan_x86_mir_frame,remapped_mir_values,stack_map_remap_is_exact,remap_plain_stack_map,
    )
    from .self_backend_precise_stackmaps import build_function_stack_map_plan
    mir=build_x86_mir(func,plan)
    if mir is None:
        if diagnostic_out is not None:diagnostic_out["reason"]="admission"
        return None
    try:
        allocation=allocate_x86_mir(mir,keep_diagnostics=False)
    except MIRAllocationBudgetExceeded as error:
        if diagnostic_out is not None:diagnostic_out["reason"]=str(error)
        return None
    pure_layout=plan_x86_mir_frame(func,plan,mir,allocation)
    pure_plan=remap_plain_stack_map(plan,pure_layout)
    if pure_plan is not None:
        lines=_emit_lowered(
            func,pure_plan,remapped_mir_values(mir,pure_layout,allocation),allocation,
            compact_padding=pure_layout.padding,diagnostic_out=diagnostic_out,frame_layout=pure_layout,
        )
        if lines is None:return None
        if diagnostic_out is not None:
            diagnostic_out["frame_old"]=pure_layout.original_size
            diagnostic_out["frame_new"]=pure_layout.new_size
        return lines,pure_plan
    # Packed/custom route retains the checked mutating oracle.
    layout=compact_x86_mir_frame(func,plan,mir,allocation)
    new_plan=plan
    completed=False
    try:
        if layout.moved:
            new_plan=build_function_stack_map_plan(
                func,globals_,target="x86_64-linux",identity_name=x64._asm_symbol(func.name),
                _globals_index=globals_index,
            )
            if not stack_map_remap_is_exact(plan,new_plan,layout):
                if diagnostic_out is not None:diagnostic_out["reason"]="stackmap remap mismatch"
                return None
        lines=_emit_lowered(func,new_plan,remapped_mir_values(mir,layout),allocation,
                            compact_padding=layout.padding,diagnostic_out=diagnostic_out)
        if lines is None:return None
        completed=True
        if diagnostic_out is not None:
            diagnostic_out["frame_old"]=layout.original_size
            diagnostic_out["frame_new"]=layout.new_size
        return lines,new_plan
    finally:
        layout.restore(func)
        if new_plan is not plan:
            if completed:
                if plan.packed_records is not None:plan.packed_records.close()
            elif new_plan.packed_records is not None:new_plan.packed_records.close()
