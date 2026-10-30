"""Host execution of PCC waitset functions with explicit memory/kernel adapters.

This is not a native allocator or scheduler benchmark. Only the candidate's
event allocation/release is substituted; output allocation and event decoding
execute the supplied production source. No production module imports this file.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import struct
import threading


class Memory:
    """Bounds-checked byte-addressed host adapter, not an actual native heap."""

    PAGE = 1 << 20

    def __init__(self):
        self.blocks = {}
        self.next_page = 1
        self.event_allocations = 0
        self.event_frees = 0
        self.event_requested_bytes = 0
        self.event_live_bytes = 0
        self.event_peak_bytes = 0
        self.fail_next_event = False

    def allocate(self, size, kind="other"):
        if not 0 <= size < self.PAGE:
            raise ValueError("experiment allocation outside bounded adapter")
        if kind == "event" and self.fail_next_event:
            self.fail_next_event = False
            return 0
        pointer = self.next_page * self.PAGE
        self.next_page += 1
        self.blocks[pointer] = (bytearray(size), kind)
        if kind == "event":
            self.event_allocations += 1
            self.event_requested_bytes += size
            self.event_live_bytes += size
            self.event_peak_bytes = max(self.event_peak_bytes, self.event_live_bytes)
        return pointer

    def region(self, pointer, size):
        base = pointer // self.PAGE * self.PAGE
        if base not in self.blocks:
            raise RuntimeError("freed or invalid address")
        data = self.blocks[base][0]
        offset = pointer - base
        if offset < 0 or offset + size > len(data):
            raise RuntimeError("out-of-bounds access")
        return data, offset

    def free(self, pointer):
        if not pointer:
            return
        data, kind = self.blocks.pop(pointer)
        if kind == "event":
            self.event_frees += 1
            self.event_live_bytes -= len(data)

    def realloc(self, pointer, size):
        new = self.allocate(size)
        if pointer:
            old = self.blocks[pointer][0]
            self.blocks[new][0][:min(len(old), size)] = old[:size]
            self.free(pointer)
        return new

    def load(self, fmt, pointer, offset=0):
        data, at = self.region(pointer + offset, struct.calcsize(fmt))
        return struct.unpack_from(fmt, data, at)[0]

    def store(self, fmt, pointer, offset, value):
        data, at = self.region(pointer + offset, struct.calcsize(fmt))
        bits = struct.calcsize(fmt) * 8
        struct.pack_into(fmt, data, at, value & ((1 << bits) - 1))

    def clear(self, pointer, value, size):
        data, offset = self.region(pointer, size)
        data[offset:offset + size] = bytes([value & 255]) * size

    def release_stack(self):
        for pointer in list(self.blocks):
            if self.blocks[pointer][1] == "stack":
                self.free(pointer)


class EventPool:
    """One idle buffer per waitset; checked-out buffers are never shared.

    Ownership is a waitset/lease fact, not carrier affinity. A concurrent or
    reentrant borrow allocates independently. Closing a waitset with outstanding
    leases fails rather than freeing storage still retained by a backend.
    """

    def __init__(self, memory):
        self.memory = memory
        self.idle = {}
        self.active = {}
        self.closed = set()
        self.lock = threading.Lock()

    def acquire(self, waitset, size):
        with self.lock:
            if waitset in self.closed:
                raise RuntimeError("closed waitset")
            saved = self.idle.pop(waitset, None)
            if saved is not None and saved[1] < size:
                self.memory.free(saved[0])
                saved = None
            if saved is None:
                pointer = self.memory.allocate(size, "event")
                if not pointer:
                    return 0
                saved = (pointer, size)
            self.active[saved[0]] = (waitset, saved[1])
            return saved[0]

    def release(self, pointer):
        if not pointer:
            return
        with self.lock:
            waitset, capacity = self.active.pop(pointer)
            # Erase the full retained capacity, not only the last result count.
            # No escaped view may legally observe the buffer after this point.
            self.memory.clear(pointer, 0, capacity)
            prior = self.idle.get(waitset)
            if prior is None or prior[1] < capacity:
                if prior is not None:
                    self.memory.free(prior[0])
                self.idle[waitset] = (pointer, capacity)
            else:
                self.memory.free(pointer)

    def dispose(self, waitset):
        with self.lock:
            if any(owner == waitset for owner, _ in self.active.values()):
                raise RuntimeError("backend still retains a buffer")
            saved = self.idle.pop(waitset, None)
            if saved is not None:
                self.memory.free(saved[0])
            self.closed.add(waitset)


@dataclass
class Batch:
    address: int
    pointer: int
    owner: object
    phase: str = "prepared"
    cancelled: bool = False

    def check(self, owner):
        if owner is not self.owner:
            raise RuntimeError("different logical-task owner")
        if self.phase == "closed":
            raise RuntimeError("closed lease")


class WaitsetHarness:
    def __init__(self, source: Path, pooled=False, backend=2):
        self.memory = Memory()
        self.pool = EventPool(self.memory) if pooled else None
        self.now = 100
        self.backend = backend
        self.kernel_events = []
        self.kernel_results = []
        self.blocking_depth = 0
        self.batches = []
        memory = self.memory
        env = {
            "i64": int, "c_ptr": int,
            "ptr_add": lambda p, n: p + n,
            "ptr_eq": lambda a, b: int(a == b),
            "ptr_is_null": lambda p: int(p == 0),
            "null": lambda: 0,
            "malloc": lambda n: memory.allocate(n, "event"),
            "realloc": memory.realloc, "free": memory.free,
            "stack_alloc": lambda n: memory.allocate(n, "stack"),
            "memset": memory.clear,
            "logical_shift_right_i64": lambda x, n: (x & ((1 << 64) - 1)) >> n,
            "unsigned_div_i64": lambda a, b: a // b,
            "unsigned_rem_i64": lambda a, b: a % b,
            "epoll_ctl": lambda *args: 0,
            "epoll_wait": self.epoll_wait,
            "kevent_call": self.kevent,
            "read": lambda *args: -11,
            "clock_gettime": self.clock_gettime,
            "frame_address": lambda: 0,
            "pcc_thread_blocking_enter": self.blocking_enter,
            "pcc_thread_blocking_leave": self.blocking_leave,
        }
        for name, fmt in (("i8", "b"), ("i32", "i"), ("i64", "q"), ("ptr", "Q")):
            env["load_" + name] = lambda p, n=0, fmt=fmt: memory.load("<" + fmt, p, n)
            unsigned = {"b": "B", "i": "I", "q": "Q", "Q": "Q"}[fmt]
            env["store_" + name] = lambda p, n, v, fmt=unsigned: memory.store("<" + fmt, p, n, v)
        tree = ast.parse(source.read_text())
        functions = []
        replacements = 0
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            node.decorator_list = []
            if pooled and node.name == "pcc_io_waitset_wait_prepare":
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "malloc":
                        call.func.id = "acquire_events"
                        call.args.insert(0, ast.Name(id="ws", ctx=ast.Load()))
                        replacements += 1
            if pooled and node.name == "pcc_io_waitset_wait_discard":
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "free":
                        call.func.id = "release_events"
                        replacements += 1
            functions.append(node)
        if pooled:
            if replacements != 2:
                raise ValueError("source shape changed; inspect candidate substitutions")
            env["acquire_events"] = self.pool.acquire
            env["release_events"] = self.pool.release
        tree.body = functions
        ast.fix_missing_locations(tree)
        exec(compile(tree, str(source), "exec"), env)
        self.runtime = env
        self.waitset = memory.allocate(88)
        env["store_i32"](self.waitset, 0, backend)
        env["store_i32"](self.waitset, 72, 41)
        env["store_i32"](self.waitset, 80, 42)

    def blocking_enter(self, frame):
        self.blocking_depth += 1
        return 1

    def blocking_leave(self, entered):
        self.blocking_depth -= entered

    def clock_gettime(self, clock, pointer):
        self.memory.store("<Q", pointer, 0, self.now // 1000)
        self.memory.store("<Q", pointer, 8, (self.now % 1000) * 1000000)
        return 0

    def epoll_wait(self, descriptor, pointer, capacity, timeout):
        if self.kernel_results:
            return self.kernel_results.pop(0)
        selected = self.kernel_events[:capacity]
        del self.kernel_events[:capacity]
        for index, (fd, generation, events) in enumerate(selected):
            self.memory.store("<I", pointer, index * 12, events)
            self.memory.store("<I", pointer, index * 12 + 4, fd)
            self.memory.store("<I", pointer, index * 12 + 8, generation)
        return len(selected)

    def kevent(self, descriptor, changes, count, pointer, capacity, timeout):
        if pointer == 0:
            return 0
        if self.kernel_results:
            return self.kernel_results.pop(0)
        selected = self.kernel_events[:capacity]
        del self.kernel_events[:capacity]
        for index, (fd, generation, events) in enumerate(selected):
            event = pointer + index * 32
            self.runtime["_write_kevent"](event, fd, -1 if events & 1 else -2, 0,
                                          generation * 4294967296 + fd)
        return len(selected)

    def add(self, fd, interest=1, deadline=-1):
        result = self.runtime["pcc_io_waitset_add"](self.waitset, fd, interest, deadline, 0)
        if result != 0:
            raise RuntimeError("registration failed")
        slot = self.runtime["_find_slot"](self.waitset, fd)
        pointer = self.runtime["load_ptr"](self.waitset, 8) + slot * 40
        return self.runtime["load_i32"](pointer, 36)

    def prepare(self, owner):
        address = self.memory.allocate(64, "batch")
        result = self.runtime["pcc_io_waitset_wait_prepare"](
            self.waitset, self.now, self.now, address)
        if result != 0:
            self.memory.free(address)
            raise MemoryError("prepare failed")
        batch = Batch(address, self.runtime["load_ptr"](address, 0), owner)
        self.batches.append(batch)
        return batch

    def start(self, batch, owner):
        batch.check(owner)
        if batch.phase != "prepared":
            raise RuntimeError("not prepared")
        batch.phase = "inflight"

    def block_returned(self, batch, owner):
        batch.check(owner)
        if batch.phase != "inflight":
            raise RuntimeError("not in flight")
        # Explicit acknowledgement: until return, cancellation cannot reclaim.
        result = self.runtime["pcc_io_waitset_wait_block"](self.waitset, batch.address)
        batch.phase = "returned"
        self.memory.release_stack()
        return result

    def cancel(self, batch, owner):
        batch.check(owner)
        batch.cancelled = True
        # Requesting cancellation is not proof that the kernel relinquished it.

    def finish(self, batch, owner, discard=False):
        batch.check(owner)
        if batch.phase == "inflight":
            raise RuntimeError("backend still retains buffer")
        if batch.phase == "prepared" and not discard:
            raise RuntimeError("wait never completed")
        if discard:
            self.runtime["pcc_io_waitset_wait_discard"](batch.address)
            result = (0, (), ())
        else:
            out = self.memory.allocate(32)
            status = self.runtime["pcc_io_waitset_wait_finish"](self.waitset, batch.address, out)
            load = self.runtime["load_i64"]
            ready = self.runtime["load_ptr"](out, 0)
            timeout = self.runtime["load_ptr"](out, 16)
            # Copy outputs; no result escapes as a view into reusable storage.
            result = (status,
                      tuple((load(ready, i * 16), load(ready, i * 16 + 8))
                            for i in range(load(out, 8))),
                      tuple(load(timeout, i * 8) for i in range(load(out, 24))))
            self.memory.free(out)
        batch.phase = "closed"
        self.batches.remove(batch)
        self.memory.free(batch.address)
        self.memory.release_stack()
        return result

    def read_lease(self, batch, owner):
        batch.check(owner)
        data, offset = self.memory.region(batch.pointer, 1)
        return data[offset]

    def dispose(self):
        if any(batch.phase != "closed" for batch in self.batches):
            raise RuntimeError("outstanding lease")
        if self.pool is not None:
            self.pool.dispose(self.waitset)
        for offset in (8, 40, 56):
            self.memory.free(self.runtime["load_ptr"](self.waitset, offset))
        self.memory.free(self.waitset)
